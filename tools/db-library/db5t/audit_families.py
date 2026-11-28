# -*- coding: utf-8 -*-
"""12 家族跨库一致性审计（只读）：结构漂移 / 计数镜像 / qc 存量 / 日志模式 / 目录说明。

用法：python audit_families.py            -> 控制台 + stage/AUDIT-12.md
      python audit_families.py --data     -> 同时审计已切库的 _DataBase 家族
"""
import sqlite3
import pathlib
import sys

import paths as _P  # ROOT/STAGE 由 DB5T_BATCH / DB5T_ROOT 决定

ROOT = _P.BATCH
STAGE = _P.STAGE
DBROOT = pathlib.Path(r'D:\MyProjects\_DataBase')
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ddl_drift  # noqa: E402

EXP = ddl_drift.expected_sets()


def ro(p):
    return sqlite3.connect('file:' + str(p).replace('\\', '/') + '?mode=ro', uri=True, timeout=5)


def one(c, sql, args=(), default=None):
    try:
        r = c.execute(sql, args).fetchone()
        return r[0] if r and r[0] is not None else default
    except sqlite3.Error:
        return default


def audit_family(fam_dir, tag):
    fam = fam_dir.name
    fam_db = fam_dir / '_PHI' / f'{fam}db'
    row = {'family': fam, 'tag': tag, 'db_bytes': fam_db.stat().st_size if fam_db.exists() else 0,
           'registered': 0, 'dirs': 0, 'id': 0, 'id_number': 0, 'dict': 0,
           'person': 0, 'clin': 0, 'clin_person': 0, 'longi': 0, 'multi': 0,
           'qc_p0': 0, 'qc_p1': 0, 'qc_p2': 0, 'drift': 0, 'journal': '', 'sidecar': 0,
           'readme': 0, 'problems': []}
    if not fam_db.exists():
        row['problems'].append('缺家族库')
        return row
    c = ro(fam_db)
    row['journal'] = one(c, 'pragma journal_mode', default='')
    row['id'] = one(c, 'select count(*) from id', default=0)
    row['id_number'] = one(c, 'select count(*) from id_number', default=0)
    row['dict'] = one(c, 'select count(*) from dict', default=0)
    try:
        projs = [r[0] for r in c.execute('select slug from ref_project order by slug')]
    except sqlite3.Error:
        projs = []
        row['problems'].append('无 ref_project')
    row['registered'] = len(projs)
    c.close()
    on_disk = sorted(p.name for p in fam_dir.iterdir()
                     if p.is_dir() and p.name not in ('_PHI', '__zip') and not p.name.startswith(('_', '.'))
                     and (p / f'{p.name}db').exists())
    row['dirs'] = len(on_disk)
    for s in on_disk:
        if s not in projs:
            row['problems'].append(f'库在账不在:{s}')
    for s in projs:
        if s not in on_disk:
            row['problems'].append(f'账在库不在:{s}')

    row['drift'] += len(ddl_drift.check_db(str(fam_db), EXP['family']))
    for slug in projs:
        p = fam_dir / slug / f'{slug}db'
        if not p.exists():
            continue
        c = ro(p)
        row['drift'] += len(ddl_drift.check_db(str(p), EXP['project']))
        row['person'] += one(c, 'select count(*) from person', default=0)
        row['clin'] += one(c, 'select count(*) from clin', default=0)
        row['clin_person'] += one(c, 'select count(distinct family_uid) from clin', default=0)
        row['longi'] += one(c, "select count(*) from (select family_uid from clin where period_month<>'UNK' "
                                "group by family_uid having count(distinct period_month)>1)", default=0)
        row['multi'] += one(c, 'select count(*) from (select family_uid from clin group by family_uid '
                               'having count(*)>1)', default=0)
        for sev in ('P0', 'P1', 'P2'):
            row['qc_' + sev.lower()] += one(c, "select count(*) from qc where severity=? and status='open'",
                                            (sev,), default=0)
        c.close()
    row['sidecar'] = sum(1 for p in fam_dir.rglob('*-wal')) + sum(1 for p in fam_dir.rglob('*-shm'))
    row['readme'] = int((fam_dir / '_PHI' / '目录说明.md').exists())
    if not row['readme']:
        row['problems'].append('缺 _PHI/目录说明.md')
    if row['journal'] != 'delete':
        row['problems'].append(f'journal={row["journal"]}')
    return row


def main():
    rows = []
    for d in sorted(STAGE.iterdir()):
        if not d.is_dir() or d.name.startswith(('_', '.')):
            continue
        if not (d / '_PHI').is_dir():
            continue
        rows.append(audit_family(d, 'stage'))
    if '--data' in sys.argv:
        for d in sorted(DBROOT.iterdir()):
            if not d.is_dir() or d.name.startswith(('_', '.')):
                continue
            fam_db = d / '_PHI' / (d.name + 'db')
            if not fam_db.exists():
                continue
            try:
                c = ro(fam_db)
                has5 = one(c, "select count(*) from sqlite_master where type='table' and name='ref_project'",
                           default=0)
                c.close()
            except sqlite3.Error:
                has5 = 0
            if has5:
                rows.append(audit_family(d, 'DATA'))

    hdr = ['家族', '源', '库MB', '登记/在盘', 'id', 'id_number', 'dict', 'person', 'clin', 'clin人',
           '纵向', '多条', 'qc P0/P1/P2', '漂移', 'journal', '边车', '说明', '问题']
    out = ['# 12 家族一致性审计（只读）', '']
    out.append('| ' + ' | '.join(hdr) + ' |')
    out.append('|' + '|'.join(['---'] * len(hdr)) + '|')
    for r in rows:
        out.append('| ' + ' | '.join(map(str, [
            r['family'], r['tag'], f"{r['db_bytes'] / 1048576:.2f}", f"{r['registered']}/{r['dirs']}",
            r['id'], r['id_number'], r['dict'], r['person'], r['clin'], r['clin_person'],
            r['longi'], r['multi'], f"{r['qc_p0']}/{r['qc_p1']}/{r['qc_p2']}",
            r['drift'], r['journal'], r['sidecar'], r['readme'],
            '; '.join(r['problems']) or '-',
        ])) + ' |')
    out.append('')
    out.append('列说明：`纵向`=有 ≥2 个不同 period_month 的人数；`多条`=>1 条 clin 行的人数；'
               '`漂移`=与 SCHEMA 不符的表/列项数；`边车`=目录内 -wal/-shm 文件数；'
               '`登记/在盘`=`ref_project` 行数 / 磁盘上带库文件的子工程目录数。')
    txt = '\n'.join(out)
    (STAGE / 'AUDIT-12.md').write_text(txt, encoding='utf-8')
    print(txt)


if __name__ == '__main__':
    main()
