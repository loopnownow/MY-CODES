# -*- coding: utf-8 -*-
"""db_core 写路径自测：core-6t 直写 + legacy 7 表回归（全部落 %TEMP%，不碰真库/真叶）。

用法：python -B tools/db-library/test_core6t.py   # 退出码 0 = 全过或 SKIP
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db_core as core  # noqa: E402

TABLES = ("src", "item", "val", "person", "person_key", "doc", "sheet", "fact", "entity")


def make_xlsx(path, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "T1"
    for r in rows:
        ws.append(r)
    wb.save(path)


def counts(con):
    out = {}
    for t in TABLES:
        try:
            out[t] = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            out[t] = None
    return out


def find_6t_db():
    """在 {db-root} 找第一个 core-6t 家族库（找不到返回 None）。"""
    root = core.DEFAULT_DB_ROOT
    if not root.is_dir():
        return None
    for fam in sorted(p for p in root.iterdir() if p.is_dir()):
        db = fam / "_db" / f"{fam.name}db"
        if not db.is_file():
            continue
        try:
            con = core.open_db(db)
        except sqlite3.Error:
            continue
        try:
            if core.is_core6t(con):
                return db
        finally:
            con.close()
    return None


def test_6t(tmp, src_db):
    fam = tmp / "dbt6" / "SCH"
    (fam / "_db").mkdir(parents=True)
    shutil.copy2(src_db, fam / "_db" / "SCHdb")
    db = fam / "_db" / "SCHdb"
    leaf = fam / "lx_TEST6T"
    leaf.mkdir()
    make_xlsx(leaf / "t.xlsx", [
        ["编号", "姓名", "电话", "数值"],
        ["9001", "测试甲", "13900001111", "3.14"],
        ["9002", "测试乙", "13900002222", "-2"],
        ["9003", "测试丙", "", "7"],
    ])
    con = core.open_db(db)
    assert core.is_core6t(con), "拷贝库应为 core-6t 布局"
    before = counts(con)
    ing = core.Ingestor(con, "tst", mask_phi=True)
    _pid, added = ing.ingest(leaf / "t.xlsx", "lx_TEST6T/t.xlsx", "SCH", "lx_TEST6T", "lx_TEST6T")
    core.refresh_meta_counts(con)
    con.commit()
    after = counts(con)
    assert added == 1, added
    assert after["src"] == before["src"] + 1, (before["src"], after["src"])
    assert after["doc"] == before["doc"] + 1, (before["doc"], after["doc"])
    assert after["val"] > before["val"], (before["val"], after["val"])
    assert after["person"] == before["person"] + 3, (before["person"], after["person"])
    assert after["person_key"] == before["person_key"] + 3
    row = con.execute(
        "SELECT family,project_slug,leaf,short_code,doc_id,rel_path,sheet_id,sheet_name,"
        "nrow,ncol,status,kind,n_fact FROM src ORDER BY src_id DESC LIMIT 1").fetchone()
    assert row[0] == "SCH" and row[2] == "lx_TEST6T" and row[3] == "tst", row
    assert row[6] == con.execute("SELECT MAX(src_id) FROM src").fetchone()[0], row
    assert row[11] == "clinical" and row[12] == 11, row  # 3 行 × 4 列，第 3 行电话空
    masked = con.execute(
        "SELECT text FROM val WHERE src_id=? AND item_id="
        "(SELECT item_id FROM item WHERE name='电话')", (row[6],)).fetchall()
    assert len(masked) == 2 and all(t[0].startswith("P") and len(t[0]) == 13 for t in masked), masked
    pk = con.execute(
        "SELECT id_type,id_value,priority FROM person_key WHERE project_id="
        "(SELECT id FROM project WHERE slug='lx_TEST6T')").fetchall()
    assert {k[0] for k in pk} == {"编号"} and {k[2] for k in pk} == {85}, pk
    con.close()
    print(f"A core-6t OK: doc {before['doc']}->{after['doc']} src {before['src']}->{after['src']} "
          f"val {before['val']}->{after['val']} person {before['person']}->{after['person']}")


def test_legacy(tmp):
    fam = tmp / "db7" / "SCH"
    (fam / "_db").mkdir(parents=True)
    db = fam / "_db" / "SCHdb"
    con = core.create_db(db)
    assert not core.is_core6t(con), "legacy 库不应判为 6t"
    leaf = fam / "lx_LEGACY"
    leaf.mkdir()
    make_xlsx(leaf / "t.xlsx", [["编号", "a", "b"], ["1", "1.5", "x"], ["2", "2", "y"]])
    ing = core.Ingestor(con, "tst")
    _pid, added = ing.ingest(leaf / "t.xlsx", "lx_LEGACY/t.xlsx", "SCH", "lx_LEGACY", "lx_LEGACY")
    core.refresh_meta_counts(con)
    con.commit()
    c = counts(con)
    assert added == 1 and c["doc"] == 1 and c["sheet"] == 1, c
    assert c["fact"] == 6 and c["entity"] == 2, c
    con.close()
    print(f"B legacy OK: doc={c['doc']} sheet={c['sheet']} fact={c['fact']} entity={c['entity']}")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="dbc-test-"))
    print(f"tmp={tmp}")
    try:
        src_db = find_6t_db()
        if src_db is None:
            print("A core-6t SKIP: 本机未找到 core-6t 家族库")
        else:
            print(f"A core-6t 样本: {src_db}")
            test_6t(tmp, src_db)
        test_legacy(tmp)
    except AssertionError as e:
        print(f"FAIL assertion: {e}")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
