r"""旧路径 / 旧名称审计：扫描文本件里指向已弃布局的路径，可给出替换建议。

用途（配合 `OR-RULES\01_project-org.md` Q21：改名 / 搬迁后全库无旧路径）：
- 默认只读扫描，输出命中清单（文件 / 行号 / 命中串 / 建议新串）
- `--fix` 用 `--map` 给的 old->new 对照做字面替换，改前把原件备份到 `--backup`

用法
    python audit_legacy_paths.py --root {lab_root}\pre-submit\lxf_LUNG ^
        --pattern 'roi[\\/](BB|JS)[\\/]' --ext .csv,.md,.py,.json,.ini,.yaml,.tsv
    python audit_legacy_paths.py --root ... --map rekey.tsv --fix --backup ...\ref\tmp\...
"""
import argparse
import csv
import os
import re
import shutil
import sys

DEFAULT_EXT = (".csv", ".md", ".py", ".json", ".ini", ".yaml", ".yml", ".tsv", ".txt", ".ps1")
SKIP_DIRS = {".git", "__pycache__", ".ipynb_checkpoints", "_pre-reorg-20261117"}
SKIP_NAMES = ("backup", "_before_", "_before-", "MOVED")  # 快照 / 搬移台账：必须保留旧值


def is_snapshot(path, name):
    if any(k.lower() in name.lower() for k in SKIP_NAMES):
        return True
    return any(k.lower() in part.lower() for part in path.split(os.sep)[-3:] for k in ("backup",))


def iter_files(root, exts, skip_backup):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith("_del")
                  and "backup" not in d.lower() and "_before_" not in d.lower()
                  and not d.lower().startswith("legacy-path-fix")]
        if skip_backup and os.path.abspath(dp).startswith(os.path.abspath(skip_backup)):
            continue
        for f in fns:
            if os.path.splitext(f)[1].lower() in exts and not is_snapshot(dp, f):
                yield os.path.join(dp, f)


def load_map(path):
    """两列 tsv：old<TAB>new。"""
    m = []
    with open(path, encoding="utf-8") as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if len(row) >= 2 and row[0] and not row[0].startswith("#"):
                m.append((row[0], row[1]))
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--pattern", action="append", default=[])
    ap.add_argument("--ext", default=",".join(DEFAULT_EXT))
    ap.add_argument("--map", help="old->new 对照 tsv（两列）")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--backup", help="改前备份根目录")
    ap.add_argument("--out", help="命中清单落点 tsv")
    args = ap.parse_args()
    exts = tuple(e.strip().lower() for e in args.ext.split(",") if e.strip())
    pats = [re.compile(p) for p in args.pattern] or [re.compile(r"roi[\\/](BB|JS)[\\/]")]
    pairs = load_map(args.map) if args.map else []
    hits = []
    for path in iter_files(args.root, exts, args.backup):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        new_text = text
        for old, new in pairs:
            if old in new_text:
                new_text = new_text.replace(old, new)
        matched = [p.pattern for p in pats if p.search(text)]
        if not matched:
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            if any(p.search(line) for p in pats):
                hits.append([os.path.relpath(path, args.root), ln, "|".join(matched), line.strip()[:220]])
        if args.fix and pairs and new_text != text:
            if args.backup:
                dst = os.path.join(args.backup, os.path.relpath(path, os.path.dirname(args.root.rstrip("\\/"))))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if not os.path.exists(dst):
                    shutil.copy2(path, dst)
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(new_text)
    out = args.out or os.path.join(args.root, "LEGACY-PATH-HITS.tsv")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["file", "line", "pattern", "text"])
        w.writerows(hits)
    files = len({h[0] for h in hits})
    print("hits=%d files=%d -> %s%s" % (len(hits), files, out, "（已按 map 替换）" if args.fix else ""))


if __name__ == "__main__":
    main()
