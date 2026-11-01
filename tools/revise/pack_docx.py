# -*- coding: utf-8 -*-
"""把解包后的 Word 目录打回修订稿。作者记为 A。

三个课题里的 ``_pack.py``（wky_OEM、wxt_SSD、zyp_EAOC）是同一段代码，
只是路径不同，已收成这一支。

运行前须设环境变量 PACK_DOCX_OFFICE_DIR 指向含 pack.py 的 docx office 目录
（原 .grok bundled 的 docx office 目录已随 .grok 退役）。
未设置或目录无效时直接报错，不退回默认路径。

    set PACK_DOCX_OFFICE_DIR=<含 pack.py 的目录>
    python D:\\0Grok\\0doc\\_code\\07-response/revise\\pack_docx.py ^
        --unpacked <解包目录> --original <原docx> --dest <修订稿.docx>
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────
OFFICE_DIR_ENV = "PACK_DOCX_OFFICE_DIR"   # 含 pack.py 的 docx office 目录；未设置即报错
# ──────────────────────────────────────────────────────────────


def office_dir() -> Path:
    raw = os.environ.get(OFFICE_DIR_ENV, "").strip()
    if not raw:
        raise SystemExit(f"未设置 {OFFICE_DIR_ENV}：需指向含 pack.py 的 docx office 目录")
    path = Path(raw)
    if not (path / "pack.py").is_file():
        raise SystemExit(f"{OFFICE_DIR_ENV} 无效：{path} 下没有 pack.py")
    return path


sys.path.insert(0, str(office_dir()))
from pack import pack  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Pack an unpacked docx; author A.")
    ap.add_argument("--unpacked", required=True)
    ap.add_argument("--original", required=True)
    ap.add_argument("--dest", required=True)
    args = ap.parse_args()
    dest = Path(args.dest)
    tmp = dest.with_name(dest.stem + ".tmp.docx")
    _, msg = pack(
        args.unpacked,
        str(tmp),
        original_file=args.original,
        infer_author_func=lambda _u, _o: "A",
    )
    print(msg)
    shutil.copy2(tmp, dest)
    tmp.unlink()
    print("out", dest, dest.stat().st_size)


if __name__ == "__main__":
    main()
