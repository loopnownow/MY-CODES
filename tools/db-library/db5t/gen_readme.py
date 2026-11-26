"""生成/刷新 <leaf>/目录说明.md（幂等，由各 build 收尾调用）。

用法：
    python -B gen_readme.py <family>           # 家族库 + 该项目库里所有 ref_project 叶
    python -B gen_readme.py <family>:<slug>     # 只刷一个叶

写法约束（lead m04269）：
  1. 只写白名单文件（<fam>db / 目录说明.md），不碰 _DataBase；
  2. 内容全部现读现算（表清单+行数、src 数、built_at、门禁结果、PHI 边界），不手填；
  3. 幂等：重复调用内容一致（门禁部分读 stage/gate.log 的最新片段）。
"""
import json
import pathlib
import re
import sqlite3
import sys

import paths as _P  # DB5T_BATCH / DB5T_ROOT 可覆盖

STAGE = _P.STAGE
GATELOG = STAGE / 'gate.log'
PHI_FAMILY_TABLES = {'id', 'id_number'}


def q(path, sql, args=()):
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def tables(path):
    rows = q(path, "select name, type from sqlite_master where type in ('table','view') order by type, name")
    out = []
    for name, typ in rows:
        if typ != 'table':
            continue
        n = q(path, f'select count(*) from "{name}"')[0][0]
        out.append((name, n))
    return out


def kv(path):
    try:
        return {k: v for k, v in q(path, 'select k, v from meta')}
    except sqlite3.DatabaseError:
        return {}


def gate_section(path):
    """从 gate.log 取该库最新一段（按路径匹配）。"""
    if not GATELOG.exists():
        return '未运行（gate.log 不存在）'
    txt = GATELOG.read_text(encoding='utf-8', errors='replace')
    key = str(path)
    idx = txt.rfind(key)
    if idx < 0:
        return '未在 gate.log 找到本库记录'
    seg = txt[idx:]
    nxt = seg.find('\n== ', 10)
    if nxt > 0:
        seg = seg[:nxt]
    seg = seg.strip().splitlines()
    keep = [ln.strip() for ln in seg if ('CHECK' in ln or 'integrity' in ln or '全部通过' in ln
                                         or '存在违反' in ln or 'D48' in ln or 'D53' in ln
                                         or 'PHI' in ln.lower() or 'dict 覆盖' in ln)]
    return '\n'.join(f'- {ln}' for ln in keep) if keep else '（记录为空）'


def write_family(fam):
    dbfile = STAGE / fam / '_PHI' / f'{fam}db'
    if not dbfile.exists():
        return None
    m = kv(dbfile)
    tb = tables(dbfile)
    refs = q(dbfile, 'select slug, db_rel_path, person_count, clin_person_n, longitudinal_n, '
                     'multi_record_n, period_unit from ref_project order by slug')
    st = json.loads(m.get('subject_types') or '{}')
    lines = [
        f'# {fam} 家族库（`{fam}db`）',
        '',
        '## 库角色',
        f'- 家族库：身份（`id`/`id_number`）与字典（`dict`）的唯一落点，**PHI 只允许出现在 `id` 表**。',
        f'- 建库脚本：`stage/_work/` 下对应的 build 脚本；生成时间 {m.get("built_at", "未记录")}。',
        f'- 受试者类型：{json.dumps(st, ensure_ascii=False)}',
        f'- uid 段：{m.get("uid_ranges", "未记录")}',
        '',
        '## 表清单与行数',
        '',
        '| 表 | 行数 |',
        '| --- | --- |',
    ]
    lines += [f'| `{n}` | {c} |' for n, c in tb]
    lines += ['', '## 项目子库', '', '| slug | person_count | clin_person_n | longitudinal_n | multi_record_n | period_unit |',
              '| --- | --- | --- | --- | --- | --- |']
    for slug, rel, pc, cpn, ln, mrn, pu in refs:
        lines.append(f'| `{slug}` | {pc} | {cpn} | {ln} | {mrn} | {pu if pu is not None else "—（无时间轴）"} |')
    lines += ['', '## 门禁结果', '', gate_section(dbfile), '',
              '## PHI 边界', '',
              '- 姓名 / 身份证 / 电话 / 地址：**仅** `id.name`、`id.card`、`id.phone`、`id.address`。',
              '- 住院号 / 门诊号 / 影像号 / 病理号：`id_number`（`kind` 成对使用，禁止裸号 join）。',
              '- 子库（`<slug>/<slug>db`）任何列与单元格都不得出现上述值。',
              '', '## 说明', '',
              '- 本文件由 `stage/_work/gen_readme.py` 生成，勿手改；重跑 build 会刷新。']
    (STAGE / fam / '_PHI' / '目录说明.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return f'{fam}/_PHI'


def write_leaf(fam, slug):
    dbfile = STAGE / fam / slug / f'{slug}db'
    if not dbfile.exists():
        return None
    m = kv(dbfile)
    tb = tables(dbfile)
    srcs = q(dbfile, 'select status, count(*) from src group by status order by status')
    lines = [
        f'# {slug} 项目库（`{slug}db`）',
        '',
        '## 库角色',
        '- 项目子库：分析用窄表（`person`/`clin`/`_modules_*`/`*_val`/`qc`），**不含 PHI**。',
        f'- 建库时间 {m.get("built_at", "未记录")}；源库 `{m.get("source_db", "未记录")}`。',
    ]
    if m.get('period_unit'):
        lines.append(f'- 时间轴：`period_unit={m["period_unit"]}` 月。')
    else:
        lines.append(f'- 时间轴：**无**（`period_month` 全 UNK）。{m.get("period_unit_missing_reason", "")}')
    if m.get('joined_sources'):
        lines.append(f'- 拼列来源：`{m["joined_sources"]}`（受控姓名/月份链接，已留 `qc(NAME_LINK)`）。')
    lines += ['', '## 表清单与行数', '', '| 表 | 行数 |', '| --- | --- |']
    lines += [f'| `{n}` | {c} |' for n, c in tb]
    lines += ['', '## 来源', '',
              '| status | 张数 |', '| --- | --- |']
    lines += [f'| `{s}` | {c} |' for s, c in srcs]
    lines += ['', f'- 源记录数 `src_records_n` = {m.get("src_records_n", "未记录")}（应等于 `Σ clin.merged_n`）。',
              '', '## 门禁结果', '', gate_section(dbfile), '',
              '## 说明', '', '- 本文件由 `stage/_work/gen_readme.py` 生成，勿手改。']
    (STAGE / fam / slug / '目录说明.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return f'{fam}/{slug}'


def write_for_db(path):
    """按库文件路径推断 family/slug 并刷新对应说明（build 收尾用）。"""
    p = pathlib.Path(path)
    slug = p.parent.name
    fam = p.parent.parent.name
    if p.parent.name == '_PHI':
        return write_family(fam)
    return write_leaf(fam, slug)


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    if ':' in arg:
        fam, slug = arg.split(':', 1)
        print('wrote', write_leaf(fam, slug))
        return
    fam = arg
    print('wrote', write_family(fam))
    dbfile = STAGE / fam / '_PHI' / f'{fam}db'
    if dbfile.exists():
        for (slug,) in q(dbfile, 'select slug from ref_project order by slug'):
            print('wrote', write_leaf(fam, slug))


if __name__ == '__main__':
    main()
