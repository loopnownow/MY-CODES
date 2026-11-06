"""
流程
----
    Step 1 — 近零方差过滤（Near-Zero Variance）
    Step 2 — Pearson 相关去冗余（joblib 并行遍历列）
    Step 3 — LassoCV 正则化筛选（n_jobs 多核并行 CV）

软编码说明
----------
所有超参数集中在顶部 CFG 字典中，函数体内零硬编码。
配置直接在 CFG 字典中修改，不依赖外部 config.py，保证模块完全独立运行。

并行加速
--------
- Step 2：Pearson 矩阵由 pandas 向量化计算；列遍历改为
          joblib.Parallel（当特征数 > CFG["parallel_col_threshold"] 时有效）
- Step 3：LassoCV(n_jobs=CFG["n_jobs"]) 自动利用全部 CPU 核心并行 CV

接口兼容
--------
select_features(X, y, output_dir) 签名与 radiomics.py 原版完全一致，
pipeline.py 中只需将导入改为：
    from LASSO import select_features
其余调用代码无需修改。

额外输出
--------
{output_dir}/lasso_best_alpha.txt — 记录 LassoCV 选出的最优 alpha，便于复现

单独运行示例
------------
    python LASSO.py
    （需要 06_selected 目录中已有 delta_features_selected.csv 及标签 CSV）
"""

import os
import logging
import numpy as np
import pandas as pd
from pathlib import Path
import matplotlib
matplotlib.use('Agg')  # 强制使用无 GUI 后端，确保在各环境下稳定运行且不弹出窗口
import matplotlib.pyplot as plt

from sklearn.feature_selection import SelectFromModel
from sklearn.linear_model import LassoCV, lasso_path
from sklearn.preprocessing import StandardScaler
from joblib import Parallel, delayed

# ═══════════════════════════════════════════════════════════════════
#  ██  置顶配置区  ██  所有可调参数集中于此，函数体内禁止硬编码  ██
# ═══════════════════════════════════════════════════════════════════

CFG = {
    # ── 路径配置 ──────────────────────────────────────────────────
    "default_output_dir": r"results_xa/06_selected",
    "debug_feature_csv":  r"E:\study\results_xa\05_delta\all_patients_delta_features.csv",
    "debug_label_csv":    r"E:\study\QC_train.csv",

    # ── Step 1：近零方差过滤 ──────────────────────────────────────
    # 方差低于此值的特征在所有样本中几乎无变化，对分类无区分能力，直接剔除
    "near_zero_var_threshold": 0.01,

    # ── Step 2：Pearson 相关去冗余 ───────────────────────────────
    # 特征对 |r| > 此值时，保留与标签 y 相关性更强的一个，删除另一个
    "correlation_threshold": 0.90,

    # 当特征列数超过此阈值时，启用 joblib 并行列遍历（小数据集避免调度开销）
    "parallel_col_threshold": 200,

    # ── Step 3：LassoCV 正则化筛选 ───────────────────────────────
    # 交叉验证折数
    "lasso_cv_folds": 10,

    # 最大迭代次数（高维稀疏场景需足够大以保证收敛）
    "lasso_max_iter": 10000,

    # alpha 搜索网格密度（越大搜索越精细，速度越慢）
    "lasso_n_alphas": 50,

    # 系数绝对值超过此阈值才视为"选中"（精确 L1 稀疏约束）
    "lasso_coef_threshold": 1e-8,

    # 随机种子（固定以保证可重复性）
    "lasso_random_state": 66,

    # 并行核数：-1 = 使用全部 CPU 核心；1 = 禁用并行（调试用）
    "n_jobs": -1,

    # ── 数据预处理 ────────────────────────────────────────────────
    # NaN 填充值（通常因 Habitat 体素数不足导致特征为 NaN）
    "fill_na_value": 0.0,

    # ── 输出文件名 ────────────────────────────────────────────────
    "output_selected_csv": "delta_features_selected.csv",
    "output_names_csv":    "selected_feature_names.csv",
    "output_alpha_txt":    "lasso_best_alpha.txt",
    "output_cv_png":       "lasso_cv_curve.png",
    "output_path_png":     "lasso_path.png",
}

# ───────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Step 1：近零方差过滤
# ═══════════════════════════════════════════════════════════════════

def filter_near_zero_variance(
    X:         pd.DataFrame,
    threshold: float = None,
) -> pd.DataFrame:
    """
    删除方差低于阈值的特征列。

    Parameters
    ----------
    X         : pd.DataFrame — 特征矩阵（样本 × 特征）
    threshold : float, optional — 覆盖 CFG["near_zero_var_threshold"]

    Returns
    -------
    pd.DataFrame — 过滤后的特征矩阵（列数 ≤ 原始列数）
    """
    thr = threshold if threshold is not None else CFG["near_zero_var_threshold"]

    variances = X.var(axis=0)
    mask      = variances > thr
    X_out     = X.loc[:, mask]

    logger.info(
        f"  Step 1（近零方差过滤，阈值 < {thr}）："
        f"剩余 {X_out.shape[1]} / {X.shape[1]} 特征"
        f"（删除 {X.shape[1] - X_out.shape[1]} 个）"
    )
    return X_out


# ═══════════════════════════════════════════════════════════════════
#  Step 2：Pearson 相关去冗余（joblib 并行列遍历）
# ═══════════════════════════════════════════════════════════════════

def _check_column_redundancy(
    col:          str,
    upper_tri:    pd.DataFrame,
    corr_with_y:  pd.Series,
    threshold:    float,
) -> list[str]:
    """
    判断与 col 高度相关的其他列中，哪些应被标记为待删除。

    在 joblib 并行任务中每列独立调用，返回本列需要删除的列名列表。
    """
    high_corr = upper_tri.index[upper_tri[col] > threshold].tolist()
    drops = []
    for other in high_corr:
        if corr_with_y.get(col, 0) >= corr_with_y.get(other, 0):
            drops.append(other)
        else:
            drops.append(col)
    return drops


def filter_pearson_redundancy(
    X:         pd.DataFrame,
    y:         pd.Series,
    threshold: float = None,
    n_jobs:    int   = None,
) -> pd.DataFrame:
    """
    删除与其他特征高度线性相关的冗余特征。

    策略
    ----
    对每对 |r| > threshold 的特征，保留与标签 y 绝对相关性更强的那个，
    删除另一个。采用上三角矩阵避免重复比较同一对。

    并行加速（方案 A）
    ------------------
    当特征列数 > CFG["parallel_col_threshold"] 时，列遍历改用
    joblib.Parallel；列数较少时退化为串行，避免调度开销。

    Parameters
    ----------
    X         : pd.DataFrame — 特征矩阵
    y         : pd.Series    — 标签
    threshold : float, optional — 覆盖 CFG["correlation_threshold"]
    n_jobs    : int, optional   — 覆盖 CFG["n_jobs"]

    Returns
    -------
    pd.DataFrame — 去冗余后的特征矩阵
    """
    thr    = threshold if threshold is not None else CFG["correlation_threshold"]
    jobs   = n_jobs if n_jobs is not None else CFG["n_jobs"]
    n_cols = X.shape[1]

    # Pearson 绝对相关矩阵（上三角）
    corr_matrix = X.corr(method="pearson").abs()
    upper_tri   = corr_matrix.where(
        np.triu(np.ones(corr_matrix.shape), k=1).astype(bool)
    )

    # 每个特征与标签 y 的绝对相关系数（用于保留决策）
    corr_with_y = X.corrwith(y).abs()

    # 并行 vs 串行决策
    use_parallel = (n_cols > CFG["parallel_col_threshold"]) and (jobs != 1)

    if use_parallel:
        logger.debug(
            f"  Step 2：特征数 {n_cols} > {CFG['parallel_col_threshold']}，"
            f"启用 joblib 并行（n_jobs={jobs}）"
        )
        results = Parallel(n_jobs=jobs)(
            delayed(_check_column_redundancy)(col, upper_tri, corr_with_y, thr)
            for col in upper_tri.columns
        )
        to_drop = set(drop for sublist in results for drop in sublist)
    else:
        to_drop = set()
        for col in upper_tri.columns:
            high_corr = upper_tri.index[upper_tri[col] > thr].tolist()
            for other in high_corr:
                if corr_with_y.get(col, 0) >= corr_with_y.get(other, 0):
                    to_drop.add(other)
                else:
                    to_drop.add(col)

    # 只删除当前列中存在的列（并行时可能产生已不存在的列名）
    to_drop = to_drop.intersection(X.columns)
    X_out   = X.drop(columns=list(to_drop))

    logger.info(
        f"  Step 2（Pearson 去冗余，|r| > {thr}，"
        f"{'并行' if use_parallel else '串行'}）："
        f"剩余 {X_out.shape[1]} 特征（删除 {len(to_drop)} 个冗余特征）"
    )
    return X_out


# ═══════════════════════════════════════════════════════════════════
#  Step 3：LassoCV 正则化筛选（多核并行 CV）
# ═══════════════════════════════════════════════════════════════════

def filter_lasso(
    X:      pd.DataFrame,
    y:      pd.Series,
    n_jobs: int = None,
) -> tuple[pd.DataFrame, list[str], LassoCV]:
    """
    通过 LassoCV 正则化将冗余特征系数精确压缩至 0，筛选非零系数特征。

    并行加速（方案 A）
    ------------------
    LassoCV 内部的 K-fold 交叉验证通过 n_jobs 参数在多个 CPU 核心上并行，
    每折独立训练，无需额外代码改动。

    前提
    ----
    输入 X 必须已经过 Z-score 标准化（在训练集上拟合），
    因为 Lasso 对特征尺度敏感。

    Parameters
    ----------
    X      : pd.DataFrame — 特征矩阵（已过 Step 1 & Step 2）
    y      : pd.Series    — 标签
    n_jobs : int, optional — 覆盖 CFG["n_jobs"]

    Returns
    -------
    X_selected        : pd.DataFrame — 筛选后的特征矩阵
    selected_features : list[str]    — 保留的特征名列表
    lasso             : LassoCV      — 已拟合的 LassoCV 对象（含 alpha_ 属性）
    """
    jobs = n_jobs if n_jobs is not None else CFG["n_jobs"]

    lasso = LassoCV(
        cv           = CFG["lasso_cv_folds"],
        max_iter     = CFG["lasso_max_iter"],
        n_alphas     = CFG["lasso_n_alphas"],
        random_state = CFG["lasso_random_state"],
        n_jobs       = jobs,          # ← 多核并行 CV
    )
    lasso.fit(X.values, y.values)

    selector          = SelectFromModel(lasso, prefit=True,
                                        threshold=CFG["lasso_coef_threshold"])
    lasso_mask        = selector.get_support()
    X_selected        = X.loc[:, lasso_mask]
    selected_features = X_selected.columns.tolist()

    logger.info(
        f"  Step 3（LassoCV，cv={CFG['lasso_cv_folds']}，"
        f"n_jobs={jobs}，最优 alpha={lasso.alpha_:.6f}）："
        f"剩余 {len(selected_features)} 特征"
    )
    return X_selected, selected_features, lasso


# ═══════════════════════════════════════════════════════════════════
#  Lasso 绘图辅助函数：绘制并保存 Lasso 路径图和 LassoCV 交叉验证曲线
# ═══════════════════════════════════════════════════════════════════

def plot_lasso_results(
    lasso:      LassoCV,
    X:          pd.DataFrame,
    y:          pd.Series,
    output_dir: str,
) -> None:
    """
    绘制并保存 Lasso 路径图 (Coefficients vs. Log10(alpha)) 
    和 LassoCV 交叉验证曲线 (Mean Squared Error vs. Log10(alpha))。

    Parameters
    ----------
    lasso      : LassoCV — 已拟合的 LassoCV 对象
    X          : pd.DataFrame — 已标准化的特征矩阵
    y          : pd.Series — 标签
    output_dir : str — 图像输出目录
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # 获取 alpha 搜索路径（LassoCV 内部排序为降序）
    alphas = lasso.alphas_
    log_alphas = np.log10(alphas)
    opt_log_alpha = np.log10(lasso.alpha_)
    
    # ─── 1. 绘制 Lasso CV 曲线 ───
    plt.figure(figsize=(7, 5.5), dpi=300)
    
    # mse_path_ 形状为 (n_alphas, n_folds)，计算均值与标准误 (Standard Error)
    mean_mse = np.mean(lasso.mse_path_, axis=1)
    std_mse  = np.std(lasso.mse_path_, axis=1)
    se_mse   = std_mse / np.sqrt(lasso.mse_path_.shape[1])
    
    # 绘制 MSE 均值和标准误误差棒 (使用 colorblind-safe 配色)
    plt.errorbar(
        log_alphas, mean_mse, yerr=se_mse,
        fmt='o', color='#0173B2', ecolor='#CC78BC', capsize=3, elinewidth=0.8, mfc='none',
        label='Mean Square Error'
    )
    
    # 标记最优 alpha 垂直线
    plt.axvline(
        opt_log_alpha, color='#DE8F05', linestyle='--', linewidth=1.5,
        label=f'Optimal $\\log_{{10}}(\\alpha)$ = {opt_log_alpha:.4f}'
    )
    
    plt.title('LASSO Cross-Validation Error Curve', fontsize=12, fontweight='bold', pad=12)
    plt.xlabel('$\\log_{10}(\\alpha)$', fontsize=11)
    plt.ylabel('Mean Squared Error (MSE)', fontsize=11)
    plt.grid(True, linestyle=':', alpha=0.5)
    plt.legend(fontsize=9, loc='upper left')
    plt.tight_layout()
    
    cv_fig_path = os.path.join(output_dir, CFG["output_cv_png"])
    plt.savefig(cv_fig_path, dpi=300)
    plt.close()
    
    # ─── 2. 绘制 Lasso Path 曲线 ───
    plt.figure(figsize=(7, 5.5), dpi=300)
    
    # 使用 sklearn 的 lasso_path 计算所有特征系数的完整演变路径
    alphas_path, coefs_path, _ = lasso_path(
        X.values, y.values, alphas=alphas, max_iter=CFG["lasso_max_iter"]
    )
    
    # coefs_path 形状为 (n_features, n_alphas)
    log_alphas_path = np.log10(alphas_path)
    for i in range(coefs_path.shape[0]):
        plt.plot(log_alphas_path, coefs_path[i, :], linewidth=0.8, alpha=0.7)
        
    # 标记最优 alpha 垂直线
    plt.axvline(
        opt_log_alpha, color='#DE8F05', linestyle='--', linewidth=1.5,
        label=f'Optimal $\\log_{{10}}(\\alpha)$ = {opt_log_alpha:.4f}'
    )
    
    plt.title('LASSO Coefficient Paths', fontsize=12, fontweight='bold', pad=12)
    plt.xlabel('$\\log_{10}(\\alpha)$', fontsize=11)
    plt.ylabel('Coefficients', fontsize=11)
    plt.grid(True, linestyle=':', alpha=0.5)
    plt.legend(fontsize=9, loc='upper left')
    plt.tight_layout()
    
    path_fig_path = os.path.join(output_dir, CFG["output_path_png"])
    plt.savefig(path_fig_path, dpi=300)
    plt.close()
    
    logger.info(
        f"  [绘图成功] Lasso CV 曲线已保存至 -> {cv_fig_path}\n"
        f"  [绘图成功] Lasso 路径图已保存至   -> {path_fig_path}"
    )


# ═══════════════════════════════════════════════════════════════════
#  主入口：select_features（与 radiomics.py 原版接口完全兼容）
# ═══════════════════════════════════════════════════════════════════

def select_features(
    X:          pd.DataFrame,
    y:          pd.Series,
    output_dir: str,
) -> tuple[pd.DataFrame, list[str]]:
    """
    三步有监督特征筛选（在训练集上执行，防止数据泄露）。

    Step 1 — 近零方差过滤
    Step 2 — Pearson 相关去冗余（joblib 并行列遍历，方案 A）
    Step 3 — LassoCV 正则化筛选（n_jobs 多核并行 CV）

    本函数签名与 radiomics.py::select_features 完全相同，
    pipeline.py 可直接替换导入：
        from LASSO import select_features

    前提
    ----
    输入 X 必须已经过 Z-score 标准化（在训练集上拟合，见 pipeline.py），
    因为 Lasso 对特征尺度敏感。NaN 由本函数内部填充（默认填 0）。

    Parameters
    ----------
    X          : pd.DataFrame — 患者级 Delta 特征矩阵（已标准化，训练集）
    y          : pd.Series    — 因变量标签（如治疗响应：0/1）
    output_dir : str          — 结果文件输出目录

    Returns
    -------
    X_selected        : pd.DataFrame — 筛选后的特征矩阵（训练集）
    selected_features : list[str]    — 保留的特征名列表

    输出文件
    --------
    {output_dir}/delta_features_selected.csv  — 筛选后特征矩阵
    {output_dir}/selected_feature_names.csv   — 特征名列表（一列，无索引）
    {output_dir}/lasso_best_alpha.txt         — 最优 alpha 值（便于复现）
    """
    os.makedirs(output_dir, exist_ok=True)
    logger.info(
        f"[特征筛选] 初始特征数：{X.shape[1]}，样本数：{X.shape[0]}"
    )

    # 预处理：NaN 填充（通常因 Habitat 体素数不足导致）
    X_work = X.copy().fillna(CFG["fill_na_value"])

    # 自动标准化：Lasso 对尺度极度敏感
    scaler = StandardScaler()
    X_scaled = pd.DataFrame(
        scaler.fit_transform(X_work),
        index=X_work.index,
        columns=X_work.columns
    )
    X_work = X_scaled

    # ── Step 1 ────────────────────────────────────────────────────
    X_work = filter_near_zero_variance(X_work)

    # ── Step 2 ────────────────────────────────────────────────────
    X_work = filter_pearson_redundancy(X_work, y)

    # ── Step 3 ────────────────────────────────────────────────────
    X_selected, selected_features, lasso = filter_lasso(X_work, y)

    # ── 绘制并保存 Lasso & LassoCV 图像 ─────────────────────────────
    plot_lasso_results(lasso, X_work, y, output_dir)

    # ── 保存结果 ──────────────────────────────────────────────────
    selected_csv = os.path.join(output_dir, CFG["output_selected_csv"])
    names_csv    = os.path.join(output_dir, CFG["output_names_csv"])
    alpha_txt    = os.path.join(output_dir, CFG["output_alpha_txt"])

    X_selected.to_csv(selected_csv)
    pd.Series(selected_features).to_csv(names_csv, index=False)
    Path(alpha_txt).write_text(
        f"lasso_best_alpha={lasso.alpha_:.8f}\n"
        f"cv_folds={CFG['lasso_cv_folds']}\n"
        f"n_alphas={CFG['lasso_n_alphas']}\n"
        f"random_state={CFG['lasso_random_state']}\n",
        encoding="utf-8",
    )

    logger.info(
        f"[特征筛选] 完成：{X.shape[1]} → {len(selected_features)} 特征\n"
        f"  筛选后特征矩阵 → {selected_csv}\n"
        f"  特征名列表     → {names_csv}\n"
        f"  最优 alpha     → {alpha_txt}"
    )
    return X_selected, selected_features


# ═══════════════════════════════════════════════════════════════════
#  单独运行入口（调试用）
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level  = logging.INFO,
        format = "%(levelname)s  %(message)s",
    )

    import sys

    # 使用 CFG 中定义的调试路径
    selected_dir = CFG["default_output_dir"]
    feature_csv  = CFG["debug_feature_csv"]
    label_csv    = CFG["debug_label_csv"]

    if not os.path.exists(feature_csv):
        print(
            f"[调试] 特征矩阵文件不存在：{feature_csv}\n"
            "请确保该文件已存在，或手动准备数据。"
        )
        sys.exit(1)

    if not os.path.exists(label_csv):
        print(
            f"[调试] 标签文件不存在：{label_csv}\n"
            "labels.csv 应包含索引列和 label 列。"
        )
        sys.exit(1)

    # 尝试多种编码读取，解决 Windows 下中文乱码/报错问题
    try:
        X_all = pd.read_csv(feature_csv, index_col=0, encoding='utf-8')
    except UnicodeDecodeError:
        X_all = pd.read_csv(feature_csv, index_col=0, encoding='gbk')

    try:
        y_df = pd.read_csv(label_csv, index_col=0, encoding='utf-8')
    except UnicodeDecodeError:
        y_df = pd.read_csv(label_csv, index_col=0, encoding='gbk')

    # 显式提取 label 列，并剔除标签为空的样本
    if 'label' in y_df.columns:
        y_all = y_df['label'].dropna()
    else:
        y_all = y_df.squeeze().dropna()

    # 对齐患者索引
    common_idx = X_all.index.intersection(y_all.index)
    X_all      = X_all.loc[common_idx]
    y_all      = y_all.loc[common_idx]

    # 进一步确保标签是数值类型
    y_all = pd.to_numeric(y_all, errors='coerce').dropna()
    common_idx = X_all.index.intersection(y_all.index)
    X_all = X_all.loc[common_idx]
    y_all = y_all.loc[common_idx]

    print(f"特征矩阵：{X_all.shape}，标签列：{y_all.name} -> {y_all.shape}")

    X_sel, feat_names = select_features(X_all, y_all, selected_dir)
    print(f"\n筛选后特征数：{len(feat_names)}")
    print("前 10 个特征名：", feat_names[:10])
