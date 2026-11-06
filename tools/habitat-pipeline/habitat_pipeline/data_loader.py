r"""
data_loader.py
==============
多参数患者数据解析模块

功能
----
从 CSV 数据表中解析每位患者的多个序列（Multi-sequence / Multi-phase）
信息，包括图像路径、模态、是否为配准参考序列，返回结构化的患者数据列表，
供主流程（pipeline.py）使用。

与旧版（pre/post 两时间点配对）的区别
--------------------------------------
旧版假设每位患者恰好有 1 条基线 + 1 条随访记录，用于计算 Delta 特征。
本版本面向"单时间点、多序列融合"场景：每位患者可以有任意数量的序列
（如 CT 平扫 + 动脉期 + 门脉期 + 延迟期，或 T2WI + ADC + 增强 MRI），
其中恰好一个序列被指定为"参考序列"（ROI mask 定义于此序列空间），
其余序列在预处理后配准至参考序列空间。

数据表格式（长表：每行 = 一位患者的一个序列）
----------------------------------------------
逗号或制表符分隔（编码：UTF-8-BOM 或 GBK），必须包含以下列：

    patient_id   序列    模态       image_path                  roi_path                    is_reference
    P001    CT_plain      CT     E:\data\P001\plain            (留空)                        0
    P001    CT_arterial   CT     E:\data\P001\arterial          (留空)                        0
    P001    CT_portal     CT     E:\data\P001\portal.nii.gz    E:\data\P001\roi.nii.gz        1
    P002    T2WI          MRI    E:\data\P002\T2.nrrd          E:\data\P002\roi.nrrd          1
    P002    ADC           MRI    E:\data\P002\ADC.nii.gz       (留空)                         0

列说明
------
    patient_id   : str  — 患者唯一标识
    sequence     : str  — 序列/期相名称（自由命名，同一患者内需唯一）
    modality     : str  — "CT" 或 "MRI"（大小写不敏感），决定预处理策略
    image_path   : str  — 图像路径（列名 image_path 或 img_path 均可）：
                             - 目录        → 视为 DICOM 序列文件夹
                             - .nii/.nii.gz → NIfTI 单文件
                             - .nrrd        → NRRD 单文件
    roi_path     : str  — ROI mask 路径（列名 roi_path 或 mask_path 均可），
                          仅参考序列行必填，其余行留空
    is_reference : 0/1  — 可选列。每位患者应恰好 1 行标记为真值
                          （1/true/yes，大小写不敏感）表示配准参考序列。
                          若该列缺失、或取值无法识别为真值（如本流程
                          兼容的历史数据中出现的 'fuzzy' 等非标准取值），
                          在该患者只有 1 个序列、或恰好 1 个序列携带
                          roi_path 时会自动推断参考序列，并记录日志；
                          仍无法唯一确定时才跳过该患者。
    label        : str  — 可选列。患者级标签（如病理分级/分类），若提供
                          会原样保留在返回记录的 'label' 字段中，便于
                          直接用于 pipeline.py 的有监督特征筛选。

解析规则
--------
    1. 路径末尾的中文括号注释自动去除（兼容全角/半角括号）
    2. 按 patient_id 分组，每组必须恰好 1 条 is_reference=1 的记录且含 roi_path
    3. 每位患者至少需要 1 个序列（参考序列可单独构成，无需额外序列）

单独运行示例
------------
    python data_loader.py
    （从 config.DATA_TABLE_PATH 读取，打印解析结果；validate_paths=False）
"""

import re
import os
import logging
import pandas as pd

logger = logging.getLogger(__name__)

VALID_MODALITIES = {"CT", "MRI"}


# ═══════════════════════════════════════════════════════════════════
#  辅助：路径清理
# ═══════════════════════════════════════════════════════════════════

def _clean_path(raw: str) -> str:
    """
    去除路径末尾的中文括号注释，但保留可能是正常文件夹名的一部分的括号（如编号）。

    规则：
    1. 如果原始路径已存在，则不进行任何清理。
    2. 仅清理包含特定中文关键字（前/后/增强）的括号。

    Parameters
    ----------
    raw : str — 含注释的原始路径字符串

    Returns
    -------
    str — 处理后的路径字符串
    """
    raw = raw.strip()
    if os.path.exists(raw):
        return raw

    # 仅移除包含特定临床注释关键字的括号内容
    # 常用关键字：前, 后, 增强, 期
    return re.sub(r'[（(][^）)]*[前后增强期][^）)]*[）)]', '', raw).strip()


def _normalize_modality(raw: str, default: str) -> str:
    """
    规范化模态字符串为 "CT" 或 "MRI"（大小写不敏感，容错常见别名）。

    Parameters
    ----------
    raw     : str — 数据表中原始模态字符串
    default : str — 无法识别时的回退值（来自 config.PREPROC["default_modality"]）

    Returns
    -------
    str — "CT" 或 "MRI"
    """
    if not raw or not raw.strip():
        return default
    token = raw.strip().upper()
    aliases = {
        "CT": "CT", "CTA": "CT",
        "MR": "MRI", "MRI": "MRI", "MRA": "MRI",
    }
    return aliases.get(token, default)


# ═══════════════════════════════════════════════════════════════════
#  辅助：图像格式识别（仅用于日志/诊断，SimpleITK 读取时会自动识别）
# ═══════════════════════════════════════════════════════════════════

def detect_image_format(path: str) -> str:
    """
    识别图像路径对应的格式类别，供日志展示和前置校验使用。

    判断规则
    --------
    - 目录            → 'dicom'（DICOM 序列文件夹，多文件）
    - .nii / .nii.gz  → 'nifti'
    - .nrrd           → 'nrrd'
    - 其他扩展名      → 'unknown'（仍交由 SimpleITK 尝试读取）

    Parameters
    ----------
    path : str

    Returns
    -------
    str — 'dicom' / 'nifti' / 'nrrd' / 'unknown'
    """
    p = str(path).strip()
    if os.path.isdir(p):
        return 'dicom'
    lower = p.lower()
    if lower.endswith('.nii') or lower.endswith('.nii.gz'):
        return 'nifti'
    if lower.endswith('.nrrd'):
        return 'nrrd'
    return 'unknown'


# ═══════════════════════════════════════════════════════════════════
#  主函数：解析并组织患者多序列数据
# ═══════════════════════════════════════════════════════════════════

def load_patient_data(csv_path:       str,
                       validate_paths: bool = True,
                       default_modality: str = "CT") -> list[dict]:
    """
    解析长表格式 CSV，返回患者多序列数据列表。

    输出格式（每个元素）
    --------------------
    {
        'patient_id'          : 'P001',
        'reference_sequence'  : 'CT_portal',
        'roi_path'            : 'E:\\data\\P001\\roi.nii.gz',
        'sequences': {
            'CT_plain':    {'image_path': '...', 'modality': 'CT', 'is_reference': False},
            'CT_arterial': {'image_path': '...', 'modality': 'CT', 'is_reference': False},
            'CT_portal':   {'image_path': '...', 'modality': 'CT', 'is_reference': True},
        }
    }

    解析步骤
    --------
    1. 读取 CSV（自动检测分隔符，兼容 UTF-8-BOM 和 GBK 编码）
    2. 逐行提取 patient_id / sequence / modality / 路径 / is_reference
    3. 按 patient_id 分组，校验恰好 1 条参考序列记录且含 roi_path
    4. 校验路径在磁盘上实际存在（validate_paths=True 时）
    5. 按 patient_id 字符串升序排序

    Parameters
    ----------
    csv_path         : str  — CSV 文件路径
    validate_paths   : bool — 是否检查路径实际存在（默认 True）
    default_modality : str  — modality 列缺失/无法识别时的回退值

    Returns
    -------
    list[dict] — 有效患者数据列表，按 patient_id 升序排列

    Raises
    ------
    FileNotFoundError — csv_path 不存在
    ValueError        — CSV 缺少必要列，或没有可解析的有效行
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"数据表不存在：{csv_path}")

    # ── 读取：自动识别分隔符（,  or \t），兼容两种编码 ────────────
    try:
        df = pd.read_csv(
            csv_path, sep=None, engine='python',
            encoding='utf-8-sig',
            dtype=str, keep_default_na=False,
        )
    except Exception:
        df = pd.read_csv(
            csv_path, sep=None, engine='python',
            encoding='gbk',
            dtype=str, keep_default_na=False,
        )

    df.columns = [c.strip() for c in df.columns]

    # 列名别名兼容：不同来源的数据表可能使用 img_path 而非 image_path
    df = df.rename(columns={'img_path': 'image_path', 'mask_path': 'roi_path'})

    required = {'patient_id', 'sequence', 'modality', 'image_path', 'roi_path'}
    if not required.issubset(df.columns):
        raise ValueError(
            f"CSV 必须包含列：{required}，实际列：{set(df.columns)}"
        )
    # is_reference 列可选：单序列（每位患者仅 1 行）数据集可以不提供该列，
    # 此时该患者的唯一序列自动视为参考序列（见下方推断逻辑）。
    has_is_reference_col = 'is_reference' in df.columns
    # label 列可选：若数据表本身携带患者级标签（如组织学分级/分类），
    # 一并解析出来，便于直接用于 pipeline.py 的有监督特征筛选。
    has_label_col = 'label' in df.columns

    # 过滤空行（patient_id 或 image_path 为空的行无意义）
    df = df[df['patient_id'].str.strip() != '']
    df = df[df['image_path'].str.strip() != '']

    if df.empty:
        raise ValueError("CSV 中没有可解析的有效行，请检查 patient_id / image_path 列。")

    # ── 按患者分组解析 ────────────────────────────────────────────
    patients  = []
    skipped   = []

    for patient_id, group in df.groupby('patient_id'):
        sequences = {}
        roi_by_seq = {}          # 每个序列各自的 roi_path（若有），供参考序列推断使用
        explicit_ref_rows = []   # is_reference 列被明确标记为真值的行
        label_value = None

        for _, row in group.iterrows():
            seq_name = row['sequence'].strip()
            if not seq_name:
                logger.warning(f"患者 {patient_id}：存在空序列名的行，已跳过该行。")
                continue

            modality = _normalize_modality(row['modality'], default_modality)
            if row['modality'].strip() and modality != row['modality'].strip().upper() \
               and row['modality'].strip().upper() not in {"CT", "MRI"}:
                logger.warning(
                    f"患者 {patient_id} | {seq_name}：无法识别模态 "
                    f"'{row['modality']}'，回退为 '{modality}'。"
                )

            # is_reference 容错解析：只认可明确的真值标记（1/true/yes），
            # 其余任意字符串（包括本数据集中出现的 'fuzzy' 等非标准取值）
            # 一律视为"未标记"，交由下方的单序列自动推断逻辑兜底处理，
            # 而非直接报错，以兼容真实世界数据表中的不规范取值。
            is_ref = False
            if has_is_reference_col:
                is_ref = str(row['is_reference']).strip().lower() in {'1', 'true', 'yes', 'y'}

            image_path = _clean_path(row['image_path'])
            roi_path   = _clean_path(row['roi_path']) if row['roi_path'].strip() else None

            if seq_name in sequences:
                logger.warning(
                    f"患者 {patient_id}：序列名 '{seq_name}' 重复，"
                    "后出现的记录将覆盖之前的记录。"
                )

            sequences[seq_name] = {
                'image_path'  : image_path,
                'modality'    : modality,
                'is_reference': is_ref,
            }
            if roi_path:
                roi_by_seq[seq_name] = roi_path
            if is_ref:
                explicit_ref_rows.append((seq_name, roi_path))

            if has_label_col and label_value is None and row['label'].strip():
                label_value = row['label'].strip()

        # ── 确定参考序列：优先使用明确标记，其次按数据形态自动推断 ──
        # 优先级：
        #   1. 恰好 1 条 is_reference 明确标记为真且携带 roi_path → 直接采用
        #   2. 该患者只有 1 个序列且该序列携带 roi_path → 自动推断为参考序列
        #      （覆盖本数据集中 is_reference 取值为 'fuzzy' 等非标准情况）
        #   3. 该患者恰好 1 个序列携带 roi_path（无论 is_reference 如何标记）
        #      → 自动推断为参考序列
        #   否则 → 无法确定，跳过该患者
        reference_sequence, roi_path = None, None

        if len(explicit_ref_rows) == 1:
            reference_sequence, roi_path = explicit_ref_rows[0]
        elif len(explicit_ref_rows) > 1:
            logger.warning(
                f"患者 {patient_id}：is_reference 明确标记数 = {len(explicit_ref_rows)}"
                "（超过 1 条），无法唯一确定参考序列 → 跳过该患者。"
            )
            skipped.append(patient_id)
            continue
        elif len(sequences) == 1 and len(roi_by_seq) == 1:
            reference_sequence, roi_path = next(iter(roi_by_seq.items()))
            logger.info(
                f"患者 {patient_id}：is_reference 未提供有效标记，"
                f"因该患者仅含单一序列 '{reference_sequence}'，已自动推断为参考序列。"
            )
        elif len(roi_by_seq) == 1:
            reference_sequence, roi_path = next(iter(roi_by_seq.items()))
            logger.warning(
                f"患者 {patient_id}：is_reference 标记不明确，但恰好只有序列 "
                f"'{reference_sequence}' 携带 roi_path，已自动推断为参考序列。"
            )
        else:
            logger.warning(
                f"患者 {patient_id}：无法确定参考序列"
                f"（明确标记数={len(explicit_ref_rows)}，携带 roi_path 的序列数={len(roi_by_seq)}）"
                "→ 跳过该患者。"
            )
            skipped.append(patient_id)
            continue

        if not roi_path:
            logger.warning(
                f"患者 {patient_id}：参考序列 '{reference_sequence}' "
                "缺少 roi_path → 跳过该患者。"
            )
            skipped.append(patient_id)
            continue

        # 将实际采用的参考序列标记回 sequences 字典（供下游模块判断）
        sequences[reference_sequence]['is_reference'] = True

        record = {
            'patient_id'         : str(patient_id),
            'reference_sequence' : reference_sequence,
            'roi_path'           : roi_path,
            'sequences'          : sequences,
        }
        if has_label_col:
            record['label'] = label_value

        # ── 路径存在性验证 ────────────────────────────────────────
        if validate_paths:
            missing = []
            if not os.path.exists(roi_path):
                missing.append(f"roi: {roi_path}")
            for seq_name, info in sequences.items():
                if not os.path.exists(info['image_path']):
                    missing.append(f"{seq_name}: {info['image_path']}")
            if missing:
                logger.warning(
                    f"患者 {patient_id} 以下路径不存在，已跳过：\n"
                    + "\n".join(f"  {m}" for m in missing)
                )
                skipped.append(patient_id)
                continue

        patients.append(record)

    if not patients:
        raise ValueError(
            "没有解析到任何有效患者，请检查参考序列标记（is_reference）"
            "及各序列路径是否正确。"
        )

    patients.sort(key=lambda x: x['patient_id'])

    logger.info(
        f"数据表解析完成：{len(patients)} 位有效患者，"
        f"{len(skipped)} 位跳过（ID：{skipped}）"
    )
    return patients


# ═══════════════════════════════════════════════════════════════════
#  辅助：图像诊断（元数据打印）
# ═══════════════════════════════════════════════════════════════════

def diagnose_image(path: str, label: str = ""):
    """
    打印单个图像 / ROI 文件的 SimpleITK 元数据诊断信息。

    输出内容
    --------
    Shape / Spacing / Origin / Direction / Min-Max 像素值 / 数据格式

    Parameters
    ----------
    path  : str — 图像或 ROI 的文件/目录路径
    label : str — 显示标签（如 "P001 CT_arterial"）
    """
    import SimpleITK as sitk

    fmt = detect_image_format(path)
    print(f"\n  {label}  [格式: {fmt}]")
    print(f"    路径：{path}")

    if fmt == 'unknown':
        print("    [警告] 无法识别的文件格式，仍将尝试由 SimpleITK 自动读取。")

    if not os.path.exists(path):
        print("    [错误] 文件/目录不存在")
        return

    try:
        if fmt == 'dicom':
            reader = sitk.ImageSeriesReader()
            files  = reader.GetGDCMSeriesFileNames(path)
            if not files:
                print("    [错误] 目录内未找到 DICOM 文件")
                return
            reader.SetFileNames(files)
            img = reader.Execute()
            print(f"    DICOM 层数：{len(files)}")
        else:
            img = sitk.ReadImage(path)

        print(f"    Shape：    {img.GetSize()}")
        print(f"    Spacing：  {tuple(round(s, 3) for s in img.GetSpacing())}")
        print(f"    Origin：   {tuple(round(o, 2) for o in img.GetOrigin())}")
        arr = sitk.GetArrayFromImage(img)
        print(f"    Min/Max：  {float(arr.min()):.4f} / {float(arr.max()):.4f}")
    except Exception as e:
        print(f"    [错误] 读取失败：{e}")


# ═══════════════════════════════════════════════════════════════════
#  单独运行入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import json
    logging.basicConfig(level=logging.INFO, format='%(levelname)s  %(message)s')

    from config import DATA_TABLE_PATH, PREPROC

    patients = load_patient_data(
        DATA_TABLE_PATH,
        validate_paths=False,
        default_modality=PREPROC["default_modality"],
    )

    print(f"\n共解析到 {len(patients)} 位患者：")
    for p in patients:
        print(json.dumps(p, ensure_ascii=False, indent=2))
        for seq_name, info in p['sequences'].items():
            diagnose_image(info['image_path'], f"{p['patient_id']} | {seq_name}")
        diagnose_image(p['roi_path'], f"{p['patient_id']} | ROI（参考序列：{p['reference_sequence']}）")
