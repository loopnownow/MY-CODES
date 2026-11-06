"""
radiomics.py
============
影像组学特征提取（Pictologics）—— 多参数融合版（逐 Habitat × 逐序列）

流程
----
    1. sitk.Image → Pictologics Image 格式转换（轴顺序 + 空间信息处理）
    2. 对每个 Habitat Mask、每个已配准对齐的序列，独立提取 IBSI 合规
       影像组学特征（不同序列可能来自不同模态，modality 逐序列传入）
    3. 单患者内汇总为一张 DataFrame（行=Habitat，列=序列×配置×特征名）
    4. 多患者汇总为队列级特征矩阵（patient × feature）
    5. 三步特征筛选（近零方差 → Pearson 去冗余 → LassoCV，需要标签）

与旧版的区别
------------
旧版计算 pre/post 两时间点的 Delta（差值/变化率）特征。本版本面向
单时间点多序列融合场景，直接对每个 Habitat 在每个序列上提取原始
影像组学特征，不再有"变化量"的概念；队列级特征矩阵直接由各患者
的 (Habitat × 序列) 特征展平得到。

单独运行示例
------------
    python radiomics.py
    （对第一个患者提取全部 Habitat × 全部序列特征；要求预处理、配准、
     生境划分均已完成）
"""

import os
import logging
import numpy as np
import pandas as pd
import SimpleITK as sitk

from pictologics import RadiomicsPipeline
from pictologics.loader import Image as PicImage

from sklearn.feature_selection import SelectFromModel
from sklearn.linear_model import LassoCV

from config import RADIOMICS, HABITAT, FEATURE_SELECTION

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  格式转换：SimpleITK → Pictologics
# ═══════════════════════════════════════════════════════════════════

def sitk_to_pictologics(sitk_image: sitk.Image,
                         sitk_mask:  sitk.Image,
                         modality:   str = "CT") -> tuple:
    """
    将 SimpleITK 图像和 Mask 转换为 Pictologics Image 对象。

    轴顺序说明
    ----------
    SimpleITK GetArrayFromImage → (Z,Y,X)；Pictologics Image.array 同样为
    (Z,Y,X)（无需轴转置），但 Spacing/Origin 轴顺序相反，需要翻转。

    Parameters
    ----------
    sitk_image : sitk.Image — 预处理并配准对齐后的图像
    sitk_mask  : sitk.Image — Habitat Mask（UInt8，二值）
    modality   : str        — 图像模态（"CT" / "MR" / "PT"）

    Returns
    -------
    (pic_image, pic_mask) : tuple[PicImage, PicImage]
    """
    img_arr  = sitk.GetArrayFromImage(sitk_image).astype(np.float64)
    mask_arr = sitk.GetArrayFromImage(sitk_mask).astype(bool)

    spacing_zyx = tuple(reversed(sitk_image.GetSpacing()))
    origin_zyx  = tuple(reversed(sitk_image.GetOrigin()))
    direction   = np.array(sitk_image.GetDirection()).reshape(3, 3)

    # Pictologics 的 modality 字段期望 "MR" 而非 "MRI"
    pic_modality = "MR" if modality.upper() in ("MRI", "MR") else modality.upper()

    pic_image = PicImage(array=img_arr, spacing=spacing_zyx, origin=origin_zyx,
                          direction=direction, modality=pic_modality)
    pic_mask  = PicImage(array=mask_arr.astype(np.float64), spacing=spacing_zyx,
                          origin=origin_zyx, direction=direction, modality=pic_modality)
    return pic_image, pic_mask


# ═══════════════════════════════════════════════════════════════════
#  单 Habitat × 单序列 特征提取
# ═══════════════════════════════════════════════════════════════════

def extract_features_single_habitat_sequence(pipeline:    RadiomicsPipeline,
                                               sitk_image:  sitk.Image,
                                               sitk_mask:   sitk.Image,
                                               habitat_idx: int,
                                               sequence:    str,
                                               modality:    str,
                                               patient_id:  str) -> pd.Series:
    """
    在单个 Habitat Mask 内、单个序列上提取 Pictologics 影像组学特征。

    列名规则
    --------
    # [MODIFIED 2026-07-09] 列名不再包含 habitat_idx 前缀，改为：
    # {sequence}_{config_name}_{feature_name}
    # 例：CT_arterial_standard_fbn_32_intensity_mean
    # 原因：若每个 Habitat 的列名都带 h{habitat_idx}_ 前缀，会导致同一患者内
    # 各 Habitat 行的列名互不相同，extract_features_all_habitats() 中
    # pd.DataFrame(all_series) 按列名并集对齐后，每行只在自己前缀的列有值，
    # 其余全部被填充为 NaN（行内出现大片空白）。去掉该前缀后，同一患者的
    # 所有 Habitat 行共享统一的列结构；habitat 维度改由 aggregate_cohort_features()
    # 中的 h{h}_ 前缀统一添加（仅加一次，不再重复）。


    Parameters
    ----------
    pipeline    : RadiomicsPipeline — 预初始化的流水线（跨 Habitat/序列复用）
    sitk_image  : sitk.Image        — 该序列的配准对齐后图像
    sitk_mask   : sitk.Image        — 当前 Habitat 的二值 Mask
    habitat_idx : int               — Habitat 编号（0 到 K-1）
    sequence    : str               — 序列名
    modality    : str               — 该序列的模态（"CT"/"MRI"）
    patient_id  : str

    Returns
    -------
    pd.Series — 合并所有 config 的特征向量

    Raises
    ------
    ValueError   — Habitat 体素数 < HABITAT["min_voxels"]
    RuntimeError — Pictologics 提取失败（原始异常向上传播）
    """
    n_voxels = int(sitk.GetArrayFromImage(sitk_mask).sum())
    if n_voxels < HABITAT["min_voxels"]:
        raise ValueError(
            f"患者 {patient_id} | {sequence} | Habitat {habitat_idx}："
            f"体素数 {n_voxels} < 最低要求 {HABITAT['min_voxels']}。\n"
            "可选解决方案：\n"
            "  1. 检查配准质量（QC 报告）\n"
            f"  2. 在 config.HABITAT['min_voxels'] 中降低阈值（当前 {HABITAT['min_voxels']}）"
        )

    pic_image, pic_mask = sitk_to_pictologics(sitk_image, sitk_mask, modality)

    results = pipeline.run(
        image        = pic_image,
        mask         = pic_mask,
        subject_id   = f"{patient_id}_{sequence}_h{habitat_idx}",
        config_names = RADIOMICS["config_names"],
    )

    combined_parts = []
    for config_name, feature_series in results.items():
        # [MODIFIED 2026-07-09] 去掉 h{habitat_idx}_ 前缀，避免同一患者内各 Habitat
        # 行列名互斥导致 pd.DataFrame 并集对齐产生大片 NaN（habitat 维度前缀改由
        # aggregate_cohort_features() 统一添加，仅加一次）
        prefixed = feature_series.add_prefix(f"{sequence}_{config_name}_")
        combined_parts.append(prefixed)


    combined      = pd.concat(combined_parts)
    combined.name = f"{patient_id}_{sequence}_h{habitat_idx}"
    return combined


# ═══════════════════════════════════════════════════════════════════
#  所有 Habitat × 所有序列 特征提取（单患者）
# ═══════════════════════════════════════════════════════════════════

def extract_features_all_habitats(sequence_images: dict,
                                   sequence_modalities: dict,
                                   habitat_masks: list,
                                   patient_id:    str,
                                   output_dir:    str) -> pd.DataFrame:
    """
    对单个患者的所有 Habitat、所有序列组合提取影像组学特征。

    Parameters
    ----------
    sequence_images     : dict[str, sitk.Image] — {序列名: 配准对齐后图像}
    sequence_modalities : dict[str, str]        — {序列名: 模态}
    habitat_masks        : list[sitk.Image]      — K 个 Habitat Mask
    patient_id            : str
    output_dir            : str — 特征 CSV 保存目录

    Returns
    -------
    pd.DataFrame — 行索引=Habitat 编号（0 到 K-1），
                   列=各序列×配置×特征名（index.name='habitat'）

    中间结果文件
    ------------
    {output_dir}/{patient_id}_features.csv
    （仅当 RADIOMICS["save_per_habitat"]=True 时保存）

    Raises
    ------
    ValueError / RuntimeError — 全部 Habitat 均体素不足，或非"体素不足"原因的
    特征提取失败时向上传播

    # [MODIFIED 2026-07-09] 若某 Habitat 体素数 < HABITAT["min_voxels"]，跳过
    # 该 Habitat 的特征提取（对应行填 NaN，即"留白"），仅记录 WARNING 日志，
    # 不再中断整个患者的处理；只有当所有 Habitat 都体素不足时才抛出异常。
    """
    seq_names = list(sequence_images.keys())
    target_sequences = seq_names if RADIOMICS["sequences"] == "all" else [
        s for s in RADIOMICS["sequences"] if s in seq_names
    ]
    logger.info(
        f"[特征提取] 患者 {patient_id} | {len(habitat_masks)} 个 Habitat × "
        f"{len(target_sequences)} 个序列（{target_sequences}）"
    )
    os.makedirs(output_dir, exist_ok=True)

    pipeline = RadiomicsPipeline(load_standard=True)

    all_series   = []
    skipped_rows = []  # [MODIFIED] 记录体素不足被跳过的 Habitat 索引，稍后补 NaN 行
    for h, mask in enumerate(habitat_masks):
        n_vox = int(sitk.GetArrayFromImage(mask).sum())
        logger.info(f"  Habitat {h}（体素数 = {n_vox}）")

        # [MODIFIED] 体素数不足：跳过该 Habitat（留白），仅日志记录，不抛异常
        if n_vox < HABITAT["min_voxels"]:
            logger.warning(
                f"  Habitat {h} 体素数 {n_vox} < 最低要求 {HABITAT['min_voxels']}，"
                f"跳过该生境的特征提取（结果留白 NaN），保留其余 Habitat 结果。"
            )
            skipped_rows.append(h)
            continue

        habitat_parts = []
        for seq in target_sequences:
            logger.info(f"    提取序列 {seq}...")
            series = extract_features_single_habitat_sequence(
                pipeline, sequence_images[seq], mask, h, seq,
                sequence_modalities[seq], patient_id,
            )
            habitat_parts.append(series)
        habitat_row = pd.concat(habitat_parts)
        habitat_row.name = h
        all_series.append(habitat_row)

    # [MODIFIED] 若所有 Habitat 均被跳过，该患者才真正判定为失败
    if not all_series:
        raise ValueError(
            f"患者 {patient_id}：全部 {len(habitat_masks)} 个 Habitat 体素数均 "
            f"< 最低要求 {HABITAT['min_voxels']}，无法提取任何特征。"
        )

    df = pd.DataFrame(all_series)

    # [MODIFIED] 为被跳过的 Habitat 补 NaN 行，保持行索引 0..K-1 完整、列结构一致
    for h in skipped_rows:
        df.loc[h] = np.nan
    df = df.sort_index()

    df.index.name = 'habitat'

    if RADIOMICS["save_per_habitat"]:
        save_path = os.path.join(output_dir, f"{patient_id}_features.csv")
        df.to_csv(save_path)
        logger.info(f"  特征已保存：{save_path}（{df.shape[0]} 行 × {df.shape[1]} 列）")

    return df


# ═══════════════════════════════════════════════════════════════════
#  多患者汇总
# ═══════════════════════════════════════════════════════════════════

def aggregate_cohort_features(patient_feature_dict: dict) -> pd.DataFrame:
    """
    将所有患者的 (Habitat × 序列特征) DataFrame 展平为患者级特征矩阵。

    展平规则
    --------
    每个患者有 K 个 Habitat，每个 Habitat 有 M 个特征列（已含序列前缀）。
    展平后每个患者占一行，列名格式为 h{H}_{原列名}。

    Parameters
    ----------
    patient_feature_dict : dict[str → pd.DataFrame]
        键：patient_id；值：extract_features_all_habitats() 的返回值
        （行=Habitat，列=特征）

    Returns
    -------
    pd.DataFrame — 行=patient_id，列=展平后的 h{H}_{特征名}
    """
    patient_rows = {}
    for patient_id, feat_df in patient_feature_dict.items():
        flat_row = {}
        for h in feat_df.index:
            for col in feat_df.columns:
                flat_row[f"h{h}_{col}"] = feat_df.loc[h, col]
        patient_rows[patient_id] = flat_row

    result_df = pd.DataFrame(patient_rows).T
    result_df.index.name = 'patient_id'
    logger.info(f"[汇总] 队列特征矩阵：{result_df.shape[0]} 患者 × {result_df.shape[1]} 特征")
    return result_df


# ═══════════════════════════════════════════════════════════════════
#  特征筛选（有监督，必须提供标签）
# ═══════════════════════════════════════════════════════════════════

def select_features(X: pd.DataFrame,
                    y: pd.Series,
                    output_dir: str) -> tuple:
    """
    三步有监督特征筛选（在训练集上执行，防止数据泄露）。

    Step 1 — 近零方差过滤：方差 < 阈值的特征直接删除
             （典型情况：某 Habitat 因体素不足或配准失败导致特征恒定/为 0）
    Step 2 — Pearson 相关去冗余：|r| > 阈值的特征对，保留与 y 相关性更强的一个
    Step 3 — LassoCV 正则化筛选：5 折交叉验证自动选择最优 alpha，
             保留系数绝对值 > 1e-8 的特征

    前提：输入 X 应已做标准化（Lasso 对特征尺度敏感）。

    Parameters
    ----------
    X          : pd.DataFrame — 患者级特征矩阵（训练集）
    y          : pd.Series    — 因变量标签
    output_dir : str

    Returns
    -------
    X_selected        : pd.DataFrame
    selected_features : list[str]
    """
    os.makedirs(output_dir, exist_ok=True)
    logger.info(f"[特征筛选] 初始特征数：{X.shape[1]}")

    X_work = X.copy().fillna(0)

    variances = X_work.var(axis=0)
    nzv_mask  = variances > FEATURE_SELECTION["near_zero_var_threshold"]
    X_work    = X_work.loc[:, nzv_mask]
    logger.info(
        f"  Step 1（近零方差过滤，阈值 < {FEATURE_SELECTION['near_zero_var_threshold']}）："
        f"剩余 {X_work.shape[1]} 特征"
    )

    corr_matrix = X_work.corr(method='pearson').abs()
    upper_tri   = corr_matrix.where(np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))
    corr_with_y = X_work.corrwith(y).abs()
    to_drop     = set()
    for col in upper_tri.columns:
        high_corr = upper_tri.index[
            upper_tri[col] > FEATURE_SELECTION["correlation_threshold"]
        ].tolist()
        for other in high_corr:
            if corr_with_y.get(col, 0) >= corr_with_y.get(other, 0):
                to_drop.add(other)
            else:
                to_drop.add(col)
    X_work = X_work.drop(columns=list(to_drop))
    logger.info(
        f"  Step 2（Pearson 去冗余，|r| > {FEATURE_SELECTION['correlation_threshold']}）："
        f"剩余 {X_work.shape[1]} 特征（删除 {len(to_drop)} 个冗余特征）"
    )

    lasso = LassoCV(cv=5, max_iter=10000, n_alphas=50, random_state=42)
    lasso.fit(X_work.values, y.values)
    selector   = SelectFromModel(lasso, prefit=True, threshold=1e-8)
    lasso_mask = selector.get_support()
    X_selected = X_work.loc[:, lasso_mask]

    selected_features = X_selected.columns.tolist()
    logger.info(
        f"  Step 3（LassoCV，最优 alpha={lasso.alpha_:.4f}）："
        f"剩余 {len(selected_features)} 特征"
    )

    X_selected.to_csv(os.path.join(output_dir, "cohort_features_selected.csv"))
    pd.Series(selected_features).to_csv(
        os.path.join(output_dir, "selected_feature_names.csv"), index=False
    )
    logger.info(f"  筛选结果已保存至：{output_dir}")
    return X_selected, selected_features


# ═══════════════════════════════════════════════════════════════════
#  单独运行入口（调试用）
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s  %(message)s')

    from config import DATA_TABLE_PATH, DIRS, PREPROC
    from data_loader import load_patient_data

    patients = load_patient_data(DATA_TABLE_PATH, validate_paths=True,
                                  default_modality=PREPROC["default_modality"])
    if not patients:
        print("没有可用患者，请检查数据表。")
        exit(1)

    patient = patients[0]
    pid     = patient['patient_id']

    reg_dir      = os.path.join(DIRS["registration"], pid)
    preproc_dir  = os.path.join(DIRS["preprocessed"], pid)
    habitat_dir  = os.path.join(DIRS["habitat_masks"], pid)
    feat_dir     = os.path.join(DIRS["features"], pid)

    ref_seq = patient['reference_sequence']
    seq_images    = {}
    seq_modality  = {}
    for seq_name, info in patient['sequences'].items():
        if seq_name == ref_seq:
            path = os.path.join(preproc_dir, f"{pid}_{seq_name}_image.nii.gz")
        else:
            path = os.path.join(reg_dir, f"{pid}_{seq_name}_registered.nii.gz")
        seq_images[seq_name]   = sitk.ReadImage(path)
        seq_modality[seq_name] = info['modality']

    habitat_files = sorted([
        f for f in os.listdir(habitat_dir) if f.startswith(f"{pid}_habitat")
    ])
    masks = [sitk.ReadImage(os.path.join(habitat_dir, f)) for f in habitat_files]

    feat_df = extract_features_all_habitats(seq_images, seq_modality, masks, pid, feat_dir)
    print(f"\n患者 {pid} 特征矩阵形状：{feat_df.shape}")
    print(feat_df.head())
