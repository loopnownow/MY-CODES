# stats-analysis/model-validation — 统计 / 验证课题 CLI

`stat/` 放**可复跑的统计与验证脚本**（顶部 CONFIG、绑课题），不是清洗主链，也不是 `modules/utils` 库。

| 目录 | 职责 |
|---|---|
| `data-processing/clean-data/` | raw→表（合并 / 补全 / 校验 / 抽取） |
| `stats-analysis/model-validation/` | 建模队列、VAL_MODE 对比等统计 CLI |
| `modules/utils/` | 无课题路径的库函数（`u_impute` 等）— **不接收本目录脚本** |

路径解析复用 `data-processing/clean-data/tidy_paths.py`（运行时把 `STATS_ROOT` 与 `CLEAN_DATA_ROOT` 加入 `sys.path`）。

## 脚本

| 文件 | 作用 | 默认课题 |
|---|---|---|
| `compare_val_modes.py` | 一次 Train 拟合，三种 VAL_MODE 验证对比 | `zyp_OV`（`ZYP_OV_ROOT`） |
| `downsample_train.py` | train 分层下采样（1:2 Negative） | `lxf_LG`→`lxf_PP`（`LXF_PP_ROOT`） |

## 运行

```bat
set PYTHONDONTWRITEBYTECODE=1
set PYTHONPATH=D:\0Grok\0doc\_code\04-analysis\stats-analysis

python D:\0Grok\0doc\_code\04-analysis\stats-analysis\model-validation\compare_val_modes.py
python D:\0Grok\0doc\_code\04-analysis\stats-analysis\model-validation\downsample_train.py
```

改课题根目录用同名环境变量，或改脚本顶部 CONFIG。`downsample_train.py` 默认 `DRY_RUN=True`。
