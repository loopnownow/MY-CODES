"""阶段 5/4 的新口径：missing_rule（缺值判据）、transform 的 const/mint、xlsx 源、mkid --from-person。

2026-11-28 sw_CI 试点的真实踩点，钉成反例：
- 源表里 `NA` 是文本，直接写进 REAL 列 → D97 判「真混合」（real 67 / text 31）；
- 列名 `Sex (1=M, 0=F)` 带逗号，map.csv 手写会被切成两列 → 必须用 csv.writer；
- 根数据从零建库时没有退役家族库可搬，身份号只能从子库 person 发（`--from-person`）；
- `missing_rule=stop` 命中时必须整组不写（契 6：不确定就停），不能半张表落库。
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


MKS = _load("db5t_mkschema_tf", DB5T / "mkschema.py")
NEWDB = _load("db5t_newdb_tf", DB5T / "newdb.py")
FILL = _load("db5t_fill_tf", DB5T / "fill.py")
MKID = _load("db5t_mkid_tf", DB5T / "mkid.py")
PREVIEW = _load("db5t_preview_tf", DB5T / "preview.py")


@pytest.fixture(scope="module")
def schema_sql(tmp_path_factory):
    out = tmp_path_factory.mktemp("schema_tf") / "schema.sql"
    out.write_text(MKS.render(MD), encoding="utf-8")
    return out


# ---------- 纯函数：missing_rule / transform ----------

@pytest.mark.parametrize("rule,mode,tokens", [
    ("", "default", {""}),
    (None, "default", {""}),
    ("keep", "keep", set()),
    ("stop", "stop", {""}),
    ("NA|NULL|.", "tokens", {"na", "null", ".", ""}),
    ("-", "tokens", {"-", ""}),
])
def test_parse_rule(rule, mode, tokens):
    assert FILL.parse_rule(rule) == (mode, tokens)


def test_value_of_missing_and_transform():
    mint = FILL.Mint()
    src = {"id": "S-1", "hb": "NA", "note": "  ", "sex": "1"}

    # 标记命中 → NULL；keep → 原样（连空串也保留）；默认 → 只把空串当缺
    assert FILL.value_of({"src_column": "hb", "missing_rule": "NA"}, src, mint) is None
    assert FILL.value_of({"src_column": "hb", "missing_rule": "keep"}, src, mint) == "NA"
    assert FILL.value_of({"src_column": "note", "missing_rule": "keep"}, src, mint) == ""
    assert FILL.value_of({"src_column": "note"}, src, mint) is None
    # transform 优先于源列
    assert FILL.value_of({"src_column": "note", "transform": "const:UNK",
                          "missing_rule": "keep"}, src, mint) == "UNK"


def test_stop_rule_raises():
    with pytest.raises(FILL.MissingValue):
        FILL.value_of({"src_column": "sid", "missing_rule": "stop"},
                      {"sid": ""}, FILL.Mint())


def test_mint_cross_sheet_reuse():
    """同一源值只发一次号：person 先发，clin 拿同一个（不然 FK 断）。"""
    mint = FILL.Mint()
    p = {"src_column": "sid", "transform": "mint:TST-900000"}
    c = {"src_column": "sid", "transform": "mint:TST-900000"}
    a = FILL.value_of(p, {"sid": "S-1"}, mint)
    assert a == "TST-900001"
    assert FILL.value_of(c, {"sid": "S-1"}, mint) == a          # 跨 sheet 复用
    assert FILL.value_of(p, {"sid": "S-2"}, mint) == "TST-900002"
    assert FILL.value_of(p, {"sid": "S-1"}, mint) == a          # 再次出现仍是原号


# ---------- 端到端 ----------

HEADER = ["Subject_ID", "Group", "Sex (1=M, 0=F)", "Age", "Hemoglobin", "Education_years"]
ROWS = [
    ["C001", "ESRD-CI", "1", "56", "83.9", "4.7"],
    ["C002", "HC", "0", "61", "NA", ""],
]


def _make_xlsx(path: Path, rows=ROWS):
    from openpyxl import Workbook

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Raw Data"
    ws.append(HEADER)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _write_dict(path: Path, slug: str = "s1"):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE dict(scope TEXT, table_name TEXT, column_name TEXT, role TEXT, "
              "dtype TEXT, aliases TEXT, unit TEXT, unit_si TEXT, enum_values TEXT, "
              "canonical_id TEXT, source TEXT, missing_rule TEXT, description TEXT, added_on TEXT)")
    rows = [
        ("proj:s1", "clin", "cohort", "key", "enum", None, None, None, "ESRD-CI|HC", None,
         "src:源.xlsx#Group", None, "队列", "2026-11-28"),
        ("proj:s1", "clin", "sex", "feature", "enum", None, None, None, "1=M|0=F", None,
         "src:源.xlsx#Sex", None, "性别", "2026-11-28"),
        ("proj:s1", "clin", "age", "feature", "int", None, "year", None, None, None,
         "src:源.xlsx#Age", None, "年龄", "2026-11-28"),
        ("proj:s1", "clin", "education_years", "feature", "real", None, "year", None, None, None,
         "src:源.xlsx#Edu", None, "受教育年限", "2026-11-28"),
        ("proj:s1", "clin", "hemoglobin", "feature", "real", None, "g/L", None, None, None,
         "src:源.xlsx#Hb", None, "血红蛋白", "2026-11-28"),
    ]
    c.executemany("INSERT INTO dict VALUES (" + ",".join("?" * 14) + ")", rows)
    c.commit()
    c.close()


def _write_map(path: Path, src_rel: str, hb_rule: str = "NA", sid_rule: str = "stop"):
    cols = ["src_file", "src_sheet", "src_header_row", "src_header", "src_column",
            "target_table", "target_column", "dtype", "unit", "enum_values",
            "missing_rule", "transform", "expect_rows"]
    rows = [
        [src_rel, "Raw Data", "1", "", "Subject_ID", "person", "family_uid", "text", "", "",
         sid_rule, "mint:TST-900000", "2"],
        [src_rel, "Raw Data", "1", "", "Subject_ID", "person", "subject_label", "text", "", "",
         "keep", "", "2"],
        [src_rel, "Raw Data", "1", "", "", "person", "id_status", "text", "", "",
         "", "const:label_only", "2"],
        [src_rel, "Raw Data", "1", "", "Subject_ID", "clin", "family_uid", "text", "", "",
         sid_rule, "mint:TST-900000", "2"],
        [src_rel, "Raw Data", "1", "", "Group", "clin", "cohort", "enum", "", "ESRD-CI|HC",
         "keep", "", "2"],
        [src_rel, "Raw Data", "1", "", "", "clin", "period_month", "text", "", "",
         "", "const:UNK", "2"],
        [src_rel, "Raw Data", "1", "", "Sex (1=M, 0=F)", "clin", "sex", "enum", "", "1=M|0=F",
         "keep", "", "2"],
        [src_rel, "Raw Data", "1", "", "Age", "clin", "age", "int", "year", "", "keep", "", "2"],
        [src_rel, "Raw Data", "1", "", "Education_years", "clin", "education_years", "real",
         "year", "", hb_rule, "", "2"],
        [src_rel, "Raw Data", "1", "", "Hemoglobin", "clin", "hemoglobin", "real", "g/L", "",
         hb_rule, "", "2"],
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)   # 必须走 csv.writer：列名里有逗号，手写会被切成两列
        w.writerow(cols)
        w.writerows(rows)


def _build(tmp_path, schema_sql, slug="s1"):
    root = tmp_path / "stage"
    NEWDB.main(["--family", "TST", "--slug", slug, "--root", str(root),
                "--schema", str(schema_sql), "--schema-md", str(MD), "--period-unit", "1"])
    return root


def _fill(tmp_path, root, dct, hb_rule="NA", sid_rule="stop", rows=ROWS):
    src = tmp_path / "archived" / "TST" / "s1" / "FinalData_s1.xlsx"
    _make_xlsx(src, rows)
    mp = tmp_path / "map.csv"
    _write_map(mp, "archived/TST/s1/FinalData_s1.xlsx", hb_rule=hb_rule, sid_rule=sid_rule)
    FILL.paths.DICT = dct
    os.environ["DB5T_DICT"] = str(dct)
    try:
        return FILL.main(["--map", str(mp), "--root", str(root), "--family", "TST",
                          "--slug", "s1", "--src-root", str(tmp_path)])
    finally:
        os.environ.pop("DB5T_DICT", None)


def test_fill_xlsx_with_missing_rule(tmp_path, schema_sql):
    root = _build(tmp_path, schema_sql)
    dct = tmp_path / "dict.db"
    _write_dict(dct)
    old = FILL.paths.DICT
    try:
        assert _fill(tmp_path, root, dct) == 0
    finally:
        FILL.paths.DICT = old

    proj = root / "TST" / "s1" / "s1db"
    c = sqlite3.connect(f"file:{proj}?mode=ro", uri=True)
    person = c.execute("select family_uid, subject_label, id_status from person order by 1").fetchall()
    row = c.execute("select family_uid, cohort, period_month, sex, age, education_years, "
                    "hemoglobin, typeof(hemoglobin) from clin order by family_uid").fetchall()
    src = c.execute("select kind, rows from src").fetchone()
    c.close()

    assert person == [("TST-900001", "C001", "label_only"),
                      ("TST-900002", "C002", "label_only")]
    # 带逗号的列名照常映射；NA → NULL（不再以文本落进 REAL 列）；空串 → NULL
    assert row[0] == ("TST-900001", "ESRD-CI", "UNK", "1", 56, 4.7, 83.9, "real")
    assert row[1] == ("TST-900002", "HC", "UNK", "0", 61, None, None, "null")
    assert src[0] == "workbook" and src[1] == 2


def test_fill_stop_rule_writes_nothing(tmp_path, schema_sql):
    root = _build(tmp_path, schema_sql)
    dct = tmp_path / "dict.db"
    _write_dict(dct)
    bad = [["C001", "ESRD-CI", "1", "56", "83.9", "4.7"],
           ["", "HC", "0", "61", "12.0", "3.0"]]   # 第二行 Subject_ID 空 → 契 6 停
    old = FILL.paths.DICT
    try:
        assert _fill(tmp_path, root, dct, rows=bad) == 1
    finally:
        FILL.paths.DICT = old

    proj = root / "TST" / "s1" / "s1db"
    c = sqlite3.connect(f"file:{proj}?mode=ro", uri=True)
    n = {t: c.execute(f'select count(*) from "{t}"').fetchone()[0]
         for t in ("person", "clin")}
    c.close()
    assert n == {"person": 0, "clin": 0}   # 整组不写


def test_mkid_from_person(tmp_path, schema_sql):
    """根数据从零建：没有退役家族库，只能从子库 person 发 id；period_unit 跟库里走。"""
    root = _build(tmp_path, schema_sql)
    dct = tmp_path / "dict.db"
    _write_dict(dct)
    old = FILL.paths.DICT
    try:
        assert _fill(tmp_path, root, dct) == 0
    finally:
        FILL.paths.DICT = old
    assert MKID.main(["--from-person", "--root", str(root), "--family", "TST",
                      "--slug", "s1", "--id-status", "label_only"]) == 0

    fam = root / "TST" / "_PHI" / "TSTdb"
    proj = root / "TST" / "s1" / "s1db"
    f = sqlite3.connect(f"file:{fam}?mode=ro", uri=True)
    ids = f.execute("select family_uid, id_status, source from id order by 1").fetchall()
    n_num = f.execute("select count(*) from id_number").fetchone()[0]
    reg = f.execute("select person_count, clin_person_n, period_unit, sha256 from ref_project").fetchone()
    fam_meta = dict(f.execute("select k, v from meta"))
    f.close()
    p = sqlite3.connect(f"file:{proj}?mode=ro", uri=True)
    proj_meta = dict(p.execute("select k, v from meta"))
    p.close()

    assert [r[0] for r in ids] == ["TST-900001", "TST-900002"]
    assert {r[1] for r in ids} == {"label_only"}
    assert {r[2] for r in ids} == {"person:root-xlsx"}
    assert n_num == 0
    assert reg[:2] == (2, 2)                      # person / clin 计数
    assert reg[2] == 1                            # period_unit 从子库 meta 镜像过来（INTEGER）
    assert reg[3] == FILL.sha256(proj)
    assert fam_meta["identity_grain"] == "person"
    assert fam_meta["subject_types"] == '{"s1": "human"}'
    assert proj_meta["identity_grain"] == "person"


def test_preview_renders_kpi(tmp_path, schema_sql):
    """阶段 7：预览只读生成，内容能回答「几个人、几列、门禁跑了没」。"""
    root = _build(tmp_path, schema_sql)
    dct = tmp_path / "dict.db"
    _write_dict(dct)
    old = FILL.paths.DICT
    try:
        assert _fill(tmp_path, root, dct) == 0
    finally:
        FILL.paths.DICT = old
    assert MKID.main(["--from-person", "--root", str(root), "--family", "TST",
                      "--slug", "s1"]) == 0

    out = tmp_path / "TST-预览.html"
    old_dict = PREVIEW.paths.DICT
    PREVIEW.paths.DICT = dct
    try:
        assert PREVIEW.main(["--root", str(root), "--family", "TST", "--slug", "s1",
                             "--out", str(out)]) == 0
    finally:
        PREVIEW.paths.DICT = old_dict

    t = out.read_text(encoding="utf-8")
    assert "建库预览" in t and "TST / s1" in t
    assert "<b>2</b><span>person 行（身份数）</span>" in t
    assert "<b>2</b><span>clin 行</span>" in t
    assert "TST-900001" in t                      # 抽样里有真身份号
    assert "（未跑：加 --gates）" in t            # 默认不跑门禁，避免预览拖慢
