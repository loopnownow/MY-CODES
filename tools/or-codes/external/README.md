# external · 第三方代码依赖（外来件，非自研）

> 本目录只放**从上游取得的代码**，与本仓 `tools\`（自研加工脚本）严格分层。
> 规则：不改内部文件；升级 = 取新版本后先 diff 再覆盖；不把上游代码复制进 `tools\`。

## 1 清单

| 目录 | 来源 | 性质 | 文件 |
|---|---|---|---|
| `MM-Skin\` | https://github.com/ZwQ803/MM-Skin （paper: https://arxiv.org/abs/2505.06152） | 皮肤科视觉语言模型（llava 系）官方代码 | 116 |
| `TimesFM\` | 上游 GitHub master 归档 `timesfm-master.zip`（`pyproject.toml`：name=timesfm、version=2.0.1、Apache-2.0；README 署名 Google Research，paper arXiv 2310.10688） | 时序基础模型（zip 未解压） | 136（zip 内） |

## 2 取件记录

| 项 | 值 |
|---|---|
| 取得方式 | 上游仓库整包（非 `git clone`，**无 `.git`**，无历史可 diff） |
| 文件时间戳范围 | 2025-07-06 12:38:44 ~ 2026-10-18 22:18:40 |
| 搬入本目录 | 2026-11-15（原路径 `E:\mlh\skinvl\MM-Skin`） |
| 清理 | 已移除 macOS 元数据 `.DS_Store` ×10（移入 `{DEL}`，不影响代码） |
| 许可 | 上游仓库未附 LICENSE 文件；引用请照 arXiv 2505.06152 与仓库 README 的要求 |

## 3 谁在用它

| 使用方 | 引用方式 |
|---|---|
| `_Codes\tools\vlm\skinvl\infer_batch.py` | `REPO_DIR` → `sys.path.insert`，`import llava.*` |
| `_Codes\tools\vlm\skinvl\smoke_test.py` | 同上 |
| `_Codes\tools\vlm\skinvl\run_env_check.py` | 校验 `REPO_DIR`、`llava\model\builder.py` 是否存在 |

三者常量均为 `Path(r"{or_codes}\external\MM-Skin")`。

## 4 结构速览（上游原样）

```
MM-Skin\
├─ llava\            推理/训练包（builder、mm_utils、conversation、train\、serve\、eval\）
├─ Dataframe\        数据集构建脚本（含 Pretrain\ / test\）
├─ metric\           Bertscore / UMLSScorer
├─ transferability\  local_data\prepare_partitions.py 等
├─ merge\            权重合并
├─ utils\            eval_help / lin_eval / metrics
├─ SFT_classification.py / VQA_test.py / ZS_classification.py   三个入口
├─ finetune_lora.sh / SFT_classify_test.sh / ZS_classify_test.sh
├─ merge_lora_weights.py / cog.yaml / README.md / .gitattributes
```

## 5 维护约定

1. **不要**改动 `MM-Skin\` 内部任何文件（改了就失去与上游对齐的基准）。
2. 需要改行为时，在 `tools\vlm\skinvl\` 侧写壳或子类，不动上游。
3. 升级上游：先 `git clone` 到临时目录 → 与本目录 `diff -r` → 确认差异后再覆盖，并在本文件 §2 追加一行。
4. 建议下一步做一次真正的 `git clone`（或 `git init` + 记 commit），让来源可追溯；当前只有时间戳证据。
## 6 TimesFM 取件记录

| 项 | 值 |
|---|---|
| 目录 | `external\TimesFM\timesfm-master.zip`（未解压，保持上游归档原样） |
| 上游 | GitHub `timesfm-master` 归档；`pyproject.toml` 记 name=timesfm、version=2.0.1、license=Apache-2.0 |
| 证据 | 内附 README 署名 Google Research、paper arXiv 2310.10688、HF collection `google/timesfm-release` |
| 未核 | 仓库 URL 未联网核实（本机只有 zip，无 `.git`） |
| 文件时间戳 | 2026-06-14 08:35:29 |
| 搬入本目录 | 2026-11-16（原 `E:\lxf\TimesFM\timesfm-master.zip`） |
| 同批分流 | 自研脚本 `files.zip`（4 件）→ `_Codes\tools\lxf_LUNG\timesfm\`；部署说明 html → `{lab_root}\pre-submit\lxf_LUNG\ref\docs\` |
| 许可 | Apache-2.0（见 zip 内 `pyproject.toml`） |
