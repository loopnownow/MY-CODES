"""阶段 4 的 PHI 导入（mkphi.py）：脱敏、撞唯一索引不并身份、id_number 五类 kind。

2026-11-28 mlh_AKSK / lxf_SCHTUM 的真实踩点：
- 身份证/电话/地址必须脱敏后入库（D71/D75/D72 三条门禁）；
- 脱敏后两人可能撞同一值（同前 3 + 后 4），撞唯一索引时**只丢该字段**，不能并身份；
- 门诊号/住院号/病理号进 id_number，一张人一张号码表；
- meta 记 phi_source / phi_sha256 / phi_imported_at，供复核来源。
"""
from __future__ import annotations

import csv
import importlib.util
import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
DB5T = REPO / "tools" / "db-library" / "db5t"


def _load(name: str, path: pathlib.Path):
    root = str(path.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


MKS = _load("db5t_mkschema_phi", DB5T / "mkschema.py")
NEWDB = _load("db5t_newdb_phi", DB5T / "newdb.py")
MKPHI = _load("db5t_mkphi_phi", DB5T / "mkphi.py")


def _mkdb(tmp_path: pathlib.Path):
    root = tmp_path / "stage"
    NEWDB.main(["--family", "TST", "--slug", "tst_p", "--root", str(root)])
    return root / "TST" / "_PHI" / "TSTdb", root / "TST" / "tst_p" / "tst_pdb"


def _seed_ids(fam_db: pathlib.Path, uids):
    c = sqlite3.connect(fam_db)
    c.execute("delete from id")
    c.executemany(
        "insert into id(family_uid, subject_label, id_status, source, created_on)"
        " values (?,?,?,?,?)",
        [(u, "L", "label_only", "test", "2026-11-28") for u in uids])
    c.commit()
    c.close()


def _phi(tmp_path: pathlib.Path, rows):
    p = tmp_path / "phi.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return p


def _one():
    return [dict(family_uid="TST-000001", name="张三", pinyin="ZHANG SAN",
                 outpatient_no="1200000001", id_card="110101199001011234",
                 phone="13812345678",
                 address="北京市东城区某街道某号院1号楼2单元301室")]


def test_mask_rules():
    assert MKPHI.mask_id_card("110101199001011234") == "110" + "*" * 11 + "1234"
    assert MKPHI.mask_phone("13812345678") == "138****5678"
    addr = MKPHI.mask_address("北京市东城区某街道某号院1号楼2单元301室")
    assert addr == "北京市东城区某街道"
    assert len(addr) <= 20
    assert MKPHI.mask_id_card("") is None


def test_import_writes_name_and_numbers(tmp_path):
    fam, _proj = _mkdb(tmp_path)
    _seed_ids(fam, ["TST-000001"])
    rc = MKPHI.main(["--root", str(tmp_path / "stage"), "--family", "TST", "--slug", "tst_p",
                     "--phi", str(_phi(tmp_path, _one()))])
    assert rc == 0
    c = sqlite3.connect(fam)
    name, = c.execute("select name from id where family_uid='TST-000001'").fetchone()
    assert name == "张三"
    num = c.execute("select kind, value from id_number where family_uid='TST-000001'").fetchall()
    assert ("outpatient", "1200000001") in num
    meta = dict(c.execute("select k, v from meta").fetchall())
    assert meta["phi_source"].endswith("phi.csv")
    assert len(meta["phi_sha256"]) == 64
    c.close()


def test_collision_does_not_merge_identity(tmp_path):
    """两人脱敏后身份证撞同一值：只丢该字段，另一人身份不动。"""
    fam, _proj = _mkdb(tmp_path)
    _seed_ids(fam, ["TST-000001", "TST-000002"])
    rows = [
        dict(family_uid="TST-000001", name="甲", pinyin="", outpatient_no="A1",
             id_card="110101199001011234", phone="13800000001", address=""),
        dict(family_uid="TST-000002", name="乙", pinyin="", outpatient_no="A2",
             id_card="110999199001011234", phone="13800000002", address=""),
    ]
    rc = MKPHI.main(["--root", str(tmp_path / "stage"), "--family", "TST", "--slug", "tst_p",
                     "--phi", str(_phi(tmp_path, rows))])
    assert rc == 0
    c = sqlite3.connect(fam)
    got = c.execute("select name, id_card from id where family_uid='TST-000002'").fetchone()
    assert got[0] == "乙"
    assert got[1] in (None, ""), "撞唯一索引的身份证应只丢该字段"
    c.close()


def test_unknown_uid_exit1(tmp_path):
    fam, _proj = _mkdb(tmp_path)
    _seed_ids(fam, ["TST-000001"])
    rows = _one() + [dict(family_uid="TST-999999", name="查无此人", pinyin="",
                          outpatient_no="", id_card="", phone="", address="")]
    rc = MKPHI.main(["--root", str(tmp_path / "stage"), "--family", "TST", "--slug", "tst_p",
                     "--phi", str(_phi(tmp_path, rows))])
    assert rc == 1
