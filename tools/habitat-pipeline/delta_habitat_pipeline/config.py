"""
config.py
=========
全局配置文件 — Delta 生境影像组学流程

所有可调参数集中于此，避免散落在各模块中。
修改任何参数后，其余模块无需改动。

设计原则
--------
- 路径：所有路径在此集中声明，DIRS 字典由 pipeline.py 启动时自动创建。
- 参数：每个参数均附有简短说明，表明取值范围和适用场景。
- 可扩展：如需新增阶段，只需在 DIRS 中追加键值对即可。
"""

import os

# ═══════════════════════════════════════════════════════════════════
#  路径配置
# ═══════════════════════════════════════════════════════════════════

# 输入数据表（CSV / TSV，必须包含 image_path 和 roi_path 两列）
DATA_TABLE_PATH = r"E:\study\delta_habitat_pipeline\combind.csv"

# 所有中间结果 & 最终结果的根目录（可改为任意本地路径）
OUTPUT_ROOT = r"E:\study\results_js"

# 各阶段输出子目录（由 pipeline.py 的 _setup_dirs() 自动创建）
# 键名即阶段标识，与断点标志文件名对应
DIRS = {
    "preprocessed"  : os.path.join(OUTPUT_ROOT, "01_preprocessed"),
    "registration"  : os.path.join(OUTPUT_ROOT, "02_registration"),
    "habitat_masks" : os.path.join(OUTPUT_ROOT, "03_habitat_masks"),
    "features"      : os.path.join(OUTPUT_ROOT, "04_features"),
    "delta"         : os.path.join(OUTPUT_ROOT, "05_delta"),
    "selected"      : os.path.join(OUTPUT_ROOT, "06_selected"),
    "qc_reports"    : os.path.join(OUTPUT_ROOT, "07_qc"),
}

# ═══════════════════════════════════════════════════════════════════
#  预处理参数（preprocessing.py）
# ═══════════════════════════════════════════════════════════════════

PREPROC = {
    # 重采样目标体素间距（mm），全队列统一为 1×1×1 以保证特征可比性
    "target_spacing" : (1.0, 1.0, 1.0),

    # 是否执行 N4 偏置场校正
    #   CT  → False（HU 值由扫描仪硬件校准，无需软件再校正）
    #   MRI → True （主磁场不均匀导致低频强度漂移，需校正）
    "apply_n4"       : False,

    # N4 迭代次数，对应四个多分辨率层级（粗→细）
    # 增大数值可提升校正精度，但会显著延长运行时间
    "n4_iterations"  : [50, 50, 30, 20],

    # 灰度归一化方法
    #   "zscore" : 在 mask 内计算均值/标准差，执行 Z-score（MRI 推荐）
    #   "minmax" : CT 窗宽窗位裁剪后 Min-Max 归一化至 [0, 1]（CT 推荐）
    #   "none"   : 不归一化（调试用，不建议用于正式流程）
    "normalization"  : "minmax",

    # CT 窗宽窗位裁剪范围（仅 normalization="minmax" 时生效）
    # 格式：[WL - WW/2, WL + WW/2]
    # 腹部软组织窗：WL=100, WW=600 → 裁剪范围 [-200, 400] HU
    # 肺 WW 1200–1500 HU，WL -550 to -700 HU → 裁剪范围 [-1150, 350] HU, https://accreditationsupport.acr.org/support/solutions/articles/11000061040-lung-cancer-screening-center-designation-revised-7-22-2024-
    "ct_window"      : (-1000, 400),
}

# ═══════════════════════════════════════════════════════════════════
#  非刚性配准参数（registration.py，ANTsPy）
# ═══════════════════════════════════════════════════════════════════

REGISTRATION = {
    # SyN 非刚性配准各分辨率层级的迭代次数（粗→细，共 4 层）
    # 减小此值可加快速度，但可能牺牲精度
    "syn_iterations" : [100, 70, 50, 20],

    # 各层级的高斯平滑 sigma（单位：体素）
    # 粗层级使用较大 sigma 以捕获大位移，细层级使用小 sigma 以精细对齐
    "syn_smoothing"  : [3, 2, 1, 0],

    # 各层级下采样因子（8=最粗，1=原始分辨率）
    "syn_shrink"     : [8, 4, 2, 1],

    # ── 鲁棒性增强策略 ────────────────────────────────────────────
    # 强制同步 Moving 图像的 Origin 和 Direction 至 Fixed 图像
    # 适用场景：两次扫描床位偏差过大导致 ANTs 初始化失败
    "force_spatial_sync"     : True,

    # 将图像强度平移至正值区间再参与配准计算
    # 适用场景：ANTs 报告 "Total Mass is zero" 错误（通常因负值或零均值图像）
    "intensity_shift"        : True,

    # ── 配准 QC 阈值 ─────────────────────────────────────────────
    # Dice 系数：基线 mask 与传播后 mask 的重叠度（越高越好，推荐 ≥ 0.85）
    # 调试阶段设为 0.1 以确保流程跑通
    "qc_dice_threshold"      : 0.10,
    # Hausdorff 距离（mm）：两个 mask 边界的最大偏差（越小越好，推荐 ≤ 5.0 mm）
    "qc_hausdorff_threshold" : 5.0,
}

# ═══════════════════════════════════════════════════════════════════
#  体素级特征图参数（habitat.py，生境聚类输入）
# ═══════════════════════════════════════════════════════════════════

VOXEL_FEATURES = {
    # 局部统计滑窗半径（体素），滑窗边长 = 2 * radius + 1
    # 3 → 7×7×7 体素窗，捕获约 7 mm 范围内的纹理模式
    "window_radius" : 3,

    # LoG（高斯-拉普拉斯）滤波尺度列表（mm）
    # 小 sigma → 检测细小结构/高频边缘
    # 大 sigma → 检测粗大结构/低频轮廓
    "log_sigmas"    : [1.0, 2.0, 3.0],

    # 是否追加三维小波子带能量图（需安装 PyWavelets）
    # 小波特征对多尺度纹理变化敏感，推荐保留
    "use_wavelet"   : True,

    # 小波基函数名称（Daubechies 2 阶，适合软组织纹理分析）
    # 可选：'db1'（Haar）, 'db2', 'db4', 'sym4' 等
    "wavelet_name"  : "db2",

    # PCA 降维保留的累计解释方差比例
    # 0.95 = 保留 95% 的原始信息，同时去除冗余维度加快聚类速度
    "pca_variance"  : 0.95,
}

# ═══════════════════════════════════════════════════════════════════
#  生境聚类参数（habitat.py，K-means）
# ═══════════════════════════════════════════════════════════════════

HABITAT = {
    # 候选聚类数范围（含左端，不含右端）
    # range(2, 7) 表示评估 K = 2, 3, 4, 5, 6
    "k_range"            : range(3, 4),

    # 共识聚类：每次子采样的比例（0.80 = 每次随机取 80% 体素）
    "consensus_subsample": 0.80,

    # 共识聚类：Bootstrap 重复次数（次数越多结果越稳定，运行越慢）
    # 建议生产环境使用 ≥ 50，快速测试可降至 10
    "consensus_n_iter"   : 50,

    # ARI 稳定性阈值：两次独立聚类的调整兰德指数均值 > 此值才认为该 K 稳定
    # ARI ∈ [-1, 1]，1 表示完全一致，0 表示随机
    "ari_threshold"      : 0.70,

    # 每个生境的最小有效体素数
    # 低于此值的生境认为统计上不可靠，将抛出 ValueError
    "min_voxels"         : 0,

    # 是否启用全队列一致建模模式
    # True  → 汇总所有患者基线体素特征，拟合统一的全局模型，保证跨患者 Habitat 语义一致
    # False → 每个患者独立优化 K 和聚类，灵活但 Habitat 编号无跨患者可比性
    "cohort_mode"        : True,

    # 全局模型保存路径（.joblib 格式）
    # 模型存在时跳过重新拟合，直接加载（断点续跑）
    "global_model_path"  : os.path.join(
        os.path.join(OUTPUT_ROOT, "03_habitat_masks"),
        "global_habitat_model.joblib"
    ),

    # 全队列池化体素特征保存路径（.npy 格式，调试或重训练用）
    "pooled_features_path": os.path.join(
        os.path.join(OUTPUT_ROOT, "03_habitat_masks"),
        "cohort_pooled_features.npy"
    ),

    # K-means 随机种子（固定以保证可重复性）
    "random_state"       : 666,

    # K-means 随机初始化次数（多次取最优，避免局部最优解）
    "n_init"             : 5,
}

# ═══════════════════════════════════════════════════════════════════
#  Pictologics 影像组学提取参数（radiomics.py）
# ═══════════════════════════════════════════════════════════════════

RADIOMICS = {
    # 使用的标准化配置名称（可同时使用多种）
    #   "standard_fbn_32" : Fixed Bin Number，32 箱，计数归一化
    #                       → 对不同灰度范围的图像具有可比性（推荐 MRI）
    #   "standard_fbs_16" : Fixed Bin Size，箱宽 = 值域 / 16，保留绝对强度信息
    #                       → 对 HU 值敏感，推荐 HU 标准化后的 CT 图像
    #   IBSI 推荐同时使用两种，二者对不同特征的灵敏度互补
    "config_names"    : ["standard_fbn_32", "standard_fbs_16"],

    # 是否将每个生境的特征单独保存为 CSV（便于中间步骤调试）
    "save_per_habitat": True,
}

# ═══════════════════════════════════════════════════════════════════
#  Delta 特征计算参数（radiomics.py）
# ═══════════════════════════════════════════════════════════════════

DELTA = {
    # 是否计算绝对变化量：Δf = f_post - f_pre
    "compute_absolute" : True,

    # 是否计算相对变化量：Δf% = (f_post - f_pre) / (|f_pre| + ε) × 100
    "compute_relative" : True,

    # 分母保护项（epsilon）：防止 f_pre ≈ 0 时相对变化量除以零
    # 1e-6 远小于任何实际特征值，对结果影响可忽略
    "epsilon"          : 1e-6,
}

# ═══════════════════════════════════════════════════════════════════
#  特征筛选参数（radiomics.py → select_features）
# ═══════════════════════════════════════════════════════════════════

FEATURE_SELECTION = {
    # 近零方差阈值：方差低于此值的特征在所有样本中几乎无变化，
    # 对分类模型无区分能力，直接剔除
    "near_zero_var_threshold" : 0.01,

    # Pearson 相关系数冗余阈值：特征对 |r| > 此值时，保留与标签 y
    # 相关性更强的一个，删除另一个，以降低多重共线性
    "correlation_threshold"   : 0.90,
}
