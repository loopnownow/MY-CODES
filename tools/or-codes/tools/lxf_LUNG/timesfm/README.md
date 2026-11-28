# 肺癌时序影像 × TimesFM · RECIST 预测部署包

**最小可行研究版 v1.0 | Jinshan Hospital, Fudan University**

---

## 项目结构

```
lung_recist_timesfm/
├── config.yaml                    # ← 所有参数集中管理（唯一需要修改的文件）
├── extract_lung_features.py       # Step 1：NIfTI mask → volume + diameter CSV
├── run_lung_recist_timesfm.py     # Step 2：TimesFM 预测 + RECIST 分类 + 统计验证
├── README.md                      # 本文档
│
├── data/
│   └── manifest.csv               # 输入（患者 × 时间点 × 文件路径）
│
└── artifacts/                     # 自动生成
    ├── lung_features_long.csv     # 特征提取结果
    ├── predictions.csv            # 预测结果（含 RECIST 分类）
    ├── report/
    │   ├── summary_report.md      # Markdown 统计摘要
    │   └── stats_summary.json     # 统计 JSON（供 R/Python 读入）
    └── figures/
        ├── trajectories/          # 每患者轨迹图
        ├── confusion_matrix_timesfm.png
        ├── recist_accuracy_comparison.png
        ├── bland_altman.png
        └── quantile_coverage.png
```

---

## 快速开始

### 1. 环境搭建（一次性）

```bash
conda create -n timesfm_env python=3.10 -y
conda activate timesfm_env

# PyTorch（CUDA 12.1）
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# TimesFM
git clone https://github.com/google-research/timesfm.git
cd timesfm && pip install -e ".[torch]" && cd ..

# 其他依赖
pip install nibabel pandas numpy scikit-image scipy matplotlib seaborn scikit-learn pyyaml
```

### 2. 准备 manifest.csv

```csv
patient_id,timepoint_idx,days_from_tx_start,image_path,mask_path
P001,0,0,/data/P001/t0_pre.nii.gz,/data/P001/t0_mask.nii.gz
P001,1,28,/data/P001/t1_4w.nii.gz,/data/P001/t1_mask.nii.gz
P001,2,84,/data/P001/t2_12w.nii.gz,/data/P001/t2_mask.nii.gz
P002,0,0,/data/P002/baseline.nii.gz,/data/P002/baseline_mask.nii.gz
P002,1,42,/data/P002/followup.nii.gz,/data/P002/followup_mask.nii.gz
```

**多靶病灶**（可选列 `lesion_id`）：

```csv
patient_id,timepoint_idx,days_from_tx_start,image_path,mask_path,lesion_id
P003,0,0,/data/P003/t0.nii.gz,/data/P003/t0_L1.nii.gz,L1
P003,0,0,/data/P003/t0.nii.gz,/data/P003/t0_L2.nii.gz,L2
P003,1,28,/data/P003/t1.nii.gz,/data/P003/t1_L1.nii.gz,L1
P003,1,28,/data/P003/t1.nii.gz,/data/P003/t1_L2.nii.gz,L2
```

### 3. 修改 config.yaml（按需）

主要修改项：
- `paths.manifest`：你的 manifest 路径
- `model.pretrained_id`：本地模型路径（离线环境）
- `forecast.feature_col`：`volume_ml`（默认）或 `max_diam_mm`（更贴近 RECIST）
- `recist.*`：调整分类阈值

### 4. 运行

```bash
# Step 1：特征提取（已有 mask，通常 < 1 分钟/患者）
python extract_lung_features.py --config config.yaml

# Step 2：TimesFM 预测 + 统计验证
python run_lung_recist_timesfm.py --config config.yaml

# 跳过可视化（加速批量运行）
python run_lung_recist_timesfm.py --config config.yaml --no-plots

# 详细日志（调试）
python run_lung_recist_timesfm.py --config config.yaml --verbose
```

---

## 输出说明

### predictions.csv 列说明

| 列名 | 说明 |
|------|------|
| `patient_id` | 患者 ID |
| `n_timepoints` | 总时间点数 |
| `context_length` | 输入 TimesFM 的 context 长度（LOO 模式 = n-1）|
| `baseline_value` | 基线测量值（第一个时间点）|
| `pred_value` | TimesFM 点预测值 |
| `obs_value` | 实测值（LOO 模式下的最后一个时间点）|
| `pct_change_pred` | 预测百分比变化（相对基线）|
| `pct_change_obs` | 实测百分比变化（相对基线）|
| `pred_RECIST` | TimesFM 预测的 RECIST 类别 |
| `obs_RECIST` | 实测 RECIST 类别 |
| `persistence_RECIST` | Persistence Baseline RECIST |
| `linear_RECIST` | Linear Baseline RECIST |
| `abs_error_ml` | 绝对误差（ml 或 mm）|
| `q10` / `q50` / `q90` | 分位数预测（不确定性估计）|

---

## 关键设计决策

### Leave-one-out 验证
`config.yaml` 中 `forecast.leave_one_out: true`（默认）：
- Context = 前 n-1 个时间点
- 预测目标 = 第 n 个时间点（有真实值可验证）
- **不可**将所有 n 个时间点塞入 context 再与最后一点比较——那是信息泄露

### RECIST 分类顺序
函数内判断顺序：CR → PD → PR → SD
遵循 RECIST 1.1 的"优先 PD"原则（防止边界病例被误归为 PR）

### 不确定性（Quantile Forecast）
- TimesFM 2.5 输出 10 列分位数：mean + q10 + q20 + ... + q90
- `q10-q90` 覆盖率应接近 80%，偏低说明模型低估不确定性
- 可用 q10/q90 作为 RECIST 边界的"不确定性带"

---

## 已知限制

1. **短序列（2 点）**：forecast 接近 persistence baseline，需作为 sanity check
2. **等间隔假设**：TimesFM 默认等间隔序列；不规则采样（如 day 0/28/180）建议重采样至固定间隔
3. **RECIST 完整标准**：新病灶出现、非靶灶进展等需在临床读片时叠加，本脚本仅处理靶灶百分比变化
4. **本包为研究 POC，非临床生产系统**

---

*作者：Dr. Ying Li | Jinshan Hospital, Fudan University | 2026-06*
