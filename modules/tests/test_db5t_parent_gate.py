"""t2307u: `<mod>_value` 与 `_modules_<mod>` 的成对门（D68 §2 顺序门）。

改名 `_val` → `_value` 时这里踩过一次：父表名靠 `t[:-4]` 剥后缀，
`us_value`[:-4] 得到 `us_v`，于是 7 张长表全被报「缺少父表 _modules_us_v」。
本文件锁住两种后缀都能剥对，且旧名 `<mod>_val` 仍然兼容（退役库）。
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DB5T = REPO / "tools" / "db-library" / "db5t"


def _load(name: str, path: Path):
    root = str(path.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


GATES = _load("db5t_gates_parent", DB5T / "gates.py")


def _mk(tmp_path: Path, tables: dict[str, str]) -> Path:
    p = tmp_path / "projdb"
    c = sqlite3.connect(p)
    for ddl in tables.values():
        c.execute(ddl)
    c.commit()
    c.close()
    return p


_MOD = "create table _modules_us(exam_uid text primary key, family_uid text)"
_VAL_V = ("create table us_value(exam_uid text, entity text, feature text, "
          "value real, unit text, primary key(exam_uid, entity, feature))")
_VAL_LEGACY = _VAL_V.replace("us_value", "us_val")


def test_value_pair_ok(tmp_path):
    db = _mk(tmp_path, {"m": _MOD, "v": _VAL_V})
    assert GATES.parent_gates(str(db)) == []


def test_value_without_parent_names_right_parent(tmp_path):
    db = _mk(tmp_path, {"v": _VAL_V})
    bad = GATES.parent_gates(str(db))
    assert len(bad) == 1
    level, msg, _n = bad[0]
    assert level == "P0"
    assert "_modules_us" in msg
    assert "_modules_us_v" not in msg


def test_legacy_val_pair_still_accepted(tmp_path):
    db = _mk(tmp_path, {"m": _MOD, "v": _VAL_LEGACY})
    assert GATES.parent_gates(str(db)) == []


def test_orphan_exam_reported(tmp_path):
    db = _mk(tmp_path, {"m": _MOD, "v": _VAL_V})
    c = sqlite3.connect(db)
    c.execute("insert into us_value values('nope','e','f',1.0,'mm')")
    c.commit()
    c.close()
    bad = GATES.parent_gates(str(db))
    assert bad and "exam_uid" in bad[0][1]
