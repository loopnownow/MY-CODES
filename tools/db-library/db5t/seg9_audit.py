#!/usr/bin/env python
"""seg9_audit.py —— 9 段身份分类审计（只读）。

背景：SCHEMA §8.1 说 9 段是「既无号码也无姓名」的占位，但实测 8 家 8,848 个 9 段身份
带着姓名。段位混用之后，「这个库里有多少人」只能靠猜，所以先把 9 段拆开看：

    named     有 id.name                —— 按现行口径该在 0 段（人级实体）
    numbered  有 id_number 行            —— 有号码却不发 keyed 段，也要看
    specimen  被 _modules_* 表的 exam 行引用 —— 标本/检查级身份（SCC 的病理号行）
    omics     id_status='label_only_omics'
    empty     其余，真占位

只读，不修库。输出 stage/SEG9-AUDIT.md 与 seg9_audit.csv。
"""
import csv
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

STAGE = Path(os.environ.get('DB5T_ROOT') or paths.STAGE)


def tables(c, kind):
    return [r[0] for r in c.execute(
        "select name from sqlite_master where type='table' and name GLOB ?", (kind,))]


def audit_family(fam):
    fam_db = STAGE / fam / '_PHI' / f'{fam}db'
    if not fam_db.exists():
        return []
    with sqlite3.connect(f'file:{fam_db}?mode=ro', uri=True) as f:
        named = {u for (u,) in f.execute(
            "select family_uid from id where substr(family_uid,instr(family_uid,'-')+1,1)='9' "
            "and name is not null and trim(name)<>''")}
        status = dict(f.execute("select family_uid, id_status from id").fetchall())
        numbered = {u for (u,) in f.execute("select distinct family_uid from id_number")}
    n9 = [u for u, st in status.items()
          if '-' in u and len(u.split('-')[1]) >= 6 and u.split('-')[1][0] == '9']
    referenced, proj_of = set(), {}
    for d in sorted(STAGE.joinpath(fam).iterdir()):
        dbs = list(d.glob(f'{d.name}db')) if d.is_dir() else []
        if not dbs:
            continue
        with sqlite3.connect(f'file:{dbs[0]}?mode=ro', uri=True) as c:
            for t in tables(c, '_modules_*') or []:
                for (u,) in c.execute(f'select distinct family_uid from "{t}" where family_uid is not null'):
                    referenced.add(u)
            for (u,) in c.execute('select distinct family_uid from person'):
                proj_of[u] = d.name
    rows = []
    for u in sorted(n9):
        st = status.get(u)
        cls = ('omics' if st == 'label_only_omics'
               else 'named' if u in named
               else 'numbered' if u in numbered
               else 'specimen' if u in referenced
               else 'empty')
        rows.append({'family': fam, 'family_uid': u, 'id_status': st,
                     'project': proj_of.get(u, ''), 'class': cls})
    return rows


def main():
    fams = sorted(p.name for p in STAGE.iterdir() if p.is_dir())
    rows = []
    for fam in fams:
        rows += audit_family(fam)
    if not rows:
        print('no rows')
        return 0
    csv_path = STAGE / 'seg9_audit.csv'
    with csv_path.open('w', newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=['family', 'family_uid', 'id_status', 'project', 'class'])
        w.writeheader()
        w.writerows(rows)

    agg = {}
    for r in rows:
        agg.setdefault(r['family'], {}).setdefault(r['class'], 0)
        agg[r['family']][r['class']] += 1
    classes = ['named', 'numbered', 'specimen', 'omics', 'empty']
    md = ['# 9 段身份分类审计（只读）', '',
          '口径：`named` 有人名（按现行口径应在 0 段）/ `numbered` 有号码 / '
          '`specimen` 被 modal 表引用 / `omics` 组学标签 / `empty` 真占位', '',
          '| 家族 | ' + ' | '.join(classes) + ' | 合计 |',
          '|---|' + '---|' * (len(classes) + 1)]
    for fam, d in sorted(agg.items()):
        md.append(f"| {fam} | " + ' | '.join(str(d.get(k, 0)) for k in classes)
                  + f" | {sum(d.values())} |")
    tot = {k: sum(d.get(k, 0) for d in agg.values()) for k in classes}
    md.append('| **合计** | ' + ' | '.join(str(tot[k]) for k in classes) + f" | {len(rows)} |")
    md += ['', '明细：`seg9_audit.csv`（逐 uid，含 project 与 id_status）。']
    (STAGE / 'SEG9-AUDIT.md').write_text('\n'.join(md) + '\n', encoding='utf-8')
    print('\n'.join(md))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
