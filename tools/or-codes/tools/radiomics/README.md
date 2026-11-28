# `tools\radiomics`：影像组学特征提取（通用，变体由 ini/CLI 控制）

本组由多个**同源散脚本**综合而来。原先是各课题目录下的一次性脚本，逻辑 90% 相同
（`RadiomicsFeatureExtractor(params).execute(image, roi)` → 汇总 csv/xlsx），差异只在
**image 发现方式 / ROI 定位规则 / Params.yaml / 扫描根 / 输出格式**。

## 约定（re-rule：新增变体只加参数，不改脚本）

> **新增一个放射组学提取任务时，优先用下列 CLI 参数组合或写进工程 `settings.ini` 的
> `[tools]` 段，不要复制新脚本。** 只有出现全新 ROI 定位形态时才在 `resolve_roi()` 里加一条规则。

## 1 `extract_features.py`（pyradiomics）

ROI 规则（`--roi-rule`）：

| 规则 | 含义 |
|---|---|
| `sibling_suffix:<sfx>` | 同目录，image 换成 `<sfx>`（`Z1.nrrd` / `Z2.nrrd` / `Z.nrrd`） |
| `sibling_strip:<tok>` | 同目录，image 名去掉 `<tok>`（`.Merge`） |
| `sibling_replace:<a>:<b>` | 同目录，image 名把 `<a>` 换成 `<b>`（`original:ROI`、`.nii:.Merge.nii`） |
| `roi_dir:<dir>[:<sep>]` | 独立 ROI 目录取同名；给 `<sep>` 取文件名按空格第 `<sep>` 段 |

其它：`--root` 扫描根、`--image`（可多次，缺省 `*.nii`）、`--exclude`（文件名黑名单）、
`--params` Params.yaml、`--out`（`.xlsx`/`.csv`）、`--dry-run`。

### 原 9 变体 → 新 CLI 映射

| 原脚本（已移 `{DEL}`） | CLI 组合 |
|---|---|
| `EC_ybc\...\processZ1.py` | `--image T1.nrrd --image T2.nrrd --image DWI0.nrrd --image DWI1.nrrd --image C.nrrd --image ADC.nrrd --roi-rule sibling_suffix:Z1.nrrd` |
| `EC_ybc\...\processZ2.py` | 同上，`--roi-rule sibling_suffix:Z2.nrrd` |
| `ECtxt\ICC\process.py` | `--image T2.nrrd --image DWI1.nrrd --image C.nrrd --image ADC.nrrd --roi-rule sibling_suffix:Z.nrrd` |
| `ECtxt\ICC\meta-radiomics2.py` | `--image T2.nrrd --roi-rule sibling_suffix:Z.nrrd`（递归子目录） |
| `OV_zyp\zyp.py` | `--root E:\ADATA\OV --image *.nii --exclude *.Merge.nii --roi-rule sibling_replace:.nii:.Merge.nii` |
| `RDL_yt\yt.py` | `--root E:\ADATA\yt --image *original*.nii --roi-rule sibling_replace:original:ROI` |
| `MRS\PCOS_zar\ovov.py` | `--root E:\ADATA\OVOV\OV --image *.nrrd --roi-rule roi_dir:E:\ADATA\OVOV\ROI:1`（原按**路径**空格第 2 段，通用版按**文件名**第 2 段，需核对） |
| `MRS\PCOS_zar\OV-his -new.py` | `--root . --image *.nrrd --roi-rule roi_dir:<绝对ROI目录>:1`（同上，需核对） |
| `lung_xlm\lung-meta-radiomics.py` | 原为**双目录 + seg→image 替换**（image 在 `./iccimg`、mask 在 `./icc1`）；通用版无此规则，需先整理成同目录或按需扩展 `resolve_roi()` |

> 后三个（ovov/OV-his/lung-meta）原是脆弱的课题定制逻辑；通用版给出最接近的 CLI，
> 复跑前须对着原输出核对配对是否正确。

## 2 `pictologics_extract.py`（pictologics，IBSI）

两种输入模式，共享 `PIPELINE_STEPS` 与提取逻辑：

| 模式 | 参数 | 来源 |
|---|---|---|
| `--mode csv` | `--csv pairs.csv` | 列 0=image、1=ROI、2=label（可选） |
| `--mode scan` | `--input <dir> --roi-prefix ples_lpa_m --roi-suffix .nii` | 扫描目录，ROI 前缀配同名 image，label=子文件夹名 |

原 `brain_nii_ZJL\codes\pictologics_run.py` → `--mode csv`；
原 `HE_nii_ly\utili\pictologics_LST.py` → `--mode scan`。

依赖：`pictologics>=0.4.1`（另有 `pyradiomics` 用于 `extract_features.py`）。
