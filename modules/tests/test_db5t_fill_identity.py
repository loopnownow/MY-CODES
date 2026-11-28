"""阶段 3/5 工具（mkid / fill）与 D95 结构列豁免：跑得通、幂等、账实相符。

2026-11-28 试点 MM 的真实踩点，全部在这里钉成反例：
- `src.rows` 记成「整份源 4 个 sheet 合计」→ D96 立刻 185≠6,637；
- `ref_project.sha256` 在 WAL 落盘前算 → 镜像门永远差一版；
- 家族库按契 3 不再有 dict → D95/D78 若还去家族库读 dict 会 OperationalError；
- `_modules_<mod>` 的 13 个结构列 + `*_val` 的 5 个结构列不是源变量，不进 dict 也要放行。
"""
from __future__ import annotations

import csv
import importlib.util
import os
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


MKS = _load("db5t_mkschema_fill", DB5T / "mkschema.py")
NEWDB = _load("db5t_newdb_fill", DB5T / "newdb.py")
FILL = _load("db5t_fill", DB5T / "fill.py")
MKID = _load("db5t_mkid", DB5T / "mkid.py")


@pytest.fixture(scope="module")
def schema_sql(tmp_path_factory):
    out = tmp_path_factory.mktemp("schema_fill") / "schema.sql"
    out.write_text(MKS.render(MD), encoding="utf-8")
    return out


def _make_source(path: Path):
    """假源库：person 2 人、clin 3 行（多 sheet 合计会远大于 3，用来钉 src.rows 口径）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE person(family_uid TEXT, subject_label TEXT)")
    c.executemany("INSERT INTO person VALUES (?,?)",
                  [("TST-000001", "甲"), ("TST-000002", "乙")])
    c.execute("CREATE TABLE clin(family_uid TEXT, observed_on TEXT, period_month TEXT, "
              "month_ix INTEGER, src_id INTEGER, src_row INTEGER, merged_n INTEGER, "
              "age TEXT, sex TEXT)")
    c.executemany("INSERT INTO clin VALUES (?,?,?,?,?,?,?,?,?)",
                  [("TST-000001", "2024-01-01", "2024-01", 0, 9, 1, 1, "35", "F"),
                   ("TST-000001", "2024-06-01", "2024-06", 5, 9, 2, 1, "35", "F"),
                   ("TST-000002", "2023-05-01", "2023-05", 0, 9, 3, 1, "41", "M")])
    c.execute("CREATE TABLE _modules_us(family_uid TEXT, exam_uid TEXT, site TEXT, size_mm TEXT)")
    c.executemany("INSERT INTO _modules_us VALUES (?,?,?,?)",
                  [("TST-000001", "TST-000001-1", "left", "12.5"),
                   ("TST-000002", "TST-000002-1", "right", "9.0")])
    c.execute("CREATE TABLE us_val(exam_uid TEXT, entity TEXT, feature TEXT, value REAL, unit TEXT)")
    c.executemany("INSERT INTO us_val VALUES (?,?,?,?,?)",
                  [("TST-000001-1", "lesion", "size_mm", 12.5, "mm"),
                   ("TST-000002-1", "lesion", "size_mm", 9.0, "mm")])
    c.commit()
    c.close()


def _make_dict(path: Path):
    """最小全局 dict：clin.age 与 feature 名共用一行（D95/D78 的 aname 兜底要走这条路）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE dict(scope TEXT, table_name TEXT, column_name TEXT, role TEXT, "
              "dtype TEXT, aliases TEXT, unit TEXT, unit_si TEXT, enum_values TEXT, "
              "canonical_id TEXT, source TEXT, missing_rule TEXT, description TEXT, added_on TEXT)")
    rows = [
        ("proj:s1", "clin", "age", "feature", "int", None, "year", None, None, None,
         "src:源表.xlsx#clin.age", None, "年龄", "2026-11-28"),
        ("proj:s1", "clin", "sex", "feature", "text", None, None, None, "F|M", None,
         "src:源表.xlsx#clin.sex", None, "性别", "2026-11-28"),
        ("proj:s1", "_modules_us", "size_mm", "feature", "real", None, "mm", None, None, None,
         "src:源表.xlsx#超声.size", None, "最大径", "2026-11-28"),
    ]
    c.executemany("INSERT INTO dict VALUES (" + ",".join("?" * 14) + ")", rows)
    c.commit()
    c.close()


def _map_csv(path: Path, src_rel: str):
    cols = ["src_file", "src_sheet", "src_header_row", "src_header", "src_column",
            "target_table", "target_column", "dtype", "unit", "enum_values",
            "missing_rule", "transform", "expect_rows"]
    rows = []
    for sc, tc in [("family_uid", "family_uid"), ("subject_label", "subject_label")]:
        rows.append([src_rel, "person", "0", "", sc, "person", tc, "text", "", "", "", "", "2"])
    for sc in ["family_uid", "observed_on", "period_month", "month_ix", "src_id", "src_row",
               "merged_n", "age", "sex"]:
        rows.append([src_rel, "clin", "0", "", sc, "clin", sc, "text", "", "", "", "", "3"])
    for sc in ["family_uid", "exam_uid", "site", "size_mm"]:
        rows.append([src_rel, "_modules_us", "0", "", sc, "_modules_us", sc, "text", "", "", "", "", "2"])
    for sc in ["exam_uid", "entity", "feature", "value", "unit"]:
        rows.append([src_rel, "us_val", "0", "", sc, "us_val", sc, "text", "", "", "", "", "2"])
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)


def test_fill_and_mkid_end_to_end(tmp_path, schema_sql):
    root = tmp_path / "stage"
    NEWDB.main(["--family", "TST", "--slug", "s1", "--root", str(root),
                "--schema", str(schema_sql), "--schema-md", str(MD)])
    src = tmp_path / "src" / "TSTsrc"
    _make_source(src)
    dct = tmp_path / "dict.db"
    _make_dict(dct)
    os.environ["DB5T_DICT"] = str(dct)
    old_dict = FILL.paths.DICT  # paths 在模块加载时读环境变量；测试里直接改常量
    FILL.paths.DICT = dct
    try:
        mp = tmp_path / "map.csv"
        _map_csv(mp, "src/TSTsrc")
        rc = FILL.main(["--map", str(mp), "--root", str(root), "--family", "TST",
                        "--slug", "s1", "--src-root", str(tmp_path)])
        assert rc == 0

        proj = root / "TST" / "s1" / "s1db"
        c = sqlite3.connect(f"file:{proj}?mode=ro", uri=True)
        meta = dict(c.execute("select k, v from meta"))
        n = {t: c.execute(f'select count(*) from "{t}"').fetchone()[0]
             for t in ("person", "clin", "_modules_us", "us_val", "src")}
        src_row = c.execute("select rows, kind, status from src").fetchone()
        prov = c.execute("select src_id, src_row, merged_n from clin order by src_row").fetchall()
        c.close()

        assert n == {"person": 2, "clin": 3, "_modules_us": 2, "us_val": 2, "src": 1}
        # src.rows 只记并入 clin 的行数（3），不是 4 个 sheet 合计（9）
        assert src_row[0] == 3 and src_row[1] == "old_db_blob" and src_row[2] == "active"
        # D50：Σmerged_n 与 meta.src_records_n 对齐
        assert meta["src_records_n"] == "3"
        # D52：源里带着的溯源列不算「被排除的业务列」
        assert meta["excluded_columns"] == "[]"
        # 一格一来源：源自带 src_row 沿用，merged_n 恒 1
        assert prov == [(1, 1, 1), (1, 2, 1), (1, 3, 1)]

        # 幂等：重跑不翻倍
        assert FILL.main(["--map", str(mp), "--root", str(root), "--family", "TST",
                          "--slug", "s1", "--src-root", str(tmp_path)]) == 0
        c = sqlite3.connect(f"file:{proj}?mode=ro", uri=True)
        assert c.execute("select count(*) from clin").fetchone()[0] == 3
        c.close()

        # mkid：搬身份 + 写 ref_project（sha256 必须是落盘后的文件指纹）
        fam_src = tmp_path / "old" / "TST" / "_PHI" / "TSTdb"
        fam_src.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(fam_src)
        c.execute("CREATE TABLE id(family_uid TEXT PRIMARY KEY, name TEXT, "
                  "id_status TEXT NOT NULL, created_on TEXT NOT NULL)")
        c.execute("CREATE TABLE id_number(family_uid TEXT, kind TEXT NOT NULL, "
                  "value TEXT NOT NULL)")
        c.executemany("INSERT INTO id VALUES (?,?,?,?)",
                      [("TST-000001", "甲", "keyed_full", "2026-11-28"),
                       ("TST-000002", "乙", "keyed_full", "2026-11-28")])
        c.executemany("INSERT INTO id_number VALUES (?,?,?)",
                      [("TST-000001", "phone", "13800000001"), ("TST-000002", "phone", "13800000002")])
        c.commit()
        c.close()
        assert MKID.main(["--from-family-db", str(fam_src), "--root", str(root),
                          "--family", "TST", "--slug", "s1"]) == 0
        fam = root / "TST" / "_PHI" / "TSTdb"
        f = sqlite3.connect(f"file:{fam}?mode=ro", uri=True)
        assert f.execute("select count(*) from id").fetchone()[0] == 2
        assert f.execute("select count(*) from id_number").fetchone()[0] == 2
        reg = f.execute("select slug, person_count, clin_person_n, longitudinal_n, multi_record_n, "
                        "sha256 from ref_project").fetchone()
        fam_meta = dict(f.execute("select k, v from meta"))
        f.close()
        assert reg[0] == "s1" and reg[1] == 2 and reg[2] == 3
        assert reg[3] == 1  # 甲有 2 个不同月份
        assert reg[4] == 1  # 甲有 >1 条 clin 行
        assert reg[5] == FILL.sha256(proj)
        assert fam_meta["subject_types"] == '{"s1": "human"}'
        assert fam_meta["identity_grain"] == "person"
    finally:
        FILL.paths.DICT = old_dict
        os.environ.pop("DB5T_DICT", None)


def test_gates_d95_and_d78_exempt_structural_columns(tmp_path, schema_sql):
    """家族库没有 dict（契 3）时，D95 / D78 / dict_cov 仍可跑，且结构列不判缺。"""
    root = tmp_path / "stage"
    NEWDB.main(["--family", "TST", "--slug", "s1", "--root", str(root),
                "--schema", str(schema_sql), "--schema-md", str(MD)])
    dct = tmp_path / "dict.db"
    _make_dict(dct)
    os.environ["DB5T_ROOT"] = str(root)
    os.environ["DB5T_DICT"] = str(dct)
    try:
        g = _load("db5t_gates_struct", DB5T / "gates.py")
        fam = root / "TST" / "_PHI" / "TSTdb"
        proj = root / "TST" / "s1" / "s1db"
        miss, extra = g.dict_cov(proj, "proj:s1", fam)
        # `_modules_<mod>` / `*_val` 的结构列与 meta 等一律豁免
        assert not [m for m in miss if "exam_uid" in m or "family_uid" in m]
        lines, n = g.d95_dict_contract(dct, fam, [("s1", proj)])
        assert not [ln for ln in lines if "缺行" in ln and "exam_uid" in ln]
        out, bad = g.d78_dict_provenance(fam)
        assert not [ln for ln in out if "feature" in ln]
    finally:
        os.environ.pop("DB5T_ROOT", None)
        os.environ.pop("DB5T_DICT", None)
