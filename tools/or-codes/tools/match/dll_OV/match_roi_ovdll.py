# -*- coding: utf-8 -*-
"""
针对 dll_OV 数据结构（L:\\OC\\OV_dll\\case）的确定性 ROI/模态匹配脚本。

数据结构约定（已探查确认）：
  根目录/{子集}/{病例}/
    - ROI     : 病例目录下唯一一个非 adc 的 .nii.gz（如 N01.nii.gz；ECC 偶见 nn03/nn04/nn06 变体）
    - ADC     : 优先 ADC.nii.gz（单文件）；否则取 ADC/ 子目录内的 DICOM 序列（二选一互斥）
    - T2WI    : T2WI/ 子目录内的 DICOM 序列
    - CE      : CE/  子目录内的 DICOM 序列
  缺失的模态在 CSV 对应列留空，病例照常输出。

功能：
  - dry_run : 只打印匹配预览，不写任何 CSV/报告/checkpoint 文件
  - 断点续跑 : 按病例粒度，每处理完一个病例立即增量写入 checkpoint；正常跑完删除 checkpoint

用法:
  python match_roi_ovdll.py
"""

import os
import csv
import json
from datetime import datetime

# ---- 配置来自工程 settings.ini（--ini / --project / 向上查找）----
import os as _os
import sys as _sys

_CORE = _os.environ.get("HABITAT_CORE_ROOT") or r"{or_codes}"
if _CORE not in _sys.path:
    _sys.path.insert(0, _CORE)

from habitat_core import tool_ini as _tk  # noqa: E402


# ============================== CONFIG（软编码，参数集中） ==============================
CONFIG = _tk.get("MATCH_CONFIG")
# =========================================================================================


def _log(msg):
    if CONFIG.get("verbose", True):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def _has_ext(name, exts):
    lname = name.lower()
    # 复合扩展名优先（如 .nii.gz 要优先于 .nii）
    sorted_exts = sorted(exts, key=len, reverse=True)
    return any(lname.endswith(e.lower()) for e in sorted_exts)


def _nii_stem_lower(path):
    """去掉 nii 相关扩展名（含复合 .nii.gz）后的小写文件名。
    注意：不能用 os.path.splitext，它对 .nii.gz 会误判为 (.nii, .gz)。"""
    name = os.path.basename(path).lower()
    for e in sorted(CONFIG["nii_extensions"], key=len, reverse=True):
        if name.endswith(e.lower()):
            return name[: -len(e)]
    return name


def collect_modality_dir(dirpath, exts):
    """收集指定目录（多是模态子目录）内匹配扩展名的最深层文件/子文件列表。"""
    found = []
    if not os.path.isdir(dirpath):
        return found
    for dirpath2, _dirs, files in os.walk(dirpath):
        for f in sorted(files):
            if f.startswith("._"):
                continue
            if _has_ext(f, exts):
                found.append(os.path.normpath(os.path.join(dirpath2, f)))
    return found


def find_roi(case_dir, roi_root=None):
    """ROI = 病例目录下（仅直接层）唯一一个非 adc 的 .nii* 文件。
    用排除法而非依赖与文件夹同名，以兼容 nn03/nn04/nn06 等变体。
    拆树后（roi\\<子集>\\<病例>\\）病例目录里已无掩膜，此时按同一相对路径到 roi_root 找。"""
    candidates = []
    if os.path.isdir(case_dir):
        for f in sorted(os.listdir(case_dir)):
            if f.startswith("._"):
                continue
            full = os.path.join(case_dir, f)
            if os.path.isfile(full) and _has_ext(f, CONFIG["nii_extensions"]):
                # 排除 ADC 单文件（不区分大小写，需去除 .nii.gz 复合扩展名）
                if _nii_stem_lower(f) == CONFIG["adc_single_name"]:
                    continue
                candidates.append(os.path.normpath(full))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates and roi_root:
        rel = os.path.relpath(case_dir, CONFIG["root_dir"])
        alt_dir = os.path.join(roi_root, rel)
        alt = []
        if os.path.isdir(alt_dir):
            for f in sorted(os.listdir(alt_dir)):
                if f.startswith("._"):
                    continue
                full = os.path.join(alt_dir, f)
                if os.path.isfile(full) and _has_ext(f, CONFIG["nii_extensions"]):
                    if _nii_stem_lower(f) == CONFIG["adc_single_name"]:
                        continue
                    alt.append(os.path.normpath(full))
        if len(alt) == 1:
            return alt[0]
    return None  # 0 个或多个 -> 无法确定，交由报告标记


def _find_modality_dir_name(case_dir, modal_alias):
    """在病例目录内查找模态子目录。
    规则（目录名小写）：恰好等于模态关键词，或以 '-keyword' / '_keyword' 结尾。
    兼容 T2WI、N19-T2WI、N22-ADC、N26-CE 等命名；不含关键词的目录（如 dwi、12）自动排除。"""
    if not os.path.isdir(case_dir):
        return None
    for d in os.listdir(case_dir):
        dl = d.lower()
        if dl == modal_alias or dl.endswith("-" + modal_alias) or dl.endswith("_" + modal_alias):
            dp = os.path.join(case_dir, d)
            if os.path.isdir(dp):
                return dp
    return None


def find_adc(case_dir):
    """ADC：优先 {case}/ADC.nii.gz 单文件；否则取 ADC 子目录（含 N22-ADC 变体）内 DICOM。"""
    # 1) 单文件 ADC.nii*（需去除 .nii.gz 复合扩展名再比较）
    if os.path.isdir(case_dir):
        for f in os.listdir(case_dir):
            if f.startswith("._"):
                continue
            if os.path.isfile(os.path.join(case_dir, f)) and \
               _nii_stem_lower(f) == CONFIG["adc_single_name"] and \
               _has_ext(f, CONFIG["nii_extensions"]):
                return os.path.normpath(os.path.join(case_dir, f)), "nii"
    # 2) ADC 子目录 DICOM（支持 N22-ADC 等命名）
    dp = _find_modality_dir_name(case_dir, "adc")
    if dp:
        dcm = collect_modality_dir(dp, CONFIG["dcm_extensions"])
        if dcm:
            return [os.path.normpath(x) for x in dcm], "dcm"
    return None, None


def find_modality_dir(case_dir, modality_alias, exts):
    """通用查找模态子目录（如 t2wi / ce，含 N19-T2WI 变体）内 DICOM。"""
    dp = _find_modality_dir_name(case_dir, modality_alias)
    if dp:
        found = collect_modality_dir(dp, exts)
        if found:
            return [os.path.normpath(x) for x in found]
    return None


def fmt_path(p):
    """列表转可读文本；None -> 空。"""
    if not p:
        return ""
    if isinstance(p, list):
        return ";".join(p)
    return p


def _img_dir_or_file(paths, is_nii):
    """长表 img_path：dcm 序列 -> 所在文件夹；nii 单文件 -> 完整路径。paths 为列表或 None。"""
    if not paths:
        return ""
    if is_nii:
        return paths
    return os.path.dirname(paths[0])


# ============================== 断点续跑 ==============================
def _checkpoint_path():
    return os.path.normpath(os.path.join(CONFIG["output_dir"], CONFIG["checkpoint_file"]))


def _load_checkpoint():
    """加载已完成病例集合；无 checkpoint 或 resume 关闭时返回空集。"""
    if not CONFIG.get("resume", True):
        return set()
    cp = _checkpoint_path()
    if os.path.isfile(cp):
        try:
            with open(cp, "r", encoding="utf-8") as f:
                data = json.load(f)
            done = set(data.get("completed", []))
            _log(f"断点续跑：加载 {len(done)} 个已完成病例 -> {cp}")
            return done
        except (json.JSONDecodeError, OSError) as e:
            _log(f"警告：checkpoint 读取失败，从头开始 -> {e}")
    return set()


def _save_checkpoint(completed):
    """增量保存已完成病例集合（每处理完一个病例调用一次）。"""
    cp = _checkpoint_path()
    tmp = cp + ".tmp"
    data = {"completed": sorted(completed), "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cp)  # 原子替换，避免中断写坏文件


def _remove_checkpoint():
    """正常跑完后删除 checkpoint，避免下次误判。"""
    cp = _checkpoint_path()
    if os.path.isfile(cp):
        os.remove(cp)
        _log(f"已删除 checkpoint（本次完整跑完）-> {cp}")


# ============================== dry run 预览 ==============================
def _print_preview(subset, case, roi, adc, adc_type, t2wi, ce):
    """打印单个病例的匹配预览。"""
    def mark(x):
        return "OK " if x else "MISS"
    print(f"  [{subset}/{case}] ROI={mark(roi)} ADC={mark(adc)}({adc_type or '-'}) "
          f"T2WI={mark(t2wi)} CE={mark(ce)}")
    if roi:
        print(f"      ROI : {roi}")
    if adc:
        print(f"      ADC : {fmt_path(adc)}")
    if t2wi:
        print(f"      T2WI: {fmt_path(t2wi)}")
    if ce:
        print(f"      CE  : {fmt_path(ce)}")


def _parse_cli():
    """只解析 --dry-run / --output-dir；其它参数忽略（--project/--ini 已由 tool_ini 摘走）。"""
    import argparse as _ap
    _p = _ap.ArgumentParser(add_help=False)
    _p.add_argument("--dry-run", action="store_true")
    _p.add_argument("--output-dir", default=None)
    _ns, _unknown = _p.parse_known_args()
    return bool(_ns.dry_run), _ns.output_dir


def main():
    dry_run, _cli_out = _parse_cli()
    if _cli_out:
        CONFIG["output_dir"] = _cli_out
    start = datetime.now()
    if not dry_run:
        os.makedirs(CONFIG["output_dir"], exist_ok=True)
    root = CONFIG["root_dir"]
    dry_run = CONFIG.get("dry_run", False)

    rows = []
    long_rows = []  # 长表行：subset, case, img_path, roi_path
    report = []
    counters = {
        "case_total": 0, "has_roi": 0, "has_adc": 0,
        "has_t2wi": 0, "has_ce": 0, "adc_nii": 0, "adc_dcm": 0,
    }
    problems = []  # (case_dir, 说明)

    # 断点续跑：加载已完成病例
    completed = _load_checkpoint() if not dry_run else set()

    for subset in CONFIG["subsets"]:
        subset_dir = os.path.join(root, subset)
        if not os.path.isdir(subset_dir):
            report.append(f"[{subset}] 子集目录不存在：{subset_dir}")
            continue

        cases = sorted(d for d in os.listdir(subset_dir)
                       if os.path.isdir(os.path.join(subset_dir, d)))
        for case in cases:
            case_dir = os.path.join(subset_dir, case)
            case_key = f"{subset}/{case}"

            # 断点续跑：跳过已完成病例
            if case_key in completed:
                continue

            counters["case_total"] += 1

            roi = find_roi(case_dir, CONFIG.get("roi_root"))
            adc, adc_type = find_adc(case_dir)
            t2wi = find_modality_dir(case_dir, "t2wi", CONFIG["dcm_extensions"])
            ce = find_modality_dir(case_dir, "ce", CONFIG["dcm_extensions"])

            # 统计与缺失清单（dry_run 与正式运行都累加）
            if roi:
                counters["has_roi"] += 1
            else:
                problems.append((case_dir, "未找到唯一 ROI"))
            if adc:
                counters["has_adc"] += 1
                counters["adc_nii" if adc_type == "nii" else "adc_dcm"] += 1
            else:
                problems.append((case_dir, "未找到 ADC"))
            if t2wi:
                counters["has_t2wi"] += 1
            else:
                problems.append((case_dir, "未找到 T2WI"))
            if ce:
                counters["has_ce"] += 1
            else:
                problems.append((case_dir, "未找到 CE"))

            if dry_run:
                _print_preview(subset, case, roi, adc, adc_type, t2wi, ce)
            else:
                rows.append({
                    "subset": subset,
                    "case": case,
                    "roi_path": fmt_path(roi) if roi else "",
                    "adc_path": fmt_path(adc) if adc else "",
                    "adc_type": adc_type or "",
                    "t2wi_path": fmt_path(t2wi) if t2wi else "",
                    "ce_path": fmt_path(ce) if ce else "",
                })

                # ---- 长表行：ADC/T2WI/CE 各一行；dcm 取文件夹、nii 取完整路径；缺失留空 ----
                roi_str = fmt_path(roi) if roi else ""
                long_rows.append({"subset": subset, "case": case,
                                  "img_path": _img_dir_or_file(adc, adc_type == "nii"), "roi_path": roi_str})
                long_rows.append({"subset": subset, "case": case,
                                  "img_path": _img_dir_or_file(t2wi, False), "roi_path": roi_str})
                long_rows.append({"subset": subset, "case": case,
                                  "img_path": _img_dir_or_file(ce, False), "roi_path": roi_str})

                # 断点续跑：每处理完一个病例立即增量保存
                completed.add(case_key)
                _save_checkpoint(completed)

    # ---- dry run：只打印统计与缺失清单，不写任何文件 ----
    if dry_run:
        _log(f"=== dry run 预览完成（未写任何文件）===")
        _log(f"病例总数 : {counters['case_total']}")
        _log(f"有 ROI   : {counters['has_roi']}")
        _log(f"有 ADC   : {counters['has_adc']}  (nii: {counters['adc_nii']}, dcm: {counters['adc_dcm']})")
        _log(f"有 T2WI  : {counters['has_t2wi']}")
        _log(f"有 CE    : {counters['has_ce']}")
        _log(f"缺失/异常项 : {len(problems)} 条")
        for case_dir, reason in problems:
            _log(f"  - {case_dir}  ->  {reason}")
        return

    # ---- 写宽表 CSV（utf-8-sig 便于 Excel 打开；若被占用则跳过，不影响长表） ----
    csv_path = os.path.normpath(os.path.join(CONFIG["output_dir"], CONFIG["outputs"]["csv"]))
    try:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["subset", "case", "roi_path", "adc_path", "adc_type", "t2wi_path", "ce_path"])
            writer.writeheader()
            writer.writerows(rows)
    except PermissionError:
        _log(f"警告：宽表 CSV 被占用，跳过写入 -> {csv_path}")

    # ---- 写长表 combined.csv（4 列：subset, case, img_path, roi_path） ----
    combined_path = os.path.normpath(os.path.join(CONFIG["output_dir"], CONFIG["outputs"]["combined"]))
    with open(combined_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["subset", "case", "img_path", "roi_path"])
        writer.writeheader()
        writer.writerows(long_rows)

    # ---- 写报告 ----
    duration = (datetime.now() - start).total_seconds()
    report_path = os.path.normpath(os.path.join(CONFIG["output_dir"], CONFIG["outputs"]["report"]))
    if dry_run:
        print("[dry-run] 不写盘；将写入：")
        print("  " + comb_path)
        print("  " + unmatch_path)
        print("  " + report_path)
        return
    lines = []
    lines.append(f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Duration: {duration:.2f}s")
    lines.append(f"Root: {root}")
    lines.append("")
    lines.append("=== 统计 ===")
    lines.append(f"病例总数     : {counters['case_total']}")
    lines.append(f"有 ROI       : {counters['has_roi']}")
    lines.append(f"有 ADC       : {counters['has_adc']}  (nii: {counters['adc_nii']}, dcm: {counters['adc_dcm']})")
    lines.append(f"有 T2WI      : {counters['has_t2wi']}")
    lines.append(f"有 CE        : {counters['has_ce']}")
    lines.append("")
    lines.append("=== 缺失/异常清单（需人工确认）===")
    if problems:
        for case_dir, reason in problems:
            lines.append(f"  - {case_dir}  ->  {reason}")
    else:
        lines.append("  无")
    lines.append("")
    lines.append(f"宽表 CSV 输出: {csv_path}")
    lines.append(f"长表 CSV 输出: {combined_path}")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # ---- 正常跑完，删除 checkpoint ----
    _remove_checkpoint()

    _log(f"完成！耗时 {duration:.2f}s")
    _log(f"- 宽表 CSV : {csv_path}  ({len(rows)} 行)")
    _log(f"- 长表 CSV : {combined_path}  ({len(long_rows)} 行)")
    _log(f"- 报告     : {report_path}")
    _log(f"- 统计   : {counters}")
    if problems:
        _log(f"- 异常项 : {len(problems)} 条（见报告）")


if __name__ == "__main__":
    main()