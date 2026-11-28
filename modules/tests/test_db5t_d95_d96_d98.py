"""t2298u: D95 / D96 / D98 正反 fixture；DB5T_DICT 环境变量可覆盖。"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DB5T = REPO / "tools" / "db-library" / "db5t"


def _load(name: str, path: Path, strict: bool = False):
    # gates.py does `import paths as B`; keep db5t on sys.path like a script run.
    root = str(path.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    if strict:
        os.environ["DB5T_GATES_STRICT"] = "1"
    else:
        os.environ.pop("DB5T_GATES_STRICT", None)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
    finally:
        os.environ.pop("DB5T_GATES_STRICT", None)
    return mod


@pytest.fixture(scope="module")
def gates():
    """严格模式：D95 / D96 / D98 判 P1（只有翻牌后才是业务默认）。"""
    return _load("db5t_gates_under_test", DB5T / "gates.py", strict=True)


@pytest.fixture(scope="module")
def gates_ro():
    """默认模式：D95 / D96 / D98 report-only。"""
    return _load("db5t_gates_ro_under_test", DB5T / "gates.py", strict=False)


def _dict_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE dict (
          scope        TEXT NOT NULL,
          table_name   TEXT NOT NULL,
          column_name  TEXT NOT NULL,
          dtype        TEXT,
          PRIMARY KEY (scope, table_name, column_name)
        )
        """
    )


def _proj_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    conn.execute(
        "CREATE TABLE src (src_id INTEGER PRIMARY KEY, status TEXT, rows INTEGER, rel_path TEXT, sha256 TEXT, kind TEXT, note TEXT)"
    )
    conn.execute(
        "CREATE TABLE clin (family_uid TEXT, src_id INTEGER, src_row INTEGER, merged_n INTEGER, size_val REAL, note TEXT)"
    )
    conn.execute("CREATE TABLE person (family_uid TEXT, subject_label TEXT, id_status TEXT)")
    conn.execute("CREATE TABLE qc (code TEXT, status TEXT)")


def _fill_stage_dict(conn: sqlite3.Connection, extra=()):
    rows = [
        ("stage", "src", "src_id", "INTEGER"),
        ("stage", "src", "status", "TEXT"),
        ("stage", "src", "rows", "INTEGER"),
        ("stage", "src", "rel_path", "TEXT"),
        ("stage", "src", "sha256", "TEXT"),
        ("stage", "src", "kind", "TEXT"),
        ("stage", "src", "note", "TEXT"),
        ("stage", "clin", "family_uid", "TEXT"),
        ("stage", "clin", "src_id", "INTEGER"),
        ("stage", "clin", "src_row", "INTEGER"),
        ("stage", "clin", "merged_n", "INTEGER"),
        ("stage", "person", "family_uid", "TEXT"),
        ("stage", "person", "subject_label", "TEXT"),
        ("stage", "person", "id_status", "TEXT"),
        ("stage", "qc", "code", "TEXT"),
        ("stage", "qc", "status", "TEXT"),
        ("stage", "id", "family_uid", "TEXT"),
        ("stage", "ref_project", "slug", "TEXT"),
    ]
    rows.extend(extra)
    conn.executemany(
        "INSERT INTO dict(scope, table_name, column_name, dtype) VALUES (?,?,?,?)",
        rows,
    )


def _write_ok_tree(root: Path) -> tuple[Path, Path, list[tuple[str, Path]]]:
    """Minimal 1+N tree: global dict.db, family db, one project db."""
    dict_db = root / "dict.db"
    fam_db = root / "FAM" / "_PHI" / "FAMdb"
    proj = root / "FAM" / "demo" / "demod"
    fam_db.parent.mkdir(parents=True)
    proj.parent.mkdir(parents=True)

    dc = sqlite3.connect(dict_db)
    _dict_schema(dc)
    _fill_stage_dict(
        dc,
        extra=[
            ("proj:demo", "clin", "size_val", "REAL"),
            ("proj:demo", "clin", "note", "TEXT"),
        ],
    )
    dc.commit()
    dc.close()

    fc = sqlite3.connect(fam_db)
    fc.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    fc.execute("INSERT INTO meta VALUES ('kind', 'family')")
    fc.execute("CREATE TABLE id (family_uid TEXT)")
    fc.execute("CREATE TABLE ref_project (slug TEXT)")
    fc.execute("INSERT INTO ref_project VALUES ('demo')")
    fc.commit()
    fc.close()

    pc = sqlite3.connect(proj)
    _proj_schema(pc)
    pc.execute("INSERT INTO meta VALUES ('kind', 'project')")
    pc.execute("INSERT INTO src(src_id, status, rows, rel_path, sha256, kind, note) VALUES (1, 'active', 2, 'a.csv', 'x', 'file', '-')")
    pc.execute("INSERT INTO clin VALUES ('FAM-000001', 1, 1, 1, 1.2, 'ok')")
    pc.execute("INSERT INTO clin VALUES ('FAM-000002', 1, 2, 1, 3.4, 'ok')")
    pc.commit()
    pc.close()
    return dict_db, fam_db, [("demo", proj)]


def test_d95_d96_d98_pass(tmp_path, gates):
    dict_db, fam_db, projs = _write_ok_tree(tmp_path)
    lines, n = gates.d95_dict_contract(dict_db, fam_db, projs)
    assert n == 0, lines
    lines, n = gates.d96_count_conservation(projs)
    assert n == 0, lines
    lines, n = gates.d98_ascii_units(fam_db, projs)
    assert n == 0, lines


def test_d95_missing_column_and_dtype_conflict(tmp_path, gates):
    dict_db, fam_db, projs = _write_ok_tree(tmp_path)
    # drop the proj-level size_val row so coverage fails; also type-conflict note as INTEGER vs TEXT
    dc = sqlite3.connect(dict_db)
    dc.execute("DELETE FROM dict WHERE column_name='size_val'")
    # 用真变量列造 dtype 矛盾：`note` 是 schema 声明的结构列，D95 已豁免，不能再当探针
    dc.execute("INSERT INTO dict(scope, table_name, column_name, dtype) "
               "VALUES ('proj:demo', 'clin', 'age', 'TEXT')")
    dc.commit()
    dc.close()
    pc = sqlite3.connect(projs[0][1])
    pc.execute("ALTER TABLE clin ADD COLUMN age REAL")
    pc.commit()
    pc.close()
    _lines, n = gates.d95_dict_contract(dict_db, fam_db, projs)
    assert n >= 2, _lines
    joined = "\n".join(_lines)
    assert "size_val" in joined
    assert "age" in joined


def test_d95_scope_prefers_proj_over_stage(tmp_path, gates):
    dict_db, fam_db, projs = _write_ok_tree(tmp_path)
    dc = sqlite3.connect(dict_db)
    # stage says TEXT, proj says REAL — resolved dtype must be REAL, matching storage
    dc.execute(
        "INSERT INTO dict(scope, table_name, column_name, dtype) VALUES ('stage', 'clin', 'size_val', 'TEXT')"
    )
    dc.commit()
    dc.close()
    _lines, n = gates.d95_dict_contract(dict_db, fam_db, projs)
    assert n == 0, _lines


def test_d95_missing_dict_db_is_p1(tmp_path, gates):
    _dict_db, fam_db, projs = _write_ok_tree(tmp_path)
    missing = tmp_path / "no-such-dict.db"
    lines, n = gates.d95_dict_contract(missing, fam_db, projs)
    assert n == 1
    assert any("dict.db" in ln or "缺" in ln for ln in lines)
    assert any("!! P1" in ln for ln in lines), lines


def test_d96_mismatch_fails(tmp_path, gates):
    _dict_db, fam_db, projs = _write_ok_tree(tmp_path)
    slug, path = projs[0]
    c = sqlite3.connect(path)
    c.execute("UPDATE src SET rows=99 WHERE src_id=1")
    c.commit()
    c.close()
    lines, n = gates.d96_count_conservation(projs)
    assert n == 1, lines
    assert "demo" in "\n".join(lines)


def test_d96_inactive_src_excluded(tmp_path, gates):
    _dict_db, fam_db, projs = _write_ok_tree(tmp_path)
    slug, path = projs[0]
    c = sqlite3.connect(path)
    c.execute(
        "INSERT INTO src(src_id, status, rows, rel_path, sha256, kind, note) VALUES (2, 'excluded', 50, 'b.csv', 'y', 'file', '-')"
    )
    c.commit()
    c.close()
    _lines, n = gates.d96_count_conservation(projs)
    assert n == 0, _lines


def test_d98_chinese_name_and_embedded_unit_and_value(tmp_path, gates):
    dict_db, fam_db, projs = _write_ok_tree(tmp_path)
    slug, path = projs[0]
    c = sqlite3.connect(path)
    c.execute("ALTER TABLE clin ADD COLUMN 疼痛时长 TEXT")
    c.execute("ALTER TABLE clin ADD COLUMN height_cm REAL")
    c.execute("UPDATE clin SET note='300d' WHERE src_row=1")
    c.commit()
    c.close()
    # dict coverage is not this test; D98 only
    lines, n = gates.d98_ascii_units(fam_db, projs)
    joined = "\n".join(lines)
    assert n >= 3, lines
    assert "疼痛时长" in joined
    assert "height_cm" in joined
    assert "300d" in joined


def test_d93_d94_d97_d99_are_report_only(gates):
    # stubs / report-only helpers must not use '!!' and must return a zero fail count
    for fn_name in ("d93_ri_soft", "d94_qc_codes", "d97_col_purity", "d99_enum_domain"):
        fn = getattr(gates, fn_name)
        lines, n = fn([])
        assert n == 0
        assert all("!!" not in ln for ln in lines)


def test_d95_d96_d98_report_only_by_default(tmp_path, gates_ro):
    """默认模式（不设 DB5T_GATES_STRICT）不判级：不出 '!!'，计数照出。"""
    assert gates_ro.STRICT is False
    dict_db, fam_db, projs = _write_ok_tree(tmp_path)
    # 三种违规各来一处，计数必须一样，只是不带 '!!'
    lines, n = gates_ro.d95_dict_contract(tmp_path / "no-such-dict.db", fam_db, projs)
    assert n == 1 and not any("!!" in ln for ln in lines), lines
    slug, path = projs[0]
    c = sqlite3.connect(path)
    c.execute("UPDATE src SET rows=99 WHERE src_id=1")
    c.execute("ALTER TABLE clin ADD COLUMN 疼痛时长 TEXT")
    c.commit()
    c.close()
    lines, n = gates_ro.d96_count_conservation(projs)
    assert n == 1 and not any("!!" in ln for ln in lines), lines
    lines, n = gates_ro.d98_ascii_units(fam_db, projs)
    assert n >= 1 and not any("!!" in ln for ln in lines), lines


def test_strict_mode_marks_p1(gates):
    """严格模式（DB5T_GATES_STRICT=1 / --strict）才写 '!! P1'。"""
    assert gates.STRICT is True


def test_db5t_dict_env_override(tmp_path, monkeypatch):
    target = tmp_path / "custom-dict.db"
    monkeypatch.setenv("DB5T_DICT", str(target))
    mod = _load("db5t_paths_env", DB5T / "paths.py")
    assert Path(mod.DICT) == target
    text = mod.describe()
    assert "DICT" in text
    assert "DB5T_DICT=set" in text
