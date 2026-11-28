# lxf_LUNG\radiomics · 组学加工脚本

> 2026-11-16 从 `E:\lxf\utility\` 整体迁入（工程侧 `utility\` 已撤）。自研脚本，非上游。

## 1 清单

| 脚本 | 作用 | 配置入口 |
|---|---|---|
| `generate_global_habitat.py` | 跨队列全局 habitat 模型 + K 优化（读各结果树 `01_preprocessed`） | 顶部 `CFG` |
| `LASSO.py` | 近零方差 → 相关去冗余 → LassoCV → 选中特征 | 顶部 `CFG` |
| `line_ROC.py` | 多变量逻辑回归 + ROC（AUC 1000 次 bootstrap CI） | 顶部 `CFG` |
| `plot_global.py` | 全局 habitat 的 PCA / 小提琴 / 体素数图 | 顶部常量 |
| `plot_individual.py` | 单例 habitat 叠加图与强度分布 | 顶部 `OUTPUT_ROOT` |
| `validate_delta_habitat.py` | delta 一致性校验（占位路径，跑前改） | 顶部常量 |

## 2 配置机制（与 tools 通则的偏差）

本组脚本用**文件顶部常量**，未走 `habitat_core.tool_ini`：这批脚本属 lxf_LUNG，该工程流水线仍是 `config.py` 驱动（未 INI 化）。等工程并入 `settings.ini` 后，把顶部常量换成 `_tk.get()` 即可，逻辑不动。

## 3 路径映射（2026-11-16 迁移时实际改的）

| 脚本 | 旧 | 新 |
|---|---|---|
| `generate_global_habitat.py` | `E:\lxf\0old_files\train` | `G:\LC\lxf_LUNG\old_train` |
| `plot_global.py` | `E:\lxf\0old_files\train\xa\*` | `G:\LC\lxf_LUNG\old_train\xa\*` |
| `LASSO.py` | `results_xa/06_selected`、`G:\LC\lxf_LUNG\results_xa\05_delta\…`、`E:\lxf\QC_train.csv` | `{lab_root}\pre-submit\lxf_LUNG\results\radiomics\all\06_selected`、`…\all\05_delta\…`、`…\ref\code-gates\QC_train-beng-pipeline-507.csv` |
| `line_ROC.py` | 同上三处 + 文档串 | 同上；文档串改 raw docstring |

未改：`plot_individual.py` 的 `OUTPUT_ROOT = G:\LC\lxf_LUNG\results_xa`（该树仍在 E:，逐例 `05_delta` 目录只在 E:）；`generate_global_habitat.py` 的 `habitat_module_path = E:\lxf\delta_habitat_pipeline`（等并入 `habitat_core` 后再改）。

## 4 已知占位

`validate_delta_habitat.py` 的 `E:\lxf\val\list.csv` / `model_dir` / `clf_path` 是占位路径，实际校验前按当次导出目录改。

## 5 校验

6 个脚本 `ast.parse` + `-W error::SyntaxWarning` 全过；`line_ROC.py` 文档串已改 raw（否则 `\M`、`\a` 会被当转义）。