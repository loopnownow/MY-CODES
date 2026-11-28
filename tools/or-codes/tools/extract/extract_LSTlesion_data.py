# -*- coding: utf-8 -*-
"""extract_LSTlesion_data.py — 递归扫描 LST 报告 HTML，提取病灶体积/数目为 CSV。

综合自两版同源脚本（取更通用者，根目录参数化）：
  * tools\\brain_nii_ZJL\\Flair\\extract_lesion_data.py（单层目录，固定 html 名，3 列）
  * tools\\HE_nii_ly\\utili\\extract_LSTlesion_data.py（递归 walk，通配 report*.html，4 列）

用法：
  python extract_LSTlesion_data.py --root L:\\SSD\\SSD_dyf\\Flair [--out lesion_summary.csv]
"""
from __future__ import annotations

import argparse
import csv
import os
import re

VOL_RE = re.compile(r'<td>Lesion volume</td>\s*<td class="ta_right">([\d.]+)\s*ml</td>')
NUM_RE = re.compile(r'<td>Number of lesions</td>\s*<td class="ta_right">(\d+)</td>')


def extract(root: str, out_csv: str) -> None:
    rows = []
    total = 0
    print(f"正在搜索 {os.path.abspath(root)} 下所有 report*.html ...")
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if not (filename.startswith("report") and filename.endswith(".html")):
                continue
            total += 1
            html_path = os.path.join(dirpath, filename)
            folder_name = os.path.basename(dirpath)
            volume = lesions = ""
            try:
                with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                m_vol = VOL_RE.search(content)
                if m_vol:
                    volume = m_vol.group(1)
                m_num = NUM_RE.search(content)
                if m_num:
                    lesions = m_num.group(1)
            except Exception as e:  # noqa: BLE001
                print(f"读取 {html_path} 出错: {e}")
            rows.append([os.path.abspath(html_path), folder_name, volume, lesions])

    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["文件路径", "文件夹名", "lesion_volume_ml", "number_of_lesions"])
        w.writerows(rows)
    print(f"完成。共找到 {total} 个报告 → {os.path.abspath(out_csv)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="从 LST 报告 HTML 提取病灶体积/数目")
    ap.add_argument("--root", required=True, help="扫描根目录")
    ap.add_argument("--out", default="lesion_summary.csv", help="输出 CSV（默认 lesion_summary.csv）")
    args = ap.parse_args()
    if not os.path.isdir(args.root):
        raise SystemExit(f"根目录不存在: {args.root}")
    extract(args.root, args.out)


if __name__ == "__main__":
    main()
