# -*- coding: utf-8 -*-
"""cleanup_core.py — 按 Patient_ID + 关键词清理 checkpoints（共享实现）。

工程侧薄壳：tools\\cleanup\\<proj>\\delete_files_by_keyword.py
（server.py 按 tools/<组>/<工程>/ 扫描控制台动作，故每工程保留薄壳，实现只此一份。）

配置读工程 settings.ini 的 [tools] 段（缺键报错，不静默回退）：
  CLEANUP_TARGET_DIR  目标目录
  CLEANUP_ERR_CSV     含 Patient_ID 列的报错 CSV
  CLEANUP_KEYWORDS    关键词列表；缺省 ["aligned", "habitat", "features"]
                      （yt_RDL 用 ["habitat"]，由各自 settings.ini 控制）

安全：
  * 默认 dry-run；只有 --execute 才真删。
  * CSV 无有效 ID 时，真实删除模式直接终止（避免误以为清过）。
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

# ---- 配置来自工程 settings.ini（--ini / --project / 向上查找）----
_CORE = os.environ.get("HABITAT_CORE_ROOT") or r"{or_codes}"
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from habitat_core import tool_ini as _tk  # noqa: E402

DEFAULT_KEYWORDS = ["aligned", "habitat", "features"]


def load_patient_ids(csv_path):
    """从 CSV 加载 Patient_ID 列表并去重。"""
    if not csv_path or not os.path.exists(csv_path):
        print(f"警告: 报错记录文件 '{csv_path}' 不存在。")
        return []
    patient_ids = []
    try:
        # utf-8-sig 自动剥离 BOM，否则首列表头会变成 '\ufeffPatient_ID'。
        with open(csv_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pid = row.get("Patient_ID")
                if pid:
                    patient_ids.append(pid.strip())
    except Exception as e:
        print(f"错误: 无法读取 CSV 文件: {e}")
    unique_ids = list(set(patient_ids))
    print(f"成功从 CSV 加载了 {len(unique_ids)} 个报错患者 ID。")
    return unique_ids


def delete_files_with_keywords(directory, keywords, patient_ids=None, dry_run=True):
    """双重过滤删除：文件名须含任一关键词，且（给了 ID 时）须含任一 ID。

    patient_ids 必须用 is not None 判断：空列表 [] 表示仍需过滤但无 ID 可匹配，
    any([]) 恒 False，不匹配任何文件（安全兜底）；若写成 `if patient_ids:`，
    空列表会被当作跳过过滤，退化为仅按关键词删除（危险）。
    """
    if not os.path.exists(directory):
        print(f"错误: 目录 '{directory}' 不存在。")
        return
    if isinstance(keywords, str):
        keywords = (keywords,)
    keywords = [k for k in keywords if k]
    if not keywords:
        print("错误: 未指定关键词，操作已取消。")
        return

    mode_str = "模拟" if dry_run else "实际"
    print(f"【{mode_str}运行模式】")
    print(f"目标目录: {directory}")
    print(f"关键词过滤: {keywords}")
    if patient_ids:
        print(f"患者 ID 过滤: {len(patient_ids)} 个 ID 待匹配")
    print("-" * 40)

    count = 0
    for filename in os.listdir(directory):
        file_path = os.path.join(directory, filename)
        if not os.path.isfile(file_path):
            continue
        keyword_match = any(k in filename for k in keywords)
        patient_match = True
        if patient_ids is not None:
            patient_match = any(
                re.search(rf"(?<![A-Za-z0-9]){re.escape(pid)}(?![A-Za-z0-9])", filename)
                for pid in patient_ids
            )
        if keyword_match and patient_match:
            if dry_run:
                print(f"[待删除] {filename}")
            else:
                try:
                    os.remove(file_path)
                    print(f"[已删除] {filename}")
                except Exception as e:
                    print(f"[失败] 无法删除 {filename}: {e}")
            count += 1

    print("-" * 40)
    if dry_run:
        print(f"模拟运行结束。找到 {count} 个匹配文件。")
        if count > 0:
            print("提示: 实际执行请加 --execute（并在 actions.ini 关掉 dry-run）。")
    else:
        print(f"操作完成。共处理 {count} 个文件。")


def _read_tool_cfg():
    """从 ini 取默认目标目录/报错 CSV/关键词（缺键用安全默认）。"""
    target = _tk.get("CLEANUP_TARGET_DIR")
    err_csv = _tk.get("CLEANUP_ERR_CSV")
    kw = _tk.get("CLEANUP_KEYWORDS", list(DEFAULT_KEYWORDS))
    if isinstance(kw, str):
        kw = [kw]
    return target, err_csv, tuple(kw)


def main():
    target_dir, err_csv, default_keywords = _read_tool_cfg()
    parser = argparse.ArgumentParser(description="按关键词 + Patient_ID 清理 checkpoints")
    parser.add_argument("keyword", nargs="*", help="关键词（可选，缺省读 ini CLEANUP_KEYWORDS）")
    parser.add_argument("--dir", default=target_dir, help=f"目标目录（默认 {target_dir}）")
    parser.add_argument("--execute", action="store_true", help="实际删除（覆盖默认 dry-run）")
    parser.add_argument("--no_id", action="store_true", help="忽略 Patient_ID，仅按关键词清理")
    args = parser.parse_args()

    final_keywords = args.keyword if args.keyword else default_keywords
    final_dry_run = not args.execute

    target_patient_ids = None
    if not args.no_id:
        target_patient_ids = load_patient_ids(err_csv)
        if not target_patient_ids:
            print("提示: 未能从 CSV 获取到 Patient_ID，本次不会匹配任何文件（安全兜底）。")
            if not final_dry_run:
                print("错误: 真实删除模式下未获取到有效 Patient_ID 清单，已终止。")
                print("请检查 CSV，或用 --no_id 改为仅按关键词清理。")
                sys.exit(1)

    if not final_dry_run:
        confirm = input(
            f"【警告】即将在真实模式下删除 '{args.dir}' 中匹配的文件，不可逆。"
            f"确认执行请输入 y/yes: "
        ).strip().lower()
        if confirm not in ("y", "yes"):
            print("操作已取消。")
            sys.exit(0)

    delete_files_with_keywords(
        directory=args.dir,
        keywords=final_keywords,
        patient_ids=target_patient_ids,
        dry_run=final_dry_run,
    )


if __name__ == "__main__":
    main()
