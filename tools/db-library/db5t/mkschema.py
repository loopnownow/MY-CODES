# -*- coding: utf-8 -*-
"""mkschema.py — schema-v1.md → schema.sql（契 1 的第三份契约：机器可读建库 DDL）。

用法：
  python -B tools/db-library/db5t/mkschema.py                    # 写本目录 schema.sql
  python -B tools/db-library/db5t/mkschema.py --out X.sql
  python -B tools/db-library/db5t/mkschema.py --check            # 过期就 rc=1，不写盘

规则（出自 db-build-contract.md 契 1 / 契 3）：
- 只认 ```sql 围栏，按出现顺序：第 1 段 = 家族库 DDL，第 2 段 = 子工程库 DDL。
- 子工程库里的 `<mod>` 按 7 个模态展开：radiomics | us | mri | ct | pet | path | report。
- 家族库的 `CREATE TABLE dict(...)` 整段剔除：契 3 目标态家族库不留 dict，词典读
  {RAW}\\Family\\dict.db（旧库里的 dict 表由 mkdict.py 归并成全局库）。
- 生成文件首行记 source_sha256；--check 比的就是这个值，schema-v1.md 一改就过期。
- 两段之间插 `-- @section family` / `-- @section project` 标记，newdb.py 据此切分。

自检：跑一次 --check，输出 `OK` 才算 schema.sql 与 schema-v1.md 同步。
"""
import argparse
import hashlib
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
MODS = ('radiomics', 'us', 'mri', 'ct', 'pet', 'path', 'report')
MARK_FAM = '-- @section family'
MARK_PROJ = '-- @section project'


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def fence_blocks(text: str):
    """取所有 ```sql 围栏正文（按出现顺序）。"""
    return re.findall(r'^```sql\n(.*?)^```', text, re.S | re.M)


def strip_dict(block: str) -> str:
    """家族库段落剔除 CREATE TABLE dict(...) 整段。"""
    start = block.find('CREATE TABLE dict(')
    if start < 0:
        return block
    end = block.find('\n);\n', start)
    if end < 0:
        raise SystemExit('family DDL: CREATE TABLE dict 找不到结尾 ");"')
    note = '-- （dict 表已剔除：契 3 目标态家族库不留 dict，词典读 Family\\dict.db）\n'
    return block[:start] + note + block[end + len('\n);\n'):]


def expand_mods(block: str) -> str:
    """只把模态段（`_modules_<mod>` … `<mod>_val` 到下一个公共表前）复制 7 份。

    子工程库 DDL 的结构是：公共头（meta/src/person/clin）→ 模态段 → 公共尾（qc）。
    整段复制会把 meta/src/clin 也建 7 遍，所以按 `CREATE TABLE _modules_<mod>` 与
    `CREATE TABLE qc(` 两处切出模态段。
    """
    head_mark, mid_mark = 'CREATE TABLE _modules_<mod>', 'CREATE TABLE qc('
    i = block.find(head_mark)
    j = block.find(mid_mark, i + 1)
    if i < 0 or j < 0:
        raise SystemExit('project DDL: 找不到 _modules_<mod> / qc 切分点')
    head, seg, tail = block[:i], block[i:j], block[j:]
    out = []
    for mod in MODS:
        body = []
        for line in seg.splitlines():
            if line.lstrip().startswith('--') and '<mod>' in line:
                continue  # 说明性注释含占位符，展开会重复 7 遍
            body.append(line.replace('<mod>', mod))
        out.append(f'-- ---------- mod: {mod} ----------')
        out.extend(body)
        out.append('')
    return head + '\n'.join(out) + tail


def render(md_path: pathlib.Path) -> str:
    text = md_path.read_text(encoding='utf-8')
    blocks = fence_blocks(text)
    if len(blocks) < 2:
        raise SystemExit(f'{md_path} 只找到 {len(blocks)} 段 ```sql，需要 2 段（家族库 / 子工程库）')
    fam, proj = strip_dict(blocks[0]), expand_mods(blocks[1])
    head = (
        '-- schema.sql — 由 schema-v1.md 反编译生成，不要手改。\n'
        '-- 生成器：tools/db-library/db5t/mkschema.py\n'
        f'-- source: {md_path.name}\n'
        f'-- source_sha256: {sha256(md_path)}\n'
        '-- 用法：newdb.py 按 @section 标记切分，家族库段落已剔除 dict 表（契 3）。\n'
    )
    return head + MARK_FAM + '\n' + fam.rstrip() + '\n\n' + MARK_PROJ + '\n' + proj.rstrip() + '\n'


def recorded_sha(path: pathlib.Path):
    if not path.exists():
        return None
    for line in path.read_text(encoding='utf-8').splitlines()[:8]:
        if line.startswith('-- source_sha256:'):
            return line.split(':', 1)[1].strip()
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='schema-v1.md → schema.sql')
    sys.path.insert(0, str(HERE))
    import paths  # noqa: E402  （同目录，读 DB5T_SCHEMA 默认值）

    default_md = HERE / 'schema-v1.md'
    ap.add_argument('--md', type=pathlib.Path, default=default_md if default_md.exists() else paths.SCHEMA)
    ap.add_argument('--out', type=pathlib.Path, default=HERE / 'schema.sql')
    ap.add_argument('--check', action='store_true', help='只比对 sha256，不写盘')
    a = ap.parse_args(argv)

    if not a.md.exists():
        print(f'MISSING schema: {a.md}')
        return 2
    want = sha256(a.md)
    have = recorded_sha(a.out)
    if a.check:
        if have == want:
            print(f'OK   {a.out}  (source_sha256={want[:12]}…)')
            return 0
        print(f'STALE {a.out}\n  source={a.md} sha256={want[:12]}…\n  built_on sha256={(have or "-")[:12]}…')
        return 1

    sql = render(a.md)
    a.out.write_text(sql, encoding='utf-8')
    fam_n = sql.count('CREATE TABLE')
    print(f'WROTE {a.out}  {len(sql.encode("utf-8")):,} B  CREATE TABLE×{fam_n}  mods={len(MODS)}')
    print(f'      source_sha256={want[:12]}…')
    return 0


if __name__ == '__main__':
    sys.exit(main())
