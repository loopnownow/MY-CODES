# `test/` — 端到端 QC 夹具（合成数据，可进公开仓）

一个**最小真实工程**：`test.xlsx`（200 行合成数据，`cln` / `rad` / `qc` / `icc` 四张表）+
`ref/settings.ini`。跑一次约 80 秒，产出 `Group/ResultsGroup.html`，用于跟
`expected/golden.json` 逐格对拍。

> 这些数字**不是**患者数据，也不是任何在稿结果。它只回答一个问题：
> 「改完算法后，同样的输入是否还产出同样的数字」。

## 结构

```
test/
  test.xlsx                     合成数据（N=200；Group 阳性 93 / 阴性 107）
  ref/settings.ini              工程配置（MODULES_DIR 留空 = 用本仓 modules）
  ref/.rad_console_overlay.json 控制台覆盖项（data 用相对路径）
  expected/golden.json          9 张表的数字基线（权威；对拍用它）
  run_golden.py                 一条命令：跑 pipeline → 对拍
  .gitignore                    Group/ ref/cache/ PNG 等运行产物
```

## 跑法

```powershell
cd <repo>          # 例：MY-CODES 仓根
python -B test/run_golden.py            # 重跑 + 对拍；有 FAIL 则 exit 1
python -B test/run_golden.py --no-run   # 只对拍临时区现有产出
```

运行产物（`Group/`、`ref/cache/`、PNG、`ref/qc.html` 等）只落在 `<TEMP>\mycodes-golden-run\`，
**本仓 `test/` 里不产生任何生成物**（公开仓的 `test_public_path_policy` 会扫文件系统，
provenance 注里的本机路径不能进仓）。

`golden.json` 覆盖 9 张表：`table1-all`、`table2-all`、`table1-train-test`、
`table2-train-test`、`uni-train`、`table2-lasso-train`、`combined-coef-train`、
`roc-metrics-train-test`、`hosmer-lemeshow-internal`。

容差：默认 `1e-10`；`roc-metrics-train-test`（bootstrap CI）`1e-6`。
「数字在容差内但文本不同」（如 `<0.001***` → `<0.001**`）记 WARN，不判失败。

## 什么时候可以 `--refresh`

只在**你有意改变数字**时（新增模型、改统计方法、换阈值口径），并且要同时说明：

```powershell
python -B test/run_golden.py --refresh
```

刷新后请把 `golden.json` 的 diff 贴进当轮汇报——它是「数字变了多少」的唯一证据。

## 为什么仓库里没有 `expected/ResultsGroup.html`

试跑报告里的 provenance 注会写入本机 `code_root`（绝对路径），公开仓的
`test_public_path_policy` 会拦。数字基线只需要 `golden.json`；要看报告就本地跑一次
`test/run_golden.py`，`test/Group/ResultsGroup.html` 会被 `.gitignore` 挡住。

## 当前已知状态

这个夹具是**最小冒烟夹具**（临床特征 0 入选、`DO_SHAP` / landmark / IPCW 关闭），
所以报告顶部写 `Run status: INCOMPLETE` —— 这是预期内的。因此 golden 无法覆盖
mediation / DCA / IPCW / NRI / KM / 校准分箱；那些由 `modules/tests/` 里的
合成数据单测负责对拍。
