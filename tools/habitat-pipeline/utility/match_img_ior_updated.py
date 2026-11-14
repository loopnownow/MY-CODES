# -*- coding: utf-8 -*-
"""
match_img_roi.py (v2)

在 v1 基础上，根据首次真实批量运行（6168 匹配 / 8939 未匹配）暴露出的问题修复：

  1. 模态关键词改为"前缀匹配"而非"整词匹配"
       原因：T1FS / T2FS / DWI0 / DWI1 / C1 这类文件名，关键词后面紧跟其他字符，
             整词边界正则匹配不上，导致被判 unclassified，进而拖累整个目录失败。
  2. Z 系列掩膜文件（Z1/Z2/Z3.../Zn）改为正则通用识别，不再逐个硬编码关键词。
  3. 同目录内文件名归一化去重（大小写 + 空格），修复 "Z1.nrrd" 与 "Z1 .nrrd"
     这种拷贝残留/尾随空格重复文件导致整目录判失败的问题。
  4. 跨根目录匹配新增两级兜底：
       a) roi 文件名开头多一个 "z"（如 "1.nii" <-> "Z1.nii"）
       b) 同一相对目录下，剩余文件按文件名相似度做双向最优匹配（fuzzy）
  5. combined.csv 新增 match_method 列，标注每条匹配的置信度来源，
     便于对 fuzzy 等低置信度匹配做人工抽查，而不是全部silently信任。
  6. 统一任务命名去重（原配置中 3 个任务重名为 CAC_CCEC_fyh，导致报告无法区分）。
  7. 未匹配原因细化：目录内唯一且无法判定的孤立文件标为 single_file_no_pair，
     和"目录内多个文件但判不清"的 unclassified 区分开。
  8. (v3) 配对前对称归一化：新增 img_role_keywords（main/image），与 roi 关键词一并
     剥离后再计算相似度，修复"长相似前缀患者名"场景下的贪心错配（如 wang hai / wang hui min）。

用法:
  python match_img_roi.py
"""

import os
import re
import csv
import logging
import sys
from datetime import datetime
from difflib import SequenceMatcher
# 确保能找到同目录下的 workflow_utils.py
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from workflow_utils import setup_logger, CheckpointManager

# ============================== CONFIG（软编码，全部参数集中于此） ==============================
CONFIG = {
    # ---- 待处理任务列表：每项为一组 (img_root, roi_root)，name 必须唯一 ----
    # img_root == roi_root（或 roi_root 缺省）-> 同目录模式；不同 -> 跨根目录模式
    "tasks": [
        {"name": "lung_xlm_baoceng", "img_root": os.environ.get("MATCH_IMG_ROOT_1", "data/baoceng/main"), "roi_root": os.environ.get("MATCH_ROI_ROOT_1", "data/baoceng/seg")},
        {"name": "lung_xlm_thin",    "img_root": os.environ.get("MATCH_IMG_ROOT_2", "data/thin/main"),    "roi_root": os.environ.get("MATCH_ROI_ROOT_2", "data/thin/seg")},
    ],

    "output_dir": os.environ.get("MATCH_OUTPUT_DIR", "results/match_img"),
    "log_dir": os.environ.get("MATCH_LOG_DIR", "logs/match_img"),
    "checkpoint_dir": os.environ.get("MATCH_CHECKPOINT_DIR", "checkpoints/match_img"),

    # ---- 支持的扩展名（复合扩展名放前面，如 .nii.gz，避免被 .nii 提前截断） ----
    "img_extensions": [".nii.gz", ".nii", ".nrrd", ".dcm"],
    "roi_extensions": [".nii.gz", ".nii", ".nrrd", ".dcm"],
    "skip_prefix": "._",  # 过滤 macOS 缓存文件

    # ---- 同目录模式：文件名关键词判定为 roi（在整个 stem 中按词边界搜索） ----
    "roi_keywords": ["roi", "merge", "mask", "label", "seg", "gt"],
    "img_role_keywords": ["main", "image"],  # 配对前与 roi 关键词一并剥离（见 strip_roi_keywords）
    # Z 系列掩膜（Z1/Z2/.../Zn）通过正则通用识别，见 ROI_Z_PATTERN，无需在此列举

    # ---- 同目录模式：已知影像模态关键词，按"前缀匹配"（如 t1fs.startswith("t1")） ----
    "img_modality_keywords": [
        "adc", "dwi", "t1", "t1c", "t2", "flair", "dce",
        "swi", "asl", "pwi", "cor", "sag", "tra", "c", "image",
    ],

    # ---- 跨根目录 fuzzy 兜底：相似度阈值（0~1），低于此值不予匹配 ----
    "fuzzy_threshold": 0.72,

    # ---- 跨根目录显式编码映射（数据集特定命名习惯，无法靠相似度猜出来的场景）----
    # 全局默认空；如某个任务的 img/roi 命名遵循固定字母<->数字对照表，
    # 在该 task 字典里加 "explicit_code_map" 覆盖此默认值即可，只影响该任务。
    "explicit_code_map": {},

    "outputs": {
        "combined": "combined.csv",
        "unmatched": "unmatched.csv",
        "report": "process_report.txt",
    },
    "verbose": True,
}
# ================================================================================================

ROI_Z_PATTERN = re.compile(r"^z\d+$")


def _ts():
    return datetime.now().strftime("%H:%M:%S")


logger = logging.getLogger("match_img")

def _log(msg, level=logging.INFO):
    if level == logging.INFO:
        logger.info(msg)
    elif level == logging.ERROR:
        logger.error(msg)
    elif level == logging.WARNING:
        logger.warning(msg)


# ----------------------------------------------------------------------
# 扩展名 / 文件名工具
# ----------------------------------------------------------------------
def has_ext(filename, exts):
    fl = filename.lower()
    return any(fl.endswith(e.lower()) for e in exts)


def strip_known_ext(name):
    """按 CONFIG 中登记的扩展名（含复合扩展名）安全去除后缀，最长优先。"""
    fl = name.lower()
    all_exts = sorted(set(CONFIG["img_extensions"] + CONFIG["roi_extensions"]), key=len, reverse=True)
    for e in all_exts:
        if fl.endswith(e.lower()):
            return name[: -len(e)]
    return os.path.splitext(name)[0]


def clean_stem(path):
    """basename 去扩展名 + 去首尾空格 + 小写，用于分类判定与去重。"""
    return strip_known_ext(os.path.basename(path)).strip().lower()


def normalize_key(s):
    """归一化用于去重比较：去掉所有空白，小写。"""
    return re.sub(r"\s+", "", s.lower())


def normalize_rel(rel):
    """相对路径归一化用于跨平台一致的匹配 key：统一分隔符 + 统一小写。
    注意：不用 os.path.normcase，因为它是否转小写取决于运行平台（Windows 会转，Linux 不会），
    会导致同一份脚本在不同系统上出现不一致的大小写敏感行为。"""
    return os.path.normpath(rel).lower()


def _tokens(stem):
    return [t for t in re.split(r"[^a-z0-9]+", stem.lower()) if t]


def _kw_hit_anywhere(stem_lower, keywords):
    """roi 关键词：允许出现在 stem 任意位置，按词边界匹配（如 'adc.merge' 中的 merge）。"""
    for kw in keywords:
        if re.search(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])", stem_lower):
            return True
    return False


def _modality_prefix_hit(token, keywords):
    """模态关键词：按前缀匹配（如 t1fs -> t1, dwi0 -> dwi）。"""
    return any(token.startswith(kw.lower()) for kw in keywords)


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def strip_roi_keywords(stem, extra=()):
    """剥离关键词并压缩为纯字母数字串；extra 用于配对前对称归一化
    （如 img 侧角色词 main/image），使 'x main' 与 'x seg' 得到同一 key。"""
    s = stem.lower()
    for kw in list(CONFIG["roi_keywords"]) + list(extra):
        s = re.sub(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


# ----------------------------------------------------------------------
# 文件扫描
# ----------------------------------------------------------------------
def collect_files(root, exts):
    if os.path.isfile(root):
        return [os.path.normpath(root)] if has_ext(os.path.basename(root), exts) else []
    if not os.path.isdir(root):
        _log(f"警告：路径不存在 -> {root}")
        return []
    result = []
    skip = CONFIG.get("skip_prefix", "._")
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.startswith(skip):
                continue
            if has_ext(f, exts):
                result.append(os.path.normpath(os.path.join(dirpath, f)))
    return result


def dedupe_by_stem(files):
    """按归一化 stem（去空格+小写）去重，重复项（多为拷贝残留/命名笔误）单独返回。
    同一归一化 key 下优先保留"干净"的文件名（basename 本身不含多余空格）。"""
    groups = {}
    for f in files:
        key = normalize_key(clean_stem(f))
        groups.setdefault(key, []).append(f)

    kept, dups = [], []
    for key, flist in groups.items():
        if len(flist) == 1:
            kept.append(flist[0])
            continue
        def _has_stray_space(p):
            raw_stem = strip_known_ext(os.path.basename(p))
            return raw_stem != raw_stem.strip()
        flist_sorted = sorted(flist, key=lambda p: (_has_stray_space(p), p))
        kept.append(flist_sorted[0])
        dups.extend(flist_sorted[1:])
    return kept, dups


# ----------------------------------------------------------------------
# 场景 A：同目录匹配（关键词 + 前缀 + 消去法 + 相似度兜底）
# ----------------------------------------------------------------------
def classify_file(path):
    """依据文件名本身（basename）判定，扫描全部分词而非仅最后一个——
    应对 "chen xue ming image 2.nrrd" 这类关键词不在末尾（末尾是序号）的命名。"""
    stem = clean_stem(path)
    tokens = _tokens(stem)

    if any(ROI_Z_PATTERN.fullmatch(t) for t in tokens):
        return "roi"
    if _kw_hit_anywhere(stem, CONFIG["roi_keywords"]):
        return "roi"
    if any(_modality_prefix_hit(t, CONFIG["img_modality_keywords"]) for t in tokens):
        return "img"
    return None


def _best_assignment(img_f, roi_f):
    """按文件名相似度做双向最优（贪心）配对，返回 (pairs, leftover_img, leftover_roi)。"""
    pairs, used_roi = [], set()
    for imgf in sorted(img_f):
        istem = strip_roi_keywords(clean_stem(imgf), CONFIG.get("img_role_keywords", []))
        best, best_score = None, -1.0
        for r in roi_f:
            if r in used_roi:
                continue
            rstem = strip_roi_keywords(clean_stem(r), CONFIG.get("img_role_keywords", []))
            score = similarity(istem, rstem)
            if score > best_score:
                best, best_score = r, score
        if best is not None:
            pairs.append((imgf, best, best_score))
            used_roi.add(best)
    leftover_img = [f for f in img_f if f not in [p[0] for p in pairs]]
    leftover_roi = [f for f in roi_f if f not in used_roi]
    return pairs, leftover_img, leftover_roi


def _norm_stem_key(s):
    return s.strip().lower()


def match_same_dir(root, code_map=None):
    code_map = {_norm_stem_key(k): _norm_stem_key(v) for k, v in (code_map or {}).items()}
    all_exts = CONFIG["img_extensions"] + CONFIG["roi_extensions"]
    files = collect_files(root, all_exts)

    by_dir = {}
    for f in files:
        by_dir.setdefault(os.path.dirname(f), []).append(f)

    pairs, unmatched, ambiguous = [], [], []  # pairs: (img, roi, method)

    for d, flist in sorted(by_dir.items()):
        flist, dups = dedupe_by_stem(flist)
        for dup in dups:
            unmatched.append((dup, "duplicate_ignored"))

        # 0) 显式编码映射：精确匹配（忽略大小写/首尾空格），优先于关键词分类
        #    支持多个 img 映射到同一个 roi（共享掩膜），如 {"ADC":"Z1","T2":"Z1","C":"Z1"}
        explicit_img_used = set()
        explicit_roi_used = set()
        if code_map:
            stem_to_files = {}
            for f in flist:
                stem_to_files.setdefault(_norm_stem_key(clean_stem(f)), []).append(f)
            for f in flist:
                stem = _norm_stem_key(clean_stem(f))
                target = code_map.get(stem)
                if target and target in stem_to_files:
                    for rf in stem_to_files[target]:
                        pairs.append((f, rf, "explicit_code_map"))
                        explicit_img_used.add(f)
                        explicit_roi_used.add(rf)

        remaining = [f for f in flist if f not in explicit_img_used]

        roi_f = [f for f in remaining if classify_file(f) == "roi"]
        img_f = [f for f in remaining if classify_file(f) == "img"]
        unk_f = [f for f in remaining if classify_file(f) is None]

        # 消去法：目录内只有一类被识别出来时，剩余未识别文件归为另一类
        if unk_f:
            if roi_f and not img_f:
                img_f = img_f + unk_f
                unk_f = []
            elif img_f and not roi_f:
                roi_f = roi_f + unk_f
                unk_f = []
            elif not roi_f and not img_f and len(unk_f) == 2:
                ambiguous.append((d, unk_f))
                unk_f = []
            elif not roi_f and not img_f and len(unk_f) == 1:
                if unk_f[0] not in explicit_roi_used:
                    unmatched.append((unk_f[0], "single_file_no_pair"))
                unk_f = []
            else:
                for f in unk_f:
                    if f not in explicit_roi_used:
                        unmatched.append((f, "unclassified"))
                unk_f = []

        if not roi_f and not img_f:
            continue

        if len(roi_f) == 1 and img_f:
            for imgf in img_f:
                pairs.append((imgf, roi_f[0], "same_dir_keyword"))
        elif len(img_f) == 1 and len(roi_f) > 1:
            for r in roi_f:
                pairs.append((img_f[0], r, "same_dir_keyword"))
        elif img_f and roi_f and len(roi_f) == len(img_f):
            assigned, _, _ = _best_assignment(img_f, roi_f)
            for imgf, r, _score in assigned:
                pairs.append((imgf, r, "same_dir_elimination"))
        elif img_f and roi_f:
            # 数量不等的兜底：尽量按相似度配出能配的部分，配不出的记未匹配
            assigned, left_img, left_roi = _best_assignment(img_f, roi_f)
            for imgf, r, score in assigned:
                if score >= CONFIG["fuzzy_threshold"]:
                    pairs.append((imgf, r, "same_dir_fuzzy"))
                else:
                    unmatched.append((imgf, "count_mismatch_in_dir"))
                    unmatched.append((r, "count_mismatch_in_dir"))
            for f in left_img + left_roi:
                unmatched.append((f, "count_mismatch_in_dir"))
        else:
            for f in roi_f + img_f:
                if f not in explicit_roi_used:
                    unmatched.append((f, "count_mismatch_in_dir"))

    return pairs, unmatched, ambiguous


# ----------------------------------------------------------------------
# 场景 B：跨根目录匹配（相对路径 -> z前缀 -> 同目录相似度兜底）
# ----------------------------------------------------------------------
def match_cross_root(img_root, roi_root):
    img_files = collect_files(img_root, CONFIG["img_extensions"])
    roi_files = collect_files(roi_root, CONFIG["roi_extensions"])

    img_by_rel = {normalize_rel(os.path.relpath(p, img_root)): p for p in img_files}
    roi_by_rel = {normalize_rel(os.path.relpath(p, roi_root)): p for p in roi_files}

    pairs = []          # (img, roi, method)
    matched_rel = set()

    # 1) 相对路径（含扩展名）精确匹配
    for rel, ip in img_by_rel.items():
        if rel in roi_by_rel:
            pairs.append((ip, roi_by_rel[rel], "exact_relpath"))
            matched_rel.add(rel)

    def _remaining():
        ri = {r: p for r, p in img_by_rel.items() if r not in matched_rel}
        rr = {r: p for r, p in roi_by_rel.items() if r not in matched_rel}
        return ri, rr

    # 2) 去扩展名后按相对路径匹配
    remaining_img, remaining_roi = _remaining()
    roi_by_stem = {strip_known_ext(r): (r, p) for r, p in remaining_roi.items()}
    for rel, ip in remaining_img.items():
        stem = strip_known_ext(rel)
        if stem in roi_by_stem:
            r_rel, rp = roi_by_stem[stem]
            pairs.append((ip, rp, "stem"))
            matched_rel.add(rel)
            matched_rel.add(r_rel)

    # 3) roi 相对路径开头多一个 "z"（如 1.nii <-> Z1.nii）
    remaining_img, remaining_roi = _remaining()
    for r_rel, rp in list(remaining_roi.items()):
        base = os.path.basename(r_rel)
        if base.startswith("z") and len(base) > 1:
            candidate_rel = normalize_rel(os.path.join(os.path.dirname(r_rel), base[1:]))
            if candidate_rel in remaining_img:
                pairs.append((remaining_img[candidate_rel], rp, "z_prefix"))
                matched_rel.add(candidate_rel)
                matched_rel.add(r_rel)

    # 4) 同一相对目录下，按文件名相似度做双向最优匹配兜底
    remaining_img, remaining_roi = _remaining()
    img_by_dir, roi_by_dir = {}, {}
    for rel, p in remaining_img.items():
        img_by_dir.setdefault(os.path.dirname(rel), []).append(p)
    for rel, p in remaining_roi.items():
        roi_by_dir.setdefault(os.path.dirname(rel), []).append(p)

    for dkey in set(img_by_dir) & set(roi_by_dir):
        assigned, _, _ = _best_assignment(img_by_dir[dkey], roi_by_dir[dkey])
        for imgf, rf, score in assigned:
            if score >= CONFIG["fuzzy_threshold"]:
                i_rel = normalize_rel(os.path.relpath(imgf, img_root))
                r_rel = normalize_rel(os.path.relpath(rf, roi_root))
                pairs.append((imgf, rf, "fuzzy"))
                matched_rel.add(i_rel)
                matched_rel.add(r_rel)

    unmatched_img = [p for r, p in img_by_rel.items() if r not in matched_rel]
    unmatched_roi = [p for r, p in roi_by_rel.items() if r not in matched_rel]
    return pairs, unmatched_img, unmatched_roi


# ----------------------------------------------------------------------
# 单个 task 分发
# ----------------------------------------------------------------------
def process_task(task):
    name = task.get("name", "task")
    img_root = task["img_root"]
    roi_root = task.get("roi_root", img_root)

    _log(f"[{name}] IMG={img_root}  ROI={roi_root}")

    if os.path.isfile(img_root) and os.path.isfile(roi_root):
        return [(img_root, roi_root, "single_file")], [], []

    if normalize_rel(img_root) == normalize_rel(roi_root):
        _log(f"[{name}] 同目录模式（关键词 + 前缀 + 消去法 + 显式编码映射）")
        code_map = task.get("explicit_code_map", CONFIG.get("explicit_code_map", {}))
        return match_same_dir(img_root, code_map)
    else:
        _log(f"[{name}] 跨根目录模式（相对路径 -> z前缀 -> 相似度兜底）")
        pairs, u_img, u_roi = match_cross_root(img_root, roi_root)
        unmatched = [(p, "img_no_match") for p in u_img] + [(p, "roi_no_match") for p in u_roi]
        return pairs, unmatched, []


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main():
    start_time = datetime.now()
    os.makedirs(CONFIG["output_dir"], exist_ok=True)
    
    global logger
    logger = setup_logger(CONFIG["log_dir"], name="match_img")
    CheckpointManager(CONFIG["checkpoint_dir"])

    # 任务名唯一性检查（避免统计/报告混淆）
    names = [t.get("name", "task") for t in CONFIG["tasks"]]
    dup_names = {n for n in names if names.count(n) > 1}
    if dup_names:
        _log(f"警告：以下 task name 重复，请改为唯一名称，否则统计会混在一起: {dup_names}")

    combined_rows = []   # task, img_path, roi_path, match_method
    unmatched_rows = []  # task, path, reason
    report_lines = []
    method_counter = {}

    for task in CONFIG["tasks"]:
        name = task.get("name", "task")
        
        pairs, unmatched, ambiguous = process_task(task)

        for img_p, roi_p, method in pairs:
            combined_rows.append({"task": name, "img_path": img_p, "roi_path": roi_p, "match_method": method})
            method_counter[method] = method_counter.get(method, 0) + 1
        for p, reason in unmatched:
            unmatched_rows.append({"task": name, "path": p, "reason": reason})

        report_lines.append(f"[{name}]")
        report_lines.append(f"  matched   : {len(pairs)}")
        report_lines.append(f"  unmatched : {len(unmatched)}")
        if ambiguous:
            report_lines.append(f"  ambiguous_dirs (无法自动判定，需人工确认): {len(ambiguous)}")
            for d, fs in ambiguous:
                report_lines.append(f"    - {d}: {[os.path.basename(f) for f in fs]}")
        report_lines.append("")

    comb_path = os.path.normpath(os.path.join(CONFIG["output_dir"], CONFIG["outputs"]["combined"]))
    unmatch_path = os.path.normpath(os.path.join(CONFIG["output_dir"], CONFIG["outputs"]["unmatched"]))
    report_path = os.path.normpath(os.path.join(CONFIG["output_dir"], CONFIG["outputs"]["report"]))

    with open(comb_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["task", "img_path", "roi_path", "match_method"])
        writer.writeheader()
        writer.writerows(combined_rows)

    with open(unmatch_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["task", "path", "reason"])
        writer.writeheader()
        writer.writerows(unmatched_rows)

    duration = (datetime.now() - start_time).total_seconds()
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Duration: {duration:.2f}s\n")
        f.write(f"Total matched: {len(combined_rows)}\n")
        f.write(f"Total unmatched: {len(unmatched_rows)}\n")
        f.write("Match method breakdown (越靠后建议越优先人工抽查):\n")
        for m in ["single_file", "exact_relpath", "stem", "z_prefix",
                  "same_dir_keyword", "same_dir_elimination", "same_dir_fuzzy", "fuzzy"]:
            if m in method_counter:
                f.write(f"  {m}: {method_counter[m]}\n")
        f.write("=" * 60 + "\n\n")
        f.write("\n".join(report_lines))

    print(f"\nDone! Duration: {duration:.2f}s")
    print(f"- Combined Result : {comb_path}  ({len(combined_rows)} pairs)")
    print(f"- Unmatched Items : {unmatch_path}  ({len(unmatched_rows)} items)")
    print(f"- Report          : {report_path}")
    print(f"- Match methods   : {method_counter}")


if __name__ == "__main__":
    main()