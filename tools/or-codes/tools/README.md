# `_Codes\tools`：工程加工脚本（按功能归组）

来源：`G:\<project>\utility\`（已全部迁出，工程侧不再保留 `utility\`）。

**为什么按工程分子目录**：同名脚本在各工程间**逻辑不同**（把路径与工程名抹平后哈希仍不同），
合并会改变行为，所以按功能归组、保留各自版本。

## 归组

| 组 | 文件 | 来自工程 | 作用 |
|---|---|---|---|
| `match\` | `match_img_ior_updated.py`（工程侧薄壳） | fyh_CAC、fyh_CCEC、ybc_EC、xlm_LUNG、llj_OV、zyp_OV、yt_RDL、jfl_SSD | 影像 ↔ ROI 配对，生成 `combined.csv`（含 fuzzy 兜底与 match_method 标注）。**实现已合并**：4 种形态在 `match\_shared\shape_a..d.py`，工程侧各 10 行薄壳，配置读 `MATCH_CONFIG` |
| `match\` | `match_roi_ovdll.py` | dll_OV | dll_OV 专用配对（命名习惯不同） |
| `cleanup\` | `delete_files_by_keyword.py`（1 共享实现 + 6 工程薄壳） | fyh_CCEC、ybc_EC、xlm_LUNG、llj_OV、zyp_OV、yt_RDL | 按患者 ID + 关键词清理 `results\checkpoints`。实现 `cleanup\_shared\cleanup_core.py`；工程侧 10 行薄壳（server.py 按 `tools/<组>/<工程>/` 扫描动作）；关键词读 `settings.ini` 的 `CLEANUP_KEYWORDS` |
| `adc\` | `batch_adc.py` | ybc_EC、dll_OV | 由 DWI0/DWI1 拟合 ADC 图 |
| `roi\` | `expand_roi_5mm.py` + `expand_roi_5mm_methods_only.docx` | xlm_LUNG | ROI 外扩 5 mm（附方法说明稿） |
| `dicom\` | `extract_dll_fold.py` | dll_OV | 按序列抽取 DICOM 目录结构 |
| `plot\` | `plot_global.py` | yt_RDL | 全局特征/分布绘图 |
| `fit\` | `dki_fit.py`、`ivim_fit.py`、`extract_data_by_roi.py` | jfl_SSD | DKI / IVIM 参数图拟合 + 按 ROI 提取双侧区域均值 |
| `registry\` | `refresh_g_root_readme.py` | —（工具，不对应工程） | 刷新 `G:\README.md` 的「现有工程登记」表；`--check` 可校验与实盘是否一致 |
| `audit\` | `l_audit.py`、`l_path_map.py`、`l_offload.py` | —（工具，L 盘） | L 盘结构与路径卫生：`l_audit` 体检（工程名/字符集/层深/`image`-`roi` 分层/`_index.csv` 漂移）；`l_path_map` 抓写死的 `L:` 路径并给改名提案（`--no-rename` = 只回当前实存路径）；`l_offload` 把表/结果件/垃圾外迁到 `G:\_offload\<batch>\`（默认 dry-run，`--execute` 才动，8 线程复制 + 台账） |
| `match\lxf_LUNG\` | `build_lung_pairs_auto.py` | lxf_LUNG | 由 nnU-Net 预测掩膜 + `5mm_nii` 生成 delta-habitat 配对表（`name,exam,img_path,roi_path`，默认 dry-run，`--execute` 才写盘） |
| `lxf_LUNG\timesfm\` | `files.zip`（4 件：`run_lung_recist_timesfm.py` / `extract_lung_features.py` / `config.yaml` / `README.md`） | lxf_LUNG | 自研 TimesFM 时序脚本，保持压缩态；用时解到工程 `ref\tmp` |
| `lxf_LUNG\radiomics\` | 6 个：`generate_global_habitat.py` / `LASSO.py` / `line_ROC.py` / `plot_global.py` / `plot_individual.py` / `validate_delta_habitat.py` | lxf_LUNG | delta 生境组学的建模与绘图；顶部常量配置（未走 `tool_ini`）；迁入时改 5 处路径，见该夹 README |
| `lxf_LUNG\qc\` | 3 个：`delete_files_by_keyword.py` / `folds_files_match.py` / `log_reader.py` | lxf_LUNG | 旧 QC 脚本（与 `cleanup\` 及 `habitat_core\tools\log_reader.py` 不是同一份，按工程保留） |
| `lxf_LUNG\clinical\` | 9 个：`extract_beng1_file.py` / `extract_beng1_fold .py` / `compress_patient_data.py` / `extract_jinshan_data.py` / `supplement_cln.py` / `query_patient.py` / `convert_dcm2nii.py` / `csv_IDs_proc_tool.py` / `extract_ct_info.py` | lxf_LUNG | 三中心临床抽取与文本整理；路径均绝对，未用 `__file__` / cwd；`query_patient.py` 为 HIS 抓取**唯一实现**（`--mode category` 按 24 类抓历史报告对比，`--mode recent` 为旧的最近 N 条） |
| `convert\` | `decompress_niigz.py` / `convert_dcm2nii.py` / `diocmdir.py` | 通用 | `.nii.gz` 批量解压 / DICOM→NIfTI / DICOMDIR 按 **PatientID**（非姓名）整理目录，去 PHI |
| `extract\` | `collect_txt_info.py` / `csv_IDs_proc_tool.py` / `extract_IDs.py` / `extract_LSTlesion_data.py` | 通用 | txt 字段汇总 / CSV 聚合清理压缩 / 出院小结解析 / LST 报告病灶提取（HIS 抓取已并入 `lxf_LUNG\clinical\query_patient.py`，此处不再留副本） |
| `file_ops\` | `copy_files.py` | 通用 | 按关键词把一级子目录文件复制到另一根（原 brain_nii_ZJL/HE_nii_ly 两份同构脚本合并，参数化） |
| `lst\` | `LST_T2WI.m` | 通用 | SPM LST 病灶分割批处理（`root_dir` 参数化；原两份合并取增强版） |
| `radiomics\` | `extract_features.py` / `pictologics_extract.py` / `README.md` | 通用 | pyradiomics / pictologics 特征提取；**变体由 ini/CLI 控制**，见该夹 README |
| `stats\` | `z_impute.R` / `ec_radiomics_model.R` | 通用 | MICE/missForest 插补 / 放射组学建模（LASSO+logistic+nomogram+DCA+heatmap） |
| `<leaf>\`（现仅 `MRS\`、`wxt_ASL\`） | `MRS` 4 个、`wxt_ASL` 18 个 | 各对应 L 盘数据叶子 | 课题专用方法套件（波谱 / ASL 灌注），保留在叶子目录。原 13 个叶子中通用脚本已归组（`convert`/`extract`/`file_ops`/`lst`/`radiomics`/`stats`），一次性脚本已移 `{DEL}`（批次 `code-cleanup-20261119`） |

## 路径与配置（已 ini 化）

脚本顶部不再有绝对路径，全部读工程 `settings.ini` 的 `[tools]` 段：

```python
from habitat_core import tool_ini as _tk
CLEANUP_TARGET_DIR = _tk.get("CLEANUP_TARGET_DIR")   # 缺键直接报错
```

调用时指定工程（三种等价写法）：

```powershell
python "{or_codes}\tools\cleanup\zyp_OV\delete_files_by_keyword.py" --project zyp_OV
python "{or_codes}\tools\cleanup\zyp_OV\delete_files_by_keyword.py" --ini G:\OC\zyp_OV\settings.ini
$env:HABITAT_PROJECT='zyp_OV'; python ...\delete_files_by_keyword.py
```

`--project` / `--ini` 由 `habitat_core.tool_ini` 摘走，脚本自己的 argparse 看不到。

| ini 键 | 用于 |
|---|---|
| `MATCH_CONFIG` | 影像 ↔ ROI 配对（tasks / output_dir / 关键词） |
| `CLEANUP_TARGET_DIR`、`CLEANUP_ERR_CSV`、`CLEANUP_KEYWORDS` | 清理 checkpoints（关键词缺省 `["aligned","habitat","features"]`，yt_RDL 为 `["habitat"]`） |
| `ADC_ROOT_DIR` | DWI→ADC 拟合根 |
| `FIT_DKI_ROOT_DIR`、`FIT_IVIM_ROOT_DIR` | DKI / IVIM 拟合根 |
| `PLOT_MODEL_PATH`、`PLOT_POOLED_PATH`、`PLOT_OUTPUT_DIR` | 绘图输入与输出 |
| `ROI_DEFAULT_PATH`、`ROI_LOG_DIR`、`ROI_CHECKPOINT_DIR` | ROI 外扩 |

也可以在控制台 `settings.html` → 「运行」页直接点按钮跑（走白名单动作，需二次确认）。
`match` / `roi` 默认 `--dry-run`（`actions.ini` 的 `[match]` / `[roi]` 段控制）：
`match` 只统计匹配、不写 `combined/unmatched/report`；`roi` 只打印将生成的 ROI 路径，**不建目录、不写盘**。

## 运行

```powershell
python "{or_codes}\tools\match\fyh_CAC\match_img_ior_updated.py" --project fyh_CAC
```

删除类脚本默认 dry-run，要真删必须显式关掉各自文件顶部的开关。

