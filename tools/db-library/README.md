# db-library — 家族库建库 / 归档入库 / 散表归并 / 校验

`_DataBase` 家族 SQLite 库、`_share` 归档与「散表 → 单工作簿」的现役脚本。
2026-11-09 由 `{DEL}\_by-function\2026-11-db-sqlite\build_code_db.py`、
`2026-11-16family\build_family_db.py`、`2026-11-mhe-split\sweep_five_20261109.py`
提炼而来；DDL 与三层去重算法与原脚本逐字一致，逻辑抽到 `db_core.py` 共用。

## 文件

| 文件 | 作用 |
|---|---|
| `db_core.py` | 公共核心：常量 / DDL / `Ingestor`（三层去重、blob、fact/entity）/ 解档 / 台账 / 归档归位 / 移动校验 |
| `common.py` | 表格读写与 sha256：`read_any` / `write_workbook` / `ledger_write` |
| `build_code_db.py` | 按 `--family` + `--code` 建 `<db-root>\<family>\_db\<family>db`；`--share-archives` 档内表入库；`--strict-leaf` 叶内残留校验 |
| `merge_tables.py` | 按项目合并散表（`ADAPTERS`：`SCH/ly_SCH`、`OC`、`PCOS/zar_PCOS3D`） |
| `verify_db.py` | 只读校验：integrity / schema / 计数 / 引用 / 台账 / 新口径 |
| `flatten_share.py` | `_share\{thesis,ethics}\<slug>\*` → `<slug>__<name>` 打平 |
| `archive_to_share.py` | 叶内 / 家族根压缩档归位到 `_share\<leaf>\` |
| `archive_ingest.py` | `_share` 档内表格解出入库，解包树移 `{DEL}` |
| `sweep_tables.py` | 叶内散表入库 + 原件移 `{DEL}` |
| `leaf_raw_pack.py` | 叶内 raw 按单元打 `<code>__<leaf>__<unit>-raw-<date>.tar.zst` 上收 `_share` |

依赖：Python 3.9+（实测 3.13），`openpyxl`（xlsx）、`xlrd`（xls）；`py7zr`（7z）；
`rar` / `tar.zst` 用 `bsdtar`（Windows 在 `D:\anaconda3\Library\bin\bsdtar`）。
其余为标准库（`sqlite3` / `zlib` / `hashlib` / `csv` / `zipfile` / `argparse`）。

## 路径契约（权威）

- `_DataBase\<family>\_db\<family>db`（无扩展名），同目录只放 `目录说明.md`。
- 压缩档只在 `_DataBase\<family>\_share\`，层级 ≤2；`ethics` / `thesis` 不分层，
  档名 `<slug>__<原文件名>`（嵌套目录用 `__` 连接）。
- 档内表格解出入库（`doc.rel_path = <FAM>/_share[/<类>]/<档名>/<成员>`）；
  档内非表格成员留在档内，原档不移动。
- 叶内只留 `目录说明.md`；唯一例外 `MHE_PA/lx_MHE_1` 的 PHI 表（见 `LEAF_EXCEPTIONS`）。
- 原件移 `{DEL}\_by-function\<batch>\originals\<code>\<family>\<leaf>\<叶内相对路径>`，
  台账逐行 sha256 + kind。
- 叶内 raw 按单元（叶下第一层路径名；直属文件 = `loose`）打包到
  `_share\<leaf>\<code>__<leaf>__<unit>-raw-<YYYY-MM-DD>.tar.zst`。

> **禁止**在未获用户当轮点名时写入 `D:\MyProjects\_DataBase`（建库、移原件、
> `--apply` 均属写盘）。所有 `--apply` / `--move` 均须点名；缺省 dry-run。

## 用法

```bash
# 1) 建库（先删旧库再重建）
python -B tools/db-library/build_code_db.py --family MHE_AN --code ly

# 指定叶 / 从批次 originals 重建（不读 _DataBase 叶）
python -B tools/db-library/build_code_db.py --family MHE_AN --code ly --leaf MHE_AN/lxf_ABCMRS
python -B tools/db-library/build_code_db.py --family MHE_AN --code ly --from-originals

# 档内表入库 + 移原件 + 台账（须点名）
python -B tools/db-library/build_code_db.py --family MHE_AN --code ly --share-archives --move
# 入库后校验叶内无残留（有残留退出码 2）
python -B tools/db-library/build_code_db.py --family MHE_AN --code ly --strict-leaf

# 2) 散表归并（默认只落 {DEL}/<batch>/staging/）
python -B tools/db-library/merge_tables.py --project SCH/ly_SCH --build
python -B tools/db-library/merge_tables.py --project OC --build
python -B tools/db-library/merge_tables.py --project PCOS/zar_PCOS3D --build
python -B tools/db-library/merge_tables.py --build                        # 全部已注册
python -B tools/db-library/merge_tables.py --project SCH/ly_SCH --apply   # 原件移 {DEL}，须点名
python -B tools/db-library/merge_tables.py --project SCH/ly_SCH --into "D:/MyProjects/_DataBase/SCH/_db/SCHdb"

# 3) 新口径五个 CLI（均默认 dry-run，--apply 落盘，--ledger 指台账）
python -B tools/db-library/flatten_share.py    --family MHE_AN
python -B tools/db-library/archive_to_share.py --family MHE_AN
python -B tools/db-library/archive_ingest.py   --family MHE_AN
python -B tools/db-library/sweep_tables.py     --family MHE_AN
python -B tools/db-library/leaf_raw_pack.py    --family MHE_AN

# 4) 只读校验
python -B tools/db-library/verify_db.py --db "D:/MyProjects/_DataBase/MHE_AN/_db/MHE_ANdb"
```

公共参数：`--db-root`（默认 `D:\MyProjects\_DataBase`）、`--batch`
（`{DEL}\_by-function` 下的批次目录）、`--family` / `--code`。`merge_tables.py`
另有 `--stamp YYYYMMDD`。

### build_code_db.py

- 叶缺省发现：`<db-root>/<family>/<slug>` 中 `slug == code` 或以 `code_` 开头，按完整
  `FAM/slug` 排序；`--from-originals` 改从 `<batch>/originals/<code>/<family>/<slug>`
  反推，`--leaf` 优先。没有任何可用叶时直接退出，不删旧库。
- 去重三层：文件 sha → 整表级（表头 + 行哈希集合：全等 = `dup`，真子集 = `subset`，
  只记 `sheet_dup` 边，不存第二份 blob）→ 行级；每张 ingested sheet 以 zlib(TSV) 存 blob。
  可识别表头且 `ncol ≤ 40` 时展开 fact / entity。
- 宽表：表头数字占比 > 0.6（谱图 / 积分矩阵）→ `is_wide=1`，只存 blob。
- 表头白名单 `IDCOL`：`医技号|样本编号|样本号|编号|case no.|mid|谱图编号|patient(_id)?|id`。
- `--share-archives`：枚举 `<family>/_share/` 下归档（层级 ≤2），解到
  `<batch>/staging/share-unpack/<FAM>/<share 相对目录>/` 后按同一 `Ingestor` 增量入库；
  原档留 `_share`；配 `--move` 时解包树校验后移 `<batch>/unpack/`，台账记
  `kind=archive-member`（`archive` / `member` 列）。
- `--strict-leaf`：入库后校验叶内除 `目录说明.md` 外无残留，有则打印明细并退出码 2；
  `LEAF_EXCEPTIONS` 中的叶只告警。
- `--move` 原件落点：`<batch>/originals/<code>/<family>/<leaf>/<叶内相对路径>`，
  逐件 `move_verified`（移动前后 sha256 + size 校验，拒绝覆盖）。
- 台账：`<batch>/<CODE>-MOVED-<FAM>-<YYYYMMDD>.tsv`，列
  `kind rel_path dst size sha256 archive member`；`kind=table` 为叶内表，
  `kind=archive-member` 为档内成员。

### merge_tables.py

- `ADAPTERS` 键 = 项目名；`--project` 还接受叶 slug（`ly_SCH` / `zar_PCOS3D`）或家族名
  （`SCH` / `PCOS` / `OC`）；未注册项目会列出可用键并退出。
- 默认输出 `<batch>/staging/<项目>_merged_<stamp>.xlsx`；`--out` 可改，但目标落在
  `_DataBase` 内时必须显式 `--in-db`（复制回叶根，旧行为）或 `--into <db>`（增量入库），
  否则拒绝执行。
- `--apply`：生成 → 原件移 `<batch>/originals/<project>/`（记台账
  `<batch>/MERGE-MOVED-<stamp>.tsv`，列 `fam src rel dst size_B sha256_12 moved_at`）
  → 按 `--in-db` / `--into` 处置合并簿；缺省留在 staging，不写回 `_DataBase`。
  目标已存在即停止（不覆盖）。

### verify_db.py

只读打开（`file:...?mode=ro`）并检查：

1. `PRAGMA integrity_check`；核心表 / 索引集合（缺 = FAIL，多 = warn）；
2. `doc` / `sheet`（ingested/dup/subset）/ `sheet_dup` / `fact` / `entity` 计数与 `meta`；
3. 引用完整性（sheet→doc、fact→sheet/entity、entity→project、sheet_dup→sheet）与
   `doc.n_sheets`；
4. 台账覆盖：按 `<code>+<family>` 精确归属（文件名 token + 行内路径证据），再以
   `doc.rel_path` 精确 / 归档前缀 / sha256 命中；顺带检查 `dst` 原件是否还在；
5. entity 键分布、空键 / 同项目同键重复；
6. 新口径：叶内只留 `目录说明.md`（`LEAF_EXCEPTIONS` 仅 warn）、归档只在 `_share`、
   `_share` 层级 ≤2 且无子目录、`meta.rule` 与现行口径一致、
   `archive→member` 映射在 `_share` 能找到对应档。

任一 `[FAIL]` → `sys.exit(1)`；只有 `[warn]` → 0。`--quiet` 只打印 WARN/FAIL。

## 幂等 / 回归

- 新 CLI 缺省 dry-run，可安全重跑盘点；`--apply` 重跑时已存在目标会报错（不覆盖）。
- `verify_db.py` 对 2026-11-09 收口后 5 库的期望（fail=0）：

| 库 | doc | sheet | fact | entity |
|---|---|---|---|---|
| `MHE_AN/_db/MHE_ANdb` | 123 | 387 | 1,192,754 | 958 |
| `MHE_PA/_db/MHE_PAdb` | 15 | 53 | 31,775 | 603 |
| `SCH/_db/SCHdb` | 7 | 48 | 498,530 | 42,045 |
| `ACN/_db/ACNdb` | 12 | 82 | 25,012 | 706 |
| `OTHERS/_db/OTHERSdb` | 21 | 67 | 360,475 | 76 |

- 建库 / 归并前后按规则跑 `structure_check.py`；本目录新增文件后需重跑
  `tools/sync/gen_code_manifest.py` 并一起 `git add`（S8）。

## 不做

- 不按短码拆库 / 不建 `<family>\_db\<short_code>\` 子夹；`_db` 下只放
  `<family>db` + `目录说明.md`。
- 不移原档（`_share` 归档原样保留）；只把档内表格解出入库。
- 不自动做跨项目 / 跨叶实体 join；短码是归属人，不是患者主索引。
- 不 `git commit` / `push` / 开 PR（由 Lead 处理）。
