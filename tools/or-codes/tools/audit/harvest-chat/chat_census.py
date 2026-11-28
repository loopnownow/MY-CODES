"""CHAT-CENSUS —— Cline 会话级普查（只读源，写 census 与水位线台账）。

用途
  1) 扫 Cline sessions 下每个会话的 messages.json，出会话级 census（脱敏）；
  2) 生成 `_Docs\\harvest\\CHAT-LEDGER.tsv`（会话级水位线台账，每次 harvest 后整表覆写）；
  3) 下次 harvest 用 `--since <watermark>` 只列新记录。

配置（改口径只改这里）
  ROOT / OUT_CENSUS / OUT_LEDGER
  CLASS_OVERRIDE  人工判定表：session_id -> (class, lanes, note)
  RED_PAT         脱敏：禁用网段 IP / 手机 / 身份证 / 长数字串
  HINT_*          无人工判定时的建议判级关键词

运行
  python -B chat_census.py --mark 1795650932821_uv9sb
  python -B chat_census.py --since 1795650932821_uv9sb
"""
from __future__ import annotations

import os

import argparse
import json
import re
from pathlib import Path

ROOT = Path(os.environ.get("ROOT", ""))
ROUND = r"20261126a-chat-history"
OUT_CENSUS = Path(r"{lab_root}\_Docs\harvest\rounds") / ROUND / "evidence" / "CHAT-CENSUS.tsv"
OUT_LEDGER = Path(r"{lab_root}\_Docs\harvest\CHAT-LEDGER.tsv")

# 人工判定表（按会话 id）：class / lane / note。class 见 CLASS_VALUES。
CLASS_OVERRIDE: dict[str, tuple[str, str, str]] = {
    "1795610561191_lgdgm": ("H1-harvested", "clean-data", "L 盘 (n) 副本清理：SHA 判等/同目录分组/配对保护/孤儿退役/_links 改指向"),
    "1795441023360_41trt": ("H1-harvested", "preprocess-imaging", "L-dcm2nii 收口：长路径前缀/整目录 rename/计数器不可信/三方对账"),
    "1795569017162_ekwg1": ("H1-harvested", "db-library", "AITEE 两级库退子库：设备维度先证有数据/DICOM 只进 dict/ref_leaf"),
    "1795501573983_865t5": ("H1-already-promoted", "preprocess-imaging", "多中心切分+MRI 序列黑白名单+DICOM→nii 台账 -> 01_project-org.md 13/15"),
    "1795650932821_uv9sb": ("H1-already-promoted", "db-library", "db 收口：meta.kind 判据/issuer 命名空间/姓名众数 -> 01_project-org.md 12.8/10/12"),
    "1795432627481_xkylc": ("H1-already-promoted", "db-library", "db 精简与去重（首轮）-> 02_or-skill.md 11（f-j 轮）"),
    "1795438154396_svvwg": ("H1-already-promoted", "db-library", "db 精简与去重（续轮）-> 02_or-skill.md 11（f-j 轮）"),
    "1795597542654_kuhi4": ("H1-already-promoted", "db-library", "库切换与名字落人 -> 01_project-org.md 12"),
    "1795569682957_5fb4e": ("H4-project", "preprocess-imaging", "zjl_RDL 任务 A/B 执行件，正文留课题 HANDOFF"),
    "1795609024953_wnfey": ("H4-project", "preprocess-imaging", "zjl_RDL 交接与任务 B 派单，正文留课题"),
    "1795419411647_emel3": ("harvest-op", "-", "11-23 harvest 轮（a-e）的操作会话，已在 LEDGER"),
    "1795680950489_akdf8": ("harvest-op", "clean-data+preprocess-imaging+db-library", "本轮（20261126a）自身会话"),
    "1782545358562_xybj8": ("H2-no-harvest", "-", "一次性 R 脚本审计；按预测概率排序的分类误差可视化判据列下轮 plot-figures 候选（本轮未抽）"),
}

CLASS_VALUES = (
    "H1-harvested", "H1-already-promoted", "H2-no-harvest", "H3-live-dep",
    "H4-project", "redline-phi", "harvest-op", "new",
)

RED_PAT = [
    (re.compile(r"\\\\1(?:0|72|92)\.\d{1,3}\.\d{1,3}\.\d{1,3}\\[\w$]+"), "<外置源盘>"),
    (re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<私网IP>"),
    (re.compile(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"), "<私网IP>"),
    (re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"), "<私网IP>"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "<手机>"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "<身份证>"),
    (re.compile(r"\b\d{15,}\b"), "<长数字>"),
]

HINT_H1 = ("dry-run", "台账", "REPORT", "去重", "入库", "建库", "建工程", "脚本",
           "校验", "判据", "or-rule", "harvest", "收口", "迁移", "重构")
HINT_H2 = ("看一下进度", "总结", "状态", "看一下目前", "多少", "为什么", "先给我建议")

# 本轮开始时刻（UTC）：此后的会话属"新记录"，交下次 harvest，不并入本轮水位线
NEW_AFTER = "2026-11-26T08:15:51Z"

# 分组规则（按 started 升序匹配第一条满足者）：(上界, class, lanes, note)
GROUP_RULES: list[tuple[str, str, str, str]] = [
    ("2026-11-19T00:00:00Z", "H2-no-harvest", "-",
     "早期项目会话（2026-06~10）；抽样 3 例均为一次性脚本/运行日志；个别可视化判据列下轮候选"),
    ("2026-11-23T10:00:00Z", "H1-already-promoted", "-",
     "a-e 轮已从 {DEL} 批次抽取；本会话为其操作现场（对应轮次见 LEDGER）"),
]


def rule_for(started: str) -> tuple[str, str, str] | None:
    for bound, cls, lanes, note in GROUP_RULES:
        if started and started < bound:
            return cls, lanes, note
    return None


LEDGER_FIELDS = [
    "session_id", "started", "ended", "n_user", "user_chars",
    "class", "lanes", "round", "artifact", "note", "watermark",
]
CENSUS_FIELDS = LEDGER_FIELDS + ["first_user", "last_user"]


def sanitize(text: str, limit: int = 240) -> str:
    for pat, rep in RED_PAT:
        text = pat.sub(rep, text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("...[trunc]" if len(text) > limit else "")


def load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def user_texts(msgs: list[dict]) -> list[str]:
    out: list[str] = []
    for m in msgs:
        if m.get("role") != "user":
            continue
        for c in m.get("content") or []:
            if c.get("type") != "text" or not c.get("text"):
                continue
            t = c["text"]
            t = re.sub(r"<hook_context.*?</hook_context>", "", t, flags=re.S)
            t = re.sub(r"<mode_notice>.*?</mode_notice>", "", t, flags=re.S)
            t = re.sub(r"</?user_input[^>]*>", "", t)
            t = re.sub(r"\[TASK RESUMPTION\].*", "", t)
            t = t.strip()
            if t and "The user has answered the question" not in t:
                out.append(t)
    return out


def suggest(texts: list[str]) -> str:
    blob = " ".join(texts)
    if "姓名" in blob and any(k in blob for k in ("登记", "写入", "整理", "xlsx", "花名册")):
        return "redline-phi"
    low = blob.lower()
    if any(k.lower() in low for k in HINT_H1):
        return "H1-cand"
    if any(k in blob for k in HINT_H2):
        return "H2-no-harvest"
    return "H2-no-harvest"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mark", default="", help="把该 session_id 标为水位线（watermark=Y）")
    ap.add_argument("--since", default="", help="只打印水位线之后启动的会话")
    args = ap.parse_args()

    rows: list[dict] = []
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir():
            continue
        meta = load(d / f"{d.name}.json")
        msgs_obj = load(d / f"{d.name}.messages.json")
        if meta is None and msgs_obj is None:
            continue
        msgs = (msgs_obj or {}).get("messages") or []
        texts = user_texts(msgs)
        started = str((meta or {}).get("started_at") or "")
        cls, lanes, note = CLASS_OVERRIDE.get(d.name, ("", "", ""))
        if not cls:
            if started and started > NEW_AFTER:
                cls, lanes, note = "new", "待定", "本轮开始后新增会话，交下次 harvest"
            else:
                got = rule_for(started)
                if got:
                    cls, lanes, note = got
                else:
                    cls, lanes, note = suggest(texts), "待定", "自动建议，待人工核"
        rows.append({
            "session_id": d.name,
            "started": str((meta or {}).get("started_at") or ""),
            "ended": str((meta or {}).get("ended_at") or ""),
            "n_user": len(texts),
            "user_chars": sum(len(t) for t in texts),
            "class": cls,
            "lanes": lanes,
            "round": ROUND if cls.startswith(("H1-harvested", "harvest-op")) else "",
            "artifact": "",
            "note": note,
            "watermark": "",
            "first_user": sanitize(texts[0] if texts else ""),
            "last_user": sanitize(texts[-1] if texts else ""),
        })

    rows.sort(key=lambda r: (r["started"] or "0"))
    mark = args.mark or next((r["session_id"] for r in reversed(rows) if r["class"] != "harvest-op"), "")
    for r in rows:
        r["watermark"] = "Y" if r["session_id"] == mark else "N"

    OUT_CENSUS.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CENSUS.open("w", encoding="utf-8", newline="") as f:
        f.write("\t".join(CENSUS_FIELDS) + "\n")
        for r in rows:
            f.write("\t".join(str(r[k]) for k in CENSUS_FIELDS) + "\n")

    OUT_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with OUT_LEDGER.open("w", encoding="utf-8", newline="") as f:
        f.write("\t".join(LEDGER_FIELDS) + "\n")
        for r in rows:
            f.write("\t".join(str(r[k]) for k in LEDGER_FIELDS) + "\n")

    stats: dict[str, int] = {}
    for r in rows:
        stats[r["class"]] = stats.get(r["class"], 0) + 1
    print(f"[ok] sessions={len(rows)} -> {OUT_CENSUS}")
    print(f"[ok] ledger -> {OUT_LEDGER}  watermark={mark}")
    print("[stat] " + "  ".join(f"{k}={v}" for k, v in sorted(stats.items())))
    if args.since:
        base = next((r["started"] for r in rows if r["session_id"] == args.since), args.since)
        new = [r for r in rows if (r["started"] or "") > base]
        print(f"[since] {args.since} ({base}) -> {len(new)} new")
        for r in new:
            print(f"  {r['session_id']}  {r['started']}  {r['class']:22s} {r['n_user']:>3} msgs  {r['first_user'][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
