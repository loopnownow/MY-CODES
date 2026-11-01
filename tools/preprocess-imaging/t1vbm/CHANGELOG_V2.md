# T1 VBM V2 稳定化版本

本版本在原始 `T1_vbm` pipeline 基础上完成 1–15 项优化，重点不是重写 CAT12/SPM，而是建立可靠的输入—预处理—统计—报告闭环。

## 核心变化

1. 修正 FWE 阈值计算后未参与结果筛选的问题。
2. `kMin` 真正作用于 cluster extraction。
3. CAT12 完成判定从“mwp1 存在”升级为 completion marker + mwp1/mwp2/y_ 输出 QC。
4. 统一 NIfTI basename 处理，避免 `.nii.nii` 等路径错误。
5. 新增 `manifest/subject_manifest.csv` 和 `.mat`，作为全流程 subject-level 单一状态源。
6. 新增 `pipeline_state.mat`，记录 step 状态、失败节点和时间。
7. CAT12 监督 timeout 参数化，并按并行槽位计算批次预算。
8. 新增 G0–G4 QC 思路的实际检查：输入、CAT12、设计矩阵、阈值和报告。
9. SPM 设计矩阵增加 rank、condition number、VIF、residual DF 检查。
10. 连续协变量默认中心化；Sex 采用 0/1 编码。
11. Table 1 使用 Welch t-test；Sex 优先 Fisher exact，小样本避免强行使用 Pearson χ²。
12. `t1vbm_util.m` 保留为兼容层，实际功能拆分至 `t1vbm_env/files/log/table/manifest`。
13. cluster 表增加 cluster volume、threshold type、threshold T、MNI peak 等字段。
14. 自动生成 `T1_VBM_QC_Report.html`。
15. 自动生成 `Methods_analysis_summary.txt` 和 `Analysis_threshold_summary.csv`。

## 统计报告逻辑

- Primary analysis: voxel-level FWE p < 0.05；优先 RFT(RPV)，失败时使用 Bonferroni fallback，并明确记录方法。
- Exploratory analysis: voxel-level uncorrected p < 0.001 + cluster size ≥ 10 voxels。
- 两个方向的 group contrast 均生成。
- 不再把 uncorrected threshold 写成 FWE 结果。

## 推荐运行顺序

```matlab
addpath('G:\SSD_dyf\T1_vbm')
C = t1vbm_config();
t1vbm_env('checkspm', C)
t1vbm_step1_unzip(C)
t1vbm_calibrate(C)
run_all('from','step2','nproc',8)
```

> 注：示例首行 `G:\SSD_dyf` 为外置数据盘，未挂载时不存在；本段是 V2 时期旧版记录，属史证（`test\allowlist.tsv` 已登记 `G:\SSD_dyf`）。勿据此改现役脚本默认路径。

`nproc=8` 仅为示例，应以本机校准结果为准。


## V2.1 — Windows 后台 CAT12 worker 修正

- 新增 `t1vbm_cat_worker.m`：每个 subject 独立 MATLAB worker，以 `-batch`、`nproc=0` 运行 CAT12。
- `t1vbm_step2_cat12.m` 默认使用 PowerShell `Start-Process -WindowStyle Hidden` 启动 worker，避免 CAT12 官方 Windows 并行实现的可见 Command Window。
- 取消依赖 CAT12 父进程的 `Unknown/Unprocessed` 批次监督；改为 `.running/.done/.fail` 状态文件监督。
- `batchN` 默认调整为 12，并自动限制为 `min(batchN,nproc)`，避免实际 worker 数超过 nproc。
- CAT12 worker 的失败日志写入 `log/cat12_worker_<ID>.log`。
- 保留原始 CAT12 直接并行模式作为 `C.cat.backgroundHidden=false` 的兼容选项。
- 注意：CAT12 自身产生的 `catROI_*.xml` 依赖提示仍可能写入 worker 日志；该提示不再作为本流程的完成判定依据，最终完成仍以 CAT12 输出 QC + done marker 为准。


## V2.2 — CAT12 完成状态回写修正

- 修复 V2.1 的 manifest 回写 bug：CAT12 worker 完成后，原代码错误地从完整 NIfTI basename 重新生成 subject Key，导致 42 例实际完成但未被标记为 `CAT12Status=complete`。
- 改为直接用 `manifest.NiiFile == batch input` 精确匹配 subject。
- 若 batch 输入无法映射回 manifest，现在直接计为失败并写入日志，不再静默丢失。
- 已完成输出的续跑逻辑不变：重新运行 step2 时会先进行 `mwp1/mwp2/y_` 输出 QC，并直接补写 completion marker，不会重复运行 CAT12。

## V2.3 (2026-10-02)

- 修复 step4 `M` 未定义导致 Table 1 无法生成的问题：step4 现在直接从 subject manifest 加载 `M`。
- 修复 Bonferroni FWE 阈值计算的数值方向：使用 `-spm_invTcdf(alpha,df)`，避免 `1-alpha` 形式触发 `spm_invTcdf` 的 out-of-range 警告。
- 使用说明文件为 `t1vbm-user-guide-v2.html`；README 引用同步更新。


## V2.4
- Fixed Table 1 Sex validation logical-indexing bug (`sex(iA | iB)`).
- Avoided calling SPM RFT threshold calculation when RPV.nii is absent; Bonferroni fallback is used directly.

## V2.5 (2026-10-06)
- 可选协变量 TIV。`C.covVars` 默认仍是 Age、Sex、EducationYears。
- step2 在 CAT12 完成后把 TIV 写入 manifest。先读 `cat_*.mat`，没有再读 `cat_*.xml` 的 `<vol_TIV>`。
- 旧 `subject_manifest.csv` / `.mat` 可以没有 TIV 列。读取时补 NaN。
- step3 按 `C.covVars` 生成协变量。去掉「必须正好 3 个协变量」的检查。Design QC 的名字来自该列表。
- 协变量缺失时报错并写日志。未知性别不编为 0。
- 加入 TIV：`C.covVars={'Age','Sex','EducationYears','TIV'};`
- 新增 `cat12_xml_extract.py`：从 CAT12 XML 导出 Excel。`--input` 必填。
