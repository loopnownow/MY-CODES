r"""
data_loader.py
==============
患者配对信息解析模块

功能
----
从 CSV 数据表中解析图像路径、患者 ID 和时间点，
将每位患者的基线（pre）和随访（post）图像配对，
返回结构化的患者配对列表，供主流程（pipeline.py）使用。

数据表格式
----------
逗号或制表符分隔（编码：UTF-8-BOM 或 GBK），必须包含
image_path、roi_path、name_dcm 三列（兼容旧列名 folder_path / nii_path，
以及新版列名 img_path / name）：

    image_path                          roi_path                        name_dcm
    E:\study\img\...\patient_A\1        E:\study\roi\...\patient_a1.nii.gz  patient_A
    E:\study\img\...\patient_A\2        E:\study\roi\...\patient_a2.nii.gz  patient_A

    （combind.csv 格式，含 exam 列，时间点由 exam 列判断）
    roi_path                            img_path                        name         exam
    E:\study\roi\...\patient_B1.nii.gz  E:\study\img\...\studyX\1.nii.gz  patient_B  1
    E:\study\roi\...\patient_B2.nii.gz  E:\study\img\...\studyY\2.nii.gz  patient_B  2

解析规则
--------
    1. 路径末尾的中文括号注释自动去除
    2. 患者标识直接采用 name_dcm（规范化为「小写下划线」）
    3. 时间点判断：若存在 exam 列，用 exam 值判断（1 → pre，2 → post）；
       否则从末尾目录名判断（以 "1" 开头 → pre；以 "2" 开头 → post）
    4. 按规范化姓名分组，每组恰好 1 条 pre + 1 条 post → 有效配对

单独运行示例
------------
    python data_loader.py
    （从 config.DATA_TABLE_PATH 读取，打印解析结果；validate_paths=False）
"""

import re
import os
import logging
import pandas as pd
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  辅助：路径清理
# ═══════════════════════════════════════════════════════════════════

def _clean_path(raw: str) -> str:
    """
    去除路径末尾的中文括号注释。

    支持全角括号（（））和半角括号（()）的混合使用：
        'E:\\img\\1（治疗前）'              → 'E:\\img\\1'
        'E:\\roi\\mask1.nii.gz（治疗前）'   → 'E:\\roi\\mask1.nii.gz'
        'E:\\img\\1(baseline)'              → 'E:\\img\\1'

    Parameters
    ----------
    raw : str — 含注释的原始路径字符串

    Returns
    -------
    str — 清理后的路径字符串（已 strip 首尾空白）
    """
    # 匹配全角/半角括号对及其内容，贪婪匹配括号内的任意字符
    return re.sub(r'[（(][^）)]*[）)]', '', raw).strip()


# ═══════════════════════════════════════════════════════════════════
#  辅助：规范化患者姓名
# ═══════════════════════════════════════════════════════════════════

def _normalize_name(name_dcm: str) -> Optional[str]:

    """
    将 DCM 真实姓名规范化为「小写下划线」命名 key。

        'patient_A'   → 'patient_a'
        '  Li Lian Zhong ' → 'li_lian_zhong'

    规则：去首尾空白 → 转小写 → 连续空白压缩为单个下划线。

    Returns
    -------
    str — 规范化姓名，输入为空时返回 None
    """
    if not name_dcm:
        return None
    cleaned = name_dcm.strip().lower()
    if not cleaned:
        return None
    return re.sub(r'\s+', '_', cleaned)



# ═══════════════════════════════════════════════════════════════════
#  辅助：判断时间点
# ═══════════════════════════════════════════════════════════════════

def _extract_timepoint(image_path: str) -> Optional[str]:
    """
    从图像路径的末尾目录名判断时间点（pre / post）。

    判断规则
    --------
    末尾目录名以 "1" 开头 → 'pre'（基线 / 治疗前）
    末尾目录名以 "2" 开头 → 'post'（随访 / 治疗后）

    注意事项
    --------
    必须先通过 _clean_path() 去除中文注释再取末尾目录名，
    否则 "1（治疗前）" 取得 "1（治疗前）" 而非 "1"，仍能正确判断，
    但若注释中含数字则可能误判。

    Parameters
    ----------
    image_path : str

    Returns
    -------
    str — 'pre' 或 'post'，解析失败时返回 None 并记录警告
    """
    last_dir = Path(_clean_path(image_path)).name   # 取末尾目录名（去注释后）

    if last_dir.startswith('1'):
        return 'pre'
    elif last_dir.startswith('2'):
        return 'post'
    else:
        logger.warning(
            f"无法判断时间点，末尾目录名：'{last_dir}'，路径：{image_path}"
        )
        return None


# ═══════════════════════════════════════════════════════════════════
#  辅助：图像诊断（元数据打印）
# ═══════════════════════════════════════════════════════════════════

def diagnose_image(path: str, label: str = ""):
    """
    打印单个图像 / ROI 文件的 ANTs 元数据诊断信息。

    输出内容
    --------
    Shape / Spacing / Origin / Direction / Min-Max 像素值 / 维度

    适用场景
    --------
    调试预处理前后的空间信息差异，确认文件是否可被 ANTs 正确读取。
    调用 load_patient_pairs(..., diagnose=True) 即可对全队列输出诊断报告。

    Parameters
    ----------
    path  : str — 图像或 ROI 的文件路径
    label : str — 显示标签（如 "P1 PRE Image"）
    """
    import ants
    if not os.path.exists(path):
        print(f"  [错误] 文件不存在：{path}")
        return
    try:
        img = ants.image_read(path)
        print(f"\n  {label}")
        print(f"    路径：     {path}")
        print(f"    Shape：    {img.shape}")
        print(f"    Spacing：  {img.spacing}")
        print(f"    Origin：   {img.origin}")
        print(f"    Direction：\n{img.direction}")
        print(f"    Min/Max：  {img.min():.4f} / {img.max():.4f}")
        print(f"    Dimension：{img.dimension}")
    except Exception as e:
        print(f"  [错误] 读取 {label} 失败：{e}")


# ═══════════════════════════════════════════════════════════════════
#  主函数：解析并配对患者数据
# ═══════════════════════════════════════════════════════════════════

def load_patient_pairs(csv_path:       str,
                        validate_paths: bool = True,
                        diagnose:       bool = False) -> list[dict]:
    """
    解析 CSV 数据表，返回患者配对列表。

    输出格式（每个元素）
    --------------------
    {
        'patient_id' : '1',
        'pre'  : {'image_path': '...', 'roi_path': '...'},
        'post' : {'image_path': '...', 'roi_path': '...'},
    }

    解析步骤
    --------
    1. 读取 CSV（自动检测分隔符，兼容 UTF-8-BOM 和 GBK 编码）
    2. 逐行提取患者 ID、时间点、清理后路径
    3. 按患者 ID 分组，确保每组恰好 1 条 pre + 1 条 post 记录
    4. 验证路径在磁盘上实际存在（validate_paths=True 时）
    5. 按患者 ID 数字排序

    Parameters
    ----------
    csv_path       : str  — CSV 文件路径
    validate_paths : bool — 是否检查路径实际存在（默认 True；调试时可设 False）
    diagnose       : bool — 是否对每位患者打印 ANTs 元数据诊断报告（默认 False）

    Returns
    -------
    list[dict] — 有效患者配对列表，按患者 ID 升序排列

    Raises
    ------
    FileNotFoundError — csv_path 不存在
    ValueError        — CSV 缺少必要列，或没有可解析的有效行
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"数据表不存在：{csv_path}")

    # ── 读取：自动识别分隔符（,  or \t），兼容两种编码 ────────────
    try:
        df = pd.read_csv(
            csv_path, sep=None, engine='python',
            encoding='utf-8-sig',           # UTF-8 with BOM
            dtype=str, keep_default_na=False,
        )
    except Exception:
        # 回退：尝试 GBK（中文 Windows 默认编码）
        df = pd.read_csv(
            csv_path, sep=None, engine='python',
            encoding='gbk',
            dtype=str, keep_default_na=False,
        )

    df.columns = [c.strip() for c in df.columns]

    # 兼容旧版列名（folder_path / nii_path）与新版列名（img_path / name）
    df = df.rename(columns={
        'folder_path': 'image_path',
        'nii_path':    'roi_path',
        'img_path':    'image_path',
        'name':        'name_dcm',
    })

    required = {'image_path', 'roi_path', 'name_dcm'}
    if not required.issubset(df.columns):
        raise ValueError(
            f"CSV 必须包含列：{required}，实际列：{set(df.columns)}"
        )

    # 是否使用 exam 列判断时间点（combind.csv 格式：exam=1 → pre，exam=2 → post）
    use_exam_col = 'exam' in df.columns

    # 过滤空行
    df = df.dropna(subset=['image_path', 'roi_path'])
    df = df[df['image_path'].str.strip() != '']

    # ── 逐行解析 ──────────────────────────────────────────────────
    records = []
    for _, row in df.iterrows():
        raw_img = row['image_path'].strip()
        raw_roi = row['roi_path'].strip()

        # 患者标识直接采用 name_dcm（规范化为小写下划线），不再解析 new/old
        name = _normalize_name(row['name_dcm'])

        # 时间点判断：优先使用 exam 列（combind.csv 格式），否则回退到路径判断
        if use_exam_col:
            exam_val = str(row['exam']).strip()
            if exam_val == '1':
                timepoint = 'pre'
            elif exam_val == '2':
                timepoint = 'post'
            else:
                logger.warning(
                    f"无法判断时间点，exam='{exam_val}'，路径：{raw_img}"
                )
                timepoint = None
        else:
            timepoint = _extract_timepoint(raw_img)

        if name is None or timepoint is None:
            logger.warning(f"跳过无法解析的行（缺少 name_dcm 或时间点）：{raw_img}")
            continue

        records.append({
            'group_key'  : name,   # 按规范化姓名分组
            'name'       : name,
            'timepoint'  : timepoint,
            'image_path' : _clean_path(raw_img),
            'roi_path'   : _clean_path(raw_roi),
        })


    if not records:
        raise ValueError(
            "CSV 中没有可解析的有效行，请检查 name_dcm 列与路径结尾（\\1 / \\2）是否正确。"
        )


    # ── 按规范化姓名分组，配对 pre/post ───────────────────────────
    record_df    = pd.DataFrame(records)
    patient_pairs = []
    skipped       = []
    first_patient = True
    used_names    = {}   # 规范化姓名 → 已出现次数，用于重名去重

    for group_key, group in record_df.groupby('group_key'):
        pre_rows  = group[group['timepoint'] == 'pre']
        post_rows = group[group['timepoint'] == 'post']

        # 该组姓名（组内一致，取首行）
        name = group.iloc[0]['name']

        # 严格要求每位患者各恰好 1 条 pre 和 1 条 post 记录
        if len(pre_rows) != 1 or len(post_rows) != 1:
            logger.warning(
                f"患者 {name}：pre={len(pre_rows)} 条，"
                f"post={len(post_rows)} 条 → 跳过（需各 1 条）"
            )
            skipped.append(name)
            continue

        # 重名去重：第二次出现的同名追加 _2、_3 ...
        used_names[name] = used_names.get(name, 0) + 1
        patient_id = name if used_names[name] == 1 else f"{name}_{used_names[name]}"

        pair = {
            'patient_id': str(patient_id),
            'pre' : {

                'image_path': pre_rows.iloc[0]['image_path'],
                'roi_path'  : pre_rows.iloc[0]['roi_path'],
            },

            'post': {
                'image_path': post_rows.iloc[0]['image_path'],
                'roi_path'  : post_rows.iloc[0]['roi_path'],
            },
        }

        # ── 路径存在性验证 ────────────────────────────────────────
        if validate_paths:
            missing = []
            for tp in ('pre', 'post'):
                if not os.path.exists(pair[tp]['image_path']):
                    missing.append(f"{tp} image: {pair[tp]['image_path']}")
                if not os.path.exists(pair[tp]['roi_path']):
                    missing.append(f"{tp} roi:   {pair[tp]['roi_path']}")
            if missing:
                logger.warning(
                    f"患者 {patient_id} 以下路径不存在，已跳过：\n"
                    + "\n".join(f"  {m}" for m in missing)
                )
                skipped.append(patient_id)
                continue

        # ── 诊断模式（打印 ANTs 元数据）──────────────────────────
        if diagnose:
            if first_patient:
                print("\n" + "=" * 50)
                print("  ANTs 图像元数据诊断报告")
                print("=" * 50)
                first_patient = False
            for tp in ('pre', 'post'):
                diagnose_image(pair[tp]['image_path'], f"P{patient_id} {tp.upper()} Image")
                diagnose_image(pair[tp]['roi_path'],   f"P{patient_id} {tp.upper()} ROI")

        patient_pairs.append(pair)

    # ── 按 patient_id（规范化姓名）字母升序排序 ───────────────────
    patient_pairs.sort(key=lambda x: x['patient_id'])



    logger.info(
        f"数据表解析完成：{len(patient_pairs)} 对有效患者，"
        f"{len(skipped)} 对跳过（ID：{skipped}）"
    )
    return patient_pairs


# ═══════════════════════════════════════════════════════════════════
#  单独运行入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import json
    logging.basicConfig(level=logging.INFO, format='%(levelname)s  %(message)s')

    from config import DATA_TABLE_PATH

    # validate_paths=False：仅测试解析逻辑，不要求文件实际存在于磁盘
    # diagnose=True：若文件存在，打印 ANTs 元数据报告
    pairs = load_patient_pairs(DATA_TABLE_PATH, validate_paths=False, diagnose=True)

    print(f"\n共解析到 {len(pairs)} 对患者：")
    for p in pairs:
        print(json.dumps(p, ensure_ascii=False, indent=2))
