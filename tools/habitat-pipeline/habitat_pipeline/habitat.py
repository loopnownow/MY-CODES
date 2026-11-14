"""
habitat.py
==========
生境划分模块 —— 多参数融合版（Multi-sequence Fusion Habitat Imaging）

核心概念
--------
"生境"（Habitat）指 ROI 内部具有相似局部影像学表型的体素集合。
本版本的关键变化：聚类输入特征不再局限于单一序列，而是融合同一患者
全部已配准对齐序列的四类信息：
    1. Intensity      — 各序列原始强度
    2. Parametric map — 由 config.PARAMETRIC_MAPS 派生的定量/半定量图
                        （如强化率、ADC 图等）
    3. Local texture  — 滑窗局部均值 / 方差 / 熵
    4. Gradient       — 一阶梯度幅值（Sobel），刻画边界/形变强度
通过在体素级多通道融合特征空间中执行 K-means 聚类（或先做 Superpixel
超体素预分割再聚类），将 ROI 内每个体素（或每个超体素）分配到某一
生境，从而捕获病灶内部的多参数异质性。

全队列一致建模策略
------------------
问题：若每位患者独立聚类，同一编号的 Habitat 在不同患者中可能代表
      不同的组织表型（聚类结果具有置换不变性）。
解决：汇总全队列所有患者的融合特征，拟合统一的全局模型
     （Scaler → PCA → KMeans），再对每位患者应用该模型，保证 Habitat
     编号在全队列中语义一致，便于组间比较统计分析。

Superpixel（SLIC 超体素）可选方案
----------------------------------
原理：先用 SLIC 算法将 ROI 内体素聚合为空间连续、强度均质的"超体素"，
      对每个超体素取融合特征均值，再对超体素（而非原始体素）执行聚类。
优点：显著降低样本量、抑制孤立噪声体素、生境边界更贴合解剖连续性。
开关：config.HABITAT["use_superpixel"]

模块职责
--------
    compute_voxel_feature_maps  — 提取单患者体素级多序列融合特征
    compute_superpixel_segmentation / aggregate_by_superpixel — Superpixel 方案
    CohortHabitatModel          — 封装全局模型（Scaler/PCA/KMeans）
    optimize_k                  — 自动确定最优聚类数 K
    build_habitat_masks         — 根据聚类标签生成 3D Mask 文件
    build_habitats              — 统一接口（全队列模式 / 单患者模式，体素/超体素自动切换）
"""

import os
import logging
import numpy as np
import SimpleITK as sitk
import joblib
from scipy import ndimage
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score, adjusted_rand_score

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from config import VOXEL_FEATURES, HABITAT, PARAMETRIC_MAPS, PARAMETRIC_MAP_EPSILON

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  辅助：序列选择解析（"all" 或显式列表）
# ═══════════════════════════════════════════════════════════════════

def _select_sequences(spec, available: list) -> list:
    """
    将配置项（"all" 或序列名列表）解析为实际参与计算的序列名列表，
    并过滤掉当前患者数据中不存在的序列（记录警告，不中断流程）。

    Parameters
    ----------
    spec      : "all" | list[str] — 来自 config 的序列选择配置
    available : list[str]         — 当前患者实际可用的序列名列表

    Returns
    -------
    list[str] — 有效的序列名列表（保持 available 中的顺序，确保跨
                患者通道顺序一致，这对全队列一致建模至关重要）
    """
    if spec == "all":
        return list(available)
    missing = [s for s in spec if s not in available]
    if missing:
        logger.warning(f"以下序列在当前患者数据中不存在，已跳过：{missing}")
    return [s for s in available if s in spec]


# ═══════════════════════════════════════════════════════════════════
#  1. 参数图（Parametric map）派生
# ═══════════════════════════════════════════════════════════════════

def compute_parametric_maps(sequence_arrays: dict) -> dict:
    """
    根据 config.PARAMETRIC_MAPS 规则，由已加载的多序列强度数组派生
    参数图（定量/半定量图）。

    支持规则类型（见 config.py 顶部说明）
    --------------------------------------
    "direct"           — 直接引用某序列（如 ADC 本身即为参数图）
    "absolute_change"  — seq_b - seq_a
    "relative_change"  — (seq_b - seq_a) / (|seq_a| + epsilon)
    "ratio"            — seq_b / (seq_a + epsilon)

    容错处理
    --------
    所需序列缺失时跳过该条规则并记录警告，不中断流程（不同患者的
    可用序列组合可能不同，例如并非所有患者都做了全部期相扫描）。

    Parameters
    ----------
    sequence_arrays : dict[str, np.ndarray] — {序列名: (Z,Y,X) 强度数组}

    Returns
    -------
    dict[str, np.ndarray] — {参数图名称: (Z,Y,X) 数组}
    """
    maps = {}
    eps = PARAMETRIC_MAP_EPSILON

    for rule in PARAMETRIC_MAPS:
        name  = rule["name"]
        rtype = rule["type"]

        try:
            if rtype == "direct":
                maps[name] = sequence_arrays[rule["seq"]].copy()

            elif rtype == "absolute_change":
                a, b = sequence_arrays[rule["seq_a"]], sequence_arrays[rule["seq_b"]]
                maps[name] = b - a

            elif rtype == "relative_change":
                a, b = sequence_arrays[rule["seq_a"]], sequence_arrays[rule["seq_b"]]
                maps[name] = (b - a) / (np.abs(a) + eps)

            elif rtype == "ratio":
                a, b = sequence_arrays[rule["seq_a"]], sequence_arrays[rule["seq_b"]]
                maps[name] = b / (a + eps)

            else:
                logger.warning(f"未知参数图类型 '{rtype}'（规则：{name}），已跳过。")

        except KeyError as e:
            logger.warning(f"参数图规则 '{name}' 所需序列 {e} 不存在，已跳过。")

    return maps


# ═══════════════════════════════════════════════════════════════════
#  2. 体素级多序列融合特征提取
# ═══════════════════════════════════════════════════════════════════

def compute_voxel_feature_maps(sequence_images: dict,
                                mask: sitk.Image
                                ) -> tuple:
    """
    在 ROI mask 内提取每个体素的多序列融合特征向量。

    特征通道组成（各自可通过 config.VOXEL_FEATURES 独立开关）
    ------------------------------------------------------------
    1. Intensity      — 每个选定序列的原始强度（1 通道/序列）
    2. Parametric map — compute_parametric_maps() 派生的每张参数图（1 通道/图）
    3. Local texture  — 每个选定序列的局部均值/方差/熵（3 通道/序列）
    4. Gradient       — 每个选定序列的 Sobel 梯度幅值（1 通道/序列）
    5.（可选）LoG 多尺度响应、三维小波子带（默认关闭，见 config）

    通道顺序在同一 config 下对所有患者严格一致（依赖 _select_sequences
    保持顺序稳定），这是全队列一致建模（CohortHabitatModel）的前提。

    Parameters
    ----------
    sequence_images : dict[str, sitk.Image] — {序列名: 已配准对齐的图像}，
                       所有图像必须与 mask 处于同一物理空间（相同 Size）
    mask            : sitk.Image — ROI mask（UInt8，二值）

    Returns
    -------
    voxel_features : np.ndarray, shape (N, C) — ROI 内 N 个体素的 C 维特征
    mask_arr       : np.ndarray, shape (Z,Y,X), bool — boolean mask 数组
    feature_names  : list[str] — 每个通道的可读名称（长度 = C，便于调试/复核）
    """
    mask_arr = sitk.GetArrayFromImage(mask).astype(bool)
    seq_names = list(sequence_images.keys())

    # 尺寸一致性前置检查：配准失败或未对齐时应及早暴露，而非静默产生错误特征
    for name, img in sequence_images.items():
        if img.GetSize() != mask.GetSize():
            raise RuntimeError(
                f"序列 '{name}' 尺寸 {img.GetSize()} 与 mask 尺寸 "
                f"{mask.GetSize()} 不一致，请检查配准结果。"
            )

    sequence_arrays = {
        name: sitk.GetArrayFromImage(img).astype(np.float32)
        for name, img in sequence_images.items()
    }
    spacing_map = {name: img.GetSpacing() for name, img in sequence_images.items()}

    win = VOXEL_FEATURES["window_radius"] * 2 + 1
    channels, feature_names = [], []

    # ── 1. Intensity ─────────────────────────────────────────────
    if VOXEL_FEATURES["use_intensity"]:
        for seq in _select_sequences(VOXEL_FEATURES["intensity_sequences"], seq_names):
            channels.append(sequence_arrays[seq].copy())
            feature_names.append(f"intensity_{seq}")

    # ── 2. Parametric map ────────────────────────────────────────
    if VOXEL_FEATURES["use_parametric_map"]:
        param_maps = compute_parametric_maps(sequence_arrays)
        for name, arr in param_maps.items():
            channels.append(arr.astype(np.float32))
            feature_names.append(f"param_{name}")

    # ── 3. Local texture（均值 / 方差 / 熵）─────────────────────
    if VOXEL_FEATURES["use_local_texture"]:
        n_bins = VOXEL_FEATURES["texture_entropy_bins"]
        for seq in _select_sequences(VOXEL_FEATURES["texture_sequences"], seq_names):
            arr = sequence_arrays[seq]

            local_mean = ndimage.uniform_filter(arr, size=win)
            channels.append(local_mean)
            feature_names.append(f"texture_mean_{seq}")

            local_sq_mean = ndimage.uniform_filter(arr ** 2, size=win)
            local_var = np.maximum(local_sq_mean - local_mean ** 2, 0)
            channels.append(local_var)
            feature_names.append(f"texture_var_{seq}")

            arr_min, arr_max = float(arr.min()), float(arr.max())
            disc = np.floor(
                (arr - arr_min) / (arr_max - arr_min + 1e-8) * (n_bins - 1)
            ).astype(np.int32)
            entropy = np.zeros_like(arr, dtype=np.float32)
            for b in range(n_bins):
                p_b = ndimage.uniform_filter((disc == b).astype(np.float32), size=win)
                valid = p_b > 1e-10
                entropy[valid] -= p_b[valid] * np.log2(p_b[valid] + 1e-10)
            channels.append(entropy)
            feature_names.append(f"texture_entropy_{seq}")

    # ── 4. Gradient（Sobel 梯度幅值）─────────────────────────────
    if VOXEL_FEATURES["use_gradient"]:
        for seq in _select_sequences(VOXEL_FEATURES["gradient_sequences"], seq_names):
            arr = sequence_arrays[seq]
            gz = ndimage.sobel(arr, axis=0)
            gy = ndimage.sobel(arr, axis=1)
            gx = ndimage.sobel(arr, axis=2)
            grad_mag = np.sqrt(gz ** 2 + gy ** 2 + gx ** 2)
            channels.append(grad_mag.astype(np.float32))
            feature_names.append(f"gradient_{seq}")

    # ── 5.（可选）LoG 多尺度响应 ─────────────────────────────────
    if VOXEL_FEATURES.get("use_log", False):
        for seq in _select_sequences(VOXEL_FEATURES["log_sequences"], seq_names):
            arr = sequence_arrays[seq]
            spacing = spacing_map[seq]   # (x,y,z)
            for sigma_mm in VOXEL_FEATURES["log_sigmas"]:
                sigma_vox = [sigma_mm / s for s in spacing]
                log_resp  = ndimage.gaussian_laplace(arr, sigma=sigma_vox)
                channels.append(log_resp)
                feature_names.append(f"log{sigma_mm}_{seq}")

    # ── 6.（可选）三维小波子带 ───────────────────────────────────
    if VOXEL_FEATURES.get("use_wavelet", False):
        try:
            import pywt
            for seq in _select_sequences(VOXEL_FEATURES["wavelet_sequences"], seq_names):
                arr = sequence_arrays[seq]
                coeffs = pywt.dwtn(arr, VOXEL_FEATURES["wavelet_name"])
                for band in ('llh', 'lhl'):
                    if band in coeffs:
                        band_map = pywt.idwtn(
                            {band: coeffs[band]}, VOXEL_FEATURES["wavelet_name"],
                            shape=arr.shape,
                        )
                        channels.append(band_map.astype(np.float32))
                        feature_names.append(f"wavelet_{band}_{seq}")
        except ImportError:
            logger.warning("PyWavelets 未安装，跳过小波特征（pip install PyWavelets）。")

    if not channels:
        raise RuntimeError(
            "未生成任何体素特征通道，请检查 config.VOXEL_FEATURES 中至少"
            "开启一类特征（use_intensity / use_parametric_map / "
            "use_local_texture / use_gradient）。"
        )

    feature_stack  = np.stack(channels, axis=-1)     # (Z,Y,X,C)
    voxel_features = feature_stack[mask_arr]         # (N,C)
    voxel_features = np.nan_to_num(voxel_features, nan=0.0, posinf=0.0, neginf=0.0)

    logger.debug(
        f"体素融合特征提取完成：{voxel_features.shape[0]} 体素 × "
        f"{voxel_features.shape[1]} 通道（{feature_names}）"
    )
    return voxel_features, mask_arr, feature_names


# ═══════════════════════════════════════════════════════════════════
#  3. Superpixel（SLIC 超体素）预分割方案
# ═══════════════════════════════════════════════════════════════════

def compute_superpixel_segmentation(sequence_images: dict,
                                     mask: sitk.Image) -> np.ndarray:
    """
    在 ROI 内执行 3D SLIC 超体素分割。

    实现说明
    --------
    - 输入为多通道 3D 体积（默认使用全部可用序列的原始强度作为分割依据，
      也可在 config.HABITAT["superpixel"]["channels"] 中指定序列子集）
    - 分割前对每个通道独立做 min-max 归一化，避免不同序列量纲差异
      主导 SLIC 的空间-强度联合距离度量
    - 仅在 ROI 的外接包围盒内计算，显著减少计算量；结果映射回原尺寸

    Parameters
    ----------
    sequence_images : dict[str, sitk.Image] — 已配准对齐的多序列图像
    mask            : sitk.Image — ROI mask

    Returns
    -------
    np.ndarray, shape (Z,Y,X), dtype=int32 — 超体素标签图，ROI 外体素为 -1

    Raises
    ------
    ImportError — scikit-image 未安装
    """
    try:
        from skimage.segmentation import slic
    except ImportError as e:
        raise ImportError(
            "Superpixel 方案依赖 scikit-image，请先安装：pip install scikit-image"
        ) from e

    cfg = HABITAT["superpixel"]
    mask_arr = sitk.GetArrayFromImage(mask).astype(bool)

    seq_names = list(sequence_images.keys())
    channels_spec = cfg.get("channels", "all")
    selected = _select_sequences(channels_spec, seq_names)
    if not selected:
        raise RuntimeError("Superpixel 分割：没有可用于分割的序列通道。")

    # 外接包围盒裁剪，减少无关背景计算量
    zs, ys, xs = np.where(mask_arr)
    pad = 2
    z0, z1 = max(zs.min() - pad, 0), min(zs.max() + pad, mask_arr.shape[0] - 1)
    y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad, mask_arr.shape[1] - 1)
    x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad, mask_arr.shape[2] - 1)
    roi_slice = (slice(z0, z1 + 1), slice(y0, y1 + 1), slice(x0, x1 + 1))

    crop_mask = mask_arr[roi_slice]

    channel_stack = []
    for seq in selected:
        arr = sitk.GetArrayFromImage(sequence_images[seq]).astype(np.float32)[roi_slice]
        lo, hi = float(arr.min()), float(arr.max())
        norm = (arr - lo) / (hi - lo + 1e-8)
        channel_stack.append(norm)
    volume = np.stack(channel_stack, axis=-1)   # (z,y,x,C)

    segments = slic(
        volume,
        n_segments   = cfg["n_segments"],
        compactness  = cfg["compactness"],
        sigma        = cfg["sigma"],
        mask         = crop_mask,
        channel_axis = -1,
        start_label  = 0,
    )

    superpixel_labels = np.full(mask_arr.shape, fill_value=-1, dtype=np.int32)
    valid = crop_mask & (segments >= 0)
    superpixel_labels[roi_slice] = np.where(valid, segments, -1)
    # 上一行未落在 crop_mask 内的体素强制置 -1（SLIC 的 mask 参数已保证，
    # 此处为双重防护，避免版本差异导致的边界行为不一致）
    superpixel_labels[~mask_arr] = -1

    n_sp = len(np.unique(superpixel_labels[superpixel_labels >= 0]))
    logger.info(f"  Superpixel 分割完成：ROI 内生成 {n_sp} 个超体素")
    return superpixel_labels


def aggregate_by_superpixel(voxel_features: np.ndarray,
                             mask_arr: np.ndarray,
                             superpixel_labels: np.ndarray
                             ) -> tuple:
    """
    将体素级融合特征按超体素分组取均值，得到超体素级特征矩阵。

    Parameters
    ----------
    voxel_features     : np.ndarray, shape (N, C) — ROI 内体素特征
                          （N = mask_arr.sum()，顺序与 mask_arr 展平顺序一致）
    mask_arr            : np.ndarray, shape (Z,Y,X), bool
    superpixel_labels   : np.ndarray, shape (Z,Y,X), int — compute_superpixel_segmentation 输出

    Returns
    -------
    sp_features : np.ndarray, shape (S, C) — S 个超体素的均值特征
    sp_ids      : np.ndarray, shape (S,)   — 对应的超体素标签（升序）
    voxel_sp_id : np.ndarray, shape (N,)   — 每个体素所属的超体素标签
                  （用于后续将超体素聚类结果映射回体素级 mask）
    """
    voxel_sp_id = superpixel_labels[mask_arr]   # (N,)，与 voxel_features 行对应
    sp_ids = np.unique(voxel_sp_id)
    sp_ids = sp_ids[sp_ids >= 0]

    sp_features = np.zeros((len(sp_ids), voxel_features.shape[1]), dtype=np.float32)
    for i, sp in enumerate(sp_ids):
        sp_features[i] = voxel_features[voxel_sp_id == sp].mean(axis=0)

    return sp_features, sp_ids, voxel_sp_id


# ═══════════════════════════════════════════════════════════════════
#  4. 全局模型容器（体素级 / 超体素级通用）
# ═══════════════════════════════════════════════════════════════════

class CohortHabitatModel:
    """
    封装全队列生境划分流水线：StandardScaler → PCA → KMeans。

    与旧版一致，仅将输入语义从"单序列体素特征"扩展为"多序列融合特征"
    （可以是体素级或超体素级，取决于 HABITAT["use_superpixel"]）；
    模型本身对此完全无感知（只处理已经打包好的 (N, C) 矩阵），因此
    无需改动内部实现。
    """

    def __init__(self):
        self.scaler    = StandardScaler()
        self.pca       = None
        self.kmeans    = None
        self.optimal_k = None

    def fit(self, X: np.ndarray, output_dir: str):
        """
        在全队列池化特征矩阵上依次拟合 Scaler、PCA、KMeans。

        Parameters
        ----------
        X          : np.ndarray, shape (N, C) — 全队列池化融合特征矩阵
        output_dir : str                      — K 优化图表保存目录
        """
        logger.info(f"拟合全局生境模型：{X.shape[0]} 样本，{X.shape[1]} 特征通道")

        X_scaled = self.scaler.fit_transform(X)

        if X.shape[1] > 5:
            self.pca = PCA(n_components=VOXEL_FEATURES["pca_variance"], svd_solver='full')
            X_reduced = self.pca.fit_transform(X_scaled)
            logger.info(
                f"  PCA：{X_scaled.shape[1]}D → {X_reduced.shape[1]}D "
                f"（保留 {VOXEL_FEATURES['pca_variance']*100:.0f}% 方差）"
            )
        else:
            self.pca  = None
            X_reduced = X_scaled
            logger.info(f"  PCA：跳过（特征维度 {X.shape[1]} ≤ 5）")

        self.optimal_k, _ = optimize_k(X_reduced, output_dir, "Cohort")

        self.kmeans = KMeans(
            n_clusters   = self.optimal_k,
            random_state = HABITAT["random_state"],
            n_init       = HABITAT["n_init"],
            max_iter     = 500,
        )
        self.kmeans.fit(X_reduced)
        logger.info(f"  全局 KMeans 拟合完成（K={self.optimal_k}）")

    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        对新患者的融合特征矩阵应用已拟合的流水线，返回聚类标签。

        Parameters
        ----------
        X : np.ndarray, shape (N, C)

        Returns
        -------
        np.ndarray, shape (N,) — 聚类标签（0 到 K-1）
        """
        X_scaled = self.scaler.transform(X)
        X_reduced = self.pca.transform(X_scaled) if self.pca is not None else X_scaled
        return self.kmeans.predict(X_reduced)

    def save(self, path: str):
        """将模型序列化至 .joblib 文件（包含 Scaler / PCA / KMeans 全部参数）。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        joblib.dump(self, path)
        logger.info(f"全局模型已保存：{path}")

    @staticmethod
    def load(path: str) -> 'CohortHabitatModel':
        """从 .joblib 文件反序列化全局模型。"""
        model = joblib.load(path)
        logger.info(f"全局模型已加载：{path}（K={model.optimal_k}）")
        return model


# ═══════════════════════════════════════════════════════════════════
#  5. K 自动优化（Elbow / Silhouette / 共识聚类，逻辑与旧版一致）
# ═══════════════════════════════════════════════════════════════════

def _elbow_optimal_k(X: np.ndarray, k_range: range, rs: int) -> tuple:
    """
    肘部法则确定最优 K：Inertia 二阶差分最大处。

    健壮性说明
    ----------
    二阶差分至少需要 3 个 K 候选值才有意义（np.diff 两次后长度为
    len(k_range)-2）。若 k_range 仅含 1~2 个候选值（如调试/测试场景
    人为收窄范围），退化为直接取惯性下降幅度最大的相邻 K（一阶差分），
    仅 1 个候选值时直接返回该值，避免 np.argmax 在空数组上崩溃。
    """
    inertias = []
    for k in k_range:
        km = KMeans(n_clusters=k, random_state=rs, n_init=10, max_iter=300).fit(X)
        inertias.append(km.inertia_)

    ks = list(k_range)
    if len(inertias) < 3:
        if len(inertias) == 1:
            return ks[0], inertias
        # 仅 2 个候选值：退化为一阶差分（下降幅度）最大处
        diffs1 = np.diff(inertias)
        idx = int(np.argmax(np.abs(diffs1)))
        logger.warning(
            f"k_range 仅含 {len(ks)} 个候选值，无法计算二阶差分，"
            "肘部法退化为一阶差分判断。"
        )
        return ks[idx], inertias

    diffs2    = np.diff(np.diff(inertias))
    elbow_idx = int(np.argmax(diffs2)) + 1   # +1 补偿两次 diff 的偏移
    return ks[elbow_idx], inertias


def _silhouette_optimal_k(X: np.ndarray, k_range: range, rs: int) -> tuple:
    """轮廓系数法确定最优 K：得分最高者。k<2 时直接返回 1（轮廓系数无定义）。"""
    sil_scores  = {}
    sample_size = min(10000, len(X))
    for k in k_range:
        if k < 2:
            continue
        km     = KMeans(n_clusters=k, random_state=rs, n_init=10, max_iter=300)
        labels = km.fit_predict(X)
        sil    = silhouette_score(X, labels, sample_size=sample_size, random_state=rs)
        sil_scores[k] = sil
    if not sil_scores:
        return 1, {1: 0.0}
    return max(sil_scores, key=sil_scores.get), sil_scores


def _consensus_optimal_k(X: np.ndarray, k_range: range, rs: int) -> dict:
    """共识聚类评估各 K 的稳定性（重复子采样两次独立 KMeans 的 ARI 均值）。"""
    rng   = np.random.RandomState(rs)
    n_sub = int(len(X) * HABITAT["consensus_subsample"])

    consensus = {}
    for k in k_range:
        ari_list = []
        for _ in range(HABITAT["consensus_n_iter"]):
            idx   = rng.choice(len(X), n_sub, replace=False)
            X_sub = X[idx]
            l1 = KMeans(n_clusters=k, n_init=5, random_state=None).fit_predict(X_sub)
            l2 = KMeans(n_clusters=k, n_init=5, random_state=None).fit_predict(X_sub)
            ari_list.append(adjusted_rand_score(l1, l2))

        mean_ari = float(np.mean(ari_list))
        consensus[k] = {
            'mean_ari': mean_ari,
            'std_ari' : float(np.std(ari_list)),
            'stable'  : mean_ari > HABITAT["ari_threshold"],
        }
    return consensus


def optimize_k(X: np.ndarray, output_dir: str, name: str) -> tuple:
    """
    综合 Elbow / Silhouette / 共识聚类三种方法自动确定最优聚类数 K。

    决策规则：优先在"共识 ARI 稳定"的 K 中选轮廓系数最高者；
    若无稳定 K，回退至轮廓系数最高的 K 并记录警告。
    """
    os.makedirs(output_dir, exist_ok=True)
    k_range = HABITAT["k_range"]
    rs      = HABITAT["random_state"]

    n_sample = min(20000, len(X))
    idx      = np.random.RandomState(rs).choice(len(X), n_sample, replace=False)
    X_sample = X[idx]

    elbow_k, inertias  = _elbow_optimal_k(X_sample, k_range, rs)
    sil_k, sil_scores  = _silhouette_optimal_k(X_sample, k_range, rs)
    consensus          = _consensus_optimal_k(X_sample, k_range, rs)

    stable_ks = [k for k in k_range if consensus[k]['stable']]
    if not stable_ks:
        logger.warning(
            f"[{name}] 未找到 ARI > {HABITAT['ari_threshold']} 的稳定 K，"
            f"回退至轮廓系数最高的 K={sil_k}。"
        )
        optimal_k = sil_k
    else:
        optimal_k = max(stable_ks, key=lambda k: sil_scores.get(k, -1))
        logger.info(
            f"[{name}] 稳定 K 候选：{stable_ks}，"
            f"最终 optimal_k={optimal_k}（轮廓系数={sil_scores[optimal_k]:.4f}）"
        )

    _plot_k_optimization(k_range, inertias, sil_scores, consensus, optimal_k, output_dir, name)
    return optimal_k, {'optimal_k': optimal_k, 'consensus': consensus}


def _plot_k_optimization(k_range, inertias, sil_scores, consensus,
                          optimal_k, output_dir, name):
    """绘制三方法 K 优化曲线图（Elbow / Silhouette / 共识 ARI），保存为 PNG。"""
    ks = list(k_range)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    fig.suptitle(f"{name} — K 优化分析", fontsize=13)

    axes[0].plot(ks, inertias, 'b-o', markersize=5)
    axes[0].axvline(optimal_k, color='red', linestyle='--', label=f'K={optimal_k}')
    axes[0].set_title('Elbow（惯性）')
    axes[0].set_xlabel('K')
    axes[0].set_ylabel('Inertia')
    axes[0].legend()

    sil_ks = list(sil_scores.keys())
    sil_vals = [sil_scores[k] for k in sil_ks]
    axes[1].plot(sil_ks, sil_vals, 'g-o', markersize=5)
    axes[1].axvline(optimal_k, color='red', linestyle='--', label=f'K={optimal_k}')
    axes[1].set_title('Silhouette（轮廓系数）')
    axes[1].set_xlabel('K')
    axes[1].set_ylabel('Score')
    axes[1].legend()

    ari_means = [consensus[k]['mean_ari'] for k in ks]
    ari_stds  = [consensus[k]['std_ari']  for k in ks]
    axes[2].errorbar(ks, ari_means, yerr=ari_stds, fmt='r-o', markersize=5, capsize=4)
    axes[2].axhline(HABITAT["ari_threshold"], color='gray', linestyle=':',
                     label=f'阈值={HABITAT["ari_threshold"]}')
    axes[2].axvline(optimal_k, color='red', linestyle='--', label=f'K={optimal_k}')
    axes[2].set_title('共识 ARI（稳定性）')
    axes[2].set_xlabel('K')
    axes[2].set_ylabel('ARI')
    axes[2].legend()

    plt.tight_layout()
    out_path = os.path.join(output_dir, f"{name}_k_optimization.png")
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close()
    logger.info(f"K 优化图已保存：{out_path}")


# ═══════════════════════════════════════════════════════════════════
#  6. Mask 构建与保存
# ═══════════════════════════════════════════════════════════════════

def build_habitat_masks(labels:     np.ndarray,
                         mask_arr:   np.ndarray,
                         reference:  sitk.Image,
                         patient_id: str,
                         output_dir: str,
                         expected_k: int = None) -> list:
    """
    根据体素级聚类标签生成 K 个二值 Habitat Mask，并保存为 NIfTI 文件。

    Parameters
    ----------
    labels     : np.ndarray, shape (N,) — ROI 内体素的聚类标签（0 到 K-1）
    mask_arr   : np.ndarray, shape (Z,Y,X), bool
    reference  : sitk.Image — 参考序列图像（提供空间元数据）
    patient_id : str
    output_dir : str
    expected_k : int, optional — 若提供则强制生成 0..expected_k-1 共 K 个 mask
                                 （即使某簇体素=0）；否则按 labels.max()+1 生成

    Returns
    -------
    list[sitk.Image] — K 个二值 Habitat Mask（UInt8）
    """
    label_3d = np.full(mask_arr.shape, fill_value=-1, dtype=np.int16)
    label_3d[mask_arr] = labels

    if expected_k is not None and HABITAT.get("force_full_k_masks", True):
        k = int(expected_k)
    else:
        k = int(labels.max()) + 1 if len(labels) > 0 else 1

    habitat_masks = []
    for h in range(k):
        binary_arr  = (label_3d == h).astype(np.uint8)
        mask_h_sitk = sitk.GetImageFromArray(binary_arr)
        mask_h_sitk.CopyInformation(reference)

        save_path = os.path.join(output_dir, f"{patient_id}_habitat{h}.nii.gz")
        sitk.WriteImage(mask_h_sitk, save_path)
        habitat_masks.append(mask_h_sitk)

        n_vox = int((labels == h).sum()) if len(labels) > 0 else 0
        pct = (n_vox / len(labels) * 100) if len(labels) > 0 else 0.0
        logger.info(
            f"  Habitat {h}：{n_vox} 体素（占 ROI 的 {pct:.1f}%）→ {save_path}"
        )

    return habitat_masks


# ═══════════════════════════════════════════════════════════════════
#  7. 统一调用接口
# ═══════════════════════════════════════════════════════════════════

def _fallback_local_kmeans(X: np.ndarray, expected_k: int, patient_id: str) -> np.ndarray:
    """
    当全局 predict 产生空簇或非空数不足时，在患者自身特征上重新拟合
    KMeans(n_clusters=expected_k)，尽量保证每个簇都有样本。
    """
    n_samples = X.shape[0]
    k = min(expected_k, n_samples)
    if k < 1:
        k = 1
    logger.warning(
        f"  [回退] 患者 {patient_id}：全局模型产生空簇/非空不足，"
        f"改用局部 KMeans（K={k}）重新聚类"
    )
    km = KMeans(
        n_clusters=k,
        random_state=HABITAT["random_state"],
        n_init=HABITAT["n_init"],
        max_iter=500,
    )
    return km.fit_predict(X)


def build_habitats(sequence_images: dict,
                   mask:         sitk.Image,
                   output_dir:   str,
                   patient_id:   str,
                   global_model: CohortHabitatModel = None
                   ) -> tuple:
    """
    生境划分统一接口（全队列模式 / 单患者模式，体素级 / 超体素级自动切换）。

    执行流程
    --------
    1. compute_voxel_feature_maps()：提取多序列融合体素特征
    2. 若 HABITAT["use_superpixel"]=True：Superpixel 预分割
    3. 全队列模式：global_model.predict()；单患者模式：本地 optimize_k + KMeans
    4. 若全局 predict 后空簇或非空数不足且 fallback_to_local_kmeans=True，
       则回退局部 KMeans
    5. build_habitat_masks(..., expected_k=k) 强制生成 full-K masks

    Returns
    -------
    (habitat_masks, optimal_k, detail_dict)
    """
    voxel_feats, mask_arr, feature_names = compute_voxel_feature_maps(sequence_images, mask)

    use_sp = HABITAT.get("use_superpixel", False)
    voxel_sp_id = None

    if use_sp:
        logger.info(f"  [生境划分] 患者 {patient_id}：启用 Superpixel 预分割")
        sp_labels = compute_superpixel_segmentation(sequence_images, mask)
        cluster_input, sp_ids, voxel_sp_id = aggregate_by_superpixel(
            voxel_feats, mask_arr, sp_labels
        )
    else:
        cluster_input = voxel_feats

    if global_model is not None:
        labels_for_units = global_model.predict(cluster_input)
        k      = global_model.optimal_k
        detail = {'optimal_k': k, 'mode': 'global', 'feature_names': feature_names,
                   'use_superpixel': use_sp}
        logger.info(f"  [生境划分] 患者 {patient_id}：全局模型（K={k}）")

        # 空簇 / 非空数检查 + 可选局部回退
        unique, counts = np.unique(labels_for_units, return_counts=True)
        n_nonempty = len(unique)
        min_nonempty = HABITAT.get("min_nonempty_habitats", 2)
        if k == 1 and HABITAT.get("allow_k1", True):
            min_nonempty = 1
        has_empty = (n_nonempty < k) or np.any(counts < HABITAT.get("min_voxels", 10))

        if HABITAT.get("fallback_to_local_kmeans", True) and (n_nonempty < min_nonempty or has_empty):
            labels_for_units = _fallback_local_kmeans(cluster_input, k, patient_id)
            detail['mode'] = 'global_fallback_local'
            unique = np.unique(labels_for_units)
            n_nonempty = len(unique)
            logger.info(
                f"  [回退完成] 患者 {patient_id}：局部 KMeans 后非空簇数={n_nonempty}"
            )
    else:
        logger.info(f"  [生境划分] 患者 {patient_id}：单患者模式（独立优化 K）")
        X_reduced, scaler, pca = scale_and_reduce_local(cluster_input)
        k_opt_dir = os.path.join(output_dir, "k_optimization")
        k, detail = optimize_k(X_reduced, k_opt_dir, patient_id)
        if k == 1 and HABITAT.get("allow_k1", True):
            labels_for_units = np.zeros(cluster_input.shape[0], dtype=np.int64)
            detail['mode'] = 'local_k1'
        else:
            km = KMeans(n_clusters=k, random_state=HABITAT["random_state"], n_init=HABITAT["n_init"])
            km.fit(X_reduced)
            labels_for_units = km.labels_
            detail['mode'] = 'local'
        detail['feature_names'] = feature_names
        detail['use_superpixel'] = use_sp

    # Superpixel 标签展开
    if use_sp:
        labels = np.empty(voxel_sp_id.shape[0], dtype=np.int64)
        sp_id_to_label = {sp: lab for sp, lab in zip(sp_ids, labels_for_units)}
        for i, sp in enumerate(voxel_sp_id):
            labels[i] = sp_id_to_label[sp]
    else:
        labels = labels_for_units

    # k=1 特判
    if k == 1 and HABITAT.get("allow_k1", True):
        labels = np.zeros(len(labels), dtype=np.int64)

    masks = build_habitat_masks(
        labels, mask_arr, next(iter(sequence_images.values())),
        patient_id, output_dir, expected_k=k
    )
    return masks, k, detail


def scale_and_reduce_local(features: np.ndarray) -> tuple:
    """
    单患者局部模式的特征标准化与 PCA 降维（体素级或超体素级均适用）。
    局部 Scaler / PCA 不保存，因为每位患者独立执行。
    """
    scaler   = StandardScaler()
    X_scaled = scaler.fit_transform(features)

    if features.shape[1] > 5:
        pca       = PCA(n_components=VOXEL_FEATURES["pca_variance"])
        X_reduced = pca.fit_transform(X_scaled)
    else:
        pca       = None
        X_reduced = X_scaled

    return X_reduced, scaler, pca
