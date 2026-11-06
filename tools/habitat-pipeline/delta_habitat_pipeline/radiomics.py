"""
radiomics.py
============
影像组学特征提取（Pictologics）+ Delta 特征计算 + 特征筛选

流程
----
    1. sitk.Image → Pictologics Image 格式转换（轴顺序 + 空间信息处理）
    2. 在每个 Habitat Mask 内提取 IBSI 合规影像组学特征
    3. 逐 Habitat 计算绝对变化量 / 相对变化量 / 变化率（Delta 特征）
    4. 多患者汇总为队列级特征矩阵（patient × feature）
    5. 三步特征筛选（近零方差 → Pearson 去冗余 → LassoCV）

Bug 修复（相比原版）
--------------------
    [BUG-1] aggregate_delta_features 中 flat_row 字典的 key 使用 col
    而非 f"h{h}_{col}"，导致多 Habitat（K>1）时各 Habitat 的特征相互
    覆盖，最终只保留了最后一个 Habitat 的值。
    修复：key 改为 f"h{h}_{col}"，每个 Habitat-特征组合独立存储。

单独运行示例
------------
    python radiomics.py
    （对第一个患者的 t1/t2 提取特征并计算 Delta；要求预处理和生境划分已完成）
"""

import os
import logging
import numpy as np
import pandas as pd
import SimpleITK as sitk
from pathlib import Path

from pictologics import RadiomicsPipeline
from pictologics.loader import Image as PicImage

from sklearn.feature_selection import SelectFromModel
from sklearn.linear_model import LassoCV
from sklearn.preprocessing import StandardScaler

from config import RADIOMICS, DELTA, FEATURE_SELECTION, HABITAT, DIRS

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  格式转换：SimpleITK → Pictologics
# ═══════════════════════════════════════════════════════════════════

def sitk_to_pictologics(sitk_image: sitk.Image,
                         sitk_mask:  sitk.Image,
                         modality:   str = "CT") -> tuple[PicImage, PicImage]:
    """
    将 SimpleITK 图像和 Mask 转换为 Pictologics Image 对象。

    轴顺序说明
    ----------
    SimpleITK GetArrayFromImage → (Z, Y, X)
    Pictologics Image.array    → 同为 (Z, Y, X)（无需轴转置）
    但 Spacing / Origin 的轴顺序相反：
        SimpleITK → (x, y, z)
        Pictologics → (z, y, x)（需翻转）

    Parameters
    ----------
    sitk_image : sitk.Image — 预处理后的图像（Float32）
    sitk_mask  : sitk.Image — Habitat Mask（UInt8，二值）
    modality   : str        — 图像模态（"CT" / "MR" / "PT"）

    Returns
    -------
    (pic_image, pic_mask) : tuple[PicImage, PicImage]
    """
    img_arr  = sitk.GetArrayFromImage(sitk_image).astype(np.float64)   # (Z,Y,X)
    mask_arr = sitk.GetArrayFromImage(sitk_mask).astype(bool)           # (Z,Y,X)

    # SimpleITK Spacing/Origin 为 (x,y,z)，Pictologics 要求 (z,y,x)
    spacing_zyx = tuple(reversed(sitk_image.GetSpacing()))
    origin_zyx  = tuple(reversed(sitk_image.GetOrigin()))
    direction   = np.array(sitk_image.GetDirection()).reshape(3, 3)

    pic_image = PicImage(
        array     = img_arr,
        spacing   = spacing_zyx,
        origin    = origin_zyx,
        direction = direction,
        modality  = modality,
    )
    # Mask 的 bool 数组需转回 float64 以满足 Pictologics 接口要求
    pic_mask = PicImage(
        array     = mask_arr.astype(np.float64),
        spacing   = spacing_zyx,
        origin    = origin_zyx,
        direction = direction,
        modality  = modality,
    )
    return pic_image, pic_mask


# ═══════════════════════════════════════════════════════════════════
#  单 Habitat 特征提取
# ═══════════════════════════════════════════════════════════════════

def extract_features_single_habitat(pipeline:    RadiomicsPipeline,
                                     sitk_image:  sitk.Image,
                                     sitk_mask:   sitk.Image,
                                     habitat_idx: int,
                                     patient_id:  str,
                                     timepoint:   str) -> pd.Series:
    """
    在单个 Habitat Mask 内提取 Pictologics 影像组学特征。

    配置说明（IBSI 标准化）
    -----------------------
    standard_fbn_32：固定箱数（FBN）离散化，32 箱
        → 对不同灰度范围的图像具有可比性（计数归一化），推荐 MRI
    standard_fbs_16：固定箱宽（FBS）离散化，箱宽 = 值域 / 16
        → 对绝对强度敏感，推荐 HU 标准化后的 CT 图像
    IBSI 建议同时使用两种，二者对不同类别特征的灵敏度互补。

    列名规则
    --------
    h{habitat_idx}_{config_name}_{feature_name}
    例：h0_standard_fbn_32_intensity_mean

    Parameters
    ----------
    pipeline    : RadiomicsPipeline — 预初始化的流水线（重用避免重复加载配置）
    sitk_image  : sitk.Image        — 当前时间点的图像
    sitk_mask   : sitk.Image        — 当前 Habitat 的二值 Mask
    habitat_idx : int               — Habitat 编号（0 到 K-1）
    patient_id  : str
    timepoint   : str               — 'pre' 或 'post'

    Returns
    -------
    pd.Series — 合并所有配置的特征向量（Series.name = 'p{pid}_{tp}_h{h}'）

    Raises
    ------
    ValueError   — Habitat 体素数 < HABITAT["min_voxels"]
    RuntimeError — Pictologics 提取失败（原始异常向上传播）
    """
    # 前置检查：体素数不足时特征统计量不可靠，直接拒绝
    n_voxels = int(sitk.GetArrayFromImage(sitk_mask).sum())
    if n_voxels < HABITAT["min_voxels"]:
        raise ValueError(
            f"患者 {patient_id} | {timepoint} | Habitat {habitat_idx}："
            f"体素数 {n_voxels} < 最低要求 {HABITAT['min_voxels']}。\n"
            "可选解决方案：\n"
            "  1. 检查 Mask 传播结果（配准质量是否达标）\n"
            f"  2. 在 config.HABITAT['min_voxels'] 中降低阈值（当前 {HABITAT['min_voxels']}）"
        )

    # 格式转换
    pic_image, pic_mask = sitk_to_pictologics(sitk_image, sitk_mask)

    # 提取特征（不捕获异常，让失败直接暴露给上层）
    results = pipeline.run(
        image        = pic_image,
        mask         = pic_mask,
        subject_id   = f"p{patient_id}_{timepoint}_h{habitat_idx}",
        config_names = RADIOMICS["config_names"],
    )

    # 合并所有配置的特征，列名加 Habitat 编号和配置名前缀
    combined_parts = []
    for config_name, feature_series in results.items():
        # 前缀格式：h{h}_{config_name}_{feature_name}
        prefixed = feature_series.add_prefix(f"h{habitat_idx}_{config_name}_")
        combined_parts.append(prefixed)

    combined      = pd.concat(combined_parts)
    combined.name = f"p{patient_id}_{timepoint}_h{habitat_idx}"
    return combined


# ═══════════════════════════════════════════════════════════════════
#  所有 Habitat 特征提取（单时间点）
# ═══════════════════════════════════════════════════════════════════

def extract_features_all_habitats(sitk_image:    sitk.Image,
                                   habitat_masks: list[sitk.Image],
                                   patient_id:    str,
                                   timepoint:     str,
                                   output_dir:    str) -> pd.DataFrame:
    """
    对单个时间点的所有 Habitat 提取影像组学特征。

    Parameters
    ----------
    sitk_image    : sitk.Image       — 当前时间点的预处理图像
    habitat_masks : list[sitk.Image] — K 个 Habitat Mask
    patient_id    : str
    timepoint     : str              — 'pre' 或 'post'
    output_dir    : str              — 特征 CSV 保存目录

    Returns
    -------
    pd.DataFrame — 行索引=Habitat 编号（0 到 K-1），列=特征名
                   index.name='habitat'

    中间结果文件
    ------------
    {output_dir}/p{patient_id}_{timepoint}_features.csv
    （仅当 RADIOMICS["save_per_habitat"]=True 时保存）

    Raises
    ------
    ValueError / RuntimeError — 任意 Habitat 特征提取失败时向上传播
    """
    logger.info(
        f"[特征提取] 患者 {patient_id} | {timepoint} | "
        f"{len(habitat_masks)} 个 Habitat"
    )
    os.makedirs(output_dir, exist_ok=True)

    # 预初始化 Pipeline，所有 Habitat 共用同一实例，避免重复加载配置文件
    pipeline = RadiomicsPipeline(load_standard=True)

    all_series = []
    for h, mask in enumerate(habitat_masks):
        logger.info(f"  提取 Habitat {h}（体素数 = {int(sitk.GetArrayFromImage(mask).sum())}）...")
        series = extract_features_single_habitat(
            pipeline, sitk_image, mask, h, patient_id, timepoint
        )
        series.name = h   # 用 Habitat 编号作为 Series.name（成为 DataFrame 行索引）
        all_series.append(series)

    df = pd.DataFrame(all_series)
    df.index.name = 'habitat'

    if RADIOMICS["save_per_habitat"]:
        save_path = os.path.join(
            output_dir, f"p{patient_id}_{timepoint}_features.csv"
        )
        df.to_csv(save_path)
        logger.info(
            f"  特征已保存：{save_path} "
            f"（{df.shape[0]} 行 × {df.shape[1]} 列）"
        )

    return df


# ═══════════════════════════════════════════════════════════════════
#  Delta 特征计算
# ═══════════════════════════════════════════════════════════════════

def compute_delta_features(features_pre:  pd.DataFrame,
                            features_post: pd.DataFrame,
                            patient_id:    str,
                            output_dir:    str,
                            delta_t_days:  float = None) -> pd.DataFrame:
    """
    逐 Habitat、逐特征计算 Delta（前后变化量）。

    计算形式（由 config.DELTA 控制）
    ---------------------------------
    绝对变化量：Δf      = f_post - f_pre
    相对变化量：Δf%     = (f_post - f_pre) / (|f_pre| + ε) × 100
    变化率    ：Δf_rate = (f_post - f_pre) / Δt
                         （仅在提供 delta_t_days 时追加）

    前提
    ----
    features_pre 和 features_post 的 index（Habitat 编号）必须对应相同
    的解剖位置，这由 Mask Propagation 机制（registration.py::propagate_masks）
    保证。若 index 不完全一致，仅计算共有 Habitat 的 Delta。

    数据泄露防护
    ------------
    Z-score 标准化不在此处执行，应在全队列汇总后仅基于训练集统计量完成
    （详见 pipeline.py::run_cohort_aggregation），防止测试集信息泄露至特征缩放。

    Parameters
    ----------
    features_pre  : pd.DataFrame — t1 特征矩阵（index=Habitat 编号）
    features_post : pd.DataFrame — t2 特征矩阵（index=Habitat 编号）
    patient_id    : str
    output_dir    : str
    delta_t_days  : float, optional — 两次扫描的间隔天数（用于计算变化率）

    Returns
    -------
    pd.DataFrame — 行=Habitat，列=Delta 特征名（前缀：delta_abs_ / delta_pct_ / delta_rate_）
                   index.name='habitat'

    Raises
    ------
    ValueError — pre 和 post 特征列名无交集，或 Habitat 索引无交集
    """
    logger.info(f"[Delta 计算] 患者 {patient_id}")
    os.makedirs(output_dir, exist_ok=True)

    # 只计算 pre 和 post 共有的特征列（应完全相同；不同说明配置有变）
    common_cols = features_pre.columns.intersection(features_post.columns)
    if len(common_cols) == 0:
        raise ValueError(
            f"患者 {patient_id}：pre 与 post 特征列名无交集，"
            "请确认两个时间点使用了完全相同的 Pictologics 配置。"
        )

    # 取 pre 和 post 共有的 Habitat 编号（理论上应完全一致）
    common_idx = features_pre.index.intersection(features_post.index)
    if len(common_idx) == 0:
        raise ValueError(
            f"患者 {patient_id}：pre 与 post Habitat 索引无交集，"
            "请检查 Mask Propagation 是否正确执行。"
        )
    if len(common_idx) < len(features_pre.index):
        logger.warning(
            f"  Habitat 索引部分不一致：pre={list(features_pre.index)}，"
            f"post={list(features_post.index)}，仅计算共有：{list(common_idx)}"
        )

    f_pre  = features_pre.loc[common_idx,  common_cols]
    f_post = features_post.loc[common_idx, common_cols]
    eps    = DELTA["epsilon"]

    delta_records = {}
    for h in common_idx:
        v_pre  = f_pre.loc[h].values.astype(np.float64)
        v_post = f_post.loc[h].values.astype(np.float64)
        row    = {}

        if DELTA["compute_absolute"]:
            # Δf = f_post - f_pre
            for name, val in zip(common_cols, v_post - v_pre):
                row[f"delta_abs_{name}"] = float(val)

        if DELTA["compute_relative"]:
            # Δf% = (f_post - f_pre) / (|f_pre| + ε) × 100
            # ε 防止 f_pre ≈ 0 时除以零（极端归一化值可能接近 0）
            delta_pct = (v_post - v_pre) / (np.abs(v_pre) + eps) * 100.0
            for name, val in zip(common_cols, delta_pct):
                row[f"delta_pct_{name}"] = float(val)

        if delta_t_days is not None and delta_t_days > 0:
            # Δf_rate = (f_post - f_pre) / Δt（单位：特征单位/天）
            delta_rate = (v_post - v_pre) / delta_t_days
            for name, val in zip(common_cols, delta_rate):
                row[f"delta_rate_{name}"] = float(val)

        delta_records[h] = row

    delta_df            = pd.DataFrame(delta_records).T
    delta_df.index.name = 'habitat'

    save_path = os.path.join(output_dir, f"p{patient_id}_delta_features.csv")
    delta_df.to_csv(save_path)
    logger.info(
        f"  Delta 特征：{delta_df.shape[0]} Habitat × {delta_df.shape[1]} 列 → {save_path}"
    )
    return delta_df


# ═══════════════════════════════════════════════════════════════════
#  多患者汇总
# ═══════════════════════════════════════════════════════════════════

def aggregate_delta_features(patient_delta_dict: dict[str, pd.DataFrame]
                              ) -> pd.DataFrame:
    """
    将所有患者的 Delta DataFrame 展平为患者级特征矩阵（patient × feature）。

    展平规则
    --------
    每个患者有 K 个 Habitat，每个 Habitat 有 M 个 Delta 特征。
    展平后每个患者占一行，列名格式为 h{H}_{delta_feature_name}。

    例（K=2，特征 delta_abs_f1）：
        Habitat 0 → "h0_delta_abs_f1"
        Habitat 1 → "h1_delta_abs_f1"
    最终矩阵：N 行（患者数）× K×M 列（展平后的全部 Delta 特征）

    [BUG-1 修复]
    -----------
    原版 flat_row 使用 col 作为 key（无 Habitat 前缀），
    导致 K>1 时各 Habitat 的同名特征相互覆盖（最后一个 Habitat 覆盖之前所有）。
    修复：key 改为 f"h{h}_{col}"，保证每个 Habitat-特征组合唯一。

    Parameters
    ----------
    patient_delta_dict : dict[str → pd.DataFrame]
        键：patient_id；值：该患者的 Delta DataFrame（行=Habitat，列=Delta 特征）

    Returns
    -------
    pd.DataFrame — 行=patient_id，列=展平后的 h{H}_{delta_feature} 名称
    """
    patient_rows = {}
    for patient_id, delta_df in patient_delta_dict.items():
        flat_row = {}
        for h in delta_df.index:
            for col in delta_df.columns:
                # [BUG-1 修复] 列名加 Habitat 编号前缀，防止不同 Habitat 的
                # 同名特征相互覆盖
                flat_row[f"h{h}_{col}"] = delta_df.loc[h, col]
        patient_rows[patient_id] = flat_row

    result_df            = pd.DataFrame(patient_rows).T
    result_df.index.name = 'patient_id'
    logger.info(
        f"[汇总] 队列 Delta 矩阵：{result_df.shape[0]} 患者 × {result_df.shape[1]} 特征"
    )
    return result_df


# ═══════════════════════════════════════════════════════════════════
#  特征筛选（有监督，必须提供标签）
# ═══════════════════════════════════════════════════════════════════

def select_features(X: pd.DataFrame,
                    y: pd.Series,
                    output_dir: str) -> tuple[pd.DataFrame, list[str]]:
    """
    三步有监督特征筛选（在训练集上执行，防止数据泄露）。

    Step 1 — 近零方差过滤
    ---------------------
    方差 < FEATURE_SELECTION["near_zero_var_threshold"] 的特征
    在所有样本中几乎无变化，对分类无区分能力，直接删除。
    典型情况：Mask 传播失败导致某 Habitat 特征全为 0。

    Step 2 — Pearson 相关去冗余
    ---------------------------
    计算特征间两两绝对相关系数；对 |r| > threshold 的特征对，
    保留与因变量 y 绝对相关性更强的一个，删除另一个。
    原因：高度相关的特征携带近乎相同的信息，但会膨胀特征维数
    并使后续模型系数不稳定（多重共线性）。

    Step 3 — LassoCV 正则化筛选
    ----------------------------
    通过 5-fold 交叉验证自动选择最优正则化强度 alpha；
    L1 正则化将冗余特征的系数精确压缩至 0（稀疏性）；
    保留系数绝对值 > 1e-8 的特征。
    优势：同时执行特征筛选和模型拟合，避免两步骤不一致。

    前提
    ----
    输入 X 必须已经过 Z-score 标准化（在训练集上拟合，见 pipeline.py），
    因为 Lasso 对特征尺度敏感。

    Parameters
    ----------
    X          : pd.DataFrame — 患者级 Delta 特征矩阵（已标准化，训练集）
    y          : pd.Series    — 因变量标签（如治疗响应：0/1）
    output_dir : str

    Returns
    -------
    X_selected        : pd.DataFrame — 筛选后的特征矩阵（训练集）
    selected_features : list[str]    — 保留的特征名列表

    中间结果文件
    ------------
    {output_dir}/delta_features_selected.csv    — 筛选后特征矩阵
    {output_dir}/selected_feature_names.csv     — 特征名列表
    """
    os.makedirs(output_dir, exist_ok=True)
    logger.info(f"[特征筛选] 初始特征数：{X.shape[1]}")

    X_work = X.copy().fillna(0)   # NaN 填 0（通常因 Habitat 体素数不足导致）

    # ── Step 1：近零方差过滤 ──────────────────────────────────────
    variances = X_work.var(axis=0)
    nzv_mask  = variances > FEATURE_SELECTION["near_zero_var_threshold"]
    X_work    = X_work.loc[:, nzv_mask]
    logger.info(
        f"  Step 1（近零方差过滤，阈值 < {FEATURE_SELECTION['near_zero_var_threshold']}）："
        f"剩余 {X_work.shape[1]} 特征"
    )

    # ── Step 2：Pearson 相关去冗余 ───────────────────────────────
    # 只取上三角矩阵（避免重复比较同一对特征）
    corr_matrix = X_work.corr(method='pearson').abs()
    upper_tri   = corr_matrix.where(
        np.triu(np.ones(corr_matrix.shape), k=1).astype(bool)
    )
    # 计算每个特征与标签的绝对相关系数（用于决定保留哪个）
    corr_with_y = X_work.corrwith(y).abs()
    to_drop     = set()
    for col in upper_tri.columns:
        # 找到与 col 高度相关的其他特征
        high_corr = upper_tri.index[
            upper_tri[col] > FEATURE_SELECTION["correlation_threshold"]
        ].tolist()
        for other in high_corr:
            # 保留与 y 相关性更强的特征，删除较弱的
            if corr_with_y.get(col, 0) >= corr_with_y.get(other, 0):
                to_drop.add(other)
            else:
                to_drop.add(col)
    X_work = X_work.drop(columns=list(to_drop))
    logger.info(
        f"  Step 2（Pearson 去冗余，|r| > {FEATURE_SELECTION['correlation_threshold']}）："
        f"剩余 {X_work.shape[1]} 特征（删除 {len(to_drop)} 个冗余特征）"
    )

    # ── Step 3：LassoCV 正则化筛选 ───────────────────────────────
    # cv=5：5 折交叉验证自动选最优 alpha
    # max_iter=10000：确保在高维稀疏场景下收敛
    # n_alphas=50：alpha 搜索网格密度
    lasso = LassoCV(cv=5, max_iter=10000, n_alphas=50, random_state=42)
    lasso.fit(X_work.values, y.values)
    # threshold=1e-8：系数绝对值超过此值才视为"选中"
    selector      = SelectFromModel(lasso, prefit=True, threshold=1e-8)
    lasso_mask    = selector.get_support()
    X_selected    = X_work.loc[:, lasso_mask]

    selected_features = X_selected.columns.tolist()
    logger.info(
        f"  Step 3（LassoCV，最优 alpha={lasso.alpha_:.4f}）："
        f"剩余 {len(selected_features)} 特征"
    )

    # 保存结果
    X_selected.to_csv(os.path.join(output_dir, "delta_features_selected.csv"))
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

    from config import DATA_TABLE_PATH, DIRS
    from data_loader import load_patient_pairs

    pairs = load_patient_pairs(DATA_TABLE_PATH, validate_paths=True)
    if not pairs:
        print("没有可用患者，请检查数据表。")
        exit(1)

    pair = pairs[0]
    pid  = pair['patient_id']

    # 从已完成的预处理和生境划分目录加载结果
    preproc_dir = os.path.join(DIRS["preprocessed"],  f"patient_{pid}")
    habitat_dir = os.path.join(DIRS["habitat_masks"], f"patient_{pid}")
    feat_dir    = os.path.join(DIRS["features"],      f"patient_{pid}")

    img_t1 = sitk.ReadImage(os.path.join(preproc_dir, f"p{pid}_pre_image.nii.gz"))
    img_t2 = sitk.ReadImage(os.path.join(preproc_dir, f"p{pid}_post_image.nii.gz"))

    # 加载 Baseline Habitat Masks（t1 空间）
    baseline_files = sorted([
        f for f in os.listdir(habitat_dir)
        if f"p{pid}_habitat" in f and "_t1_baseline" in f
    ])
    masks_t1 = [sitk.ReadImage(os.path.join(habitat_dir, f)) for f in baseline_files]

    # 加载已传播的 Habitat Masks（t2 空间）
    prop_files = sorted([
        f for f in os.listdir(habitat_dir)
        if f"p{pid}_habitat" in f and "_t2_propagated" in f
    ])
    masks_t2 = [sitk.ReadImage(os.path.join(habitat_dir, f)) for f in prop_files]

    feat_t1 = extract_features_all_habitats(img_t1, masks_t1, pid, 'pre',  feat_dir)
    feat_t2 = extract_features_all_habitats(img_t2, masks_t2, pid, 'post', feat_dir)

    delta_dir = os.path.join(DIRS["delta"], f"patient_{pid}")
    delta_df  = compute_delta_features(feat_t1, feat_t2, pid, delta_dir)

    print(f"\n患者 {pid} Delta 特征矩阵形状：{delta_df.shape}")
    print(delta_df.head())
