# -*- coding: utf-8 -*-
"""
多日志合并解析器（Delta Habitat Pipeline）

功能：解析 pipeline 产生的 .log 文件，按患者合并多次运行结果，
最大程度提取预处理 / 配准 / 生境划分 / 特征提取等阶段的结构化信息。

运行前请修改下方配置区：
  INPUT_PATHS    —— 日志文件路径或所在目录列表（支持多个）
  OUTPUT_CSV     —— 患者级主表输出路径
  OUTPUT_ERR_CSV —— 失败患者表输出路径

运行：
  python log_reader_mr_v6.py

输出文件：
  1. ec_qc_summary.csv  —— 患者主表
     （含 Habitat 回退标记、回退后非空簇数、生境验证非空数、
      特征提取序列列表、低体素跳过的 Habitat 等）
  2. ec_err.csv         —— 当前终态为「失败」的患者清单

状态判定规则（硬编码优先级）：
  成功(全流程) > 成功(阶段1) > 最后一次出现的记录
  （对应「以成功运行为准，不成功以最后一次为准」）
"""

import re
import csv
import os
import glob
from datetime import datetime, timedelta
from collections import Counter

# ============================================================
# 配置区（Configuration）
# ============================================================
INPUT_PATHS = [
    r"G:\lung_xlm\results",
]

OUTPUT_CSV = r"G:\lung_xlm\QC\lung_xlm_summary.csv"
OUTPUT_ERR_CSV = r"G:\lung_xlm\QC\lung_xlm_err.csv"
GENERATE_ERR_CSV = True

LOG_ENCODING = "utf-8"
TIME_FMT = "%Y-%m-%d %H:%M:%S"
RECURSIVE = False

MERGE_STRATEGY = "last_success"

# 错误分类关键字表（key 出现在失败原因文本中即归类，大小写不敏感）
ERROR_CLASS_KEYWORDS = {
    "bad allocation": "N4_MEMORY",
    "N4BiasFieldCorrection": "N4_MEMORY",
    "Registration failed": "ANTS_REG_FAILED",
    "nifti_image_load failed": "FILE_READ_FAILED",
    "ImageFileReader_Execute": "FILE_READ_FAILED",
    "Failed to allocate memory": "MEMORY_ALLOC",
}

# ==================== STAGE_FIELDS：按阶段组织的正则定义 ====================
# 每个条目：
#   regex   —— 编译后的正则（命名组即捕获字段）
#   scope   —— "patient"（需要 current_patient 上下文）/ "cohort"（队列级）
STAGE_FIELDS = {
    # ---------------- 启动 / 数据加载（队列级） ----------------
    "pipeline_log_path":   {"regex": re.compile(r"日志文件：(?P<path>.+)$"), "scope": "cohort"},
    "pipeline_out_root":   {"regex": re.compile(r"输出根目录：(?P<path>.+)$"), "scope": "cohort"},
    "pipeline_start":      {"regex": re.compile(r"多参数生境影像组学流程启动\s*\|\s*模式:\s*(?P<mode>\S+)"), "scope": "cohort"},
    "data_loaded":         {"regex": re.compile(r"数据表解析完成：(?P<valid>\d+)\s*位有效患者，(?P<skipped>\d+)\s*位跳过（ID：(?P<skipped_ids>.*?)）"), "scope": "cohort"},
    "cohort_loaded":       {"regex": re.compile(r"共加载\s*(?P<n>\d+)\s*位有效患者"), "scope": "cohort"},
    "stage_banner":        {"regex": re.compile(r">>>\s*\[阶段\s*(?P<stage_no>\d+)\]\s*(?P<desc>.+?)\.{0,3}\s*$"), "scope": "cohort"},

    # 数据加载阶段 WARNING（进入阶段1之前就被剔除）
    "data_load_skip":    {"regex": re.compile(r"患者\s*(?P<id>.+?)\s*以下路径不存在，已跳过："), "scope": "patient"},
    "data_load_dup_seq": {"regex": re.compile(r"患者\s*(?P<id>.+?)：序列名\s*'(?P<seq>[^']+)'\s*重复，后出现的记录将覆盖之前的记录。"), "scope": "patient"},

    # ---------------- 阶段1：预处理 ----------------
    "preproc_start":  {"regex": re.compile(r"\[预处理\]\s*患者\s*(?P<id>.+?)\s*\|\s*序列\s*(?P<seq>\w+)\s*\|\s*模态\s*(?P<modality>\w+)"), "scope": "patient"},
    "read_file_debug":{"regex": re.compile(r"读取文件：(?P<path>.+)$"), "scope": "patient"},
    "img_info":       {"regex": re.compile(r"图像尺寸=(?P<size>\([^)]*\)),\s*间距=(?P<spacing>\([^)]*\))\s*mm,\s*像素类型=(?P<pixel_type>.+)$"), "scope": "patient"},
    "resample":       {"regex": re.compile(r"重采样：(?P<orig_size>\([^)]*\))\s*→\s*(?P<resampled>\[[^\]]*\])，间距：(?P<orig_spacing>\([^)]*\))\s*→\s*(?P<target_spacing>\([^)]*\))\s*mm"), "scope": "patient"},
    "n4_start":       {"regex": re.compile(r"执行\s*N4\s*偏置场校正\.\.\."), "scope": "patient"},
    "n4_done":        {"regex": re.compile(r"N4\s*偏置场校正完成"), "scope": "patient"},
    "normalize_zscore":{"regex": re.compile(r"灰度归一化（zscore，mu=(?P<mu>[\d.-]+)，sigma=(?P<sigma>[\d.-]+)）"), "scope": "patient"},
    "normalize_minmax":{"regex": re.compile(r"灰度归一化（minmax，窗：\[(?P<lo>[\d.-]+),\s*(?P<hi>[\d.-]+)\]）"), "scope": "patient"},
    "mask_voxels":    {"regex": re.compile(r"Mask\s*重采样完成，ROI\s*体素数：(?P<voxels>\d+)"), "scope": "patient"},
    "preproc_verify": {"regex": re.compile(r"预处理验证通过（患者\s*(?P<id>.+?)\s*\|\s*(?P<seq>\w+)）：尺寸=(?P<size>\([^)]*\))，mask\s*体素数=(?P<voxels>\d+)"), "scope": "patient"},
    "preproc_saved":  {"regex": re.compile(r"预处理结果已保存：(?P<path>.+)$"), "scope": "patient"},

    # ---------------- 阶段1：配准 ----------------
    "reg_start":         {"regex": re.compile(r"\[配准\]\s*患者\s*(?P<id>.+?)\s*\|\s*序列\s*(?P<seq>\w+)\s*→\s*参考序列空间"), "scope": "patient"},
    "reg_enhance_sync":  {"regex": re.compile(r"\[增强\]\s*强制同步\s*Moving\s*图像的\s*Origin/Direction\s*至\s*Fixed"), "scope": "patient"},
    "reg_enhance_shift": {"regex": re.compile(r"\[增强\]\s*执行强度平移至正值区间"), "scope": "patient"},
    "reg_stage_translation": {"regex": re.compile(r"阶段\s*1/3：Translation\s*平移对齐\.\.\."), "scope": "patient"},
    "reg_stage_affine":      {"regex": re.compile(r"阶段\s*2/3：Affine\s*仿射配准（mask\s*引导）\.\.\."), "scope": "patient"},
    "reg_stage_syn":         {"regex": re.compile(r"阶段\s*3/3：SyN\s*非刚性配准（mask\s*引导）\.\.\."), "scope": "patient"},
    "reg_apply_transform":   {"regex": re.compile(r"将变换应用于原始量程图像\.\.\."), "scope": "patient"},
    "reg_saved":         {"regex": re.compile(r"配准后图像已保存：(?P<path>.+)$"), "scope": "patient"},
    "qc_mi_eval_fail":   {"regex": re.compile(r"互信息评估失败（(?P<err>.+)$"), "scope": "patient"},
    "qc_warn":           {"regex": re.compile(r"患者\s*(?P<id>.+?)\s*\|\s*序列\s*(?P<seq>\w+)\s*配准\s*QC\s*未达标：互信息=(?P<mi>[\w.\-]+)（阈值\s*≥\s*(?P<threshold>[\d.\-]+)）"), "scope": "patient"},

    # ---------------- 阶段1：对齐验证 ----------------
    "align_verify": {"regex": re.compile(r"\[验证✓\]\s*对齐阶段：(?P<nseq>\d+)\s*个序列，mask\s*体素数=(?P<voxels>\d+)"), "scope": "patient"},

    # ---------------- 阶段2：全队列全局建模 ----------------
    "cohort_fusion_extracted": {"regex": re.compile(r"患者\s*(?P<id>.+?)\s*融合特征已提取（(?P<units>\d+)\s*单元\s*×\s*(?P<channels>\d+)\s*通道）"), "scope": "patient"},
    "voxel_fusion_extracted":  {"regex": re.compile(r"体素融合特征提取完成：\d+\s*体素\s*×\s*\d+\s*通道（(?P<names>\[.*\])）"), "scope": "cohort"},
    "cohort_merging":          {"regex": re.compile(r"正在合并全队列特征（共\s*(?P<n>\d+)\s*例）\.\.\."), "scope": "cohort"},
    "cohort_pooling_saved":    {"regex": re.compile(r"全队列池化特征已保存：(?P<shape>\([^)]*\))"), "scope": "cohort"},
    "global_model_loaded":     {"regex": re.compile(r"加载现有全局模型：(?P<path>.+)$"), "scope": "cohort"},
    "global_model_fitting":    {"regex": re.compile(r"拟合全局生境模型：(?P<n>\d+)\s*样本，(?P<c>\d+)\s*特征通道"), "scope": "cohort"},
    "pca_reduce":              {"regex": re.compile(r"PCA：(?P<in_dim>\d+)D\s*→\s*(?P<out_dim>\d+)D\s*（保留\s*(?P<variance>[\d.]+)%\s*方差）"), "scope": "cohort"},
    "pca_skip":                {"regex": re.compile(r"PCA：跳过（特征维度\s*(?P<dim>\d+)\s*≤\s*5）"), "scope": "cohort"},
    "kmeans_fit_done":         {"regex": re.compile(r"全局\s*KMeans\s*拟合完成（K=(?P<k>\d+)）"), "scope": "cohort"},
    "global_model_saved":      {"regex": re.compile(r"全局模型已保存：(?P<path>.+)$"), "scope": "cohort"},
    "global_model_loaded2":    {"regex": re.compile(r"全局模型已加载：(?P<path>.+?)（K=(?P<k>\d+)）"), "scope": "cohort"},
    "k_stable_candidates":     {"regex": re.compile(r"\[(?P<name>[^\]]+)\]\s*稳定\s*K\s*候选：(?P<candidates>\[[^\]]*\])，最终\s*optimal_k=(?P<k>\d+)（轮廓系数=(?P<sil>[\d.]+)）"), "scope": "cohort"},
    "k_fallback_warn":         {"regex": re.compile(r"\[(?P<name>[^\]]+)\]\s*未找到\s*ARI\s*>\s*(?P<ari_thr>[\d.]+)\s*的稳定\s*K，回退至轮廓系数最高的\s*K=(?P<k>\d+)"), "scope": "cohort"},
    "k_opt_plot_saved":        {"regex": re.compile(r"K\s*优化图已保存：(?P<path>.+)$"), "scope": "cohort"},

    # ---------------- 阶段3：生境划分 ----------------
    "habitat_start_global": {"regex": re.compile(r"\[生境划分\]\s*患者\s*(?P<id>.+?)：全局模型（K=(?P<k>\d+)）"), "scope": "patient"},
    "habitat_start_local":  {"regex": re.compile(r"\[生境划分\]\s*患者\s*(?P<id>.+?)：单患者模式（独立优化\s*K）"), "scope": "patient"},
    "superpixel_start":     {"regex": re.compile(r"\[生境划分\]\s*患者\s*(?P<id>.+?)：启用\s*Superpixel\s*预分割"), "scope": "patient"},
    "superpixel_done":      {"regex": re.compile(r"Superpixel\s*分割完成：ROI\s*内生成\s*(?P<n_sp>\d+)\s*个超体素"), "scope": "patient"},
    "habitat_info":         {"regex": re.compile(r"Habitat\s*(?P<num>\d+)：(?P<voxels>\d+)\s*体素（占\s*ROI\s*的\s*(?P<pct>[\d.\-]+)%）"), "scope": "patient"},
    # 扩展：可选捕获「（非空 N）」
    "habitat_verify":       {"regex": re.compile(r"\[验证✓\]\s*生境划分：(?P<k>\d+)\s*个\s*Habitat(?:（非空\s*(?P<nonempty>\d+)）)?"), "scope": "patient"},
    # 新增：全局模型空簇回退
    "habitat_fallback":     {"regex": re.compile(r"\[回退\]\s*患者\s*(?P<id>.+?)：全局模型产生空簇/非空不足，改用局部\s*KMeans（K=(?P<k>\d+)）重新聚类"), "scope": "patient"},
    "habitat_fallback_done":{"regex": re.compile(r"\[回退完成\]\s*患者\s*(?P<id>.+?)：局部\s*KMeans\s*后非空簇数=(?P<n>\d+)"), "scope": "patient"},

    # ---------------- 阶段3：特征提取 ----------------
    # 扩展：可选捕获序列列表 （['CT_plain', ...]）
    "radiomics_start":          {"regex": re.compile(r"\[特征提取\]\s*患者\s*(?P<id>.+?)\s*\|\s*(?P<k>\d+)\s*个\s*Habitat\s*×\s*(?P<nseq>\d+)\s*个序列(?:（(?P<seq_list>\[.*?\])）)?"), "scope": "patient"},
    "radiomics_habitat_voxels": {"regex": re.compile(r"Habitat\s*(?P<h>\d+)（体素数\s*=\s*(?P<voxels>\d+)）"), "scope": "patient"},
    "radiomics_seq_extract":    {"regex": re.compile(r"提取序列\s*(?P<seq>\w+)\.\.\."), "scope": "patient"},
    "radiomics_saved":          {"regex": re.compile(r"特征已保存：(?P<path>.+?)（(?P<rows>\d+)\s*行\s*×\s*(?P<cols>\d+)\s*列）"), "scope": "patient"},
    "radiomics_verify":         {"regex": re.compile(r"\[验证✓\]\s*特征提取"), "scope": "patient"},
    # 新增：低体素 Habitat 跳过特征提取
    "radiomics_skip_habitat":   {"regex": re.compile(r"Habitat\s*(?P<h>\d+)\s*体素数\s*(?P<voxels>\d+)\s*<\s*最低要求\s*(?P<min>\d+)，跳过该生境的特征提取"), "scope": "patient"},

    # ---------------- 阶段4：队列汇总与特征筛选（队列级，解析保留但不输出独立表） ----------------
    "cohort_aggregate":  {"regex": re.compile(r"\[汇总\]\s*队列特征矩阵：(?P<n_patient>\d+)\s*患者\s*×\s*(?P<n_feat>\d+)\s*特征"), "scope": "cohort"},
    "fs_init":           {"regex": re.compile(r"\[特征筛选\]\s*初始特征数：(?P<n>\d+)"), "scope": "cohort"},
    "fs_step1_nzv":      {"regex": re.compile(r"Step\s*1（近零方差过滤，阈值\s*<\s*(?P<thr>[\d.]+)）：剩余\s*(?P<n>\d+)\s*特征"), "scope": "cohort"},
    "fs_step2_corr":     {"regex": re.compile(r"Step\s*2（Pearson\s*去冗余，\|r\|\s*>\s*(?P<thr>[\d.]+)）：剩余\s*(?P<n>\d+)\s*特征（删除\s*(?P<dropped>\d+)\s*个冗余特征）"), "scope": "cohort"},
    "fs_step3_lasso":    {"regex": re.compile(r"Step\s*3（LassoCV，最优\s*alpha=(?P<alpha>[\d.]+)）：剩余\s*(?P<n>\d+)\s*特征"), "scope": "cohort"},
    "fs_saved":          {"regex": re.compile(r"筛选结果已保存至：(?P<path>.+)$"), "scope": "cohort"},
    "cohort_final":      {"regex": re.compile(r"\[汇总完成\]\s*训练集：(?P<train_n>\d+)\s*例，测试集：(?P<test_n>\d+)\s*例，筛选后特征数：(?P<n_feat>\d+)"), "scope": "cohort"},

    # ---------------- 完成 / 跳过 / 流程结束 ----------------
    "done":            {"regex": re.compile(r"患者\s*(?P<id>.+?)\s*完成\s*✓"), "scope": "patient"},
    "skip":            {"regex": re.compile(r"\[跳过\]\s*患者\s*(?P<id>.+?)：(?P<reason>.+)$"), "scope": "patient"},
    "final_summary":   {"regex": re.compile(r"流程结束：成功\s*(?P<success>\d+)\s*例，失败\s*(?P<failed>\d+)\s*例"), "scope": "cohort"},
    "final_failed_ids":{"regex": re.compile(r"失败患者\s*ID：(?P<ids>\[.*\])"), "scope": "cohort"},

    # ---------------- 错误 ----------------
    "error_align":            {"regex": re.compile(r"患者\s*(?P<id>.+?)\s*对齐阶段失败：(?P<reason>.+)$"), "scope": "patient"},
    "error_habitat_feature":  {"regex": re.compile(r"患者\s*(?P<id>.+?)\s*生境/特征阶段失败：(?P<reason>.+)$"), "scope": "patient"},
    "traceback":              {"regex": re.compile(r"Traceback \(most recent call last\)"), "scope": "patient"},
}

# 时间戳行首正则（驱动逐行状态机）
RE_TIMESTAMP = re.compile(r"^(?P<t>\d{2}:\d{2}:\d{2})")

# data_load_skip 触发后的续行格式（无时间戳）
RE_MISSING_PATH_LINE = re.compile(r"^\s+(?P<label>[A-Za-z0-9]+):\s*(?P<path>.+)$")


def collect_log_files(paths):
    log_files = []
    for p in paths:
        if os.path.isfile(p) and p.lower().endswith(".log"):
            log_files.append(p)
        elif os.path.isdir(p):
            log_files.extend(glob.glob(os.path.join(p, "*.log"), recursive=RECURSIVE))
    return sorted(set(os.path.abspath(f) for f in log_files if os.path.isfile(f)))


def classify_error(reason: str) -> str:
    """按 ERROR_CLASS_KEYWORDS 关键字表对失败原因做归类。"""
    if not reason:
        return "UNKNOWN"
    reason_lower = reason.lower()
    for k, v in ERROR_CLASS_KEYWORDS.items():
        if k.lower() in reason_lower:
            return v
    if "memory" in reason_lower:
        return "MEMORY_ALLOC"
    return "OTHER"


def parse_log_file(log_path):
    """
    解析单个日志文件，返回 (results, cohort)。
    results —— {patient_id: {字段...}}
    cohort  —— 该日志文件内发现的队列级信息字典（解析保留，不单独输出）
    """
    path = log_path
    base_date = datetime.now()
    try:
        if "pipeline_" in os.path.basename(path):
            dt_str = os.path.basename(path).replace("pipeline_", "").split(".")[0]
            base_date = datetime.strptime(dt_str, "%Y%m%d_%H%M%S")
    except Exception:
        pass

    results = {}
    cohort = {"k_stable_candidates": [], "k_fallback_warns": [], "stage_banners": []}

    def ensure(pid):
        if pid not in results:
            results[pid] = {"id": pid, "habitats": {}}
        return results[pid]

    current_patient = None
    current_seq = None
    last_time = None
    current_date = base_date
    pending_error_pid = None
    in_traceback = False
    current_dt = None
    pending_dataload_skip_pid = None

    if not os.path.exists(path):
        return results, cohort

    with open(path, "r", encoding=LOG_ENCODING, errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n").rstrip("\r")
            if not line.strip():
                continue

            tm = RE_TIMESTAMP.match(line)
            if tm:
                this_t = datetime.strptime(tm.group("t"), "%H:%M:%S")
                if last_time and this_t.time() < last_time.time() and \
                   (last_time - datetime.combine(last_time.date(), this_t.time())).total_seconds() > 3600:
                    current_date += timedelta(days=1)
                current_dt = datetime.combine(current_date.date(), this_t.time())
                last_time = current_dt
                pending_dataload_skip_pid = None
            else:
                if pending_dataload_skip_pid:
                    mm = RE_MISSING_PATH_LINE.match(line)
                    if mm:
                        rec = results.get(pending_dataload_skip_pid)
                        if rec is not None:
                            rec.setdefault("data_load_skip_missing", []).append(
                                f"{mm.group('label')}:{mm.group('path')}"
                            )
                        continue
                    pending_dataload_skip_pid = None
                if pending_error_pid:
                    rec = results.get(pending_error_pid)
                    if rec:
                        rec.setdefault("failure_reason", "")
                        rec["failure_reason"] += " " + line.strip()[:150]
                continue

            # ============ 队列级 / 患者级 字段逐条匹配 ============

            # --- 启动 / 数据加载 ---
            m = STAGE_FIELDS["pipeline_log_path"]["regex"].search(line)
            if m:
                cohort["log_path"] = m.group("path")
                continue
            m = STAGE_FIELDS["pipeline_out_root"]["regex"].search(line)
            if m:
                cohort["output_root"] = m.group("path")
                continue
            m = STAGE_FIELDS["pipeline_start"]["regex"].search(line)
            if m:
                cohort["run_mode"] = m.group("mode")
                cohort["start_time"] = current_dt
                continue
            m = STAGE_FIELDS["data_loaded"]["regex"].search(line)
            if m:
                cohort["valid_patients"] = m.group("valid")
                cohort["skipped_patients"] = m.group("skipped")
                cohort["skipped_ids"] = m.group("skipped_ids")
                continue
            m = STAGE_FIELDS["cohort_loaded"]["regex"].search(line)
            if m:
                cohort["loaded_patients"] = m.group("n")
                continue
            m = STAGE_FIELDS["stage_banner"]["regex"].search(line)
            if m:
                cohort["stage_banners"].append(f"[阶段{m.group('stage_no')}]{m.group('desc').strip()}")
                continue

            m = STAGE_FIELDS["data_load_skip"]["regex"].search(line)
            if m:
                pid = m.group("id")
                rec = ensure(pid)
                rec["status"] = "数据加载跳过"
                rec["data_load_skip_missing"] = []
                pending_dataload_skip_pid = pid
                continue

            m = STAGE_FIELDS["data_load_dup_seq"]["regex"].search(line)
            if m:
                rec = ensure(m.group("id"))
                rec.setdefault("data_load_dup_seq_warn", [])
                if m.group("seq") not in rec["data_load_dup_seq_warn"]:
                    rec["data_load_dup_seq_warn"].append(m.group("seq"))
                continue

            # --- 阶段1：预处理 ---
            m = STAGE_FIELDS["preproc_start"]["regex"].search(line)
            if m:
                current_patient = m.group("id")
                current_seq = m.group("seq")
                rec = ensure(current_patient)
                rec["proc_start_time"] = current_dt
                rec[f"{current_seq}_start_time"] = current_dt
                rec[f"{current_seq}_modality"] = m.group("modality")
                continue

            m = STAGE_FIELDS["read_file_debug"]["regex"].search(line)
            if m and current_patient and current_seq:
                ensure(current_patient)[f"{current_seq}_source_path"] = m.group("path")
                continue

            m = STAGE_FIELDS["img_info"]["regex"].search(line)
            if m and current_patient:
                rec = ensure(current_patient)
                tag = f"{current_seq}_" if current_seq else ""
                rec[f"{tag}size"] = m.group("size")
                rec[f"{tag}spacing"] = m.group("spacing")
                rec[f"{tag}pixel_type"] = m.group("pixel_type").strip()
                continue

            m = STAGE_FIELDS["resample"]["regex"].search(line)
            if m and current_patient:
                rec = ensure(current_patient)
                tag = f"{current_seq}_" if current_seq else ""
                rec[f"{tag}resampled_dims"] = m.group("resampled")
                rec[f"{tag}target_spacing"] = m.group("target_spacing")
                continue

            m = STAGE_FIELDS["n4_start"]["regex"].search(line)
            if m and current_patient and current_seq:
                ensure(current_patient)[f"{current_seq}_n4_start_time"] = current_dt
                continue
            m = STAGE_FIELDS["n4_done"]["regex"].search(line)
            if m and current_patient and current_seq:
                ensure(current_patient)[f"{current_seq}_n4_done_time"] = current_dt
                continue

            m = STAGE_FIELDS["normalize_zscore"]["regex"].search(line)
            if m and current_patient:
                rec = ensure(current_patient)
                tag = f"{current_seq}_" if current_seq else ""
                rec[f"{tag}norm_method"] = "zscore"
                rec[f"{tag}n4_mu"] = m.group("mu")
                rec[f"{tag}n4_sigma"] = m.group("sigma")
                continue
            m = STAGE_FIELDS["normalize_minmax"]["regex"].search(line)
            if m and current_patient:
                rec = ensure(current_patient)
                tag = f"{current_seq}_" if current_seq else ""
                rec[f"{tag}norm_method"] = "minmax"
                rec[f"{tag}norm_lo"] = m.group("lo")
                rec[f"{tag}norm_hi"] = m.group("hi")
                continue

            m = STAGE_FIELDS["mask_voxels"]["regex"].search(line)
            if m and current_patient:
                ensure(current_patient)["tumor_voxels"] = m.group("voxels")
                continue

            m = STAGE_FIELDS["preproc_verify"]["regex"].search(line)
            if m:
                pid, seq = m.group("id"), m.group("seq")
                rec = ensure(pid)
                rec[f"{seq}_verify_size"] = m.group("size")
                rec["tumor_voxels"] = m.group("voxels")
                rec["preproc_done_time"] = current_dt
                continue

            m = STAGE_FIELDS["preproc_saved"]["regex"].search(line)
            if m and current_patient:
                ensure(current_patient)["preproc_saved_path"] = m.group("path")
                continue

            # --- 阶段1：配准 ---
            m = STAGE_FIELDS["reg_start"]["regex"].search(line)
            if m:
                current_patient = m.group("id")
                current_seq = m.group("seq")
                rec = ensure(current_patient)
                rec["reg_start_time"] = current_dt
                rec[f"reg_{current_seq}_start_time"] = current_dt
                continue

            m = STAGE_FIELDS["reg_enhance_sync"]["regex"].search(line)
            if m and current_patient:
                ensure(current_patient)["reg_enhance_sync"] = "是"
                continue
            m = STAGE_FIELDS["reg_enhance_shift"]["regex"].search(line)
            if m and current_patient:
                ensure(current_patient)["reg_enhance_shift"] = "是"
                continue

            m = STAGE_FIELDS["reg_stage_translation"]["regex"].search(line)
            if m and current_patient and current_seq:
                ensure(current_patient)[f"reg_{current_seq}_stage1_translation_time"] = current_dt
                continue
            m = STAGE_FIELDS["reg_stage_affine"]["regex"].search(line)
            if m and current_patient and current_seq:
                ensure(current_patient)[f"reg_{current_seq}_stage2_affine_time"] = current_dt
                continue
            m = STAGE_FIELDS["reg_stage_syn"]["regex"].search(line)
            if m and current_patient and current_seq:
                ensure(current_patient)[f"reg_{current_seq}_stage3_syn_time"] = current_dt
                continue
            m = STAGE_FIELDS["reg_apply_transform"]["regex"].search(line)
            if m and current_patient and current_seq:
                ensure(current_patient)[f"reg_{current_seq}_apply_transform_time"] = current_dt
                continue

            m = STAGE_FIELDS["reg_saved"]["regex"].search(line)
            if m and current_patient:
                ensure(current_patient)["reg_done_time"] = current_dt
                if current_seq:
                    ensure(current_patient)[f"reg_{current_seq}_saved_path"] = m.group("path")
                continue

            m = STAGE_FIELDS["qc_mi_eval_fail"]["regex"].search(line)
            if m and current_patient:
                ensure(current_patient)["qc_mi_eval_error"] = m.group("err")[:200]
                continue

            m = STAGE_FIELDS["qc_warn"]["regex"].search(line)
            if m:
                rec = ensure(m.group("id"))
                seq = m.group("seq")
                rec["qc_status"] = "未达标(继续)"
                rec[f"qc_{seq}_mi_warn"] = m.group("mi")
                rec[f"qc_{seq}_threshold"] = m.group("threshold")
                rec["qc_mi_warn"] = m.group("mi")
                continue

            # --- 阶段1：对齐验证 ---
            m = STAGE_FIELDS["align_verify"]["regex"].search(line)
            if m and current_patient:
                rec = ensure(current_patient)
                rec["align_verify_nseq"] = m.group("nseq")
                rec["align_verify_voxels"] = m.group("voxels")
                rec["align_verify_time"] = current_dt
                rec["status"] = "成功(阶段1)"
                rec["stage1_success_note"] = "对齐验证通过"
                continue

            # --- 阶段2：全队列全局建模 ---
            m = STAGE_FIELDS["cohort_fusion_extracted"]["regex"].search(line)
            if m:
                rec = ensure(m.group("id"))
                rec["fusion_units"] = m.group("units")
                rec["fusion_channels"] = m.group("channels")
                continue
            m = STAGE_FIELDS["voxel_fusion_extracted"]["regex"].search(line)
            if m:
                if "feature_channel_names" not in cohort:
                    cohort["feature_channel_names"] = m.group("names")
                continue
            m = STAGE_FIELDS["cohort_merging"]["regex"].search(line)
            if m:
                cohort["fusion_merge_n"] = m.group("n")
                continue
            m = STAGE_FIELDS["cohort_pooling_saved"]["regex"].search(line)
            if m:
                cohort["pooled_feature_shape"] = m.group("shape")
                continue
            m = STAGE_FIELDS["global_model_loaded"]["regex"].search(line)
            if m:
                cohort["global_model_source"] = "已加载现有模型"
                cohort["global_model_path"] = m.group("path")
                continue
            m = STAGE_FIELDS["global_model_fitting"]["regex"].search(line)
            if m:
                cohort["global_model_source"] = "新拟合"
                cohort["global_model_fit_samples"] = m.group("n")
                cohort["global_model_fit_channels"] = m.group("c")
                continue
            m = STAGE_FIELDS["pca_reduce"]["regex"].search(line)
            if m:
                cohort["pca_in_dim"] = m.group("in_dim")
                cohort["pca_out_dim"] = m.group("out_dim")
                cohort["pca_variance"] = m.group("variance")
                continue
            m = STAGE_FIELDS["pca_skip"]["regex"].search(line)
            if m:
                cohort["pca_in_dim"] = m.group("dim")
                cohort["pca_out_dim"] = "跳过"
                continue
            m = STAGE_FIELDS["kmeans_fit_done"]["regex"].search(line)
            if m:
                cohort["global_kmeans_k"] = m.group("k")
                continue
            m = STAGE_FIELDS["global_model_saved"]["regex"].search(line)
            if m:
                cohort["global_model_saved_path"] = m.group("path")
                continue
            m = STAGE_FIELDS["global_model_loaded2"]["regex"].search(line)
            if m:
                cohort["global_model_loaded_path"] = m.group("path")
                cohort["global_model_loaded_k"] = m.group("k")
                continue
            m = STAGE_FIELDS["k_stable_candidates"]["regex"].search(line)
            if m:
                cohort["k_stable_candidates"].append(
                    f"{m.group('name')}: candidates={m.group('candidates')}, optimal_k={m.group('k')}, sil={m.group('sil')}"
                )
                continue
            m = STAGE_FIELDS["k_fallback_warn"]["regex"].search(line)
            if m:
                cohort["k_fallback_warns"].append(
                    f"{m.group('name')}: 无稳定K(阈值{m.group('ari_thr')})，回退K={m.group('k')}"
                )
                continue
            m = STAGE_FIELDS["k_opt_plot_saved"]["regex"].search(line)
            if m:
                cohort["k_opt_plot_path"] = m.group("path")
                continue

            # --- 阶段3：生境划分 ---
            m = STAGE_FIELDS["habitat_start_global"]["regex"].search(line)
            if m:
                current_patient = m.group("id")
                rec = ensure(current_patient)
                rec["habitat_k"] = int(m.group("k"))
                rec["habitat_mode"] = "全局模型"
                rec["habitat_start_time"] = current_dt
                continue
            m = STAGE_FIELDS["habitat_start_local"]["regex"].search(line)
            if m:
                current_patient = m.group("id")
                rec = ensure(current_patient)
                rec["habitat_mode"] = "单患者独立优化"
                rec["habitat_start_time"] = current_dt
                continue
            m = STAGE_FIELDS["superpixel_start"]["regex"].search(line)
            if m:
                current_patient = m.group("id")
                ensure(current_patient)["superpixel_used"] = "是"
                continue
            m = STAGE_FIELDS["superpixel_done"]["regex"].search(line)
            if m and current_patient:
                ensure(current_patient)["superpixel_count"] = m.group("n_sp")
                continue

            m = STAGE_FIELDS["habitat_info"]["regex"].search(line)
            if m and current_patient:
                rec = ensure(current_patient)
                num = int(m.group("num"))
                rec["habitats"][num] = {"voxels": m.group("voxels"), "pct": m.group("pct")}
                rec["habitat_done_time"] = current_dt
                continue

            m = STAGE_FIELDS["habitat_verify"]["regex"].search(line)
            if m and current_patient:
                rec = ensure(current_patient)
                rec["habitat_verify_k"] = m.group("k")
                if m.group("nonempty"):
                    rec["habitat_verify_nonempty"] = m.group("nonempty")
                continue

            # 新增：全局模型空簇回退
            m = STAGE_FIELDS["habitat_fallback"]["regex"].search(line)
            if m:
                current_patient = m.group("id")
                rec = ensure(current_patient)
                rec["habitat_fallback"] = "是"
                rec["habitat_fallback_k"] = m.group("k")
                continue
            m = STAGE_FIELDS["habitat_fallback_done"]["regex"].search(line)
            if m:
                rec = ensure(m.group("id"))
                rec["habitat_fallback_done_n"] = m.group("n")
                continue

            # --- 阶段3：特征提取 ---
            m = STAGE_FIELDS["radiomics_start"]["regex"].search(line)
            if m:
                current_patient = m.group("id")
                rec = ensure(current_patient)
                rec["radiomics_start_time"] = current_dt
                rec["radiomics_k"] = m.group("k")
                rec["radiomics_nseq"] = m.group("nseq")
                if m.group("seq_list"):
                    rec["radiomics_seq_list"] = m.group("seq_list")
                continue
            m = STAGE_FIELDS["radiomics_habitat_voxels"]["regex"].search(line)
            if m and current_patient:
                ensure(current_patient)[f"radiomics_h{m.group('h')}_voxels"] = m.group("voxels")
                continue
            m = STAGE_FIELDS["radiomics_seq_extract"]["regex"].search(line)
            if m and current_patient:
                ensure(current_patient)["radiomics_last_seq"] = m.group("seq")
                continue
            m = STAGE_FIELDS["radiomics_saved"]["regex"].search(line)
            if m and current_patient:
                rec = ensure(current_patient)
                rec["radiomics_done_time"] = current_dt
                rec["feature_rows"] = m.group("rows")
                rec["feature_count"] = m.group("cols")
                continue
            m = STAGE_FIELDS["radiomics_verify"]["regex"].search(line)
            if m and current_patient:
                ensure(current_patient)["radiomics_verified"] = "是"
                continue

            # 新增：低体素 Habitat 跳过
            m = STAGE_FIELDS["radiomics_skip_habitat"]["regex"].search(line)
            if m and current_patient:
                rec = ensure(current_patient)
                rec.setdefault("radiomics_skip_habitats", [])
                tag = f"H{m.group('h')}(vox={m.group('voxels')}<{m.group('min')})"
                if tag not in rec["radiomics_skip_habitats"]:
                    rec["radiomics_skip_habitats"].append(tag)
                continue

            # --- 阶段4：队列汇总与特征筛选 ---
            m = STAGE_FIELDS["cohort_aggregate"]["regex"].search(line)
            if m:
                cohort["agg_n_patient"] = m.group("n_patient")
                cohort["agg_n_feat"] = m.group("n_feat")
                continue
            m = STAGE_FIELDS["fs_init"]["regex"].search(line)
            if m:
                cohort["fs_init_n"] = m.group("n")
                continue
            m = STAGE_FIELDS["fs_step1_nzv"]["regex"].search(line)
            if m:
                cohort["fs_step1_n"] = m.group("n")
                cohort["fs_step1_threshold"] = m.group("thr")
                continue
            m = STAGE_FIELDS["fs_step2_corr"]["regex"].search(line)
            if m:
                cohort["fs_step2_n"] = m.group("n")
                cohort["fs_step2_dropped"] = m.group("dropped")
                cohort["fs_step2_threshold"] = m.group("thr")
                continue
            m = STAGE_FIELDS["fs_step3_lasso"]["regex"].search(line)
            if m:
                cohort["fs_step3_n"] = m.group("n")
                cohort["fs_lasso_alpha"] = m.group("alpha")
                continue
            m = STAGE_FIELDS["fs_saved"]["regex"].search(line)
            if m:
                cohort["fs_saved_path"] = m.group("path")
                continue
            m = STAGE_FIELDS["cohort_final"]["regex"].search(line)
            if m:
                cohort["final_train_n"] = m.group("train_n")
                cohort["final_test_n"] = m.group("test_n")
                cohort["final_feat_n"] = m.group("n_feat")
                continue

            # --- 完成 / 跳过 ---
            m = STAGE_FIELDS["done"]["regex"].search(line)
            if m:
                rec = ensure(m.group("id"))
                rec["pipeline_done_time"] = current_dt
                rec["status"] = "成功(全流程)"
                pending_error_pid = None
                continue

            m = STAGE_FIELDS["skip"]["regex"].search(line)
            if m:
                rec = ensure(m.group("id"))
                reason = m.group("reason").strip()
                if "已完成" in reason and "从磁盘加载" in reason:
                    rec["status"] = "成功(阶段1)"
                    rec["stage1_success_note"] = "断点续跑-已从磁盘加载"
                else:
                    rec["skip_reason"] = reason
                    rec["status"] = "跳过"
                pending_error_pid = None
                continue

            # --- 流程结束（队列级）---
            m = STAGE_FIELDS["final_summary"]["regex"].search(line)
            if m:
                cohort["final_success_n"] = m.group("success")
                cohort["final_failed_n"] = m.group("failed")
                continue
            m = STAGE_FIELDS["final_failed_ids"]["regex"].search(line)
            if m:
                cohort["final_failed_ids"] = m.group("ids")
                continue

            # --- 错误 ---
            m = STAGE_FIELDS["error_align"]["regex"].search(line)
            if m:
                rec = ensure(m.group("id"))
                rec["status"] = "失败"
                rec["failure_reason"] = m.group("reason").strip()
                rec["fail_stage"] = "对齐/配准"
                rec["failure_class"] = classify_error(rec["failure_reason"])
                pending_error_pid = m.group("id")
                continue

            m = STAGE_FIELDS["error_habitat_feature"]["regex"].search(line)
            if m:
                rec = ensure(m.group("id"))
                rec["status"] = "失败"
                rec["failure_reason"] = m.group("reason").strip()
                rec["fail_stage"] = "生境/特征"
                rec["failure_class"] = classify_error(rec["failure_reason"])
                pending_error_pid = m.group("id")
                continue

            if STAGE_FIELDS["traceback"]["regex"].search(line) and pending_error_pid:
                in_traceback = True
                continue
            if in_traceback:
                rec = results.get(pending_error_pid)
                if rec:
                    rec.setdefault("failure_reason", "")
                    rec["failure_reason"] += " | " + line.strip()[:150]
                if any(x in line for x in ["Error", "Exception"]):
                    in_traceback = False
                continue

    return results, cohort


def _hist_label(rec):
    """单次运行在 History 中的标签。"""
    st = rec.get("status") or ""
    if st:
        return st
    if rec.get("failure_class"):
        return rec["failure_class"]
    return ""


def _merge_fields_into(target, rec):
    """将 rec 的非空业务字段写入 target（后写覆盖）；不碰合并元数据。"""
    skip_keys = {
        "id", "source_logs", "history", "failure_reasons",
        "success_candidates", "last_seen_rec",
        "source_logs_str", "failure_reason_all", "history_str",
        "status",
    }
    for k, v in rec.items():
        if k in skip_keys:
            continue
        if v in (None, "", {}):
            continue
        if k == "habitats":
            target.setdefault("habitats", {})
            if isinstance(v, dict):
                target["habitats"].update(v)
        elif k == "radiomics_skip_habitats":
            # 列表累积，避免后写覆盖丢失多次跳过信息
            target.setdefault("radiomics_skip_habitats", [])
            if isinstance(v, list):
                for item in v:
                    if item not in target["radiomics_skip_habitats"]:
                        target["radiomics_skip_habitats"].append(item)
        else:
            target[k] = v


def merge_results(parsed_list):
    """
    合并多个日志的患者级结果。

    状态选择（硬编码优先级，无外部配置）：
      1. 任意日志出现过「成功(全流程)」→ 取该状态下时间最新的记录
      2. 否则任意日志出现过「成功(阶段1)」→ 取该状态下时间最新的记录
      3. 否则取该患者最后一次出现的记录
    描述性字段：按日志时间顺序后写覆盖，实现最大程度提取。
    """
    # 硬编码成功优先级（高 → 低）
    SUCCESS_PRIORITY = ["成功(全流程)", "成功(阶段1)"]

    merged = {}
    for log_path, data, _cohort in parsed_list:
        source_tag = os.path.basename(log_path)
        for pid, rec in data.items():
            if pid not in merged:
                merged[pid] = {
                    "id": pid,
                    "source_logs": set(),
                    "history": [],
                    "failure_reasons": [],
                    "success_candidates": {},  # {成功状态: 该状态下时间最新的一条 rec}
                    "last_seen_rec": None,
                    "habitats": {},
                }
            target = merged[pid]
            target["source_logs"].add(source_tag)

            target["history"].append({
                "log": source_tag,
                "status": _hist_label(rec),
                "fail_stage": rec.get("fail_stage", ""),
                "failure_class": rec.get("failure_class", ""),
            })

            _merge_fields_into(target, rec)

            st = rec.get("status") or ""
            if st in SUCCESS_PRIORITY:
                target["success_candidates"][st] = rec
            target["last_seen_rec"] = rec

            if rec.get("failure_reason") and rec["failure_reason"] not in target["failure_reasons"]:
                target["failure_reasons"].append(rec["failure_reason"])

    for target in merged.values():
        chosen = None
        for rank_status in SUCCESS_PRIORITY:
            if rank_status in target["success_candidates"]:
                chosen = target["success_candidates"][rank_status]
                break
        if chosen is None:
            chosen = target["last_seen_rec"]

        if chosen is not None:
            target["status"] = chosen.get("status") or ""
            if chosen.get("fail_stage"):
                target["fail_stage"] = chosen["fail_stage"]
            if chosen.get("failure_class"):
                target["failure_class"] = chosen["failure_class"]
            if chosen.get("skip_reason"):
                target["skip_reason"] = chosen["skip_reason"]
            if chosen.get("failure_reason"):
                target["failure_reason"] = chosen["failure_reason"]
            if target["status"] in SUCCESS_PRIORITY:
                target.pop("fail_stage", None)
                target.pop("failure_class", None)
                target.pop("skip_reason", None)
        else:
            target["status"] = ""

        target["source_logs_str"] = "+".join(sorted(target["source_logs"]))
        target["failure_reason_all"] = (
            " | ".join(target["failure_reasons"])
            if target["failure_reasons"]
            else target.get("failure_reason", "")
        )
        target["history_str"] = " -> ".join(
            f"{h['log']}:{h['status'] or h.get('failure_class', '')}"
            for h in target["history"]
        )
        target.pop("success_candidates", None)
        target.pop("last_seen_rec", None)

    return merged


def fmt_time(v):
    return v.strftime(TIME_FMT) if isinstance(v, datetime) else ""


def _join_list(v, sep=" | "):
    """把列表类字段安全地拼成可读字符串，写入 CSV 单元格。"""
    if isinstance(v, list):
        return sep.join(v)
    return v or ""


def write_csv(merged, path):
    all_seqs = set()
    max_habitat = -1
    for r in merged.values():
        for k in r.keys():
            if k.endswith("_modality"):
                all_seqs.add(k[: -len("_modality")])
        habs = r.get("habitats", {})
        if habs:
            max_habitat = max(max_habitat, max(habs.keys()))

    seqs = sorted(list(all_seqs))
    headers = ["Patient_ID", "Source_Logs", "Status", "Stage1_Success_Note", "History"]
    for s in seqs:
        headers.extend([
            f"{s}_Modality", f"{s}_Size", f"{s}_Spacing", f"{s}_SourcePath",
            f"{s}_ResampledDims", f"{s}_TargetSpacing", f"{s}_VerifySize",
            f"{s}_NormMethod", f"{s}_N4_mu", f"{s}_N4_sigma",
            f"{s}_Norm_Lo", f"{s}_Norm_Hi",
        ])

    headers.extend(["TumorVoxels", "Preproc_Done_Time", "Preproc_Saved_Path"])
    headers.extend(["Reg_Enhance_Sync", "Reg_Enhance_Shift", "Reg_Done_Time", "QC_MI_Eval_Error"])
    for s in seqs:
        headers.extend([f"QC_{s}_MI", f"QC_{s}_Threshold"])
    headers.extend(["QC_Status", "QC_MI_Warn", "Align_Verify_NSeq", "Align_Verify_Voxels", "Align_Verify_Time"])
    headers.extend(["Fusion_Units", "Fusion_Channels"])
    headers.extend([
        "Habitat_Mode", "Habitat_K",
        "Habitat_Fallback", "Habitat_Fallback_K", "Habitat_Fallback_Done_N",
        "Superpixel_Used", "Superpixel_Count",
    ])
    for i in range(max_habitat + 1):
        headers.extend([f"Habitat{i}_Voxels", f"Habitat{i}_Pct"])
    headers.extend(["Habitat_Done_Time", "Habitat_Verify_K", "Habitat_Verify_NonEmpty"])
    headers.extend([
        "Radiomics_K", "Radiomics_NSeq", "Radiomics_Seq_List",
        "Radiomics_Skip_Habitats",
        "Feature_Rows", "Feature_Count",
        "Radiomics_Done_Time", "Radiomics_Verified",
    ])
    headers.extend(["Fail_Stage", "Failure_Class", "Failure_Reason_All", "Skip_Reason"])
    headers.extend(["DataLoad_Skip_Missing", "DataLoad_Dup_Seq_Warn"])

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for pid in sorted(merged.keys()):
            r = merged[pid]
            row = {
                "Patient_ID": pid, "Source_Logs": r.get("source_logs_str", ""),
                "Status": r.get("status", ""),
                "Stage1_Success_Note": r.get("stage1_success_note", ""),
                "History": r.get("history_str", ""),
                "TumorVoxels": r.get("tumor_voxels", ""),
                "Preproc_Done_Time": fmt_time(r.get("preproc_done_time")),
                "Preproc_Saved_Path": r.get("preproc_saved_path", ""),
                "Reg_Enhance_Sync": r.get("reg_enhance_sync", ""),
                "Reg_Enhance_Shift": r.get("reg_enhance_shift", ""),
                "Reg_Done_Time": fmt_time(r.get("reg_done_time")),
                "QC_MI_Eval_Error": r.get("qc_mi_eval_error", ""),
                "QC_Status": r.get("qc_status", ""), "QC_MI_Warn": r.get("qc_mi_warn", ""),
                "Align_Verify_NSeq": r.get("align_verify_nseq", ""),
                "Align_Verify_Voxels": r.get("align_verify_voxels", ""),
                "Align_Verify_Time": fmt_time(r.get("align_verify_time")),
                "Fusion_Units": r.get("fusion_units", ""), "Fusion_Channels": r.get("fusion_channels", ""),
                "Habitat_Mode": r.get("habitat_mode", ""), "Habitat_K": r.get("habitat_k", ""),
                "Habitat_Fallback": r.get("habitat_fallback", ""),
                "Habitat_Fallback_K": r.get("habitat_fallback_k", ""),
                "Habitat_Fallback_Done_N": r.get("habitat_fallback_done_n", ""),
                "Superpixel_Used": r.get("superpixel_used", ""), "Superpixel_Count": r.get("superpixel_count", ""),
                "Habitat_Done_Time": fmt_time(r.get("habitat_done_time")),
                "Habitat_Verify_K": r.get("habitat_verify_k", ""),
                "Habitat_Verify_NonEmpty": r.get("habitat_verify_nonempty", ""),
                "Radiomics_K": r.get("radiomics_k", ""), "Radiomics_NSeq": r.get("radiomics_nseq", ""),
                "Radiomics_Seq_List": r.get("radiomics_seq_list", ""),
                "Radiomics_Skip_Habitats": _join_list(r.get("radiomics_skip_habitats")),
                "Feature_Rows": r.get("feature_rows", ""), "Feature_Count": r.get("feature_count", ""),
                "Radiomics_Done_Time": fmt_time(r.get("radiomics_done_time")),
                "Radiomics_Verified": r.get("radiomics_verified", ""),
                "Fail_Stage": r.get("fail_stage", ""), "Failure_Class": r.get("failure_class", ""),
                "Failure_Reason_All": r.get("failure_reason_all", "")[:1000],
                "Skip_Reason": r.get("skip_reason", ""),
                "DataLoad_Skip_Missing": _join_list(r.get("data_load_skip_missing")),
                "DataLoad_Dup_Seq_Warn": _join_list(r.get("data_load_dup_seq_warn"), sep=","),
            }
            for s in seqs:
                row[f"{s}_Modality"] = r.get(f"{s}_modality", "")
                row[f"{s}_Size"] = r.get(f"{s}_size", "")
                row[f"{s}_Spacing"] = r.get(f"{s}_spacing", "")
                row[f"{s}_SourcePath"] = r.get(f"{s}_source_path", "")
                row[f"{s}_ResampledDims"] = r.get(f"{s}_resampled_dims", "")
                row[f"{s}_TargetSpacing"] = r.get(f"{s}_target_spacing", "")
                row[f"{s}_VerifySize"] = r.get(f"{s}_verify_size", "")
                row[f"{s}_NormMethod"] = r.get(f"{s}_norm_method", "")
                row[f"{s}_N4_mu"] = r.get(f"{s}_n4_mu", "")
                row[f"{s}_N4_sigma"] = r.get(f"{s}_n4_sigma", "")
                row[f"{s}_Norm_Lo"] = r.get(f"{s}_norm_lo", "")
                row[f"{s}_Norm_Hi"] = r.get(f"{s}_norm_hi", "")
                row[f"QC_{s}_MI"] = r.get(f"qc_{s}_mi_warn", "")
                row[f"QC_{s}_Threshold"] = r.get(f"qc_{s}_threshold", "")

            habs = r.get("habitats", {})
            for i in range(max_habitat + 1):
                h = habs.get(i, {})
                row[f"Habitat{i}_Voxels"] = h.get("voxels", "")
                row[f"Habitat{i}_Pct"] = h.get("pct", "")
            writer.writerow(row)


def write_error_csv(merged, path):
    if not GENERATE_ERR_CSV:
        return
    err_rows = []
    for pid, r in merged.items():
        if r.get("status") != "失败":
            continue
        err_rows.append({
            "Patient_ID": pid, "Source_Logs": r.get("source_logs_str", ""),
            "Fail_Stage": r.get("fail_stage", ""),
            "Failure_Class": r.get("failure_class", classify_error(r.get("failure_reason_all", ""))),
            "Failure_Reason": r.get("failure_reason_all", "")[:1500],
            "History": r.get("history_str", ""),
        })

    if not err_rows:
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=err_rows[0].keys())
        writer.writeheader()
        writer.writerows(err_rows)


def main():
    print(">>> 多日志解析器 启动")
    log_files = collect_log_files(INPUT_PATHS)
    print(f"发现 {len(log_files)} 个日志文件")

    parsed_list = []
    for log_path in log_files:
        print(f"解析：{os.path.basename(log_path)}")
        data, cohort = parse_log_file(log_path)
        print(f"    -> {len(data)} 个患者条目")
        parsed_list.append((log_path, data, cohort))

    print("\n>>> 智能合并中...")
    merged = merge_results(parsed_list)

    write_csv(merged, OUTPUT_CSV)
    write_error_csv(merged, OUTPUT_ERR_CSV)

    total = len(merged)
    status_counts = Counter(r.get("status", "") for r in merged.values())

    print("\n========== 合并统计 ==========")
    print(f"患者总数 : {total}")
    print(f"  成功(全流程): {status_counts.get('成功(全流程)', 0)}")
    print(f"  成功(阶段1) : {status_counts.get('成功(阶段1)', 0)}")
    print(f"  失败        : {status_counts.get('失败', 0)}")
    print(f"  数据加载跳过: {status_counts.get('数据加载跳过', 0)}")
    print(f"  跳过        : {status_counts.get('跳过', 0)}")
    print(f"  无终态      : {status_counts.get('', 0)}")

    print(f"\n患者级主表 : {OUTPUT_CSV}")
    print(f"处理失败表 : {OUTPUT_ERR_CSV}")
    print(">>> 全部完成")


if __name__ == "__main__":
    main()
