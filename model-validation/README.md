# stats-analysis/model-validation — 统计 / 验证课题 CLI

本目录放**可复跑的统计与验证脚本**（顶部 CONFIG、绑课题），不是清洗主链，也不是 `modules/utils` 库。

| 位置 | 职责 |
|---|---|
| `model-validation/*.py`（**扁平，无子目录**） | 建模队列、VAL_MODE 对比、泄漏检查、研究卡审计、插补 CLI |
| `_code/02-data-processing/`（02 整理/清洗树，**不在本仓**） | raw→表（合并 / 补全 / 校验 / 抽取） |
| `modules/utils/` | 无课题路径的库函数（`u_impute` 等）— **不接收本目录脚本** |

## 脚本

| 文件 | 作用 | 课题根 |
|---|---|---|
| `compare_val_modes.py` | 一次 Train 拟合，三种 VAL_MODE 验证对比 | `PROJECT_ROOT` / `STUDY_ROOT`（默认 `demo_study`） |
| `downsample_train.py` | train 分层下采样（1:2 Negative） | 同上；源/输出表名见脚本 CONFIG |
| `split_leakage_check.py` | 检查 patient ID 是否跨 train/val/test 泄漏 | 命令行传入 CSV |
| `radiology_audit.py` | 放射 AI 研究卡 Markdown 快速审计 | 命令行 / JSON 模板 |
| `impute_excel.py` | 分组分层缺失值插补 CLI（备份 / QC 表 / 小数位对齐）；库函数 `modules/utils/u_impute.py` | 命令行传入 xlsx |
| `recalc_xlsx.py` | 用 LibreOffice 重算公式 + 扫描 `#REF!`/`#DIV/0!` 等错误；`--check-only` 只扫描 | 命令行传入 xlsx |
| `mi_impute.py` | 多重插补：`draw` 出 m 个完成数据集，`pool` 按 Rubin 规则合并（含 Barnard–Rubin 自由度、FMI） | 命令行传入 xlsx / 估计表 |

## 运行

```bat
set PYTHONDONTWRITEBYTECODE=1
set STATS_ROOT=<path-to-this-repo>
set PROJECT_ROOT=<path-to-your-study>

python model-validation\compare_val_modes.py
python model-validation\downsample_train.py
python model-validation\split_leakage_check.py path\to\splits.csv
python model-validation\radiology_audit.py --help
python model-validation\impute_excel.py --input data.xlsx --sheet cln --group Group --method iterative
python model-validation\recalc_xlsx.py book.xlsx
python model-validation\mi_impute.py draw --input cln.xlsx --group Group -m 20 --out-dir mi
```

未设 `STATS_ROOT` 时，脚本默认把仓库根（本文件的上一级）加入 `sys.path`。`downsample_train.py` 默认 `DRY_RUN=True`。
