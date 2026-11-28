"""建库三件套（mkschema / newdb / mkdict）：生成物可复核、建库幂等、dict 归并可追。"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DB5T = REPO / "tools" / "db-library" / "db5t"
MD = DB5T / "schema-v1.md"


def _load(name: str, path: Path):
    root = str(path.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# 模块级加载：fixture 名叫 mkschema 会遮蔽同名模块，测试体里要用模块
MKS = _load("db5t_mkschema", DB5T / "mkschema.py")
NEWDB = _load("db5t_newdb", DB5T / "newdb.py")
MKDICT = _load("db5t_mkdict", DB5T / "mkdict.py")


@pytest.fixture(scope="module")
def schema_sql(tmp_path_factory):
    out = tmp_path_factory.mktemp("schema") / "schema.sql"
    out.write_text(MKS.render(MD), encoding="utf-8")
    return out


def test_schema_sections_and_mods(schema_sql):
    sql = schema_sql.read_text(encoding="utf-8")
    # --check 用的指纹写在首部
    assert "-- source_sha256:" in sql.splitlines()[3]

    fam, proj = sql.split(MKS.MARK_PROJ, 1)
    fam = fam.split(MKS.MARK_FAM, 1)[1]
    # 契 3：家族库不留 dict
    assert "CREATE TABLE dict(" not in fam
    assert fam.count("CREATE TABLE") == 6
    # 7 个模态各一对表 + 各一条带模态名的索引
    for mod in ("radiomics", "us", "mri", "ct", "pet", "path", "report"):
        assert f"CREATE TABLE _modules_{mod}(" in proj
        assert f"CREATE TABLE {mod}_value(" in proj
        assert f"CREATE INDEX ix_modal_{mod}_month" in proj
    assert proj.count("CREATE TABLE") == 19


def test_check_flags_stale(tmp_path, schema_sql):
    assert MKS.main(["--md", str(MD), "--out", str(schema_sql), "--check"]) == 0
    md2 = tmp_path / "schema-v1.md"
    md2.write_text(MD.read_text(encoding="utf-8") + "\n<!-- changed -->\n", encoding="utf-8")
    assert MKS.main(["--md", str(md2), "--out", str(schema_sql), "--check"]) == 1


def test_newdb_builds_standard_structure(tmp_path, schema_sql):
    rc = NEWDB.main(["--family", "TST", "--slug", "s1", "--root", str(tmp_path),
                     "--schema", str(schema_sql), "--schema-md", str(MD)])
    assert rc == 0
    fam_db = tmp_path / "TST" / "_PHI" / "TSTdb"
    proj_db = tmp_path / "TST" / "s1" / "s1db"
    assert fam_db.exists() and proj_db.exists()
    assert (tmp_path / "TST" / "s1" / "目录说明.md").exists()

    c = sqlite3.connect(f"file:{fam_db}?mode=ro", uri=True)
    fam_tabs = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    fam_meta = dict(c.execute("select k, v from meta"))
    c.close()
    assert fam_tabs == {"meta", "id", "id_number", "ref_project", "ref_study", "ref_batch"}
    assert fam_meta["kind"] == "family" and fam_meta["family"] == "TST"

    c = sqlite3.connect(f"file:{proj_db}?mode=ro", uri=True)
    proj_tabs = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    proj_meta = dict(c.execute("select k, v from meta"))
    c.close()
    assert len(proj_tabs) == 19
    assert proj_meta["kind"] == "project" and proj_meta["slug"] == "s1"
    assert "dict_owner" in proj_meta
    assert "period_unit" not in proj_meta  # singleton 不写整数键

    # 幂等：再跑一次不许覆盖，文件 mtime/内容不动
    before = proj_db.stat().st_mtime_ns
    rows = sqlite3.connect(f"file:{proj_db}?mode=ro", uri=True).execute(
        "select count(*) from meta").fetchone()[0]
    assert NEWDB.main(["--family", "TST", "--slug", "s1", "--root", str(tmp_path),
                       "--schema", str(schema_sql), "--schema-md", str(MD)]) == 0
    assert proj_db.stat().st_mtime_ns == before
    assert sqlite3.connect(f"file:{proj_db}?mode=ro", uri=True).execute(
        "select count(*) from meta").fetchone()[0] == rows


def test_newdb_period_unit_is_integer_key(tmp_path, schema_sql):
    NEWDB.main(["--family", "TST", "--slug", "p6", "--root", str(tmp_path),
                "--schema", str(schema_sql), "--schema-md", str(MD), "--period-unit", "6"])
    db = tmp_path / "TST" / "p6" / "p6db"
    meta = dict(sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute("select k, v from meta"))
    assert meta["period_unit"] == "6"
    fam_meta = dict(sqlite3.connect(f"file:{tmp_path / 'TST' / '_PHI' / 'TSTdb'}?mode=ro",
                                    uri=True).execute("select k, v from meta"))
    assert fam_meta["period_units"] == "6"


LEGACY_COLS = ("scope", "table_name", "column_name", "role", "dtype", "aliases", "unit",
               "source", "missing_rule", "description", "added_on")


def _legacy_family(root: Path, fam: str, rows):
    db = root / fam / "_PHI" / f"{fam}db"
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db)
    c.execute(f"CREATE TABLE dict({','.join(LEGACY_COLS)}, PRIMARY KEY(scope, table_name, column_name))")
    c.executemany(f"INSERT INTO dict VALUES ({','.join('?' * len(LEGACY_COLS))})", rows)
    c.commit()
    c.close()


def test_mkdict_merges_and_dedupes(tmp_path, schema_sql):
    root = tmp_path / "RAW"
    shared_a = ("family", "id", "name", "phi", "text", None, None, None, None, "姓名", "2026-11-01")
    shared_b = ("family", "id", "name", "phi", "TEXT", None, None, None, None, "汉字姓名；拉丁走 subject_label", "2026-11-01")
    only = ("proj:lxj_TST", "clin", "age", "feature", "int", None, "year", None, None, "年龄", "2026-11-01")
    _legacy_family(root, "TSTA", [shared_a, only])
    _legacy_family(root, "TSTB", [shared_b])

    out = tmp_path / "dict.db"
    report = tmp_path / "conflicts.tsv"
    rc = MKDICT.main(["--root", str(root), "--out", str(out), "--schema", str(schema_sql),
                      "--report", str(report)])
    assert rc == 0
    c = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    rows = c.execute("select scope, table_name, column_name, dtype, description from dict").fetchall()
    meta = dict(c.execute("select k, v from meta"))
    c.close()

    keys = {(r[0], r[1], r[2]) for r in rows}
    assert ("proj:lxj_TST", "clin", "age") in keys
    assert ("family", "id", "name") in keys
    # 骨架行：子库公共列 + 家族表列都算 stage
    stage = [r for r in rows if r[0] == "stage"]
    assert ("stage", "clin", "family_uid") in keys
    assert ("stage", "id", "family_uid") in keys
    assert ("stage", "src", "sha256") in keys
    assert not any(k[1] == "meta" for k in keys)  # meta 表不登记
    # 同键去重：留描述最长的那条，dtype 归一为小写
    name_row = next(r for r in rows if r[:3] == ("family", "id", "name"))
    assert name_row[3] == "text"  # dtype 归一为小写
    assert "汉字姓名" in name_row[4]
    # 冲突报告留痕
    assert meta["conflicts"] == "1"
    assert report.exists() and "family\tid\tname" in report.read_text(encoding="utf-8")
    assert meta["stage_rows"] == str(len(stage))


def test_newdb_family_only(tmp_path):
    """VEGF 这类无子库的家族：--family-only 只建家族库，不建子库、不写目录说明。"""
    rc = NEWDB.main(["--family", "VEGF", "--root", str(tmp_path), "--family-only",
                     "--allow-stale"])
    assert rc == 0
    fam = tmp_path / "VEGF" / "_PHI" / "VEGFdb"
    assert fam.exists()
    with sqlite3.connect(fam) as c:
        names = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    assert "id" in names and "dict" not in names
    assert not (tmp_path / "VEGF" / "VEGF").exists()


def test_preview_default_out_inside_leaf(tmp_path, schema_sql):
    """preview --out 缺省时落在子工程叶内（可重建预览页），不再写 lab_root。"""
    PREVIEW = _load("db5t_preview", DB5T / "preview.py")
    assert NEWDB.main(["--family", "TST", "--slug", "s1", "--root", str(tmp_path),
                       "--schema", str(schema_sql), "--allow-stale"]) == 0
    rc = PREVIEW.main(["--root", str(tmp_path), "--family", "TST", "--slug", "s1"])
    assert rc == 0
    assert (tmp_path / "TST" / "s1" / "s1-库预览.html").exists()


def test_paths_resolves_family_before_stage(tmp_path, monkeypatch):
    """默认库根优先 {RAW}\\Family（转正后），没有才回落批次 stage。"""
    import importlib
    (tmp_path / "Family").mkdir()
    monkeypatch.setenv("DB5T_RAW", str(tmp_path))
    monkeypatch.delenv("DB5T_ROOT", raising=False)
    paths = importlib.import_module("paths")
    paths = importlib.reload(paths)
    assert paths.STAGE == tmp_path / "Family"
    assert paths.DICT == tmp_path / "Family" / "dict.db"
