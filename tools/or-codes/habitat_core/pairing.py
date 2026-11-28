# -*- coding: utf-8 -*-
"""pre/post 配对表解析（从 delta_habitat_pipeline/data_loader.py 并入）。

用途
----
多参数融合流程按"序列"组织数据（load_patient_data），而 delta 流程按
"同一患者的 pre/post 两次检查"组织。本模块只负责后者，与序列加载互不影响。

数据表要求
----------
必须含 image_path / roi_path / name_dcm 三列（兼容 folder_path / nii_path /
img_path / name）。若有 exam 列则按 exam 判定时间点（1=pre，2=post）；
没有则按路径末级目录名判定（以 "1" 开头为 pre、"2" 开头为 post）。

与源实现的差异
--------------
1. 去掉 diagnose 参数（原版用于打印 ANTs 元数据，属调试件）；
2. 其余解析规则、重名去重（_2/_3）、按 ID 排序、路径存在性校验保持原样。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _clean_path(raw: str) -> str:
    r"""去掉路径末尾的中文/半角括号注释，如 `E:\img\1（治疗前）`。"""
    return re.sub(r'[（(][^）)]*[）)]', '', raw).strip()


def _normalize_name(name_dcm: str) -> Optional[str]:
    """DCM 姓名规范化为小写下划线：'LU SHAO XIN' → 'lu_shao_xin'。"""
    if not name_dcm:
        return None
    cleaned = name_dcm.strip().lower()
    if not cleaned:
        return None
    return re.sub(r'\s+', '_', cleaned)


def _extract_timepoint(image_path: str) -> Optional[str]:
    """按路径末级目录名判断时间点：以 1 开头 → pre；以 2 开头 → post。"""
    last_dir = Path(_clean_path(image_path)).name
    if last_dir.startswith('1'):
        return 'pre'
    if last_dir.startswith('2'):
        return 'post'
    logger.warning("无法判断时间点，末尾目录名：'%s'，路径：%s", last_dir, image_path)
    return None
def load_patient_pairs(csv_path: str, validate_paths: bool = True) -> list[dict]:
    """解析配对表，返回 [{patient_id, pre:{image_path,roi_path}, post:{...}}]。

    解析步骤
    --------
    1. 读 CSV（sep=None 自动识别，先 UTF-8-BOM 再回退 GBK）
    2. 列名归一（folder_path/nii_path/img_path/name → image_path/roi_path/name_dcm）
    3. 时间点：有 exam 列按 exam；否则按路径末级目录名
    4. 按规范化姓名分组，要求每组恰好 1 条 pre + 1 条 post
    5. 重名追加 _2/_3；按 patient_id 升序；validate_paths 时校验四个路径存在

    与源实现一致；区别仅为去掉 diagnose。
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError("数据表不存在：%s" % csv_path)

    try:
        df = pd.read_csv(csv_path, sep=None, engine='python',
                         encoding='utf-8-sig', dtype=str, keep_default_na=False)
    except Exception:
        df = pd.read_csv(csv_path, sep=None, engine='python',
                         encoding='gbk', dtype=str, keep_default_na=False)

    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={
        'folder_path': 'image_path',
        'nii_path':    'roi_path',
        'img_path':    'image_path',
        'name':        'name_dcm',
    })

    required = {'image_path', 'roi_path', 'name_dcm'}
    if not required.issubset(df.columns):
        raise ValueError("CSV 必须包含列：%s，实际列：%s"
                         % (sorted(required), sorted(set(df.columns))))

    use_exam_col = 'exam' in df.columns
    df = df.dropna(subset=['image_path', 'roi_path'])
    df = df[df['image_path'].str.strip() != '']

    records = []
    for _, row in df.iterrows():
        raw_img = row['image_path'].strip()
        raw_roi = row['roi_path'].strip()
        name = _normalize_name(row['name_dcm'])

        if use_exam_col:
            exam_val = str(row['exam']).strip()
            if exam_val == '1':
                timepoint = 'pre'
            elif exam_val == '2':
                timepoint = 'post'
            else:
                logger.warning("无法判断时间点，exam='%s'，路径：%s", exam_val, raw_img)
                timepoint = None
        else:
            timepoint = _extract_timepoint(raw_img)

        if name is None or timepoint is None:
            logger.warning("跳过无法解析的行（缺少 name_dcm 或时间点）：%s", raw_img)
            continue

        records.append({
            'group_key':  name,
            'name':       name,
            'timepoint':  timepoint,
            'image_path': _clean_path(raw_img),
            'roi_path':   _clean_path(raw_roi),
        })

    if not records:
        raise ValueError("CSV 中没有可解析的有效行，请检查 name_dcm 列与路径结尾（\\1 / \\2）。")

    record_df = pd.DataFrame(records)
    patient_pairs, skipped = [], []
    used_names = {}

    for _, group in record_df.groupby('group_key'):
        pre_rows = group[group['timepoint'] == 'pre']
        post_rows = group[group['timepoint'] == 'post']
        name = group.iloc[0]['name']

        if len(pre_rows) != 1 or len(post_rows) != 1:
            logger.warning("患者 %s：pre=%d 条，post=%d 条 → 跳过（需各 1 条）",
                           name, len(pre_rows), len(post_rows))
            skipped.append(name)
            continue

        used_names[name] = used_names.get(name, 0) + 1
        patient_id = name if used_names[name] == 1 else "%s_%d" % (name, used_names[name])

        pair = {
            'patient_id': str(patient_id),
            'pre':  {'image_path': pre_rows.iloc[0]['image_path'],
                     'roi_path':   pre_rows.iloc[0]['roi_path']},
            'post': {'image_path': post_rows.iloc[0]['image_path'],
                     'roi_path':   post_rows.iloc[0]['roi_path']},
        }

        if validate_paths:
            missing = []
            for tp in ('pre', 'post'):
                if not os.path.exists(pair[tp]['image_path']):
                    missing.append("%s image: %s" % (tp, pair[tp]['image_path']))
                if not os.path.exists(pair[tp]['roi_path']):
                    missing.append("%s roi:   %s" % (tp, pair[tp]['roi_path']))
            if missing:
                logger.warning("患者 %s 路径不存在，已跳过：%s", patient_id, '; '.join(missing))
                skipped.append(patient_id)
                continue

        patient_pairs.append(pair)

    patient_pairs.sort(key=lambda x: x['patient_id'])
    logger.info("数据表解析完成：%d 对有效患者，%d 对跳过（%s）",
                len(patient_pairs), len(skipped), skipped)
    return patient_pairs