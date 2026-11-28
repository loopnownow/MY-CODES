# `tools\nnunet`：nnU-Net v2 数据集构建与推理工具

按工程变体走 CLI / 环境变量，不复制脚本。五个入口，各自 `--help` 可查全部参数。

| 脚本 | 作用 | 默认行为 |
|---|---|---|
| `build_dataset.py` | 由 L 叶 `img/roi` 建 nnUNet raw 数据集（imagesTr / labelsTr / dataset.json / splits_final.json / BUILD-MANIFEST.tsv）；支持 `--include-table`（只收 MASK-QC 的 A 档）与 `--flat-root`（平铺源配对，去 `i` 后缀）；写盘时把非 gzip 源规范化成 gzip | **dry-run**，`--execute` 才写 |
| `check_pairs.py` | 校验 raw 数据集：配对、几何、label 值域、dataset.json、splits 覆盖与患者泄漏 | 只读；`--sha256` 加哈希复核 |
| `predict_zs.py` | 中山（ZS）多序列推理：staging → `nnUNetv2_predict` → 收集概率图 | **dry-run**，`--execute` 才推 |
| `select_primary.py` | 按 `series_desc` 分期 → 选 primary / check → 跨序列 Dice → 定主 mask 与 flag | 只写台账，不碰 L 盘 |
| `cross_dice.py` | 跨序列一致性 Dice 分布（分组：plain-venous / plain-unknown / plain-plain / plain-arterial），阈值取最大组 p10 | 只读 |
| `apply_to_l.py` | 把选中的 primary 预测写入 `L:\...\roi\<sample>\<SEQ>_label.nii.gz`，几何断言 + flag + `ROI-LEDGER.tsv` | **dry-run**，`--execute` 才写 |
| `mask_qc.py` | 逐 mask 算 z 方向剖面（首末/峰值比、断层、碎片层），分 A 完整 / B 截断 / C 低质 | 只读；给 `--out` 才写台账 |
| `align_mask_to_image.py` | 核对 mask 与 image 网格（shape/zooms/sform），不一致时最近邻重采样 | **dry-run**，`--execute --dst-root` 才写 |
| `redo_truncated_series.py` | 截断 gzip 的 image 从源盘 DICOM 重转（pydicom 排序 + 对齐参考网格，坏 dcm 容错） | **dry-run**；`--execute` 或 `--apply-only` 才回写 |

## 关键约定

1. **案例名**：`<dst_prefix>_NNNN`，由源样本键末四位派生（`zjl_CT_GW_0001` → `RLPS_GW_0001`）。
2. **排除名单不写在代码里**：`build_dataset.py` 从 ROI/IMAGE QC 表读 `verdict == BLANK` 的样本（`--qc-table`），
   保证排除项与 QC 台账同源。该表后缀是 `.tsv` 但内容是**逗号分隔**。
3. **splits 按患者分组**：`--seed` 固定，`patient_hash` 来自工程 `ref\data\*_study-index.csv`；
   同一患者两次检查不会同时出现在 train / val（`check_pairs.py` 会复核）。
4. **路径必须 ASCII**：nnUNet 读写 nii 走 SimpleITK，含中文的根会 `RuntimeError`。
   `E:\_models\nnUNet\zjl_RDL` 是指向 `E:\_models\nnUNet\其他\zjl_RDL` 的 junction，env 脚本只用 ASCII 这一侧。

## 环境

```powershell
. E:\_models\nnUNet\zjl_RDL\_qc\env_nnunet_zjl.ps1   # 三个 nnUNet_* 根 + PATH
python {or_codes}\tools\nnunet\build_dataset.py            # dry-run
python {or_codes}\tools\nnunet\build_dataset.py --execute  # 落盘
```

## 已落地实例

| 日期 | 数据集 | 规模 | 台账 |
|---|---|---|---|
| 2026-11-25 | `Dataset002_RLPS_CT` | 267 例 / 259 患者 / 6.24 GiB | `_qc\BUILD-MANIFEST-20261125.tsv`、`_qc\splits_summary.json`、`_qc\CHECK-PAIRS-20261125.tsv` |
| 2026-11-26 | `Dataset004_RLPS_CT_full`（A 档 60 GW + 116 YT 平铺） | 176 例 / 176 患者 / **3.72 GiB** | `_qc\BUILD-MANIFEST-20261126.tsv`、`_qc\CHECK-PAIRS-20261126.tsv`、`_qc\SMOKE-20261126.md` |
| 2026-11-26 | RDL 源盘 mask 质量普查（未建数据集） | 2,930 个 mask / 4 组 | `ref\code-gates\MASK-QC-20261126.tsv`、`ALIGN-20261126.tsv` |

## 关键约定（补）

5. **截断 mask 不能直接当 3D GT**：本队列源的 ROI 中位只勾 10–12 层（覆盖率 12%–21%），
   首末层面积仍是峰值的 68%–84%。用这类 label 训 3D 模型，模型会学成"肿瘤只有十几层"。
   训练前必须先 `mask_qc.py` 分档，A 档直接可用、B 档需模型补全、C 档需人工重勾。
6. **mask 与 image 不一定同网格**：实测 CT_ZS 19%、MRI_ZS 17% 配对差 1–4 层。
   训练/推理前一律先跑 `align_mask_to_image.py`；重采样只许最近邻（`order=0`）。
7. **不用 SimpleITK 处理中文路径**：`align_mask_to_image.py` 全程 nibabel，中文 UNC 可读；
   若必须用 SimpleITK，先把文件拷到 ASCII 目录。
8. **平铺源可能不是 gzip**：`--flat-root` 直读的 `.nii` 常是**未压缩** NIfTI（实测 `L:\RDL\yt_RDL` 118/118 首两字节 `5c01`，而 GW 叶是 `1f8b`）。
   写盘时按源 magic 判定，非 gzip 一律 `gzip level 6` 规范化并解压回读校验字节数；否则产物会用未压缩字节顶 `.nii.gz` 名，
   nnU-Net（SimpleITKIO 嗅探 magic）能读，但 `check_pairs / mask_qc / align_mask_to_image` 这条 nibabel 链全部 `is not a gzip file`。
   顺带收益：176 例集合由 10.74 GiB 降到 3.72 GiB。

本组不含一次性修补脚本；一次性脚本留 SHA 台账后进 `{DEL}`（`del-and-harvest.md` §2 / §6）。
