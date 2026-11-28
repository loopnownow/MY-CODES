"""带断点续传与大小校验的文件下载器（仅用于 SkinVL-MM 部署）。

网络开关默认关闭：必须显式设环境变量
    $env:SKINVL_ALLOW_DOWNLOAD = "1"
否则脚本只打印计划、不发请求。

用法：
    python download_files.py --list            # 只看计划
    python download_files.py --task skinvl     # 下 3 个权重分片
    python download_files.py --task clip       # 下 CLIP 视觉塔
    python download_files.py --url URL --dest PATH
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests

ALLOW_NETWORK = os.environ.get("SKINVL_ALLOW_DOWNLOAD") == "1"

HF_MIRROR = os.environ.get("SKINVL_HF_MIRROR", "https://hf-mirror.com")
MODEL_ROOT = Path(os.environ.get("MODEL_ROOT", ""))   # 权重统一落 E:\_models（SkinVL-MM / clip-vit-large-patch14-336）

CLIP_FILES = [
    "pytorch_model.bin",
    "config.json",
    "preprocessor_config.json",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "tokenizer.json",
]

TASKS: dict[str, list[tuple[str, Path]]] = {
    "skinvl": [
        (
            f"{HF_MIRROR}/zwq803/SkinVL-MM/resolve/main/model-0000{i}-of-00003.safetensors",
            MODEL_ROOT / "SkinVL-MM" / f"model-0000{i}-of-00003.safetensors",
        )
        for i in (1, 2, 3)
    ],
    "clip": [
        (
            f"{HF_MIRROR}/openai/clip-vit-large-patch14-336/resolve/main/{name}",
            MODEL_ROOT / "clip-vit-large-patch14-336" / name,
        )
        for name in CLIP_FILES
    ],
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "skinvl-deploy/1.0"})


def remote_size(url: str) -> int | None:
    """先 HEAD 取大小；镜像不支持 HEAD 时退回 Range:0-0。"""
    try:
        r = SESSION.head(url, timeout=30, allow_redirects=True)
        if r.ok and r.headers.get("Content-Length"):
            return int(r.headers["Content-Length"])
    except requests.RequestException:
        pass
    try:
        r = SESSION.get(url, headers={"Range": "bytes=0-0"}, timeout=30, allow_redirects=True, stream=True)
        cr = r.headers.get("Content-Range", "")
        if "/" in cr:
            return int(cr.split("/")[-1])
        if r.ok and r.headers.get("Content-Length") and r.status_code == 200:
            return int(r.headers["Content-Length"])
    except requests.RequestException:
        pass
    return None


def download_one(url: str, dest: Path, retries: int = 30) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = remote_size(url)
    print(f"[head] {dest.name}: remote={total if total else 'unknown'} bytes", flush=True)

    for attempt in range(1, retries + 1):
        pos = dest.stat().st_size if dest.is_file() else 0
        if total and pos >= total:
            print(f"[skip] {dest.name} already complete ({pos} bytes)", flush=True)
            return True
        headers = {"Range": f"bytes={pos}-"} if pos else {}
        try:
            with SESSION.get(url, headers=headers, stream=True, timeout=60, allow_redirects=True) as r:
                if r.status_code not in (200, 206):
                    raise requests.RequestException(f"HTTP {r.status_code}")
                mode = "ab" if (pos and r.status_code == 206) else "wb"
                if mode == "wb":
                    pos = 0
                written = pos
                t0 = time.time()
                last = 0.0
                with dest.open(mode) as fh:
                    for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        written += len(chunk)
                        now = time.time()
                        if now - last > 5:
                            last = now
                            mb = written / 1024**2
                            rate = mb / max(now - t0, 1e-6)
                            pct = f"{100*written/total:5.1f}%" if total else "  ?  "
                            print(f"  {dest.name} {mb:8.1f} MB {pct} {rate:5.2f} MB/s", flush=True)
                if total is None or written >= total:
                    print(f"[ok] {dest.name} ({written} bytes)", flush=True)
                    return True
                print(f"[warn] short read {written}/{total}, retry {attempt}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[retry {attempt}/{retries}] {dest.name}: {exc!r}", flush=True)
            time.sleep(min(10, 2 * attempt))
    return False


def download_range(url: str, start: int, end: int, part_path: Path, retries: int = 30) -> bool:
    """下载一个字节区间；part 文件已存在则从其中断处续。"""
    expect = end - start + 1
    for attempt in range(1, retries + 1):
        pos = part_path.stat().st_size if part_path.is_file() else 0
        if pos >= expect:
            return True
        headers = {"Range": f"bytes={start + pos}-{end}"}
        try:
            with SESSION.get(url, headers=headers, stream=True, timeout=60, allow_redirects=True) as r:
                if r.status_code not in (200, 206):
                    raise requests.RequestException(f"HTTP {r.status_code}")
                with part_path.open("ab") as fh:
                    for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
                        if chunk:
                            fh.write(chunk)
            if part_path.is_file() and part_path.stat().st_size >= expect:
                return True
        except Exception as exc:  # noqa: BLE001
            print(f"  [seg retry {attempt}] {part_path.name}: {exc!r}", flush=True)
            time.sleep(min(10, 2 * attempt))
    return False


def download_segmented(url: str, dest: Path, workers: int = 4) -> bool:
    from concurrent.futures import ThreadPoolExecutor

    dest.parent.mkdir(parents=True, exist_ok=True)
    total = remote_size(url)
    if not total:
        print(f"[fallback] {dest.name}: unknown size, single stream", flush=True)
        return download_one(url, dest)
    if dest.is_file() and dest.stat().st_size == total:
        print(f"[skip] {dest.name} already complete ({total} bytes)", flush=True)
        return True

    if dest.exists():
        dest.unlink()
    seg = total // workers
    ranges = []
    for i in range(workers):
        s = i * seg
        e = total - 1 if i == workers - 1 else (i + 1) * seg - 1
        ranges.append((s, e, Path(f"{dest}.part{i}")))

    print(f"[start] {dest.name}  {total/1024**2:.1f} MB  x{workers} threads", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(download_range, url, s, e, p): (s, e, p) for s, e, p in ranges}
        last = 0.0
        while any(not f.done() for f in futs):
            time.sleep(2)
            now = time.time()
            if now - last >= 10:
                last = now
                got = sum(p.stat().st_size for _, _, p in ranges if p.is_file())
                print(
                    f"  {dest.name} {got/1024**2:8.1f}/{total/1024**2:.0f} MB "
                    f"{100*got/total:5.1f}% {(got/1024**2)/max(now-t0,1e-6):5.2f} MB/s",
                    flush=True,
                )
        results = [f.result() for f in futs]

    if not all(results):
        print(f"[FAIL] {dest.name}: some segments incomplete", flush=True)
        return False

    with dest.open("wb") as out:
        for _, _, p in ranges:
            with p.open("rb") as fh:
                while True:
                    chunk = fh.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)

    ok = dest.stat().st_size == total
    if ok:
        for _, _, p in ranges:
            p.unlink(missing_ok=True)
        print(f"[ok] {dest.name} ({total} bytes) in {(time.time()-t0)/60:.1f} min", flush=True)
    else:
        print(f"[FAIL] {dest.name}: merged size {dest.stat().st_size} != {total}", flush=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=sorted(TASKS), default=None)
    ap.add_argument("--url", default=None)
    ap.add_argument("--dest", default=None)
    ap.add_argument("--only", default=None, help="只下某个文件名（同 task 内）")
    ap.add_argument("--workers", type=int, default=4, help="分段并发数；1 表示单流")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.url and args.dest:
        jobs = [(args.url, Path(args.dest))]
    elif args.task:
        jobs = TASKS[args.task]
    else:
        jobs = [j for v in TASKS.values() for j in v]

    if args.only:
        jobs = [j for j in jobs if Path(j[1]).name == args.only]

    if args.list or not ALLOW_NETWORK:
        print("计划下载：")
        for url, dest in jobs:
            print(f"  {url}\n    -> {dest}")
        if not ALLOW_NETWORK:
            print("\n网络开关未打开。设 SKINVL_ALLOW_DOWNLOAD=1 后重跑。")
        return 0

    ok = 0
    for url, dest in jobs:
        good = (
            download_segmented(url, dest, args.workers)
            if args.workers > 1
            else download_one(url, dest)
        )
        if good:
            ok += 1
        else:
            print(f"[FAIL] {dest}", flush=True)
    print(f"完成 {ok}/{len(jobs)}")
    return 0 if ok == len(jobs) else 1


if __name__ == "__main__":
    sys.exit(main())

