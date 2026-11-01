# data-processing/clean-data — 临床数据表（cln/rad/qc）统一脚本库

`data-processing\clean-data\` 是 02 的分析前准备：txt / Excel 的提取、清洗、整理。  
**扁平布局**：脚本按**作用**命名，直接放在 `data-processing\clean-data\` 下；不建课题子工程目录。  
课题差异用 `--profile` / 环境变量 / 文件顶部 CONFIG，不靠文件夹分叉。

临时文件 → `D:\0del\`（`{DEL}`；`tidy_paths.DEL_ROOT`，可用环境变量 `DEL_ROOT` 覆盖）；要留下的说明和表 → 课题 `ref/`。

## 布局（现用）

| 文件 | 作用 |
|---|---|
| `tidy_paths.py` | 统一根目录 / profile 环境变量 / 0del |
| `tidy_profiles.py` | lxj_AN / ssd 声明式课题配置 |
| `tidy_io.py` | Excel 读写与规范化 |
| `tidy_yt_rdl.py` | yt_RDL 路径与 `read_yt_cln`（非子包） |
| `tidy_merge.py` | 合并（`--profile` / `--strategy`） |
| `tidy_complete.py` | 补全 |
| `tidy_verify.py` | 校验 |
| `tidy_split.py` | SSD 模态拆分 |
| `extract_discharge.py` | 出院小结 txt → 宽表 CSV |
| `screen_liposarcoma.py` | 脂肪肉瘤候选筛选（`--audit`） |
| `followup_recurrence.py` | 复发随访列（`--audit`） |
| `enrich_candidate_clinical.py` | 候选表并入临床/病理列 |
| `fill_cln_followup.py` | 随访列回填 cln（`--audit` / `--mrn` / `--mrn-only`） |
| `fill_cln_mrn.py` | 单独追加病历卡号（也可经 `fill_cln_followup --mrn`） |
| `fill_cln_from_discharge.py` | 从出院小结填判读列 |
| `impute_cln_en.py` | cln 插补 + 列名英文化（默认 lya_WML） |
| `align_cln_to_rad.py` | cln 对齐 rad ID + 多源空白填充（门禁；默认 xlm_LG，可改 CONFIG） |
| `fill_cln_from_supplement.py` | 从补充表/2nd sheet 填 cln 空白（门禁；默认 xlm_LG） |
| `setup_cln_only.py` | clin-only ID + settings.ini（默认 wkl_UGIB） |
| `_selftest/` | 旧→新等价性夹具（测试用，非课题子工程） |

统计/验证 CLI 在算法库主库 **`%GROK_ROOT%\0doc\GitHub\stats-analysis\model-validation\`**（`compare_val_modes.py`、`downsample_train.py`），见该目录 `README.md`。算法包本体 `%GROK_ROOT%\0doc\GitHub\stats-analysis\modules\`（2026-10-25 起 `_code` 副本退役）。

病历 / HIS 抽取脚本在 `..\clinical-data-extraction\`。

已删除的课题子目录：`yt_RDL/`、`lxf_RP/`、`lya_WML/`、`wkl_UGIB/`、`zyp_OV/`。

## 快速开始

```bat
set PYTHONDONTWRITEBYTECODE=1
set PYTHONPATH=%GROK_ROOT%\0doc\_code\02-data-processing/clean-data

:: ── lxj_AN / SSD 主链 ──
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_merge.py    --profile lxj_AN
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_complete.py --profile lxj_AN
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_verify.py   --profile lxj_AN
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_complete.py --profile lxj_AN --layout cln-rad-qc

python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_merge.py    --profile ssd
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_complete.py --profile ssd --strategy paper_fill
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_verify.py   --profile ssd
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_complete.py --profile ssd --strategy impute
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_split.py

:: ── yt_RDL（路径：YT_RDL_ROOT / tidy_yt_rdl）──
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\extract_discharge.py
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\screen_liposarcoma.py
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\screen_liposarcoma.py --audit
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\enrich_candidate_clinical.py
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\followup_recurrence.py
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\fill_cln_followup.py --out <副本.xlsx>
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\fill_cln_followup.py --mrn
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\fill_cln_from_discharge.py --validate
```

每个脚本 `--help` 可看全部参数。路径类参数优先环境变量，其次文件顶部 CONFIG。

## 策略一览

`tidy_merge.py`

| strategy | profile | 做什么 | 主要产物 |
|---|---|---|---|
| `keyed_union` | `lxj_AN` | 按主键并集，冲突留痕 | `<root>/lxj_AN.xlsx` |
| `source_records` | `ssd` | 多源长记录回填 | `clinical_recovery/…` |
| `modality_outer` | — | 两模态 Name 外连接 | `--out` |

`tidy_complete.py`

| strategy | profile | 做什么 |
|---|---|---|
| `gate` | `lxj_AN` | 行/列完整性门槛 |
| `impute` | `ssd` | 按 Group 分组插补 |
| `paper_fill` | `ssd` | 论文感知补缺 |

`tidy_verify.py`

| strategy | profile | 做什么 |
|---|---|---|
| `cell` | `lxj_AN` | 主表逐格回查源 `.xls` |
| `paper` | `ssd` | vs 论文表 1 |

## 数据契约：cln / rad / qc

下游 RAD console 读 `CLIN_SHEET="cln"` / `RAD_SHEET="rad"` / `QC_SHEET="qc"`，ID 列名 `Pateint_ID`（拼写沿用 settings.ini，故意不改），结局列 `Group`。

`tidy_complete.py --layout cln-rad-qc` 按此约定切表。

## 路径与环境变量

```
子工程根 = <PROFILE_ENV> → 设了 ORAD_ROOT 类变量时 <ORAD_ROOT>\<目录名> → 否则 %GROK_ROOT%\<阶段>\<目录名>（找不到或重名即报错）
ORAD_ROOT = ZERAD_ROOT → ORAD_ROOT → MS_ORAD_ROOT → %GROK_ROOT%\drafting（不存在即报错）
0del      = D:\0del（DEL_ROOT 可覆盖；与脚本备份同址）
```

| profile | 环境变量 | 目录名（在阶段目录下查找） |
|---|---|---|
| `lxj_AN` | `LXJ_AN_ROOT` | `lxj_AN` |
| `ssd` | `SSD_ROOT` | `jfl_SSD_ASL` |
| `ssd_dti_ivim` | `SSD_DTI_IVIM_ROOT` | `jfl_SSD_DTI_IVIM` |
| `ssd_dki` | `SSD_DKI_ROOT` | `jfl_SSD_DKI` |
| `yt_RDL` | `YT_RDL_ROOT` | `yt_RDL` |
| `lxf_PP` | `LXF_PP_ROOT` | `lxf_RP`（源表 `lxf_LG.xlsx`） |
| `lya_WML` | `LYA_WML_ROOT` | `lya_WML` |
| `wkl_UGIB` | `WKL_UGIB_ROOT` | 脚本默认 `%GROK_ROOT%\in-review\wkl_UGIB` |
| `zyp_OV` | `ZYP_OV_ROOT` | `zyp_OV` |

请带 `PYTHONDONTWRITEBYTECODE=1` 运行。

## 等价性验证

`_selftest/` 存放 lxj_AN / ssd 逐格核对工具与边界用例，命令见该目录 `README.md`。

## 已知注意

* `tidy_verify --profile lxj_AN` 默认始终返回 0；加 `--fail-on-diff` 才在失败时返回 1。
* 统计脚本不在本目录：见 `%GROK_ROOT%\0doc\GitHub\stats-analysis\model-validation\`。
