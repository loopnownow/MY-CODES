"""
config.py
=========
全局配置文件 — 多参数生境影像组学流程（Multi-parametric Habitat Radiomics）

"""

import os

# ═══════════════════════════════════════════════════════════════════
#  路径配置
# ═══════════════════════════════════════════════════════════════════

# 输入数据表（CSV / TSV，长表格式：每行 = 一个患者的一个序列）
# 必须包含列：patient_id, sequence, modality, image_path, roi_path, is_reference
# 详见 data_loader.py 顶部说明
DATA_TABLE_PATH = r"G:\lung_xlm\habitat_pipeline\combined.csv"

# 可选：因变量标签表（患者级，用于有监督特征筛选），列：patient_id, label
LABELS_TABLE_PATH = DATA_TABLE_PATH # r"G:\EC_ybc\habitat_pipeline\combined.csv"

# 所有中间结果 & 最终结果的根目录（可改为任意本地路径）
OUTPUT_ROOT = r"G:\lung_xlm\results"

# 各阶段输出子目录（由 pipeline.py 的 _setup_dirs() 自动创建）
# 键名即阶段标识，与断点标志文件名对应
DIRS = {
    "preprocessed"  : os.path.join(OUTPUT_ROOT, "01_preprocessed"),   # 各序列独立预处理结果
    "registration"  : os.path.join(OUTPUT_ROOT, "02_registration"),   # 序列间配准结果（对齐至参考序列空间）
    "habitat_masks" : os.path.join(OUTPUT_ROOT, "03_habitat_masks"),  # 多参数融合生境划分结果
    "features"      : os.path.join(OUTPUT_ROOT, "04_features"),       # 逐 Habitat × 逐序列影像组学特征
    "selected"      : os.path.join(OUTPUT_ROOT, "05_selected"),       # 队列汇总 + 特征筛选结果
    "qc_reports"    : os.path.join(OUTPUT_ROOT, "06_qc"),             # 配准 / 聚类质控报告
}

# ═══════════════════════════════════════════════════════════════════
#  预处理参数（preprocessing.py）—— 按模态（CT / MRI）分别配置
# ═══════════════════════════════════════════════════════════════════

PREPROC = {
    # 重采样目标体素间距（mm），全队列 & 全序列统一，保证特征可比性
    "target_spacing" : (1.0, 1.0, 1.0),

    # 数据表中 modality 列缺失或无法识别时的回退模态
    "default_modality" : "CT",

    # 按模态区分的预处理策略。新增模态（如 PET）时只需在此追加一个键。
    "modality_settings" : {
        "CT": {
            # CT 的 HU 值由扫描仪硬件校准，通常无需 N4 偏置场校正
            "apply_n4"      : False,
            "n4_iterations" : [50, 50, 30, 20],
            # CT 推荐 minmax：窗宽窗位裁剪后归一化，保留 HU 的绝对物理意义
            "normalization" : "minmax",
            # 窗宽窗位裁剪范围 [WL-WW/2, WL+WW/2]（单位 HU）
            #   腹部软组织窗：约 [-200, 400]
            #   肺窗：约 [-1150, 350]
            # 若需要按期相（平扫/动脉期/门脉期）使用不同窗宽窗位，
            # 可在 SEQUENCE_OVERRIDES 中按序列名覆盖 ct_window。
            "ct_window"     : (-1150, 350),
        },
        "MRI": {
            # MRI 主磁场不均匀导致低频强度漂移，需 N4 校正
            "apply_n4"      : True,
            "n4_iterations" : [50, 50, 30, 20],
            # MRI 信号无绝对物理单位，推荐 zscore 消除扫描仪间尺度差异
            "normalization" : "zscore",
            "ct_window"     : None,   # MRI 不适用
        },
    },

    # 按具体序列名覆盖上述模态级默认设置（可选，键为 sequence 名称）。
    # 典型用途：ADC 图已经是定量参数图，通常不做 zscore（会破坏其
    # 定量意义），改用 "none" 或专用的 minmax 范围。
    # 示例：
    #   "SEQUENCE_OVERRIDES": {"ADC": {"normalization": "none"}}
    "SEQUENCE_OVERRIDES" : {
        "ADC": {"normalization": "none"},
    },
}

# ═══════════════════════════════════════════════════════════════════
#  序列间配准参数（registration.py，ANTsPy）
# ═══════════════════════════════════════════════════════════════════
#  所有非参考序列均配准至患者的"参考序列"（数据表中 is_reference=1 的行，
#  ROI mask 也定义在参考序列空间）。参考序列本身不参与配准（恒等变换）。

REGISTRATION = {
    # SyN 非刚性配准各分辨率层级的迭代次数（粗→细，共 4 层）
    "syn_iterations" : [100, 70, 50, 20],
    # 各层级的高斯平滑 sigma（单位：体素）
    "syn_smoothing"  : [3, 2, 1, 0],
    # 各层级下采样因子（8=最粗，1=原始分辨率）
    "syn_shrink"     : [8, 4, 2, 1],

    # 相似度测度：Mattes 互信息对同模态（CT-CT / MRI-MRI）和跨模态
    # （CT-MRI）配准均有效，是多参数融合场景下的稳健默认选择。
    # 若序列均为同一模态，可改为 "GC"（全局相关系数）以提升精度。
    "aff_metric" : "GC",
    "syn_metric" : "GC",

    # ── 鲁棒性增强策略 ────────────────────────────────────────────
    # 强制同步 Moving 图像的 Origin 和 Direction 至 Fixed 图像
    "force_spatial_sync" : True,
    # 将图像强度平移至正值区间再参与配准计算（避免 "Total Mass is zero"）
    "intensity_shift"    : True,

    # ── 配准 QC（质控，非强制失败）───────────────────────────────
    # 由于非参考序列通常没有独立 ROI mask，QC 改为基于 mask 区域内
    # 配准前后图像的归一化互信息（NMI），仅作为质控参考，不中断流程。
    # 如需严格质控（不达标即跳过该患者），将 qc_hard_fail 设为 True。
    "qc_metric"          : "mattes_mutual_information",
    "qc_min_similarity"  : 0.10,     # 经验阈值，需结合具体数据集调整
    "qc_hard_fail"       : False,
}

# ═══════════════════════════════════════════════════════════════════
#  参数图（Parametric map）派生规则（habitat.py 多序列融合特征之一）
# ═══════════════════════════════════════════════════════════════════
#  参数图指由原始序列直接使用或由多序列组合计算得到的定量/半定量图，
#  例如 CT 多期增强的强化率、MRI 的 ADC 图等。
#
#  每条规则字典字段：
#    name : str  — 派生特征图名称（将作为体素特征列名的一部分）
#    type : str  — "direct"           直接使用某序列（如 ADC 本身即为参数图）
#                  "absolute_change"  seq_b - seq_a
#                  "relative_change"  (seq_b - seq_a) / (|seq_a| + epsilon)
#                  "ratio"            seq_b / (seq_a + epsilon)
#    seq  : str  — type="direct" 时使用的序列名
#    seq_a, seq_b : str — 其余 type 使用的两个序列名（seq_a 为基准/前期相）
#
#  找不到所需序列时该条规则自动跳过并记录警告，不会中断流程。

PARAMETRIC_MAPS = [
    # 示例（按实际序列命名修改后启用）：
    # {"name": "ADC_map",            "type": "direct",          "seq": "ADC"},
    # {"name": "arterial_enhancement","type": "relative_change", "seq_a": "CT_plain", "seq_b": "CT_arterial"},
    # {"name": "portal_enhancement",  "type": "relative_change", "seq_a": "CT_plain", "seq_b": "CT_portal"},
    # {"name": "washout_ratio",       "type": "ratio",           "seq_a": "CT_arterial", "seq_b": "CT_delayed"},
]

PARAMETRIC_MAP_EPSILON = 1e-6   # 分母保护项，防止除以零

# ═══════════════════════════════════════════════════════════════════
#  体素级多序列融合特征参数（habitat.py，生境聚类输入）
# ═══════════════════════════════════════════════════════════════════
#  特征分为四大类（对应用户需求的 Multi-sequence fusion 四要素）：
#     1. Intensity        — 各序列原始强度
#     2. Parametric map   — PARAMETRIC_MAPS 派生的定量/半定量图
#     3. Local texture    — 滑窗局部均值/方差/熵（纹理粗糙度与复杂度）
#     4. Gradient         — 一阶梯度幅值（边缘/形变强度）
#  以上每一类均可独立开关，且可指定仅对哪些序列计算，避免维度爆炸。

VOXEL_FEATURES = {
    # 局部统计滑窗半径（体素），滑窗边长 = 2 * radius + 1
    "window_radius" : 3,

    # ── 1. Intensity（原始强度）──────────────────────────────────
    "use_intensity"      : True,
    "intensity_sequences": "all",   # "all" 或 ["CT_plain", "T2WI", ...]

    # ── 2. Parametric map（派生参数图，见 PARAMETRIC_MAPS）───────
    "use_parametric_map"  : True,

    # ── 3. Local texture（局部纹理：均值 / 方差 / 熵）─────────────
    "use_local_texture"   : True,
    "texture_sequences"   : "all",
    "texture_entropy_bins": 32,     # 局部熵离散化箱数

    # ── 4. Gradient（一阶梯度幅值，Sobel 算子）───────────────────
    "use_gradient"        : True,
    "gradient_sequences"  : "all",

    # ── 可选：LoG 多尺度响应（补充边缘/斑点检测，默认关闭以控制维度）
    "use_log"        : False,
    "log_sigmas"      : [1.0, 2.0, 3.0],
    "log_sequences"   : "all",

    # ── 可选：三维小波子带能量图（默认关闭，依赖 PyWavelets）───
    "use_wavelet"      : False,
    "wavelet_name"     : "db2",
    "wavelet_sequences": "all",

    # PCA 降维保留的累计解释方差比例（对最终融合特征矩阵生效）
    "pca_variance" : 0.95,
}

# ═══════════════════════════════════════════════════════════════════
#  生境聚类参数（habitat.py，K-means，可选 Superpixel 预分割）
# ═══════════════════════════════════════════════════════════════════

HABITAT = {
    # 候选聚类数范围（含左端，不含右端）
    "k_range" : range(3, 5),

    # 共识聚类：每次子采样比例 / Bootstrap 重复次数 / ARI 稳定性阈值
    "consensus_subsample" : 0.80,
    "consensus_n_iter"    : 50,
    "ari_threshold"       : 0.70,

    # 每个生境的最小有效体素数（体素级）；低于此值在特征提取时记为跳过/NaN
    "min_voxels" : 0,

    # 硬性要求的最少非空 Habitat 数（optimal_k==1 时自动降为 1）
    "min_nonempty_habitats" : 2,

    # 全局 predict 后若出现空簇或非空数不足，是否回退到患者局部 KMeans
    # （使用全局 optimal_k，保证尽量每个簇都有体素；会轻微破坏全局编号一致性）
    "fallback_to_local_kmeans" : True,

    # 是否强制生成 optimal_k 个 mask（即使某些簇体素=0）
    "force_full_k_masks" : True,

    # 允许 k_range 包含 1 时正确运行（把整个 ROI 当作 Habitat 0）
    "allow_k1" : True,

    # 是否启用全队列一致建模模式（跨患者 Habitat 编号语义一致，推荐）
    "cohort_mode" : True,

    # 全局模型保存路径（.joblib，存在时跳过重新拟合，支持断点续跑）
    "global_model_path" : os.path.join(
        os.path.join(OUTPUT_ROOT),
        "global_habitat_model.joblib"
    ),
    # 全队列池化体素/超体素特征保存路径（.npy，调试或重训练用）
    "pooled_features_path" : os.path.join(
        os.path.join(OUTPUT_ROOT),
        "cohort_pooled_features.npy"
    ),

    "random_state" : 666,
    "n_init"       : 5,   # K-means 随机初始化次数

    # ── Superpixel（SLIC 超体素）预分割方案 ──────────────────────
    # 原理：先用 SLIC 将 ROI 内体素聚合为空间连续的"超体素"（每个
    # 超体素内取融合特征均值），再对超体素而非原始体素执行 K-means。
    # 优点：显著降低样本量、抑制孤立噪声体素、生境边界更符合解剖连续性。
    # 缺点：牺牲部分体素级细节，超体素数需与 ROI 大小匹配。
    "use_superpixel" : False,
    "superpixel" : {
        # 期望生成的超体素数量（实际数量由 SLIC 算法根据紧凑度自适应）
        "n_segments"  : 200,
        # 紧凑度：值越大，超体素形状越规则（趋于立方体）；
        # 值越小，超体素边界越贴合强度边缘
        "compactness" : 0.1,
        # SLIC 前的高斯平滑 sigma（体素），抑制噪声对分割的影响
        "sigma"       : 1.0,
        # 用于计算 SLIC 分割的通道来源："all"（全部融合特征）
        # 或指定序列名列表（仅用这些序列的原始强度做分割，速度更快）
        "channels"    : "all",
    },
}

# ═══════════════════════════════════════════════════════════════════
#  Pictologics 影像组学提取参数（radiomics.py，逐 Habitat × 逐序列）
# ═══════════════════════════════════════════════════════════════════

RADIOMICS = {
    # 使用的标准化配置名称（可同时使用多种，IBSI 推荐二者互补）
    #   "standard_fbn_32" : Fixed Bin Number，32 箱，推荐 MRI / 归一化后图像
    #   "standard_fbs_16" : Fixed Bin Size，箱宽=值域/16，推荐 HU 标准化 CT
    "config_names" : ["standard_fbn_32", "standard_fbs_16"],

    # 参与影像组学提取的序列："all" 或指定序列名列表
    # （例如仅对增强期相提特征，跳过用于配准引导的平扫）
    "sequences" : "all",

    # 是否将每位患者每个序列的逐 Habitat 特征单独保存为 CSV（便于调试）
    "save_per_habitat" : True,
}

# ═══════════════════════════════════════════════════════════════════
#  特征筛选参数（radiomics.py → select_features，有监督）
# ═══════════════════════════════════════════════════════════════════

FEATURE_SELECTION = {
    # 近零方差阈值：方差低于此值的特征对分类无区分能力，直接剔除
    "near_zero_var_threshold" : 0.01,
    # Pearson 相关系数冗余阈值：|r| 超过此值的特征对，保留与标签相关性更强的一个
    "correlation_threshold"   : 0.90,
}

# ═══════════════════════════════════════════════════════════════════
#  内存管理（pipeline.py）—— 避免全队列影像常驻 RAM
# ═══════════════════════════════════════════════════════════════════
#  旧实现会在阶段 1 把所有患者的 sitk.Image 塞进 aligned_store，
#  千例级队列必然 OOM。默认改为：store 只存磁盘路径句柄，阶段 2/3
#  按需 load 当前患者，处理完立即释放。

MEMORY = {
    # True：aligned_store 只存路径；False：回退为旧行为（全量常驻，仅调试用）
    "lazy_load_aligned" : True,
    # 每处理完 N 位患者执行一次 gc.collect()（1=每位都回收，更稳但稍慢）
    "gc_every_n"        : 1,
}

