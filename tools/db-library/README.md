# db-library — 家族库建库 / 散表归并 / 校验

`_DataBase` 家族 SQLite 库与「散表 → 单工作簿」的现役脚本。2026-11-09 由
`{DEL}\_by-function\2026-11-db-sqlite\build_code_db.py`、`2026-11-db-root-merge\merge3.py`
提炼而来；算法与 DDL 保持与原脚本一致。

## 文件

| 文件 | 作用 |
|---|---|
| `build_code_db.py` | 按 `--family` + `--code` 建 `<db-root>\<family>\_db\<family>db`；三层去重、blob 保真、fact/entity 展开 |
| `merge_tables.py` | 按项目合并散表；`ADAPTERS` 已注册 `SCH/ly_SCH`、`OC`、`PCOS/zar_PCOS3D` |
| `verify_db.py` | 只读校验：integrity / schema / 计数 / 引用 / 台账 / entity 分布 |
| `common.py` | 三个 CLI 共用：`sha256` / `norm` / `read_any` / `write_workbook` / `ledger_write` |

依赖：Python 3.9+，`openpyxl`（xlsx）、`xlrd`（xls）；其余为标准库
（`sqlite3` / `zlib` / `hashlib` / `csv` / `argparse`）。当前不用 FTS5。

## 路径契约（权威）

- `{RULES}\workspace-layout.md`「家族库 `_db`」：一族一库，位置
  `_DataBase\<family>\_db\<family>db`（无扩展名），同目录只放 `目录说明.md`。
- `{RULES}\workspace-layout.md`「散表归并与表格入库」：项目粒度 = 叶 slug；
  合并簿 `<项目>_merged_YYYYMMDD.xlsx` 回叶根 / 家族根；原件移 `{DEL}` 并写台账。

> **禁止**在未获用户当轮点名时写入 `D:\MyProjects\_DataBase`（建库、移原件、
> `--apply` 均属写盘）。默认产出只落 `{DEL}` 批次目录或 `out/`。

## 用法

```bash
# 1) 建库（dry：只写 <db-root>/<family>/_db/<family>db）
python -B tools/db-library/build_code_db.py --family MHE --code ly

# 指定叶（可多次；缺省 = 该短码全部叶，扫 <db-root>/<family>/<slug>）
python -B tools/db-library/build_code_db.py --family MHE --code ly --leaf MHE/ly_RI

# 从批次 originals 重建（不读 _DataBase 叶；对账回归用）
python -B tools/db-library/build_code_db.py --family MHE --code ly --from-originals

# 建库 + 移原件 + 写台账（须点名授权）
python -B tools/db-library/build_code_db.py --family MHE --code ly --move

# 2) 散表归并
python -B tools/db-library/merge_tables.py --project SCH/ly_SCH --build
python -B tools/db-library/merge_tables.py --project OC --build
python -B tools/db-library/merge_tables.py --project PCOS/zar_PCOS3D --build
python -B tools/db-library/merge_tables.py --project SCH/ly_SCH --apply   # 须点名授权
python -B tools/db-library/merge_tables.py --build                        # 全部已注册

# 3) 只读校验
python -B tools/db-library/verify_db.py --db "D:/MyProjects/_DataBase/MHE/_db/MHEdb"
```

公共参数：`--db-root`（默认 `D:\MyProjects\_DataBase`）、`--batch`（`{DEL}\_by-function`
下的批次目录）。`merge_tables.py` 另有 `--stamp YYYYMMDD`。

### build_code_db.py

- 叶缺省发现：`<db-root>/<family>/<slug>` 中 `slug == code` 或以 `code_` 开头，按完整
  `FAM/slug` 排序；`--leaf` 接受 `FAM/slug` 或裸 slug。
- 建库时**先删旧库再重建**（原脚本行为）；库文件为 `<family>db`，无扩展名。
- 去重三层：L1 文件 sha；L2 整表（表头 + 行哈希集合：全等 = `dup`，真子集 = `subset`，
  只记 `sheet_dup` 边、不存第二份 blob）；L3 行级仅在可识别表头且 `ncol ≤ 40` 时展开 fact。
- 宽表：表头数字占比 > 0.6（谱图 / 积分矩阵）→ `is_wide=1`，只存 zlib(TSV) blob。
- 表头白名单 `IDCOL`：`医技号|样本编号|样本号|编号|case no.|mid|谱图编号|patient(_id)?|id`。
- `--move` 的原件落点：`{DEL}/_by-function/<batch>/originals/<code>/<leaf>/<相对路径>`；
  台账 `<batch>/<CODE>-MOVED-<YYYYMMDD>.tsv`（`rel_path / dst / size / sha256`）。
  `--from-originals` 从同一布局读取，默认批次 `2026-11-db-sqlite`（与现有实例一致）。

### merge_tables.py

- `ADAPTERS` 键 = 项目名；`--project` 还接受叶 slug（`ly_SCH` / `zar_PCOS3D`）或家族名
  （`SCH` / `PCOS` / `OC`）；未注册项目会列出可用键并退出。
- `--build` 只写 `<batch>/out/<项目>_merged_<stamp>.xlsx`；`--apply` 额外：原件移
  `<batch>/originals/<project>/`、合并簿 `copy2` 回项目根、写
  `<batch>/MERGE-MOVED-<stamp>.tsv`。目标已存在即停止（不覆盖）。
- 未注册 `HZ`：本仓只搬入本轮实际用到的三个适配器；新项目在 `ADAPTERS` 增加
  `build(src, out_path) -> Path` 即可。

### verify_db.py

只读打开（`file:...?mode=ro`）并检查：

1. `PRAGMA integrity_check`；
2. 核心表 / 索引集合（缺 = FAIL，多 = warn）；
3. `doc` / `sheet`（ingested/dup/subset）/ `sheet_dup` / `fact` / `entity` 计数与 `meta`；
4. 引用完整性（sheet→doc、fact→sheet/entity、entity→project、sheet_dup→sheet）与
   `doc.n_sheets`；
5. 台账覆盖：`doc.rel_path` ↔ `*-MOVED-*.tsv`（按 `meta.short_code` 自动找，可用
   `--ledger` 指定），并核对 sha256；顺带检查 `dst` 原件是否还在；
6. entity 键分布（按 `key_kind` 计数）与空键 / 同项目同键重复。

任一 `[FAIL]` → `sys.exit(1)`；只有 `[warn]` → 0。

## 幂等 / 回归

- `merge_tables.py --build` 的 `out/` 可删；重跑会生成同名文件（按 `--stamp`）。
- `verify_db.py` 对身体现有 `MHEdb` 的期望：103 doc / 420 sheet（351 ingested、67 dup、
  2 subset）/ 1,630,725 fact / 43,285 entity；`integrity_check` ok。
- 建库 / 归并前后按规则跑 `structure_check.py`；本目录新增文件后需重跑
  `tools/sync/gen_code_manifest.py` 并一起 `git add`（S8）。

## 不做

- 不按短码拆库 / 不建 `<family>\_db\<short_code>\` 子夹；`_db` 下只放 `<family>db` +
  `目录说明.md`。
- 不把压缩档（`*.tar.zst` / `7z` / `zip`）入库或移动。
- 不自动做跨项目 / 跨叶实体 join；短码是归属人，不是患者主索引。
- 不 `git commit` / `push` / 开 PR（由 Lead 处理）。
