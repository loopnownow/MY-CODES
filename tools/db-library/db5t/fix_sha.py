"""把 ref_project.sha256 刷新成子库文件的当前 sha256（D35 的 sha 项要求「冻结有据」）。

只改家族库的 `ref_project` 一列，不动子库内容；因此算出的 sha 在写入后仍然准确。
用法：python fix_sha.py all            # 枚举 DB5T_ROOT 下所有家族桶
      python fix_sha.py MCI LC EC     # 也可点名若干家族
"""
import hashlib
import os
import pathlib
import sqlite3
import sys

import paths as _P  # DB5T_ROOT 可指向 _DataBase 修切库后的真库

STAGE = pathlib.Path(os.environ.get('DB5T_ROOT') or _P.STAGE)


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def one(fam):
    fam_db = STAGE / fam / '_PHI' / f'{fam}db'
    if not fam_db.exists():
        print(f'{fam}: 家族库不存在 {fam_db}')
        return
    c = sqlite3.connect(str(fam_db))
    rows = c.execute('select slug, sha256 from ref_project').fetchall()
    n = 0
    for slug, old in rows:
        p = STAGE / fam / slug / f'{slug}db'
        if not p.exists():
            print(f'  {fam}/{slug}: 子库缺失 {p}')
            continue
        # WAL 库在最后一个连接关闭时会 checkpoint，改写主文件字节 → 先显式 checkpoint 再算，
        # 否则「sha 与实际不符」会是读取副作用造成的假 P0（ACN 三家踩过）。
        _ck = sqlite3.connect(str(p))
        try:
            _ck.execute('pragma wal_checkpoint(truncate)')
        finally:
            _ck.close()
        cur = sha(p)
        if old != cur:
            c.execute('update ref_project set sha256=? where slug=?', (cur, slug))
            n += 1
            print(f'  {fam}/{slug}: {(old or "NULL")[:12]}… -> {cur[:12]}…')
    c.commit()
    c.close()
    print(f'{fam}: ref_project {len(rows)} 行，更新 {n} 行')


if __name__ == '__main__':
    args = sys.argv[1:] or ['all']
    for f in (sorted(p.name for p in STAGE.iterdir()
                     if p.is_dir() and not p.name.startswith(('_', '.')))
              if 'all' in args else args):
        one(f)
