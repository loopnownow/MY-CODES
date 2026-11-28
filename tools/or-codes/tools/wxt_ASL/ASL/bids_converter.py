# -*- coding: utf-8 -*-
"""
bids_converter.py - SSD/HC 数据集 BIDS 标准化转换脚本
==========================================================
功能:
  1. 扫描 L:\\00used\\SSD_dyf\\dingallcases (根目录 + rawnii 子目录),
     为每个具备 T1 + ASL(+可选 DWI) 的受试者生成 BIDS ready 目录:
        rawdata/sub-XXX/
        +-- anat/sub-XXX_T1w.nii.gz
        +-- perf/sub-XXX_asl.nii.gz (+ _asl.json + _aslcontext.tsv)
        +-- dwi/sub-XXX_acq-dti_dwi.nii.gz/.bval/.bvec
                sub-XXX_acq-dki_dwi.*
                sub-XXX_acq-ivim_dwi.*
  2. 从 SSDHC.xlsx 读取组别与人口学变量 -> participants.tsv。
  3. 输出校验报告与受试者清单 subjects_matrix.tsv。

用法:
  python bids_converter.py                 # 正常执行
  python bids_converter.py --dry-run       # 仅预演, 不写文件
  python bids_converter.py --excel <路径>
  python bids_converter.py --src   <路径>

安全机制:
  * 默认不覆盖已有 rawdata (--force 才重建)
  * 同盘优先硬链接, 失败退回复制
  * 输出 subjects_matrix.tsv 记录受试者来源与文件映射
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import openpyxl

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ====================================================================
# 1. 配置区 ------ 置顶, 按需修改
# ====================================================================
SRC_ROOT      = Path(os.environ.get("SRC_ROOT", ""))
EXCEL_PATH    = Path(r"{lab_root}\_DataBase\SSD\_share\SSD_dyf\SSDHC.xlsx")   # 表不进 L:
STUDY_ROOT    = Path(os.environ.get("STUDY_ROOT", ""))
RAW_DIR       = STUDY_ROOT / "rawdata"
SUBJ_NUM_FMT  = "{n:03d}"
DRY_RUN       = False
FORCE_REBUILD = False
USE_HARDLINK  = True
MAX_SUBJECTS  = 0                 # 0 = 全部

# ---- ASL sidecar 参数 (Siemens 3T Verio, ep2d_pasl) ----
# NOTE: TI1/TI2 为典型默认值, 需按实际扫描协议核实修改。
ASL_JSON_TPL = {
    "Manufacturer": "Siemens",
    "ManufacturersModelName": "Verio",
    "MagneticFieldStrength": 3,
    "RepetitionTime": 2.5,             # TR = 2500 ms (用户确认)
    "RepetitionTimePreparation": 2.5,  # ExploreASL ASL 约定字段 (映射 legacy RepetitionTime)
    "EchoTime": 0.012,                 # TE = 12 ms (用户确认)
    "FlipAngle": 90,
    "ArterialSpinLabelingType": "PASL",
    "MRAcquisitionType": "2D",
    "PulseSequenceType": "2D_EPI",   # ExploreASL 约定: 2D 采集须为 '2D_EPI'
    "M0Type": "Included",
    "TotalAcquiredPairs": 45,
    "LabelingDuration": 0.7,           # TI1 ~700 ms
    "InitialPostLabelingDelay": 1.8,   # TI2 ~1800 ms
    "PostLabelingDelay": 1.8,
    "InversionTime": 1.8,
    "SliceTiming": 0.030,              # 2D EPI 每层读出时间 (s) -> legacy SliceReadoutTime(ms)
    "BolusCutOffFlag": False,
    "BackgroundSuppression": False,
}
ASL_N_VOLS       = 91                     # 验证用
ASL_N_PAIRS      = 45
ASL_FIRST_ISTYPE = "label"    # 第1对第1卷为 label (与机载PW图相关性验证)

# ---- 文件关键词匹配 ----
K_T1      = "t1_mprage_tra_p2_iso"
K_ASL     = "asl_2d_tra"
K_DTI4D   = "ep2d_diff_mddw_25_p2"
K_DKI4D   = "ep2d_diff_dki_tracew"
K_IVIM4D  = "ep2d_diff_ivim_tracew"
K_3SCAN   = "ep2d_diff_3scan_trace_p2"

DATASET_DESCRIPTION = {
    "Name": "SSD_HC_ASL_PASL",
    "BIDSVersion": "1.6.0",
    "License": "CC0",
    "Authors": [],
    "Acknowledgements": "Imported with ExploreASL VERSION_1.11.0",
    "HowToAcknowledge": "",
}

PARTICIPANTS_COLS = [
    "participant_id", "group", "age", "sex", "education_years",
    "source_dir", "sub_tag", "has_t1", "has_asl", "has_dti", "has_dki",
    "has_ivim", "notes",
]
# ====================================================================
# 2. 工具函数
# ====================================================================
def xlsx_to_dict(xlsx_path: Path) -> dict:
    """读取 SSDHC.xlsx -> {英文姓名大写: {group,age,sex,edu}}。"""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    sheet = wb["SSDHC"]
    rows = list(sheet.iter_rows(values_only=True))
    hdr = [str(h).strip() if h else "" for h in rows[0]]
    lookup = {name: hdr.index(name) for name in
              ("英文姓名", "组别", "年龄", "性别", "受教育年限")}
    out: dict[str, dict] = {}
    for r in rows[1:]:
        en = r[lookup["英文姓名"]]
        if not en:
            continue
        key = str(en).strip().upper()
        age, sex, edu = r[lookup["年龄"]], r[lookup["性别"]], r[lookup["受教育年限"]]
        out[key] = {
            "group": str(r[lookup["组别"]]).strip() if r[lookup["组别"]] else "",
            "age": float(age) if isinstance(age, (int, float)) else "",
            "sex": str(sex).strip() if sex else "",
            "edu": float(edu) if isinstance(edu, (int, float)) else "",
        }
    return out


def discover_subjects(src_root: Path) -> list[dict]:
    """扫描根目录 + rawnii 子目录, 返回受试者列表 (root 优先去重)。"""
    subjects: dict[str, dict] = {}

    def add(tag, name_key, raw_dir, loc, sub_tag):
        if name_key in subjects:
            return
        files = [f.name for f in raw_dir.iterdir() if f.is_file()]
        subjects[name_key] = {
            "tag": tag, "name_key": name_key, "loc": loc,
            "raw_dir": raw_dir, "files": files, "sub_tag": sub_tag,
        }

    if src_root.is_dir():
        for d in sorted(p for p in src_root.iterdir()
                        if p.is_dir() and p.name != "rawnii"):
            raw_p = d / "rawnii"
            if not raw_p.is_dir():
                continue
            tag = d.name
            name_key = _name_key(tag)
            add(tag, name_key, raw_p, "root", tag.split("_", 1)[0] if "_" in tag else "")

    rawnii_root = src_root / "rawnii"
    if rawnii_root.is_dir():
        for d in sorted(p for p in rawnii_root.iterdir() if p.is_dir()):
            tag = d.name
            name_key = _name_key(tag)
            add(tag, name_key, d, "rawnii", tag.split("_", 1)[0] if "_" in tag else "")

    return list(subjects.values())


def _name_key(tag: str) -> str:
    if "_" in tag:
        return tag.split("_", 1)[1].replace("_", " ").strip().upper()
    return tag.upper()
def find_kw(files, kw):
    """按关键词返回第一个命中 .nii.gz 文件名 (或 None)。"""
    hit = [f for f in files if kw in f and f.endswith(".nii.gz")]
    return hit[0] if hit else None


def find_bval(files, kw_4d):
    hit = [f for f in files if kw_4d in f and f.endswith(".bval")]
    return hit[0] if hit else None


def find_bvec(files, kw_4d):
    hit = [f for f in files if kw_4d in f and f.endswith(".bvec")]
    return hit[0] if hit else None


def hlink_or_copy(src: Path, dst: Path, use_hardlink: bool = True) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    if use_hardlink:
        try:
            os.link(src, dst)
            return "hardlink"
        except OSError:
            pass
    shutil.copy2(src, dst)
    return "copy"
# ====================================================================
# 3. 核心转换函数
# ====================================================================
def write_aslcontext(subj_id: str, out_dir: Path):
    """生成 BIDS aslcontext.tsv。
    第1行 header 'volume_type' (bids-matlab layout 依赖此字段名);
    M0 卷用 'm0scan' (xASL_bids_parseM0 硬编码);
    M0 位于第1卷, 之后每对 (label, control) (与机载PW图相关验证)。
    """
    if ASL_FIRST_ISTYPE == "label":
        pair = ("label", "control")
    else:
        pair = ("control", "label")
    lines = ["volume_type", "m0scan"]
    for _ in range(ASL_N_PAIRS):
        lines.extend(pair)
    p = out_dir / f"{subj_id}_aslcontext.tsv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def convert_subject(s: dict, subj_id: str, dry: bool) -> list[str]:
    """为单个受试者执行 BIDS 转换; 返回生成/复制的文件相对路径。"""
    made: list[str] = []
    files = s["files"]
    raw_dir = s["raw_dir"]
    sub_dir = RAW_DIR / subj_id

    # --- anat/T1 ---
    t1_name = find_kw(files, K_T1)
    if t1_name:
        dst = sub_dir / "anat" / f"{subj_id}_T1w.nii.gz"
        if not dry:
            hlink_or_copy(raw_dir / t1_name, dst, USE_HARDLINK)
        made.append(str(dst))

    # --- perf/ASL ---
    asl_name = find_kw(files, K_ASL)
    if asl_name:
        perf_dir = sub_dir / "perf"
        dst = perf_dir / f"{subj_id}_asl.nii.gz"
        if not dry:
            hlink_or_copy(raw_dir / asl_name, dst, USE_HARDLINK)
            j = dict(ASL_JSON_TPL)
            (perf_dir / f"{subj_id}_asl.json").write_text(
                json.dumps(j, indent=2), encoding="utf-8")
            write_aslcontext(subj_id, perf_dir)
        made.append(str(dst))

    # --- dwi: DTI / DKI / IVIM 原始 4D ---
    for kw, acq in ((K_DTI4D, "dti"), (K_DKI4D, "dki"), (K_IVIM4D, "ivim")):
        nii = find_kw(files, kw)
        if not nii:
            continue
        dwi_dir = sub_dir / "dwi"
        bval = find_bval(files, kw)
        bvec = find_bvec(files, kw)
        base = f"{subj_id}_acq-{acq}_dwi"
        if not dry:
            hlink_or_copy(raw_dir / nii, dwi_dir / f"{base}.nii.gz", USE_HARDLINK)
            if bval:
                hlink_or_copy(raw_dir / bval, dwi_dir / f"{base}.bval", USE_HARDLINK)
            if bvec:
                hlink_or_copy(raw_dir / bvec, dwi_dir / f"{base}.bvec", USE_HARDLINK)
        made.append(str(dwi_dir / f"{base}.nii.gz"))

    return made
# ====================================================================
# 4. 主流程
# ====================================================================
def write_participants(rows: list[dict], force: bool):
    out = RAW_DIR / "participants.tsv"
    if out.exists() and not force:
        return out
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("\t".join(PARTICIPANTS_COLS) + "\n")
        for r in rows:
            vals = []
            for c in PARTICIPANTS_COLS:
                v = str(r.get(c, "")).replace("\t", " ").replace("\n", " ").strip()
                # BIDS: missing values MUST be 'n/a'; empty cells also break
                # bids-matlab dsv_read (collapses consecutive delimiters).
                vals.append(v if v != "" else "n/a")
            fh.write("\t".join(vals) + "\n")
    return out


def write_dataset_description(force: bool):
    out = RAW_DIR / "dataset_description.json"
    if out.exists() and not force:
        return out
    out.write_text(json.dumps(DATASET_DESCRIPTION, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    return out


def write_subjects_matrix(part_rows: list[dict]):
    """生成受试者清单: BIDS编号, 来源目录, 队列, 各模态有无。"""
    out = STUDY_ROOT / "subjects_matrix.tsv"
    header = ["participant_id", "group", "source_dir", "sub_tag", "age", "sex",
              "has_dti", "has_dki", "has_ivim"]
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("\t".join(header) + "\n")
        for r in part_rows:
            fh.write("\t".join(str(r.get(c, "")) for c in header) + "\n")
    return out


def main() -> None:
    global DRY_RUN, FORCE_REBUILD, MAX_SUBJECTS
    ap = argparse.ArgumentParser(description="BIDS conversion for SSD/HC data")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--excel", type=Path, default=EXCEL_PATH)
    ap.add_argument("--src", type=Path, default=SRC_ROOT)
    args = ap.parse_args()
    DRY_RUN = DRY_RUN or args.dry_run
    FORCE_REBUILD = FORCE_REBUILD or args.force
    excel_path = args.excel or EXCEL_PATH
    src_root = args.src or SRC_ROOT

    if not excel_path.exists():
        print(f"[错误] 未找到 Excel: {excel_path}")
        sys.exit(1)
    if not src_root.is_dir():
        print(f"[错误] 未找到源目录: {src_root}")
        sys.exit(1)

    print("=" * 60)
    print(f"BIDS 转换 | 源={src_root} | Excel={excel_path}")
    print(f"输出     | {RAW_DIR}" + ("  [DRY-RUN]" if DRY_RUN else ""))
    print("=" * 60)

    excel = xlsx_to_dict(excel_path)
    subjects = discover_subjects(src_root)
    print(f"扫描受试者(含无ASL): {len(subjects)}")

    usable = [s for s in subjects
              if find_kw(s["files"], K_T1) and find_kw(s["files"], K_ASL)]
    print(f"具备 T1+ASL 可用受试者: {len(usable)}")

    if not DRY_RUN:
        RAW_DIR.mkdir(parents=True, exist_ok=True)

    part_rows: list[dict] = []
    stat = {"HC": 0, "SSD": 0, "": 0, "dti": 0, "dki": 0, "ivim": 0}
    for i, s in enumerate(usable):
        if MAX_SUBJECTS and i >= MAX_SUBJECTS:
            break
        rec = excel.get(s["name_key"], {})
        grp = rec.get("group", "")
        subj_id = f"sub-{SUBJ_NUM_FMT.format(n=i + 1)}"
        has_dti = find_kw(s["files"], K_DTI4D) is not None
        has_dki = find_kw(s["files"], K_DKI4D) is not None
        has_ivim = find_kw(s["files"], K_IVIM4D) is not None
        stat[grp] = stat.get(grp, 0) + 1
        stat["dti"] += int(has_dti); stat["dki"] += int(has_dki)
        stat["ivim"] += int(has_ivim)
        part_rows.append({
            "participant_id": subj_id, "group": grp,
            "age": rec.get("age", ""), "sex": rec.get("sex", ""),
            "education_years": rec.get("edu", ""),
            "source_dir": s["tag"], "sub_tag": s["sub_tag"],
            "has_t1": 1, "has_asl": 1,
            "has_dti": int(has_dti), "has_dki": int(has_dki),
            "has_ivim": int(has_ivim), "notes": "",
        })
        if DRY_RUN:
            convert_subject(s, subj_id, True)
        else:
            convert_subject(s, subj_id, False)

    print(f"组别分布: HC={stat.get('HC',0)} SSD={stat.get('SSD',0)} "
          f"未标注={stat.get('',0)}")
    print(f"DWI 覆盖: DTI={stat['dti']} DKI={stat['dki']} IVIM={stat['ivim']}")

    if DRY_RUN:
        print("\n[DRY-RUN] 未写任何文件。")
        return

    write_dataset_description(FORCE_REBUILD)
    write_participants(part_rows, FORCE_REBUILD)
    write_subjects_matrix(part_rows)
    print(f"\n完成: {RAW_DIR}")
    print(f"participants.tsv 行数: {len(part_rows)}")


if __name__ == "__main__":
    main()