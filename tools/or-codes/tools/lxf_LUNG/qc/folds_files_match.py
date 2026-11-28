"""
folds_files_match.py

以 ROI 为基准，按姓名对齐 IMG，校验检查号 1/2 的完整性。

支持两种数据集 (dataset):
  - jinshan   : img/jinshan (nii) + roi/jinshan (nii)   [默认]
  - xianainew : img/xianainew (dcm) + roi/xianainew (nii)

匹配规则:
  - ROI 文件名形如 {姓名}1.nii.gz / {姓名}2.nii.gz，检查号 1/2 从文件名提取
  - IMG 侧按姓名对齐 ROI，检查号 1/2 判定:
      jinshan   : 递归查找患者目录下 1.nii.gz / 2.nii.gz (含 Syn 子目录);
                  主目录缺失时查补充目录 img/jinshan/补充/
      xianainew : 从 dcm 路径提取姓名，按检查号 1/2 匹配

用法:
  python folds_files_match.py                    # 默认: process (jinshan)
  python folds_files_match.py process            # 同上
  python folds_files_match.py process jinshan    # 指定数据集
  python folds_files_match.py process xianainew  # 指定数据集
  python folds_files_match.py dryrun             # 只扫描+匹配+校验, 不写文件
  python folds_files_match.py dryrun jinshan
  python folds_files_match.py check <csv>        # 校验已有 CSV
"""

import os
import re
import csv
import sys
import pandas as pd
from datetime import datetime

# ================= CONFIGURATION (Soft Coding) =================
CONFIG = {
    # ---- 默认数据集 ----
    "dataset": "jinshan",   # jinshan | xianainew

    # ---- 各数据集路径 ----
    "datasets": {
        "jinshan": {
            "img_root": os.environ.get("CONFIG", ""),
            "roi_root": os.environ.get("CONFIG_2", ""),
            "supplement_root": os.environ.get("CONFIG_3", "")# 主目录缺失时补充
        },
        "xianainew": {
            "img_root": os.environ.get("CONFIG_4", ""),
            "roi_root": os.environ.get("CONFIG_5", ""),
            "supplement_root": None,
        },
    },

    "output_dir": r"{lab_root}\pre-submit\lxf_LUNG\ref\tmp\folds_match",

    # ---- 扫描规则 ----
    "exam_numbers": ["1", "2"],          # 检查号
    "nii_extension": ".nii.gz",
    "dcm_extension": ".dcm",
    "skip_prefix": "._",                 # 过滤缓存文件前缀

    # ---- CSV 列名 ----
    "columns": {
        "roi": "roi_path",
        "img": "img_path",
        "name": "name",
        "exam": "exam_num",
    },

    # ---- 输出文件名 ----
    "outputs": {
        "combined": "combind.csv",
        "unmatched": "unmatched.csv",
        "report": "process_report.txt",
    },
}
# ===============================================================


# ----------------------------------------------------------------------
# 公共工具函数
# ----------------------------------------------------------------------
def normalize_name(name):
    """标准化姓名用于匹配：仅保留字母/中文，转小写。"""
    if not name:
        return ""
    return re.sub(r'[^a-zA-Z\u4e00-\u9fa5]', '', name).lower()


def get_ds():
    """返回当前数据集的配置字典。"""
    return CONFIG["datasets"][CONFIG["dataset"]]


def extract_name_from_roi_path(path):
    """
    从 roi 路径提取姓名。
    roi/jinshan/{姓名}/{姓名}{1|2}.nii.gz
    roi/xianainew/{姓名}/{姓名}{1|2}.nii.gz
    """
    if not isinstance(path, str):
        return None
    path = os.path.normpath(path)
    parts = path.split(os.sep)
    if not parts:
        return None
    # 文件名形如 {姓名}1.nii.gz / {姓名}2.nii.gz
    fname = parts[-1]
    m = re.match(r'^(.+?)[12]\.nii\.gz$', fname, re.IGNORECASE)
    if m:
        return m.group(1)
    # 兜底: 取父目录名
    if len(parts) >= 2:
        return parts[-2]
    return None


def extract_exam_from_roi_path(path):
    """
    从 roi 路径提取检查号 (1 或 2)。
    文件名形如 {姓名}1.nii.gz / {姓名}2.nii.gz
    """
    if not isinstance(path, str):
        return None
    path = os.path.normpath(path)
    fname = os.path.basename(path)
    m = re.match(r'^.+?([12])\.nii\.gz$', fname, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def extract_name_dcm(path):
    """
    从 dcm 路径提取姓名 (xianainew 模式)。
    img/xianainew/{检查号}/{DownLoadimg_..._姓名}/1/...dcm
    """
    if not isinstance(path, str):
        return None
    path = os.path.normpath(path)
    parts = path.split(os.sep)
    if not parts:
        return None

    # 找到含 DownLoadimg 的文件夹
    folder_name = None
    for p in reversed(parts):
        if "downloadimg" in p.lower():
            folder_name = p
            break
    if folder_name is None:
        # 兜底: 取最后一个非检查号文件夹
        for p in reversed(parts):
            if p not in CONFIG["exam_numbers"]:
                folder_name = p
                break

    if not folder_name:
        return None

    # Style 1 (linai): Name at end after last underscore
    if "_" in folder_name:
        name_raw = folder_name[folder_name.rfind("_") + 1:]
        name_upper = re.sub(r"[^A-Z\s]", "", name_raw).strip()
        if name_upper:
            return name_upper

    # Style 2 (one): Name at beginning, followed by digits/date
    m_one = re.match(r"^([A-Z\s]+)\d+", folder_name)
    if m_one:
        name = m_one.group(1).strip()
        if name:
            return name

    # Fallback: Just take upper chars
    name_upper = re.sub(r"[^A-Z\s]", "", folder_name).strip()
    return name_upper or None


def extract_exam_from_dcm_path(path):
    """
    从 dcm 路径提取检查号 (xianainew 模式)。
    img/xianainew/{检查号}/{DownLoadimg_..._姓名}/1/...dcm
    检查号 = 路径中的 1/2 子目录 (优先)，否则取 img_root 后的第一层
    """
    if not isinstance(path, str):
        return None
    path = os.path.normpath(path)
    parts = path.split(os.sep)
    if not parts:
        return None
    # 优先: 路径中任意一段为 1/2 (检查号子目录)
    for p in parts:
        if p in CONFIG["exam_numbers"]:
            return p
    # 否则取 img_root 后的第一层 (检查号)
    ds = get_ds()
    img_root_norm = os.path.normpath(ds["img_root"])
    try:
        rel = os.path.relpath(path, img_root_norm)
        rel_parts = rel.split(os.sep)
        if rel_parts:
            return rel_parts[0]
    except Exception:
        pass
    return None


# ----------------------------------------------------------------------
# 扫描函数
# ----------------------------------------------------------------------
def scan_roi():
    """扫描 roi 目录，返回 [{roi_path, name, exam}]。"""
    ds = get_ds()
    records = []
    for root, dirs, files in os.walk(ds["roi_root"]):
        for f in files:
            if f.lower().endswith(CONFIG["nii_extension"]) and not f.startswith(CONFIG["skip_prefix"]):
                full = os.path.normpath(os.path.join(root, f))
                name = extract_name_from_roi_path(full)
                exam = extract_exam_from_roi_path(full)
                if name and exam:
                    records.append({"roi_path": full, "name": name, "exam": exam})
    return records


def scan_img_jinshan():
    """
    扫描 img/jinshan 目录，返回 {name: {exam: img_path}}。
    递归查找患者目录下 1.nii.gz / 2.nii.gz (含 Syn 子目录)。
    """
    ds = get_ds()
    result = {}  # name -> {exam: path}
    for root, dirs, files in os.walk(ds["img_root"]):
        # 跳过补充目录 (单独处理)
        if ds["supplement_root"] and os.path.normpath(root).startswith(os.path.normpath(ds["supplement_root"])):
            continue
        for f in files:
            if f.lower().endswith(CONFIG["nii_extension"]) and not f.startswith(CONFIG["skip_prefix"]):
                m = re.match(r'^([12])\.nii\.gz$', f, re.IGNORECASE)
                if m:
                    exam = m.group(1)
                    full = os.path.normpath(os.path.join(root, f))
                    # 患者名 = img_root 后的第一层目录
                    rel = os.path.relpath(full, ds["img_root"])
                    name = normalize_name(rel.split(os.sep)[0])
                    result.setdefault(name, {})[exam] = full
    return result


def scan_img_jinshan_supplement():
    """
    扫描 img/jinshan/补充 目录，返回 {name: {exam: img_path}}。
    补充目录文件形如 {姓名}_{序号}_{协议}.nii.gz，检查号从文件名中的 1/2 判定。
    """
    ds = get_ds()
    if not ds["supplement_root"] or not os.path.exists(ds["supplement_root"]):
        return {}
    result = {}
    for root, dirs, files in os.walk(ds["supplement_root"]):
        for f in files:
            if f.lower().endswith(CONFIG["nii_extension"]) and not f.startswith(CONFIG["skip_prefix"]):
                full = os.path.normpath(os.path.join(root, f))
                # 文件名形如 {姓名}_{序号}_{协议}.nii.gz
                # 姓名部分可能用下划线 (MAO_XIU_YING) 或空格 (LI QING HUA) 分隔
                base = os.path.splitext(os.path.splitext(f)[0])[0]
                # 提取姓名 (大写字母/下划线/空格部分)
                m_name = re.match(r'^([A-Z][A-Z\s_]*?)_\d+', base)
                if not m_name:
                    continue
                name = normalize_name(m_name.group(1))
                # 提取检查号: 序号 1/2
                m_exam = re.match(r'^[A-Z][A-Z\s_]*?_([12])_', base)
                if not m_exam:
                    continue
                exam = m_exam.group(1)
                result.setdefault(name, {})[exam] = full
    return result


def scan_img_xianainew():
    """
    扫描 img/xianainew 目录 (dcm)，返回 {name: {exam: img_path}}。
    检查号 1/2 体现在 DownLoadimg 文件夹下的 1/、2/ 子目录。
    收集含 dcm 的 1/、2/ 子目录作为 img 记录。
    """
    ds = get_ds()
    result = {}
    for root, dirs, files in os.walk(ds["img_root"]):
        # 只收集名为 1/2 且含 dcm 的文件夹 (检查号子目录)
        if os.path.basename(root) in CONFIG["exam_numbers"]:
            if any(f.lower().endswith(CONFIG["dcm_extension"]) for f in files):
                full = os.path.normpath(root)
                name = extract_name_dcm(full)
                exam = os.path.basename(root)
                if name and exam:
                    result.setdefault(normalize_name(name), {})[exam] = full
    return result


# ----------------------------------------------------------------------
# 匹配逻辑 (以 roi 为基准)
# ----------------------------------------------------------------------
def build_matches(roi_records, img_map):
    """
    以 roi 为基准，按姓名+检查号匹配 img。
    返回 (matched, unmatched_roi, unmatched_img)
    """
    matched = []
    unmatched_roi = []
    used_img = set()  # (name, exam) 已匹配的 img

    for rec in roi_records:
        name_key = normalize_name(rec["name"])
        exam = rec["exam"]
        img_path = img_map.get(name_key, {}).get(exam)
        if img_path:
            matched.append({
                "roi_path": rec["roi_path"],
                "img_path": img_path,
                "name": rec["name"],
                "exam": exam,
            })
            used_img.add((name_key, exam))
        else:
            unmatched_roi.append(rec)

    # 未匹配的 img (roi 中不存在的患者/检查号)
    unmatched_img = []
    for name_key, exams in img_map.items():
        for exam, path in exams.items():
            if (name_key, exam) not in used_img:
                unmatched_img.append({"name": name_key, "exam": exam, "img_path": path})

    return matched, unmatched_roi, unmatched_img


# ----------------------------------------------------------------------
# 校验逻辑
# ----------------------------------------------------------------------
def run_checks(matched, unmatched_roi):
    """
    对匹配结果执行一致性校验。
    """
    errors = []
    patients = {}  # name_key -> {"exams": set, "name": str}

    for rec in matched:
        name_key = normalize_name(rec["name"])
        exam = rec["exam"]
        if name_key not in patients:
            patients[name_key] = {"exams": set(), "name": rec["name"]}
        patients[name_key]["exams"].add(exam)

    # 1. 检查号完整性: 每个患者应有检查号 1 和 2
    required = set(CONFIG["exam_numbers"])
    for key, info in patients.items():
        missing = required - info["exams"]
        if missing:
            errors.append(
                f"Patient '{info['name']}' missing exams: {sorted(missing)}"
            )

    # 2. roi 有但 img 缺失的检查号
    for rec in unmatched_roi:
        errors.append(
            f"ROI '{rec['name']}' exam {rec['exam']} has no matching IMG: {rec['roi_path']}"
        )

    return errors


# ----------------------------------------------------------------------
# 入口: process 模式
# ----------------------------------------------------------------------
def cmd_process(dry_run=False):
    start_time = datetime.now()
    ds = get_ds()
    mode_label = "DRY RUN" if dry_run else "PROCESS"
    print(f"[{mode_label}] Dataset: {CONFIG['dataset']}")
    print(f"[{mode_label}] IMG={ds['img_root']}, ROI={ds['roi_root']}")

    # 1. 扫描 roi (基准)
    print("Scanning ROI...")
    roi_records = scan_roi()
    if not roi_records:
        print("Error: No ROI files found.")
        return
    print(f"  Found {len(roi_records)} ROI records")

    # 2. 扫描 img
    print("Scanning IMG...")
    if CONFIG["dataset"] == "jinshan":
        img_map = scan_img_jinshan()
        # 主目录缺失时查补充目录
        supplement = scan_img_jinshan_supplement()
        for name, exams in supplement.items():
            for exam, path in exams.items():
                if name not in img_map or exam not in img_map[name]:
                    img_map.setdefault(name, {})[exam] = path
    else:
        img_map = scan_img_xianainew()
    print(f"  Found {len(img_map)} IMG patients")

    # 3. 匹配
    print("Matching...")
    matched, unmatched_roi, unmatched_img = build_matches(roi_records, img_map)
    print(f"  Matched: {len(matched)}")
    print(f"  Unmatched ROI (no IMG): {len(unmatched_roi)}")
    print(f"  Unmatched IMG (no ROI): {len(unmatched_img)}")

    # 4. 校验
    print("Running data checks...")
    errors = run_checks(matched, unmatched_roi)

    # 5. 输出
    if dry_run:
        # dry run: 不写文件, 仅控制台打印
        print(f"\n{'='*50}")
        print(f"DRY RUN RESULT")
        print(f"{'='*50}")
        print(f"Matched: {len(matched)}")
        print(f"Unmatched ROI (no IMG): {len(unmatched_roi)}")
        print(f"Unmatched IMG (no ROI): {len(unmatched_img)}")
        print(f"Issues Found: {len(errors)}")
        print(f"{'='*50}\n")
        if errors:
            print("Detail Issues:")
            for err in errors:
                print(f"- {err}")
        else:
            print("All checks passed!")
        if unmatched_img:
            print("\nUnmatched IMG (no ROI):")
            for rec in unmatched_img:
                print(f"- {rec['name']} exam {rec['exam']}: {rec['img_path']}")
        duration = (datetime.now() - start_time).total_seconds()
        print(f"\nDone! Duration: {duration:.2f}s")
        return

    # 正常模式: 写文件
    os.makedirs(CONFIG["output_dir"], exist_ok=True)
    comb_path = os.path.normpath(os.path.join(CONFIG["output_dir"], CONFIG["outputs"]["combined"]))
    unmatch_path = os.path.normpath(os.path.join(CONFIG["output_dir"], CONFIG["outputs"]["unmatched"]))
    report_path = os.path.normpath(os.path.join(CONFIG["output_dir"], CONFIG["outputs"]["report"]))

    col = CONFIG["columns"]
    df_matched = pd.DataFrame(matched)
    df_matched.to_csv(comb_path, index=False, encoding="utf-8-sig")

    # unmatched: roi 缺失 + img 缺失
    unmatched_rows = []
    for rec in unmatched_roi:
        unmatched_rows.append({"source": "roi", "name": rec["name"], "exam": rec["exam"], "path": rec["roi_path"]})
    for rec in unmatched_img:
        unmatched_rows.append({"source": "img", "name": rec["name"], "exam": rec["exam"], "path": rec["img_path"]})
    pd.DataFrame(unmatched_rows).to_csv(unmatch_path, index=False, encoding="utf-8-sig")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Dataset: {CONFIG['dataset']}\n")
        f.write(f"{'='*50}\n")
        f.write(f"Matched: {len(matched)}\n")
        f.write(f"Unmatched ROI (no IMG): {len(unmatched_roi)}\n")
        f.write(f"Unmatched IMG (no ROI): {len(unmatched_img)}\n")
        f.write(f"Issues Found: {len(errors)}\n")
        f.write(f"{'='*50}\n\n")
        if errors:
            f.write("Detail Issues:\n")
            for err in errors:
                f.write(f"- {err}\n")
        else:
            f.write("All checks passed!\n")

    duration = (datetime.now() - start_time).total_seconds()
    print(f"\nDone! Duration: {duration:.2f}s")
    print(f"- Combined Result: {comb_path}")
    print(f"- Unmatched Items: {unmatch_path}")
    print(f"- Report: {report_path}")
    if errors:
        print(f"Warning: Found {len(errors)} issues, see report.")
    else:
        print("Success: All checks passed.")


# ----------------------------------------------------------------------
# 入口: check 模式
# ----------------------------------------------------------------------
def cmd_check(csv_path=None):
    if not csv_path:
        print("Error: check 模式需显式指定 CSV：python folds_files_match.py check <csv>")
        return
    if not os.path.exists(csv_path):
        print(f"Error: File {csv_path} does not exist.")
        return

    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        records = list(csv.DictReader(f))

    # check 模式: 从 CSV 重建 matched 结构
    col = CONFIG["columns"]
    matched = []
    for rec in records:
        roi_path = (rec.get(col["roi"]) or "").strip()
        img_path = (rec.get(col["img"]) or "").strip()
        name = (rec.get(col["name"]) or "").strip()
        exam = (rec.get(col["exam"]) or "").strip()
        if roi_path and img_path:
            matched.append({"roi_path": roi_path, "img_path": img_path, "name": name, "exam": exam})

    errors = run_checks(matched, [])

    if not errors:
        print("Success: All checks passed!")
    else:
        print(f"Found {len(errors)} issues:\n")
        for err in errors:
            print(f"- {err}")


# ----------------------------------------------------------------------
# CLI Distribution
# ----------------------------------------------------------------------
def main():
    args = sys.argv[1:]
    mode = args[0].lower() if args else "process"

    if mode == "check":
        path = args[1] if len(args) > 1 else None
        cmd_check(path)
    elif mode == "dryrun":
        if len(args) > 1:
            CONFIG["dataset"] = args[1].lower()
        print(f"Config: DATASET={CONFIG['dataset']}")
        cmd_process(dry_run=True)
    elif mode == "process":
        if len(args) > 1:
            CONFIG["dataset"] = args[1].lower()
        print(f"Config: DATASET={CONFIG['dataset']}")
        cmd_process(dry_run=False)
    else:
        print(f"Unknown mode: {mode} (Available: process | dryrun | check)")


if __name__ == "__main__":
    main()