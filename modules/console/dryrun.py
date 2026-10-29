"""控制台 dry-run 预览层（B4c4：从 console.py 逐字搬出）。

依赖 schema_ui（设置归一 / 去重）与 ops（jsonable、overlay 助手）；不 import console，
也不 import http_impl；console 反向回导本模块的同名符号。
运行: 由 console.py / http_impl.py 导入，不单独执行。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from modules.console.ops import jsonable
from modules.console.schema_ui import _uniq_keep_str, hydrate_console_settings

def _parse_pair_list(raw) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in list(raw or []):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            a, b = str(item[0]).strip(), str(item[1]).strip()
        elif isinstance(item, str) and "vs" in item.lower():
            a, b = [x.strip() for x in item.replace("VS", "vs").split("vs", 1)]
        else:
            continue
        if not a or not b or a == b:
            continue
        key = f"{a}\0{b}"
        if key in seen:
            continue
        seen.add(key)
        out.append((a, b))
    return out

def _levels_from_outcome_dist(
    preview: dict[str, Any], outcome: str = "", subgroup: str = ""
) -> list[str]:
    """亚组水平：优先 SUBGROUP_COL，否则该结局列。"""
    skip = {"", "nan", "none", "nat", "<na>"}
    col = str(subgroup or "").strip() or str(outcome or "").strip()
    clin = preview.get("clin_dist") or {}
    dist = preview.get("outcome_dist") or {}
    src = None
    if col and isinstance(clin.get(col), dict):
        src = clin[col]
    elif col and isinstance(dist.get(col), dict):
        src = dist[col]
    if src is not None:
        return _uniq_keep_str(
            k for k in src
            if str(k).strip() and str(k).strip().lower() not in skip
        )
    return _uniq_keep_str(preview.get("group_levels") or [])

def _levels_for_pairs(
    merged: dict[str, Any], preview: dict[str, Any], outcome: str = ""
) -> list[str]:
    keep = [str(x).strip() for x in (merged.get("GROUP_KEEP") or []) if str(x).strip()]
    if len(keep) >= 2:
        return _uniq_keep_str(keep)
    extra: list[str] = []
    for a, b in _parse_pair_list(merged.get("PAIRWISE_GROUPS")):
        extra.extend([a, b])
    sub = str(merged.get("SUBGROUP_COL") or "").strip()
    prev = _levels_from_outcome_dist(preview, outcome, sub)
    return _uniq_keep_str(prev + extra)

def resolve_dry_pairs(
    merged: dict[str, Any], preview: dict[str, Any], outcome: str = ""
) -> list[tuple[str, str]]:
    parsed = _parse_pair_list(merged.get("PAIRWISE_GROUPS"))
    if parsed:
        return parsed
    if not bool(merged.get("PAIRWISE_ALL")):
        return []
    labels = _levels_for_pairs(merged, preview, outcome)
    if len(labels) < 2:
        return []
    return [(a, b) for i, a in enumerate(labels) for b in labels[i + 1 :]]

def _pair_display(merged: dict[str, Any], outcome: str, pos: str, neg: str) -> tuple[str, str]:
    """对子展示名：PAIRWISE_OVERRIDES > GROUP_LEVEL_DISPLAY > 列值。"""
    from modules.config.outcome import pair_key, level_display_name

    pw = (merged.get("PAIRWISE_OVERRIDES") or {}).get(outcome) or {}
    ov = pw.get(pair_key(pos, neg)) if isinstance(pw, dict) else None
    ov = ov if isinstance(ov, dict) else {}
    mp = merged.get("GROUP_LEVEL_DISPLAY") or {}
    dp = ov.get("GROUP_DISPLAY_POS")
    dn = ov.get("GROUP_DISPLAY_NEG")
    pos_d = str(dp).strip() if dp not in (None, "") else level_display_name(pos, mapping=mp)
    neg_d = str(dn).strip() if dn not in (None, "") else level_display_name(neg, mapping=mp)
    return pos_d, neg_d

def _outcome_cfg(merged: dict[str, Any], outcome: str) -> dict[str, Any]:
    out = dict(merged)
    ov = (merged.get("OUTCOME_OVERRIDES") or {}).get(outcome) or {}
    if isinstance(ov, dict):
        out.update(ov)
    return out

def _pair_n(preview: dict[str, Any], pos: str, neg: str, outcome: str = "") -> str:
    dist = preview.get("outcome_dist") or {}
    vc = dist.get(outcome) if outcome else None
    if not isinstance(vc, dict):
        vc = next((v for v in dist.values() if isinstance(v, dict)), {}) or {}
    counts: dict[str, int] = {}
    for k, n in vc.items():
        try:
            counts[str(k)] = int(n)
        except (TypeError, ValueError):
            continue
    if pos in counts or neg in counts:
        return f"n≈{counts.get(pos, 0)}/{counts.get(neg, 0)}"
    return ""

def _exclude_file_log(raw: Any) -> str:
    """日志里的排除文件名。一个路径或「a.xlsx + b.xlsx」。"""
    if isinstance(raw, (list, tuple)):
        names = [Path(str(x)).name for x in raw if str(x).strip()]
        return " + ".join(names) if names else "false_classification.xlsx"
    text = str(raw or "").strip()
    return text or "false_classification.xlsx"

def plan_dry_dests(merged: dict[str, Any], preview: dict[str, Any]) -> list[dict[str, Any]]:
    from modules.config.outcome import (
        map_outcome_out_dir, pair_dir_name, results_html_name, safe_dir_token,
        subproject_root,
    )

    outcomes = [str(x).strip() for x in (merged.get("OUTCOME_COLS") or []) if str(x).strip()]
    if not outcomes:
        outcomes = [str(merged.get("GROUP_SOURCE_COL") or "").strip() or "Group"]
    mapping = merged.get("SUBPROJECT_BY_OUTCOME") or {}
    if not isinstance(mapping, dict):
        mapping = {}
    use_subdir = bool(merged.get("OUTCOME_OUT_SUBDIR", True))
    rows: list[dict[str, Any]] = []
    for outcome in outcomes:
        cfg_o = _outcome_cfg(merged, outcome)
        parent = map_outcome_out_dir(outcome, "", mapping, use_subdir)
        pairs = resolve_dry_pairs(cfg_o, preview, outcome)
        if not pairs:
            sub = subproject_root(outcome, parent, mapping, use_subdir) or outcome
            html = results_html_name(sub)
            dest = str(parent or outcome or "<工程根>").replace("\\", "/")
            rows.append({
                "outcome": outcome,
                "pair": None,
                "pos": "",
                "neg": "",
                "disp_pos": "",
                "disp_neg": "",
                "dest": dest,
                "html": f"{dest}/{html}" if dest and dest != "<工程根>" else html,
                "n": "",
                "tabs": False,
            })
            continue
        tabs = len(pairs) >= 3
        for pos, neg in pairs:
            disp_pos, disp_neg = _pair_display(merged, outcome, pos, neg)
            folder = pair_dir_name(disp_pos, disp_neg)
            dest = f"{parent}/{folder}" if parent else folder
            dest = dest.replace("\\", "/")
            html = f"{safe_dir_token(f'{disp_pos}_vs_{disp_neg}')}-results.html"
            rows.append({
                "outcome": outcome,
                "pair": (pos, neg),
                "pos": pos,
                "neg": neg,
                "disp_pos": disp_pos,
                "disp_neg": disp_neg,
                "dest": dest,
                "html": f"{dest}/{html}",
                "n": _pair_n(preview, pos, neg, outcome),
                "tabs": tabs,
            })
    return rows

def _td_roc_name_list(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(x).strip() if x is not None else "" for x in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []

def _td_roc_dry_lines(merged: dict[str, Any], clin_cols: list[str]) -> list[str]:
    from modules.config.ini_io import coerce_ipcw_horizons, coerce_td_roc_field
    from modules.stats.data_io import landmark_month_from_status, landmark_pairs

    cols = {str(c) for c in clin_cols}
    listed = ", ".join(clin_cols) if clin_cols else "（预览未提供临床列）"
    lines: list[str] = []

    if not merged.get("DO_LANDMARK_ROC"):
        lines.append("Landmark ROC: off")
    else:
        try:
            times = _td_roc_name_list(
                coerce_td_roc_field("TD_ROC_TIMES", merged.get("TD_ROC_TIMES", ""))
            )
            stats = _td_roc_name_list(
                coerce_td_roc_field("TD_ROC_STATUS_COLS", merged.get("TD_ROC_STATUS_COLS", ""))
            )
            pairs = landmark_pairs(times, stats)
        except ValueError as e:
            lines.append(f"Landmark ROC: on  ⚠ {e}")
            pairs = None
        if pairs is not None and not pairs:
            lines.append(
                "Landmark ROC: on  ⚠ 未配置时间列/状态列对（TD_ROC_TIMES / TD_ROC_STATUS_COLS）"
            )
        elif pairs:
            month_errs: list[str] = []
            bits: list[str] = []
            for p in pairs:
                if p["time_col"]:
                    bits.append(f"{p['time_col']}→{p['status_col']}")
                    continue
                try:
                    m = landmark_month_from_status(p["status_col"])
                    bits.append(f"{m:g}m→{p['status_col']}")
                except ValueError as e:
                    month_errs.append(str(e))
                    bits.append(f"?→{p['status_col']}")
            missing = [
                name for p in pairs for name in (p["time_col"], p["status_col"])
                if name and name not in cols
            ]
            line = f"Landmark ROC: on  pairs={len(pairs)}  " + "; ".join(bits)
            if month_errs:
                line += "  ⚠ " + "；".join(month_errs)
            if missing:
                line += f"  ⚠ 缺列 {missing!r}。临床列: {listed}"
            elif not clin_cols:
                line += f"  ⚠ 临床列未知，无法核对缺列。临床列: {listed}"
            lines.append(line)

    if not merged.get("DO_TIME_DEPENDENT_ROC"):
        lines.append("时间依赖性 ROC（IPCW）: off")
        return lines

    time_col = str(merged.get("TD_IPCW_TIME_COL") or "").strip()
    status_col = str(merged.get("TD_IPCW_STATUS_COL") or "").strip()
    try:
        horizons = coerce_ipcw_horizons(merged.get("TD_IPCW_HORIZONS"))
    except ValueError as e:
        lines.append(f"时间依赖性 ROC（IPCW）: on  ⚠ {e}")
        return lines
    if not time_col or not status_col or not horizons:
        lines.append(
            "时间依赖性 ROC（IPCW）: on  ⚠ 须填 TD_IPCW_TIME_COL、"
            f"TD_IPCW_STATUS_COL、TD_IPCW_HORIZONS。"
            f"当前时间列 {time_col!r}，状态列 {status_col!r}，评估月 {horizons!r}"
        )
        return lines
    missing = [c for c in (time_col, status_col) if c not in cols]
    line = (
        f"时间依赖性 ROC（IPCW）: on  time={time_col!r}  "
        f"status={status_col!r}  horizons={horizons}"
    )
    if missing:
        line += f"  ⚠ 缺列 {missing!r}。临床列: {listed}"
    elif not clin_cols:
        line += f"  ⚠ 临床列未知，无法核对缺列。临床列: {listed}"
    lines.append(line)
    return lines

def dry_run_text(project: str, data: Path, settings_path: Path,
                 settings: dict[str, Any], overlay: dict[str, Any],
                 preview: dict[str, Any],
                 run_outcome: str = "") -> str:
    from modules.config.ini_io import migrate_retired_settings

    merged = migrate_retired_settings({**settings, **overlay})
    hydrate_console_settings(merged)
    outcomes = [str(x).strip() for x in (merged.get("OUTCOME_COLS") or []) if str(x).strip()]
    run_one = str(run_outcome or "").strip()
    if run_one:
        outcomes = [run_one]
    levels = _levels_from_outcome_dist(
        preview,
        str(merged.get("GROUP_SOURCE_COL") or "").strip(),
        str(merged.get("SUBGROUP_COL") or "").strip(),
    )
    if not levels:
        levels = [str(x) for x in (preview.get("group_levels") or []) if str(x)]
    plan = plan_dry_dests(merged, preview)
    if run_one:
        plan = [r for r in plan if r.get("outcome") == run_one]
    n_diff = sum(1 for k, v in overlay.items() if jsonable(settings.get(k)) != jsonable(v))
    src_label = "settings.ini" if str(settings_path).lower().endswith(".ini") else "settings.py"

    lines = [
        "═══ DRY-RUN（不建模、不写图）═══",
        f"project     : {project}",
        f"data        : {data}",
        f"{src_label:<12}: {settings_path}",
        f"overlay     : 提交 {len(overlay)} 键，其中 {n_diff} 项与磁盘配置不同（页面优先）",
        f"run scope   : {'子结局 ' + run_one if run_one else '公共页 · 全部结局'}",
        f"outcomes    : {outcomes or '(none)'}",
        f"seed / split: {merged.get('SEED')} / {merged.get('TRAIN_RATIO')}"
        + (
            f"  SPLIT_COL={merged.get('SPLIT_COL')}"
            if str(merged.get("SPLIT_COL") or "").strip()
            else ""
        ),
        f"VAL_MODE    : {merged.get('VAL_MODE', 'refit')}",
        "",
        "sheets:",
    ]
    for s in preview.get("sheets") or []:
        inf = (preview.get("sheet_info") or {}).get(s) or {}
        lines.append(f"  - {s}: {inf.get('n_rows')} × {inf.get('n_cols')}")
    idc = preview.get("id_check") or {}
    if idc:
        lines += [
            "",
            f"ID  {idc.get('clin_id_col')} ↔ {idc.get('rad_id_col')}: "
            f"clin={idc.get('clin_n')} rad={idc.get('rad_n')} "
            f"overlap={idc.get('overlap')} "
            f"clin_only={idc.get('clin_only')} rad_only={idc.get('rad_only')}",
        ]
    exc = preview.get("exclude") or {}
    row_s = exc.get("row") if exc.get("row") not in (None, "", []) else merged.get("EXCLUDE_ROW") or "（整列当 ID）"
    lines.append(
        f"exclude     : n={exc.get('n', 0)}  DO_EXCLUDE={merged.get('DO_EXCLUDE_SAMPLES')}  "
        f"file={exc.get('file') or _exclude_file_log(merged.get('EXCLUDE_FILE'))}  "
        f"sheet={exc.get('sheet') or merged.get('EXCLUDE_SHEET')}  "
        f"col={exc.get('col') or merged.get('EXCLUDE_COL')}  "
        f"row={row_s}"
    )
    lines.append(
        f"survival    : DO_SURVIVAL={merged.get('DO_SURVIVAL', False)}  "
        f"time_col={merged.get('SURVIVAL_TIME_COL') or '（未选）'}  "
        f"status={merged.get('TD_IPCW_STATUS_COL') or '（未选，KM 用当前结局）'}  "
        f"unit={merged.get('SURVIVAL_TIME_UNIT', 'months')}  "
        f"by_risk={merged.get('DO_SURVIVAL_BY_RISK', True)}  "
        f"cox={merged.get('DO_COX', False)}"
    )
    lines += _td_roc_dry_lines(merged, [str(c) for c in (preview.get("clin_cols") or [])])
    lines.append(
        f"fig palette : {merged.get('FIG_PALETTE') or 'npg'}  "
        f"main={merged.get('FIG_COLORS_MAIN')}  aux={merged.get('FIG_COLORS_AUX')}"
    )
    if preview.get("n_after_exclude_est") is not None:
        lines.append(f"n(overlap−exc) ≈ {preview['n_after_exclude_est']}")
    if preview.get("outcome_dist"):
        lines.append("outcome dist:")
        for col, dist in preview["outcome_dist"].items():
            bits = ", ".join(f"{k}={v}" for k, v in dist.items())
            lines.append(f"  {col}: {bits}")

    lines.append("")
    lines.append("分组:")
    lines.append(f"  检出水平   : {', '.join(levels) if levels else '（预览未检出，先载入数据）'}")
    if merged.get("GROUP_FROM_ID"):
        lines.append(
            "  ⚠ GROUP_FROM_ID=true（控制台已撤下此项；仍会从 ID 前缀覆盖 Group）"
        )
    else:
        src = str(merged.get("GROUP_SOURCE_COL") or "").strip()
        if not src and len(outcomes) == 1:
            src = outcomes[0]
        if len(outcomes) > 1 and not src:
            src = "各结局列（进入该结局后用该列水平）"
        sub = str(merged.get("SUBGROUP_COL") or "").strip()
        src_col = sub or src or "当前结局列"
        lines.append(
            f"  来源       : {src_col}"
            + ("（亚组列）" if sub else "（子结局列；不用公共 Group）")
        )
    n_pairs = sum(1 for r in plan if r.get("pair"))
    if n_pairs:
        lines.append(f"  两两比较   : {n_pairs} 对（阳/阴按 PAIRWISE_GROUPS 顺序；各对可改）")
        if any(r.get("tabs") for r in plan):
            lines.append("  控制台分页 : ≥3 对 → 下行 公共 | 各对（标签用展示名）")
        level_set = set(levels)
        missing = []
        for r in plan:
            if not r.get("pair"):
                continue
            if r["pos"] not in level_set:
                missing.append(r["pos"])
            if r["neg"] not in level_set:
                missing.append(r["neg"])
        missing = _uniq_keep_str(missing)
        if levels and missing:
            lines.append(
                "  ⚠ 对子列值不在 Group 检出水平里: "
                + "、".join(missing)
                + "。GROUP_FROM_ID 已关，按这些字符串筛行会 n=0。"
                + " 请把 PAIRWISE_GROUPS 改成检出全称，展示名可另写 CCC / ECC。"
            )
    else:
        keep = [str(x).strip() for x in (merged.get("GROUP_KEEP") or []) if str(x).strip()]
        if keep:
            lines.append(f"  行筛选     : GROUP_KEEP={keep}")
        else:
            lines.append("  行筛选     : 不筛（全部组）")

    icc = preview.get("icc") or {}
    if icc:
        lines.append(
            f"ICC         : n={icc.get('n')}  ≥{icc.get('threshold')} both="
            f"{icc.get('both_ge_thr', '—')}"
        )
    lines.append("")
    lines.append("DO_* 生效值:")
    for k in sorted(merged):
        if k.startswith("DO_") or k in ("ENABLE_CORR_FILTER", "ICC_THRESHOLD"):
            mark = " ◆" if k in overlay and overlay[k] != settings.get(k) else ""
            lines.append(f"  {k}={merged[k]}{mark}")

    lines += ["", "将写入:"]
    if merged.get("DO_QC_REPORT", True) and str(merged.get("QC_SHEET") or "").strip():
        lines.append("  ref/qc.html   ← 工程 ref/")
    from modules.utils.u_qc import _external_specs_from_settings, qc_html_rel, external_qc_html_name
    for spec in _external_specs_from_settings(merged):
        lines.append(
            f"  {qc_html_rel(external_qc_html_name(spec['name']))}  ← 工程 ref/ 外验 QC"
            f"  [{spec['name']}] {spec['file']}"
        )
    lines.append("  ref/pipeline/pipeline_run.log   ← 工程 ref/pipeline/ 运行日志")
    if merged.get("DO_FALSE_CLASS", True):
        lines.append("  false_classification.xlsx   ← 工程根（不随 OUT_DIR）")
    if not plan:
        lines.append("  （无结局、无对子）")
    else:
        cur_out = None
        for r in plan:
            if r["outcome"] != cur_out:
                cur_out = r["outcome"]
                lines.append(f"  结局 {cur_out}")
            if r["pair"]:
                nbit = f"  {r['n']}" if r["n"] else ""
                lines.append(
                    f"    {r['pos']} vs {r['neg']}  →  {r['dest']}/{r['html'].rsplit('/', 1)[-1]}"
                    f"  展示 {r['disp_pos']} / {r['disp_neg']}{nbit}"
                )
            else:
                lines.append(f"    {r['html']}")
    lines.append("═══ DRY-RUN 结束 ═══")
    return "\n".join(lines)
