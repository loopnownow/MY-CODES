# restore-from-summary — 汇总统计表 → 受试者级合成原始数据

从「只有 n / mean(SD) / AUC / sens / spec / P / r」的汇总表还原**受试者级**数据，供 pooled 再分析、
重绘图、敏感性模拟使用。输出**恒为 SYNTHETIC**（复现的是报表统计量，不是测量值），每行 `SYNTHETIC=1`，
README sheet 写明「不得冒充实测」。

首个用例：`re.xlsx`（mT2 / mT1 / mFA / mADC / GA / SINBAD / Age / BMI；n=56：Pos 23 / Neg 13 / Ctrl 20；
Sheet1 = 51 人子集）。2026-11-11 由 `{DEL}\_by-function\re-restore-20261111` 提炼。

## 文件

| 文件 | 作用 |
|---|---|
| `restore_raw.py` | 主体：配置区（源/交付路径、种子、容差、三档目标）+ 5 段流水线 + Verify/README 生成；默认 dry-run，`--execute` 才写盘 |
| `smoke_test.py` | 10 项单元/冒烟（矩精确、SS 恒等式、整数多重集、三元移动守恒、AUC、粒度换算、子集-全集四情形、锚点、迷你流水线、舍入感知 repair） |

依赖：Python 3.13（实测）、`numpy` / `pandas` / `scipy` / `openpyxl`。

## 用法

```powershell
cd <此目录>
python -B smoke_test.py                      # 12/12 通过才继续
python -B restore_raw.py                     # dry-run：锚点核验 + 可行性预检，不写盘
python -B restore_raw.py --execute           # 正式写出（单核约 12 min）
```

命令行 30 s 会超时，长跑用后台：

```powershell
$env:PYTHONIOENCODING='utf-8'
Start-Process python -ArgumentList '-B','restore_raw.py','--execute' -WorkingDirectory (Get-Location) `
  -RedirectStandardOutput run.log -RedirectStandardError run.err -NoNewWindow
```

Python 重定向后全缓冲，`run.log` 在进程结束才落盘；轮询 `Get-Process python` 判定结束。

## 配置区（换课题只改这里）

| 常量 | 含义 |
|---|---|
| `SRC_XLSX` / `OUT_XLSX` | 源汇总表（只读）/ 交付工作簿（可被 `--src` / `--out` 覆盖） |
| `SEED` | 固定种子，全程可复现 |
| `TOL` / `GRID_STEP` | 显示舍入窗口（半显示单位）/ 窗口网格步长 |
| `REPORT_DP` | 表内报告位数 `(mean_dp, sd_dp)`；派生 `MEAN_DP`（落盘位）/ `SD_DP`（比对位） |
| `SIM_DP` | 模拟精度：带 AUC/CI 的变量 4 位，其余取 SD 位数 |
| `LAND_DP` | 落盘精度：均值位数（SINBAD 整数 0 位） |
| `MICRO_REPAIR_ITERS` | 模拟精度舍入后计数微修的预算（只动临界值） |
| `MOM` / `PV` / `ROC` / `CORR` | 三档目标：矩 / P / ROC（含 cut 与方向）/ 相关 |
| `BOUNDS` / `INT_VARS` | 生理边界 / 整数变量 |
| `STAGE2_*` / `REPAIR_ITERS` / `STAGE3_*` / `R_TOL` | 预算与相关命中容差 |
| `ANCHOR_*` | 转录锚点（执行前断言源表确含这些字面值） |

## 三档口径

| 档 | 目标 | 规则 |
|---|---|---|
| A | n / mean / SD | 命中**表内显示精度**（1 位小数即 1 位） |
| B | AUC / sens / spec | 取最近**数学可达值**（AUC ∈ k/(nA·nB)，sens/spec ∈ k/n），差多少写多少 |
| C | P | 由锁定的矩决定；Welch / pooled / MW（+ ANOVA / KW）全算全报；阈值型判 HIT/MISS |

## 流水线

1. **矩构造**：子集⊂全集用 `SS_F = SS_S + SS_C` 解析分解；±0.05 窗口网格搜可行解；整数评分用计数向量穷举（剪枝）+ 洗牌。
2. **ROC 形态调参**：`z + a(z²−1) + b(z³−3z)/3` 每步重标准化（矩不动），Powell + logistic 退火 + 随机重启。
3. **repair**：三元移动 (δ, ε, ζ) 双守恒（`Σx`、`Σx²`），退火+爬山，最优快照回滚。
4. **相关配对**：组内 swap（边际不变 ⇒ 矩/AUC/计数不动），退火 + 贪心抛光；判据为逐项 `max|Δr|`。
5. **落模拟精度 → 计数微修 → 相关配对 → 落盘复核 → 写盘**：值先落到 `SIM_DP`（ROC 变量 4 位、其余 SD 位），
   临界值微修把舍入翻转的计数改回；`stage3` swap 不动边际；RawData 按 `LAND_DP`（均值位）写出，Verify 附 `*-landed` 复核行。

## 必须知道的坑

- **两套位数别混**：模拟精度（AUC/CI 变量 4 位、其余 SD 位）与落盘精度（均值位）分开；表里 `mT2 90.4` 是 1 位，`mT2` 的 AUC 0.948 却需要 4 位才能落在 k/(nA·nB) 网格上。
- **舍入翻转**：连续值修好再落位，临界计数会翻转（实测 mADC Neg vs Ctrl sens 9/13 → 10/13）。用 `micro_repair` 的定 δ 三元移动只推临界值，且要求显示精度矩不变。
- **落盘位比模拟位粗**：RawData 上重算的 ROC/矩可能与模拟精度行不同，Verify 的 `*-landed` 行如实记 `DRIFT`，不许藏。
- **粒度天花板**：nA×nB 与 n 决定可达网格，表内多数 AUC/sens/spec 不可达；取最近值并在 README sheet 打 `UNREACHABLE-*`。
- **跨表不相容**：子集行与全集行在显示窗口内都成立时才算可解；不可解则子集精确、全集取恒等式隐含值，Verify 记 `INCOMPATIBLE` 并给算式。
- **P 不可调**：矩锁定后参数法 P 唯一；精确目标 `MISS` 属正常，如实报告。
- **无回退**：不可行即抛错或进 Verify 六态；不静默降级、不换默认路径。

## 输出契约

- `RawData`：一行一人；`subject_id` 合成、`group`、`in_subset_S`、规范单位变量、`SYNTHETIC=1`；数值按**落盘精度**（均值位数）写出，单元格 `number_format` 同步（1 位→`0.0`、3 位→`0.000`、整数→`0`）。
- `Verify`：`block / item / target / achieved / delta / status / note`；状态 `EXACT` `HIT` `NEAREST` `REPORT` `MISS` `DRIFT` `INCOMPATIBLE`
  （`DRIFT` = 落盘值上重算与模拟精度行不一致）。
- `README`：用途、源路径、种子、队列定义、双精度说明、假设、方法、P 口径、`UNREACHABLE-*` / `INCOMPATIBLE:*`。

## 验收

`smoke_test.py` 12/12 通过后再 `--execute`；验收看四处：Verify 状态分布符预期、`MISS`/`INCOMPATIBLE` 都能给算式、
`*-landed` 的 `DRIFT` 都能解释、RawData 每行 `SYNTHETIC=1` 且小数位与表内一致。
