# -*- coding: utf-8 -*-
"""mkid.py — 阶段 3：身份先于值。把家族库 id / id_number 与 ref_project 行补齐。

本批只做「从退役库搬运」这一种来源（源 xlsx 的号码列在 blob 里拿不到）：
  python -B tools/db-library/db5t/mkid.py --from-family-db <retired>/MM/_PHI/MMdb \\
      --root <stage> --family MM --slug zy_MM [--dry-run]

做四件事：
  id / id_number       按 family_uid 原样搬（D75/D71 的判据不变，搬完立刻能验）
  ref_project          按子库实测重算 person_count / clin_person_n / sha256，写一行
  子库 meta            补 subject_types（D63 必需；本批按 --subject-types 传，默认 human）
  家族库 meta          补 subject_types（与子库一致）

不覆盖已有 id 行（有就跳过，报数），保证重跑幂等。
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sqlite3
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def copy_table(src: sqlite3.Connection, dst: sqlite3.Connection, table: str, key: str) -> tuple[int, int]:
    scols = [r[1] for r in src.execute(f'PRAGMA table_info({table})')]
    dcols = {r[1] for r in dst.execute(f'PRAGMA table_info({table})')}
    cols = [c for c in scols if c in dcols]
    have = {r[0] for r in dst.execute(f'SELECT {key} FROM {table}')}
    rows = [r for r in src.execute(f'SELECT {",".join(cols)} FROM {table}') if r[0] not in have]
    ph = ','.join('?' * len(cols))
    dst.executemany(f'INSERT INTO {table}({",".join(cols)}) VALUES ({ph})', rows)
    return len(rows), len(have)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='阶段 3：身份搬运 + ref_project 登记')
    ap.add_argument('--from-family-db', type=pathlib.Path, required=True)
    ap.add_argument('--root', type=pathlib.Path, default=paths.STAGE)
    ap.add_argument('--family', required=True)
    ap.add_argument('--slug', required=True)
    ap.add_argument('--subject-types', default='{"%s": "human"}', help='子库 subject_types（%%s 替换为 slug）')
    ap.add_argument('--period-unit', default=None)
    ap.add_argument('--identity-grain', default='person',
                    choices=['person', 'specimen', 'exam', 'sample', 'label', 'mixed'])
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    fam_db = a.root / a.family / '_PHI' / f'{a.family}db'
    proj_db = a.root / a.family / a.slug / f'{a.slug}db'
    for p in (a.from_family_db, fam_db, proj_db):
        if not p.exists():
            raise SystemExit(f'{p} 不存在')
    print(f'from   = {a.from_family_db}')
    print(f'fam    = {fam_db}')
    print(f'proj   = {proj_db}')

    s = sqlite3.connect(f'file:{a.from_family_db}?mode=ro', uri=True)
    f = sqlite3.connect(fam_db)
    p = sqlite3.connect(proj_db)
    try:
        n_id, had = copy_table(s, f, 'id', 'family_uid')
        n_num, _ = copy_table(s, f, 'id_number', 'family_uid')
        print(f'id      +{n_id}（已有 {had}）')
        print(f'id_number +{n_num}')

        pid = p.execute('SELECT count(*) FROM person').fetchone()[0]
        cid = p.execute('SELECT count(*) FROM clin').fetchone()[0]
        # 三个镜像值以「实时重算」为准：门的判据是三方相等，写死会变成第三套真值
        live_lon = p.execute(
            "SELECT count(*) FROM (SELECT family_uid FROM clin WHERE period_month<>'UNK' "
            "GROUP BY family_uid HAVING count(DISTINCT period_month)>1)").fetchone()[0]
        live_multi = p.execute(
            'SELECT count(*) FROM (SELECT family_uid FROM clin GROUP BY family_uid '
            'HAVING count(*)>1)').fetchone()[0]
        for conn in (p,):
            conn.execute('INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)', ('longitudinal_n', live_lon))
            conn.execute('INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)', ('multi_record_n', live_multi))
        print(f'meta.longitudinal_n={live_lon}  meta.multi_record_n={live_multi}')

        rel = f'{a.family}/{a.slug}/{a.slug}db'
        st = a.subject_types % a.slug
        f.execute('INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)', ('subject_types', st))
        p.execute('INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)', ('subject_types', st))
        for conn in (f, p):
            conn.execute('INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)',
                         ('identity_grain', a.identity_grain))
        print(f'meta[proj|family].subject_types = {st}  identity_grain = {a.identity_grain}')

        # sha256 必须在 WAL 落盘后算：先提交再 checkpoint（TRUNCATE）再读文件，
        # 否则 ref_project.sha256 永远差一版（2026-11-28 首次跑 MM 时踩到）。
        if not a.dry_run:
            f.commit()
            p.commit()
            p.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            digest = sha256(proj_db)
            f.execute('INSERT OR REPLACE INTO ref_project(slug, db_rel_path, person_count, '
                      'clin_person_n, longitudinal_n, multi_record_n, period_unit, built_at, sha256) '
                      'VALUES (?,?,?,?,?,?,?,?,?)',
                      (a.slug, rel, pid, cid, live_lon, live_multi,
                       int(a.period_unit) if a.period_unit else None,
                       __import__('datetime').date.today().isoformat(), digest))
            f.commit()
            print(f'ref_project ← {a.slug}（person={pid}, clin={cid}, lon={live_lon}, '
                  f'multi={live_multi}, sha256={digest[:12]}…）')
    finally:
        s.close()
        f.close()
        p.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
