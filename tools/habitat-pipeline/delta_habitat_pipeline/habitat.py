"""
habitat.py
==========
生境划分模块（全队列一致性增强版）

核心概念
--------
"生境"（Habitat）指肿瘤内部具有相似局部影像学表型的体素集合。
通过在体素级多通道特征空间中执行 K-means 聚类，将肿瘤 mask 内的
每个体素分配到某一生境，从而捕获肿瘤内异质性。

全队列一致建模策略
------------------
    问题：若每位患者独立聚类，同一编号的 Habitat 在不同患者中代表
          不同的组织表型（聚类结果具有置换不变性）。
    解决：汇总全队列所有患者基线体素特征，拟合统一的全局模型
         （Scaler → PCA → KMeans），再对每位患者应用该模型。
         保证 Habitat H 在所有患者中代表相同的体素表型语义。

模块职责
--------
    compute_voxel_feature_maps  — 提取单患者体素级多通道特征图
    CohortHabitatModel          — 封装全局模型（Scaler/PCA/KMeans）
    optimize_k                  — 自动确定最优聚类数 K
    build_habitat_masks         — 根据聚类标签生成 3D Mask 文件
    build_habitats              — 统一接口（全队列模式 / 单患者模式）

Bug 修复（相比原版）
--------------------
    [BUG-6] CohortHabitatModel.predict 中对 self.pca is None 的情况
    缺乏显式判断，当特征通道数 ≤ 5（无需 PCA 时）会引发 AttributeError。
    修复：在 predict 中加入与 fit 对称的 None 检查。
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
matplotlib.use('Agg')   # 非交互式后端，适合服务器环境（无显示器）
import matplotlib.pyplot as plt

from config import VOXEL_FEATURES, HABITAT, DIRS

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  1. 体素级特征图提取
# ═══════════════════════════════════════════════════════════════════

def compute_voxel_feature_maps(image: sitk.Image,
                                mask:  sitk.Image
                                ) -> tuple[np.ndarray, np.ndarray]:
    """
    在肿瘤 mask 内提取每个体素的多通道局部特征向量。

    特征通道说明
    ------------
    1. 原始强度               — 直接反映局部灰度值（HU / 信号强度）
    2. 局部均值               — 窗口内灰度均值，表征区域平均强度
    3. 局部方差               — 窗口内灰度方差，表征局部纹理粗糙程度
    4. 局部熵                 — 窗口内离散化强度的信息熵，衡量纹理复杂度
    5. LoG 响应（多尺度）     — 高斯-拉普拉斯滤波，检测不同尺度的边缘/斑点
    6. 小波子带（可选）       — 小波分解的低高频子带，捕获多尺度方向纹理

    注意事项
    --------
    - 所有特征在提取后统一进行 NaN / Inf 清零，防止下游标准化出错
    - 局部滑窗半径由 config.VOXEL_FEATURES["window_radius"] 控制
    - 小波特征依赖 PyWavelets，未安装时自动跳过并记录警告

    Parameters
    ----------
    image : sitk.Image — 预处理后的单时间点图像（Float32）
    mask  : sitk.Image — 肿瘤 ROI mask（UInt8，二值）

    Returns
    -------
    voxel_features : np.ndarray, shape (N, C)
        mask 内 N 个体素的 C 维特征矩阵
    mask_arr       : np.ndarray, shape (Z, Y, X), dtype=bool
        boolean mask 数组（用于后续将 1D 标签映射回 3D 体积）
    """
    img_arr  = sitk.GetArrayFromImage(image).astype(np.float32)  # (Z, Y, X)
    mask_arr = sitk.GetArrayFromImage(mask).astype(bool)          # (Z, Y, X)

    # 滑窗边长 = 2 * radius + 1（radius=3 → 7×7×7 体素）
    win = VOXEL_FEATURES["window_radius"] * 2 + 1

    channels = []

    # ── 特征 1：原始强度 ─────────────────────────────────────────
    channels.append(img_arr.copy())

    # ── 特征 2：局部均值（均匀滤波近似滑窗均值）─────────────────
    local_mean = ndimage.uniform_filter(img_arr.astype(np.float32), size=win)
    channels.append(local_mean)

    # ── 特征 3：局部方差（Var = E[X²] - E[X]²）──────────────────
    # 使用 uniform_filter 分别计算 E[X²] 和 E[X]²，数值稳定
    local_sq_mean = ndimage.uniform_filter(img_arr ** 2, size=win)
    local_var     = np.maximum(local_sq_mean - local_mean ** 2, 0)
    channels.append(local_var)

    # ── 特征 4：局部熵 ───────────────────────────────────────────
    # 将图像离散化为 n_bins 个灰度箱，在滑窗内估计每个箱的概率，
    # 计算香农信息熵：H = -Σ p(b) * log2(p(b))
    n_bins  = 32
    img_min = img_arr.min()
    img_max = img_arr.max()
    img_disc = np.floor(
        (img_arr - img_min) / (img_max - img_min + 1e-8) * (n_bins - 1)
    ).astype(np.int32)
    local_entropy = np.zeros_like(img_arr, dtype=np.float32)
    for b in range(n_bins):
        # 在滑窗内估计第 b 箱的占比（连续近似）
        p_b   = ndimage.uniform_filter((img_disc == b).astype(np.float32), size=win)
        valid = p_b > 1e-10     # 仅对非零概率体素计算熵贡献
        local_entropy[valid] -= p_b[valid] * np.log2(p_b[valid] + 1e-10)
    channels.append(local_entropy)

    # ── 特征 5：LoG 多尺度响应 ───────────────────────────────────
    # 高斯-拉普拉斯（Laplacian of Gaussian）：检测不同尺度的边缘和斑点
    # sigma_mm → sigma_vox：将物理尺度（mm）转换为体素尺度
    spacing = image.GetSpacing()   # (x, y, z) 顺序
    for sigma_mm in VOXEL_FEATURES["log_sigmas"]:
        sigma_vox = [sigma_mm / s for s in spacing]
        log_resp  = ndimage.gaussian_laplace(img_arr, sigma=sigma_vox)
        channels.append(log_resp)

    # ── 特征 6（可选）：三维小波子带 ────────────────────────────
    if VOXEL_FEATURES["use_wavelet"]:
        try:
            import pywt
            # 执行单层三维离散小波变换（DWT）
            # coeffs 是一个字典，键为子带名称（'lll', 'llh', 'lhl', ... 'hhh'）
            coeffs = pywt.dwtn(img_arr, VOXEL_FEATURES["wavelet_name"])
            # 选取 LLH 和 LHL 子带（低频×低频×高频 和 低频×高频×低频）
            # 这两个子带对垂直方向和水平方向的纹理变化敏感
            for band in ('llh', 'lhl'):
                if band in coeffs:
                    # 通过逆变换将子带恢复到原始体积尺寸
                    band_map = pywt.idwtn(
                        {band: coeffs[band]},
                        VOXEL_FEATURES["wavelet_name"],
                        shape=img_arr.shape,
                    )
                    channels.append(band_map.astype(np.float32))
        except ImportError:
            logger.warning("PyWavelets 未安装，跳过小波特征（pip install PyWavelets）。")

    # ── 汇总特征图 ───────────────────────────────────────────────
    # feature_stack: (Z, Y, X, C)；voxel_features: (N, C)
    feature_stack  = np.stack(channels, axis=-1)
    voxel_features = feature_stack[mask_arr]    # 仅提取 mask 内体素

    # 清除数值异常（NaN / ±Inf），防止下游 StandardScaler 崩溃
    voxel_features = np.nan_to_num(
        voxel_features, nan=0.0, posinf=0.0, neginf=0.0
    )

    logger.debug(
        f"体素特征提取完成：{voxel_features.shape[0]} 体素 × "
        f"{voxel_features.shape[1]} 通道"
    )
    return voxel_features, mask_arr


# ═══════════════════════════════════════════════════════════════════
#  2. 全局模型容器
# ═══════════════════════════════════════════════════════════════════

class CohortHabitatModel:
    """
    封装全队列生境划分流水线：StandardScaler → PCA → KMeans。

    用途
    ----
    - fit()     ：在全队列池化特征上拟合三阶段流水线
    - predict() ：对任意患者的体素特征矩阵应用已拟合的流水线，返回聚类标签
    - save()    ：序列化模型至 .joblib 文件（断点续跑用）
    - load()    ：从 .joblib 文件反序列化模型

    跨患者一致性
    ------------
    由于所有患者共用同一 Scaler、PCA 和 KMeans，相同的体素表型
    在不同患者中会被分配到相同的 Habitat 编号，Delta 特征才具有
    跨患者可比性。
    """

    def __init__(self):
        self.scaler    = StandardScaler()
        self.pca       = None       # 特征维度 ≤ 5 时不使用 PCA
        self.kmeans    = None
        self.optimal_k = None

    def fit(self, X: np.ndarray, output_dir: str):
        """
        在全队列池化特征矩阵上依次拟合 Scaler、PCA、KMeans。

        步骤
        ----
        1. StandardScaler：零均值单位方差标准化，消除不同通道量纲差异
        2. PCA：保留 VOXEL_FEATURES["pca_variance"] 累计解释方差比例，
                降低维度加快后续聚类（特征维度 ≤ 5 时跳过）
        3. optimize_k：通过 Elbow / Silhouette / 共识聚类自动确定最优 K
        4. KMeans：以最优 K 在降维后的特征空间上拟合最终聚类模型

        Parameters
        ----------
        X          : np.ndarray, shape (N, C) — 全队列池化体素特征矩阵
        output_dir : str                      — K 优化图表保存目录
        """
        logger.info(
            f"拟合全局生境模型：{X.shape[0]} 体素样本，{X.shape[1]} 特征通道"
        )

        # 步骤 1：StandardScaler（在全队列上拟合）
        X_scaled = self.scaler.fit_transform(X)

        # 步骤 2：PCA 降维（维度 > 5 时才有意义）
        if X.shape[1] > 5:
            self.pca = PCA(
                n_components = VOXEL_FEATURES["pca_variance"],
                svd_solver   = 'full',
            )
            X_reduced = self.pca.fit_transform(X_scaled)
            logger.info(
                f"  PCA：{X_scaled.shape[1]}D → {X_reduced.shape[1]}D "
                f"（保留 {VOXEL_FEATURES['pca_variance']*100:.0f}% 方差）"
            )
        else:
            # 特征维度较低，无需 PCA
            self.pca  = None
            X_reduced = X_scaled
            logger.info(f"  PCA：跳过（特征维度 {X.shape[1]} ≤ 5）")

        # 步骤 3：自动确定最优聚类数 K（采样加速）
        self.optimal_k, _ = optimize_k(X_reduced, output_dir, "Cohort")

        # 步骤 4：以最优 K 拟合最终 KMeans
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
        对新患者的体素特征矩阵应用已拟合的流水线，返回聚类标签。

        [BUG-6 修复]
        -----------
        原版未检查 self.pca is None，在低维特征（≤5 通道）场景下
        fit() 不会创建 PCA，但 predict() 仍调用 self.pca.transform()
        会引发 AttributeError: 'NoneType' object has no attribute 'transform'。
        修复：与 fit() 对称地检查 self.pca，None 时跳过变换步骤。

        Parameters
        ----------
        X : np.ndarray, shape (N, C) — 单患者体素特征矩阵

        Returns
        -------
        np.ndarray, shape (N,) — 聚类标签（0 到 K-1）
        """
        X_scaled = self.scaler.transform(X)
        # [BUG-6 修复] 显式检查 pca 是否为 None
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
#  3. K 自动优化
# ═══════════════════════════════════════════════════════════════════

def _elbow_optimal_k(X: np.ndarray, k_range: range, rs: int) -> tuple[int, list]:
    """
    肘部法则（Elbow Method）确定最优 K。

    原理
    ----
    计算各 K 的 KMeans 惯性（Inertia = 簇内误差平方和）。
    绘制 Inertia 随 K 变化的曲线，"肘部"（二阶差分最大处）
    对应的 K 即为最优值——此后增加 K 带来的收益递减。

    Returns
    -------
    (elbow_k, inertias) — 肘部处的 K 值 + 各 K 的惯性列表
    """
    inertias = []
    for k in k_range:
        km = KMeans(n_clusters=k, random_state=rs, n_init=10, max_iter=300).fit(X)
        inertias.append(km.inertia_)
    # 二阶差分最大处即为肘部
    ks = list(k_range)
    # 二阶差分至少需要 3 个点；k_range 过短时退化，避免 argmax 空数组崩溃
    if len(inertias) < 3:
        if len(inertias) == 1:
            return ks[0], inertias
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


def _silhouette_optimal_k(X: np.ndarray, k_range: range, rs: int) -> tuple[int, dict]:
    """
    轮廓系数法（Silhouette Score）确定最优 K。

    原理
    ----
    轮廓系数 = (b - a) / max(a, b)，其中：
        a = 样本到同簇其他样本的平均距离（簇内凝聚度）
        b = 样本到最近异簇样本的平均距离（簇间分离度）
    范围 [-1, 1]，越高表示聚类质量越好。

    实现优化
    --------
    使用 sample_size 限制计算量（大数据集时全量计算代价极高）。
    K=1 无法计算轮廓系数，跳过。

    Returns
    -------
    (best_k, sil_scores) — 最高轮廓系数对应的 K + 各 K 的得分字典
    """
    sil_scores  = {}
    sample_size = min(10000, len(X))   # 最多采样 1 万体素计算（性能/精度平衡）
    for k in k_range:
        if k < 2:
            continue
        km     = KMeans(n_clusters=k, random_state=rs, n_init=10, max_iter=300)
        labels = km.fit_predict(X)
        sil    = silhouette_score(X, labels, sample_size=sample_size, random_state=rs)
        sil_scores[k] = sil
    return max(sil_scores, key=sil_scores.get), sil_scores


def _consensus_optimal_k(X: np.ndarray, k_range: range, rs: int) -> dict:
    """
    共识聚类（Consensus Clustering）评估各 K 的稳定性。

    原理
    ----
    对每个 K，重复执行 consensus_n_iter 次：
        - 每次随机子采样 consensus_subsample 比例的体素
        - 在子样本上独立运行 KMeans 两次，计算两次结果的 ARI
    ARI 均值越高，说明该 K 的聚类结果越稳定（对初始值和数据扰动不敏感）。

    Returns
    -------
    dict — {k: {'mean_ari', 'std_ari', 'stable'}} — 各 K 的稳定性评估结果
    """
    rng   = np.random.RandomState(rs)
    n_sub = int(len(X) * HABITAT["consensus_subsample"])

    consensus = {}
    for k in k_range:
        ari_list = []
        for _ in range(HABITAT["consensus_n_iter"]):
            idx   = rng.choice(len(X), n_sub, replace=False)
            X_sub = X[idx]
            # 两次独立随机初始化的 KMeans（通过 random_state=None 保证独立性）
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


def optimize_k(X: np.ndarray, output_dir: str, name: str) -> tuple[int, dict]:
    """
    综合三种方法自动确定最优聚类数 K。

    决策规则
    --------
    1. 计算各 K 的 Elbow / Silhouette / 共识 ARI 指标
    2. 筛选共识 ARI 均值 > HABITAT["ari_threshold"] 的稳定 K 值
    3. 若存在稳定 K：选取其中轮廓系数最高的
    4. 若无稳定 K（所有 K 均不稳定）：回退至轮廓系数最高的 K，并记录警告

    性能优化
    --------
    对超大数据集（体素数 > 20000）采样后计算，保证速度可接受。

    Parameters
    ----------
    X          : np.ndarray — 降维后的特征矩阵
    output_dir : str        — K 优化图表保存目录
    name       : str        — 图表标题前缀（如 "Cohort" 或患者 ID）

    Returns
    -------
    (optimal_k, detail_dict) — 最优 K + 各方法的详细结果字典
    """
    os.makedirs(output_dir, exist_ok=True)
    k_range = HABITAT["k_range"]
    rs      = HABITAT["random_state"]

    # 采样（超大数据集时加速计算）
    n_sample = min(20000, len(X))
    idx      = np.random.RandomState(rs).choice(len(X), n_sample, replace=False)
    X_sample = X[idx]

    # 三种方法独立评估
    elbow_k, inertias  = _elbow_optimal_k(X_sample, k_range, rs)
    sil_k, sil_scores  = _silhouette_optimal_k(X_sample, k_range, rs)
    consensus          = _consensus_optimal_k(X_sample, k_range, rs)

    # 综合决策
    stable_ks = [k for k in k_range if consensus[k]['stable']]
    if not stable_ks:
        logger.warning(
            f"[{name}] 未找到 ARI > {HABITAT['ari_threshold']} 的稳定 K，"
            f"回退至轮廓系数最高的 K={sil_k}。"
        )
        optimal_k = sil_k
    else:
        # 在稳定 K 中选轮廓系数最高的
        optimal_k = max(stable_ks, key=lambda k: sil_scores.get(k, -1))
        logger.info(
            f"[{name}] 稳定 K 候选：{stable_ks}，"
            f"最终 optimal_k={optimal_k}（轮廓系数={sil_scores[optimal_k]:.4f}）"
        )

    _plot_k_optimization(
        k_range, inertias, sil_scores, consensus, optimal_k, output_dir, name
    )
    return optimal_k, {'optimal_k': optimal_k, 'consensus': consensus}


def _plot_k_optimization(k_range, inertias, sil_scores, consensus,
                          optimal_k, output_dir, name):
    """
    绘制三方法 K 优化曲线图（Elbow / Silhouette / 共识 ARI），保存为 PNG。

    图表布局
    --------
    左 → Elbow（惯性随 K 变化，寻找"弯折点"）
    中 → Silhouette（轮廓系数，越高越好）
    右 → 共识 ARI（均值 ± 标准差，虚线为稳定性阈值）
    """
    ks = list(k_range)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    fig.suptitle(f"{name} — K 优化分析", fontsize=13)

    # 左：Elbow
    axes[0].plot(ks, inertias, 'b-o', markersize=5)
    axes[0].axvline(optimal_k, color='red', linestyle='--', label=f'K={optimal_k}')
    axes[0].set_title('Elbow（惯性）')
    axes[0].set_xlabel('K')
    axes[0].set_ylabel('Inertia')
    axes[0].legend()

    # 中：Silhouette
    sil_ks = list(sil_scores.keys())
    sil_vals = [sil_scores[k] for k in sil_ks]
    axes[1].plot(sil_ks, sil_vals, 'g-o', markersize=5)
    axes[1].axvline(optimal_k, color='red', linestyle='--', label=f'K={optimal_k}')
    axes[1].set_title('Silhouette（轮廓系数）')
    axes[1].set_xlabel('K')
    axes[1].set_ylabel('Score')
    axes[1].legend()

    # 右：共识 ARI（均值 ± 标准差）
    ari_means = [consensus[k]['mean_ari'] for k in ks]
    ari_stds  = [consensus[k]['std_ari']  for k in ks]
    axes[2].errorbar(ks, ari_means, yerr=ari_stds, fmt='r-o', markersize=5, capsize=4)
    axes[2].axhline(
        HABITAT["ari_threshold"], color='gray', linestyle=':',
        label=f'阈值={HABITAT["ari_threshold"]}'
    )
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
#  4. Mask 构建与保存
# ═══════════════════════════════════════════════════════════════════

def build_habitat_masks(labels:     np.ndarray,
                         mask_arr:   np.ndarray,
                         reference:  sitk.Image,
                         patient_id: str,
                         output_dir: str) -> list[sitk.Image]:
    """
    根据体素聚类标签生成 K 个二值 Habitat Mask，并保存为 NIfTI 文件。

    实现步骤
    --------
    1. 创建一个与原始体积等尺寸的 int16 数组，初始值为 -1（表示 mask 外）
    2. 将 mask 内的体素按标签赋值（0 到 K-1）
    3. 对每个 Habitat H，提取 label_3d == H 的二值数组，转为 sitk.Image
    4. CopyInformation 确保输出 mask 与原始图像的物理空间完全对齐

    Parameters
    ----------
    labels     : np.ndarray, shape (N,) — mask 内体素的聚类标签（0 到 K-1）
    mask_arr   : np.ndarray, shape (Z,Y,X), bool — boolean mask 数组
    reference  : sitk.Image             — 参考图像（提供空间元数据）
    patient_id : str
    output_dir : str

    Returns
    -------
    list[sitk.Image] — K 个二值 Habitat Mask（UInt8）
    """
    # 初始化 3D 标签图（-1 表示 mask 外部背景）
    label_3d = np.full(mask_arr.shape, fill_value=-1, dtype=np.int16)
    label_3d[mask_arr] = labels    # 将 1D 标签映射回 3D 坐标

    habitat_masks = []
    k = labels.max() + 1           # 聚类数（标签从 0 开始）
    for h in range(k):
        # 提取第 h 个 Habitat 的二值 mask
        binary_arr  = (label_3d == h).astype(np.uint8)
        mask_h_sitk = sitk.GetImageFromArray(binary_arr)
        mask_h_sitk.CopyInformation(reference)   # 复制 Origin/Spacing/Direction

        save_path = os.path.join(
            output_dir, f"p{patient_id}_habitat{h}_t1_baseline.nii.gz"
        )
        sitk.WriteImage(mask_h_sitk, save_path)
        habitat_masks.append(mask_h_sitk)

        n_vox = int((labels == h).sum())
        logger.info(
            f"  Habitat {h}：{n_vox} 体素 "
            f"（占总 mask 的 {n_vox/len(labels)*100:.1f}%）→ {save_path}"
        )

    return habitat_masks


# ═══════════════════════════════════════════════════════════════════
#  5. 统一调用接口
# ═══════════════════════════════════════════════════════════════════

def build_habitats(image:        sitk.Image,
                   mask:         sitk.Image,
                   output_dir:   str,
                   patient_id:   str,
                   global_model: CohortHabitatModel = None
                   ) -> tuple[list[sitk.Image], int, dict]:
    """
    生境划分统一接口（全队列模式 / 单患者模式自动切换）。

    全队列模式（global_model 已提供）
    -----------------------------------
    直接调用 global_model.predict()，应用全局 Scaler/PCA/KMeans，
    保证跨患者的 Habitat 编号语义一致。
    不重新优化 K（K 已在全队列建模时确定）。

    单患者模式（global_model=None）
    --------------------------------
    在当前患者的体素特征上独立执行：
        StandardScaler → PCA → optimize_k → KMeans
    每位患者独立确定 K，灵活但 Habitat 编号无跨患者可比性。
    适合小样本探索性分析。

    Parameters
    ----------
    image        : sitk.Image            — 基线（t1）图像
    mask         : sitk.Image            — 基线肿瘤 mask
    output_dir   : str                   — Habitat Mask 保存目录
    patient_id   : str
    global_model : CohortHabitatModel, optional — 全局模型

    Returns
    -------
    (habitat_masks, optimal_k, detail_dict)
    """
    # 提取体素级多通道特征
    voxel_feats, mask_arr = compute_voxel_feature_maps(image, mask)

    if global_model is not None:
        # ── 全队列模式：直接应用全局模型预测 ──────────────────────
        labels = global_model.predict(voxel_feats)
        k      = global_model.optimal_k
        detail = {'optimal_k': k, 'mode': 'global'}
        logger.info(f"  [生境划分] 患者 {patient_id}：全局模型（K={k}）")
    else:
        # ── 单患者模式：局部优化拟合 ──────────────────────────────
        logger.info(f"  [生境划分] 患者 {patient_id}：单患者模式（独立优化 K）")
        X_reduced, scaler, pca = scale_and_reduce_local(voxel_feats)
        k_opt_dir = os.path.join(output_dir, "k_optimization")
        k, detail = optimize_k(X_reduced, k_opt_dir, patient_id)
        km = KMeans(
            n_clusters   = k,
            random_state = HABITAT["random_state"],
            n_init       = HABITAT["n_init"],
        ).fit(X_reduced)
        labels = km.labels_
        detail['mode'] = 'local'

    # 生成并保存 3D Habitat Mask
    masks = build_habitat_masks(labels, mask_arr, image, patient_id, output_dir)
    return masks, k, detail


def scale_and_reduce_local(voxel_features: np.ndarray
                            ) -> tuple[np.ndarray, StandardScaler, PCA]:
    """
    单患者局部模式的特征标准化与 PCA 降维。

    逻辑与 CohortHabitatModel.fit() 相同，但仅在单患者数据上拟合。
    注意：局部 Scaler 和 PCA 不保存，因为每位患者独立执行。

    Parameters
    ----------
    voxel_features : np.ndarray, shape (N, C)

    Returns
    -------
    (X_reduced, scaler, pca)
    """
    scaler   = StandardScaler()
    X_scaled = scaler.fit_transform(voxel_features)

    if voxel_features.shape[1] > 5:
        pca       = PCA(n_components=VOXEL_FEATURES["pca_variance"])
        X_reduced = pca.fit_transform(X_scaled)
    else:
        pca       = None
        X_reduced = X_scaled

    return X_reduced, scaler, pca
