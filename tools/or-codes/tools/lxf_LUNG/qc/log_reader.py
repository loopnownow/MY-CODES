# -*- coding: utf-8 -*-
"""
解析 Delta 生境 Pipeline 日志（文件夹输入），按患者提取全流程信息并输出合并 CSV。

设计原则：
  - 软编码：所有路径、编码、正则锚点均集中在顶部配置区。
  - 文件夹输入：自动扫描 INPUT_DIR 下所有 .log 文件。
  - 取最后一次：按文件名时间戳排序，每个患者取最后一次运行结果（成功或失败都取最后一次）。
  - 信息最大化：尽量提取每一项信息（预处理/配准/生境/特征/Delta/全局模型/失败原因/各阶段耗时）。

日志实测编码为 UTF-8（无 BOM）。
"""

import re
import csv
import os
import glob
from datetime import datetime, timedelta

# ============================================================
# 配置区（Configuration）
# ============================================================
INPUT_DIR = os.environ.get("INPUT_DIR", "")                 # 输入文件夹（自动扫描 .log）
OUTPUT_DIR = r"{lab_root}\pre-submit\lxf_LUNG\ref\code-gates\qc-logs\qc_3js"      # 输出目录
OUTPUT_CSV_NAME = "qc_3js_merged.csv"            # 输出文件名
LOG_ENCODING = "utf-8"
TIME_FMT = "%Y-%m-%d %H:%M:%S"
LOG_GLOB = "*.log"                               # 扫描模式
FILENAME_DATE_RE = re.compile(r"(\d{8})")        # 从文件名提取日期段 → base_date

RE = {
    "preproc_start": re.compile(r"\[预处理\]\s*患者\s*(?P<id>\w+)\s*\|\s*(?P<tp>pre|post)"),
    "img_info": re.compile(r"图像尺寸=(?P<size>\([^)]*\)),\s*间距=(?P<spacing>\([^)]*\))\s*mm,\s*像素类型=(?P<pixel_type>.+)$"),
    "resample": re.compile(r"重采样：\([^)]*\)\s*→\s*(?P<resampled>\[[^\]]*\])"),
    "mask_voxels": re.compile(r"Mask\s*重采样完成，肿瘤体素数：(?P<voxels>\d+)"),
    "preproc_verify": re.compile(r"预处理验证通过（患者\s*(?P<id>\w+)\s*\|\s*(?P<tp>pre|post)）：尺寸=(?P<size>\([^)]*\))，mask\s*体素数=(?P<voxels>\d+)"),
    "feature_extracted": re.compile(r"患者\s*(?P<id>\w+)\s*体素特征已提取（(?P<voxels>\d+)\s*体素，(?P<channels>\d+)\s*通道）"),
    "reg_start": re.compile(r"\[配准\]\s*患者\s*(?P<id>\w+)\s*$"),
    "reg_saved": re.compile(r"配准后图像已保存：.*patient_(?P<id>\w+)"),
    "reg_qc_pass": re.compile(r"配准\s*QC\s*通过：Dice=(?P<dice>[\d.]+)，Hausdorff=(?P<hd>[\d.]+)\s*mm"),
    "qc_warn": re.compile(r"患者\s*(?P<id>\w+)\s*配准\s*QC\s*未达标"),
    "dice": re.compile(r"Dice=(?P<dice>[\d.]+)"),
    "hausdorff": re.compile(r"Hausdorff=(?P<hd>[\d.]+)\s*mm"),
    "reg_stage": re.compile(r"阶段\s*(?P<stage>\d)/3：(?P<name>Translation|Affine|SyN)"),
    "habitat_start": re.compile(r"\[生境划分\]\s*患者\s*(?P<id>\w+)：全局模型（K=(?P<k>\d+)）"),
    "habitat_info": re.compile(r"Habitat\s*(?P<num>\d+)：(?P<voxels>\d+)\s*体素\s*（占总\s*mask\s*的\s*(?P<pct>[\d.]+)%）→\s*(?P<path>\S+)"),
    "habitat_verify": re.compile(r"\[验证✓\]\s*生境划分：(?P<k>\d+)\s*个\s*Habitat"),
    "radiomics_start": re.compile(r"\[特征提取\]\s*患者\s*(?P<id>\w+)\s*\|\s*(?P<tp>pre|post)\s*\|\s*(?P<k>\d+)\s*个\s*Habitat"),
    "feature_saved": re.compile(r"特征已保存：.*?(?P<tp>pre|post)_features\.csv\s*（(?P<rows>\d+)\s*行\s*×\s*(?P<cols>\d+)\s*列）"),
    "delta_start": re.compile(r"\[Delta\s*计算\]\s*患者\s*(?P<id>\w+)"),
    "delta_saved": re.compile(r"Delta\s*特征：\s*(?P<hab>\d+)\s*Habitat\s*×\s*(?P<cols>\d+)\s*列\s*→\s*(?P<path>\S+)"),
    "verify": re.compile(r"\[验证✓\]\s*(?P<stage>.+)$"),
    "skip_preproc": re.compile(r"\[跳过\]\s*预处理"),
    "done": re.compile(r"患者\s*(?P<id>\w+)\s*完成\s*✓"),
    "error_extract": re.compile(r"患者\s*(?P<id>\w+)\s*提取失败（跳过）：(?P<reason>.+)$"),
    "error_process": re.compile(r"患者\s*(?P<id>\w+)\s*处理失败：(?P<reason>.+)$"),
    "global_model": re.compile(r"拟合全局生境模型：(?P<samples>\d+)\s*体素样本，\s*(?P<channels>\d+)\s*特征通道"),
    "pca": re.compile(r"PCA：\s*(?P<in_d>\d+)D\s*→\s*(?P<out_d>\d+)D\s*（保留\s*(?P<var>[\d.]+)%\s*方差）"),
    "optimal_k": re.compile(r"最终\s*optimal_k=(?P<k>\d+)（轮廓系数=(?P<sc>[\d.]+)）"),
    "kmeans_done": re.compile(r"全局\s*KMeans\s*拟合完成（K=(?P<k>\d+)）"),
    "timestamp": re.compile(r"^(?P<t>\d{2}:\d{2}:\d{2})"),
}

def discover_logs():
    """扫描输入文件夹下所有 .log，按文件名时间戳排序，返回配置列表。"""
    pattern = os.path.join(INPUT_DIR, LOG_GLOB)
    files = glob.glob(pattern)
    cfgs = []
    for path in files:
        fname = os.path.basename(path)
        base_date = None
        m = FILENAME_DATE_RE.search(fname)
        if m:
            try:
                base_date = datetime.strptime(m.group(1), "%Y%m%d")
            except ValueError:
                base_date = None
        cfgs.append({
            "path": path,
            "tag": os.path.splitext(fname)[0],
            "base_date": base_date,
        })
    # 按文件名排序（时间戳决定运行先后）
    cfgs.sort(key=lambda c: os.path.basename(c["path"]))
    return cfgs

def parse_log_file(cfg):
    path = cfg["path"]
    base_date = cfg["base_date"]
    results = {}

    def ensure(pid):
        if pid not in results:
            results[pid] = {"id": pid}
        return results[pid]

    current_patient = None
    current_tp = "pre"
    last_time = None
    current_date = base_date
    pending_error_pid = None
    reg_stage_times = {}

    if not os.path.exists(path):
        print("  [警告] 找不到日志文件，跳过：" + path)
        return results

    with open(path, "r", encoding=LOG_ENCODING, errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n").rstrip("\r")
            if not line.strip():
                continue

            current_dt = None
            tm = RE["timestamp"].match(line)
            if tm:
                this_t = datetime.strptime(tm.group("t"), "%H:%M:%S")
                if last_time and this_t.time() < last_time.time() and \
                        (last_time - datetime.combine(last_time.date(), this_t.time())).total_seconds() > 3600:
                    current_date += timedelta(days=1)
                current_dt = datetime.combine(current_date.date(), this_t.time())
                last_time = current_dt
                pending_error_pid = None
            else:
                if pending_error_pid and current_patient:
                    rec = results.get(pending_error_pid)
                    if rec and rec.get("failure_reason"):
                        rec["failure_reason"] += " " + line.strip()

            if not tm:
                if current_patient:
                    md = RE["dice"].search(line)
                    if md:
                        ensure(current_patient).setdefault("dice", md.group("dice"))
                    mh = RE["hausdorff"].search(line)
                    if mh:
                        rec = ensure(current_patient)
                        rec.setdefault("hausdorff", mh.group("hd"))
                continue

            m = RE["preproc_start"].search(line)
            if m:
                current_patient = m.group("id")
                current_tp = m.group("tp")
                ensure(current_patient)[current_tp + "_start_time"] = current_dt
                continue

            m = RE["img_info"].search(line)
            if m and current_patient:
                rec = ensure(current_patient)
                rec[current_tp + "_img_size"] = m.group("size")
                rec[current_tp + "_spacing"] = m.group("spacing")
                rec[current_tp + "_pixel_type"] = m.group("pixel_type").strip()
                continue

            m = RE["resample"].search(line)
            if m and current_patient:
                ensure(current_patient)[current_tp + "_resampled_size"] = m.group("resampled")
                continue

            m = RE["mask_voxels"].search(line)
            if m and current_patient:
                ensure(current_patient)[current_tp + "_tumor_voxels"] = m.group("voxels")
                continue

            m = RE["preproc_verify"].search(line)
            if m:
                pid = m.group("id"); tp = m.group("tp")
                rec = ensure(pid)
                rec[tp + "_resampled_size"] = m.group("size")
                rec[tp + "_tumor_voxels"] = rec.get(tp + "_tumor_voxels", m.group("voxels"))
                rec["preproc_done_time"] = current_dt
                continue

            m = RE["feature_extracted"].search(line)
            if m:
                rec = ensure(m.group("id"))
                rec["tumor_voxels_total"] = m.group("voxels")
                rec["channels"] = m.group("channels")
                continue

            m = RE["reg_start"].search(line)
            if m:
                current_patient = m.group("id")
                ensure(current_patient)["reg_start_time"] = current_dt
                reg_stage_times = {}
                continue

            m = RE["reg_stage"].search(line)
            if m and current_patient:
                reg_stage_times[m.group("name")] = current_dt
                continue

            m = RE["reg_saved"].search(line)
            if m:
                current_patient = m.group("id")
                rec = ensure(current_patient)
                rec["reg_done_time"] = current_dt
                # 计算配准子阶段耗时
                t = reg_stage_times.get("Translation")
                a = reg_stage_times.get("Affine")
                s = reg_stage_times.get("SyN")
                if t and a:
                    d = (a - t).total_seconds()
                    rec["reg_translation_dur"] = int(d) if d >= 0 else ""
                if a and s:
                    d = (s - a).total_seconds()
                    rec["reg_affine_dur"] = int(d) if d >= 0 else ""
                if s and isinstance(current_dt, datetime):
                    d = (current_dt - s).total_seconds()
                    rec["reg_syn_dur"] = int(d) if d >= 0 else ""
                continue

            m = RE["reg_qc_pass"].search(line)
            if m:
                rec = ensure(current_patient)
                rec["qc_status"] = "通过"
                rec["dice"] = m.group("dice")
                rec["hausdorff"] = m.group("hd")
                continue

            m = RE["qc_warn"].search(line)
            if m:
                current_patient = m.group("id")
                ensure(current_patient)["qc_status"] = "未达标(继续)"
                md = RE["dice"].search(line)
                if md:
                    ensure(current_patient).setdefault("dice", md.group("dice"))
                continue

            m = RE["habitat_start"].search(line)
            if m:
                current_patient = m.group("id")
                rec = ensure(current_patient)
                rec["habitat_k"] = int(m.group("k"))
                rec["habitat_start_time"] = current_dt
                rec.setdefault("habitats", {})
                continue

            m = RE["habitat_info"].search(line)
            if m and current_patient:
                rec = ensure(current_patient)
                rec.setdefault("habitats", {})
                num = int(m.group("num"))
                rec["habitats"][num] = {
                    "voxels": m.group("voxels"),
                    "pct": m.group("pct"),
                    "path": m.group("path"),
                }
                rec["habitat_done_time"] = current_dt
                continue

            m = RE["habitat_verify"].search(line)
            if m and current_patient:
                ensure(current_patient)["habitat_k"] = int(m.group("k"))
                continue

            m = RE["radiomics_start"].search(line)
            if m:
                current_patient = m.group("id")
                current_tp = m.group("tp")
                continue

            m = RE["feature_saved"].search(line)
            if m and current_patient:
                rec = ensure(current_patient)
                rec["radiomics_" + m.group("tp") + "_done_time"] = current_dt
                rec["radiomics_" + m.group("tp") + "_rows"] = m.group("rows")
                rec["radiomics_" + m.group("tp") + "_cols"] = m.group("cols")
                continue

            m = RE["delta_start"].search(line)
            if m:
                current_patient = m.group("id")
                continue

            m = RE["delta_saved"].search(line)
            if m and current_patient:
                rec = ensure(current_patient)
                rec["delta_done_time"] = current_dt
                rec["delta_habitats"] = m.group("hab")
                rec["delta_cols"] = m.group("cols")
                rec["delta_path"] = m.group("path")
                continue

            m = RE["verify"].search(line)
            if m and current_patient:
                ensure(current_patient)["verify_" + m.group("stage")] = current_dt
                continue

            m = RE["skip_preproc"].search(line)
            if m and current_patient:
                ensure(current_patient)["preproc_skipped"] = True
                continue

            m = RE["done"].search(line)
            if m:
                rec = ensure(m.group("id"))
                rec["pipeline_done_time"] = current_dt
                rec["status"] = "成功"
                continue

            m = RE["error_extract"].search(line)
            if m:
                pid = m.group("id")
                rec = ensure(pid)
                rec["failure_reason"] = m.group("reason").strip()
                rec["status"] = "失败"
                rec["fail_stage"] = "预处理/提取"
                current_patient = pid
                pending_error_pid = pid
                continue

            m = RE["error_process"].search(line)
            if m:
                pid = m.group("id")
                rec = ensure(pid)
                reason = m.group("reason").strip()
                rec["failure_reason"] = reason
                rec["status"] = "失败"
                if "Habitat" in reason:
                    rec["fail_stage"] = "生境"
                elif "Hausdorff" in reason or "SimpleITK" in reason:
                    rec["fail_stage"] = "配准QC"
                elif "预处理" in reason:
                    rec["fail_stage"] = "预处理"
                else:
                    rec["fail_stage"] = "其他"
                current_patient = pid
                pending_error_pid = pid
                continue

            m = RE["global_model"].search(line)
            if m:
                rec = ensure("__global__")
                rec["global_samples"] = m.group("samples")
                rec["global_channels"] = m.group("channels")
                continue

            m = RE["pca"].search(line)
            if m:
                rec = ensure("__global__")
                rec["pca_in"] = m.group("in_d")
                rec["pca_out"] = m.group("out_d")
                rec["pca_var"] = m.group("var")
                continue

            m = RE["optimal_k"].search(line)
            if m:
                rec = ensure("__global__")
                rec["optimal_k"] = m.group("k")
                rec["silhouette"] = m.group("sc")
                continue

            m = RE["kmeans_done"].search(line)
            if m:
                rec = ensure("__global__")
                rec["kmeans_k"] = m.group("k")
                continue

    return results


def merge_results(parsed):
    """按患者取最后一次运行（后运行覆盖先运行），并附加最后一次运行的全局模型信息。"""
    merged = {}
    global_info = {}

    for cfg, data in parsed:
        tag = cfg["tag"]
        # 记录最后一次日志的全局模型信息（后运行覆盖）
        if "__global__" in data:
            global_info = dict(data["__global__"])
        for pid, rec in data.items():
            if pid == "__global__":
                continue
            if pid not in merged:
                merged[pid] = {"id": pid, "_sources": set()}
            target = merged[pid]
            target["_sources"].add(tag)
            for key, val in rec.items():
                if key == "id":
                    continue
                if val is None or val == "":
                    continue
                if key == "habitats":
                    target["habitats"] = val  # 后运行覆盖
                    continue
                target[key] = val  # 后运行覆盖

    # 将最后一次运行的全局模型信息填充到每个患者记录
    for pid, rec in merged.items():
        for key, val in global_info.items():
            if val not in (None, ""):
                rec.setdefault(key, val)

    return merged


def fmt_time(v):
    return v.strftime(TIME_FMT) if isinstance(v, datetime) else ""


def write_csv(merged, path):
    max_k = 0
    for rec in merged.values():
        habs = rec.get("habitats", {})
        if habs:
            max_k = max(max_k, max(habs.keys()) + 1)
        if rec.get("habitat_k"):
            max_k = max(max_k, rec["habitat_k"])

    base_headers = [
        "Patient_ID", "Source", "Status",
        "Pre_Size", "Pre_Spacing", "Pre_PixelType", "Pre_TumorVoxels", "Pre_ResampledSize",
        "Post_Size", "Post_Spacing", "Post_PixelType", "Post_TumorVoxels", "Post_ResampledSize",
        "Channels", "Total_Voxels", "Preproc_Done_Time", "Preproc_Skipped",
        "Dice", "Hausdorff", "QC_Status", "Registration_Done_Time",
        "Habitat_K",
    ]
    habitat_headers = []
    for i in range(max_k):
        habitat_headers += ["Habitat" + str(i) + "_Voxels", "Habitat" + str(i) + "_Pct", "Habitat" + str(i) + "_Path"]
    tail_headers = [
        "Habitat_Done_Time",
        "Radiomics_Pre_Done", "Radiomics_Pre_Rows", "Radiomics_Pre_Cols",
        "Radiomics_Post_Done", "Radiomics_Post_Rows", "Radiomics_Post_Cols",
        "Delta_Done_Time", "Delta_Habitats", "Delta_Cols", "Delta_Path",
        "Pipeline_Done_Time",
        "Preproc_Duration_s", "Registration_Duration_s", "Habitat_Duration_s",
        "Reg_Translation_s", "Reg_Affine_s", "Reg_SyN_s",
        "Global_Samples", "Global_Channels", "PCA_In", "PCA_Out", "PCA_Var",
        "Optimal_K", "Silhouette", "KMeans_K",
        "Fail_Stage", "Failure_Reason",
    ]
    headers = base_headers + habitat_headers + tail_headers

    def duration(rec, start_key, end_key):
        s = rec.get(start_key); e = rec.get(end_key)
        if isinstance(s, datetime) and isinstance(e, datetime):
            d = (e - s).total_seconds()
            return int(d) if d >= 0 else ""
        return ""

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for pid in sorted(merged.keys()):
            r = merged[pid]
            habs = r.get("habitats", {})
            row = {
                "Patient_ID": pid,
                "Source": "+".join(sorted(r.get("_sources", []))),
                "Status": r.get("status", ""),
                "Pre_Size": r.get("pre_img_size", ""),
                "Pre_Spacing": r.get("pre_spacing", ""),
                "Pre_PixelType": r.get("pre_pixel_type", ""),
                "Pre_TumorVoxels": r.get("pre_tumor_voxels", ""),
                "Pre_ResampledSize": r.get("pre_resampled_size", ""),
                "Post_Size": r.get("post_img_size", ""),
                "Post_Spacing": r.get("post_spacing", ""),
                "Post_PixelType": r.get("post_pixel_type", ""),
                "Post_TumorVoxels": r.get("post_tumor_voxels", ""),
                "Post_ResampledSize": r.get("post_resampled_size", ""),
                "Channels": r.get("channels", ""),
                "Total_Voxels": r.get("tumor_voxels_total", ""),
                "Preproc_Done_Time": fmt_time(r.get("preproc_done_time")),
                "Preproc_Skipped": "是" if r.get("preproc_skipped") else "",
                "Dice": r.get("dice", ""),
                "Hausdorff": r.get("hausdorff", ""),
                "QC_Status": r.get("qc_status", ""),
                "Registration_Done_Time": fmt_time(r.get("reg_done_time")),
                "Habitat_K": r.get("habitat_k", ""),
                "Habitat_Done_Time": fmt_time(r.get("habitat_done_time")),
                "Radiomics_Pre_Done": fmt_time(r.get("radiomics_pre_done_time")),
                "Radiomics_Pre_Rows": r.get("radiomics_pre_rows", ""),
                "Radiomics_Pre_Cols": r.get("radiomics_pre_cols", ""),
                "Radiomics_Post_Done": fmt_time(r.get("radiomics_post_done_time")),
                "Radiomics_Post_Rows": r.get("radiomics_post_rows", ""),
                "Radiomics_Post_Cols": r.get("radiomics_post_cols", ""),
                "Delta_Done_Time": fmt_time(r.get("delta_done_time")),
                "Delta_Habitats": r.get("delta_habitats", ""),
                "Delta_Cols": r.get("delta_cols", ""),
                "Delta_Path": r.get("delta_path", ""),
                "Pipeline_Done_Time": fmt_time(r.get("pipeline_done_time")),
                "Preproc_Duration_s": duration(r, "pre_start_time", "preproc_done_time"),
                "Registration_Duration_s": duration(r, "reg_start_time", "reg_done_time"),
                "Habitat_Duration_s": duration(r, "habitat_start_time", "habitat_done_time"),
                "Reg_Translation_s": r.get("reg_translation_dur", ""),
                "Reg_Affine_s": r.get("reg_affine_dur", ""),
                "Reg_SyN_s": r.get("reg_syn_dur", ""),
                "Global_Samples": r.get("global_samples", ""),
                "Global_Channels": r.get("global_channels", ""),
                "PCA_In": r.get("pca_in", ""),
                "PCA_Out": r.get("pca_out", ""),
                "PCA_Var": r.get("pca_var", ""),
                "Optimal_K": r.get("optimal_k", ""),
                "Silhouette": r.get("silhouette", ""),
                "KMeans_K": r.get("kmeans_k", ""),
                "Fail_Stage": r.get("fail_stage", ""),
                "Failure_Reason": r.get("failure_reason", ""),
            }
            for i in range(max_k):
                h = habs.get(i, {})
                row["Habitat" + str(i) + "_Voxels"] = h.get("voxels", "")
                row["Habitat" + str(i) + "_Pct"] = h.get("pct", "")
                row["Habitat" + str(i) + "_Path"] = h.get("path", "")
            writer.writerow(row)


def main():
    cfgs = discover_logs()
    if not cfgs:
        print("未在 " + INPUT_DIR + " 下找到 .log 文件")
        return

    parsed = []
    for cfg in cfgs:
        print("解析 [" + cfg["tag"] + "] " + cfg["path"])
        data = parse_log_file(cfg)
        print("    -> " + str(len(data)) + " 个患者条目")
        parsed.append((cfg, data))

    merged = merge_results(parsed)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, OUTPUT_CSV_NAME)
    write_csv(merged, out_path)

    total = len(merged)
    success = sum(1 for r in merged.values() if r.get("status") == "成功")
    failed = sum(1 for r in merged.values() if r.get("status") == "失败")
    qc_warn = sum(1 for r in merged.values() if r.get("qc_status"))
    both = sum(1 for r in merged.values() if len(r.get("_sources", [])) > 1)

    print("")
    print("========== 合并统计 ==========")
    print("扫描日志数     : " + str(len(cfgs)))
    print("合并后患者总数 : " + str(total))
    print("  成功         : " + str(success))
    print("  失败         : " + str(failed))
    print("  含 QC 警告   : " + str(qc_warn))
    print("  多日志均含   : " + str(both))
    print("结果已保存至   : " + out_path)


if __name__ == "__main__":
    main()