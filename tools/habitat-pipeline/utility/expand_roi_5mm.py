#!/usr/bin/env python3
r"""
将 ROI (seg) 边缘各向同性扩大 5mm（物理空间椭球扩张，适配各向异性 spacing）。

数据来源：DEFAULT_PATH 指定的目录，自动检测其中所有文件名含 SEG_KEYWORD 关键词
的 .nrrd 文件并批量处理。
  - img 文件（含关键词 IMAGE_KEYWORD）：只读，绝不写入/修改；仅在开启肺组织约束
    (--enable-lung-constraint) 时才需要，此时自动配对查找：
      * legacy 同目录布局：与原行为一致，在 seg 同目录按关键词配对；
      * v2 分离目录布局（--img-root 指定影像根目录）：按患者键精确配对，
        如 data/seg/patient_a_seg.nrrd ↔ data/main/patient_a_main.nrrd
  - roi/seg 原文件（含关键词 SEG_KEYWORD）：只读，绝不覆盖，原样保留
  - 扩张后的新文件（含关键词 ROI_KEYWORD）：另存为新文件名，写入 OUTPUT_DIR（默认与
    原 seg 同目录）


肺组织 HU 约束（仅限制扩张的新增区域，避免长入软组织/胸壁/纵膈）默认开启。

新增 GPU 加速支持（2026-07）：
  - 使用 --use-gpu 启用 CuPy 加速 binary_dilation / binary_closing / label / sum
  - 自动处理 structure=se 等 kwargs 中的 ndarray 转 cupy array
  - 默认 False，保持原有 CPU 行为 100% 兼容
  - 推荐在 A100 / CUDA 12.4 环境下使用：pip install cupy-cuda12x

# 默认：处理 DEFAULT_PATH 目录下所有 seg 文件，纯5mm扩张
python expand_roi_5mm.py

# 指定其他目录
python expand_roi_5mm.py --path "data/lung_xlm/g"

# 需要肺组织约束时
python expand_roi_5mm.py --path "data/lung_xlm" --enable-lung-constraint

# 使用 GPU (CuPy) 加速形态学扩张（推荐在 A100 等 CUDA 设备上处理大体积数据）
python expand_roi_5mm.py --path "data/lung_xlm" --use-gpu

# 同时开启肺组织约束 + GPU
python expand_roi_5mm.py --path "data/lung_xlm" --enable-lung-constraint --use-gpu

# (v2) img 与 seg 分离目录布局：--img-root 指定影像根目录，按患者键配对
python expand_roi_5mm.py --path "data/seg" --img-root "data/main" --use-gpu

# (v3) CSV 驱动模式：按 csv 的 img_path / roi_path 列逐条扩张（与 --path 二选一）
python expand_roi_5mm.py --csv "data/habitat_pipeline/combined.csv" --use-gpu
"""

import argparse
import os
import re
import sys
import logging

# ==================== CUDA 环境自修复（CuPy 14.x / Windows / Python 3.8+） ====================
# CuPy 14 不再直接读 CUDA_PATH，而是通过 cuda.pathfinder 找 nvrtc.dll 反推根目录；
# 若 nvrtc 来自 site-packages 的 splayed 布局会 return None，导致：
#   1) 警告 "CUDA path could not be detected"
#   2) 不调用 os.add_dll_directory → JIT/形态学运算可能找不到 DLL
#
# 修复策略（必须在 import cupy 完整包之前完成）：
#   A. 写 CUDA_PATH / CUDA_HOME + PATH 前置
#   B. os.add_dll_directory(cuda/bin)   # Python 3.8+ Windows 必需
#   C. 先单独加载 cupy._environment，预置 _cuda_path 缓存，
#      再让后续 import cupy 时的 _setup_win32_dll_directory() 读到正确路径
def _fix_cuda_env():
    """探测 CUDA Toolkit 根目录，写回环境变量，注入 DLL 搜索路径，并预置 CuPy 缓存。"""
    import importlib.util
    import types

    candidates = []

    # 1) 已有环境变量
    for key in ("CUDA_PATH", "CUDA_HOME"):
        val = os.environ.get(key)
        if val:
            candidates.append(val)

    # 2) 标准 NVIDIA 安装目录（优先高版本）
    if os.name == "nt":
        toolkit_root = os.environ.get("CUDA_TOOLKIT_ROOT", r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
        if os.path.isdir(toolkit_root):
            try:
                versions = sorted(
                    (d for d in os.listdir(toolkit_root)
                     if d.startswith("v") and os.path.isdir(os.path.join(toolkit_root, d))),
                    reverse=True,
                )
                candidates.extend(os.path.join(toolkit_root, v) for v in versions)
            except OSError:
                pass

    # 3) 选第一个含 bin/nvcc 的有效路径
    cuda_root = None
    for path in candidates:
        if not path:
            continue
        path = os.path.normpath(path)
        nvcc = os.path.join(path, "bin", "nvcc.exe" if os.name == "nt" else "nvcc")
        if os.path.isfile(nvcc):
            cuda_root = path
            break

    if cuda_root is None:
        return None

    # 双向补全环境变量
    os.environ["CUDA_PATH"] = cuda_root
    os.environ["CUDA_HOME"] = cuda_root

    cuda_bin = os.path.join(cuda_root, "bin")
    path_env = os.environ.get("PATH", "")
    if cuda_bin not in path_env.split(os.pathsep):
        os.environ["PATH"] = cuda_bin + os.pathsep + path_env

    # Python 3.8+ Windows：必须显式 add_dll_directory（即使 CuPy 内部失败也兜底）
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(cuda_bin)
            cuda_bin_x64 = os.path.join(cuda_bin, "x64")
            if os.path.isdir(cuda_bin_x64):
                os.add_dll_directory(cuda_bin_x64)
        except (OSError, FileNotFoundError):
            pass

    # ---- 预置 cupy._environment._cuda_path（赶在 cupy/__init__.py 的 setup 之前）----
    # 若 cupy 已完整导入，直接改缓存即可
    if "cupy._environment" in sys.modules:
        try:
            sys.modules["cupy._environment"]._cuda_path = cuda_root
        except Exception:
            pass
        return cuda_root

    # cupy 尚未导入：先只加载 _environment.py，写入缓存，再清掉 stub 包，
    # 这样后续 `import cupy` 会正常执行 __init__.py，且 from cupy import _environment
    # 会复用已缓存模块 → get_cuda_path() 返回 cuda_root → 不再警告。
    try:
        cupy_spec = importlib.util.find_spec("cupy")
        if cupy_spec is None or not cupy_spec.submodule_search_locations:
            return cuda_root
        cupy_dir = list(cupy_spec.submodule_search_locations)[0]
        env_py = os.path.join(cupy_dir, "_environment.py")
        if not os.path.isfile(env_py):
            return cuda_root

        # 临时 namespace包（不执行 cupy/__init__.py）
        if "cupy" not in sys.modules:
            stub = types.ModuleType("cupy")
            stub.__path__ = [cupy_dir]
            stub.__file__ = os.path.join(cupy_dir, "__init__.py")
            stub.__package__ = "cupy"
            sys.modules["cupy"] = stub
            stub_created = True
        else:
            stub_created = False

        env_spec = importlib.util.spec_from_file_location("cupy._environment", env_py)
        env_mod = importlib.util.module_from_spec(env_spec)
        sys.modules["cupy._environment"] = env_mod
        env_spec.loader.exec_module(env_mod)
        env_mod._cuda_path = cuda_root

        # 去掉 stub，保留 _environment，让真正的 import cupy 走完整初始化
        if stub_created:
            del sys.modules["cupy"]
    except Exception:
        # 预置失败不致命：环境变量 + add_dll_directory 已足够让 GPU 工作
        pass

    return cuda_root


_CUDA_ROOT = _fix_cuda_env()


# 确保能找到同目录下的 workflow_utils.py
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from workflow_utils import setup_logger, CheckpointManager



import numpy as np
import nibabel as nib
from scipy import ndimage


# ==================== 软编码配置（全部置顶，便于修改） ====================
# 路径与基础设置
DEFAULT_PATH = os.environ.get("EXPAND_ROI_DEFAULT_PATH", "data/habitat")          # 默认扫描目录
SEARCH_RECURSIVE = True                # 是否递归扫描子目录
RADIUS_MM = 5.0                        # 扩张半径（mm）

# 文件名关键词
SEG_KEYWORD = "seg"                    # 原始 ROI/分割文件关键词
ROI_KEYWORD = "roi"                    # 扩张后新 ROI 文件关键词
IMAGE_KEYWORD = "image"                # CT 影像文件关键词

# ---- v2: img 与 seg 分离目录布局（此前 img 与 seg 同目录，现分开放置） ----
DEFAULT_IMG_ROOT = None                # 默认 None：沿用同目录配对；指定影像根目录后按患者键配对
IMG_ROLE_KEYWORDS = ("main", "image")  # img 文件名中可剥离的患者名角色词（用于患者键配对）

# ---- v3: CSV 驱动模式（按 img_path / roi_path 列逐条扩张，替代目录扫描） ----
DEFAULT_CSV = None                       # 默认 None：目录扫描模式；指定 CSV 路径后按行配对处理
CSV_IMG_COLS = ("img_path", "image_path")  # CSV 中的影像列（按实际列名匹配，任一非空即可）
CSV_ROI_COLS = ("roi_path", "mask_path")   # CSV 中的 ROI/seg 列（按实际列名匹配，任一非空即可）

# 肺组织 HU 约束配置
ENABLE_LUNG_CONSTRAINT = True
LUNG_HU_LOW = -1200
LUNG_HU_HIGH = -100
ENABLE_CLOSING = True
ENABLE_LARGEST_LUNG = True

# 系统路径配置（日志与断点）；公开仓不留本机盘符，env 可覆盖（默认相对当前工作目录）
DEFAULT_OUTPUT_DIR = None              # 默认 None：输出到原 seg 同目录
LOG_DIR = os.environ.get("EXPAND_ROI_LOG_DIR", "logs/expand_roi")  # 日志保存目录
CHECKPOINT_DIR = os.environ.get("EXPAND_ROI_CHECKPOINT_DIR", "checkpoints/expand_roi")  # 断点标记目录

# 数据类型映射配置（软编码，按需扩展）
NRRD_TYPE_MAP = {
    "signed char": np.int8, "int8": np.int8, "int8_t": np.int8,
    "uchar": np.uint8, "unsigned char": np.uint8, "uint8": np.uint8, "uint8_t": np.uint8,
    "short": np.int16, "short int": np.int16, "signed short": np.int16,
    "int16": np.int16, "int16_t": np.int16,
    "ushort": np.uint16, "unsigned short": np.uint16, "uint16": np.uint16, "uint16_t": np.uint16,
    "int": np.int32, "signed int": np.int32, "int32": np.int32, "int32_t": np.int32,
    "uint": np.uint32, "unsigned int": np.uint32, "uint32": np.uint32, "uint32_t": np.uint32,
    "float": np.float32,
    "double": np.float64,
}


# ==================== NRRD 读写 ====================

def parse_nrrd_meta(header_bytes):
    """最小化解析 NRRD header，提取 sizes / spacings / type / encoding（robust）"""
    text = header_bytes.decode("ascii", errors="ignore")
    meta = {}
    for line in text.splitlines():
        if not line or ":" not in line:
            continue
        key, val = [x.strip() for x in line.split(":", 1)]
        meta[key] = val
    if "sizes" in meta:
        meta["sizes"] = [int(x) for x in meta["sizes"].split()]
    if "space directions" in meta:
        matches = re.findall(r"\(([^)]*)\)", meta["space directions"])
        spacings = []
        for m in matches:
            vals = [float(x.strip()) for x in m.split(",") if x.strip()]
            sp = max(abs(v) for v in vals) if vals else 0.0
            spacings.append(sp)
        meta["spacings"] = spacings  # 原始顺序（通常 x, y, z）
    return meta


def read_image(path):
    """读取影像：支持 .nrrd 和 .nii.gz"""
    if path.lower().endswith(".nrrd"):
        header_bytes, meta, data_flat = read_nrrd_raw(path)
        sizes = meta.get("sizes")
        spacings_orig = meta.get("spacings")
        shape = tuple(reversed(sizes))
        spacings = list(reversed(spacings_orig))
        data = data_flat.reshape(shape)
        return data, spacings, header_bytes  # NRRD 返回 header 用于写回
    elif path.lower().endswith((".nii", ".nii.gz")):
        img = nib.load(path)
        data = img.get_fdata()
        # NIfTI affine 包含 spacing 信息
        spacings = img.header.get_zooms()[:3]
        # nibabel 读取的数据通常是 (x, y, z)，而脚本逻辑期望 (z, y, x) 或类似，
        # 为了兼容 ndimage 操作，通常建议转换为 (z, y, x)
        data = np.transpose(data, (2, 1, 0))
        spacings = spacings[::-1]
        return data, spacings, img.affine
    else:
        raise ValueError(f"不支持的文件格式: {path}")


def read_nrrd_raw(path):
    """读取 NRRD：返回 header 原始字节 + 按 header 中 type 字段解析出的扁平数据数组。
    仅支持 raw（未压缩）encoding；其余 encoding 会显式报错，避免静默解析出乱码。
    """
    with open(path, "rb") as f:
        header_lines = []
        while True:
            line = f.readline()
            if not line:
                raise ValueError(f"文件 {path} 未找到 NRRD header 结束空行，文件可能损坏")
            header_lines.append(line)
            if line.strip() == b"":
                break
        header_bytes = b"".join(header_lines)
        raw_tail = f.read()

    meta = parse_nrrd_meta(header_bytes)
    encoding = meta.get("encoding", "raw").lower()
    if encoding not in ("raw",):
        raise ValueError(
            f"文件 {path} 的 NRRD encoding='{encoding}'，本脚本仅支持 raw（未压缩）编码。\n"
            f"请先用 3D Slicer / SimpleITK 转存为 raw 编码的 NRRD 后再运行。"
        )

    nrrd_type = meta.get("type", "").lower()
    dtype = NRRD_TYPE_MAP.get(nrrd_type)
    if dtype is None:
        raise ValueError(
            f"文件 {path} 的 NRRD type='{meta.get('type')}' 未在 NRRD_TYPE_MAP 中登记，"
            f"请在脚本顶部补充映射。"
        )

    data = np.frombuffer(raw_tail, dtype=dtype)
    return header_bytes, meta, data


def create_physical_ellipsoid_se(radius_mm, spacings):
    """
    创建物理空间各向同性球形 structuring element（适配各向异性 spacing）
    spacings: (dz, dy, dx) mm
    """
    rz = int(np.ceil(radius_mm / spacings[0])) + 2
    ry = int(np.ceil(radius_mm / spacings[1])) + 2
    rx = int(np.ceil(radius_mm / spacings[2])) + 2
    z, y, x = np.ogrid[-rz:rz + 1, -ry:ry + 1, -rx:rx + 1]
    dist_sq = (z * spacings[0]) ** 2 + (y * spacings[1]) ** 2 + (x * spacings[2]) ** 2
    se = dist_sq <= (radius_mm ** 2)
    return se.astype(bool)


# ==================== 目录自动发现（替代原 CSV 驱动方式） ====================

def find_seg_files(root_path, seg_keyword=SEG_KEYWORD, recursive=SEARCH_RECURSIVE):
    """
    自动扫描 root_path，找出所有文件名中含 SEG_KEYWORD（大小写不敏感）的
    .nrrd 或 .nii.gz 文件。
    """
    pattern = re.compile(re.escape(seg_keyword), re.IGNORECASE)
    matches = []
    exts = (".nrrd", ".nii", ".nii.gz")

    if recursive:
        for dirpath, _, filenames in os.walk(root_path):
            for fname in filenames:
                if any(fname.lower().endswith(e) for e in exts) and pattern.search(fname):
                    matches.append(os.path.join(dirpath, fname))
    else:
        for fname in os.listdir(root_path):
            fpath = os.path.join(root_path, fname)
            if os.path.isfile(fpath) and any(fname.lower().endswith(e) for e in exts) and pattern.search(fname):
                matches.append(fpath)

    return sorted(matches)


def find_paired_image_path(seg_path, img_root=None, seg_keyword=SEG_KEYWORD,
                           image_keyword=IMAGE_KEYWORD,
                           img_role_keywords=IMG_ROLE_KEYWORDS):
    """
    查找与 seg 配对的 image 文件（仅在开启肺组织约束时调用）。支持两种布局：
      1) legacy 同目录布局（img_root=None）：在 seg 同目录按关键词替换/扫描配对；
      2) v2 分离目录布局（--img-root 指定影像根目录，如 H:\\baoceng\\main）：
         按患者键精确配对 '<患者> seg.nrrd' ↔ '<患者> main.nrrd'，
         患者键 = 文件名剥离角色词（seg / main / image 及扩展名）后的字母数字串。
    支持 .nrrd, .nii, .nii.gz。
    """
    seg_dir = os.path.dirname(os.path.abspath(seg_path))
    seg_name = os.path.basename(seg_path)
    exts = (".nrrd", ".nii", ".nii.gz")

    def _patient_key(basename, role_words):
        s = basename.lower()
        for e in exts:
            if s.endswith(e):
                s = s[: -len(e)]
                break
        for w in role_words:
            s = re.sub(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", "", s)
        return re.sub(r"[^a-z0-9]", "", s)

    # ---- v2：img 与 seg 分离目录布局（--img-root 指定影像根目录） ----
    if img_root:
        img_root = os.path.abspath(img_root)
        if not os.path.isdir(img_root):
            raise FileNotFoundError(f"--img-root 指定的影像目录不存在: {img_root}")

        seg_key = _patient_key(seg_name, [seg_keyword])
        img_keywords = list(img_role_keywords)

        # 2a) 患者键精确匹配（首选：'ba chun ping seg' ↔ 'ba chun ping main'）
        if seg_key:
            hits = [f for f in os.listdir(img_root)
                    if any(f.lower().endswith(e) for e in exts)
                    and _patient_key(f, img_keywords) == seg_key]
            if len(hits) == 1:
                return os.path.join(img_root, hits[0])
            if not (len(hits) < 2):
                raise FileNotFoundError(
                    f"影像目录中患者键 '{seg_key}' 匹配到多个候选，无法确定配对: {hits}")

        # 2b) 关键词替换兜底（seg→image / 第一个 img 角色词）
        for repl in (image_keyword,) + tuple(img_keywords):
            candidate_name, n = re.compile(re.escape(seg_keyword), re.IGNORECASE).subn(repl, seg_name)
            if n:
                candidate_path = os.path.join(img_root, candidate_name)
                if os.path.exists(candidate_path):
                    return candidate_path

        raise FileNotFoundError(
            f"无法在影像目录 '{img_root}' 中为 '{seg_name}'（患者键 '{seg_key}'）"
            f"找到配对 image 文件（--enable-lung-constraint 需要）。")

    seg_pattern = re.compile(re.escape(seg_keyword), re.IGNORECASE)
    candidate_name, n = seg_pattern.subn(image_keyword, seg_name)
    if n > 0:
        candidate_path = os.path.join(seg_dir, candidate_name)
        if os.path.exists(candidate_path):
            return candidate_path

    img_pattern = re.compile(re.escape(image_keyword), re.IGNORECASE)
    candidates = [f for f in os.listdir(seg_dir)
                  if any(f.lower().endswith(e) for e in exts) and img_pattern.search(f)]
    if len(candidates) == 1:
        return os.path.join(seg_dir, candidates[0])
    if len(candidates) > 1:
        best = max(candidates, key=lambda f: len(set(f.lower()) & set(seg_name.lower())))
        return os.path.join(seg_dir, best)

    raise FileNotFoundError(
        f"无法为 '{seg_path}' 自动找到配对的 image 文件（--enable-lung-constraint 需要）。"
    )


# ==================== 输出路径生成（防覆盖校验） ====================

def derive_output_path(seg_path, output_dir=None,
                        seg_keyword=SEG_KEYWORD, roi_keyword=ROI_KEYWORD):
    """
    由 seg 文件名生成扩张后的 roi 输出文件名。
    使用大小写不敏感的正则替换 SEG_KEYWORD -> ROI_KEYWORD，
    正确处理 "xxx seg 2.nrrd" / "xxx seg.nrrd" 等各种带/不带序号后缀的命名。
    若替换后文件名未发生变化，抛出异常，避免生成与输入相同的路径而静默覆盖原文件。
    """
    seg_dir = os.path.dirname(os.path.abspath(seg_path))
    seg_name = os.path.basename(seg_path)

    pattern = re.compile(re.escape(seg_keyword), flags=re.IGNORECASE)
    new_name, n_sub = pattern.subn(roi_keyword, seg_name)

    if n_sub == 0:
        raise ValueError(
            f"文件名 '{seg_name}' 中未找到关键词 '{seg_keyword}'（整词匹配），"
            f"无法安全生成输出文件名，请检查 SEG_KEYWORD 配置或文件命名。"
        )

    out_dir = output_dir if output_dir else seg_dir
    output_path = os.path.join(out_dir, new_name)

    if os.path.abspath(output_path) == os.path.abspath(seg_path):
        raise ValueError(f"生成的输出路径与输入 seg 路径相同，为防止覆盖原文件，已中止：{output_path}")

    return output_path


def _apply_morphology(op_name, *args, use_gpu=False, **kwargs):
    """
    内部辅助函数：根据 use_gpu 标志分派到 scipy.ndimage 或 cupyx.scipy.ndimage。
    - 自动处理 'sum' -> 'sum_labels' 名称映射（CuPy API 差异）
    - GPU 模式下强制注入 brute_force=True 并手动处理多重迭代（解决 A100 兼容性报错）
    - 自动处理 numpy/cupy 数组转换，保持原有 CPU 路径 100% 不变
    """
    if not use_gpu:
        return getattr(ndimage, op_name)(*args, **kwargs)

    # === GPU 路径 (CuPy) ===
    try:
        import cupy as cp
        import cupyx.scipy.ndimage as cndimage
    except ImportError as e:
        raise ImportError(
            "使用 --use-gpu 需要安装 CuPy 且匹配您的 CUDA 版本。\n"
            "您的系统检测到 CUDA，推荐命令：\n"
            "    pip install cupy-cuda12x\n"
            "安装完成后重新运行即可。"
        ) from e

    # 获取迭代次数
    iterations = kwargs.pop("iterations", 1)
    
    # 同时转换 *args 和 **kwargs 中的 ndarray 为 cupy 数组
    # 注意：这里需要递归处理 list/range 等容器，以修复 labels/sum 的 index 参数问题
    def _to_cp(obj):
        if isinstance(obj, np.ndarray):
            return cp.asarray(obj)
        if isinstance(obj, (list, tuple, range)):
            return cp.array(list(obj))
        return obj

    new_args = [_to_cp(a) for a in args]
    new_kwargs = {k: _to_cp(v) for k, v in kwargs.items()}

    # 针对形态学操作强制注入 brute_force=True (CuPy 3D 限制)
    if op_name in ("binary_dilation", "binary_erosion", "binary_closing", "binary_opening"):
        new_kwargs["brute_force"] = True

    # CuPy 中 sum 对应 sum_labels
    real_op = "sum_labels" if op_name == "sum" else op_name
    func = getattr(cndimage, real_op)

    # 处理多重迭代：CuPy 的 3D 迭代实现在 iterations > 1 时常报错，改为 Python 循环
    if iterations > 1 and op_name in ("binary_dilation", "binary_erosion"):
        data = new_args[0]
        other_args = new_args[1:]
        for _ in range(iterations):
            data = func(data, *other_args, **new_kwargs)
        result = data
    else:
        # 对于 label/sum 或 iterations=1 的情况
        if iterations > 1:
            new_kwargs["iterations"] = iterations
        result = func(*new_args, **new_kwargs)

    # 结果转回 numpy
    if isinstance(result, tuple):
        return tuple(cp.asnumpy(x) if isinstance(x, cp.ndarray) else x for x in result)
    if isinstance(result, cp.ndarray):
        return cp.asnumpy(result)
    return result


# ==================== 核心处理 ====================

logger = logging.getLogger("expand_roi")

def process_one_pair(seg_path, img_path, radius_mm,
                      enable_lung_constraint, lung_low, lung_high,
                      enable_closing, enable_largest, output_dir,
                      use_gpu=False):
    """
    处理单个 seg：物理空间各向同性扩张 -> (可选)肺组织约束 -> 写出新 roi 文件。
    """
    logger.info(f"开始处理: {os.path.basename(seg_path)}")
    if use_gpu:
        logger.info("  [GPU] 使用 CuPy 加速形态学运算（A100 CUDA 12.4）")

    # 1. 读取 seg
    seg, spacings, extra = read_image(seg_path)
    seg_bool = seg > 0
    orig_vox = int(seg_bool.sum())
    if orig_vox == 0:
        logger.warning(f"  [警告] {os.path.basename(seg_path)}: 原始 ROI 为空（0 体素），跳过")
        return None

    # 2. 物理空间各向同性扩张（支持 GPU 加速）
    se = create_physical_ellipsoid_se(radius_mm, spacings)
    dilated = _apply_morphology("binary_dilation", seg_bool, structure=se, use_gpu=use_gpu)

    # 3. 可选：肺组织 HU 约束
    if enable_lung_constraint:
        img, img_spacings, _ = read_image(img_path)
        if img.shape != seg.shape:
            raise ValueError(
                f"img 与 seg 尺寸不一致：img={img.shape}, seg={seg.shape} "
                f"({img_path} vs {seg_path})"
            )
        
        lung_mask = (img > lung_low) & (img < lung_high)
        
        if enable_closing:
            # 针对 GPU 拆分 binary_closing (iterations=2) 为 dilation + erosion 以确保兼容性
            if use_gpu:
                lung_mask = _apply_morphology("binary_dilation", lung_mask, iterations=2, use_gpu=True)
                lung_mask = _apply_morphology("binary_erosion", lung_mask, iterations=2, use_gpu=True)
            else:
                lung_mask = _apply_morphology("binary_closing", lung_mask, iterations=2, use_gpu=False)

        if enable_largest:
            labeled, num_features = _apply_morphology("label", lung_mask, use_gpu=use_gpu)
            if num_features > 0:
                # 连通域大小 = 各标签体素数。CuPy sum_labels 对 bool 输入走慢分支（实测单例数十秒），
                # 改 np.bincount 一次 O(N) 直方图（约0.5s）；label 仍走 GPU，加速不受影响，语义相同。
                sizes_cc = np.bincount(labeled.ravel(), minlength=num_features + 1)[1:]
                max_label = int(np.argmax(sizes_cc)) + 1
                lung_mask = (labeled == max_label)
        new_roi_bool = (dilated & lung_mask) | seg_bool
    else:
        new_roi_bool = dilated | seg_bool

    # 4. 生成输出路径并写出
    output_path = derive_output_path(seg_path, output_dir=output_dir)

    for guard_path in (seg_path, img_path):
        if guard_path and os.path.abspath(output_path) == os.path.abspath(guard_path):
            raise ValueError(f"输出路径与输入文件冲突，已中止写出：{output_path}")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    if output_path.lower().endswith(".nrrd"):
        new_data = new_roi_bool.astype(seg.dtype)
        header_bytes = extra
        with open(output_path, "wb") as f:
            f.write(header_bytes)
            new_data.tofile(f)
    elif output_path.lower().endswith((".nii", ".nii.gz")):
        # 转回原始维度顺序 (x, y, z)
        out_data = np.transpose(new_roi_bool.astype(np.uint8), (2, 1, 0))
        affine = extra
        new_img = nib.Nifti1Image(out_data, affine)
        nib.save(new_img, output_path)

    # 5. 统计
    new_vox = int(new_roi_bool.sum())
    added_vox = new_vox - orig_vox
    logger.info(f"  统计: 原始={orig_vox} -> 新={new_vox} (新增={added_vox})")
    logger.info(f"  输出保存至: {output_path}")
    return output_path


def get_args():
    parser = argparse.ArgumentParser(
        description="自动扫描目录中所有含 SEG_KEYWORD 的 .nrrd 文件，将 ROI 边缘物理空间各向同性扩大 5mm，保留原 seg，另存为 roi",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--path", default=DEFAULT_PATH,
                        help="扫描目录，自动检测其中所有含 SEG_KEYWORD 的 .nrrd 文件并批量处理")

    parser.add_argument("--img-root", default=DEFAULT_IMG_ROOT,
                        help="独立影像根目录（img 与 seg 分离布局时指定，如 H:\\baoceng\\main；"
                             "不指定则以原逻辑在 seg 同目录配对）")
    parser.add_argument("--csv", default=DEFAULT_CSV,
                        help="CSV 数据表路径（需含 img_path/image_path 与 roi_path/mask_path 列，"
                             "按行逐条扩张；与 --path 二选一，指定后忽略 --path）")
    parser.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=SEARCH_RECURSIVE,
                        help="递归扫描 --path 的子目录（--no-recursive 关闭）")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help="输出目录（默认与原 seg 文件同目录）")
    parser.add_argument("--radius-mm", type=float, default=RADIUS_MM,
                        help="扩张半径（mm，物理空间，各向同性）")
    parser.add_argument("--enable-lung-constraint", action=argparse.BooleanOptionalAction, default=ENABLE_LUNG_CONSTRAINT,
                        help="开启后，新增区域仅限于肺组织 HU 范围内（原始 ROI 体素不受影响），"
                             "此时会自动在同目录按关键词配对查找 image 文件（--no-enable-lung-constraint 关闭）")
    parser.add_argument("--lung-low", type=int, default=LUNG_HU_LOW, help="肺组织 HU 下限")
    parser.add_argument("--lung-high", type=int, default=LUNG_HU_HIGH, help="肺组织 HU 上限")
    parser.add_argument("--enable-closing", action=argparse.BooleanOptionalAction, default=ENABLE_CLOSING,
                        help="对 lung_mask 执行 binary_closing（仅在 --enable-lung-constraint 时有效，"
                             "--no-enable-closing 关闭）")
    parser.add_argument("--enable-largest-lung", action=argparse.BooleanOptionalAction, default=ENABLE_LARGEST_LUNG,
                        help="只保留最大连通肺野（仅在 --enable-lung-constraint 时有效，"
                             "--no-enable-largest-lung 关闭）")
    parser.add_argument("--use-gpu", action=argparse.BooleanOptionalAction, default=False,
                        help="启用 GPU (CuPy) 加速形态学操作（binary_dilation / closing / label）。"
                             "需先安装：pip install cupy-cuda12x （对应 CUDA 12.4）")
    return parser.parse_args()




# ==================== v3: CSV 驱动模式（按 img_path / roi_path 列） ====================

def _load_csv_rows(csv_path):
    """读取长表 CSV（utf-8-sig → gbk），按列名提取 img_path/image_path 与 roi_path/mask_path。"""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"--csv 数据表不存在: {csv_path}")
    import csv as _csv_mod
    frame = None
    for enc in ("utf-8-sig", "gbk"):
        frame = []
        try:
            with open(csv_path, "r", encoding=enc, newline="") as fh:
                reader = _csv_mod.DictReader(fh)
                for row in reader:
                    clean = {k.strip(): v for k, v in row.items()}
                    img = next((clean[c] for c in CSV_IMG_COLS if clean.get(c)), "").strip()
                    roi = next((clean[c] for c in CSV_ROI_COLS if clean.get(c)), "").strip()
                    if img or roi:
                        frame.append({"img_path": img, "roi_path": roi})
            break
        except (UnicodeDecodeError, UnicodeError):
            frame = None
            continue
    if frame is None:
        raise ValueError(f"无法解码 CSV（尝试 utf-8-sig / gbk）: {csv_path}")
    return frame


def run_from_csv(args, cp_mgr):
    """CSV 驱动模式：按 csv 的 img_path / roi_path 列逐条扩张（img_path 用于肺约束配对）。"""
    logger.info(f"[CSV 模式] 数据表: {args.csv}")
    rows = _load_csv_rows(args.csv)
    if not rows:
        logger.error("CSV 中没有包含 img_path / roi_path 列的有效行，无法处理。")
        return
    logger.info(f"[CSV 模式] 读取 {len(rows)} 个 img/roi 配对")

    missing_roi = [r["roi_path"] for r in rows if r["roi_path"] and not os.path.exists(r["roi_path"])]
    if missing_roi:
        logger.warning(
            f"以下 {len(missing_roi)} 个 roi_path 不存在（这些行将跳过）: " + ", ".join(missing_roi[:5]))
    missing_img = [r["img_path"] for r in rows if args.enable_lung_constraint
                   and r["img_path"] and not os.path.exists(r["img_path"])]
    if missing_img:
        logger.warning(
            f"以下 {len(missing_img)} 个 img_path 不存在（肺约束将失败）: " + ", ".join(missing_img[:5]))

    processed, skipped, failed = [], [], []
    for row in rows:
        seg_path = row["roi_path"]
        if not seg_path or not os.path.exists(seg_path):
            failed.append((seg_path or "(空)", "roi_path 不存在"))
            continue
        if args.enable_lung_constraint and not os.path.exists(row["img_path"]):
            failed.append((seg_path, f"img_path 不存在: {row['img_path']}"))
            continue
        img_path = row["img_path"] or None

        # 任务唯一标识：绝对路径规范化，避免与目录扫描模式的相对路径断点混淆
        task_id = "expand_" + re.sub(r"[^0-9A-Za-z_.-]", "_",
                                     os.path.normpath(os.path.abspath(seg_path)))
        if cp_mgr.is_done(task_id):
            logger.info(f"[跳过] {os.path.basename(seg_path)} (断点记录：已完成)")
            skipped.append(seg_path)
            continue

        try:
            expected_out = derive_output_path(
                seg_path, output_dir=args.output_dir,
                seg_keyword=SEG_KEYWORD, roi_keyword=ROI_KEYWORD,
            )
            if os.path.exists(expected_out) and os.path.getsize(expected_out):
                logger.info(f"[跳过] {os.path.basename(seg_path)} (输出文件已存在: {expected_out})")
                cp_mgr.mark_done(task_id)
                skipped.append(seg_path)
                continue
        except Exception:
            pass

        try:
            out = process_one_pair(
                seg_path=seg_path,
                img_path=img_path,
                radius_mm=args.radius_mm,
                enable_lung_constraint=args.enable_lung_constraint,
                lung_low=args.lung_low,
                lung_high=args.lung_high,
                enable_closing=args.enable_closing,
                enable_largest=args.enable_largest_lung,
                output_dir=args.output_dir,
                use_gpu=args.use_gpu,
            )
            if out is None:
                skipped.append(seg_path)
            else:
                processed.append(out)
                cp_mgr.mark_done(task_id)
        except Exception as e:
            logger.error(f"  [失败] {seg_path}: {e}")
            failed.append((seg_path, str(e)))

    logger.info(
        f"[CSV 模式] 汇总：成功 {len(processed)} / 跳过 {len(skipped)} / 失败 {len(failed)} (共 {len(rows)})")
    if failed:
        logger.error("失败列表已在日志中记录。")



def main():
    args = get_args()

    # 初始化日志和断点管理
    global logger
    logger = setup_logger(LOG_DIR, name="expand_roi")
    cp_mgr = CheckpointManager(CHECKPOINT_DIR)

    logger.info("=== 任务启动 ===")
    logger.info(f"扫描路径: {args.path}")
    logger.info(f"影像根目录: {args.img_root if args.img_root else "(同目录配对)"}")
    logger.info(f"递归模式: {args.recursive}")
    logger.info(f"扩张半径: {args.radius_mm}mm")
    logger.info(f"肺约束  : {args.enable_lung_constraint}")
    logger.info(f"GPU加速 : {args.use_gpu}")
    if _CUDA_ROOT:
        logger.info(f"CUDA路径: {_CUDA_ROOT}")

    # --use-gpu 时启动自检：确认 CuPy 可见设备且路径已识别
    if args.use_gpu:
        try:
            import cupy as cp
            from cupy import _environment as _ce
            n_dev = cp.cuda.runtime.getDeviceCount()
            if n_dev < 1:
                raise RuntimeError("CuPy 未检测到任何 CUDA 设备")
            name = cp.cuda.runtime.getDeviceProperties(0)["name"]
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            cuda_path = _ce.get_cuda_path()
            logger.info(f"[GPU自检] 设备数={n_dev}, 设备0={name}, get_cuda_path={cuda_path}")
            # 最小形态学冒烟测试
            _t = cp.zeros((8, 8, 8), dtype=bool)
            _t[3:5, 3:5, 3:5] = True
            import cupyx.scipy.ndimage as _cnd
            _ = _cnd.binary_dilation(_t, brute_force=True)
            del _t, _
            cp.get_default_memory_pool().free_all_blocks()
            logger.info("[GPU自检] binary_dilation 冒烟测试通过")
        except Exception as e:
            logger.error(
                f"[GPU自检失败] {e}\n"
                f"  CUDA_PATH={os.environ.get('CUDA_PATH')}\n"
                f"  请确认: 1) nvidia-smi 正常  2) pip install cupy-cuda12x  3) CUDA Toolkit 已安装"
            )
            return

    # ---- v3: CSV 驱动模式（--csv 指定数据表后按行处理，跳过目录扫描） ----
    if args.csv:
        run_from_csv(args, cp_mgr)
        return

    if not os.path.isdir(args.path):
        logger.error(f"路径不存在: {args.path}")
        return


    seg_files = find_seg_files(args.path, recursive=args.recursive, seg_keyword=SEG_KEYWORD)
    logger.info(f"检测到 {len(seg_files)} 个待处理文件")
    
    if not seg_files:
        logger.warning("未找到待处理文件。")
        return

    processed, skipped, failed = [], [], []
    for seg_path in seg_files:
        # 构造任务唯一标识用于断点记录（相对路径）
        rel_path = os.path.relpath(seg_path, args.path)
        task_id = f"expand_{rel_path}"
        
        # 1. 检查断点标记文件
        if cp_mgr.is_done(task_id):
            logger.info(f"[跳过] {os.path.basename(seg_path)} (断点记录：已完成)")
            skipped.append(seg_path)
            continue

        # 2. 检查输出文件是否已存在（针对之前手动处理过的情况）
        try:
            expected_out = derive_output_path(
                seg_path, 
                output_dir=args.output_dir,
                seg_keyword=SEG_KEYWORD,
                roi_keyword=ROI_KEYWORD
            )
            if os.path.exists(expected_out) and os.path.getsize(expected_out) > 0:
                logger.info(f"[跳过] {os.path.basename(seg_path)} (输出文件已存在: {expected_out})")
                cp_mgr.mark_done(task_id) # 自动补全断点标记
                skipped.append(seg_path)
                continue
        except Exception:
            pass # 如果无法生成路径则继续正常流程，让后续报错

        try:
            img_path = None
            if args.enable_lung_constraint:
                img_path = find_paired_image_path(
                    seg_path,
                    img_root=args.img_root,
                    seg_keyword=SEG_KEYWORD,
                    image_keyword=IMAGE_KEYWORD
                )

            out = process_one_pair(
                seg_path=seg_path,
                img_path=img_path,
                radius_mm=args.radius_mm,
                enable_lung_constraint=args.enable_lung_constraint,
                lung_low=args.lung_low,
                lung_high=args.lung_high,
                enable_closing=args.enable_closing,
                enable_largest=args.enable_largest_lung,
                output_dir=args.output_dir,
                use_gpu=args.use_gpu,
            )
            
            if out is None:
                skipped.append(seg_path)
            else:
                processed.append(out)
                cp_mgr.mark_done(task_id) # 标记成功完成
                
        except Exception as e:
            logger.error(f"  [失败] {seg_path}: {e}")
            failed.append((seg_path, str(e)))

    logger.info(f"任务汇总：成功 {len(processed)} / 跳过 {len(skipped)} / 失败 {len(failed)} (共 {len(seg_files)})")
    if failed:
        logger.error("失败列表已在日志中记录。")


if __name__ == "__main__":
    main()
