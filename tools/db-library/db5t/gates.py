# -*- coding: utf-8 -*-
"""机器门：integrity / FK / CHECK 逐条复验 / PHI 值级对账 / 语义门（§3、§8.4）。
只读 stage 产物。用法: python gates.py  -> 写 stage/gate.log
"""
import os
import datetime
import sqlite3
import hashlib
import pathlib
import json
import re
import sys
from pathlib import Path
import paths as B  # 路径解析集中在这里；DB5T_ROOT 可指向 _DataBase 验切库后的真库

STAGE = pathlib.Path(os.environ.get('DB5T_ROOT') or B.STAGE)
FAM = sys.argv[1] if len(sys.argv) > 1 else 'MCI'      # MCI | LC | SSD
FAM_DB = STAGE / FAM / '_db' / f'{FAM}db'
LOG = STAGE / 'gate.log'
# 子库清单从家族库 ref_project 读（不再硬编码），保持门禁对新家族通用
PROJ = []
if FAM_DB.exists():
    _c = sqlite3.connect(f'file:{FAM_DB}?mode=ro', uri=True)
    PROJ = [(sl, STAGE / FAM / sl / f'{sl}db') for (sl,) in _c.execute('select slug from ref_project order by slug')]
    _c.close()
SCHEMA = (B.SCHEMA_MD).read_text(encoding='utf-8')


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()



def d65_origin_unknown(path):
    """D65（只报数不判级）：有日期但 period_origin 未知的行数。"""
    out = []
    try:
        cols = {r[1] for r in sqlite3.connect(f'file:{path}?mode=ro', uri=True).execute('pragma table_info(clin)')}
    except Exception:
        return out
    c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    if 'period_origin' in cols:
        n_all, n_null = c.execute("select sum(period_month<>'UNK'), "
                                  "sum(period_month<>'UNK' and period_origin is null) from clin").fetchone()
        out.append(f'D65 有日期 {n_all or 0} 行，其中 period_origin 未知 {n_null or 0} 行（只报数，不判级）')
    c.close()
    return out

def checks_of(conn):
    """[(table, expr)] —— 从 sqlite_master 的 DDL 里取 CHECK(...)"""
    out = []
    for name, sql in conn.execute("select name,sql from sqlite_master where type='table'"):
        if not sql:
            continue
        for m in re.finditer(r'CHECK\s*\(', sql, re.I):
            i, depth = m.end(), 1
            while i < len(sql) and depth:
                if sql[i] == '(':
                    depth += 1
                elif sql[i] == ')':
                    depth -= 1
                i += 1
            out.append((name, sql[m.end():i - 1].strip()))
    return out


def check_gate(conn, tag):
    rows = []
    for t, expr in checks_of(conn):
        try:
            n = conn.execute(f'select count(*) from {t} where not ({expr})').fetchone()[0]
            rows.append((tag, t, expr[:90], n))
        except sqlite3.Error as e:
            rows.append((tag, t, expr[:90], f'ERR:{e}'))
    return rows


def phi_values():
    c = sqlite3.connect(f'file:{FAM_DB}?mode=ro', uri=True)
    vals = set()
    for sql in ["select name from id where name is not null",
                "select id_card from id where id_card is not null",
                "select phone from id where phone is not null",
                "select address from id where address is not null"]:
        vals |= {r[0].strip() for r in c.execute(sql) if r[0] and str(r[0]).strip()}
    c.close()
    return vals


# 这些列本身就是「号码列」：号码出现在这里不算泄漏，只算跨命名空间碰撞（ID_COLLISION）。
# subject_label 是 person 的样本名/行标签（flow 行拿标本号当标签），属同类。
KEY_COLS = {'sample_no','flow_id','ct_id','ename','id_number_value','visit_no','path_no','case_id','study_uid','path_real','files','image_id','subject_label'}


def idnum_leak(path, fam_db):
    """院号/病历号/门诊号（id_number.kind ∈ mrn|inpatient|outpatient）不得出现在子库任何 TEXT 列。"""
    f = sqlite3.connect(f'file:{fam_db}?mode=ro', uri=True)
    _rows = list(f.execute(
        "select value, coalesce(issuer,'') from id_number "
        "where kind in ('mrn','inpatient','outpatient') and value is not null"))
    no_issuer = sum(1 for v, iss in _rows if v and len(v.strip()) >= 4 and not iss.strip())
    ids = {v.strip() for v, _ in _rows if v and len(v.strip()) >= 4}
    f.close()
    if not ids:
        return []
    c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    hits = []
    for (t,) in c.execute("select name from sqlite_master where type='table'"):
        for r in c.execute(f'pragma table_info("{t}")'):
            col = r[1]
            try:
                vals = {x[0].strip() for x in c.execute(f'select distinct "{col}" from "{t}"') if isinstance(x[0], str)}
            except sqlite3.Error:
                continue
            inter = vals & ids
            if inter:
                # 键列（样本号/流式号/CT 号/英文名）与院号字面碰撞属跨命名空间巧合：不掩码、单列写出
                _cl = col.lower()
                kind = ('IDNUM_COLLISION_KEYCOL'
                        if any(k in _cl for k in KEY_COLS) else 'IDNUM_LEAK')
                if kind == 'IDNUM_LEAK' and len(inter) < 2:
                    continue   # 单值巧合（如年龄/分期串恰好等于某个 4 位号）不判泄漏，需 ≥2 个不同值才算
                hits.append((t, col, kind, len(inter)))
    c.close()
    if no_issuer:
        hits.append(('id_number', 'issuer', 'BARE_ID_CANDIDATE', no_issuer))
    return hits


def phi_scan(path, phis):
    """PHI 双判据：①列名形状（姓名/电话/住院号/身份证/地址…）；②值集交集——子库任一列的非空值
    与家族库 id.phone/id_card/address 值集相交 → P0（见 d54_phi_valueset）。只做①会漏掉
    「列名叫 联系电话、里面是手机号」这类：列名不带 name 也照样是 PHI。"""
    c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    hits = []
    for (t,) in c.execute("select name from sqlite_master where type='table'").fetchall():
        cols = [r[1] for r in c.execute(f'pragma table_info({t})')]
        for col in cols:
            lc = col.lower()
            if lc in ('name', 'cname', 'patient_name', 'person_name') or '姓名' in col:
                hits.append((t, col, 'COLUMN_NAME', None))
            try:
                vals = {r[0].strip() for r in c.execute(f'select distinct "{col}" from "{t}"') if isinstance(r[0], str)}
            except sqlite3.Error:
                continue
            inter = vals & phis
            if inter and t == 'person' and col == 'subject_label':
                # lead 口径：拉丁名可进 person.subject_label（不进 id.name）；只对非 ASCII 值报警
                inter = {v for v in inter if any(ord(ch) > 127 for ch in v)}
            if inter:
                hits.append((t, col, 'VALUE', len(inter)))
    c.close()
    return hits


def dict_cov(path, scope, fam_db):
    c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    f = sqlite3.connect(f'file:{fam_db}?mode=ro', uri=True)
    rows = {(r[0], r[1]) for r in f.execute('select table_name,column_name from dict where scope=?', (scope,))}
    cols = [r[1] for r in c.execute('pragma table_info(clin)')]
    missing = [x for x in cols if ('clin', x) not in rows]
    extra = [x for x in rows if x[0] == 'clin' and x[1] not in cols]
    tabs = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    # 通用：任一 <mod>_val（裁决 3：按模态成对建表）其 feature 必须逐条 dict
    for t in sorted(tabs):
        if not t.endswith('_val'):
            continue
        feats = sorted({r[0] for r in c.execute(f'select distinct feature from "{t}"')})
        miss_f = [x for x in feats if (t, x) not in rows]
        extra_f = [x for x in rows if x[0] == t and x[1] not in feats
                   and x[1] not in ('exam_uid', 'entity', 'feature', 'value', 'unit')]
        missing = missing + [f'{t}.feature:{x}' for x in miss_f]
        extra = extra + [f'{t}.feature:{x}' for x in extra_f]
    for t in sorted(tabs):
        if not t.startswith('modal_'):
            continue
        mcols = [r[1] for r in c.execute(f'pragma table_info("{t}")')]
        missing = missing + [f'{t}.{x}' for x in mcols if (t, x) not in rows]
        extra = extra + [f'{t}.{x}' for x in rows if x[0] == t and x[1] not in mcols]
    if False and 'radiomics_val' in tabs:
        feats = sorted({r[0] for r in c.execute('select distinct feature from radiomics_val')})
        miss_f = [x for x in feats if ('radiomics_val', x) not in rows]
        extra_f = [x for x in rows if x[0] == 'radiomics_val' and x[1] not in feats
                   and x[1] not in ('exam_uid', 'entity', 'feature', 'value', 'unit')]
        missing = missing + [f'radiomics_val.feature:{x}' for x in miss_f]
        extra = extra + [f'radiomics_val.feature:{x}' for x in extra_f]
    if 'modal_radiomics' in tabs:
        mcols = [r[1] for r in c.execute('pragma table_info(modal_radiomics)')]
        missing = missing + [f'modal_radiomics.{x}' for x in mcols if ('modal_radiomics', x) not in rows]
        extra = extra + [f'modal_radiomics.{x}' for x in rows if x[0] == 'modal_radiomics' and x[1] not in mcols]
    c.close(); f.close()
    return missing, extra


def parent_gates(path):
    """§2 顺序门：<mod>_val 有行但 modal_<mod> 里没有对应 exam，说明重建顺序错了
    （典型：先删 modal_cells 再插 cells_val → 裸 FOREIGN KEY constraint failed）。
    明确报「父表 exam 缺失，按 §2 顺序重建」，不要让人对着 FK 报错猜。"""
    c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    bad = []
    tabs = {t for (t,) in c.execute("select name from sqlite_master where type='table'")}
    for (t,) in list(c.execute("select name from sqlite_master where type='table'")):
        if not t.endswith('_val'):
            continue
        mod = t[:-4]
        parent = f'modal_{mod}'
        if parent not in tabs:
            bad.append(('P0', f'{t} 存在但缺少父表 {parent}（§2 应按模态成对建表）', 1))
            continue
        n = c.execute(f'select count(*) from "{t}"').fetchone()[0]
        if not n:
            continue
        orphan = c.execute(f'select count(*) from "{t}" v where not exists '
                           f'(select 1 from "{parent}" m where m.exam_uid=v.exam_uid)').fetchone()[0]
        if orphan:
            bad.append(('P0', f'{t} 有 {orphan} 行的 exam_uid 在 {parent} 里不存在'
                              f'（父表 exam 缺失，按 §2 顺序重建：先删 *_val 与非 cells modal → '
                              f'build him/proj 重建 modal_cells → 再跑 omics）', orphan))
    c.close()
    return bad


def semantic_gates(path):
    """§3 / §8.4 语义门：
    - period_unit 只能 {1,3,6} 或 缺失（缺失 = 无时间轴，唯一标记）
    - period_unit 缺失 => clin 必须全 UNK；有 dated 行 => P0
    - qc 的 scope/table/column 不得借位
    """
    c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    bad = []
    meta = dict(c.execute('select k,v from meta').fetchall())
    unit = meta.get('period_unit')
    dated = c.execute("select count(*) from clin where period_month<>'UNK'").fetchone()[0]
    if 'no_time_axis' in meta:
        bad.append(('P0', '仍有 no_time_axis 键（v1.3.1 已删，唯一标记是 period_unit 缺失）', 1))
    if unit is None and dated > 0:
        bad.append(('P0', 'period_unit 缺失（=无时间轴）但库内有 dated 行', dated))
    if unit is not None and unit not in ('1', '3', '6'):
        bad.append(('P0', f'period_unit={unit!r} 不在 {{1,3,6}}', 1))
    for code, sev, sc, tb, col, detail in c.execute(
            'select code,severity,scope,table_name,column_name,substr(detail,1,40) from qc').fetchall():
        if code in ('ROW_DROPPED', 'ID_MISSING', 'MODAL_ABSENT', 'CROSS_PROJECT_NO_KEY') and col is not None:
            bad.append(('P1', f'{code} 应无 column_name，实际 {col!r}', 1))
        if code == 'CROSS_PROJECT_NO_KEY' and (sc != 'family' or tb is not None):
            bad.append(('P1', f'CROSS_PROJECT_NO_KEY 应为库级(family/NULL/NULL)，实际 {sc}/{tb}', 1))
        if code == 'ROW_DROPPED' and sc != 'clin':
            bad.append(('P1', f'ROW_DROPPED 应 scope=clin，实际 {sc}', 1))
        if code == 'HEADER_LOST' and col is None:
            bad.append(('P1', 'HEADER_LOST 应指向具体列（#n）', 1))
        if code == 'MODAL_ABSENT' and tb is not None:
            bad.append(('P1', f'MODAL_ABSENT 应项目级(table NULL)，实际 {tb}', 1))
        if code in ('ENUM_UNMAPPED', 'GROUP_INFERRED') and col is None:
            bad.append(('P1', f'{code} 应指向具体列', 1))
        if sev not in ('P0', 'P1', 'P2'):
            bad.append(('P1', f'severity={sev!r} 非法', 1))
    c.close()
    return bad


def mirror_gates():
    """家族库 ref_project.period_unit 必须镜像子库 meta.period_unit（不一致 P0）。"""
    bad = []
    f = sqlite3.connect(f'file:{FAM_DB}?mode=ro', uri=True)
    rows = f.execute('select slug,db_rel_path,period_unit,sha256,person_count,clin_person_n,'
                     'longitudinal_n,multi_record_n from ref_project').fetchall()
    f.close()
    for slug, rel, fam_unit, fam_sha, fam_pn, fam_cn, fam_ln, fam_mn in rows:
        p = STAGE / FAM / slug / f'{slug}db'
        if not p.exists():
            bad.append(('P0', f'ref_project.{slug} 指向的子库不存在: {p}', 1))
            continue
        c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        sub_unit = dict(c.execute('select k,v from meta').fetchall()).get('period_unit')
        c.close()
        # 数值比较：家族库列是 INTEGER，子库 meta 是 k/v 文本，直接比会 '6' != 6 造成假失败
        def _u(v):
            if v in (None, ''):
                return None
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return 'INVALID:' + str(v)
        fu, su = _u(fam_unit), _u(sub_unit)
        if isinstance(fu, str) or isinstance(su, str):
            bad.append(('P0', f'ref_project.{slug}.period_unit 非整数: fam={fam_unit!r} sub={sub_unit!r}', 1))
        elif fu != su:
            bad.append(('P0', f'ref_project.{slug}.period_unit={fam_unit!r} ≠ 子库 meta.period_unit={sub_unit!r}', 1))
        # D35 的四计数项：家族库镜像值必须与子库现算值逐项相等（登记值只是镜像，真相在子库）
        c2 = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        try:
            pn = c2.execute('select count(*) from person').fetchone()[0]
            cn, mn = c2.execute(
                'select count(distinct family_uid),'
                ' count(distinct case when nm>1 then family_uid end)'
                ' from (select family_uid, count(*) nm from clin group by 1)').fetchone()
            # 纵向：同一人在 >=2 个不同的非 UNK 月份出现过
            ln = c2.execute("select count(*) from (select family_uid from clin"
                            " where period_month is not null and period_month<>'UNK'"
                            " group by 1 having count(distinct period_month)>1)").fetchone()[0]
        except sqlite3.Error:
            pn = cn = ln = mn = None
        c2.close()
        if pn is not None:
            for label, fam_v, real_v in (('person_count', fam_pn, pn),
                                         ('clin_person_n', fam_cn, cn),
                                         ('longitudinal_n', fam_ln, ln),
                                         ('multi_record_n', fam_mn, mn)):
                if fam_v is None or int(fam_v) != int(real_v):
                    bad.append(('P0', f'ref_project.{slug}.{label}={fam_v!r} ≠ 子库现算 {real_v}', 1))
        # D35 的 sha 项：ref_project.sha256 必须等于子库文件当前 sha256（冻结有据）
        cur = sha(str(p))
        if not fam_sha:
            bad.append(('P0', f'ref_project.{slug}.sha256 为空（冻结无据）', 1))
        elif fam_sha != cur:
            bad.append(('P0', f'ref_project.{slug}.sha256={fam_sha[:12]}… ≠ 子库文件 sha256={cur[:12]}…', 1))
    return bad



def longitudinal_gate():
    """ref_project.longitudinal_n 必须 = 子库 meta.longitudinal_n = 实时重算值（任一不等 P0）。

    定义见 SCHEMA-v1.md:85：有 >=2 个不同 period_month 的人数（不含 UNK）。
    布尔标记会成为第三套真值；计数可复核，所以这里每次都重算。
    """
    bad = []
    f = sqlite3.connect(f'file:{FAM_DB}?mode=ro', uri=True)
    rows = f.execute('select slug,longitudinal_n from ref_project').fetchall()
    f.close()
    for slug, fam_n in rows:
        p = STAGE / FAM / slug / f'{slug}db'
        if not p.exists():
            continue
        c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        live = c.execute("select count(*) from (select family_uid from clin where period_month<>'UNK' "
                         "group by family_uid having count(distinct period_month)>1)").fetchone()[0]
        sub_n = dict(c.execute('select k,v from meta').fetchall()).get('longitudinal_n')
        c.close()
        if fam_n != live:
            bad.append(('P0', f'ref_project.{slug}.longitudinal_n={fam_n!r} ≠ 实时重算={live}', 1))
        if sub_n is None or str(sub_n) != str(live):
            bad.append(('P0', f'子库 meta.longitudinal_n={sub_n!r} ≠ 实时重算={live}', 1))
    return bad


def multi_record_gate():
    """multi_record_n（有 >1 条 clin 行的人数，不论有无日期）镜像 + 实时重算（不一致 P0）。

    另外一条语义闸（SCHEMA §8.7 同精神）：某人有多条**全为 UNK**的 clin 行时，必须有一条
    qc(code='UNK_MULTI_RECORD')——「同一人多条无日期记录」既可能是多次就诊，也可能是身份复用，
    事故就是因为没人问这句话而一路过闸。有此情形而无 qc → P1。
    """
    bad = []
    f = sqlite3.connect(f'file:{FAM_DB}?mode=ro', uri=True)
    colnames = [r[1] for r in f.execute('pragma table_info(ref_project)')]
    rows = f.execute('select slug,multi_record_n from ref_project').fetchall() if 'multi_record_n' in colnames else []
    f.close()
    for slug, fam_n in rows:
        p = STAGE / FAM / slug / f'{slug}db'
        if not p.exists():
            continue
        c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        live = c.execute('select count(*) from (select family_uid from clin group by family_uid '
                         'having count(*)>1)').fetchone()[0]
        sub_n = dict(c.execute('select k,v from meta').fetchall()).get('multi_record_n')
        unk_multi = c.execute("select count(*) from (select family_uid from clin where period_month='UNK' "
                              "group by family_uid having count(*)>1)").fetchone()[0]
        has_qc = c.execute("select count(*) from qc where code='UNK_MULTI_RECORD'").fetchone()[0]
        c.close()
        if fam_n != live:
            bad.append(('P0', f'ref_project.{slug}.multi_record_n={fam_n!r} ≠ 实时重算={live}', 1))
        if sub_n is None or str(sub_n) != str(live):
            bad.append(('P0', f'子库 meta.multi_record_n={sub_n!r} ≠ 实时重算={live}', 1))
        if unk_multi and not has_qc:
            bad.append(('P1', f'{slug}: {unk_multi} 人有多条全 UNK 的 clin 行，却无 qc(UNK_MULTI_RECORD)', 1))
    return bad


def d48_d49_d50(path):
    """D48 后缀变量族 P0 / D49 一人多行半套变量 P1 / D50 Σmerged_n 守恒 P1。"""
    out = []
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    cols = [r[1] for r in conn.execute('pragma table_info(clin)')]
    BASE = {'family_uid', 'period_month', 'month_ix', 'period_source', 'unk_seq', 'observed_on',
            'merged_n', 'src_id', 'src_row', 'cohort'}
    fams = {}
    for c in cols:
        if c in BASE:
            continue
        m = re.match(r'^(.*?)_(\d+)$', c)
        if m:
            fams.setdefault(m.group(1), []).append(c)
    fams = {k: v for k, v in fams.items() if len(v) >= 2}
    if fams:
        out.append(f'D48 后缀变量族 {len(fams)} 个：' + '; '.join(f'{k}×{len(v)}' for k, v in list(fams.items())[:8]) + ' !!')
    else:
        out.append('D48 后缀变量族 0 个 .')
    multi = conn.execute('select count(*) from (select family_uid from clin group by 1 having count(*)>1)').fetchone()[0]
    # 真缺陷 = 同人同月多行（有日期却仍拆行）；纵向项目的多行是设计
    dup_month = conn.execute("select count(*) from (select family_uid, period_month from clin "
                             "where period_month<>'UNK' group by 1,2 having count(*)>1)").fetchone()[0]
    unk_multi = conn.execute("select count(*) from (select family_uid from clin "
                             "where period_month='UNK' group by 1 having count(*)>1)").fetchone()[0]
    # 无时间轴项目（period_unit 未声明）里「同人多次测量、无日期排序」是设计而非缺陷：
    # 需同时满足 ①meta 无 period_unit ②全部行 UNK ③有 qc(UNK_MULTI_RECORD) ④multi_record_n 已登记
    pu = conn.execute("select v from meta where k='period_unit'").fetchone()
    nunk = conn.execute("select count(*) from clin where period_month<>'UNK'").fetchone()[0]
    unk_qc = conn.execute("select count(*) from qc where code='UNK_MULTI_RECORD'").fetchone()[0]
    split_rows = 0
    split_zero = 0
    if multi:
        # 「变量被拆到多行」：任两行非空列集合交集 < 30% 且各自非空列 >= 5
        vcols = [r[1] for r in conn.execute('pragma table_info(clin)')
                 if r[1] not in ('family_uid', 'period_month', 'month_ix', 'period_source',
                                 'unk_seq', 'observed_on', 'merged_n', 'src_id', 'src_row',
                                 'cohort', 'period_origin')]
        if not vcols:
            return []
        sets = {}
        for r in conn.execute('select rowid, family_uid, period_month, ' + ','.join('"'+c+'"' for c in vcols) + ' from clin'):
            nn = {c for c, v in zip(vcols, r[3:]) if v is not None}
            sets.setdefault(r[1], []).append((r[2], nn))
        for uid, ss in sets.items():
            if len(ss) < 2:
                continue
            bad_pair = False
            for i in range(len(ss)):
                for j in range(i + 1, len(ss)):
                    (ma, a), (mb, b) = ss[i], ss[j]
                    # 四条件同时成立才算「半套变量被拆到两行」（D49 第②支）：
                    # ①一有日期一 UNK（EC v2 指纹）②各自非空 >=20（半套变量的前提）
                    # ③交集/较小侧 <30% ④两行不属互不相同的 prefix__ 测量块
                    if not ((ma == 'UNK') != (mb == 'UNK')):
                        continue
                    if len(a) < 20 or len(b) < 20:
                        continue
                    pa = {c.split('__')[0] for c in a if '__' in c}
                    pb = {c.split('__')[0] for c in b if '__' in c}
                    if pa and pb and not (pa & pb):
                        continue
                    inter = len(a & b) / max(min(len(a), len(b)), 1)
                    if inter < 0.3:
                        bad_pair = True
            if bad_pair:
                split_rows += 1
                if all(not (ss[i][1] & ss[j][1]) for i in range(len(ss)) for j in range(i + 1, len(ss))):
                    split_zero += 1
        br3 = max(multi - dup_month - split_rows, 0)
        out.append(f'D49 三支：①同人同月多行 {dup_month} 组 P1；②半套变量拆行 {split_rows} 人 P1'
                   f'（其中交集为 0、属不同测量矩阵 {split_zero} 人）；③其余多行 {br3} 人 P2（须有 '
                   f'meta.multi_record_reason）'
                   + (' !!' if (dup_month or split_rows - split_zero) else ' .'))
    if not multi:
        out.append('D49 一人多行 0 .')
    elif dup_month:
        out.append(f'D49 同人同月多行 {dup_month} 组（应聚成一行） !!')
    elif pu is None and nunk == 0 and unk_qc > 0:
        out.append(f'D49 一人多行 {multi} 人：无时间轴项目（period_unit 未声明、全 UNK、'
                   f'已登 UNK_MULTI_RECORD），按重复测量处理，不计缺陷 .')
    elif unk_multi == 0 or unk_qc > 0:
        out.append(f'D49 一人多行 {multi} 人：均为不同月份（纵向）或已登 UNK_MULTI_RECORD，不判缺陷 .')
    else:
        out.append(f'D49 一人多行 {multi} 人（同月未违规、UNK 多条未登 qc） !!')
    tot = conn.execute('select count(*), coalesce(sum(merged_n),0) from clin').fetchone()
    src_rec = conn.execute("select v from meta where k='src_records_n'").fetchone()
    if src_rec is None:
        out.append('D50 meta.src_records_n 缺失 !!')
    elif int(src_rec[0]) != tot[1]:
        out.append(f'D50 Σmerged_n {tot[1]} ≠ 源记录 {src_rec[0]} !!')
    else:
        out.append(f'D50 Σmerged_n={tot[1]} = 源记录 {src_rec[0]}，clin {tot[0]} 行 .')
    conn.close()
    return out



def _norm_feat(f):
    """特征名归一化：小写、去掉末尾标点、Shape. -> shape_。用于 D51。"""
    g = f.strip().lower().rstrip('.').rstrip('_')
    g = g.replace('shape.', 'shape_')
    return g


def d51_d52(path):
    """D51 特征名大小写/标点折叠后重复 P0；D52 excluded_columns 结构化 + 被排除列不得出现在库里 P1。"""
    out = []
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    tabs = [r[0] for r in conn.execute("select name from sqlite_master where type='table'")]
    hits = []
    for t in tabs:
        if not t.endswith('_val'):
            continue
        feats = [r[0] for r in conn.execute(f'select distinct feature from "{t}"')]
        groups = {}
        for f in feats:
            groups.setdefault(_norm_feat(f), set()).add(f)
        for k, v in groups.items():
            if len(v) > 1:
                hits.append(f'{t}: ' + ' | '.join(sorted(v)))
    if hits:
        out.append(f'D51 特征名折叠后重复 {len(hits)} 组：' + '; '.join(hits[:5]) + ' !!')
    else:
        out.append('D51 特征名折叠后重复 0 组 .')
    row = conn.execute("select v from meta where k='excluded_columns'").fetchone()
    if row is None:
        out.append('D52 meta.excluded_columns 缺失 !!')
    else:
        try:
            items = json.loads(row[0])
        except Exception:
            items = None
        if not isinstance(items, list):
            out.append('D52 meta.excluded_columns 不是 JSON 数组 !!')
        else:
            bad = [i for i in items if not isinstance(i, dict) or not str(i.get('reason', '')).strip()]
            still = []
            for it in items:
                col = (it or {}).get('column') if isinstance(it, dict) else None
                if not col:
                    continue
                for t in tabs:
                    cols = [r[1] for r in conn.execute(f'pragma table_info("{t}")')]
                    if col in cols:
                        still.append(f'{t}.{col}')
            if bad:
                out.append(f'D52 excluded_columns 有 {len(bad)} 条缺 reason !!')
            if still:
                out.append(f'D52 已排除的列仍在库里：{still} !!')
            if not bad and not still:
                out.append(f'D52 excluded_columns {len(items)} 条结构化且列已不在库 .')
    # 被排除列不得留在 clin：clin 里若还有「自由文本报告」列，靠 D52 的 still 报
    conn.close()
    return out



def d53_placeholder_values(path):
    """D53 占位行（9 段 uid）若带源记录却值列全空 -> P1（静默清空）。"""
    out = []
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    cols = [r[1] for r in conn.execute('pragma table_info(clin)')]
    BASE = {'family_uid', 'period_month', 'month_ix', 'period_source', 'unk_seq', 'observed_on',
            'merged_n', 'src_id', 'src_row', 'cohort'}
    vcols = [c for c in cols if c not in BASE]
    ph = "cast(substr(family_uid, instr(family_uid,'-')+1) as integer) >= 900000"
    total = conn.execute(f'select count(*) from clin where {ph}').fetchone()[0]
    if not total:
        out.append('D53 占位行 0 行 .')
        conn.close()
        return out
    wl = set()
    r = conn.execute("select v from meta where k='name_only_uids'").fetchone()
    if r and r[0]:
        try:
            wl = set(json.loads(r[0]))
        except Exception:
            wl = set()
    CH = 200
    withval_ids, cells = set(), 0
    for i in range(0, len(vcols), CH):
        sub = vcols[i:i + CH]
        nn = ' or '.join(f'"{c}" is not null' for c in sub)
        withval_ids.update(r[0] for r in conn.execute(f'select rowid from clin where {ph} and ({nn})'))
        cells += conn.execute('select coalesce(sum(n),0) from (select (' +
                              ' + '.join(f'("{c}" is not null)' for c in sub) +
                              f') as n from clin where {ph})').fetchone()[0] or 0
    withval = len(withval_ids)
    emp_rows = []
    for r in conn.execute(f'select rowid, family_uid from clin where {ph} and merged_n>0'):
        if r[0] not in withval_ids:
            emp_rows.append(r[1])
    empty = len([u for u in emp_rows if u not in wl])
    empty_wl = len(emp_rows) - empty
    msg = (f'D53 占位行 {total} 行，其中带值 {withval} 行（共 {cells} 个值格），'
           f'有源记录却全空 {empty} 行')
    if empty_wl:
        msg += f'（另有 {empty_wl} 行已由 meta.name_only_uids 说明：仅姓名、无临床值）'
    out.append(msg + (' !!' if empty else ' .'))
    if empty:
        uids = [u for u in emp_rows if u not in wl][:8]
        out.append('   !! 样例 uid：' + ', '.join(uids))
    conn.close()
    return out


def d54_phi_valueset(path, fam_db, scope=None):
    """D54（lead 裁决：行级对齐，不是数次数）。
    P0 真泄漏：某格的值 == 该行 family_uid 名下的 id_number.value / id.phone / id_card / address。
    P2 巧合：格值等于**别人**的号码 → 只报计数，不报警。
    键列从 dict.role='key' 读（不写死名单）；结构列 family_uid 恒跳过。
    """
    out = []
    own = {}          # uid -> set(values)
    every = set()     # 全部号码值（用于巧合桶）
    fc = sqlite3.connect(f'file:{fam_db}?mode=ro', uri=True)
    try:
        for uid, col, v in fc.execute(
                "select family_uid, 'id_number', value from id_number where value is not null"):
            v = str(v).strip()
            if len(v) >= 4:
                own.setdefault(uid, set()).add(v); every.add(v)
        for col in ('phone', 'id_card', 'address'):
            for uid, v in fc.execute(f'select family_uid, {col} from id where {col} is not null and {col}<>""'):
                v = str(v).strip()
                if len(v) >= 4:
                    own.setdefault(uid, set()).add(v); every.add(v)
    except sqlite3.Error as e:
        fc.close(); return [f'D54 读家族库失败：{e} !!']
    fc.close()
    # 键列：dict.role='key'
    keycol = set()
    try:
        fc = sqlite3.connect(f'file:{fam_db}?mode=ro', uri=True)
        # 键列：项目 scope + 家族 scope 都要读（id.subject_label 等结构列登记在 family scope）
        if scope:
            keycol = {r[0].lower() for r in fc.execute("select column_name from dict where role='key' and scope in (?, 'family')", (scope,))}
        else:
            keycol = {r[0].lower() for r in fc.execute("select column_name from dict where role='key'")}
        fc.close()
    except sqlite3.Error:
        pass
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    p0, coincidence, coin_cols = [], 0, {}
    for (t,) in conn.execute("select name from sqlite_master where type='table'"):
        cols = [(r[1], (r[2] or '').upper()) for r in conn.execute(f'pragma table_info("{t}")')]
        names = {c.lower() for c, _ in cols}
        if 'family_uid' not in names:
            continue
        for c, ty in cols:
            lc = c.lower()
            if lc in ('family_uid',) or lc in keycol:
                continue
            try:
                rows = conn.execute(f'select family_uid, "{c}" from "{t}" where "{c}" is not null').fetchall()
            except sqlite3.Error:
                continue
            n0 = 0; n2 = 0
            for uid, v in rows:
                if not isinstance(v, str):
                    continue
                v = v.strip()
                if len(v) < 4:
                    continue
                sv = own.get(uid)
                if sv and v in sv:
                    n0 += 1
                elif v in every:
                    n2 += 1
            if n0:
                p0.append((t, c, n0))
            if n2:
                coincidence += n2; coin_cols[c] = coin_cols.get(c, 0) + n2
    conn.close()
    out.append(f'D54 行级对齐：家族库号码值 {len(every)} 个、一人一名下集合 {len(own)} 人')
    if p0:
        for t, c, n in p0:
            out.append(f'   !! {t}.{c} 有 {n} 格等于**本行自己的**号码 → P0 泄漏')
        out.append('   !! D54 存在 P0 命中')
    else:
        out.append('   P0 行级泄漏 0 .')
    if coincidence:
        top = ', '.join(f'{c}={n}' for c, n in sorted(coin_cols.items(), key=lambda x: -x[1])[:5])
        out.append(f'   P2 巧合 {coincidence} 格（值是别人的号码，不报警）：{top}')
    return out


def d64_header_rows(path):
    """D64：凡**确实丢了表头行**的 sheet（有 header 列且为空 / header 含 #n / __nohdr__ /
    note 写「表头行丢失」「首行即」），meta.header_rows 必须有条目 `src_id=<id>#<sheet>`
    （值 **1 基**行号）；缺条目 → P1。src 无 header 列（老库）时不判，按需存在。"""
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    meta = {k: v for k, v in conn.execute('select k, v from meta')}
    try:
        data = json.loads(meta.get('header_rows') or '{}')
    except ValueError:
        conn.close()
        return ['D64 meta.header_rows 不是合法 JSON !!'], 1
    cols = [c[1] for c in conn.execute('pragma table_info(src)')]
    if 'header' not in cols:
        conn.close()
        return ['D64 src 无 header 列（旧库），按需跳过 .'], 0
    miss, n_lost = [], 0
    for sid, rel, note, hdr in conn.execute(
            "select src_id, rel_path, note, coalesce(header,'') from src"):
        rel, note, hdr = rel or '', note or '', hdr or ''
        sheet = rel.split('#', 1)[1] if '#' in rel else ''
        h = hdr.strip()
        lost = (h == '') or ('#n' in h) or ('__nohdr__' in h) or ('nohdr' in note.lower()) \
            or ('表头行丢失' in note) or ('首行即' in note)
        if not lost:
            continue
        n_lost += 1
        if f'src_id={sid}#{sheet}' not in data:
            miss.append(f'src_id={sid}#{sheet}')
    conn.close()
    if miss:
        return [f'D64 表头行丢失 {n_lost} 张、缺 header_rows {len(miss)} 条：'
                + '; '.join(miss[:5]) + (' …' if len(miss) > 5 else '') + ' !!'], len(miss)
    return [f'D64 表头行丢失 {n_lost} 张，header_rows {len(data)} 条 .'], 0


def d55_relative_time(path):
    """D55：相对时间列（postop_day/followup_month/relative_*）dict 必须写 unit 与 time_origin；
    若 meta.date_time_mode 声明 only-relative 而 clin 里有非 UNK 的 period_month → P0（自相矛盾）。"""
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    fam = sqlite3.connect(f'file:{FAM_DB}?mode=ro', uri=True)
    rows, bad = [], 0
    cols = [r[1] for r in conn.execute('pragma table_info(clin)')]
    rel = [c for c in cols if c in ('postop_day', 'followup_month') or c.startswith('relative_')]
    for c in rel:
        d = fam.execute("select unit, description from dict where scope=? and table_name='clin' and column_name=?",
                        (f'proj:{path.parent.name}', c)).fetchone()
        unit = (d[0] if d else None) or ''
        desc = (d[1] if d else '') or ''
        if not unit:
            rows.append(f'!! {c}: dict.unit 为空（相对时间必须写单位）')
            bad += 1
        if 'time_origin' not in desc:
            rows.append(f'!! {c}: dict.description 未写 time_origin（零点未声明）')
            bad += 1
        else:
            n = conn.execute(f'select count("{c}") from clin').fetchone()[0]
            rows.append(f'{c}: 非空 {n} 行，unit={unit or "（空）"}，time_origin 已登记')
    mode = conn.execute("select v from meta where k='date_time_mode'").fetchone()
    if mode and mode[0] == 'relative_only_no_anchor':
        n = conn.execute("select count(*) from clin where period_month<>'UNK'").fetchone()[0]
        if n:
            rows.append(f'!! 声明 only-relative 但有 {n} 行 period_month 非 UNK（相对量被当日期用）')
            bad += 1
    if not rel:
        rows.append('无相对时间列')
    fam.close()
    conn.close()
    return rows, bad


def d68_mtime_period(path):
    """D56：period_month 若等于 meta.ingest_batch 的批次日（YYYY-MM）→ P0（把文件 mtime 当时点）。"""
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    rows, bad = [], 0
    b = conn.execute("select v from meta where k='ingest_batch'").fetchone()
    if not b:
        rows.append('meta.ingest_batch 未记录（仅溯源用，可选）')
    else:
        months = set(re.findall(r'(\d{4}-\d{2})', b[0]))
        for m in sorted(months):
            n = conn.execute("select count(*) from clin where period_month=?", (m,)).fetchone()[0]
            if n:
                rows.append(f'!! period_month={m} 有 {n} 行，与 ingest_batch 同日 → 疑似用 mtime 造时点')
                bad += 1
        rows.append(f'ingest_batch={b[0]}；与 period_month 交集 {bad} 条')
    conn.close()
    return rows, bad

_EXP = {}


def d70(path, is_proj):
    """D58：库内表结构与 SCHEMA 的 DDL 逐列比对（缺表/缺列/类型不符 → P0）。"""
    import ddl_drift
    if not _EXP:
        _EXP.update(ddl_drift.expected_sets())
    exp = _EXP['project' if is_proj else 'family']
    probs = ddl_drift.check_db(str(path), exp)
    return [f'{k} {t}.{c} {d}'.strip() for k, t, c, d in probs]



DBROOT = pathlib.Path(r'D:\MyProjects\_DataBase')

_BLOB_PTR = re.compile(r'^blob:(?P<cont>[^@]+)@(?P<date>[^:]*):src_id=(?P<sid>\d+)#(?P<sheet>.*)$')


def _has_src(db_path):
    """旧容器判据：有 src 表（core-6t）。5t 家族库没有 src。"""
    try:
        with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as c:
            return bool(c.execute("select 1 from sqlite_master where type='table' and name='src'").fetchone())
    except sqlite3.Error:
        return False

def _fam_of(path):
    """家族名：stage/<fam>/... 或 _DataBase/<fam>/<slug>/<slug>db 两种根都认。"""
    s = str(path).replace('\\', '/')
    if '/stage/' in s:
        return s.split('/stage/')[1].split('/')[0]
    return pathlib.Path(path).parent.parent.name


def _old_container(fam):
    """切库后旧 core-6t 库叫 <fam>db_6t；未切库时叫 <fam>db。取带 src 表且 src 有 blob 列的那个。"""
    for name in (f'{fam}db_6t', f'{fam}db'):
        p = DBROOT / fam / '_db' / name
        if not p.exists():
            continue
        try:
            c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
            cols = [r[1] for r in c.execute('pragma table_info(src)')]
            c.close()
            if 'blob' in cols:
                return p
        except sqlite3.Error:
            continue
    return None


def d62_blob_ptr(path):
    """D62：blob 指针可解析。
    跨族两段式 `blob:<容器>@<日>:src_id=<n>#<sheet>`：容器存在 / src_id 可查 / sha 一致，任一不符 → P1。
    同族旧式 `blob:<fam>/<slug>/<file>#<sheet>`：
      - 原件存在但 sha 不符 → **P0**（原件可重抽、不等即有人在库里改过值）；
      - 原件不存在 → 解旧库 blob，与 `src.sha256` 比：一致记「快照」，并要求该行有
        `qc(SOURCE_SNAPSHOT_ONLY)`；不一致或旧库查不到 → P1。
    """
    out, bad, p0 = [], 0, 0
    if not path.exists():
        return out, bad, p0
    f = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    rows = f.execute("select src_id,rel_path,sha256 from src where rel_path like 'blob:%'").fetchall()
    qc_codes = {}
    try:
        for sid, det in f.execute("select src_id, detail from qc where code='SOURCE_SNAPSHOT_ONLY'"):
            pass
    except sqlite3.OperationalError:
        pass
    qc_rows = [r for r in f.execute("select scope,table_name,column_name,code,detail from qc")
               if r[3] == 'SOURCE_SNAPSHOT_ONLY']
    f.close()
    if not rows:
        return ['无 blob 指针'], 0, 0
    fam = _fam_of(path)
    caches, snap, snap_noqc = {}, 0, 0
    for sid, rel, s_sha in rows:
        m = _BLOB_PTR.match(rel or '')
        if not m:
            if (rel or '').startswith('blob:') and '#' in (rel or '') and '@' not in rel:
                raw = rel[5:].split('#', 1)[0]
                sheet = rel.split('#', 1)[1]
                fp = DBROOT / raw
                if fp.exists():
                    import hashlib
                    h = hashlib.sha256(fp.read_bytes()).hexdigest()
                    if s_sha and h != s_sha:
                        out.append(f'src {sid} 原件 sha 不符（P0）：{raw}')
                        p0 += 1
                    continue
                # 原件不在 → 旧库快照
                odb = _old_container(fam)
                if not odb or not odb.exists():
                    out.append(f'src {sid} 旧库不存在，无法验快照：{odb}'); bad += 1; continue
                if fam not in caches:
                    oc = sqlite3.connect(f'file:{odb}?mode=ro', uri=True)
                    import hashlib, zlib
                    got = []
                    for r in oc.execute('select src_id, sha256, blob from src'):
                        got.append((r[0], r[1], 0))
                        if r[2]:   # 也认「解压后 TSV 的 sha256」与「压缩 blob 字节的 sha256」
                            got.append((r[0], hashlib.sha256(r[2]).hexdigest(), 0))
                            try:
                                got.append((r[0], hashlib.sha256(zlib.decompress(r[2])).hexdigest(), 0))
                            except Exception:
                                pass
                    try:      # curated 工作簿登记在 doc 表，不在 src 表
                        got += [(0, r[0], 0) for r in oc.execute('select sha256 from doc')]
                    except sqlite3.OperationalError:
                        pass
                    oc.close()
                    caches[fam] = got
                cand = [r for r in caches[fam] if r[1] and r[1] == s_sha]
                if not cand:
                    # 容器整体丢失（被覆盖 / 未保留）：源快照已不可比对。
                    # 允许用 qc(SNAPSHOT_CONTAINER_LOST) 显式记账，代价是这条来源永久失去可复验性；
                    # 所以只报数、报告单列，不许拿它掩盖「容器在盘但 sha 不符」。
                    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as _c:
                        lost = {int(q[0]) for q in _c.execute(
                            "select column_name from qc where code='SNAPSHOT_CONTAINER_LOST'"
                            " and column_name glob '[0-9]*'")}
                    if sid in lost:
                        out.append(f'~  src {sid} 快照容器已丢失（qc 已记账，不可复验）：{raw}#{sheet}')
                        continue
                    out.append(f'src {sid} 快照 sha 在旧库查不到：{raw}#{sheet}'); bad += 1; continue
                snap += 1
                if not any(str(sid) in (q[4] or '') for q in qc_rows):
                    snap_noqc += 1
                continue
            out.append(f'src {sid} 指针不合法：{rel[:80]}'); bad += 1; continue
        cont, old = m.group('cont'), int(m.group('sid'))
        cfam = cont[:-2] if cont.endswith('db') else cont
        cpath = DBROOT / cfam / '_db' / cont
        # 切库后 `<fam>db` 已换成 5t 家族库（无 src 表），旧容器是 `<fam>db_6t`。
        # 两段式指针若指向同名旧容器，必须回落到 _6t，否则「容器在但查不到」会被误判成缺表崩溃。
        if cpath.exists() and not _has_src(cpath):
            alt = cpath.with_name(cont + '_6t')
            if alt.exists() and _has_src(alt):
                cpath = alt
        if not cpath.exists():
            out.append(f'src {sid} 容器缺失：{cpath}'); bad += 1; continue
        if cont not in caches:
            c = sqlite3.connect(f'file:{cpath}?mode=ro', uri=True)
            caches[cont] = [r for r in c.execute('select src_id, sha256 from src')]
            c.close()
        hit = [r for r in caches[cont] if r[0] == old]
        if not hit:
            out.append(f'src {sid} 容器 {cont} 无 src_id={old}'); bad += 1; continue
        if s_sha and hit[0][1] and s_sha != hit[0][1]:
            out.append(f'src {sid} sha 不一致：本库 {s_sha[:10]}… vs 容器 {hit[0][1][:10]}…'); bad += 1
    out.append(f'blob 指针 {len(rows)} 条：跨族 {len(rows) - snap - snap_noqc if False else len(rows) - snap}，'
               f'同族快照 {snap}（其中缺 SOURCE_SNAPSHOT_ONLY qc {snap_noqc}）'
               + (f'，不符 {bad}' if bad else '，一致') + (f'，P0 {p0}' if p0 else ' .'))
    if snap_noqc:
        bad += snap_noqc
        out.append(f'D62 快照行缺 qc(SOURCE_SNAPSHOT_ONLY)：{snap_noqc} 行 !!')
    return out, bad, p0


def _registered_id_collisions():
    """读各子库 qc 表里已登记的 ID_COLLISION（scope='id_number' 且 column_name=具体值）。

    豁免必须「逐值」登记：写一条泛泛的 qc 就放行全部共享，等于把门禁关掉。
    """
    reg = set()
    base = FAM_DB.parent
    if base.name == '_db':          # 家族库在 <fam>/_db/<fam>db，项目库在 <fam>/<slug>/ 下
        base = base.parent
    if not base.exists():
        return reg
    for d in base.iterdir():
        if not d.is_dir():
            continue
        for db in d.glob('*db'):
            try:
                c = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
                if not c.execute("select 1 from sqlite_master where name='qc'").fetchone():
                    c.close(); continue
                for (val,) in c.execute(
                        "select column_name from qc where code='ID_COLLISION'"
                        " and scope='id_number' and column_name is not null"
                        " and (status is null or status in ('open','fixed'))"):
                    reg.add(str(val))
                c.close()
            except sqlite3.Error:
                continue
    return reg


def d75_id_triple_unique():
    """D70：(kind,issuer,value) 在 0/8 段真身份上必须唯一；命中 9 段占位只报数。
    占位共享号码属正常（多个无名行共用同一门诊号/样本号），只写 qc 计数，不判 P0。"""
    out, bad, ph_groups, reg_hit, checked = [], 0, [], [], 0
    REG = _registered_id_collisions()
    with sqlite3.connect(f'file:{FAM_DB}?mode=ro', uri=True) as c:
        rows = c.execute("select kind, issuer, value, family_uid from id_number").fetchall()
    agg = {}
    for kind, iss, val, uid in rows:
        agg.setdefault((kind, iss, val), []).append(uid)
    def ph(u):
        return int(u.split('-')[1][0]) >= 9
    for (kind, iss, val), uids in agg.items():
        uniq = set(uids)
        if len(uniq) <= 1:
            continue
        checked += 1
        real = [u for u in uniq if not ph(u)]
        if real:
            if str(val) in REG:
                reg_hit.append((kind, iss, val, len(real)))
                continue
            bad += 1
            if len(out) < 8:
                out.append(f"  !! P0 D70 真身份号码被多个人共用 {kind}/{iss}/{val} -> {len(real)} 人")
        else:
            ph_groups.append((kind, iss, val, len(uniq)))
    out.append(f'  D75 (kind,issuer,value)：检查 {checked} 组，违规 {bad} 组（真身份）；占位共享 {len(ph_groups)} 组（只报数）')
    if reg_hit:
        out.append(f'    · 其中 {len(reg_hit)} 组已登记 qc(ID_COLLISION) 逐值豁免（不判 P0）：'
                   + ', '.join(f'{r[2]}→{r[3]}人' for r in reg_hit[:3]))
    if ph_groups:
        worst = max(ph_groups, key=lambda x: x[3])
        out.append(f'    · 占位共享最重 {worst[0]}/{worst[2]} -> {worst[3]} 个占位')
    return out, bad, len(ph_groups)


def d63_uid_segments():
    """D63：uid 段 ↔ (meta.subject_type, id.status) 三元映射，任一不符 P0。

    段 0 = human 真实（keyed_full|keyed_partial）；8 = animal 真实（keyed_animal）；
    9 = 占位（label_only|label_only_omics）。
    """
    out, bad, soft = [], 0, 0
    if not FAM_DB.exists():
        return out, bad
    f = sqlite3.connect(f'file:{FAM_DB}?mode=ro', uri=True)
    mk = dict(f.execute('select k,v from meta').fetchall())
    if mk.get('no_data') == '1':
        # 显式声明「该家族无数据」是第一条真值，不是缺席；不许用冒名 subject_type 顶
        f.close()
        return ['~  无数据家族（meta.no_data=1）：跳过身份类检查'], 0
    idst = {u: (st, nm) for u, st, nm in
            f.execute('select family_uid,id_status,name from id').fetchall()}
    f.close()
    try:
        stype = json.loads(mk.get('subject_types') or '{}')
    except Exception:
        stype = {}
    if not stype:
        # 缺席不等于通过：判不了要判成「判不了」，否则这个键一丢，D63 就静默变成绿灯
        # （LC 就这样带着「0 段 1,173 人 label_only」跑过了一整轮验收）。
        return ['!! P1 meta.subject_types 缺失，D63 无法判定（不等于通过）'], 1
    for slug, p in PROJ:
        if not p.exists():
            continue
        typ = stype.get(slug) or 'unknown'
        c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        uids = [r[0] for r in c.execute('select family_uid from person').fetchall()]
        c.close()
        for u in uids:
            seg = (u.split('-')[1][0] if '-' in u and len(u.split('-')[1]) >= 6 else '?')
            st, nm = idst.get(u, (None, None))
            if seg == '0' and typ == 'human' and st == 'label_only':
                # 只有姓名/源标签、没有任何号码的人：段位是判断题，不是结构题。
                # EC 的拉丁 name 串、EGVB 的 short code 都落在这里——判失败等于逼人
                # 去猜「这个标签算不算身份」，那正是 LC 拆行事故的起点。只报数。
                soft += 1
                continue
            if seg == '0':
                okk = typ == 'human' and st in ('keyed_full', 'keyed_partial')
            elif seg == '8':
                okk = (typ == 'animal' and st == 'keyed_animal')
            elif seg == '9':
                okk = st in ('label_only', 'label_only_omics')
            else:
                okk = False
            if not okk:
                out.append(f'{slug} uid={u} 段={seg} subject_type={typ} status={st}'); bad += 1
    out.append(f'三段映射检查：{'符合' if not bad else f'{bad} 条不符 !!'}'
               + (f'；0 段仅标签身份 {soft} 人（只报数：既无号码，也无姓名/拉丁名之外的标识）' if soft else ''))
    return out, bad



def d34_registry(fam, fam_db):
    """D68 双向登记门：库在账不在 / 账在库不在 → P0。"""
    from pathlib import Path as _P
    base = STAGE / fam
    if not base.exists():
        return []
    out, miss = [], []
    reg = {r[0]: r[1] for r in sqlite3.connect(f'file:{fam_db}?mode=ro', uri=True).execute(
        'select slug, db_rel_path from ref_project')}
    on_disk = {}
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name == '_db':
            continue
        f = d / (d.name + 'db')
        if f.exists():
            on_disk[d.name] = f
    for slug, f in on_disk.items():
        if slug not in reg:
            out.append(f'  !! P0 D68 {fam}/{slug}/{slug}db 存在但 ref_project 无此行（库在账不在） -> 1')
            miss.append(f'- {fam}/{slug}: 库存在、ref_project 缺行')
    for slug, rel in reg.items():
        if slug not in on_disk:
            out.append(f'  !! P0 D68 ref_project 有 {slug} 但 {fam}/{slug}/{slug}db 不存在（账在库不在） -> 1')
            miss.append(f'- {fam}/{slug}: ref_project 有行、库文件缺失')
    if not out:
        out.append(f'  D68 登记一致：磁盘 {len(on_disk)} 个库 / ref_project {len(reg)} 行 .')
    if miss:
        p = STAGE / 'ref_project_missing.md'
        old = p.read_text(encoding='utf-8') if p.exists() else '# ref_project 双向登记缺口\n'
        p.write_text(old.rstrip() + '\n' + '\n'.join(miss) + '\n', encoding='utf-8')
    return out


def d71_seg9_idnum():
    """D71（P2，只报数）：9 段人物持有患者级号码 → 「可提升」候选清单。

    9 段的定义是「没有患者级号码」；一旦 9 段人物在 id_number 里有行，两者必有一错：
    要么段位判错（有号就该进 0 段），要么那个值根本不是跨机构号码（项目内标签、批内序号、
    孔位编号——今天 HZ 的 `Ba##`、SSD 的 `编号` 各撞了一次）。

    这条不替代 D63（D63 查 status↔段位），它查的是「号码表里混进了非号码」。
    修法有两种，按证据选：真号码 → 把人挪进 0 段；项目内标签 → 撤到 clin 的 aux 列。
    """
    out, bad, checked = [], 0, 0
    with sqlite3.connect(f'file:{FAM_DB}?mode=ro', uri=True) as c:
        try:
            rows = c.execute(
                "select n.kind, n.issuer, count(*), count(distinct n.family_uid) "
                "from id_number n where substr(n.family_uid, instr(n.family_uid,'-')+1, 1) = '9' "
                # §8.1 本来就允许 9 段记录带 sample 号（样本级命名空间），所以只查患者级种类：
                # 一个占位人身上挂着住院号，等于「没有患者级键」这句话不成立。
                "and n.kind in ('inpatient','outpatient','mrn','pathology') "
                "group by 1,2 order by 3 desc").fetchall()
        except sqlite3.Error as e:      # 老库没有 id_number 之类
            return [f'  D71 跳过（{e}）'], 0
    checked = sum(r[2] for r in rows)
    if not rows:
        return [f'  D71 9 段人物持有患者级号码：检查 0 行，违规 0 行 .'], 0
    for kind, iss, n, nu in rows:
        bad += n
        if len(out) < 8:
            out.append(f'  P2 D71 9 段人物持有患者级号码：kind={kind} issuer={iss} {n} 行 / {nu} 人'
                       f'（§8.1 允许占位带号码；若该号确实唯一指向一人，可提升到 0 段做 keyed）')
    out.append(f'  D71 9 段人物持有患者级号码：检查 {checked} 行，违规 {bad} 行（只报数，不判失败——段位是判断题）')
    return out, 0



def _fam_db_of(proj_db):
    """项目库 -> 同家族 `_db/<fam>db`（gates 里两种根都认）。"""
    p = pathlib.Path(proj_db)
    fam = p.parent.parent.name
    return p.parent.parent / '_db' / (fam + 'db')


def d72_identity_grain():
    """D72（P1）：段位与「身份粒度」必须相容。

    段位不是「有没有名字」的问题，是「一个身份代表什么」的问题：
      SCC 一个身份 = 一个病理标本（标本行当然带病人姓名）→ 9 段正确；
      PCOS 一个身份 = 一个真人（有名无号）→ 必须在 0 段。
    所以由项目声明 `identity_grain`，门禁只查这条声明与库内事实是否相容：

      person        → 带姓名或有患者级号码的身份必须在 0/8 段；
                      9 段只许「既无姓名也无患者级号码」的占位行
      specimen/label/exam/sample
                    → 身份必须在 9 段；落在 0/8 段只提示（P2 只报数）
      mixed         → 需 grain_note，只报数
    """
    PAT = ('inpatient', 'outpatient', 'mrn', 'pathology')
    out, bad = [], 0
    for slug, p in PROJ:
        if not p.exists():
            continue
        try:
            with sqlite3.connect(f'file:{p}?mode=ro', uri=True) as c:
                mk = dict(c.execute('select k,v from meta').fetchall())
                uids = [r[0] for r in c.execute('select family_uid from person').fetchall()]
        except sqlite3.Error:
            continue
        grain = (mk.get('identity_grain') or '').strip()
        if grain == 'mixed':
            out.append(f'  D72 {slug} grain=mixed（{mk.get("grain_note", "未写 grain_note")}），只报数')
            continue
        if grain not in ('person', 'specimen', 'exam', 'sample', 'label'):
            bad += 1
            out.append(f'  !! P1 D72 {slug} 未声明 meta.identity_grain（person|specimen|exam|sample|label|mixed）')
            continue
        try:
            with sqlite3.connect(f'file:{_fam_db_of(p)}?mode=ro', uri=True) as f:
                named = {r[0] for r in f.execute("select family_uid from id where name is not null and trim(name)<>''")}
                nummed = {r[0] for r in f.execute(
                    "select distinct family_uid from id_number where kind in (%s)" % ','.join('?'*len(PAT)), PAT)}
        except sqlite3.Error:
            continue
        seg = lambda u: (u.split('-')[1][:1] if '-' in u and len(u.split('-')[1]) >= 6 else '?')
        ppl = [u for u in uids if u in named or u in nummed]
        if grain == 'person':
            wrong = [u for u in ppl if seg(u) in ('9', '?')]
            if wrong:
                bad += len(wrong)
                out.append(f'  !! P1 D72 {slug} grain=person 但 {len(wrong)} 个「有名或有号」的身份在 9 段'
                           f'（例 {wrong[0]}）→ 升 0 段')
            else:
                out.append(f'  D72 {slug} grain=person：检查 {len(ppl)} 个身份，违规 {len(wrong)} 个（0 段违规 0 / 9 段违规 {len(wrong)}）')
        else:
            stray = [u for u in ppl if seg(u) in ('0', '8')]
            if stray:
                out.append(f'  D72 {slug} grain={grain}，{len(stray)} 个有名身份在 0 段（只报数，例 {stray[0]}）')
            else:
                out.append(f'  D72 {slug} grain={grain}，段位相容 .')
    return out, bad


def d73_name_label(path):
    """D73（只报数）：`id.name` 不得是合成标签（Subject12 / sub003 / Case7 / P10 这类）。
    有人名≠有身份，把工具生成的标签写进姓名字段，报告里就会出现「患者 Subject1」这种假人。
    """
    out, n = [], 0
    if not path.exists():
        return out, 0
    try:
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as c:
            rows = c.execute("select family_uid, name from id where name is not null").fetchall()
    except sqlite3.Error:
        return out, 0
    pat = re.compile(r'(?i)^(subject|sub|case|sample|patient|pt|p|s|mice|rat|no)[\s_\-]*\d+$')
    bad = [r for r in rows if pat.match((r[1] or '').strip())]
    if bad:
        out.append(f'id.name 为合成标签 {len(bad)} 条（例 {bad[0][0]}:{bad[0][1]}）')
        n = len(bad)
    return out, n


def d74_meta_keys(path):
    """D74（只报数）：家族库 meta 必备键是否齐（schema_version / family / built_at）。

    缺键不脏数据，但会让「这个库按哪版 SCHEMA 建」变成不可知——D70 比的是当前试点
    SCHEMA，旧版建的库若又没自述版本，结构一致也只是巧合。
    """
    if not path.exists():
        return [], 0
    c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    t = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    if 'meta' not in t:
        c.close(); return [], 0
    mk = dict(c.execute('select k,v from meta').fetchall())
    c.close()
    if mk.get('no_data') == '1':
        return [], 0
    miss = [k for k in ('schema_version', 'family', 'built_at') if not mk.get(k)]
    if not miss:
        return [], 0
    return [f'meta 缺必备键：{", ".join(miss)}（补齐后重跑 fix_sha）'], len(miss)


def main():
    out = [f'# gate.log  built={datetime.datetime.now():%Y-%m-%d %H:%M:%S}  python={sys.version.split()[0]}']
    phis = phi_values()
    out.append(f'PHI 值集合（家族库 id 的 name/id_card/phone/address）= {len(phis)} 个')
    all_rows = []
    _pending = []
    d68 = d34_registry(FAM, FAM_DB)
    out.append('')
    out.append('== D68 双向登记门（库在账不在 / 账在库不在）')
    for ln in d68:
        out.append(ln)
    n68 = len([1 for x in d68 if '!!' in x])
    if n68:
        all_rows.append((f'family:{FAM}', 'ref_project', 'D34', n68))
    for tag, p in [(f'family:{FAM}', FAM_DB)] + [(f'proj:{s}', p) for s, p in PROJ]:
        if not p.exists():
            out.append(f'{tag}: 缺失 {p}')
            all_rows.append((tag, '-', 'MISSING', 'ERR'))
            continue
        c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        integ = c.execute('pragma integrity_check').fetchone()[0]
        fk = c.execute('pragma foreign_key_check').fetchall()
        tabs = [(t, c.execute(f'select count(*) from "{t}"').fetchone()[0])
                for (t,) in c.execute("select name from sqlite_master where type='table'").fetchall()]
        out.append(f'\n== {tag}  {p}')
        out.append(f'bytes={p.stat().st_size} sha256={sha(p)}')
        out.append(f'integrity_check={integ}  foreign_key_check={len(fk)} 违规')
        out.append('tables: ' + ', '.join(f'{t}={n}' for t, n in tabs))
        drift = d70(p, tag.startswith('proj:'))
        out.append('D70 结构漂移：' + ('0 项 .' if not drift else f'{len(drift)} 项 !!'))
        for ln in drift[:10]:
            out.append('  !! ' + ln)
        if drift:
            all_rows.append((tag, '-', 'D58', len(drift)))
        rows = check_gate(c, tag)
        bad = [r for r in rows if (isinstance(r[3], int) and r[3] > 0) or isinstance(r[3], str)]
        out.append(f'CHECK 复验：共 {len(rows)} 条，违反 {len(bad)} 条')
        for r in bad:
            out.append(f'  !! {r[1]} :: {r[2]} -> {r[3]}')
        all_rows += rows
        if tag.startswith('proj'):
            d62, b62, p0_62 = d62_blob_ptr(p)
            out.append('D62 跨族 blob 指针：')
            for ln in d62:
                out.append('  ' + ln)
            if b62:
                all_rows.append((tag, 'src', 'D62', b62))
            if p0_62:
                all_rows.append((tag, 'src', 'D62_P0', p0_62))
            sem = semantic_gates(p) + parent_gates(p)
            d48 = d48_d49_d50(p)
            out.append('D48/D49/D50：')
            for ln in d48:
                out.append('  ' + ln)
            d54 = d54_phi_valueset(p, FAM_DB, scope=f'proj:{tag.split(":",1)[1]}')
            out.append('D54：')
            for ln in d54:
                out.append('  ' + ln)
            if any(ln.rstrip().endswith('!!') for ln in d54):
                all_rows.append((tag, '-', 'D54', sum(1 for ln in d54 if ln.rstrip().endswith('!!'))))
            d53 = d53_placeholder_values(p)
            out.append('D53：')
            for ln in d53:
                out.append('  ' + ln)
            if any(ln.rstrip().endswith('!!') for ln in d53):
                all_rows.append((tag, '-', 'D53', sum(1 for ln in d53 if ln.rstrip().endswith('!!'))))
            d55, b55 = d55_relative_time(p)
            out.append('D55：')
            for ln in d55:
                out.append('  ' + ln)
            if b55:
                all_rows.append((tag, '-', 'D55', b55))
            d56, b56 = d68_mtime_period(p)
            out.append('D56：')
            for ln in d56:
                out.append('  ' + ln)
            if b56:
                all_rows.append((tag, '-', 'D56', b56))
            d64, b64 = d64_header_rows(p)
            out.append('D64：')
            for ln in d64:
                out.append('  ' + ln)
            if b64:
                all_rows.append((tag, 'src', 'D64', b64))
                d65 = d65_origin_unknown(path)
                for ln in d65:
                    print('    ' + ln)
                all_rows.append((tag, 'clin', 'D65', 0))
            d67 = d67_value_conflict(p)
            out.append('D67：')
            for ln in d67:
                out.append('  ' + ln)
            if any('[待修]' in ln for ln in d67):
                out.append('  ==> 计入「待修」（P2/换库前必修），不进家级违规')
                _pending.append((tag, 'meta', 'D67'))
            d5152 = d51_d52(p)
            out.append('D51/D52：')
            for ln in d5152:
                out.append('  ' + ln)
            if any(ln.rstrip().endswith('!!') for ln in d5152):
                all_rows.append((tag, '-', 'D51_52', sum(1 for ln in d5152 if ln.rstrip().endswith('!!'))))
            if any(ln.rstrip().endswith('!!') for ln in d48):
                all_rows.append((tag, '-', 'D48_50', sum(1 for ln in d48 if ln.rstrip().endswith('!!'))))
            out.append(f'语义门（§3 period_unit / §8.4 qc 落点 / §8.3 混编码）：{len(sem)} 条')
            for sev, msg, n in sem:
                out.append(f'  !! {sev} {msg} -> {n}')
            raw_hits = phi_scan(p, phis) + idnum_leak(p, FAM_DB)
            hits = [h for h in raw_hits if h[2] in ('COLUMN_NAME', 'VALUE', 'IDNUM_LEAK')]
            soft = [h for h in raw_hits if h[2] == 'IDNUM_COLLISION_KEYCOL']
            out.append(f'PHI 值级扫描：命中 {len(hits)}  -> {hits if hits else "无"}')
            if soft:
                out.append(f'  ~~ 键列与院号字面碰撞（非泄漏，已单列 qc ID_COLLISION/P2）：{soft}')
            slug = tag.split(':')[1]
            miss, extra = dict_cov(p, f'proj:{slug}', FAM_DB)
            out.append(f'dict 覆盖：clin 列 {len([r for r in c.execute("pragma table_info(clin)")])} 个，缺 dict {len(miss)} {miss}，多余 {len(extra)} {extra}')
            hard = [h for h in hits if h[2] != 'IDNUM_COLLISION_KEYCOL']
            if os.environ.get('GATE_DEBUG'):
                out.append(f'  DEBUG miss={miss} extra={extra} hard={hard} sem={sem}')
            if miss or extra or hard or sem:
                all_rows.append((tag, '-', 'SEM/PHI/DICT', len(miss) + len(extra) + len(hard) + len(sem)))
        c.close()
    d75, b75, n75 = d75_id_triple_unique()
    out.append('\n== D75 号码三元唯一性（占位共享只报数；检查/违规见下行）')
    out.extend(d75)
    if b75:
        all_rows.append((f'family:{FAM}', 'id_number', 'D75', b75))
    d71, b71 = d71_seg9_idnum()
    out.append('\n== D71 号码表纯度（9 段不得持号码；检查/违规见下行）')
    out.extend(d71)
    if b71:
        all_rows.append((f'family:{FAM}', 'id_number', 'D71', b71))
    d72, b72 = d72_identity_grain()
    out.append('\n== D72 身份粒度 ↔ 段位（检查/违规见下行）')
    out.extend(d72)
    if b72:
        all_rows.append((f'family:{FAM}', 'person', 'D72', b72))
    # D73 只报数（不进 all_rows）：合成标签进姓名会让报告写出「患者 Subject1」
    _l73, _n73 = d73_name_label(FAM_DB)
    try:
        with sqlite3.connect(f'file:{FAM_DB}?mode=ro', uri=True) as _c73:
            _n73_checked = _c73.execute(
                "select count(*) from id where coalesce(trim(name),'')<>''").fetchone()[0]
    except sqlite3.Error:
        _n73_checked = 0
    out.append(f'\n== D73 姓名是否合成标签：检查 {_n73_checked} 个 id.name，违规 0 个（命中 {_n73} 条，只报数）')
    out.extend('  ' + x for x in _l73)
    d63, b63 = d63_uid_segments()
    out.append(f'\n== D63 三段 uid 映射（0 human / 8 animal / 9 占位）：{b63} 条')
    for ln in d63:
        out.append('  ' + ln)
    if b63:
        all_rows.append((f'family:{FAM}', 'id', 'D63', b63))
    _l74, _n74 = d74_meta_keys(FAM_DB)
    out.append(f'\n== D74 meta 必备键：{_n74} 项缺失（只报数）')
    out.extend('  ' + x for x in _l74)
    mir = mirror_gates()
    out.append(f'\n== 家族库镜像门（ref_project.period_unit vs 子库 meta.period_unit）：{len(mir)} 条')
    for sev, msg, n in mir:
        out.append(f'  !! {sev} {msg} -> {n}')
    if mir:
        all_rows.append((f'family:{FAM}', 'ref_project', 'MIRROR', len(mir)))
    lon = longitudinal_gate()
    mr = multi_record_gate()
    out.append(f'\n== 多重记录门（multi_record_n 镜像 + 实时重算 / UNK 多条需 qc）：{len(mr)} 条')
    for sev, msg, n in mr:
        out.append(f'  !! {sev} {msg} -> {n}')
    if mr:
        all_rows.append((f'family:{FAM}', 'ref_project', 'MULTI_RECORD', len(mr)))
    out.append(f'\n== 纵向门（ref_project.longitudinal_n = 子库 meta = 实时重算）：{len(lon)} 条')
    for sev, msg, n in lon:
        out.append(f'  !! {sev} {msg} -> {n}')
    if lon:
        all_rows.append((f'family:{FAM}', 'ref_project', 'LONGITUDINAL', len(lon)))
    ok = all((isinstance(r[3], int) and r[3] == 0) for r in all_rows) and '!!' not in '\n'.join(out)
    _pt = f'（待修 {len(_pending)}）' if _pending else ''
    if _pending:
        out.append('\n待修（P2/换库前必修，不计家级违规）：' +
                   '; '.join(f'{a}:{b}.{c}' for a, b, c in _pending))
    out.append(f'\nCHECK 全库汇总：{"全部通过" if ok else "存在违反，见上"}{_pt}')
    if '--cutover' in sys.argv:
        # 换库前一次性判定：现算「违反 + 待修」，不落任何库存字段（能算出来的不落盘）
        v = [r for r in all_rows if not (isinstance(r[3], int) and r[3] == 0)]
        lines = [f'# cutover blockers — {FAM}', f'',
                 f'- 生成时间：{datetime.datetime.now().isoformat(timespec="seconds")}',
                 f'- 违反（P0/P1）：{len(v)}', f'- 待修（换库前必修）：{len(_pending)}', '']
        lines += ['## 违反'] + ([f'- {a} | {b}.{c}' for a, b, c in v] or ['- 无'])
        lines += ['', '## 待修'] + ([f'- {a} | {b}.{c}' for a, b, c in _pending] or ['- 无'])
        cb = Path(__file__).resolve().parent.parent.joinpath('cutover_blockers.md')
        # 按家族分段维护：只替换本家族段，其它家族段原样保留（旧实现用 split[0] 会把后面全丢）
        prev = cb.read_text(encoding='utf-8') if cb.exists() else ''
        secs = {}
        for chunk in re.split(r'(?m)^# cutover blockers — ', prev):
            chunk = chunk.strip()
            if not chunk:
                continue
            name, _, body = chunk.partition('\n')
            if name.strip():
                secs[name.strip()] = body.strip()
        secs[FAM] = '\n'.join(lines[1:]).strip()
        order = [f for f in ('MCI', 'LC', 'EC', 'SSD', 'EGVB', 'OTHERS', 'ACN', 'CID', 'MM', 'SCH', 'SCC', 'WML')
                 if f in secs] + sorted(f for f in secs if f not in
                                        ('MCI', 'LC', 'EC', 'SSD', 'EGVB', 'OTHERS', 'ACN', 'CID', 'MM', 'SCH', 'SCC', 'WML'))
        cb.write_text('\n\n'.join(f'# cutover blockers — {f}\n\n' + secs[f] for f in order) + '\n',
                      encoding='utf-8')
        out.append(f'CUTOVER: 违反 {len(v)} / 待修 {len(_pending)} → stage/cutover_blockers.md')
    # 自检：门禁必须真的产出检查段。2026-11-25 我把一个函数插进 main() 体内，
    # 整段检查被吞、打印为空、退出码仍 0——「空输出 = 通过」是这类工具最危险的失败模式。
    _secs = sum(1 for ln in '\n'.join(out).split('\n') if ln.startswith('== '))
    if _secs < 5:
        out.append(f'!! P0 GATE_SILENT 门禁只产出 {_secs} 个检查段（应 ≥5）：'
                   f'判据脚本可能被改坏，本次结果不可采信')
        all_rows.append((f'family:{FAM}', 'meta', 'GATE_SILENT', 1))
        ok = False
    if not ok and os.environ.get('GATE_DEBUG'):
        out.append('DEBUG all_rows=' + repr([r for r in all_rows if not (isinstance(r[3], int) and r[3] == 0)]))
    # 每个家族一段，追加而不是覆盖（MCI 的段不会因为跑 LC 而消失）
    head = f'\n##### family={FAM} #####\n'
    prev = LOG.read_text(encoding='utf-8') if LOG.exists() else ''
    if f'##### family={FAM} #####' in prev:
        keep = prev.split(f'\n##### family={FAM} #####\n')[0].rstrip() + '\n'
    else:
        keep = prev.rstrip() + '\n' if prev.strip() else ''
    LOG.write_text(keep + head + '\n'.join(out) + '\n', encoding='utf-8')
    print('\n'.join(out))


def d67_value_conflict(path):
    """D67：meta.value_conflict_state 表示「冲突未持久化」时，报告标红、换库前必修。"""
    c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    r = c.execute("select v from meta where k='value_conflict_state'").fetchone()
    c.close()
    if r and r[0] and 'not_persisted' in str(r[0]):
        return [f'D67 VALUE_CONFLICT 未持久化（meta.value_conflict_state={r[0]}）：'
                f'零冲突与「没记」同形，换库前必修 [待修]']
    return ['D67 VALUE_CONFLICT 状态已持久化 .']

if __name__ == '__main__':
    main()

