# -*- coding: utf-8 -*-
"""D82 判据与债务基线（C 路线）：dict.source 按表分级 + meta.d82_debt_rows。

分级（2026-11-27 主人选定 HANDOFF 第 4 节 C 路线）：

  clin / src   严格：只认「可追形态」与 `SCHEMA `；`派生：` 也计债——
               宽表/登记表的每一格数值都应能回到源列，派生说法会盖住「哪一格重编码过」。
  *_val 长表   宽松：`*_val` 是 values 长表（exam_uid / entity / feature / value / unit），
               与 `_modules_<mod>` 的「一次检查一行」配对，**不是验证子集**（验证组是
               `clin.group_v` / `cohort` 列）；删它等于删全部模块测量值，禁止。
               可追 / SCHEMA / `派生：` 都合规——NMR 按 ppm 分箱（回填前 nmr_val 7,000 行）、
               PyRadiomics 特征（回填前 radi_val 3,640 行）本来就不是某张表的某一列，
               强求 `blob:` 只会逼出伪造路径。
  其它结构表   同严格：id / id_number / person / qc / meta / ref_project / _modules_*。

结构列（SYS，与 D78 同一份名单）不参与：它们描述「这次检查怎么来的」，不是源表变量，
source 常写 schema/derived/源表，计入只会把债灌水（旧口径 20,386 行里大半是它们）。

债务 = 该表该等级不接受的 dict 行数。基线记在家族库 `meta.d82_debt_rows`（JSON）：

  {"clin": 1657, "src": 9, "val": 13434, "other": 123, "total": 15223,
   "recorded_on": "2026-11-27"}

门禁只在「高于基线」时判违反（新债），低于基线只提示刷新（待修），
因此既有债不阻塞、但库再长出一行不可追 source 会立刻红——这正是 C 路线的目的。
"""
from __future__ import annotations

import json
import re
import sqlite3

KEY = 'd82_debt_rows'
GRADES = ('clin', 'src', 'val', 'other')

# 与 gates.d78_dict_provenance 的 SYS 同一份名单（结构列不是源表变量）。
SYS = frozenset({
    'family_uid', 'period_month', 'month_ix', 'period_source', 'period_origin',
    'unk_seq', 'observed_on', 'merged_n', 'src_id', 'src_row', 'cohort',
    'exam_uid', 'sequence', 'batch_id', 'lesion_ix', 'specimen_ix',
    'entity', 'feature', 'value', 'unit',
    'device', 'site', 'roi_n', 'note',
})

# 与 D82 原判据逐字一致：blob:<rel>#<sheet>:<col> / <path>#<sheet>:#<N> / …#txt:<标签>
PAT_OK = re.compile(r'^(派生[:：]|SCHEMA |blob:.*#.*:|.*#.*:(#\d+|.*\S))')

# 2026-11-27 主人裁定（rules workspace-layout「clin 可追口径」）：clin 默认不收派生句，
# 但由行序 / 行号 / 计数 / 归一等得到的结构派生列允许 派生： 句，前提是句内点名依据
# （源表 / 源列 / 行序 / 条数 / 归一 / 重编码）。泛泛的「派生：见源表」不算，仍按债计。
PAT_DERIVED_BASIS = re.compile(
    r'派生[:：].*(源表|源列|源行|原列|原表|行序|行号|条数|计数|归一|重编码|命中行数|合并多来源)')

# 2026-11-27 主人裁定（m01376「认旧库」）：clin 的列若只能定位到迁移前旧库快照
# （家族级、列级原件未登记），允许写「派生：旧库迁移——家族自 <FAM>db@<日期> 迁入，
# 列级原件未登记」。句内必须点名快照库与日期；只报数，不记债。
PAT_DERIVED_OLDDB = re.compile(
    r'派生[:：].*(旧库|快照).*(迁入|迁移).*(@\d{4}-\d{2}-\d{2}|未登记)')

ACCEPT = {
    'clin': frozenset(('TRACE', 'SCHEMA')),
    'src': frozenset(('TRACE', 'SCHEMA')),
    'val': frozenset(('TRACE', 'SCHEMA', 'DERIVED')),
    'other': frozenset(('TRACE', 'SCHEMA')),
}

LABEL = {'clin': 'clin', 'src': 'src', 'val': '*_val', 'other': '其它'}


def classify(src):
    """source 形态：TRACE / SCHEMA / DERIVED / EMPTY / BAD。"""
    v = (src or '').strip()
    if not v:
        return 'EMPTY'
    if v.startswith(('派生:', '派生：')):
        return 'DERIVED'
    if v.startswith('SCHEMA '):
        return 'SCHEMA'
    if PAT_OK.match(v):
        return 'TRACE'
    return 'BAD'


def grade_of(table_name):
    """表名 → 分级键。长表按 `*_val` 后缀判，兼容 `<mod>_val` 与 `radiomics_val`。"""
    t = (table_name or '').strip()
    if t == 'clin':
        return 'clin'
    if t == 'src':
        return 'src'
    if t.endswith('_val'):
        return 'val'
    return 'other'


def scan(conn):
    """只读扫描 dict，返回统计与债务行。conn 必须是家族库只读连接。"""
    st = {'total': 0, 'trace': 0, 'schema': 0, 'derived': 0, 'empty': 0, 'bad': 0,
          'debt': dict({g: 0 for g in GRADES}, total=0), 'tables': {}, 'examples': {}, 'rows': []}
    try:
        rows = list(conn.execute(
            "select coalesce(table_name,''), column_name, coalesce(source,'') from dict"))
    except sqlite3.Error:
        return st
    for table, column, source in rows:
        if column in SYS:
            continue
        st['total'] += 1
        kind = classify(source)
        st[kind.lower()] = st.get(kind.lower(), 0) + 1
        g = grade_of(table)
        if kind in ACCEPT[g]:
            continue
        if kind == 'DERIVED' and g == 'clin' and PAT_DERIVED_BASIS.search(source or ''):
            # clin 结构派生列：句内点名依据即算可追，只报数不记债。
            st['clin_derived'] = st.get('clin_derived', 0) + 1
            continue
        if kind == 'DERIVED' and g == 'clin' and PAT_DERIVED_OLDDB.search(source or ''):
            # clin 旧库迁移列：家族级可追到快照，列级原件未登记，只报数不记债。
            st['clin_olddb'] = st.get('clin_olddb', 0) + 1
            continue
        st['debt'][g] += 1
        st['debt']['total'] = st['debt'].get('total', 0) + 1
        st['tables'][(table, g)] = st['tables'].get((table, g), 0) + 1
        st['rows'].append((table, column, g, kind, (source or '').strip()))
        ex = st['examples'].setdefault(g, [])
        if len(ex) < 3:
            ex.append('%s.%s=%r' % (table, column, (source or '').strip()))
    return st


def load_baseline(conn):
    """读 meta.d82_debt_rows。返回 {grade..., total, recorded_on} 或 None。

    兼容早期只写整数的形态；缺某个分级键时该键为 None（比较时退回总数口径）。
    """
    try:
        row = conn.execute("select v from meta where k=?", (KEY,)).fetchone()
    except sqlite3.Error:
        return None
    if not row or row[0] is None or not str(row[0]).strip():
        return None
    raw = str(row[0]).strip()
    try:
        obj = json.loads(raw)
    except ValueError:
        return {'total': int(raw)} if raw.isdigit() else None
    if isinstance(obj, int):
        return {'total': obj}
    if not isinstance(obj, dict):
        return None

    def as_int(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    out = {g: as_int(obj.get(g)) for g in GRADES}
    out['total'] = as_int(obj.get('total'))
    out['recorded_on'] = str(obj.get('recorded_on') or '')
    return out


def compare(stats, base):
    """(新增债行数, 状态, 明细)。状态：ok / new / stale / no-baseline。"""
    debt = stats['debt']
    total = debt.get('total', 0)
    if base is None:
        return 0, 'no-baseline', {}
    if base.get('total') is None or any(base.get(g) is None for g in GRADES):
        # 只有总数可比：整数老口径，或 JSON 缺键。
        b = base.get('total') or 0
        if total > b:
            return total - b, 'new', {'total': total - b}
        return (0, 'stale', {'removed': b - total}) if total < b else (0, 'ok', {})
    new, detail = 0, {}
    for g in GRADES:
        d = debt.get(g, 0) - (base.get(g) or 0)
        if d > 0:
            detail[g] = d
            new += d
    if new:
        return new, 'new', detail
    if total < (base.get('total') or 0):
        return 0, 'stale', {'removed': (base.get('total') or 0) - total}
    return 0, 'ok', detail


def payload(stats, day):
    """要写进 meta.d82_debt_rows 的 JSON 文本。"""
    d = stats['debt']
    obj = {g: d.get(g, 0) for g in GRADES}
    obj['total'] = d.get('total', 0)
    obj['recorded_on'] = day
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def write_baseline(fam_db, stats, day):
    """把基线写进家族库 meta（幂等）。改的是家族库自身，不动子库，无需重算 ref_project.sha256。"""
    conn = sqlite3.connect(str(fam_db))
    try:
        conn.execute(
            "insert into meta(k, v) values(?, ?) "
            "on conflict(k) do update set v=excluded.v",
            (KEY, payload(stats, day)))
        conn.commit()
    finally:
        conn.close()
