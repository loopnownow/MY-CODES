# =============================================================================
# step0_check_env.py — 环境检查与依赖验证
# 运行方式：python step0_check_env.py
# 目的：在开始任何训练前，确认所有依赖和硬件均符合要求
# =============================================================================

import os
import sys
import subprocess

# Windows：conda 的 libiomp5md.dll 与 torch 自带副本重复，import torch 会以 OMP Error #15 直接退出
# 与 skinvl 胶水脚本（_Codes\tools\vlm\skinvl）的做法一致：导入 torch 前先设环境变量
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def check(name: str, condition: bool, fix: str = ""):
    """统一的检查输出格式"""
    status = "[OK]" if condition else "[FAILED]"
    try:
        print(f"  {status} {name}")
    except UnicodeEncodeError:
        print(f"  {status} {name.encode('ascii', 'ignore').decode('ascii')}")
    if not condition and fix:
        try:
            print(f"     -> Fix: {fix}")
        except UnicodeEncodeError:
            pass
    return condition


def section(title: str):
    print(f"\n" + "="*50)
    print(f"  {title}")
    print(f"="*50)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Python 版本
# ─────────────────────────────────────────────────────────────────────────────
section("1. Python 版本")
py_ok = check(
    f"Python >= 3.10（当前: {sys.version.split()[0]}）",
    sys.version_info >= (3, 10),
    "conda create -n medgemma-ft python=3.11"
)

# ─────────────────────────────────────────────────────────────────────────────
# 2. GPU / CUDA
# ─────────────────────────────────────────────────────────────────────────────
section("2. GPU / CUDA")
try:
    import torch

    cuda_ok = check(
        f"CUDA 可用",
        torch.cuda.is_available(),
        "重新安装 PyTorch：pip install torch --index-url https://download.pytorch.org/whl/cu124"
    )

    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9

        check(f"GPU 型号: {gpu_name}", True)

        vram_ok = check(
            f"VRAM >= 40GB（当前: {vram_gb:.1f} GB）",
            vram_gb >= 40,
            "VRAM < 40GB：降低 LORA_R=8，MAX_SEQ_LENGTH=2048，或改用 medgemma-4b-it"
        )

        cuda_ver = torch.version.cuda
        check(f"CUDA 版本: {cuda_ver}", True)

        bf16_ok = check(
            f"支持 bfloat16（A100/H100/RTX30xx+）",
            torch.cuda.is_bf16_supported(),
            "不支持 bf16：在 config.py 中将 BF16=False，改用 fp16"
        )
    else:
        vram_ok = bf16_ok = False

except ImportError:
    print("  [FAILED] torch is not installed")
    print("     -> Fix: pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124")
    cuda_ok = vram_ok = bf16_ok = False

# ─────────────────────────────────────────────────────────────────────────────
# 3. 核心依赖包
# ─────────────────────────────────────────────────────────────────────────────
section("3. 核心依赖包")

# 包名 → (import 名, 最低版本字符串或 None)
REQUIRED_PACKAGES = {
    "unsloth":       ("unsloth",       None),
    "unsloth_zoo":   ("unsloth_zoo",   None),
    "trl":           ("trl",           "0.8.0"),
    "peft":          ("peft",          "0.9.0"),
    "accelerate":    ("accelerate",    "0.28.0"),
    "bitsandbytes":  ("bitsandbytes",  None),
    "datasets":      ("datasets",      None),
    "transformers":  ("transformers",  "4.40.0"),
    "PIL":           ("PIL",           None),
    "pandas":        ("pandas",        None),
    "sklearn":       ("sklearn",       None),
}

pkg_ok_all = True
for pkg_name, (import_name, min_ver) in REQUIRED_PACKAGES.items():
    try:
        mod = __import__(import_name)
        ver = getattr(mod, "__version__", "unknown")
        label = f"{pkg_name} ({ver})"

        if min_ver and ver != "unknown":
            # 简单版本比较（仅比较主.次版本号）
            from packaging.version import Version
            try:
                ver_ok = Version(ver) >= Version(min_ver)
                label += f" >= {min_ver}"
            except Exception:
                ver_ok = True
        else:
            ver_ok = True

        ok = check(label, ver_ok, f"pip install --upgrade {pkg_name}")
        pkg_ok_all = pkg_ok_all and ok
    except ImportError:
        ok = check(f"{pkg_name}（未安装）", False,
                   f"pip install {pkg_name}")
        pkg_ok_all = False

# ─────────────────────────────────────────────────────────────────────────────
# 4. Hugging Face Token
# ─────────────────────────────────────────────────────────────────────────────
section("4. Hugging Face 认证")
try:
    from huggingface_hub import whoami
    try:
        info = whoami()
        check(f"HF 登录用户: {info['name']}", True)
        hf_ok = True
    except Exception:
        check("HF Token 未配置", False,
              "执行：huggingface-cli login，然后在 https://huggingface.co/google/medgemma-27b-it 点击同意协议")
        hf_ok = False
except ImportError:
    check("huggingface_hub 未安装", False, "pip install huggingface_hub")
    hf_ok = False

# ─────────────────────────────────────────────────────────────────────────────
# 5. 磁盘空间
# ─────────────────────────────────────────────────────────────────────────────
section("5. 磁盘空间")
import shutil
total, used, free = shutil.disk_usage(".")
free_gb = free / 1e9
check(
    f"磁盘可用空间 >= 100GB（当前: {free_gb:.1f} GB）",
    free_gb >= 100,
    "清理磁盘。模型本身约 54GB + 输出约 25GB + 缓存约 20GB"
)

# ─────────────────────────────────────────────────────────────────────────────
# 6. UnslothVisionDataCollator 可用性（关键修复点）
# ─────────────────────────────────────────────────────────────────────────────
section("6. Unsloth 视觉训练组件")
try:
    from unsloth import FastVisionModel
    check("FastVisionModel 可导入", True)
    vision_ok = True
except ImportError:
    check("FastVisionModel 不可导入", False,
          "pip install --upgrade --force-reinstall unsloth unsloth_zoo")
    vision_ok = False

try:
    from unsloth.trainer import UnslothVisionDataCollator
    check("UnslothVisionDataCollator 可导入", True)
    collator_ok = True
except ImportError:
    check("UnslothVisionDataCollator 不可导入", False,
          "unsloth 版本过低，执行：pip install --upgrade unsloth")
    collator_ok = False

# ─────────────────────────────────────────────────────────────────────────────
# 7. 数据文件检查
# ─────────────────────────────────────────────────────────────────────────────
section("7. 数据文件")
import os, json
from config import TRAIN_JSONL, TEST_JSONL, CLASSES

for filepath, label in [(TRAIN_JSONL, "训练集"), (TEST_JSONL, "测试集")]:
    if not os.path.exists(filepath):
        check(f"{label} 文件存在: {filepath}", False,
              f"先运行 step1_prepare_data.py 生成示例数据，或手动准备 JSONL 文件")
    else:
        # 读取并验证第一条记录的字段完整性
        with open(filepath, encoding="utf-8") as f:
            first = json.loads(f.readline())

        required_fields = {"image_path", "history", "label", "standard_output"}
        missing = required_fields - set(first.keys())
        if missing:
            check(f"{label}: 缺少字段 {missing}", False,
                  "确保 JSONL 每行包含 image_path, history, label, standard_output")
        else:
            # 统计行数
            with open(filepath, encoding="utf-8") as f:
                n = sum(1 for _ in f)
            check(f"{label} ({n} 条): 字段完整", True)

            # 检查类别是否合法
            invalid_labels = []
            with open(filepath, encoding="utf-8") as f:
                for line in f:
                    row = json.loads(line)
                    if row["label"] not in CLASSES:
                        invalid_labels.append(row["label"])
            if invalid_labels:
                unique_invalid = list(set(invalid_labels))[:5]
                check(f"{label}: 存在非法类别 {unique_invalid}", False,
                      f"合法类别为：{CLASSES}")
            else:
                check(f"{label}: 所有类别合法", True)

# ─────────────────────────────────────────────────────────────────────────────
# 最终汇总
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 50)
print("  环境检查完成")
print("=" * 50)

critical_ok = cuda_ok and vram_ok and pkg_ok_all and vision_ok and collator_ok
if critical_ok:
    print("  [SUCCESS] All critical checks passed, you can run step1_prepare_data.py")
else:
    print("  [ERROR] Critical issues found, please fix them before proceeding")
    sys.exit(1)   # 非零退出码，方便 shell 脚本检测
