# -*- coding: utf-8 -*-
"""Compare two text files (or CSV) line by line; report first differences."""
import sys
import difflib
from pathlib import Path


def main(a, b):
    pa, pb = Path(a), Path(b)
    ta = pa.read_text(encoding="utf-8").splitlines()
    tb = pb.read_text(encoding="utf-8").splitlines()
    if ta == tb:
        print(f"[{pa.name}] IDENTICAL ({len(ta)} lines) ✓")
        return 0
    diff = list(difflib.unified_diff(ta, tb, fromfile=pa.name, tofile=pb.name, lineterm=""))
    print(f"[{pa.name}] DIFFERS: {len(ta)} vs {len(tb)} lines, {len(diff)} diff lines")
    for line in diff[:40]:
        print("   " + line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
