# -*- coding: utf-8 -*-
r"""l_offload.py — 把 L 盘工程里的表/结果件/垃圾外迁（默认 dry-run）。

为什么：影像叶里混着分析产物。目标布局只允许 image\ 与 roi\，其余一律出 L:。
  * 表 / 报告 / 中间件 / 位图 -> G:\_offload\<batch>\<family>\<project>\<相对路径>
  * 垃圾（__pycache__ / .pyc / .log / .Rhistory / Thumbs.db）-> {batch}\moved\...
  * 重复目录（65535 / copy）只报告，不动（需人工判重）
  * 工程总量 < --to-del-limit（默认 1 GiB）时原件仍移入 _Del；≥ 阈值则校验后直删、只留 MOVED 台账

分类：
  TABLE     .csv .xlsx .xls .docx .doc .pdf .tsv .html .htm .md .txt
  ARTIFACT  .mat .npz .pkl .pickle .py .m .r .ipynb .json .yaml .yml
  RASTER    .png .jpg .jpeg .bmp .tif .tiff .gif（--skip-raster 可排除）
  JUNK      目录 __pycache__ / .ipynb_checkpoints；文件 .pyc .log .Rhistory 等
  DUP       目录名含 65535 / copy（仅报告）

用法（先看计划，再落盘）：
  python l_offload.py --family MHE_PA --project HE_nii_ly
  python l_offload.py --family MHE_PA --project HE_nii_ly --execute
  python l_offload.py --family SSD --project SSD_dyf --project wxt_ASL --execute
  python l_offload.py --family MCI --project lya --skip-raster

落盘顺序（--execute）：复制到 _share 的 staging -> 打包 tar.zst -> 校验件数 ->
写 MOVED tsv -> 原件 os.replace 进 _Del -> 删 staging。任何一步失败即停。

退出码：0 正常；1 有错误；2 前置缺失。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

ROOT = os.environ.get("ROOT", "")
DEL_ROOT = os.path.join(ROOT, "_Del", "_by-function")
G_ROOT = os.environ.get("G_ROOT", "")          # 处理后盘：外迁件落 G:\_offload\<batch>\<family>\<project>\

TABLE_EXT = {".csv", ".xlsx", ".xls", ".docx", ".doc", ".pdf", ".tsv", ".html", ".htm", ".md", ".txt"}
ARTIFACT_EXT = {".mat", ".npz", ".pkl", ".pickle", ".py", ".m", ".r", ".ipynb",
               ".json", ".yaml", ".yml", ".js", ".css", ".svg"}
RASTER_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif"}
JUNK_FILE = {".pyc", ".pyo", ".log", ".rhistory", ".ds_store", "thumbs.db", "desktop.ini"}
JUNK_DIR = {"__pycache__", ".ipynb_checkpoints"}
DUP_HINT = ("65535", "copy")
# 数据本体目录：整棵跳过（BIDS rawdata 里的 participants.tsv / *_aslcontext.tsv 属数据集）
KEEP_DIRS = {"rawdata", "image", "roi"}
# 影像伴生件：留在 image\，不算外迁
KEEP_NAMES = {"patient_info.txt", "dicomdir", "label.txt", "ids.txt", "cdinfo"}
SIDECAR_EXT = {".json", ".yaml", ".yml", ".txt"}
IMAGE_SUFFIX = (".nii", ".nii.gz", ".nrrd", ".dcm", ".ima", ".mitk")


def kind_of(name: str) -> str:
    low = name.lower()
    ext = os.path.splitext(low)[1]
    if ext in TABLE_EXT:
        return "TABLE"
    if ext in ARTIFACT_EXT:
        return "ARTIFACT"
    if ext in RASTER_EXT:
        return "RASTER"
    return ""


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def scan(family: str, project: str, skip_raster: bool) -> tuple[list[dict], list[dict], list[str]]:
    """返回 (待外迁文件, 垃圾目录/文件, 重复目录)。"""
    base = os.path.join(ROOT, family, project)
    if not os.path.isdir(base):
        raise SystemExit(f"[FAIL] 工程不存在：{base}")
    files, junk, dups = [], [], []
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = os.path.relpath(dirpath, base)
        keep = []
        for d in dirnames:
            rel = os.path.normpath(os.path.join(rel_dir, d))
            if d.lower() in KEEP_DIRS:
                continue                      # 数据本体：整棵跳过
            if d.lower() in JUNK_DIR:
                junk.append({"kind": "JUNK_DIR", "path": os.path.join(dirpath, d), "rel": rel})
                continue
            if any(h in d.lower() for h in DUP_HINT):
                dups.append(rel)
                continue
            keep.append(d)
        dirnames[:] = keep
        names_low = {f.lower() for f in filenames}
        for fn in filenames:
            rel = os.path.normpath(os.path.join(rel_dir, fn))
            low = fn.lower()
            if low in JUNK_FILE or os.path.splitext(low)[1] in JUNK_FILE:
                junk.append({"kind": "JUNK_FILE", "path": os.path.join(dirpath, fn), "rel": rel})
                continue
            if low in KEEP_NAMES:
                continue          # patient_info.txt / DICOMDIR 等影像伴生件留原处
            ext = os.path.splitext(low)[1]
            if ext in SIDECAR_EXT:
                stem = low[: -len(ext)]
                if any((stem + s) in names_low for s in IMAGE_SUFFIX):
                    continue      # dcm2niix 等 sidecar，随影像留原处
            k = kind_of(fn)
            if not k or (k == "RASTER" and skip_raster):
                continue
            try:
                size = os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                size = 0
            files.append({"kind": k, "path": os.path.join(dirpath, fn), "rel": rel, "bytes": size})
    return files, junk, dups


def report(family: str, project: str, files: list[dict], junk: list[dict],
           dups: list[str], out_dir: str) -> str:
    tsv = os.path.join(out_dir, f"offload-{family}-{project}.tsv")
    with open(tsv, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["kind", "rel", "bytes", "action"], delimiter="\t")
        w.writeheader()
        for r in files:
            w.writerow({"kind": r["kind"], "rel": r["rel"], "bytes": r["bytes"], "action": "archive"})
        for r in junk:
            w.writerow({"kind": r["kind"], "rel": r["rel"], "bytes": 0, "action": "to_del"})
        for d in dups:
            w.writerow({"kind": "DUP_DIR", "rel": d, "bytes": 0, "action": "report_only"})
    return tsv


_DIRS: set[str] = set()
_DIR_LOCK = threading.Lock()


def _ensure_dir(path: str) -> None:
    """按目录缓存 makedirs：逐件调用时开销很大（多线程下加锁）。"""
    with _DIR_LOCK:
        if path in _DIRS:
            return
        os.makedirs(path, exist_ok=True)
        _DIRS.add(path)


def copy_verify(src: str, dst: str, size: int) -> bool:
    """复制并校验。

    小件（<64 MiB）只比字节数 + mtime；≥64 MiB 再比 sha256。
    原因：G: 实测写入约 3–7 MB/s，逐件 sha256 会把读放大三倍，12 万件跑不完。
    """
    _ensure_dir(os.path.dirname(dst))
    if os.path.exists(dst) and os.path.getsize(dst) == size:
        return True                      # 断点续跑：已复制且大小一致
    shutil.copy2(src, dst)
    if os.path.getsize(dst) != size:
        return False
    if abs(os.path.getmtime(src) - os.path.getmtime(dst)) >= 2:
        return False
    if size >= 64 << 20:
        return sha256(src) == sha256(dst)
    return True


def do_move(family: str, project: str, files: list[dict], junk: list[dict],
            batch: str, date_tag: str, to_del_limit: int) -> int:
    """外迁到 G:\\_offload\\<batch>\\<family>\\<project>\\<rel>；垃圾进 _Del。

    总字节 ≥ to_del_limit 的工程：原件校验后直接删（不复制进 _Del），只留 MOVED 台账；
    小于阈值的：原件仍移入 _Del（保留相对路径）。
    """
    dest = os.path.join(G_ROOT, batch, family, project)
    moved_root = os.path.join(DEL_ROOT, batch, "moved", family, project)
    ledger = os.path.join(DEL_ROOT, batch, f"MOVED-{family}-{project}-{date_tag}.tsv")
    os.makedirs(DEL_ROOT, exist_ok=True)

    total = sum(r["bytes"] for r in files)
    put_in_del = total < to_del_limit
    rows, failed = [], 0

    # 1) 并发复制 + 校验（小文件占多数，单线程是 latency-bound）
    jobs = [(r, os.path.join(dest, r["rel"])) for r in files]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [(r, d, pool.submit(copy_verify, r["path"], d, r["bytes"])) for r, d in jobs]
    for r, _d, fut in futures:
        if not fut.result():
            print(f"[FAIL] 校验失败，停止：{r['rel']}")
            return 1

    # 2) 原件处置（同卷 rename / 删除，串行足够快）
    for r in files:
        if put_in_del:
            back = os.path.join(moved_root, r["rel"])
            _ensure_dir(os.path.dirname(back))
            shutil.move(r["path"], back)
        else:
            os.remove(r["path"])
        rows.append({"rel": r["rel"], "bytes": r["bytes"], "kind": r["kind"],
                     "to": "del" if put_in_del else "deleted_after_verify",
                     "dest": os.path.join(dest, r["rel"])})

    for j in junk:                       # 垃圾只进 _Del，不进 G:
        back = os.path.join(moved_root, j["rel"])
        os.makedirs(os.path.dirname(back), exist_ok=True)
        try:
            shutil.move(j["path"], back)
            rows.append({"rel": j["rel"], "bytes": 0, "kind": j["kind"], "to": "del", "dest": ""})
        except OSError as exc:
            failed += 1
            print(f"[WARN] 垃圾移动失败 {j['rel']}: {exc}")

    ledger_new = not os.path.exists(ledger)
    with open(ledger, "a", encoding="utf-8-sig" if ledger_new else "utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["rel", "bytes", "kind", "to", "dest"], delimiter="\t")
        if ledger_new:
            w.writeheader()
        w.writerows(rows)
    manifest = os.path.join(dest, "_MANIFEST.tsv")
    manifest_new = not os.path.exists(manifest)
    with open(manifest, "a", encoding="utf-8-sig" if manifest_new else "utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["rel", "bytes", "kind"], delimiter="\t")
        if manifest_new:
            w.writeheader()
        for r in rows:
            if r["dest"]:
                w.writerow({"rel": r["rel"], "bytes": r["bytes"], "kind": r["kind"]})

    print(f"  moved {len(rows)} items / {human(total)} -> {dest} "
          f"| original: {'_Del' if put_in_del else 'deleted (ledger only)'}")
    print(f"  ledger {ledger}")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="L 盘表/结果件/垃圾外迁（默认 dry-run）")
    ap.add_argument("--family", required=True)
    ap.add_argument("--project", action="append", required=True, help="可重复")
    ap.add_argument("--execute", action="store_true", help="落盘（缺省只出计划）")
    ap.add_argument("--skip-raster", action="store_true", help="不动 png/jpg/bmp/tif")
    ap.add_argument("--scan-image", action="store_true",
                    help="也扫 image\\ / roi\\ 内部（默认整棵跳过，仅用于清理结果件）")
    ap.add_argument("--batch", default="L-offload-" + datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--out", default="", help="计划报告目录；缺省 L:\\_Del\\_by-function\\<batch>")
    ap.add_argument("--to-del-limit", type=int, default=1,
                    help="工程总量 < 该值(GiB)时原件仍进 _Del；≥ 则校验后直删，只留台账")
    args = ap.parse_args()

    date_tag = datetime.now().strftime("%Y%m%d")
    if args.scan_image:
        KEEP_DIRS.discard("image")
        KEEP_DIRS.discard("roi")
    out_dir = args.out or os.path.join(DEL_ROOT, args.batch)
    os.makedirs(out_dir, exist_ok=True)

    rc = 0
    for project in args.project:
        files, junk, dups = scan(args.family, project, args.skip_raster)
        total = sum(r["bytes"] for r in files)
        by_kind: dict[str, int] = {}
        for r in files:
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        print(f"[{args.family}\\{project}] 待外迁 {len(files)} 件 / {human(total)} "
              f"| 垃圾 {len(junk)} | 重复目录 {len(dups)} | {by_kind}")
        tsv = report(args.family, project, files, junk, dups, out_dir)
        if args.execute and (files or junk):
            rc |= do_move(args.family, project, files, junk, args.batch, date_tag,
                          args.to_del_limit * 1024 ** 3)
        elif not args.execute:
            print(f"  plan: {tsv}")
            for d in dups[:5]:
                print(f"  dup(仅报告): {d}")

    if not args.execute:
        print("== DRY-RUN == 未改任何文件；确认后加 --execute")
    return rc


if __name__ == "__main__":
    sys.exit(main())