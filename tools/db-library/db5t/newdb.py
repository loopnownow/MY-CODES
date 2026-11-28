# -*- coding: utf-8 -*-
"""newdb.py — 按 schema.sql 建空库（契 1：空库只能由 schema 生成，不许手敲 DDL）。

用法：
  python -B tools/db-library/db5t/newdb.py --family MM --slug zy_MM --dry-run
  python -B tools/db-library/db5t/newdb.py --family MM --slug zy_MM
  python -B tools/db-library/db5t/newdb.py --family MM --slug zy_MM --period-unit 6 --root <stage>

默认 root = DB5T_ROOT（paths.STAGE，pilot 批次的 stage/），**不直接写真库**：
先在 stage 建空库、跑门禁，换库那一步另按 HANDOFF 做。

建出来的东西（对照 db-build-contract.md 契 1 / zone-database.md:110）：
  <root>/<FAM>/_PHI/<FAM>db      家族库：meta / id / id_number / ref_project / ref_study / ref_batch
  <root>/<FAM>/<slug>/<slug>db   子库：meta / src / person / clin / _modules_* / *_value / qc
  <root>/<FAM>/<slug>/目录说明.md  叶内只留这两样；不再有散表 / 原始文件

硬规矩：
- 目标库已存在 → 跳过并 rc=0（绝不覆盖已建好的库，覆盖要走 --force，本脚本不提供）。
- schema.sql 必须比 schema-v1.md 新（mkschema.py --check 为 OK），否则 rc=2 不建。
- meta 里写 schema_version / kind / family / slug / built_at / rule，子库再写 dict_owner。
"""
import argparse
import datetime as dt
import pathlib
import sqlite3
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mkschema  # noqa: E402
import paths  # noqa: E402

MANIFEST = (
    '---\n'
    'family: {family}\n'
    'slug: {slug}\n'
    'db: {slug}db\n'
    'built_at: {now}\n'
    'schema_source: schema-v1.md（schema.sql 反编译）\n'
    'dict_owner: {dict_owner}\n'
    '---\n\n'
    '# {slug}\n\n'
    '## 来源\n\n'
    '- 原始文件：待登 src 表（src_id + src_row 回溯；原始文件入 `__zip\\`，叶内不留）\n'
    '- map.csv：`_work\\map.csv`（开工契约，收口随库）\n\n'
    '## 例外\n\n'
    '- 未入库文件：无\n'
    '- 未纳入列：无\n\n'
    '## 待定\n\n'
    '- 回填未开始；本文件随阶段 5/6 收口补全\n'
)


def split_schema(sql: str):
    if mkschema.MARK_PROJ not in sql or mkschema.MARK_FAM not in sql:
        raise SystemExit('schema.sql 缺 @section 标记，先用 mkschema.py 重新生成')
    fam, proj = sql.split(mkschema.MARK_PROJ, 1)
    return fam.split(mkschema.MARK_FAM, 1)[1], proj


def schema_sql(path: pathlib.Path) -> str:
    if not path.exists():
        raise SystemExit(f'{path} 不存在：先跑 python -B tools/db-library/db5t/mkschema.py')
    return path.read_text(encoding='utf-8')


def check_schema_fresh(path: pathlib.Path, md: pathlib.Path):
    have, want = mkschema.recorded_sha(path), mkschema.sha256(md)
    if have != want:
        raise SystemExit(f'schema.sql 过期（{have and have[:12]}… ≠ {want[:12]}…）：先跑 mkschema.py 或 --allow-stale')


def write_meta(db: pathlib.Path, rows: dict):
    c = sqlite3.connect(db)
    try:
        c.execute('CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL)')
        c.executemany('INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)', sorted(rows.items()))
        c.commit()
    finally:
        c.close()


def tidy_sidecars(db: pathlib.Path):
    """WAL 库关连接后若 -wal 已空，顺手清掉 -wal/-shm：叶内只留 db 与目录说明.md（S26）。"""
    wal, shm = db.with_name(db.name + '-wal'), db.with_name(db.name + '-shm')
    if wal.exists() and wal.stat().st_size == 0:
        wal.unlink()
        if shm.exists():
            shm.unlink()


def build(db: pathlib.Path, ddl: str, meta: dict, dry: bool) -> str:
    if db.exists():
        return f'SKIP 已存在 {db}（不覆盖）'
    if dry:
        return f'DRY  将建 {db}'
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db)
    try:
        c.executescript(ddl)
        c.commit()
    finally:
        c.close()
    write_meta(db, meta)
    tidy_sidecars(db)
    n = len([r for r in sqlite3.connect(db).execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%'")])
    return f'NEW  {db}  tables={n}'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='按 schema.sql 建空库')
    ap.add_argument('--family', required=True)
    ap.add_argument('--slug', default='', help='子工程 slug；--family-only 时可省略')
    ap.add_argument('--root', type=pathlib.Path, default=paths.STAGE)
    ap.add_argument('--schema', type=pathlib.Path, default=HERE / 'schema.sql')
    ap.add_argument('--schema-md', type=pathlib.Path, default=HERE / 'schema-v1.md')
    ap.add_argument('--period-unit', default='singleton', help='整数（月）或 singleton')
    ap.add_argument('--identity-grain', default='person',
                    choices=['person', 'specimen', 'exam', 'sample', 'label', 'mixed'],
                    help='family_uid 指代什么（D72 必需；默认 person）')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--family-only', action='store_true', help='只建家族库，不建子库（如 VEGF 无数据）')
    ap.add_argument('--allow-stale', action='store_true', help='允许 schema.sql 与 schema-v1.md 不同步')
    a = ap.parse_args(argv)

    if not a.allow_stale:
        check_schema_fresh(a.schema, a.schema_md)
    fam_sql, proj_sql = split_schema(schema_sql(a.schema))

    root = pathlib.Path(a.root)
    fam_db = root / a.family / '_PHI' / f'{a.family}db'
    proj_db = root / a.family / a.slug / f'{a.slug}db'
    note = root / a.family / a.slug / '目录说明.md'
    now = dt.datetime.now().astimezone().isoformat(timespec='seconds')

    fam_meta = {'schema_version': '5t/1', 'kind': 'family', 'family': a.family,
                'built_at': now, 'rule': 'db-build-contract.md',
                'identity_grain': a.identity_grain}
    proj_meta = {'schema_version': '5t/1', 'kind': 'project', 'family': a.family,
                 'slug': a.slug, 'built_at': now, 'rule': 'db-build-contract.md',
                 'identity_grain': a.identity_grain,
                 'dict_owner': str(paths.DICT)}
    if a.period_unit not in ('', 'singleton'):
        proj_meta['period_unit'] = str(int(a.period_unit))
        fam_meta['period_units'] = str(int(a.period_unit))

    print(f'root   = {root}')
    print(build(fam_db, fam_sql, fam_meta, a.dry_run))
    if a.family_only:
        return 0
    if not a.slug:
        ap.error('--slug 必填（除非 --family-only）')
    print(build(proj_db, proj_sql, proj_meta, a.dry_run))
    if a.dry_run:
        print(f'DRY  将写 {note}' if not note.exists() else f'SKIP 已存在 {note}')
    elif note.exists():
        print(f'SKIP 已存在 {note}（不覆盖）')
    else:
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(MANIFEST.format(family=a.family, slug=a.slug, now=now,
                                        dict_owner=paths.DICT), encoding='utf-8')
        print(f'NEW  {note}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
