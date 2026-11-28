# -*- coding: utf-8 -*-
"""copy_files.py — 按关键词把一级子目录内的文件复制到另一根下（通用文件复制）。

原为 brain_nii_ZJL / HE_nii_ly 两份同构脚本（正文一致，仅配置区路径与关键词不同），
合并为此一份；路径与关键词改为命令行参数，不再改文件顶部常量。

用法：
  python copy_files.py --src F:\\nii --dst F:\\Flair --keyword tirm
  python copy_files.py --src L:\\HE_dcm_ly\\sch_HE_nii --dst L:\\HE_dcm_ly\\tirm --keyword flair --dry-run

选项：
  --src/--dst      源根 / 目标根（必填）
  --keyword        关键词，可多次；文件名含任一即命中（必填）
  --prefix         允许的文件名前缀，可多次；缺省不限
  --ext            允许的后缀，可多次；缺省不限
  --case-sensitive 大小写敏感（默认不敏感）
  --no-overwrite   目标已存在则跳过（默认覆盖）
  --dry-run        只预览，不复制
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def scan(src_root: Path, keywords, prefixes, exts, case_sensitive):
    """返回 [(source_file, 相对子目录名)]：仅一级子目录，按关键词/前缀/后缀过滤。"""
    ops = []
    if not src_root.is_dir():
        print(f"❌ 错误: 源路径不存在 {src_root}")
        return ops
    subdirs = [d for d in src_root.iterdir() if d.is_dir()]
    print(f"\n 发现 {len(subdirs)} 个一级子文件夹\n")
    for idx, subdir in enumerate(subdirs, 1):
        print(f"[{idx:2d}/{len(subdirs)}] 扫描: {subdir.name}")
        matched = []
        for f in subdir.iterdir():
            if not f.is_file():
                continue
            name = f.name
            check = name if case_sensitive else name.lower()
            kw_ok = any((k if case_sensitive else k.lower()) in check for k in keywords)
            if not kw_ok:
                continue
            if prefixes and not any(check.startswith(p if case_sensitive else p.lower())
                                    for p in prefixes):
                continue
            if exts and not any(check.endswith(e if case_sensitive else e.lower())
                                for e in exts):
                continue
            matched.append(f)
        if matched:
            print(f"     找到 {len(matched)} 个匹配文件")
        for f in matched:
            ops.append((f, subdir.name))
    return ops


def main():
    ap = argparse.ArgumentParser(description="按关键词复制一级子目录内的文件")
    ap.add_argument("--src", help="源根目录")
    ap.add_argument("--dst", help="目标根目录")
    ap.add_argument("--keyword", action="append", default=[], help="关键词，可多次")
    ap.add_argument("--prefix", action="append", default=[], help="允许的前缀，可多次")
    ap.add_argument("--ext", action="append", default=[], help="允许的后缀，可多次")
    ap.add_argument("--case-sensitive", action="store_true")
    ap.add_argument("--no-overwrite", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.src or not args.dst or not args.keyword:
        ap.error("--src / --dst / --keyword 均为必填")

    src_root = Path(args.src)
    dst_root = Path(args.dst)
    print("=" * 70)
    print(" 文件复制工具")
    print("=" * 70)
    print(f" 源路径: {src_root}")
    print(f" 目标路径: {dst_root}")
    print(f" 匹配关键词: {args.keyword}")
    print(f" 运行模式: {'🔍 测试（仅预览）' if args.dry_run else '正式复制'}")
    print("-" * 70)

    ops = scan(src_root, args.keyword, args.prefix, args.ext, args.case_sensitive)
    total = len(ops)
    print("\n" + "-" * 70)
    print(f" 匹配文件总数: {total} 个")
    if total == 0:
        print("⚠️  没有找到任何匹配的文件")
        return
    if args.dry_run:
        for f, _sub in ops:
            print(f"    → {f}")
        print("\n测试完成，未执行任何复制（去掉 --dry-run 正式复制）")
        return

    copied = skipped = errors = 0
    for idx, (src_f, sub) in enumerate(ops, 1):
        dst_file = dst_root / sub / src_f.name
        try:
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            if dst_file.exists() and args.no_overwrite:
                skipped += 1
                continue
            print(f"[{idx:3d}/{total}] 复制: {src_f.name}")
            shutil.copy2(src_f, dst_file)
            copied += 1
        except Exception as e:  # noqa: BLE001
            print(f"      错误: {e}")
            errors += 1

    print("\n" + "=" * 70)
    print(f" 复制完成!  成功 {copied} / 跳过 {skipped} / 错误 {errors}")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n 用户中断")
        sys.exit(130)
