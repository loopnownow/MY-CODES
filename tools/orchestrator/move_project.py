#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""move_project.py -- 在 GROK_ROOT 的阶段夹之间搬课题，并同步控制台注册表。

为什么需要它
    控制台注册表（0doc/console.projects.json）存的是**绝对目录**；
    手动 Move-Item 后 `entry.dir_ok()` 变 False，控制台 API 直接 403
    "registration stale"。本工具把「搬夹 + 重锚注册表 + 自检」做成一步。

配置（脚本顶部 CONFIG 块）
    STAGE_ORDER      允许的阶段夹（与 modules/console/paths.py 的 STAGE_ROOT_NAMES 对齐）
    GROK_ROOT        课题根，默认取 modules.console.const.GROK_ROOT（$env:GROK_ROOT 可覆盖）

用法
    python move_project.py --project lya_CTA --to pre-submit            # 干跑，只打印计划
    python move_project.py --project lya_CTA --to pre-submit --execute  # 真搬并重锚注册表
    python move_project.py --project lya_CTA --to pre-submit --execute --register-new
        未登记的项目顺手登记（新 token 写进 <project>.html 的 meta）

行为
    * 干跑默认；`--execute` 才落盘。禁止覆盖：目标已存在即报错退出。
    * 只搬整个项目夹，不拆 ref/tmp，不删任何文件。
    * 搬前做占用预检（逐文件试写打开）；有占用即中止，源夹不动。
    * 只用 `os.rename` 同级改名；失败就报错退出，**不**退化成复制+删源。
    * 自检失败（目录/文件数/注册表/starter 页）→ exit 1，不静默通过。

退出码
    0 成功  1 校验失败  2 参数或路径错误
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

CONFIG = {
    # Canonical stage order (t1817u: `in-review` -> `reviewing`). Writers only
    # create canonical folders; legacy `in-review` is accepted on input.
    "STAGE_ORDER": ("drafting", "pre-submit", "reviewing", "accepted"),
}
STAGE_ORDER = CONFIG["STAGE_ORDER"]

_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from modules.console import const as console_const  # noqa: E402
from modules.console import registry  # noqa: E402
from modules.console.paths import (  # noqa: E402
    LEGACY_STAGE_ALIASES,
    normalize_stage_name,
    stage_root_dirs,
)

GROK_ROOT = Path(console_const.GROK_ROOT)
META_TOKEN_RE = r'(<meta\s+name="console-token"\s+content=")([^"]*)(")'


def _die(msg: str, code: int = 2) -> None:
    print(f"ERROR {msg}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def _count_files(root: Path) -> tuple[int, int]:
    files = [p for p in root.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def _locked_files(root: Path) -> list[str]:
    """占用预检：能被写打开的文件视为未占用；只读属性的文件不参与判定。"""
    locked: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file() or not os.access(p, os.W_OK):
            continue
        try:
            with open(p, "rb+"):
                pass
        except OSError as e:
            locked.append(f"{p.relative_to(root)}: {e.strerror or e}")
    return locked


def _find_source(project: str, src_stage: str) -> tuple[str, Path]:
    if src_stage:
        stage = normalize_stage_name(src_stage)
        roots = [(stage, GROK_ROOT / stage)]
    else:
        # canonical stages + legacy in-review dirs still on disk (read compat, t1817u)
        roots = [(p.name, p) for p in stage_root_dirs(GROK_ROOT, include_drafting=True)]
    hits = [(s, root / project) for s, root in roots]
    hits = [(s, p) for s, p in hits if p.is_dir()]
    if not hits:
        _die(f"找不到项目夹：{project}（已查 {', '.join(s for s, _ in roots)}）")
    if len(hits) > 1:
        _die(f"项目夹重复，先定归属：{', '.join(str(p) for _, p in hits)}")
    return hits[0]


def _starter_has_token(page: Path) -> str:
    """返回 starter 页 meta token 状态：token / placeholder / missing。"""
    if not page.is_file():
        return "missing"
    text = page.read_text(encoding="utf-8")
    m = re.search(META_TOKEN_RE, text)
    if not m:
        return "missing"
    return "placeholder" if m.group(2) in ("", "__CONSOLE_TOKEN__") else "token"



def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="在 GROK_ROOT 阶段夹之间搬课题并重锚控制台注册表（干跑默认）",
    )
    ap.add_argument("--project", required=True, help="课题夹名，如 lya_CTA")
    ap.add_argument("--to", required=True, choices=list(STAGE_ORDER), help="目标阶段夹")
    ap.add_argument("--from", dest="src_stage", default="",
                    choices=("", *STAGE_ORDER, *LEGACY_STAGE_ALIASES),
                    help="源阶段夹（可写旧名 in-review）；空=自动在阶段夹里找唯一一个")
    ap.add_argument("--execute", action="store_true", help="真搬（默认只干跑）")
    ap.add_argument("--register-new", action="store_true",
                    help="未登记的项目顺手登记并写 token 到起始页")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = ap.parse_args(argv)

    project = args.project.strip()
    if not project:
        _die("未指定 --project")
    if args.src_stage and args.src_stage == args.to:
        _die("--from 与 --to 相同，无需搬")

    dst = GROK_ROOT / args.to / project
    src_stage, src = _find_source(project, args.src_stage)
    if dst.exists():
        _die(f"目标已存在，拒绝覆盖：{dst}")

    n_src, bytes_src = _count_files(src)
    entry = registry.get(project)
    plan = {
        "project": project,
        "from": str(src),
        "from_stage": src_stage,
        "to": str(dst),
        "to_stage": args.to,
        "files": n_src,
        "bytes": bytes_src,
        "registry": "absent" if entry is None else ("ok" if entry.dir_ok() else "STALE"),
        "registry_dir": "" if entry is None else entry.dir,
        "starter": _starter_has_token(src / f"{project}.html"),
        "execute": bool(args.execute),
    }

    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, indent=2) if args.json else
              "\n".join([
                  f"[dry-run] {project}: {src_stage} -> {args.to}",
                  f"  {src}",
                  f"  -> {dst}",
                  f"  文件 {n_src} 个 / {bytes_src / 1024 / 1024:.1f} MB",
                  f"  注册表 {plan['registry']}  起始页 token {plan['starter']}",
                  "  加 --execute 才落盘",
              ]), flush=True)
        return 0

    dst.parent.mkdir(parents=True, exist_ok=True)
    locked = _locked_files(src)
    if locked:
        for line in locked[:10]:
            print(f"  LOCKED {line}", file=sys.stderr, flush=True)
        _die(f"{project} 有 {len(locked)} 个文件被占用；先关掉控制台/Excel/Word 再重试（源夹未动）", 1)
    try:
        os.rename(str(src), str(dst))
    except OSError as e:
        _die(f"rename 失败（不做复制删源回退）：{e}；源夹未动", 1)
    print(f"moved {src} -> {dst}", flush=True)

    page = dst / f"{project}.html"
    action = "re-anchored"
    if entry is None:
        if not args.register_new:
            _die(f"{project} 未登记控制台，且未给 --register-new；目录已搬，注册需另行处理", 1)
        entry, token = registry.register(
            project, dst, rotate=True, starter_html=str(page) if page.is_file() else "",
        )
        if token and page.is_file():
            registry.inject_token(page, token)
            action = "registered+token"
        else:
            action = "registered"
    else:
        registry.register(
            project, dst, scopes=entry.scopes, rotate=False,
            starter_html=str(page) if page.is_file() else "",
        )

    after = registry.get(project)
    checks = {
        "dst_exists": dst.is_dir(),
        "src_gone": not src.exists(),
        "files_match": _count_files(dst)[0] == n_src,
        "registry_dir_ok": bool(after and after.dir_ok()),
        "registry_points_here": bool(
            after and registry.dir_key(after.dir) == registry.dir_key(dst)
        ),
        "starter_token": _starter_has_token(page),
    }
    report = {**plan, "action": action, "checks": checks,
              "registry_dir_after": "" if after is None else after.dir}
    ok = checks["starter_token"] == "token" and all(
        v for k, v in checks.items() if k != "starter_token"
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    else:
        for k, v in checks.items():
            print(f"  {'OK  ' if v else 'FAIL'} {k}={v}", flush=True)
        print(f"  registry {report['registry_dir_after']}  ({action})", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
