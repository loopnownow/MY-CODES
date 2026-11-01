# -*- coding: utf-8 -*-
"""yt_RDL 复发随访列：6 个月 / 1 年 / 2 年 / 3 年复发情况。

数据来源
--------
1. ``ref/*.xls``（20/24/26）影像报告：每次检查的 检查日期 + 四列诊断文本。
2. ``work/Results``（``ref/Results.7z`` 解包）出院小结：由
   ``scripts/extract_jinshan_data.py`` 抽成 ``work/jinshan_extracted_data.csv``，
   内含每次住院的 入院/出院时间、门诊/入院/出院诊断、住院经过全文。

口径
----
基线日期 ``基线日期``
    * 优先 = **原发肿瘤切除手术日**（出院小结「住院经过」里首次出现、且该次入院诊断
      尚未记载「复发/术后复发」的肿瘤切除手术日期）→ ``基线来源=原发手术``（可靠性高）
    * 次选 = 该患者在本批影像报告里**最早一次检查日期** → ``基线来源=首次检查日期``（可靠性中）
    * 都拿不到 → ``基线来源=缺失``（4 个随访列填“无法判定”）

复发事件（默认口径：含“复发可能/不能除外”）
    * 影像报告某次检查的四列文本出现**复发阳性**表述 → 事件日 = 检查日期
    * 出院小结的门诊/入院/出院诊断或住院经过出现复发阳性表述 → 事件日 = 入院时间
    * 取晚于基线日期的**最早**一次 → ``首次复发日期``

三个水平列 ``复发_6月/1年/2年/3年``
    * ``是``       —— 基线后 ≤ 该时点已出现复发事件
    * ``否``       —— 随访已覆盖到该时点且未见复发事件
    * ``随访不足`` —— 末次随访早于该时点、又未发生事件（删失）

运行：
    set PYTHONDONTWRITEBYTECODE=1
    python scripts\\yt_rdl_recurrence.py
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

import tidy_io  # noqa: E402
import tidy_yt_rdl as PATHS  # noqa: E402

ROOT = PATHS.PROJECT_ROOT

DIAG_COLS = ["临床诊断", "放射学诊断", "放射学表现", "疾病名称"]
REF = PATHS.REF
INPUT_XLS = [REF / f"{n}.xls" for n in ("20", "24", "26")]
WORK = PATHS.WORK
RESULT_DIR = PATHS.RESULTS_TXT
JINSHAN_CSV = PATHS.JINSHAN_CSV
CAND_XLSX = PATHS.CAND_XLSX

# ── 文本模式 ────────────────────────────────────────────────────────────
RE_DATE = r"(\d{4})[.\-/年](\d{1,2})[.\-/月](\d{1,2})"
RE_MD = r"(\d{1,2})[.\-/月](\d{1,2})"
RE_TUMOR = re.compile(r"肿瘤|肿物|肿块|肉瘤|恶性|复发灶|转移灶|占位")
RE_OP = re.compile(r"切除|根治|摘除|剜除")
RE_RELAPSE = re.compile(r"复发|再发")
# 复发阴性（"未见…复发"）
RE_RELAPSE_NEG = re.compile(r"(?:未见|未出现|未发现|未见明确|无|没有|不考虑|排除)[^。；;，,]{0,10}(?:复发|再发)")
# 复发阳性（"考虑/提示/符合/复发可能/不除外/警惕/待排"）
RE_RELAPSE_POS = re.compile(r"(?:考虑|提示|符合|倾向|可疑|诊断|系|为|合并|伴|待排|警惕)"
                            r"[^。；;]{0,10}(?:复发|再发)|"
                            r"(?:复发|再发)(?:可能|不能除外|不除外|待排)|"
                            r"(?:不除外|不能除外|除外)[^。；;]{0,8}(?:复发|再发)|"
                            r"(?:肿瘤|病灶|肿物|肿块)[^。；;]{0,6}(?:复发|再发)")
# 转移/进展（备选口径用）
RE_META_POS = re.compile(r"(?:考虑|提示|符合|倾向|可疑)|(?:可能|不除外|不能除外|待排)")
# 明确的既往史/术后复发（用于判断"首手术住院已是复发"）
RE_HISTORY_RECUR = re.compile(r"复发|再发|术后")


YEAR_MIN, YEAR_MAX = 1990, 2035


def parse_ts(*ymd) -> pd.Timestamp:
    """安全构造时间戳：修 5 位年份笔误（源文里有「于20236-02-02」），越界返回 NaT。"""
    try:
        y, m, d = int(ymd[0]), int(ymd[1]), int(ymd[2])
        if y > 9999:            # 20236 -> 2023
            y = int(str(y)[:4])
        if not (YEAR_MIN <= y <= YEAR_MAX) or not (1 <= m <= 12) or not (1 <= d <= 31):
            return pd.NaT
        return pd.Timestamp(y, m, d)
    except Exception:
        return pd.NaT


def to_ts(v) -> pd.Timestamp:
    s = "" if v is None else str(v).strip()
    if not s or s.lower() == "nan":
        return pd.NaT
    ts = pd.to_datetime(s[:19], errors="coerce")
    if pd.isna(ts) or not (YEAR_MIN <= ts.year <= YEAR_MAX):
        return pd.NaT
    return ts.normalize()          # 一律按“日”比较，避免同日带时间被误判成晚于基线


def add_months(ts: pd.Timestamp, months: int) -> pd.Timestamp:
    """日历月加法（月末自动收敛），比 30.44 天近似更贴近临床随访节点。"""
    if pd.isna(ts):
        return pd.NaT
    y, m = divmod((ts.month - 1) + months, 12)
    y, m = ts.year + y, m + 1
    day = min(ts.day, [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
                       31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return pd.Timestamp(y, m, day)


def months_between(a: pd.Timestamp, b: pd.Timestamp) -> float:
    """a → b 的月数（正数表示 b 晚于 a）。"""
    if pd.isna(a) or pd.isna(b):
        return float("nan")
    return (b - a).days / 30.4375


def relapse_polarity(text: str) -> str:
    """判断一段文本里的复发提及是 阳性/阴性/无提及。"""
    t = "" if text is None else str(text)
    if not RE_RELAPSE.search(t):
        return "无"
    for sent in re.split(r"[。；;\n]", t):
        if not RE_RELAPSE.search(sent):
            continue
        if RE_RELAPSE_POS.search(sent):
            return "阳性"
        if RE_RELAPSE_NEG.search(sent):
            continue  # 该句为阴性，继续看其他句子
    # 有"复发"字面但既非明确阳性也非阴性（如"脂肪肉瘤术后复发史"在诊断里）
    if RE_RELAPSE_POS.search(t):
        return "阳性"
    if RE_RELAPSE_NEG.search(t):
        return "阴性"
    return "提及"


def relapse_positive(text: str) -> bool:
    p = relapse_polarity(text)
    if p == "阳性":
        return True
    if p == "提及":
        # 只有字面提及（如诊断名“脂肪肉瘤术后复发”），在出院诊断语境下仍按阳性计
        return True
    return False


def relapse_quote(text: str, limit: int = 160) -> str:
    for sent in re.split(r"[。；;\n]", "" if text is None else str(text)):
        if RE_RELAPSE.search(sent) and RE_RELAPSE_POS.search(sent):
            return sent.strip()[:limit]
    for sent in re.split(r"[。；;\n]", "" if text is None else str(text)):
        if RE_RELAPSE.search(sent):
            return sent.strip()[:limit]
    return ""


RE_META_WORD = re.compile(r"转移|进展|播散|种植")


def alt_positive(text: str) -> bool:
    """备选口径：复发 **或** 转移/进展 均计为事件。"""
    if relapse_positive(text):
        return True
    for sent in re.split(r"[。；;\n]", "" if text is None else str(text)):
        if not RE_META_WORD.search(sent):
            continue
        if re.search(r"(?:未见|未出现|未发现|未见明确|无|没有|不考虑|排除)"
                     r"[^。；;，,]{0,10}(?:转移|进展|播散|种植)", sent):
            continue
        return True
    return False


def alt_quote(text: str, limit: int = 160) -> str:
    for sent in re.split(r"[。；;\n]", "" if text is None else str(text)):
        if (RE_RELAPSE.search(sent) or RE_META_WORD.search(sent)):
            return sent.strip()[:limit]
    return ""


# ═══════════════════════════════════════════════════════════════════════
# 1. 出院小结：手术日期 + 复发事件
# ═══════════════════════════════════════════════════════════════════════
def find_tumor_ops(text: str, adm: pd.Timestamp) -> tuple[pd.Timestamp, str]:
    """在「住院经过」中找首次肿瘤切除手术：(日期, 原句)。日期缺失时用入院日近似。"""
    for sent in re.split(r"[。\n]", "" if text is None else str(text)):
        if not (RE_OP.search(sent) and RE_TUMOR.search(sent)):
            continue
        m = re.search(r"(?:于|在)?\s*" + RE_DATE, sent)
        if m:
            return parse_ts(m.group(1), m.group(2), m.group(3)), sent.strip()[:200]
        m2 = re.search(r"(?:于|在)?\s*" + RE_MD + r"\s*日?\s*(?:在)?", sent)
        if m2 and pd.notna(adm):
            return parse_ts(adm.year, m2.group(1), m2.group(2)), sent.strip()[:200] + "（日期缺年份，按入院年补）"
        if pd.notna(adm):
            return adm, sent.strip()[:200] + "（无日期，按入院日近似）"
    return pd.NaT, ""


def build_alias_map(cand: pd.DataFrame) -> tuple[dict[str, str], dict[str, str]]:
    """同一患者可能挂在多个编号下，先建别名表。

    背景：候选表里 10 位患者的「病历卡号」被换成了他们在 `Results.7z` 查询里用的号
    （多为**住院号**），而 `ref/*.xls` 影像报告仍挂在原「病历卡号」下。若直接按
    病历卡号 join，这 10 位患者的影像事件会全部丢失。

    做法：从原始 xls 按「身份证号（优先）或姓名」把同一人的
    病历卡号 / 住院号 / 门诊号 / 就诊卡号 聚成一组，作为该患者的别名集合。

    Returns
    -------
    (alias2canon, canon2aliases)
        ``alias2canon``  任一别名 → 候选表里的病历卡号
        ``canon2aliases`` 候选表病历卡号 → 该患者的全部别名
    """
    xls_dir = ROOT / "ref"
    groups: dict[str, set[str]] = {}
    for n in ("20", "24", "26"):
        bk = tidy_io.open_book(str(xls_dir / f"{n}.xls"))
        sh = bk.sheet(0)
        hdr = bk.header(sh)
        for row in bk.rows(sh):
            rec = dict(zip(hdr, row))
            idc = re.sub(r"[\s\u3000']", "", str(rec.get("身份证号", "") or "")).upper()
            name = str(rec.get("姓名", "") or "").strip()
            gkey = f"ID:{idc}" if idc else f"NM:{name}"
            ids = groups.setdefault(gkey, set())
            # 只取 病历卡号 + 住院号：门诊号/就诊卡号 与病历卡号常有分叉，纳入会引入误并风险
            for col in ("病历卡号", "住院号"):
                for v in re.split(r"[/、,，;；]", str(rec.get(col, "") or "")):
                    v = re.sub(r"[\s\u3000]", "", v)
                    if v and v.lower() != "nan":
                        ids.add(v)

    by_name: dict[str, set[str]] = {}
    for k, v in groups.items():
        if k.startswith("NM:"):
            by_name.setdefault(k[3:], set()).update(v)

    # 先收集每个候选人的别名集合，再统一检测「一个别名指向多个患者」的冲突
    per_canon: dict[str, set[str]] = {}
    for _, r in cand.iterrows():
        canon = re.sub(r"[\s\u3000]", "", str(r["病历卡号"]))
        idc = re.sub(r"[\s\u3000']", "", str(r.get("身份证号", "") or "")).upper()
        name = str(r.get("姓名", "") or "").split("/")[0].strip()
        ids = set(groups.get(f"ID:{idc}", set())) if idc else set()
        ids |= by_name.get(name, set())
        ids.add(canon)
        per_canon[canon] = ids

    owner: dict[str, set[str]] = {}
    for canon, ids in per_canon.items():
        for i in ids:
            owner.setdefault(i, set()).add(canon)
    ambiguous = {i for i, s in owner.items() if len(s) > 1}

    alias2canon: dict[str, str] = {}
    canon2aliases: dict[str, set[str]] = {}
    for canon, ids in per_canon.items():
        keep = {i for i in ids if i not in ambiguous} | {canon}
        canon2aliases[canon] = keep
        for i in keep:
            alias2canon.setdefault(i, canon)
    if ambiguous:
        print(f"     [warn] {len(ambiguous)} 个编号同时指向多个患者，已弃用该别名："
              f"{sorted(ambiguous)[:10]}")
    return alias2canon, canon2aliases


def ensure_inputs() -> None:
    """临时文件（0del）缺失时自动重建：解包 Results.7z + 抽取出院小结宽表。"""
    PATHS.ensure_dirs()
    if not RESULT_DIR.exists() or not any(RESULT_DIR.glob("*.txt")):
        if not PATHS.RESULTS_7Z.exists():
            raise FileNotFoundError(f"缺少 {PATHS.RESULTS_7Z}")
        import py7zr
        print(f"[prep] 解包 {PATHS.RESULTS_7Z} → {PATHS.WORK}")
        with py7zr.SevenZipFile(PATHS.RESULTS_7Z, "r") as a:
            a.extractall(path=str(PATHS.WORK))
    if not JINSHAN_CSV.exists():
        import subprocess
        cmd = [sys.executable, str(PATHS.SCRIPTS_DIR / "extract_jinshan_data.py"),
               "--input-dir", str(RESULT_DIR), "--output", str(JINSHAN_CSV)]
        print("[prep] " + " ".join(cmd))
        subprocess.run(cmd, check=True)


def load_jinshan() -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (记录级表, 患者级手术/复发汇总)。"""
    if not JINSHAN_CSV.exists():
        raise FileNotFoundError(
            f"缺少 {JINSHAN_CSV}，请先运行：\n"
            f"  python scripts\\extract_jinshan_data.py --input-dir work\\Results "
            f"--output work\\jinshan_extracted_data.csv")
    d = pd.read_csv(JINSHAN_CSV, dtype=object)
    d["住院号_n"] = d["住院号"].map(lambda v: re.sub(r"\D", "", str(v or "")))
    d["入院_dt"] = d["入院时间"].map(to_ts)
    d["出院_dt"] = d["出院时间"].map(to_ts)
    d["MRN"] = d["文件名"].map(lambda s: re.sub(r"\.txt$", "", str(s)))
    d["MRN"] = d["MRN"].map(lambda s: re.sub(r"^0+(?=\d{9}$)", "", s))

    # 每条的诊断/经过文本
    d["诊断文本"] = (d["门诊诊断"].fillna("") + " || " + d["入院诊断"].fillna("")
                     + " || " + d["出院诊断"].fillna(""))

    recs = []
    for _, r in d.iterrows():
        adm = r["入院_dt"]
        op_dt, op_txt = find_tumor_ops(r.get("住院经过(全文)"), adm)
        diag = str(r["诊断文本"])
        # 该次住院是否已记载「复发/再发」——只认复发字样；「术后」单独出现不算
        # （否则「手术后肾缺失」这类既往史会误判成复发手术）
        prior_recur = bool(re.search(r"复发|再发", diag))
        recur_pos = relapse_positive(diag) or relapse_positive(r.get("住院经过(全文)"))
        recur_alt = recur_pos or alt_positive(r.get("住院经过(全文)")) or alt_positive(diag)
        recs.append({
            "MRN": r["MRN"], "文件名": r["文件名"], "记录序号": r["记录序号"],
            "科室": r["记录科室"], "住院号": r["住院号_n"],
            "入院": adm, "出院": r["出院_dt"],
            "手术日期": op_dt, "手术原句": op_txt,
            "已记载复发或术后": prior_recur,
            "复发阳性": recur_pos,
            "复发或转移阳性": recur_alt,
            "复发原文": relapse_quote(diag) or relapse_quote(r.get("住院经过(全文)")),
            "复发或转移原文": alt_quote(diag) or alt_quote(r.get("住院经过(全文)")),
            "出院诊断": str(r.get("出院诊断") or "")[:200],
        })
    R = pd.DataFrame(recs)

    # —— 患者级：原发手术日 ——
    rows = []
    for mrn, sub in R.groupby("MRN"):
        sub = sub.sort_values("入院")
        ops = sub[sub["手术日期"].notna()]
        primary = pd.NaT
        op_txt = ""
        if len(ops):
            first = ops.iloc[0]
            if not first["已记载复发或术后"]:
                primary, op_txt = first["手术日期"], first["手术原句"]
        rows.append({
            "MRN": mrn,
            "原发手术日期": primary,
            "原发手术原句": op_txt,
            "首次肿瘤手术日期(不论原发/复发)": (ops.iloc[0]["手术日期"] if len(ops) else pd.NaT),
            "住院次数": len(sub),
            "住院最早入院": sub["入院"].min(),
            "住院最晚出院": sub["出院"].max(),
            "有出院小结": True,
        })
    P = pd.DataFrame(rows)
    return R, P


# ═══════════════════════════════════════════════════════════════════════
# 2. 影像评估时间线：① 影像报告(xls)  ② 出院小结「特殊检查」里的带日期影像
# ═══════════════════════════════════════════════════════════════════════
REC_HEAD = re.compile(r"(?m)^\[(\d+)\]\s*(\S+?)[（(]住院[）)]")
RE_SPECIAL = re.compile(r"特殊检查及重要会诊（注明日期及检验号）:(.+?)(?=\n诊疗经过)", re.S)
RE_IMAGING = re.compile(r"CT|MRI|MR[（(]|MR平扫|彩超|超声|PET|X线|DR|造影")
RE_NOT_IMAGING = re.compile(r"心电图|肺功能|多导联|Holter|肌电图|脑电图")


def load_ct_events() -> pd.DataFrame:
    """来源①：ref/*.xls 影像报告，每次检查一行。"""
    frames = []
    for p in INPUT_XLS:
        bk = tidy_io.open_book(p)
        sh = bk.sheet(0)
        hdr = bk.header(sh)
        for row in bk.rows(sh):
            rec = dict(zip(hdr, row))
            txt = " || ".join(str(rec.get(c, "") or "") for c in DIAG_COLS)
            mrn = re.sub(r"[\s\u3000]", "", str(rec.get("病历卡号", "") or ""))
            frames.append({
                "MRN": mrn, "来源": "影像报告", "来源文件": p.stem,
                "影像号": rec.get("影像号"), "日期": to_ts(rec.get("检查日期")),
                "全文": txt,
                "复发阳性": relapse_positive(txt), "复发或转移阳性": alt_positive(txt),
                "复发原文": relapse_quote(txt), "复发或转移原文": alt_quote(txt),
            })
    return pd.DataFrame(frames)


def load_summary_imaging() -> pd.DataFrame:
    """来源②：出院小结「特殊检查」中带日期的影像条目（可显著延长随访窗口）。"""
    frames = []
    for f in sorted(RESULT_DIR.glob("*.txt")):
        mrn = re.sub(r"^0+(?=\d{9}$)", "", f.stem)
        try:
            t = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        pos = [m.start() for m in REC_HEAD.finditer(t)] + [len(t)]
        for i in range(len(pos) - 1):
            rec = t[pos[i]:pos[i + 1]]
            m = RE_SPECIAL.search(rec)
            if not m:
                continue
            seg = m.group(1)
            dm = list(re.finditer(RE_DATE, seg))
            for j, mm in enumerate(dm):
                dt = parse_ts(mm.group(1), mm.group(2), mm.group(3))
                if pd.isna(dt):
                    continue
                end = dm[j + 1].start() if j + 1 < len(dm) else len(seg)
                body = seg[mm.end():end].strip()
                if not body:
                    continue
                head = body[:24]
                if RE_NOT_IMAGING.search(head) or not RE_IMAGING.search(head):
                    continue
                frames.append({
                    "MRN": mrn, "来源": "出院小结特殊检查", "来源文件": f.stem,
                    "影像号": "", "日期": dt, "全文": body[:1200],
                    "复发阳性": relapse_positive(body),
                    "复发或转移阳性": alt_positive(body),
                    "复发原文": relapse_quote(body), "复发或转移原文": alt_quote(body),
                })
    return pd.DataFrame(frames)


# ═══════════════════════════════════════════════════════════════════════
# 3. 主流程
# ═══════════════════════════════════════════════════════════════════════
HORIZONS = [("复发_6月", 6), ("复发_1年", 12), ("复发_2年", 24), ("复发_3年", 36)]


def remap_ids(df: pd.DataFrame, canon2aliases: dict[str, set[str]]) -> pd.DataFrame:
    """把别名编号（住院号等）统一归并到候选表的病历卡号。"""
    if not len(df):
        return df
    m: dict[str, str] = {}
    for canon, aliases in canon2aliases.items():
        for a in aliases:
            m.setdefault(a, canon)
    out = df.copy()
    out["MRN_原始"] = out["MRN"]
    out["MRN"] = out["MRN"].map(lambda x: m.get(str(x), str(x)))
    return out


def build(cand: pd.DataFrame, R: pd.DataFrame, P: pd.DataFrame, IMG: pd.DataFrame):
    img_by = {m: s for m, s in IMG.groupby("MRN")}
    rec_by = {m: s for m, s in R.groupby("MRN")}
    p_by = {r["MRN"]: r for _, r in P.iterrows()} if len(P) else {}
    empty_img, empty_rec = IMG.iloc[0:0], R.iloc[0:0]

    out = []
    for _, c in cand.iterrows():
        mrn = re.sub(r"[\s\u3000]", "", str(c["病历卡号"]))
        imgs = img_by.get(mrn, empty_img)
        recs = rec_by.get(mrn, empty_rec)
        pinfo = p_by.get(mrn)

        img_dates = [d for d in imgs["日期"] if pd.notna(d)]
        first_img = min(img_dates) if img_dates else pd.NaT
        last_img = max(img_dates) if img_dates else pd.NaT
        surg = pinfo["原发手术日期"] if pinfo is not None else pd.NaT
        surg_all = pinfo["首次肿瘤手术日期(不论原发/复发)"] if pinfo is not None else pd.NaT

        if pd.notna(surg):
            base, src, rel = surg, "原发手术", "高"
        elif pd.notna(first_img):
            base, src, rel = first_img, "首次影像日期", "中"
        else:
            base, src, rel = pd.NaT, "缺失", "无"

        # 基线影像：不晚于基线的最后一次影像（放射组学分析用的术前 CT 的代理）
        pre = [d for d in img_dates if pd.notna(base) and d <= base]
        base_img = max(pre) if pre else pd.NaT

        # 事件候选（两种口径）：影像时间线 + 出院小结诊断
        ev, ev_alt = [], []
        for _, r in imgs.iterrows():
            if pd.notna(r["日期"]):
                if r["复发阳性"]:
                    ev.append((r["日期"], r["来源"], r["复发原文"]))
                if r["复发或转移阳性"]:
                    ev_alt.append((r["日期"], r["来源"], r["复发或转移原文"]))
        for _, r in recs.iterrows():
            if pd.notna(r["入院"]):
                if r["复发阳性"]:
                    ev.append((r["入院"], "出院小结诊断", r["复发原文"]))
                if r["复发或转移阳性"]:
                    ev_alt.append((r["入院"], "出院小结诊断", r["复发或转移原文"]))
        keep = lambda lst: sorted([e for e in lst if pd.notna(base) and e[0] > base],
                                  key=lambda x: x[0])
        ev, ev_alt = keep(ev), keep(ev_alt)
        first_ev = ev[0] if ev else None
        first_ev_alt = ev_alt[0] if ev_alt else None

        # 随访截止：末次影像评估（判"无复发"用）、末次任何接触（算删失时间用）
        contacts = [d for d in [last_img,
                                recs["出院"].max() if len(recs) else pd.NaT,
                                recs["入院"].max() if len(recs) else pd.NaT]
                    if pd.notna(d)]
        last_dt = max(contacts) if contacts else pd.NaT

        row = {
            "原发手术日期": surg.strftime("%Y-%m-%d") if pd.notna(surg) else "",
            "首次肿瘤手术日期": surg_all.strftime("%Y-%m-%d") if pd.notna(surg_all) else "",
            "基线日期": base.strftime("%Y-%m-%d") if pd.notna(base) else "",
            "基线来源": src,
            "基线可靠性": rel,
            "基线影像日期": base_img.strftime("%Y-%m-%d") if pd.notna(base_img) else "",
            "基线影像距手术(天)": (int((base - base_img).days)
                             if pd.notna(base) and pd.notna(base_img) else ""),
            "末次影像评估日期": last_img.strftime("%Y-%m-%d") if pd.notna(last_img) else "",
            "末次随访日期": last_dt.strftime("%Y-%m-%d") if pd.notna(last_dt) else "",
            "随访月数": (round(months_between(base, last_dt), 1)
                     if pd.notna(base) and pd.notna(last_dt) else ""),
            "影像评估次数": len(set(img_dates)),
            "首次复发日期": first_ev[0].strftime("%Y-%m-%d") if first_ev else "",
            "复发证据来源": first_ev[1] if first_ev else "",
            "复发证据部位提示": relapse_site(first_ev[2]) if first_ev else "",
            "复发证据原文": first_ev[2] if first_ev else "",
            "首次复发或转移日期": first_ev_alt[0].strftime("%Y-%m-%d") if first_ev_alt else "",
            "复发或转移证据原文": first_ev_alt[2] if first_ev_alt else "",
            "复发状态": ("复发" if first_ev else
                     ("无复发" if (pd.notna(base) and pd.notna(last_img)
                                 and months_between(base, last_img) >= 6) else
                      ("随访不足" if pd.notna(base) else "无法判定"))),
            "无复发生存时间(月)": (round(months_between(base, first_ev[0]), 1) if first_ev else
                             (round(months_between(base, last_dt), 1)
                              if pd.notna(base) and pd.notna(last_dt) else "")),
        }
        for name, mo in HORIZONS:
            if pd.isna(base):
                row[name] = row[name + "_含转移"] = "无法判定"
            else:
                row[name] = horizon_label(base, last_img, first_ev, mo)
                row[name + "_含转移"] = horizon_label(base, last_img, first_ev_alt, mo)
        out.append(row)
    return pd.DataFrame(out)


def horizon_label(base, last_dt, first_ev, mo: int) -> str:
    if first_ev and months_between(base, first_ev[0]) <= mo:
        return "是"
    if pd.notna(last_dt) and last_dt >= add_months(base, mo):
        return "否"
    return "随访不足"


RE_SITE_DISTANT = re.compile(r"肺|骨|脑|肝|纵隔|胸腔|远处|多发转移|淋巴结|种植|播散|肾上腺")
RE_SITE_LOCAL = re.compile(r"术区|原位|局部|腹膜后|后腹膜|腹腔|盆腔|原发|瘤床")


def relapse_site(text: str) -> str:
    """复发病灶部位提示（启发式）：远处 / 局部区域 / 未明确。"""
    t = "" if text is None else str(text)
    d = bool(RE_SITE_DISTANT.search(t))
    l = bool(RE_SITE_LOCAL.search(t))
    if d and l:
        return "局部+远处"
    if d:
        return "远处"
    if l:
        return "局部区域"
    return "未明确"


def main() -> int:
    ap = argparse.ArgumentParser(description="yt_RDL 复发随访列（6月/1年/2年/3年）")
    ap.add_argument("--audit", action="store_true", help="run independent audit and exit")
    ap.add_argument("--cand", default=str(PATHS.CAND_XLSX))
    ap.add_argument("--outdir", default=str(PATHS.PROJECT_ROOT))
    ap.add_argument("--outname", default="yt_RDL_脂肪肉瘤候选_合并表_随访")
    args = ap.parse_args()
    if args.audit:
        return run_audit()

    ensure_inputs()
    cand_path = Path(args.cand)
    xl = pd.ExcelFile(cand_path)
    sheets = {s: xl.parse(s) for s in xl.sheet_names}
    cand = sheets["01_候选患者_合并"]

    R, P = load_jinshan()
    CT = load_ct_events()
    SPEC = load_summary_imaging()
    IMG = pd.concat([CT, SPEC], ignore_index=True)
    alias2canon, canon2aliases = build_alias_map(cand)
    IMG = remap_ids(IMG, canon2aliases)
    R = remap_ids(R, canon2aliases)
    fu = build(cand, R, P, IMG)
    n_multi = sum(1 for v in canon2aliases.values() if len(v) > 1)
    print(f"     别名映射：{n_multi} 位患者有多个编号（病历卡号/住院号）")
    merged = pd.concat([cand.reset_index(drop=True), fu.reset_index(drop=True)], axis=1)
    merged["编号别名(合并用)"] = merged["病历卡号"].map(
        lambda m: " / ".join(sorted(canon2aliases.get(
            re.sub(r"[\s\u3000]", "", str(m)), set()))))
    sheets["01_候选患者_合并"] = merged
    sheets["07_随访明细_出院小结"] = R.assign(
        入院=R["入院"].dt.strftime("%Y-%m-%d"), 出院=R["出院"].dt.strftime("%Y-%m-%d"),
        本次肿瘤手术日期=R["手术日期"].dt.strftime("%Y-%m-%d"),
    ).drop(columns=["手术日期"], errors="ignore")
    sheets["09_影像时间线"] = IMG.assign(
        日期=IMG["日期"].dt.strftime("%Y-%m-%d")).sort_values(["MRN", "日期"])
    sheets["10_复发随访_分析表"] = analysis_table(merged)
    sheets["08_随访QC"] = qc_table(cand, merged, R, P, CT, SPEC, IMG, canon2aliases)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    xlsx = outdir / f"{args.outname}.xlsx"
    csv = PATHS.FU_CSV
    md = PATHS.FU_MD
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        for name, d in sheets.items():
            d.to_excel(xw, sheet_name=name[:31], index=False)
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment
    wb = load_workbook(xlsx)
    for name, d in sheets.items():
        ws = wb[name[:31]]
        tidy_io.style_header(ws, max(len(d.columns), 1))
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=False)
        for i, cn in enumerate(d.columns, start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(
                max(len(str(cn)) * 2, 12), 34)
    wb.save(xlsx)
    merged.to_csv(csv, index=False, encoding="utf-8-sig")
    write_md(md, merged, R, CT, SPEC, IMG)

    print(f"[ok] 候选患者 {len(cand)} 位")
    print("     基线来源：" + "，".join(f"{k}={v}" for k, v in
                                     merged["基线来源"].value_counts().items()))
    for name, _ in HORIZONS:
        print(f"     {name}（仅复发）：" + "，".join(
            f"{k}={v}" for k, v in merged[name].value_counts().items()))
        print(f"     {name}（含转移）：" + "，".join(
            f"{k}={v}" for k, v in merged[name + '_含转移'].value_counts().items()))
    print(f"[out] {xlsx}")
    print(f"[out] {csv}")
    return 0


def write_md(path: Path, merged: pd.DataFrame, R, CT, SPEC, IMG) -> None:
    import datetime as _dt
    H = ["复发_6月", "复发_1年", "复发_2年", "复发_3年"]
    HA = [h + "_含转移" for h in H]
    n = len(merged)
    src = merged["基线来源"].value_counts().to_dict()
    hi = merged[merged["基线可靠性"] == "高"]
    fu = pd.to_numeric(merged["随访月数"], errors="coerce")

    # 与 yt_RDL 既有标签对照
    cmp_lines = []
    if PATHS.YT_XLSX.exists():
        # 只跟原始判读队列比：追加进去的候选患者行没有课题标签，比了没意义
        yt = PATHS.read_yt_cln(cohort_only=True).drop_duplicates("Patient_ID")
        j = merged.merge(yt[["Patient_ID", "Recurrence"]], left_on="拼音首字母",
                         right_on="Patient_ID", how="inner")
        if len(j):
            # 注意：merged 里无事件是空串而不是 NaN，必须按字符串判空
            j["有复发证据"] = j["首次复发日期"].astype(str).str.strip().ne("") & \
                j["首次复发日期"].notna()
            ct = pd.crosstab(j["Recurrence"], j["有复发证据"])
            cmp_lines = [
                f"按姓名拼音首字母匹配到 **{len(j)}** 位 `yt_RDL.xlsx` 队列患者。",
                "",
                "| yt_RDL Recurrence \\ 本次是否有复发证据 | 无 | 有 |",
                "|---|---|---|",
            ]
            for lvl in ["Negative", "Positive"]:
                if lvl in ct.index:
                    cmp_lines.append(f"| {lvl} | {int(ct.loc[lvl].get(False, 0))} | "
                                     f"{int(ct.loc[lvl].get(True, 0))} |")
            cmp_lines += [
                "",
                "阳性组里只有一部分能在本次数据里找到复发证据——"
                "说明 `Recurrence` 标签的依据不在本批数据中（更长的随访/外院资料/电话随访）。",
            ]

    lines = [
        "# yt_RDL 复发随访变量（6 个月 / 1 年 / 2 年 / 3 年）说明",
        "",
        f"生成时间：{_dt.datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "对应同行评审 Q1（复发没有时间窗）与 Q2（缺 CT 日期 / 手术日期 / 复发日期）。",
        "",
        "## 1. 数据来源",
        "",
        "| 来源 | 内容 | 条目数 |",
        "|---|---|---|",
        f"| `ref/20.xls`、`24.xls`、`26.xls` | 影像报告：检查日期 + 四列诊断文本 | {len(CT)} |",
        f"| `ref/Results.7z` → 出院小结 | 每次住院的手术日期、诊断、诊疗经过 | {len(R)} |",
        f"| 同上「特殊检查」段 | 带日期的影像条目（延长随访窗口的关键） | {len(SPEC)} |",
        f"| **合计影像评估条目** | | **{len(IMG)}** |",
        "",
        "出院小结用 `scripts/extract_jinshan_data.py` 抽取（该脚本原先把记录切分写死为"
        "「肿瘤科（住院）」，已改为按任意「[N] 科室(住院)」切分，并加了 `--input-dir/--output`）。",
        "",
        "## 2. 编号归并（重要）",
        "",
        "候选表 `01_候选患者_合并` 里 **10 位患者的「病历卡号」已被改成他们在 `Results.7z` "
        "查询时用的号**（多为住院号），例如 罗梦云 `2210195 → 360239`、王海均 `2100033927 → 630265`。"
        "而 `ref/*.xls` 影像报告仍挂在原病历卡号下——直接按病历卡号关联会**丢掉这 10 位的全部影像事件**。",
        "本脚本先用「身份证号（优先）或姓名」把同一人的 病历卡号 / 住院号 聚成别名集合，"
        "再把影像时间线与出院小结统一归并到候选表的病历卡号；一个编号若同时指向两个患者则弃用。",
        "归并明细见 `01_候选患者_合并` 的 `编号别名(合并用)` 列与 `08_随访QC`。",
        "",
        "## 3. 基线日期（随访起点）",
        "",
        "| 基线来源 | 患者数 | 可靠性 | 含义 |",
        "|---|---|---|---|",
        f"| 原发手术 | {src.get('原发手术', 0)} | 高 | 出院小结「住院经过」里首次、且该次入院诊断尚未" 
        "记载「复发」的肿瘤切除手术日 |",
        f"| 首次影像日期 | {src.get('首次影像日期', 0)} | 中 | 拿不到原发手术日时，退而用最早一次影像日期 |",
        f"| 缺失 | {src.get('缺失', 0)} | 无 | 两个来源都没有可用日期 |",
        "",
        "## 4. 复发事件与两个口径",
        "",
        "* **事件日**：基线之后最早一次出现复发阳性表述的日期；来源可为影像报告、"
        "出院小结「特殊检查」影像条目、或出院小结诊断。",
        "* **仅复发**（默认）：文本含 `复发/再发`，且不是「未见/无…复发」这类否定句。"
        "含「复发可能 / 不能除外」。",
        "* **含转移**（备选，`_含转移` 后缀）：把「转移 / 播散 / 种植 / 进展」也计为事件。",
        "",
        "## 5. 取值",
        "",
        "| 值 | 含义 |",
        "|---|---|",
        "| 是 | 基线后 ≤ 该时点已出现复发事件 |",
        "| 否 | 末次影像评估 ≥ 该时点，且期间无事件 |",
        "| 随访不足 | 末次影像评估早于该时点，又未发生事件（删失） |",
        "| 无法判定 | 基线日期缺失 |",
        "",
        "## 6. 覆盖度（分母 = " + str(n) + " 位候选患者）",
        "",
        "| 时点 | 仅复发：是 / 否 / 随访不足 / 无法判定 | 含转移：是 / 否 / 随访不足 / 无法判定 |",
        "|---|---|---|",
    ]
    for h, ha in zip(H, HA):
        a, b = merged[h].value_counts(), merged[ha].value_counts()
        lines.append(
            f"| {h.replace('复发_', '')} | {a.get('是',0)} / {a.get('否',0)} / "
            f"{a.get('随访不足',0)} / {a.get('无法判定',0)} | "
            f"{b.get('是',0)} / {b.get('否',0)} / {b.get('随访不足',0)} / {b.get('无法判定',0)} |")

    lines += [
        "",
        f"随访月数：中位 **{fu.median():.1f}** 个月（四分位 {fu.quantile(.25):.1f}–{fu.quantile(.75):.1f}），"
        f"≥12 个月 **{int((fu >= 12).sum())}** 位，≥24 个月 **{int((fu >= 24).sum())}** 位，"
        f"≥36 个月 **{int((fu >= 36).sum())}** 位。",
        "",
        f"只看基线可靠性=高（原发手术，{len(hi)} 位）："
        + "；".join(f"{h.replace('复发_','')} 是 {int((hi[h]=='是').sum())}"
                    f"/ 否 {int((hi[h]=='否').sum())}"
                    f"/ 随访不足 {int((hi[h]=='随访不足').sum())}" for h in H) + "。",
        "",
        "## 7. ⚠ 关键限制（必须先解决，否则这些列不能用）",
        "",
        "1. **随访普遍不足**：多数患者在本院只有围手术期那一段记录，术后就转往上级医院，"
        "本院数据随之中断。因此 1 年 / 2 年 / 3 年档有相当比例只能标「随访不足」。",
        "2. **随访不足 ≠ 无复发**。绝不能把「随访不足」当成「否」参与建模，"
        "否则会把「没来复查」误读成「没复发」。",
        "3. **基线口径需确认**：只有原发手术日才是有临床意义的随访起点；"
        "退而用「首次影像日期」的那批患者，其真正起点更早，时间窗会被系统性拉长/压缩。",
        "4. **「复发」只有日期没有部位分级**：`复发证据部位提示` 是启发式（远处/局部区域），"
        "不能直接当作局部 vs 远处的判定依据。",
        "",
        "## 8. 与 yt_RDL 既有 `Recurrence` 标签的对照",
        "",
    ] + (cmp_lines if cmp_lines else ["（未找到 yt_RDL.xlsx，跳过）"]) + [
        "",
        "## 9. 对同行评审 C1 / C2 的建议",
        "",
        "* **C1**：若坚持做时间窗复发终点，应**限定在随访充分的那一小部分**（≥12 个月者）并"
        "把「随访不足」作为删失做生存分析；否则按评审建议选 C —— 保留二分类并弱化「预测复发」表述。",
        "* **C2**：`基线影像日期` / `原发手术日期` / `首次复发日期` 三列已齐，可直接回答"
        "「分析用 CT 是否早于手术与复发」；但本批数据里 CT 与手术的配对关系"
        "（哪一次 CT 进了组学流水线）需用 `yt_RDL.xlsx` 的 `qc` 影像号再确认一次。",
        "",
        "## 10. 产物",
        "",
        "* `yt_RDL_脂肪肉瘤候选_合并表_随访.xlsx`：`01_候选患者_合并` 新增随访列；"
        "`07_随访明细_出院小结`、`08_随访QC`、`09_影像时间线`、"
        "`10_复发随访_分析表`（临床/病理列 + 1/0/NA 编码，可直接建模）。",
        "* `yt_RDL_脂肪肉瘤候选_合并表_随访_患者级.csv`：患者级 CSV。",
        "* `scripts/yt_rdl_recurrence.py`、`scripts/yt_rdl_recurrence_audit.py`：生成与独立复核。",
        "* `scripts/yt_rdl_add_clinical.py`：把 `yt_RDL.xlsx/cln` 的 Gender / Age / KI67 / CDK4 / "
        "MDM2 / Surrounding involving / Tumor density / Satellite nodules 并入候选表"
        "（按拼音首字母关联 + 性别年龄校验：高 48 / 中 1 / 未匹配 60）。",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


CLIN_EXTRA = ["Gender", "Age", "KI67", "CDK4", "MDM2", "Surrounding involving",
              "Tumor density", "Satellite nodules"]


def analysis_table(merged: pd.DataFrame) -> pd.DataFrame:
    """分析用宽表：1/0/NA 编码 + 日期列，直接可入 R/Python 建模。

    编码：1 = 该时点前已复发；0 = 随访已覆盖该时点且未复发；NA = 随访不足或无法判定。
    """
    code = {"是": 1, "否": 0, "随访不足": pd.NA, "无法判定": pd.NA}
    d = pd.DataFrame({
        "Patient_ID_首字母": merged["拼音首字母"],
        "病历卡号": merged["病历卡号"],
        "身份证号": merged["身份证号"],
        "姓名": merged["姓名"],
        "证据等级": merged["证据等级码"],
        "匹配yt_RDL_Patient_ID": merged["匹配yt_RDL_Patient_ID"],
        "临床资料匹配": merged.get("临床资料匹配", ""),
    })
    for c in CLIN_EXTRA:                      # 临床/病理列（来自 yt_RDL.xlsx/cln）
        if c in merged.columns:
            d[c] = merged[c]
    d["手术日期"] = merged["原发手术日期"]
    d["基线日期"] = merged["基线日期"]
    d["基线来源"] = merged["基线来源"]
    d["基线可靠性"] = merged["基线可靠性"]
    d["基线影像日期"] = merged["基线影像日期"]
    d["末次影像评估日期"] = merged["末次影像评估日期"]
    d["末次随访日期"] = merged["末次随访日期"]
    d["随访月数"] = merged["随访月数"]
    d["首次复发日期"] = merged["首次复发日期"]
    d["复发证据来源"] = merged["复发证据来源"]
    d["复发证据部位提示"] = merged["复发证据部位提示"]
    d["无复发生存时间(月)"] = merged["无复发生存时间(月)"]
    for name, _ in HORIZONS:
        d[f"{name}(1/0/NA)"] = merged[name].map(code).astype("Int64")
        d[f"{name}_含转移(1/0/NA)"] = merged[name + "_含转移"].map(code).astype("Int64")
    return d


def qc_table(cand, merged, R, P, CT, SPEC, IMG, canon2aliases=None) -> pd.DataFrame:
    q = []

    def add(a, b, c=""):
        q.append({"检查项": a, "结果": b, "数量": c})

    add("候选患者数", "来自 01_候选患者_合并", len(cand))
    if canon2aliases:
        multi = {k: sorted(v) for k, v in canon2aliases.items() if len(v) > 1}
        add("一人多编号的患者", f"{len(multi)} 位（病历卡号/住院号 不一致，"
                            f"已按身份证号+姓名归并，见 01 表「编号别名(合并用)」）", len(multi))
        for k, v in list(multi.items())[:15]:
            add(f"　编号别名：{k}", " / ".join(v), len(v))
    add("出院小结记录数", "来自 work/jinshan_extracted_data.csv", len(R))
    add("有出院小结的候选患者", f"{R['MRN'].nunique()} / {len(cand)}",
        f"{R['MRN'].nunique()}/{len(cand)}")
    add("影像评估条目", f"影像报告 {len(CT)} 条 + 出院小结特殊检查 {len(SPEC)} 条 = {len(IMG)} 条",
        len(IMG))
    add("有影像评估的患者", f"{IMG['MRN'].nunique()} / {len(cand)}",
        f"{IMG['MRN'].nunique()}/{len(cand)}")
    add("可提取原发手术日的患者", f"{int(merged['基线来源'].eq('原发手术').sum())} 位（基线可靠性=高）",
        int(merged["基线来源"].eq("原发手术").sum()))
    add("仅能取首次影像日期作基线的患者", f"{int(merged['基线来源'].eq('首次影像日期').sum())} 位（可靠性=中）",
        int(merged["基线来源"].eq("首次影像日期").sum()))
    add("基线缺失", f"{int(merged['基线来源'].eq('缺失').sum())} 位（4 个随访列=无法判定）",
        int(merged["基线来源"].eq("缺失").sum()))
    for name, _ in HORIZONS:
        vc = merged[name].value_counts()
        add(f"{name} 分布", "，".join(f"{k}={v}" for k, v in vc.items()),
            int(vc.get("是", 0)))
    add("影像报告中复发阳性行数", "四列文本判定为复发阳性",
        int(CT["复发阳性"].sum()))
    add("出院小结特殊检查中复发阳性条目", "带日期影像条目判定为复发阳性",
        int(SPEC["复发阳性"].sum()))
    return pd.DataFrame(q)



# ── audit (merged) ─────────────────────────────────────────────
ROOT = PATHS.PROJECT_ROOT
XLSX = PATHS.FU_XLSX
BASE_XLSX = PATHS.CAND_XLSX
fail: list[str] = []


def chk(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not cond:
        fail.append(name)


def run_audit() -> int:
    xl = pd.ExcelFile(XLSX)
    base = pd.ExcelFile(BASE_XLSX)
    m = xl.parse("01_候选患者_合并")
    b = base.parse("01_候选患者_合并")
    H = ["复发_6月", "复发_1年", "复发_2年", "复发_3年"]
    HA = [h + "_含转移" for h in H]

    print("### 1. 结构")
    chk("候选患者行数与原表一致", len(m) == len(b), f"{len(m)} vs {len(b)}")
    chk("病历卡号集合与原表一致",
        set(m["病历卡号"].astype(str)) == set(b["病历卡号"].astype(str)))
    chk("新增了 4 个随访列", all(h in m.columns for h in H))
    chk("新增了 4 个含转移列", all(h in m.columns for h in HA))
    chk("新增了 Q2 需要的日期列",
        all(c in m.columns for c in ["原发手术日期", "基线影像日期", "首次复发日期", "末次随访日期",
                                     "原发手术日期"]))

    print("\n### 2. 取值域")
    ok = set()
    for h in H + HA:
        vals = set(m[h].dropna().unique())
        ok |= vals
        chk(f"{h} 取值合法", vals <= {"是", "否", "随访不足", "无法判定"}, str(sorted(vals)))

    print("\n### 3. 单调性（一旦『是』，其后各时点也必须『是』；『否』可退化为『随访不足』）")
    err = 0
    for hs, tag in ((H, "仅复发"), (HA, "含转移")):
        for _, r in m.iterrows():
            seen_yes = False
            for h in hs:
                v = r[h]
                if v == "是":
                    seen_yes = True
                elif seen_yes and v != "是":
                    err += 1
                    print(f"    已『是』却回落: {r['病历卡号']} {h}={v}")
    chk("『是』在更晚时点不回退", err == 0, f"{err} 处")

    print("\n### 4. 与事件日/基线日的算术一致性")
    err = 0
    for _, r in m.iterrows():
        base_d = pd.to_datetime(r["基线日期"], errors="coerce")
        ev = pd.to_datetime(r["首次复发日期"], errors="coerce")
        if pd.notna(ev):
            if pd.isna(base_d) or ev <= base_d:
                err += 1
                print("    复发日不晚于基线:", r["病历卡号"], r["基线日期"], r["首次复发日期"])
            months = (ev - base_d).days / 30.4375
            for h, mo in zip(H, [6, 12, 24, 36]):
                if r[h] == "是" and months > mo:
                    err += 1
                    print("    标是但超窗:", r["病历卡号"], h, f"实际 {months:.1f} 月")
            # 早于某时点的复发，该时点必须为「是」
            for h, mo in zip(H, [6, 12, 24, 36]):
                if months <= mo and r[h] != "是":
                    err += 1
                    print("    已在该窗内复发却非是:", r["病历卡号"], h, r[h])
        if pd.isna(ev) and r["复发_6月"] == "是":
            err += 1
            print("    无事件却标是:", r["病历卡号"])
    chk("复发日 + 随访窗口逻辑自洽", err == 0, f"{err} 处")

    print("\n### 5. 『否』必须有足够随访")
    err = 0
    for _, r in m.iterrows():
        base_d = pd.to_datetime(r["基线日期"], errors="coerce")
        last_i = pd.to_datetime(r["末次影像评估日期"], errors="coerce")
        ev = pd.to_datetime(r["首次复发日期"], errors="coerce")
        if pd.isna(base_d):
            if r["复发_3年"] != "无法判定":
                err += 1
                print("    基线缺失却非『无法判定』:", r["病历卡号"], r["复发_3年"])
            continue
        # 6 个月标"否" 时，末次影像必须 ≥ 基线+6 月（或用 30.44*6 天近似）
        if r["复发_6月"] == "否":
            if pd.isna(last_i) or (last_i - base_d).days < 180:
                err += 1
                print("    标否但随访不足 180 天:", r["病历卡号"], r["基线日期"], r["末次影像评估日期"])
    chk("『否』均有 ≥180 天影像随访（6 月档）", err == 0, f"{err} 处")

    print("\n### 6. 分析表编码一致")
    a = xl.parse("10_复发随访_分析表")
    chk("分析表行数 = 候选患者数", len(a) == len(m), f"{len(a)} vs {len(m)}")
    code = {"是": 1, "否": 0}
    err = 0
    for h in H:
        col = f"{h}(1/0/NA)"
        for i, r in m.iterrows():
            want = code.get(r[h], None)
            got = a.loc[i, col]
            if want is None:
                if pd.notna(got):
                    err += 1
            elif int(got) != want:
                err += 1
                print("    编码不符:", r["病历卡号"], h, r[h], got)
    chk("1/0/NA 编码与文本标签一致", err == 0, f"{err} 处")

    print("\n### 7. 事件确实来自原始文本（影像时间线或出院小结）")
    img = xl.parse("09_影像时间线")
    rec = xl.parse("07_随访明细_出院小结")
    ev_rows = m[m["首次复发日期"].notna()]
    miss = 0
    for _, r in ev_rows.iterrows():
        mrn, d, src = str(r["病历卡号"]), str(r["首次复发日期"]), str(r["复发证据来源"])
        if src == "出院小结诊断":
            sub = rec[(rec["MRN"].astype(str) == mrn) & (rec["入院"].astype(str) == d)]
            ok = len(sub) and sub["复发阳性"].any()
        else:
            sub = img[(img["MRN"].astype(str) == mrn) & (img["日期"].astype(str) == d)]
            ok = len(sub) and sub["复发阳性"].any()
        if not ok:
            miss += 1
            print("    事件日在对应来源中未标阳性:", mrn, d, src)
    chk("首次复发日期在对应来源中确有复发阳性证据", miss == 0, f"{miss} 处")

    print("\n" + "=" * 70)
    print("FAILED:", fail if fail else "无 —— 全部检查通过")
    return 1 if fail else 0

if __name__ == "__main__":
    raise SystemExit(main())
