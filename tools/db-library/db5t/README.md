# db5t —— 五表库（1+N）门禁与审计工具

从 pilot 批次 `{DEL}\_by-function\db5t-pilot-20261124\stage\_work\` 迁入，供后续家族复用。
**判据只此一份**：不要在批次目录里改副本，改动过 PR，改这里。

## 内容

| 文件 | 作用 |
|---|---|
| `paths.py` | 路径解析的唯一入口（`DB5T_BATCH` / `DB5T_ROOT` / `DB5T_SCHEMA`） |
| `gates.py` | 机器门 D35–D92（D87–D92 身份表 / 病理段先 report-only，只报数不改判级）+ `--cutover`（换库前待修清单，现算不落盘） |
| `ddl_drift.py` | D58 用：把 SCHEMA 的两个 sql 块在空内存库里跑一遍取 `pragma`，不手写 DDL 解析 |
| `audit_families.py` | 跨家族只读审计（结构漂移 / 计数镜像 / qc 存量 / 日志模式 / 目录说明） |
| `fix_sha.py` | 刷新 `ref_project.sha256`（**必须在写完全部 meta 之后**跑） |
| `migrate_id_v2.py` | `id` 12 列 → 24 列重建迁移（`ALTER` 改不了列序，只能建新表抄数据；缺省 dry-run，`--apply` 才写盘并先备份） |
| `gen_readme.py` | 生成 `<leaf>/目录说明.md`（幂等，由各 build 收尾调用） |
| `schema-v1.md` | SCHEMA 的兜底副本；权威版本仍在批次目录，`paths.py` 优先用它 |

## 用法

```bash
# 1) 先确认指向哪里（换批次、换库根后必跑）
python -B paths.py

# 2) 对 stage 产物跑门禁
python -B gates.py MCI

# 3) 对切库后的真库跑门禁（同一份判据）
DB5T_ROOT="D:/MyProjects/_DataBase" python -B gates.py MCI

# 4) 换库前的待修清单（现算，不落 meta——落盘必漂移）
DB5T_ROOT="D:/MyProjects/_DataBase" python -B gates.py MCI --cutover

# 5) 跨家族审计
python -B audit_families.py          # 只 stage
python -B audit_families.py --data   # 含已切库的真库

# 6) 写完全库后再刷 sha
DB5T_ROOT="D:/MyProjects/stage_root" python -B fix_sha.py MCI|all
```

## 三条硬约束（都是踩过的坑）

1. **门禁只读**：所有连接走 `mode=ro`，门禁本身不许写库。
2. **先写完库再算 sha**：`fix_sha` 之后再补写 meta，会让 sha 与文件不符，看起来像 P0 冲突。
3. **判据改动必须自带正反 fixture**（工程约束第 6 条）：改 D49/D54 这类判据时，附一对「应报/不应报」的小库，否则只是换了一种误报方式。
4. **宽表上的 `or` 表达式会撞 SQLite 上限**：`d53_placeholder_values()` 曾在 1,775 列上拼 `col1 IS NOT NULL OR col2 IS NOT NULL OR …`，直接 `sqlite3.OperationalError: Expression tree is too large (maximum depth 1000)`。凡「逐列判非空」一律按 200 列分块累加（MHE_AN 的谱矩阵是第一个撞到的，下一家 1,316 列的 LC 也会撞）。

## 不在这里的东西

各家族的 `build_<fam>.py` 不迁入：它们编码的是**该数据集的具体判断**（谁是身份键、哪列是日期、哪些 sheet 是副本）。放进来会让「通用工具」变成「21 个家族的特例合集」。工具负责判据，build 负责事实。
