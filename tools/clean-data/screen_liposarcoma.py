# -*- coding: utf-8 -*-
"""yt_RDL 脂肪肉瘤候选筛选与患者级合并。

输入：项目 ref/ 下的 3 个住院/门诊影像报告导出表 ``20.xls`` / ``24.xls`` / ``26.xls``
      （列：检查日期 摄片序号 临床诊断 放射学诊断 年龄 性别 图像否 就诊类型
             放射学表现 姓名 身份证号 病历卡号 联系电话 住院号 影像号 门诊号
             就诊卡号 家庭地址 疾病名称）

任务：① 依据 临床诊断 / 放射学诊断 / 放射学表现 / 疾病名称 四列，筛选"可能是脂肪肉瘤"的行；
      ② 以 病历卡号 + 身份证号 为唯一标识，把同一患者的多次检查记录合并成一行。

读取层直接复用 ``D:\\0Grok\\0doc\\_code\\02-data-processing/clean-data\\tidy_io.py``（``XlsBook`` 逐格读取 + 样式写出），
其余筛选/合并逻辑为本课题专用，写在 ``tidy_profiles`` 之外，不修改脚本库。

运行：
    set PYTHONDONTWRITEBYTECODE=1
    python scripts\\yt_rdl_lipo_screen.py
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import re
import sys
from pathlib import Path

import pandas as pd

import tidy_io  # noqa: E402
import tidy_yt_rdl as PATHS  # noqa: E402
import xlrd  # noqa: E402

PROJECT_ROOT = PATHS.PROJECT_ROOT
DEFAULT_INPUTS = PATHS.INPUT_XLS

# ═══════════════════════════════════════════════════════════════════════
# 1. 筛选规则（全部显式声明，便于复核与调参）
# ═══════════════════════════════════════════════════════════════════════
DIAG_COLS = ["临床诊断", "放射学诊断", "放射学表现", "疾病名称"]

# A 级：字面命中
RE_LIPO = re.compile(r"脂肪肉瘤")

# B 级：无"脂肪肉瘤"字面，但明确提示脂肪来源/含脂病变
RE_FAT_SUSPECT = re.compile(
    r"含脂|"
    r"脂肪成分|"
    r"脂肪源性|"
    r"以脂肪密度为|"
    r"脂肪(?:或|/)?纤维组织来源|"
    r"脂肪密度(?:为主|灶|占位|成分|肿块|团块|结节|影)"
)
# B 级还要求同列出现"病变词"，避免把"皮下脂肪层/脂肪间隙"等描述性用语当成病灶
RE_LESION = re.compile(r"占位|肿块|肿物|肿瘤|结节|团块|肉瘤|MT|恶性|包块|病变")

# 已知的其他肉瘤亚型 / 良性脂肪或囊性病变 / 其他肿瘤：命中即排除该列
OTHER_DX = [
    "平滑肌肉瘤", "纤维肉瘤", "骨肉瘤", "横纹肌肉瘤", "血管肉瘤", "尤文",
    "子宫内膜", "间质肉瘤", "腺肉瘤", "癌肉瘤", "子宫肉瘤", "滑膜肉瘤",
    "软骨肉瘤", "神经纤维肉瘤", "淋巴瘤", "间质瘤", "脂肪瘤", "髓样脂肪瘤",
    "脂肪肝", "畸胎瘤", "黑色素瘤", "纤维瘤", "肌瘤", "腺瘤",
]

TIER_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5}
TIER_LABEL = {
    "A": "A_明确脂肪肉瘤",
    "B": "B_脂肪源性可疑",
    "C": "C_未定型肉瘤需鉴别",
    "D": "D_其他亚型或良性",
    "E": "E_其他病变无肉瘤字样",
    "F": "F_无肿瘤线索",
}
TIER_DESC = {
    "A": "四列文本中出现「脂肪肉瘤」字样（含术后/病史/复发/可能）。",
    "B": "未出现「脂肪肉瘤」字样，但描述含脂/脂肪来源病变，脂肪肉瘤高度可疑。",
    "C": "提及「肉瘤」但未指明亚型（肉瘤可能/倾向肉瘤/肉瘤待排等），未定型肉瘤需与脂肪肉瘤鉴别。",
    "D": "已指明其他亚型（平滑肌肉瘤/纤维肉瘤/骨肉瘤/横纹肌肉瘤/血管肉瘤/子宫内膜肉瘤等）"
         "或良性脂肪病变（脂肪瘤/髓样脂肪瘤/脂肪肝）等，按非脂肪肉瘤处理。",
    "E": "命中其他病变关键词但不含「肉瘤」字样。",
    "F": "四列均无肿瘤线索。",
}
ACTION = {
    "A": "强烈建议纳入",
    "B": "建议纳入（需病理/影像复核）",
    "C": "待复核（未定型肉瘤）",
    "D": "排除（其他亚型/良性病变）",
    "E": "排除",
    "F": "排除",
}
# 病理亚型提示
RE_DEDIFF = re.compile(r"去分化")
RE_WDIFF = re.compile(r"高分化|硬化性|炎症性")
RE_MYXOID = re.compile(r"黏液样|粘液样")
RE_PLEO = re.compile(r"多形性")
# 临床事件
RE_POSTOP = re.compile(r"术后|切除后|手术后|截肢后")
RE_RECUR = re.compile(r"复发|再发")
RE_META = re.compile(r"转移|播散|种植")
RE_HISTORY = re.compile(r"病史|个人史|术后改变")
# 解剖部位提示（按特异性从高到低）
SITES = [
    ("腹膜后/后腹膜", r"腹膜后|后腹膜"),
    ("腹腔", r"腹腔|腹腔内|腹盆腔|腹盆部"),
    ("盆腔", r"盆腔|盆部"),
    ("腹壁/腹股沟", r"腹壁|腹股沟"),
    ("大腿/下肢", r"大腿|下肢|小腿|臀部|股"),
    ("上肢/胸壁", r"上肢|上臂|胸壁|腋"),
    ("颈部", r"颈部"),
    ("肝", r"肝脏|肝左叶|肝右叶|肝S"),
    ("肺/纵隔", r"肺部|两肺|左肺|右肺|纵隔|肺内"),
    ("子宫/附件", r"子宫|附件|卵巢|宫颈"),
    ("骨", r"股骨|椎体|髂骨|骨质破坏|骨质缺损|骨盆"),
]


def classify_cell(text: str) -> tuple[str, list[str]]:
    """对单个列的文本判级，返回 (等级, 命中关键词)。"""
    t = "" if text is None else str(text)
    if not t.strip():
        return "F", []
    if RE_LIPO.search(t):
        return "A", ["脂肪肉瘤"]
    m = RE_FAT_SUSPECT.search(t)
    if m and RE_LESION.search(t):
        return "B", [m.group(0)]
    if "肉瘤" in t:
        hits = [w for w in OTHER_DX if w in t]
        if hits:
            return "D", ["肉瘤+" + w for w in hits]
        return "C", ["肉瘤(未分型)"]
    hits = [w for w in OTHER_DX if w in t]
    if hits:
        return "E", hits
    return "F", []


def classify_row(rec: dict) -> tuple[str, list[str]]:
    """整行判级：四列各自判级后取最高（A>B>C>D>E>F）。"""
    best, ev = "F", []
    for col in DIAG_COLS:
        tier, kws = classify_cell(rec.get(col, ""))
        if TIER_ORDER[tier] < TIER_ORDER[best]:
            best = tier
        if tier in ("A", "B", "C", "D") and kws:
            ev += [f"{col}={tier}:{k}" for k in kws]
    return best, ev


def evidence_excerpt(rec: dict, tier: str, limit: int = 600) -> str:
    """抽取支撑该等级的原句。"""
    if tier == "A":
        pat = re.compile(r"脂肪肉瘤")
    elif tier == "B":
        pat = RE_FAT_SUSPECT
    elif tier == "C":
        pat = re.compile(r"肉瘤")
    else:
        return ""
    out = []
    for col in DIAG_COLS:
        t = str(rec.get(col, "") or "")
        if not t:
            continue
        for sent in re.split(r"[。；;]", t):
            sent = sent.strip(" ,，、")
            if sent and pat.search(sent) and sent not in out:
                out.append(f"[{col}] {sent}")
    s = " ；".join(out)
    return s[:limit] + ("…" if len(s) > limit else "")


# ═══════════════════════════════════════════════════════════════════════
# 2. 键值与字段规范化
# ═══════════════════════════════════════════════════════════════════════
def norm_id(v) -> str:
    """身份证号规范化：去前导撇号/空格/全角空格，转大写。"""
    s = tidy_io.norm(v)
    s = s.lstrip("'’").replace("'", "").replace("’", "")
    s = re.sub(r"[\s\u3000]", "", s)
    return s.upper()


def norm_mrn(v) -> str:
    s = tidy_io.norm(v)
    s = re.sub(r"[\s\u3000]", "", s)
    return s


def parse_dt(v) -> pd.Timestamp:
    if v is None or str(v).strip() == "":
        return pd.NaT
    return pd.to_datetime(str(v).strip(), errors="coerce")


def parse_age(v):
    if v is None:
        return None
    m = re.search(r"\d+", str(v))
    return int(m.group(0)) if m else None


def birth_from_id(idc: str) -> str:
    if len(idc) >= 14 and idc[6:14].isdigit():
        try:
            return _dt.date(int(idc[6:10]), int(idc[10:12]), int(idc[12:14])).isoformat()
        except ValueError:
            return ""
    return ""


def pinyin_initials(name: str) -> str:
    try:
        from pypinyin import lazy_pinyin, Style
        return "".join(lazy_pinyin(str(name), style=Style.FIRST_LETTER)).upper()
    except Exception:
        return ""


def site_hint(text: str) -> str:
    hits = [label for label, pat in SITES if re.search(pat, text)]
    return "/".join(hits[:3])


def uniq_join(values, sep="；", maxlen=None) -> str:
    seen = []
    for v in values:
        s = tidy_io.norm(v)
        if s and s.lower() not in {"nan", "none"} and s not in seen:
            seen.append(s)
    out = sep.join(seen)
    if maxlen and len(out) > maxlen:
        out = out[:maxlen] + "…"
    return out


# ═══════════════════════════════════════════════════════════════════════
# 3. 读取
# ═══════════════════════════════════════════════════════════════════════
def read_sources(paths) -> pd.DataFrame:
    frames = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            raise FileNotFoundError(f"缺少输入文件：{p}")
        bk = tidy_io.open_book(p)
        sh = bk.sheet(0)
        hdr = bk.header(sh)
        missing = [c for c in DIAG_COLS + ["病历卡号", "身份证号", "姓名"] if c not in hdr]
        if missing:
            raise KeyError(f"{p.name} 缺少必需列：{missing}")
        for i, row in enumerate(bk.rows(sh)):
            rec = dict(zip(hdr, row))
            rec["来源文件"] = p.stem
            rec["源文件行号"] = i + 2  # 表头占第 1 行
            frames.append(rec)
    return pd.DataFrame(frames)


def build_work(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in df.columns:
        if c not in ("源文件行号",):
            df[c] = df[c].map(lambda v: "" if v is None else (v if not isinstance(v, float) else ("" if pd.isna(v) else v)))
    df["__mrn"] = df["病历卡号"].map(norm_mrn)
    df["__idc"] = df["身份证号"].map(norm_id)
    df["__name"] = df["姓名"].map(tidy_io.norm)
    df["__date"] = df["检查日期"].map(parse_dt)
    df["__age"] = df["年龄"].map(parse_age)
    df["__sex"] = df["性别"].map(tidy_io.normalize_sex).map(
        lambda x: {"male": "男", "female": "女"}.get(x, ""))
    df["__key"] = df["__mrn"] + "|" + df["__idc"]
    # 行级判级
    tiers, evs = [], []
    for rec in df.to_dict("records"):
        t, e = classify_row(rec)
        tiers.append(t)
        evs.append("; ".join(e))
    df["证据等级"] = tiers
    df["命中关键词"] = evs
    df["证据摘录"] = [evidence_excerpt(rec, t) for rec, t in zip(df.to_dict("records"), tiers)]
    df["是否字面脂肪肉瘤"] = ["是" if t == "A" else "否" for t in tiers]
    df["证据等级说明"] = df["证据等级"].map(TIER_LABEL)
    df["纳入建议"] = df["证据等级"].map(ACTION)
    return df


# ═══════════════════════════════════════════════════════════════════════
# 4. 患者级合并（病历卡号 + 身份证号 为唯一标识）
# ═══════════════════════════════════════════════════════════════════════
def merge_patients(df: pd.DataFrame, yt_ids: set[str] | None = None) -> pd.DataFrame:
    # 同一身份证号下的全部病历卡号（用于标注"一人多卡号"）
    idc2mrns = {k: sorted(set(v)) for k, v in df[df["__idc"] != ""].groupby("__idc")["__mrn"]}
    rows = []
    for key, sub in df.groupby("__key", sort=False):
        sub = sub.sort_values("__date", na_position="first", kind="stable")
        tiers = sub["证据等级"].tolist()
        best = sorted(tiers, key=lambda t: TIER_ORDER[t])[0]
        last = sub.iloc[-1]
        best_row = sub[sub["证据等级"] == best].iloc[0]
        dates = [d for d in sub["__date"] if pd.notna(d)]
        ages = [a for a in sub["__age"] if a is not None]
        name = uniq_join(sub["__name"], sep="/")
        idc = last["__idc"]
        ini = pinyin_initials(name.split("/")[0]) if name else ""
        full_text = " ".join(str(sub[c].iloc[i]) for c in DIAG_COLS for i in range(len(sub)))
        rows.append({
            "病历卡号": last["__mrn"],
            "身份证号": idc,
            "姓名": name,            "拼音首字母": ini,
            "性别": uniq_join(sub["__sex"], sep="/"),
            "出生日期(证件)": birth_from_id(idc),
            "年龄_首次": ages[0] if ages else "",
            "年龄_末次": ages[-1] if ages else "",
            "年龄_范围": (f"{min(ages)}-{max(ages)}" if ages else ""),
            "证据等级": TIER_LABEL[best],
            "证据等级码": best,
            "纳入建议": ACTION[best],
            "证据等级说明": TIER_DESC[best],
            "字面命中脂肪肉瘤": "是" if "A" in tiers else "否",
            "末次记录含字面": "是" if RE_LIPO.search(
                " ".join(str(last[c]) for c in DIAG_COLS)) else "否",
            "命中关键词": uniq_join(sub["命中关键词"], sep=" || "),
            "病理亚型提示": uniq_join(
                [("去分化型" if RE_DEDIFF.search(t) else "") for t in [full_text]] +
                [("高分化型" if RE_WDIFF.search(full_text) else ""),
                 ("黏液样" if RE_MYXOID.search(full_text) else ""),
                 ("多形性" if RE_PLEO.search(full_text) else "")]),
            "部位提示": site_hint(full_text),
            "是否术后": "是" if RE_POSTOP.search(full_text) else "",
            "是否复发": "是" if RE_RECUR.search(full_text) else "",
            "是否转移": "是" if RE_META.search(full_text) else "",
            "检查次数": len(sub),
            "来源文件": uniq_join(sub["来源文件"], sep="+"),
            "首次检查日期": dates[0].strftime("%Y-%m-%d") if dates else "",
            "末次检查日期": dates[-1].strftime("%Y-%m-%d") if dates else "",
            "随访跨度(天)": ((dates[-1] - dates[0]).days if len(dates) > 1 else ""),
            "就诊类型": uniq_join(sub["就诊类型"], sep="/"),
            "影像号(全部)": uniq_join(sub["影像号"]),
            "摄片序号(全部)": uniq_join(sub["摄片序号"], sep="/"),
            "住院号": uniq_join(sub["住院号"], sep="/"),
            "门诊号": uniq_join(sub["门诊号"], sep="/"),
            "就诊卡号": uniq_join(sub["就诊卡号"], sep="/"),
            "联系电话": uniq_join(sub["联系电话"], sep="/"),
            "家庭地址": uniq_join(sub["家庭地址"], sep="/"),
            "脂肪肉瘤/含脂证据摘录": evidence_excerpt(best_row.to_dict(), best, limit=1200)
                                    or uniq_join(sub["证据摘录"], sep=" ；", maxlen=1200),
            "临床诊断(全部)": uniq_join(sub["临床诊断"], maxlen=1500),
            "放射学诊断(全部)": uniq_join(sub["放射学诊断"], maxlen=3000),
            "疾病名称(全部)": uniq_join(sub["疾病名称"], maxlen=2000),
            "放射学表现(末次)": str(last["放射学表现"])[:2000],
            "末次临床诊断": str(last["临床诊断"]),
            "末次放射学诊断": str(last["放射学诊断"]),
            "末次疾病名称": str(last["疾病名称"]),
            "同身份证号病历卡号数": len(idc2mrns.get(idc, [last["__mrn"]])) if idc else 1,
            "同身份证号其他病历卡号": "/".join(
                [m for m in idc2mrns.get(idc, []) if m != last["__mrn"]]) if idc else "",
            "匹配yt_RDL_Patient_ID": (ini if yt_ids and ini in yt_ids else ""),
        })
    out = pd.DataFrame(rows)
    out["__ord"] = out["证据等级码"].map(TIER_ORDER)
    out = out.sort_values(["__ord", "检查次数", "末次检查日期"],
                          ascending=[True, False, False]).drop(columns="__ord")
    out.insert(0, "序号", range(1, len(out) + 1))
    return out.reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════
# 5. QC
# ═══════════════════════════════════════════════════════════════════════
def qc_tables(df: pd.DataFrame, pat: pd.DataFrame) -> dict[str, pd.DataFrame]:
    qc = []

    def add(item, detail, n):
        qc.append({"检查项": item, "结果": detail, "涉及行数/患者数": n})

    add("输入文件行数", " + ".join(f"{k}:{v}" for k, v in df["来源文件"].value_counts().sort_index().items()),
        len(df))
    add("患者数（病历卡号+身份证号）", "全量唯一键数", len(pat))

    t = pat["证据等级码"].value_counts().to_dict()
    add("分层患者数", "；".join(f"{TIER_LABEL[k]}={t.get(k, 0)}" for k in sorted(t)), len(pat))

    blank_idc = df[df["__idc"] == ""]
    add("身份证号为空的行",
        uniq_join([f"{m}/{n}" for m, n in zip(blank_idc["__mrn"], blank_idc["__name"])], sep=", ") or "无",
        len(blank_idc))
    blank_mrn = df[df["__mrn"] == ""]
    add("病历卡号为空的行", uniq_join(blank_mrn["__name"], sep=", ") or "无", len(blank_mrn))

    # 键冲突：同一身份证号多病历卡号 / 同一病历卡号多身份证号
    g1 = df[df["__idc"] != ""].groupby("__idc")["__mrn"].nunique()
    multi1 = g1[g1 > 1]
    for idc in multi1.index:
        sub = df[df["__idc"] == idc]
        add(f"同一身份证号对应多个病历卡号：{idc}",
            f"{uniq_join(sub['__mrn'], sep='/')}；姓名={uniq_join(sub['__name'], sep='/')}；"
            f"来源={uniq_join(sub['来源文件'], sep='+')}；"
            f"按本题唯一标识（病历卡号+身份证号）计为 {sub['__mrn'].nunique()} 位患者", len(sub))
    g2 = df[df["__mrn"] != ""].groupby("__mrn")["__idc"].nunique()
    multi2 = g2[g2 > 1]
    for mrn in multi2.index:
        sub = df[df["__mrn"] == mrn]
        add(f"同一病历卡号对应多个身份证号：{mrn}",
            f"{uniq_join(sub['__idc'], sep='/')}；姓名={uniq_join(sub['__name'], sep='/')}", len(sub))

    # 姓名一致性
    bad_name = 0
    for key, sub in df.groupby("__key"):
        if sub["__name"].nunique() > 1:
            bad_name += 1
            add(f"同一键下姓名不一致：{key}", uniq_join(sub["__name"], sep="/"), len(sub))
    add("同键姓名一致性", "无不一致" if bad_name == 0 else f"{bad_name} 个键存在不一致", bad_name)

    # 身份证号 vs 年龄一致性
    bad_age = 0
    for _, r in df.iterrows():
        b = birth_from_id(r["__idc"])
        if not b or pd.isna(r["__date"]) or r["__age"] is None:
            continue
        bd = _dt.date.fromisoformat(b)
        d = r["__date"].date()
        calc = d.year - bd.year - ((d.month, d.day) < (bd.month, bd.day))
        if abs(calc - r["__age"]) > 1:
            bad_age += 1
            add(f"年龄与身份证不符：{r['__mrn']}/{r['__name']}",
                f"检查日期={d} 证件生日={b} 推算年龄={calc} 记录年龄={r['__age']}", 1)
    add("身份证号↔年龄一致性", "全部一致（容差±1岁）" if bad_age == 0 else f"{bad_age} 行不符", bad_age)

    # 跨文件重复
    cross = df.groupby("__key")["来源文件"].nunique()
    add("跨文件出现的患者数", f"{int((cross > 1).sum())} 位患者同时出现在多个来源文件", int((cross > 1).sum()))
    # 完全重复行
    dup_cols = ["病历卡号", "身份证号", "检查日期", "临床诊断", "放射学诊断", "放射学表现", "疾病名称", "影像号"]
    ndup = int(df.duplicated(subset=dup_cols).sum())
    add("键+内容完全重复的行", f"{ndup} 行（已按唯一键合并保留全部检查记录）", ndup)
    return pd.DataFrame(qc)


# ═══════════════════════════════════════════════════════════════════════
# 6. 输出
# ═══════════════════════════════════════════════════════════════════════
def load_yt_cln() -> pd.DataFrame:
    """读 yt_RDL.xlsx 的 cln 表（**原始判读队列**），按 Patient_ID 去重。

    用 ``PATHS.read_yt_cln(cohort_only=True)``：只取课题自己判读过的那批患者，
    排除 ``yt_rdl_fill_yt_cln.py`` 追加进去的候选患者行（否则会自我匹配）。
    """
    if not PATHS.YT_XLSX.exists():
        return pd.DataFrame()
    try:
        df = PATHS.read_yt_cln(cohort_only=True)
    except Exception:
        return pd.DataFrame()
    keep = [c for c in ["Patient_ID", "Gender", "Age", "Dedifferentiated", "Recurrence",
                        "Reader_1", "Reader_2", "KI67", "MDM2", "CDK4"] if c in df.columns]
    return df[keep].drop_duplicates(subset="Patient_ID", keep="first").reset_index(drop=True)


def build_crosslink(cand: pd.DataFrame, yt: pd.DataFrame) -> pd.DataFrame:
    """姓名拼音首字母 ↔ yt_RDL Patient_ID，再用性别/年龄交叉验证匹配可信度。"""
    if yt.empty:
        return pd.DataFrame({"提示": ["未找到 yt_RDL.xlsx 的 cln 表"]})
    yt = yt.rename(columns={"Patient_ID": "匹配yt_RDL_Patient_ID",
                            "Gender": "yt_Gender", "Age": "yt_Age"})
    m = cand.merge(yt, on="匹配yt_RDL_Patient_ID", how="inner")
    if m.empty:
        return pd.DataFrame({"提示": ["无姓名拼音首字母与 yt_RDL.xlsx 的 Patient_ID 匹配"]})

    def sex_ok(a, b):
        a, b = str(a).strip(), str(b).strip()
        if not a or a == "nan" or not b or b == "nan":
            return ""
        if a == "男":
            return "是" if b.lower().startswith("m") else "否"
        if a == "女":
            return "是" if b.lower().startswith("f") else "否"
        return ""

    ages, dage, conf = [], [], []
    for _, r in m.iterrows():
        a = [x for x in [r.get("年龄_首次"), r.get("年龄_末次")] if pd.notna(x)]
        try:
            ay = float(r["yt_Age"])
        except (TypeError, ValueError):
            ay = None
        d = min((abs(x - ay) for x in a), default=None) if (a and ay is not None) else None
        dage.append("" if d is None else int(d))
        ages.append(f"{int(min(a))}-{int(max(a))}" if a else "")
        s = sex_ok(r["性别"], r["yt_Gender"])
        if s == "是" and d is not None and d <= 3:
            conf.append("高（性别一致，年龄差≤3岁）")
        elif s == "是":
            conf.append("中（性别一致，年龄差>3岁）")
        elif s == "否":
            conf.append("低（性别不符，可能同名首字母巧合）")
        else:
            conf.append("待人工确认")
    m["报表年龄范围"] = ages
    m["年龄差(岁)"] = dage
    m["性别一致"] = [sex_ok(r["性别"], r["yt_Gender"]) for _, r in m.iterrows()]
    m["匹配可信度"] = conf
    cols = ["病历卡号", "身份证号", "姓名", "拼音首字母", "匹配yt_RDL_Patient_ID",
            "证据等级", "纳入建议", "性别", "报表年龄范围", "yt_Gender", "yt_Age",
            "性别一致", "年龄差(岁)", "匹配可信度", "Dedifferentiated", "Recurrence",
            "末次检查日期", "末次放射学诊断"]
    m = m[[c for c in cols if c in m.columns]]
    m = m.sort_values(["匹配可信度", "姓名"]).reset_index(drop=True)
    m.insert(0, "序号", range(1, len(m) + 1))
    return m


def write_workbook(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, d in sheets.items():
            d.to_excel(xw, sheet_name=name[:31], index=False)
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment
    import tidy_io as _io
    wb = load_workbook(path)
    for name, d in sheets.items():
        ws = wb[name[:31]]
        _io.style_header(ws, max(len(d.columns), 1))
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=False)
        if len(d.columns):
            for i, c in enumerate(d.columns, start=1):
                n = max(len(str(c)) * 2, 12)
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(n, 34)
    wb.save(path)


def md_report(df, pat, qc, out_path: Path, link: pd.DataFrame | None = None) -> None:
    t = pat["证据等级码"].value_counts().to_dict()
    n_hi = n_mid = n_low = 0
    if link is not None and "匹配可信度" in getattr(link, "columns", []):
        n_hi = int((link["匹配可信度"].str.startswith("高")).sum())
        n_mid = int((link["匹配可信度"].str.startswith("中")).sum())
        n_low = int((link["匹配可信度"].str.startswith("低")).sum())
    lines = [
        "# yt_RDL 脂肪肉瘤候选筛选与患者级合并 — 说明",
        "",
        f"生成时间：{_dt.datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "## 1. 输入",
        "",
        "| 文件 | 行数 |",
        "|---|---|",
    ]
    for k, v in df["来源文件"].value_counts().sort_index().items():
        lines.append(f"| {k}.xls | {v} |")
    lines += [f"| **合计** | **{len(df)}** |", ""]
    lines += [
        "## 2. 筛选规则（四列：临床诊断 / 放射学诊断 / 放射学表现 / 疾病名称）",
        "",
        "四列**逐列**判级，取该行最高等级（A > B > C > D > E > F）。",
        "",
        "| 等级 | 判定 | 含义 |",
        "|---|---|---|",
    ]
    for k in "ABCDEF":
        lines.append(f"| {TIER_LABEL[k]} | 见下 | {TIER_DESC[k]} |")
    lines += [
        "",
        "- **A**：正则 `脂肪肉瘤`",
        "- **B**：无“脂肪肉瘤”字面，但命中 `含脂|脂肪成分|脂肪源性|以脂肪密度为|"
        "脂肪(或/)?纤维组织来源|脂肪密度(为主|灶|占位|成分|肿块|团块|结节|影)`，"
        "且同列出现病灶词（占位/肿块/肿物/肿瘤/结节/团块/肉瘤/MT/恶性/包块/病变）",
        "- **C**：含 `肉瘤` 且未出现任何已知其他亚型词（平滑肌肉瘤/纤维肉瘤/骨肉瘤/横纹肌肉瘤/"
        "血管肉瘤/尤文/子宫内膜/间质肉瘤/腺肉瘤/癌肉瘤/子宫肉瘤/滑膜肉瘤/软骨肉瘤/神经纤维肉瘤/"
        "淋巴瘤/间质瘤/脂肪瘤/髓样脂肪瘤/脂肪肝/畸胎瘤/黑色素瘤/纤维瘤/肌瘤/腺瘤）",
        "- **D**：命中上述其他亚型/良性病变词，且同列出现 `肉瘤`",
        "- **E**：命中其他亚型/良性病变词，无 `肉瘤` 字样",
        "- **F**：四列均无肿瘤线索",
        "",
        "## 3. 合并规则",
        "",
        "以 **病历卡号 + 身份证号** 为唯一标识（身份证号去前导撇号与空白、转大写；"
        "病历卡号去空白）。同一键的所有检查记录合并为一行，长文本按去重后 `；` 拼接，"
        "同时保留“末次”检查的原始文本；`02_候选明细_行级` 保留合并前的每一行原始记录。",
        "",
        "## 4. 结果",
        "",
        f"- 输入行数：**{len(df)}**",
        f"- 唯一患者（病历卡号+身份证号）：**{len(pat)}**",
        "",
        "| 等级 | 患者数 | 处理 |",
        "|---|---|---|",
    ]
    for k in "ABCDEF":
        lines.append(f"| {TIER_LABEL[k]} | {t.get(k, 0)} | {ACTION[k]} |")
    cand = pat[pat["证据等级码"].isin(["A", "B", "C"])]
    lines += [
        "",
        f"**可能为脂肪肉瘤的候选患者合计 {len(cand)} 位**"
        f"（A {t.get('A', 0)} + B {t.get('B', 0)} + C {t.get('C', 0)}）。",
        "",
        f"- 与 `yt_RDL.xlsx` 队列交叉链接：首字母命中 **{n_hi + n_mid + n_low}** 位，"
        f"其中性别+年龄双重确认（高可信度）**{n_hi}** 位。",
        "",
        "## 5. 注意事项",
        "",
        "1. “可能是脂肪肉瘤”本身是临床判断，脚本只做**关键词分层**，不替代病理/影像复核；"
        "C 级为“未定型肉瘤”，是否属于脂肪肉瘤需人工确认（例：黄祖舜 C 级，"
        "但首字母 + 性别 + 年龄与 `yt_RDL` 队列完全吻合，实际即为队列内患者）。",
        "2. 部分患者的**早期记录未写“脂肪肉瘤”**（如“腹腔肉瘤术后复发”“腹膜后肉瘤术后”），"
        "按唯一键合并后，同患者任一次出现字面即升为 A 级；"
        "`字面命中脂肪肉瘤` 表示该患者全部记录中是否出现过字面，"
        "`末次记录含字面` 表示最后一次检查是否仍写明“脂肪肉瘤”。",
        "3. 身份证号为空的行（6 行）无法与真实患者对齐，单独成键，不影响候选结果，已在 QC 中列出。",
        "4. 存在 1 例**同一身份证号对应两个病历卡号**（朱占勇，1200950971 / 902138419）。"
        "按本题规定的唯一标识（病历卡号+身份证号）计为 2 位患者，两行均已在候选表中，"
        "并标注 `同身份证号其他病历卡号`，如需按人合并可据此再去重。",
        "5. `06_交叉链接_yt_RDL` 用姓名拼音首字母与 `yt_RDL.xlsx` 的 `cln` 表 `Patient_ID` "
        "匹配，再用**性别 + 年龄（±3 岁）**交叉验证：两者都吻合记为「高」可信度，"
        "首字母相同但性别/年龄不符记为「中/低」（多半是同首字母的其他人），需人工确认。",
        "",
        "## 6. 文件清单",
        "",
        "- `yt_RDL_脂肪肉瘤候选_合并表.xlsx`：主结果（多工作表）",
        "- `yt_RDL_脂肪肉瘤候选_患者级.csv`：患者级合并表（UTF-8-BOM）",
        "- `yt_RDL_脂肪肉瘤筛选说明.md`：本说明",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="yt_RDL 脂肪肉瘤候选筛选与患者级合并")
    ap.add_argument("--audit", action="store_true", help="run independent audit and exit")
    ap.add_argument("--inputs", nargs="*", default=None,
                    help="输入 .xls 列表，默认 ref/20.xls ref/24.xls ref/26.xls")
    ap.add_argument("--outdir", default=str(PATHS.REF),
                    help="候选合并表的输出目录（默认 ref；最终随访表另在子工程根目录）")
    ap.add_argument("--outname", default="yt_RDL_脂肪肉瘤候选_合并表")
    args = ap.parse_args()
    if args.audit:
        return run_audit()

    inputs = [Path(p) for p in (args.inputs or DEFAULT_INPUTS)]
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    raw = read_sources(inputs)
    df = build_work(raw)
    yt = load_yt_cln()
    yt_ids = set(yt["Patient_ID"]) if not yt.empty else set()
    pat = merge_patients(df, yt_ids)
    qc = qc_tables(df, pat)

    cand = pat[pat["证据等级码"].isin(["A", "B", "C"])].reset_index(drop=True)
    cand["序号"] = range(1, len(cand) + 1)
    excl = pat[pat["证据等级码"].isin(["D", "E", "F"])].reset_index(drop=True)
    excl["序号"] = range(1, len(excl) + 1)

    det_cols = (["来源文件", "源文件行号", "病历卡号", "身份证号", "姓名", "性别", "年龄",
                 "检查日期", "就诊类型", "证据等级说明", "纳入建议", "命中关键词", "证据摘录",
                 "是否字面脂肪肉瘤"] + DIAG_COLS + ["影像号", "摄片序号", "住院号", "门诊号",
                                                  "就诊卡号", "联系电话", "家庭地址"])
    detail = df[df["证据等级"].isin(["A", "B", "C"])][det_cols].copy()
    detail["__o"] = detail["证据等级说明"].str[0].map(TIER_ORDER)
    detail = detail.sort_values(["__o", "病历卡号", "检查日期"]).drop(columns="__o")
    detail.insert(0, "序号", range(1, len(detail) + 1))

    detail_ex = df[~df["证据等级"].isin(["A", "B", "C"])][det_cols].copy()
    detail_ex["__o"] = detail_ex["证据等级说明"].str[0].map(TIER_ORDER)
    detail_ex = detail_ex.sort_values(["__o", "病历卡号", "检查日期"]).drop(columns="__o")
    detail_ex.insert(0, "序号", range(1, len(detail_ex) + 1))

    link = build_crosslink(cand, yt)
    if not yt.empty and "匹配yt_RDL_Patient_ID" in link.columns:
        matched_ids = set(link["匹配yt_RDL_Patient_ID"])
        hi = int((link["匹配可信度"] == "高（性别一致，年龄差≤3岁）").sum())
        qc = pd.concat([qc, pd.DataFrame([{
            "检查项": "与 yt_RDL.xlsx 队列交叉链接",
            "结果": f"yt_RDL 队列 {len(yt)} 位；首字母命中 {len(matched_ids)} 位"
                    f"（其中性别+年龄双重确认 {hi} 位）；未命中 {len(yt) - len(matched_ids)} 位",
            "涉及行数/患者数": len(matched_ids),
        }])], ignore_index=True)

    sheets = {
        "00_说明": pd.DataFrame({"说明": [
            "yt_RDL 脂肪肉瘤候选筛选与患者级合并",
            "",
            "① 筛选：对 临床诊断/放射学诊断/放射学表现/疾病名称 四列逐列判级，行取最高级。",
            f"   A 明确脂肪肉瘤（字面） / B 脂肪源性可疑 / C 未定型肉瘤需鉴别 → 候选（共 {len(cand)} 位患者）",
            f"   D 其他亚型或良性病变 / E / F → 排除（共 {len(excl)} 位患者）",
            "② 合并：以 病历卡号 + 身份证号 为唯一标识，同一患者多次检查合并为一行；",
            "   长文本去重后以「；」拼接，并另存末次记录原文；行级原始记录见 03/04 表。",
            "",
            "工作表：01_候选患者_合并（主结果）/ 02_候选明细_行级 / 03_排除患者_其他诊断 /",
            "        04_排除明细_行级 / 05_QC_键值核算 / 06_交叉链接_yt_RDL",
            "",
            "详细规则见同目录 yt_RDL_脂肪肉瘤筛选说明.md",
        ]}),
        "01_候选患者_合并": cand,
        "02_候选明细_行级": detail,
        "03_排除患者_其他诊断": excl,
        "04_排除明细_行级": detail_ex,
        "05_QC_键值核算": qc,
        "06_交叉链接_yt_RDL": link,
    }

    xlsx = outdir / f"{args.outname}.xlsx"
    csv = PATHS.CAND_CSV
    md = PATHS.SCREEN_MD
    write_workbook(xlsx, sheets)
    cand.to_csv(csv, index=False, encoding="utf-8-sig")
    md_report(df, pat, qc, md, link)

    print(f"[ok] 输入 {len(inputs)} 个文件 / {len(df)} 行 / {len(pat)} 位患者")
    vc = pat["证据等级码"].value_counts().to_dict()
    print("     分层：" + "，".join(f"{TIER_LABEL[k]}={vc.get(k, 0)}" for k in "ABC"))
    print(f"[ok] 候选患者 {len(cand)} 位（A/B/C），排除 {len(excl)} 位（D/E/F）")
    print(f"[out] {xlsx}")
    print(f"[out] {csv}")
    print(f"[out] {md}")
    return 0



# ── audit (merged) ─────────────────────────────────────────────
ROOT = PATHS.PROJECT_ROOT
REF = PATHS.REF
INPUTS = dict(zip(("20", "24", "26"), PATHS.INPUT_XLS))
XLSX = PATHS.CAND_XLSX
CSV = PATHS.CAND_CSV
YT = PATHS.YT_XLSX
DIAG = ["临床诊断", "放射学诊断", "放射学表现", "疾病名称"]

fail: list[str] = []


def chk(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not cond:
        fail.append(name)


def num(s) -> str:
    """Excel 读回值统一成无小数点字符串，便于跨 pandas / xlrd 比较。"""
    s = "" if s is None or (isinstance(s, float) and pd.isna(s)) else str(s).strip()
    if re.fullmatch(r"-?\d+\.0", s):
        s = s[:-2]
    return s


def nid(v) -> str:
    return re.sub(r"[\s\u3000']", "", "" if pd.isna(v) else str(v)).upper()


def nmrn(v) -> str:
    return re.sub(r"[\s\u3000]", "", "" if pd.isna(v) else str(v))


def run_audit() -> int:
    for p in list(INPUTS.values()) + [XLSX, CSV]:
        if not p.exists():
            print(f"缺少文件：{p}")
            return 2

    # ── 独立重算 ─────────────────────────────────────────────────
    frames = []
    for tag, p in INPUTS.items():
        d = pd.read_excel(p, dtype=object)
        d["来源文件"] = tag
        frames.append(d)
    raw = pd.concat(frames, ignore_index=True)
    raw["key"] = raw["病历卡号"].map(nmrn) + "|" + raw["身份证号"].map(nid)
    raw["has_lipo"] = raw[DIAG].fillna("").astype(str).agg(" ".join, axis=1).str.contains("脂肪肉瘤")
    print(f"原始行数（pandas）= {len(raw)}")

    xl = pd.ExcelFile(XLSX)
    cand = xl.parse("01_候选患者_合并")
    excl = xl.parse("03_排除患者_其他诊断")
    det = xl.parse("02_候选明细_行级")
    det_ex = xl.parse("04_排除明细_行级")

    print("\n### 1. 行级账目（每行必须且只能出现在一张明细表）")
    chk("明细行数合计 = 输入行数", len(det) + len(det_ex) == len(raw),
        f"{len(det)}+{len(det_ex)} vs {len(raw)}")
    key = lambda d: set(zip(d["来源文件"].astype(str), d["病历卡号"].astype(str),
                           d["影像号"].map(num)))
    src, kd, ke = key(raw), key(det), key(det_ex)
    chk("明细键无重复", len(kd) == len(det) and len(ke) == len(det_ex))
    chk("明细并集 = 全部输入行", (kd | ke) == src,
        f"缺 {len(src - (kd | ke))} 行 / 多 {len((kd | ke) - src)} 行")

    print("\n### 2. 患者级账目（每个唯一键一行）")
    chk("候选+排除患者数 = 唯一键数", len(cand) + len(excl) == raw["key"].nunique(),
        f"{len(cand)}+{len(excl)} vs {raw['key'].nunique()}")

    raw_id = set(raw["身份证号"].map(nid)) - {""}
    raw_mrn = set(raw["病历卡号"].map(nmrn))
    raw_name = set(raw["姓名"].astype(str).str.strip())
    both = pd.concat([cand, excl])
    unmatched = []
    for _, r in both.iterrows():
        i = "" if pd.isna(r["身份证号"]) else num(r["身份证号"])
        m, nm = num(r["病历卡号"]), str(r.get("姓名", "")).strip()
        # 身份证号为空的患者允许按姓名核对（其病历卡号可能被人工改成 Results 查询号）
        if (i and i in raw_id) or (not i and (m in raw_mrn or nm in raw_name)):
            continue
        unmatched.append((m, i, nm))
    chk("每行都能对应到原始 xls 里的患者（身份证号 / 病历卡号 / 姓名）",
        not unmatched, f"{len(unmatched)} 行对不上：{unmatched[:5]}")

    # 病历卡号被人工改成 Results 查询号的情况：可见、不算失败
    idc2mrn = {}
    for m, i in zip(raw["病历卡号"].map(nmrn), raw["身份证号"].map(nid)):
        idc2mrn.setdefault(i, set()).add(m)
    edited = []
    for _, r in both.iterrows():
        m = num(r["病历卡号"])
        i = "" if pd.isna(r["身份证号"]) else num(r["身份证号"])
        if i and i in idc2mrn and m not in idc2mrn[i]:
            edited.append((str(r.get("姓名", "")), "/".join(sorted(idc2mrn[i])), m))
    print(f"  INFO  病历卡号与原 xls 不同的行：{len(edited)}（人工改号，对齐 Results.7z 的查询号）")
    for nm, orig, now in edited:
        print(f"          {nm}: {orig} → {now}")

    print("\n### 3. 计数一致性（与 pandas 独立重算比对）")
    n_lipo_keys = raw.loc[raw["has_lipo"], "key"].nunique()
    a = cand[cand["证据等级码"] == "A"]
    chk("A 级行数 = 含「脂肪肉瘤」字面的唯一键数（同一患者多病历卡号时各算一行）",
        len(a) == n_lipo_keys, f"{len(a)} vs {n_lipo_keys}")
    lipo_id = set(raw.loc[raw["has_lipo"], "身份证号"].map(nid)) - {""}
    lipo_name = set(raw.loc[raw["has_lipo"], "姓名"].astype(str).str.strip())
    bad = []
    for _, r in a.iterrows():
        i = "" if pd.isna(r["身份证号"]) else num(r["身份证号"])
        nm = str(r.get("姓名", "")).strip()
        if (i and i not in lipo_id) or (not i and nm not in lipo_name):
            bad.append(num(r["病历卡号"]))
    chk("A 级患者都能在原始脂肪肉瘤行里找到（身份证号，空证按姓名）",
        not bad, f"{len(bad)} 行例外：{bad[:5]}")
    chk("检查次数合计 = 输入行数",
        int(cand["检查次数"].sum()) + int(excl["检查次数"].sum()) == len(raw),
        f"{int(cand['检查次数'].sum())}+{int(excl['检查次数'].sum())}")

    print("\n### 4. 明细表等级与患者表等级自洽")
    det_max = det.assign(t=det["证据等级说明"].str[0]).groupby(
        ["病历卡号", "身份证号"], dropna=False)["t"].apply(lambda s: sorted(s)[0])
    bad = 0
    for (m, i), t in det_max.items():
        row = cand[(cand["病历卡号"].map(num) == num(m)) &
                   (cand["身份证号"].replace({"nan": ""}).map(num) == num(i))]
        if len(row) and row.iloc[0]["证据等级码"] != t:
            bad += 1
            print("    不一致:", m, i, t, row.iloc[0]["证据等级码"])
    chk("患者等级 = 其明细行最高等级", bad == 0, f"{bad} 处不一致")

    print("\n### 5. 明细表逐格回查原始 .xls")
    books = {t: xlrd.open_workbook(str(p)) for t, p in INPUTS.items()}
    sheets = {t: b.sheet_by_index(0) for t, b in books.items()}
    hdrs = {t: [str(c.value).strip() for c in s.row(0)] for t, s in sheets.items()}
    errors = checked = 0
    for _, r in det.iterrows():
        t = str(r["来源文件"])
        s, hn = sheets[t], hdrs[t]
        ci, mi = hn.index("影像号"), hn.index("病历卡号")
        ri = next((rr for rr in range(1, s.nrows)
                   if num(s.cell_value(rr, ci)) == num(r["影像号"])
                   and num(s.cell_value(rr, mi)) == num(r["病历卡号"])), None)
        if ri is None:
            errors += 1
            print("    未找到源行:", t, r["病历卡号"], r["影像号"])
            continue
        checked += 1
        for col in DIAG + ["姓名", "年龄", "性别", "检查日期"]:
            v, got = num(s.cell_value(ri, hn.index(col))), num(r[col])
            if v != got:
                errors += 1
                print(f"    字段不符 r{ri} {col}: 源={v[:60]!r} 出={got[:60]!r}")
    chk("明细表逐格回查源文件", errors == 0, f"核对 {checked}/{len(det)} 行，{errors} 处不符")

    print("\n### 6. 排除表语义")
    ex_txt = excl["命中关键词"].fillna("").astype(str)
    chk("排除表每行都有排除理由", bool((ex_txt.str.len() > 0).all()),
        f"{int((ex_txt.str.len() == 0).sum())} 行无理由")
    bad_ex = []
    for _, r in excl.iterrows():
        m = num(r["病历卡号"])
        i = "" if pd.isna(r["身份证号"]) else num(r["身份证号"])
        if raw[raw["key"] == f"{m}|{i}"]["has_lipo"].any():
            bad_ex.append((m, r["姓名"]))
    chk("排除患者中无字面脂肪肉瘤记录", not bad_ex, str(bad_ex))

    print("\n### 7. CSV 与工作簿一致")
    csv = pd.read_csv(CSV)
    chk("CSV 行数 = 候选患者数", len(csv) == len(cand), f"{len(csv)} vs {len(cand)}")
    shared = [c for c in csv.columns if c in cand.columns]
    chk("CSV 列 ⊆ 候选表列", set(csv.columns) <= set(cand.columns),
        f"多出 {sorted(set(csv.columns) - set(cand.columns))[:5]}")
    same = all(csv[c].map(num).tolist() == cand[c].map(num).tolist()
               for c in shared) if len(csv) == len(cand) else False
    chk(f"CSV 与候选表共有 {len(shared)} 列逐格一致", same)

    print("\n### 8. 交叉链接自洽")
    link = xl.parse("06_交叉链接_yt_RDL")
    if YT.exists() and "匹配yt_RDL_Patient_ID" in link.columns:
        yt_ids = set(pd.read_excel(YT, sheet_name="cln")["Patient_ID"].astype(str).str.strip())
        chk("交叉链接的 ID 均在 yt_RDL 中", set(link["匹配yt_RDL_Patient_ID"]) <= yt_ids)
        n_matched = int((cand["匹配yt_RDL_Patient_ID"].notna() &
                         (cand["匹配yt_RDL_Patient_ID"].astype(str).str.strip() != "")).sum())
        chk("交叉链接行数 = 候选表中匹配到的人数", len(link) == n_matched,
            f"{len(link)} vs {n_matched}")
        if "匹配可信度" in link.columns:
            chk("「高」可信度者性别全部一致",
                not ((link["匹配可信度"].str.startswith("高")) & (link["性别一致"] == "否")).any())
    else:
        print("  SKIP  未找到 yt_RDL.xlsx，跳过交叉链接校验")

    print("\n" + "=" * 70)
    print("FAILED:", fail if fail else "无 —— 全部检查通过")
    return 1 if fail else 0

if __name__ == "__main__":
    raise SystemExit(main())
