# -*- coding: utf-8 -*-
"""preview.py — 阶段 7：给刚建好的库出一张只读预览（单文件 HTML），放根目录给人先看。

用法：
  python -B tools/db-library/db5t/preview.py --root <stage> --family MCI --slug sw_CI \\
      --out <lab_root>\\sw_CI-库预览.html [--gates]

只读：两个库都以 mode=ro 打开，不做任何写入。`--gates` 会就地把 gates.py 跑一遍，抓摘要行贴进页面。

页面内容：
- KPI：person / clin 行、clin 列数、id / id_number 行、identity_grain、period_unit、建库日期
- 来源登记 `src`（kind / rel_path / sha256 / rows / note）
- 两个库的表清单与行数
- clin 每列：物理类型、dict role / dtype / unit、NULL 行数、样例值
- clin 抽样（默认前 14 行）
- 门禁摘要（`==` / `!!` / `CHECK` 行）
- `ref_project` 登记行
"""
from __future__ import annotations

import argparse
import html
import os
import pathlib
import sqlite3
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402


def tables(conn) -> list[tuple[str, int]]:
    out = []
    for (name,) in conn.execute("select name from sqlite_master where type='table' order by name"):
        if name == 'sqlite_sequence':
            continue
        out.append((name, conn.execute(f'select count(*) from "{name}"').fetchone()[0]))
    return out


def esc(x) -> str:
    return html.escape('' if x is None else str(x))


def fmt(v) -> str:
    if v is None:
        return '<span class="null">NULL</span>'
    if isinstance(v, float):
        return esc(f'{v:.4g}')
    return esc(v)


def gather(root: pathlib.Path, family: str, slug: str, sample_n: int = 14) -> dict:
    fam_db = root / family / '_PHI' / f'{family}db'
    proj_db = root / family / slug / f'{slug}db'
    for p in (fam_db, proj_db):
        if not p.exists():
            raise SystemExit(f'{p} 不存在')

    f = sqlite3.connect(f'file:{fam_db}?mode=ro', uri=True)
    p = sqlite3.connect(f'file:{proj_db}?mode=ro', uri=True)
    try:
        d = {'fam_db': fam_db, 'proj_db': proj_db,
             'fam_meta': dict(f.execute('select k, v from meta')),
             'proj_meta': dict(p.execute('select k, v from meta')),
             'src': p.execute('select src_id, kind, rel_path, sha256, rows, note from src').fetchall(),
             'fam_tables': tables(f), 'proj_tables': tables(p)}
        d['clin_cols'] = [(r[1], r[2], r[3]) for r in p.execute('pragma table_info("clin")')]
        d['person_n'] = p.execute('select count(*) from person').fetchone()[0]
        d['clin_n'] = p.execute('select count(*) from clin').fetchone()[0]
        d['id_n'] = f.execute('select count(*) from id').fetchone()[0]
        d['num_n'] = f.execute('select count(*) from id_number').fetchone()[0]
        d['refp'] = f.execute(
            'select slug, person_count, clin_person_n, period_unit, built_at, sha256 '
            'from ref_project where slug=?', (slug,)).fetchone()
        # dict 行（只读全局 dict.db）
        d['dict'] = {}
        dd = paths.DICT
        if pathlib.Path(dd).exists():
            dc = sqlite3.connect(f'file:{dd}?mode=ro', uri=True)
            for t, c, role, dt, unit in dc.execute(
                    'select table_name, column_name, role, dtype, unit from dict '
                    'where scope in (?, "family")', (f'proj:{slug}',)):
                d['dict'][(t, c)] = (role, dt, unit)
            dc.close()
        d['sample_cols'] = [c[0] for c in p.execute('select * from clin limit 1').description]
        d['sample'] = p.execute(f'select * from clin limit {sample_n}').fetchall()
        d['nulls'] = {name: p.execute(f'select count(*) from clin where "{name}" is null')
                      .fetchone()[0] for name, _, _ in d['clin_cols']}
        d['ex'] = {name: [r[0] for r in p.execute(
            f'select "{name}" from clin where "{name}" is not null limit 3').fetchall()]
            for name, _, _ in d['clin_cols']}
        return d
    finally:
        f.close()
        p.close()


def gates_lines(root: pathlib.Path, family: str) -> tuple[list[str], str]:
    script = HERE / 'gates.py'
    if not script.exists():
        return ['（没有 gates.py）'], '（未跑）'
    env = dict(os.environ, DB5T_ROOT=str(root))
    r = subprocess.run([sys.executable, '-B', str(script), family],
                       capture_output=True, text=True, encoding='utf-8', errors='replace',
                       env=env, cwd=str(HERE))
    out = r.stdout or ''
    keep = []
    for ln in out.splitlines():
        s = ln.strip()
        if s.startswith(('== ', '!!', 'CHECK', '违规账本行', '· 待修')):
            keep.append(ln.rstrip())
    status = '通过' if 'CHECK 全库汇总：全部通过' in out else '存在违反'
    return keep, status


def render(d: dict, family: str, slug: str, root: pathlib.Path,
           gate_lines: list[str], gate_status: str) -> str:
    def table_rows(pairs):
        return '\n'.join(f'<tr><td><code>{esc(n)}</code></td><td class="num">{v:,}</td></tr>'
                         for n, v in pairs)

    def col_rows():
        out = []
        for name, typ, _ in d['clin_cols']:
            role, dt, unit = d['dict'].get(('clin', name), ('—', '—', None))
            ex = '、'.join(esc(v) for v in d['ex'][name]) or '—'
            out.append(f'<tr><td><code>{esc(name)}</code></td><td>{esc(typ)}</td>'
                       f'<td>{esc(role)}</td><td>{esc(dt)}</td><td>{esc(unit or "")}</td>'
                       f'<td class="num">{d["nulls"][name]}</td><td class="ex">{ex}</td></tr>')
        return '\n'.join(out)

    head = '\n'.join(f'<th>{esc(c)}</th>' for c in d['sample_cols'])
    body = '\n'.join('<tr>' + ''.join(f'<td>{fmt(v)}</td>' for v in row) + '</tr>'
                     for row in d['sample'])
    src_rows = '\n'.join(
        f'<tr><td class="num">{i}</td><td>{esc(k)}</td><td><code>{esc(rel)}</code></td>'
        f'<td><code class="sha">{esc((sha or "")[:16])}…</code></td><td class="num">{n:,}</td>'
        f'<td class="note">{esc(note)}</td></tr>' for i, k, rel, sha, n, note in d['src'])
    gl = '\n'.join(f'<div class="gl{" bad" if "!!" in ln else ""}">{esc(ln)}</div>'
                   for ln in gate_lines) or '<div class="gl">（没有抓到门禁输出）</div>'
    refp = d['refp']
    if refp:
        ref_row = (f'<tr><td>{esc(refp[0])}</td><td class="num">{refp[1]:,}</td>'
                   f'<td class="num">{refp[2]:,}</td><td>{esc(refp[3])}</td>'
                   f'<td>{esc(refp[4])}</td>'
                   f'<td><code class="sha">{esc((refp[5] or "")[:16])}…</code></td></tr>')
    else:
        ref_row = ('<tr><td colspan="6" class="bad">ref_project 未登记（跑 mkid.py 后才有一行）'
                   '</td></tr>')


    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{esc(family)} / {esc(slug)} 建库预览</title>
<style>
:root{{--bg:#f6f4ef;--paper:#fffdf8;--ink:#1d2521;--muted:#63706a;--line:#ddd6c9;
--accent:#0f4c5c;--ok:#2f6f4e;--bad:#9b2c2c;--chip:#e8efe9;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 "Segoe UI","Source Han Sans SC","Noto Sans SC",system-ui,sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:28px 22px 60px}}
h1{{font-size:26px;margin:0 0 4px;letter-spacing:.3px}}
h2{{font-size:17px;margin:30px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}}
.sub{{color:var(--muted);margin:0 0 18px}}
.card{{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:16px 18px}}
.kpi{{display:flex;flex-wrap:wrap;gap:14px;margin:0 0 6px}}
.kpi div{{background:var(--paper);border:1px solid var(--line);border-radius:10px;
padding:12px 16px;min-width:132px}}
.kpi b{{display:block;font-size:22px;line-height:1.2}}
.kpi span{{color:var(--muted);font-size:12.5px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
table{{width:100%;border-collapse:collapse;background:var(--paper);font-size:13.5px}}
th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}
th{{background:var(--chip);font-weight:600}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
td.ex,td.note{{color:var(--muted);max-width:320px;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}}
code{{font-family:"Cascadia Mono",Consolas,monospace;font-size:12.5px}}
.null{{color:#b9b2a4;font-style:italic}}
.tag{{display:inline-block;border-radius:999px;padding:1px 9px;font-size:12.5px;
background:var(--chip);color:var(--accent);margin-right:6px}}
.gl{{font-family:Consolas,monospace;font-size:12.5px;white-space:pre-wrap;padding:1px 0}}
.gl.bad{{color:var(--bad)}}
.sha{{color:var(--muted)}}
.foot{{color:var(--muted);font-size:12.5px;margin-top:26px}}
@media (max-width:860px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<h1>{esc(family)} / {esc(slug)} <span class="tag">建库预览</span>
<span class="tag">{esc(gate_status)}</span></h1>
<p class="sub">源：{esc(d['src'][0][2] if d['src'] else '—')} · 库：
<code>{esc(str(d['proj_db']))}</code></p>

<div class="kpi">
  <div><b>{d['person_n']:,}</b><span>person 行（身份数）</span></div>
  <div><b>{d['clin_n']:,}</b><span>clin 行</span></div>
  <div><b>{len(d['clin_cols'])}</b><span>clin 列</span></div>
  <div><b>{d['id_n']:,}</b><span>id 行</span></div>
  <div><b>{d['num_n']:,}</b><span>id_number 行</span></div>
  <div><b>{esc(d['proj_meta'].get('identity_grain', '—'))}</b><span>identity_grain</span></div>
  <div><b>{esc(d['proj_meta'].get('period_unit', 'singleton'))}</b><span>period_unit</span></div>
  <div><b>{esc((d['fam_meta'].get('built_at') or '')[:10])}</b><span>建库日期</span></div>
</div>

<h2>来源登记（src）</h2>
<table><tr><th>#</th><th>kind</th><th>rel_path</th><th>sha256</th><th>rows</th><th>note</th></tr>
{src_rows}</table>

<h2>表清单</h2>
<div class="grid"><div><h3 style="margin:0 0 6px;font-size:14px">家族库 {esc(family)}db</h3>
<table><tr><th>表</th><th>行数</th></tr>{table_rows(d['fam_tables'])}</table></div>
<div><h3 style="margin:0 0 6px;font-size:14px">子库 {esc(slug)}db</h3>
<table><tr><th>表</th><th>行数</th></tr>{table_rows(d['proj_tables'])}</table></div></div>

<h2>clin 列与 dict（{len(d['clin_cols'])} 列）</h2>
<table><tr><th>列</th><th>类型</th><th>dict role</th><th>dtype</th><th>unit</th>
<th>NULL 行</th><th>样例值</th></tr>{col_rows()}</table>

<h2>clin 抽样（前 {len(d['sample'])} 行）</h2>
<div style="overflow-x:auto"><table><tr>{head}</tr>{body}</table></div>

<h2>门禁（gates.py {esc(family)}）</h2>
<div class="card">{gl}</div>

<h2>ref_project</h2>
<table><tr><th>slug</th><th>person_count</th><th>clin_person_n</th><th>period_unit</th>
<th>built_at</th><th>sha256</th></tr>
{ref_row}</table>

<p class="foot">只读预览，由 preview.py 生成；库未做任何修改。root：<code>{esc(str(root))}</code></p>
</div></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='阶段 7：建库预览（只读 HTML）')
    ap.add_argument('--root', type=pathlib.Path, default=paths.STAGE)
    ap.add_argument('--family', required=True)
    ap.add_argument('--slug', required=True)
    ap.add_argument('--out', type=pathlib.Path, default=None,
                    help='缺省落在子工程夹内：<root>/<family>/<slug>/<slug>-库预览.html')
    ap.add_argument('--gates', action='store_true', help='就地跑一遍 gates.py 抓摘要')
    ap.add_argument('--sample', type=int, default=14, help='clin 抽样行数')
    a = ap.parse_args(argv)

    out = a.out or (a.root / a.family / a.slug / f'{a.slug}-库预览.html')
    d = gather(a.root, a.family, a.slug, a.sample)
    if a.gates:
        lines, status = gates_lines(a.root, a.family)
    else:
        lines, status = ['（未跑：加 --gates）'], '（未跑）'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(d, a.family, a.slug, a.root, lines, status), encoding='utf-8')
    print(f'OK  {out}  ({out.stat().st_size:,} B)  门禁={status}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
