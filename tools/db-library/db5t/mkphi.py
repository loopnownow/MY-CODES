#!/usr/bin/env python
"""mkphi.py — 把 PHI（姓名、号码、身份证等）灌进家族库 `_PHI/<fam>db` 的 id / id_number。

平台无关：库路径由 `--root`（stage 根）或 `DB5T_ROOT` 给，本文件不写任何盘符。

为什么单独一个工具：
  * `fill.py` 只写子库，子库是「无 PHI」的——clin 里连姓名列都不许有；
  * `mkid.py` 只发 family_uid（并搬旧库的 id/id_number），不读源里的姓名与号码；
  * 所以「人的姓名 / 身份证 / 电话 / 门诊号」这条线必须单独一步，落在家族库。

输入（CSV 或 xlsx，表头行可指定）至少一列 `family_uid`，其余按列名认：

    family_uid  name  name_pinyin  sex  birth_date  id_card  phone  address
    inpatient_no  outpatient_no  mrn  imaging_no  pathology_no  sample_no  misc_id

不认识的列一律忽略（并且在 dry-run 的输出里点名，免得悄悄丢数据）。
同一个 uid 在源里出现多行 → 只取第一行非空值，冲突值进报告。

脱敏（契 3 与 D73：家族库里存的是脱敏形态；明文不许落库）：
  id_card   前 3 + `*`×(len-7) + 后 4
  phone     前 3 + `****` + 后 4
  address   只到区/县（启发式截断，见 `mask_address`；截不准就人工改，别把门禁绕过去）

退出码：0 正常；1 有行缺 family_uid / uid 不在 id 表 / 脱敏后撞唯一索引；2 参数或输入错。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import pathlib
import sqlite3
import sys


# 源列名 → id 表的列（值要脱敏的单独处理）
ID_COLS = ('name', 'name_pinyin', 'sex', 'birth_date', 'address',
           'inpatient_no', 'outpatient_no', 'mrn', 'imaging_no', 'pathology_no',
           'sample_no', 'misc_id')
NUMBER_COLS = {  # id 表列 → id_number.kind
    'inpatient_no': 'inpatient', 'outpatient_no': 'outpatient', 'mrn': 'mrn',
    'imaging_no': 'imaging', 'pathology_no': 'pathology', 'sample_no': 'sample',
}
MASKED = ('id_card', 'phone')


def mask_id_card(v: str) -> str | None:
    v = (v or '').strip()
    if not v:
        return None
    if len(v) <= 7:
        return '*' * len(v)
    return v[:3] + '*' * (len(v) - 7) + v[-4:]


def mask_phone(v: str) -> str | None:
    v = (v or '').strip()
    if not v:
        return None
    if len(v) <= 7:
        return '*' * len(v)
    return v[:3] + '****' + v[-4:]


def mask_address(v: str, keep_tokens: int = 3, cap: int = 20) -> str | None:
    """地址截到第 keep_tokens 级行政区（省/市/区/县/旗）。

    中文地址没有分隔符，只能按行政级别字猜：`山东省济宁市任城区XX路1号` → `山东省济宁市任城区`。
    猜不出来的（英文地址、只有小区名）保留前 cap 个字符并原样交给人工核。
    """
    v = (v or '').strip()
    if not v:
        return None
    # 行政级别标记，按“最长优先”匹配：省/市/区/县/旗，再退一级用 街道/乡/镇/路
    marks = ('街道', '自治区', '省', '市', '区', '县', '旗', '乡', '镇', '路')
    n = 0
    i = 0
    while i < len(v) and i < cap:
        for mk in marks:
            if v.startswith(mk, i):
                n += 1
                i += len(mk) - 1
                if n >= keep_tokens:
                    return v[:i + 1]
                break
        i += 1
    return v[:cap]


def load_rows(path: pathlib.Path, sheet: str | None, header_row: int) -> tuple[list[str], list[dict]]:
    if path.suffix.lower() in ('.csv', '.tsv'):
        delim = '\t' if path.suffix.lower() == '.tsv' else ','
        with path.open(encoding='utf-8-sig', newline='') as fh:
            it = csv.reader(fh, delimiter=delim)
            for _ in range(max(header_row - 1, 0)):
                next(it, None)
            header = [h.strip() for h in next(it)]
            return header, [dict(zip(header, r)) for r in it]
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
    it = ws.iter_rows(values_only=True)
    for _ in range(max(header_row - 1, 0)):
        next(it, None)
    header = [str(h).strip() if h is not None else '' for h in next(it)]
    rows = [dict(zip(header, r)) for r in it]
    wb.close()
    return header, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='PHI（姓名/号码）灌入家族库 id / id_number')
    ap.add_argument('--root', default=os.environ.get('DB5T_ROOT'),
                    help='stage 根（默认取 DB5T_ROOT）')
    ap.add_argument('--family', required=True)
    ap.add_argument('--slug', required=True, help='只用于校验子库存在，PHI 本身落家族库')
    ap.add_argument('--phi', required=True, help='CSV/TSV/xlsx')
    ap.add_argument('--sheet', default=None)
    ap.add_argument('--header-row', type=int, default=1)
    ap.add_argument('--no-mask', action='store_true', help='写入明文（只在已确认的离线副本上用）')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    if not a.root:
        print('需要 --root 或环境变量 DB5T_ROOT', file=sys.stderr)
        return 2
    root = pathlib.Path(a.root)
    fam_db = root / a.family / '_PHI' / f'{a.family}db'
    proj_db = root / a.family / a.slug / f'{a.slug}db'
    for p in (fam_db, proj_db):
        if not p.exists():
            print(f'{p} 不存在', file=sys.stderr)
            return 2
    src = pathlib.Path(a.phi)
    if not src.exists():
        print(f'{src} 不存在', file=sys.stderr)
        return 2

    header, rows = load_rows(src, a.sheet, a.header_row)
    known = {'family_uid', 'id_card', 'phone'} | set(ID_COLS)
    ignored = [h for h in header if h and h not in known]
    if 'family_uid' not in header:
        print('PHI 源缺 family_uid 列', file=sys.stderr)
        return 2
    print(f'src    = {src}  ({len(rows)} 行)')
    print(f'fam    = {fam_db}')
    if ignored:
        print(f'忽略列  = {", ".join(ignored)}')

    conn = sqlite3.connect(fam_db)
    conn.execute('PRAGMA foreign_keys=ON')
    ids = {r[0] for r in conn.execute('SELECT family_uid FROM id')}
    seen: dict[str, dict] = {}
    missing_uid = 0
    for r in rows:
        uid = str(r.get('family_uid') or '').strip()
        if not uid:
            missing_uid += 1
            continue
        seen.setdefault(uid, r)

    unknown = sorted(u for u in seen if u not in ids)
    for u in unknown:
        print(f'WARN  uid 不在 id 表：{u}')

    n_id = n_num = 0
    conflicts: list[str] = []
    for uid, r in seen.items():
        if uid not in ids:
            continue
        sets, vals = [], []
        for col in ID_COLS:
            raw = r.get(col)
            val = mask_address(raw) if col == 'address' and not a.no_mask else (
                (str(raw).strip() or None) if raw is not None else None)
            if val is None:
                continue
            sets.append(f'{col}=?')
            vals.append(val)
        if sets:
            conn.execute(f'UPDATE id SET {", ".join(sets)}, updated_on=date("now") '
                         f'WHERE family_uid=?', (*vals, uid))
            n_id += 1
        # 脱敏后的号码仍可能撞唯一索引（前 3 + 后 4 相同而中间不同）：
        # 撞了就只丢该字段、把冲突记下来，绝不能因此并掉两个人。
        for col in MASKED:
            raw = r.get(col)
            if raw is None or str(raw).strip() == '':
                continue
            val = str(raw).strip() if a.no_mask else (mask_id_card(str(raw)) if col == 'id_card'
                                                      else mask_phone(str(raw)))
            try:
                conn.execute(f'UPDATE id SET {col}=?, updated_on=date("now") '
                             f'WHERE family_uid=?', (val, uid))
            except sqlite3.IntegrityError:
                conflicts.append(f'{uid}/{col}={val} 撞唯一索引（脱敏后同形，未写入）')
        for col, kind in NUMBER_COLS.items():
            raw = r.get(col)
            val = str(raw).strip() if raw is not None else ''
            if not val or val in ('0', 'NA', 'nan'):
                continue
            try:
                conn.execute('INSERT OR IGNORE INTO id_number(family_uid, kind, value, issuer, note) '
                             'VALUES (?,?,?,?,?)', (uid, kind, val, a.family, f'mkphi:{src.name}'))
                n_num += 1
            except sqlite3.IntegrityError as e:
                conflicts.append(f'{uid}/{kind}: {e}')

    meta = {'phi_source': src.name, 'phi_sha256': hashlib.sha256(src.read_bytes()).hexdigest(),
            'phi_imported_at': __import__('datetime').datetime.now().isoformat(timespec='seconds')}
    if not a.dry_run:
        for k, v in meta.items():
            conn.execute('INSERT OR REPLACE INTO meta(k, v) VALUES (?,?)', (k, v))
        conn.commit()
    print(f'id    更新 {n_id} 行；id_number +{n_num}；缺 family_uid {missing_uid}；'
          f'uid 不在册 {len(unknown)}；唯一索引冲突 {len(conflicts)}')
    for c in conflicts[:10]:
        print(f'  CONFLICT {c}')
    if a.no_mask:
        print('WARN  用了 --no-mask，库里是明文；只有在确认过的离线副本上才允许')
    conn.close()
    if a.dry_run:
        print('dry-run：未写库')
    return 1 if (missing_uid or unknown) else 0


if __name__ == '__main__':
    raise SystemExit(main())
