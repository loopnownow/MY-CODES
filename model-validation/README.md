# stats-analysis/model-validation — 统计 / 验证课题 CLI

本目录放**可复跑的统计与验证脚本**（顶部 CONFIG、绑课题），不是清洗主链，也不是 `modules/utils` 库。

| 位置 | 职责 |
|---|---|
| `model-validation\*.py`（**扁平，无子目录**） | 建模队列、VAL_MODE 对比等统计 CLI |
| `D:\0Grok\0doc\_code\02-data-processing\`（02 整理/清洗树，**不在本仓**） | raw→表（合并 / 补全 / 校验 / 抽取） |
| `modules\utils\` | 无课题路径的库函数（`u_impute` 等）— **不接收本目录脚本** |

2026-10-26 核对：本目录实际只有 `compare_val_modes.py` / `downsample_train.py` 两个脚本，**没有** `stat/`、`data-processing/clean-data/` 子目录（旧版本此节写错）。`tidy_paths.py` 属 `_code\02-data-processing\` 共享清洗树，不在本仓。

## 脚本

| 文件 | 作用 | 默认课题 |
|---|---|---|
| `compare_val_modes.py` | 一次 Train 拟合，三种 VAL_MODE 验证对比 | `zyp_OV`（`ZYP_OV_ROOT`） |
| `downsample_train.py` | train 分层下采样（1:2 Negative） | `lxf_LG`→`lxf_PP`（`LXF_PP_ROOT`） |

## 运行

```bat
set PYTHONDONTWRITEBYTECODE=1
set PYTHONPATH=D:\0Grok\0doc\GitHub\stats-analysis

python D:\0Grok\0doc\GitHub\stats-analysis\model-validation\compare_val_modes.py
python D:\0Grok\0doc\GitHub\stats-analysis\model-validation\downsample_train.py
```

改课题根目录用同名环境变量，或改脚本顶部 CONFIG。`downsample_train.py` 默认 `DRY_RUN=True`。
