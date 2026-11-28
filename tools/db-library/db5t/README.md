# db5t —— 五表库（1+N）门禁与审计工具

从 pilot 批次 `{DEL}\_by-function\db5t-pilot-20261124\stage\_work\` 迁入，供后续家族复用。
**判据只此一份**：不要在批次目录里改副本，改动过 PR，改这里。

## 内容

| 文件 | 作用 |
|---|---|
| `paths.py` | 路径解析的唯一入口（`DB5T_BATCH` / `DB5T_ROOT` / `DB5T_SCHEMA` / `DB5T_DICT`） |
| `gates.py` | 机器门 D35–D99（D95 / D96 / D98 代码已入、**默认 report-only**，`--strict` 或 `DB5T_GATES_STRICT=1` 才判 P1；D87–D94、D97、D99 恒 report-only）+ `--cutover`（换库前待修清单，现算不落盘） |
| `mkschema.py` | `schema-v1.md` → `schema.sql`：只认两段 ```sql，7 个模态展开，家族库剔 `dict`，首行记 `source_sha256`；`--check` 校验过期 |
| `newdb.py` | 按 `schema.sql` 建空库（家族库 + 子库 + `meta` + `目录说明.md`）；目标已存在就跳过，绝不覆盖 |
| `mkdict.py` | 旧家族 `dict`（11 列）→ 全局 `{RAW}\Family\dict.db`（14 列），补 `scope='stage'` 骨架行，同键异写出冲突报告 |
| `mkid.py` | 阶段 3：从退役家族库搬 `id` / `id_number`，实时算 `longitudinal_n` / `multi_record_n`，写 `ref_project` 登记行与两库 `meta`（`subject_types` / `identity_grain`）；幂等 |
| `fill.py` | 阶段 5：读 13 列 `map.csv`，按物理依赖序 src→person→clin→`_modules_*`→`*_value` 回填；源可 `.xlsx/.xls`（按 sheet + header_row）或 `.db`（按表名）；`transform` 支持 `const:<值>` / `mint:<茎>`（发顺序号，同一源值跨 sheet 复用）；`missing_rule` 按源侧标记判缺（`NA` → `NULL`，D97 的修法）；无 dict 行不 ALTER（契 2）；`--dry-run` 只报不做；重跑先清本 src 旧行 |
| `preview.py` | 阶段 7：给刚建好的库出一张只读预览（单文件 HTML：KPI + src 登记 + 表清单 + clin 逐列 dict/NULL/样例 + 抽样 + 门禁摘要），默认不跑门禁，`--gates` 才就地跑 |
| `ddl_drift.py` | D58 用：把 SCHEMA 的两个 sql 块在空内存库里跑一遍取 `pragma`，不手写 DDL 解析 |
| `audit_families.py` | 跨家族只读审计（结构漂移 / 计数镜像 / qc 存量 / 日志模式 / 目录说明） |
| `fix_sha.py` | 刷新 `ref_project.sha256`（**必须在写完全部 meta 之后**跑） |
| `migrate_id_v2.py` | `id` 12 列 → 24 列重建迁移（`ALTER` 改不了列序，只能建新表抄数据；缺省 dry-run，`--apply` 才写盘并先备份） |
| `migrate_clin_v2.py` | `clin` 旧头 → v2 头（11 列头 + legacy 三元组 + 15 空 `path_*`）重建迁移；`group_v`/`subgroup` 同名变量并值进头列、`--clean` 才清违例库；缺省 dry-run，`--apply` 先备份 |
| `gen_readme.py` | 生成 `<leaf>/目录说明.md`（幂等，由各 build 收尾调用） |
| `schema-v1.md` | SCHEMA 的仓库权威副本；`mkschema.py` 默认从这里生成 `schema.sql`（不再读批次副本——`.Del` 下那份是历史快照，DDL 相同时只差注释） |
| `schema.sql` | `schema-v1.md` 的机器可读产物（`@section family` / `@section project`），`newdb.py` 据此建库；**不要手改** |

## 用法

```bash
# 1) 先确认指向哪里（换批次、换库根后必跑）
python -B paths.py

# 2) 对 stage 产物跑门禁
python -B gates.py MCI

# 3) 对切库后的真库跑门禁（同一份判据）
DB5T_ROOT=<库根> python -B gates.py MCI

# 4) 换库前的待修清单（现算，不落 meta——落盘必漂移）
DB5T_ROOT=<库根> python -B gates.py MCI --cutover

# 5) 跨家族审计
python -B audit_families.py          # 只 stage
python -B audit_families.py --data   # 含已切库的真库

# 6) 写完全库后再刷 sha
DB5T_ROOT=<库根> python -B fix_sha.py MCI|all

# 7) 建库三件套（schema → 空库 → 全局 dict）
python -B mkschema.py && python -B mkschema.py --check
python -B newdb.py --family MM --slug zy_MM --dry-run
python -B mkdict.py --dry-run

# 8) 阶段 3/5（MM 试点实测命令，换家族只换 --family/--slug）
python -B mkid.py --from-family-db <退役家族库> --root <stage> --family MM --slug zy_MM
python -B fill.py --map <批次>\_work\map.csv --root <stage> --family MM --slug zy_MM \
                  --src-root <批次父目录> [--dry-run]

# 9) 从根数据从零建（无退役库可搬：身份从子库 person 发号）
python -B newdb.py --family MCI --slug sw_CI --root <stage> --period-unit 1
python -B fill.py --map <批次>\_work\sw_CI\map.csv --root <stage> --family MCI --slug sw_CI --src-root {lab_root}
python -B mkid.py --from-person --root <stage> --family MCI --slug sw_CI --id-status label_only

# 10) 阶段 7：建完一个库先出预览给人看
python -B preview.py --root <stage> --family MCI --slug sw_CI \
                     --out {lab_root}\sw_CI-库预览.html --gates
```

**未写 `map.csv` 前先看两件事**：源列名里的逗号（如 `Sex (1=M, 0=F)`）必须用 `csv.writer` 写 map，
否则会被切成两列；源里的 `NA` / `NR` 这类字面缺失要写进 `missing_rule`，否则文本会落进 `REAL` 列，
D97 立刻判「真混合」。"


## 三条硬约束（都是踩过的坑）

1. **门禁只读**：所有连接走 `mode=ro`，门禁本身不许写库。
2. **先写完库再算 sha**：`fix_sha` 之后再补写 meta，会让 sha 与文件不符，看起来像 P0 冲突。
3. **判据改动必须自带正反 fixture**（工程约束第 6 条）：改 D49/D54 这类判据时，附一对「应报/不应报」的小库，否则只是换了一种误报方式。
4. **宽表上的 `or` 表达式会撞 SQLite 上限**：`d53_placeholder_values()` 曾在 1,775 列上拼 `col1 IS NOT NULL OR col2 IS NOT NULL OR …`，直接 `sqlite3.OperationalError: Expression tree is too large (maximum depth 1000)`。凡「逐列判非空」一律按 200 列分块累加（MHE_AN 的谱矩阵是第一个撞到的，下一家 1,316 列的 LC 也会撞）。

## 不在这里的东西

各家族的 `build_<fam>.py` 不迁入：它们编码的是**该数据集的具体判断**（谁是身份键、哪列是日期、哪些 sheet 是副本）。放进来会让「通用工具」变成「21 个家族的特例合集」。工具负责判据，build 负责事实。
