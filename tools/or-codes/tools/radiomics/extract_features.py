# -*- coding: utf-8 -*-
"""extract_features.py — pyradiomics 影像组学特征提取（通用；变体由 ini/CLI 控制）。

原 9 份散脚本（EC_ybc 的 processZ1/Z2、ECtxt\\ICC 的 process/meta-radiomics2、OV_zyp 的 zyp、
RDL_yt 的 yt、MRS\\PCOS_zar 的 ovov/OV-his -new、lung_xlm 的 lung-meta-radiomics）差异只在：
image 发现方式、ROI 定位规则、Params.yaml、扫描根、输出格式。本脚本把这些做成配置项。

ROI 规则（--roi-rule，四类）：
  sibling_suffix:<sfx>     同目录，image 换成 <sfx>（如 Z1.nrrd / Z2.nrrd / Z.nrrd）
  sibling_strip:<tok>      同目录，image 名去掉 <tok>（如 .Merge）
  sibling_replace:<a>:<b>  同目录，image 名把 <a> 换成 <b>（original:ROI、seg:image）
  roi_dir:<dir>[:<sep>]    独立 ROI 目录取同名；给 <sep> 时取文件名按分隔符第 <sep> 段

image 发现（--image，可多次；缺省 *.nii）：递归 rglob 每个模式。

用法示例（对应当年各散脚本）：
  # EC processZ1：每子目录 T1/T2/DWI0/DWI1/C/ADC.nrrd 配 Z1.nrrd
  python extract_features.py --root <EC目录> --params Params.yaml \
      --image T1.nrrd --image T2.nrrd --image DWI0.nrrd --image DWI1.nrrd \
      --image C.nrrd --image ADC.nrrd --roi-rule sibling_suffix:Z1.nrrd --out result.xlsx
  # RDL yt：
  python extract_features.py --root E:\\ADATA\\yt --params E:\\ADATA\\Params.yaml \
      --image *.nii --roi-rule sibling_replace:original:ROI --out result.xlsx
  # OV-his（ROI 目录内按空格第 2 段匹配）
  python extract_features.py --root . --params his.yaml --image *.nrrd \
      --roi-rule "roi_dir:./ROI:1" --out result.csv --format csv
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path

import pandas as pd


def resolve_roi(image: Path, rule: str):
    """按 roi-rule 由 image 推 ROI 路径；找不到返回 None。"""
    if rule.startswith("sibling_suffix:"):
        suffix = rule.split(":", 1)[1]
        cand = image.with_name(suffix)
        return cand if cand.is_file() else None

    if rule.startswith("sibling_strip:"):
        token = rule.split(":", 1)[1]
        cand = image.with_name(image.name.replace(token, ""))
        return cand if cand.is_file() else None

    if rule.startswith("sibling_replace:"):
        _, a, b = rule.split(":", 2)
        cand = image.with_name(image.name.replace(a, b))
        return cand if cand.is_file() else None

    if rule.startswith("roi_dir:"):
        parts = rule.split(":")
        roi_dir = Path(parts[1])
        if not roi_dir.is_absolute():
            roi_dir = image.parent / roi_dir
        if len(parts) >= 3:
            idx = int(parts[2])
            tok = image.name.split()[idx] if len(image.name.split()) > idx else image.name
            cand = roi_dir / tok
        else:
            cand = roi_dir / image.name
        return cand if cand.is_file() else None

    raise SystemExit(f"未知 ROI 规则: {rule}")


def collect_pairs(root: Path, image_globs, rule: str, excludes=()):
    """返回 (pairs, skipped)。pairs=[(image, roi, label)]；excludes 为文件名 glob 黑名单。"""
    images = []
    for pat in image_globs:
        images.extend(sorted(root.rglob(pat)))
    seen, uniq = set(), []
    for p in images:
        if p in seen:
            continue
        seen.add(p)
        if any(fnmatch.fnmatch(p.name, ex) for ex in excludes):
            continue
        uniq.append(p)
    pairs, skipped = [], []
    for img in uniq:
        roi = resolve_roi(img, rule)
        if roi is None:
            skipped.append(str(img))
            continue
        pairs.append((img, roi, img.parent.name or img.stem))
    return pairs, skipped


def main():
    ap = argparse.ArgumentParser(description="pyradiomics 影像组学特征提取（通用）")
    ap.add_argument("--root", required=True, help="扫描根目录")
    ap.add_argument("--params", required=True, help="pyradiomics Params.yaml")
    ap.add_argument("--image", action="append", default=[], help="image glob，可多次；缺省 *.nii")
    ap.add_argument("--exclude", action="append", default=[], help="排除的文件名 glob，可多次（如 *.Merge.nii）")
    ap.add_argument("--roi-rule", required=True, help="ROI 定位规则（见模块 docstring）")
    ap.add_argument("--out", default="result.xlsx", help="输出文件（.xlsx/.csv）")
    ap.add_argument("--format", choices=["xlsx", "csv"], default=None, help="输出格式（缺省按后缀）")
    ap.add_argument("--dry-run", action="store_true", help="只统计配对，不提取")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        sys.exit(f"根目录不存在: {root}")
    if not Path(args.params).is_file():
        sys.exit(f"Params.yaml 不存在: {args.params}")

    image_globs = args.image or ["*.nii"]
    pairs, skipped = collect_pairs(root, image_globs, args.roi_rule, args.exclude)
    print(f"image 模式: {image_globs}")
    print(f"ROI 规则: {args.roi_rule}")
    print(f"有效配对: {len(pairs)}  |  未找到 ROI: {len(skipped)}")
    for s in skipped[:20]:
        print(f"  [SKIP] {s}")

    if args.dry_run:
        print("[dry-run] 不提取，仅统计配对。")
        return
    if not pairs:
        sys.exit("无有效配对，退出。")

    import radiomics.featureextractor as FEE
    extractor = FEE.RadiomicsFeatureExtractor(str(args.params))

    names, rows, errors = [], [], []
    for i, (img, roi, label) in enumerate(pairs, 1):
        try:
            rows.append(extractor.execute(str(img), str(roi)))
            names.append(label)
            if i % 20 == 0 or i == len(pairs):
                print(f"  [{i}/{len(pairs)}]")
        except Exception as e:  # noqa: BLE001
            errors.append({"image": str(img), "roi": str(roi), "error": str(e)})
            print(f"  [ERR] {img}: {e}")

    if rows:
        df = pd.DataFrame(rows, index=names)
        out = Path(args.out)
        fmt = args.format or ("csv" if out.suffix.lower() == ".csv" else "xlsx")
        out.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "csv":
            df.to_csv(out, index=True)
        else:
            df.to_excel(out, index=True)
        print(f"特征 → {out}  (shape {df.shape})")
    if errors:
        err_path = Path(args.out).with_name(Path(args.out).stem + "_errors.csv")
        pd.DataFrame(errors).to_csv(err_path, index=False)
        print(f"错误 → {err_path}  ({len(errors)} 条)")


if __name__ == "__main__":
    main()

