"""DDL 漂移检查（D58）：逐库 pragma table_info 对比 SCHEMA-v1.md 的两个 sql 块。

输出：缺表 / 缺列 / 多列 / 类型不符。P0 口径：任何一项非空即 fail。
"""
import re
import sqlite3
import sys
from pathlib import Path

import paths as _P  # DB5T_BATCH / DB5T_SCHEMA 可覆盖

STAGE = _P.STAGE
SCHEMA = _P.SCHEMA
FAMS = ['MCI', 'LC', 'EC', 'SSD', 'EGVB']

TYPE_MAP = {}


def norm_type(t):
    t = (t or '').strip().upper()
    if not t:
        return ''
    if 'INT' in t:
        return 'INTEGER'
    if any(k in t for k in ('REAL', 'FLOA', 'DOUB')):
        return 'REAL'
    if 'BLOB' in t:
        return 'BLOB'
    if t.startswith('TEXT') or 'CHAR' in t or 'CLOB' in t:
        return 'TEXT'
    if 'NUM' in t or 'DEC' in t:
        return 'NUMERIC'
    return t


def ddl_blocks():
    """SCHEMA 里必须恰好两个 sql 块：family 在前、project 在后。"""
    md = SCHEMA.read_text(encoding='utf-8')
    blocks = re.findall(r'```sql\n(.*?)```', md, re.S)
    assert len(blocks) == 2, f'SCHEMA sql 块数 = {len(blocks)}，应为 2（{SCHEMA}）'
    return blocks[0], blocks[1]


def stmts(sql):
    """切语句，并剔掉夹在 sql 块里的 Markdown 说明行。

    只剔「行首」是说明的行；行内出现 ** 的合法 DDL（如 `person_count INTEGER, -- **不是队列人数**`）
    不能整行删——那会静默丢列（2026-11-25：ref_project 少了 person_count）。
    """
    lines = [line for line in sql.split('\n')
             if not line.lstrip().startswith(('|', '>', '`', '**'))]
    body = '\n'.join(lines)
    return [s.strip() + ';' for s in body.split(';') if s.strip()]


def expected_sets():
    """用 SCHEMA 的 sql 块在空内存库里执行一遍，读 pragma —— 免得手工解析 DDL。"""
    blocks = ddl_blocks()
    out = {}
    for name, blk in (('family', blocks[0]), ('project', blocks[1])):
        conn = sqlite3.connect(':memory:')
        conn.execute('PRAGMA foreign_keys=OFF')
        for st in stmts(blk):
            if '<mod>' in st or 'MODAL_' in st.upper() or '<MOD>' in st:
                continue
            try:
                conn.execute(st)
            except sqlite3.Error as e:
                print(f'  [schema] {name}: 跳过一个语句（{e}）: {st[:60]}')
        exp = {}
        for (t,) in conn.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
            exp[t.lower()] = {r[1]: norm_type(r[2]) for r in conn.execute(f'pragma table_info("{t}")')}
        conn.close()
        if name == 'family':
            # 契 3：dict 只住全局 `_DataBase\Family\dict.db`，家族库里不得再有 dict。
            # schema-v1.md 的第一段仍留着这段 DDL（旧库历史形态），期望集必须剔掉，
            # 否则 D70 会对每个 5t 家族库报 MISSING_TABLE dict —— 那正好是新口径要的形态。
            exp.pop('dict', None)
        out[name] = exp
    # 模板表的期望列：从 build 的 project_ddl 生成的真实库里读不到，改用已知两份
    return out


def check_db(path, expect):
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    have = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    problems = []
    for tname, ecols in expect.items():
        tmpl = tname in ('__modal__', '__val__')
        if tmpl:
            cand = [t for t in have if (t.startswith('_modules_') and tname == '__modal__')
                    or (t.endswith('_val') and tname == '__val__')]
        else:
            cand = [tname] if tname in have else []
        if not cand:
            problems.append(('MISSING_TABLE', tname, '', ''))
            continue
        for t in cand:
            cur = {r[1]: norm_type(r[2]) for r in conn.execute(f'pragma table_info("{t}")')}
            for c, ty in ecols.items():
                if c not in cur:
                    problems.append(('MISSING_COL', t, c, ty))
                elif ty and cur[c] and cur[c] != ty:
                    problems.append(('TYPE', t, c, f'{ty} vs 库内 {cur[c]}'))
            extra = [c for c in cur if c not in ecols]
            if extra and not tmpl and t != 'clin':
                problems.append(('EXTRA_COL', t, ','.join(extra), ''))
    conn.close()
    return problems


def main():
    exp = expected_sets()
    total = 0
    for fam in FAMS:
        famdb = STAGE / fam / '_PHI' / f'{fam}db'
        if not famdb.exists():
            print(f'[skip] {fam}: 无家族库')
            continue
        targets = [(str(famdb), exp['family'])]
        conn = sqlite3.connect(f'file:{famdb}?mode=ro', uri=True)
        slugs = [r[0] for r in conn.execute('select slug from ref_project')]
        conn.close()
        for slug in slugs:
            p = STAGE / fam / slug / f'{slug}db'
            if p.exists():
                targets.append((str(p), exp['project']))
        for path, exps in targets:
            probs = check_db(path, exps)
            total += len(probs)
            tag = Path(path).relative_to(STAGE)
            if not probs:
                print(f'  OK   {tag}')
            else:
                print(f'  DRIFT {tag}: {len(probs)}')
                for kind, t, c, d in probs[:12]:
                    print(f'        {kind} {t}.{c} {d}')
    print(f'\nD58 合计漂移项：{total}')
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())
