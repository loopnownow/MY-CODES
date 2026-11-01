<!-- HISTORICAL 2026-10-23: archival path mentions below are not live SSOT. -->
# clean-data/_selftest — 旧→新等价性核对工具

这些脚本用来证明 `clean-data/` 合并后的行为与旧脚本**逐格一致**。它们不是生产代码，
不参与 `clean-data/` 的运行链路。

| 文件 | 用途 |
|---|---|
| `xlsx_diff.py A.xlsx B.xlsx` | 两个工作簿**逐格**比对（所有表、所有单元格） |
| `text_diff.py A B` | 两个文本/CSV 文件逐行比对（unified diff） |
| `hdr_diff.py A.xlsx B.xlsx` | 只比表头与形状（用于核对 cln/rad/qc 契约） |
| `make_ssd_fixtures.py <root>` | 生成 SSD 侧的合成夹具（主库、0list、合并结果、SSD.xlsx、SSD_imputed.xlsx、DTI_IVIM） |
| `strip_optional_cols.py <root>` | 删掉 `height_cm`/`weight_kg`/`随访备注`，回归"可选列整列缺失"这一分支 |
| `make_c4_ws_orphan.py <root>` | 把孤儿行的 `subject_id` 改成空白串，回归 `pd.notna` vs `is_blank` |
| `make_nat_case.py <root>` | 把 `exam_date` 变成含 `NaT` 的 datetime 列，回归 `pd.isna` vs `is_blank` |
| `make_outer_inputs.py <SSD_imputed.xlsx> <outdir>` | 把容器里的 DTI / IVIM 抽成单表文件，用于测 `--strategy modality_outer` 的 CLI |

## 复现方式（lxj_AN，需真实数据）

> 旧链脚本已不在磁盘上（`0del` 已清空），旧→新逐格核对已在合并时完成。
> 下面只保留新链命令；`<scratch>\old` / `<scratch>\ssd_old` 放已有的参照输出。

`lxj_AN` 的等价性核对需要 `%GROK_ROOT%\drafting\lxj_AN\ref\` 下的 3 个 `.xls`。
把源文件复制到两个独立根目录（各自带 `ref\`），然后分别用旧脚本与新脚本跑：

```bat
set PYTHONDONTWRITEBYTECODE=1
set LXJ_AN_ROOT=<scratch>\new
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_merge.py    --profile lxj_AN
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_complete.py --profile lxj_AN
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_verify.py   --profile lxj_AN

python _selftest\xlsx_diff.py <scratch>\old\lxj_AN.xlsx          <scratch>\new\lxj_AN.xlsx
python _selftest\xlsx_diff.py <scratch>\old\lxj_AN_complete.xlsx <scratch>\new\lxj_AN_complete.xlsx
```

## 复现方式（SSD，合成夹具即可）

```bat
set PYTHONDONTWRITEBYTECODE=1
:: modules 包由 tidy_*.py 内的 STATS_ROOT 自行解析（默认 GitHub\stats-analysis），无需 PYTHONPATH
python _selftest\make_ssd_fixtures.py <scratch>\ssd_old
xcopy /E /I <scratch>\ssd_old <scratch>\ssd_new

:: 新链
set ORAD_ROOT=<scratch>\ssd_new
set SSD_ROOT=<scratch>\ssd_new
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_merge.py    --profile ssd
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_complete.py --profile ssd --strategy paper_fill
python %GROK_ROOT%\0doc\_code\02-data-processing/clean-data\tidy_verify.py   --profile ssd

python _selftest\xlsx_diff.py <scratch>\ssd_old\clinical_recovery\clinical_real_merged.xlsx ^
                              <scratch>\ssd_new\clinical_recovery\clinical_real_merged.xlsx
```

`impute` 与 `split` 需要各自的 `SSD_DTI_IVIM_ROOT` / `SSD_DKI_ROOT`（夹具里是
`DTI_IVIM_SRC` 与 `DTI_OUT`/`DKI_OUT`），见 `clean-data/README.md` 的"等价性验证"一节。

### 边界用例（都做过旧→新逐格核对）

```bat
:: ① 可选列整列缺失
python _selftest\strip_optional_cols.py <scratch>\ssd_old
:: ② 孤儿行 subject_id 是空白串
python _selftest\make_c4_ws_orphan.py <scratch>\ssd_old
:: ③ exam_date 为含 NaT 的 datetime 列
python _selftest\make_nat_case.py <scratch>\ssd_old
```

每个用例都要把 `<scratch>\ssd_old` 复制成 `<scratch>\ssd_new` 再跑新链，与参照输出比对，
最后比 `clinical_real_merged.xlsx`、`clinical_filled_paper_aware.xlsx`、`source_long.csv`。

> 把 `ORAD_ROOT` 指到夹具目录是为了让 `GROK_ROOT\0del`（插补备份）落在工作区内；
> 真实运行时 `ORAD_ROOT` 指向 `%GROK_ROOT%\drafting` 即可，`modules` 由 `PYTHONPATH`
> 或 `%GROK_ROOT%\0doc\GitHub\stats-analysis\modules` 提供。
