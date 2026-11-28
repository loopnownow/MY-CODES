"""SkinVL-MM 环境自检。

只读检查，不写文件、不联网。逐项打印 PASS/FAIL，最后给一行结论。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

OUT_DIR = Path(r"{lab_root}\pre-submit\mlh_SIX\results\skinvl")   # 结果落 mlh_SIX
MODEL_DIR = Path(os.environ.get("MODEL_DIR", ""))             # 权重统一落 E:\_models
CLIP_DIR = Path(os.environ.get("CLIP_DIR", ""))
REPO_DIR = Path(r"{or_codes}\external\MM-Skin")   # 上游代码（第三方 clone）

EXPECTED_SHARDS = {
    "model-00001-of-00003.safetensors": 4_900_000_000,
    "model-00002-of-00003.safetensors": 4_900_000_000,
    "model-00003-of-00003.safetensors": 4_500_000_000,
}
EXPECTED_TOTAL_MIN = 14_500_000_000  # HF API usedStorage 14525951003

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("=" * 72)
    print("SkinVL-MM environment check")
    print("=" * 72)

    # 1. Python
    check("Python 3.10.x", sys.version_info[:2] == (3, 10), sys.version.split()[0])

    # 2. torch / CUDA
    try:
        import torch

        check("torch import", True, torch.__version__)
        check("torch is CUDA build", "+cpu" not in torch.__version__, torch.__version__)
        check("CUDA available", torch.cuda.is_available())
        if torch.cuda.is_available():
            check("GPU", True, torch.cuda.get_device_name(0))
            cap = torch.cuda.get_device_capability(0)
            check("compute capability >= 8.0", cap[0] >= 8, str(cap))
            free_gb = torch.cuda.mem_get_info()[0] / 1024**3
            check("free VRAM >= 20 GB", free_gb >= 20, f"{free_gb:.1f} GB")
    except Exception as exc:  # noqa: BLE001
        check("torch import", False, repr(exc))
        return finish()

    # 3. transformers family
    try:
        import transformers
        import tokenizers
        import safetensors  # noqa: F401

        check("transformers==4.49.0", transformers.__version__ == "4.49.0", transformers.__version__)
        check("tokenizers 0.21.x", tokenizers.__version__.startswith("0.21"), tokenizers.__version__)
        major = int(transformers.__version__.split(".")[0])
        check("transformers major < 5", major < 5, "LLaVA 代码不支持 5.x")
    except Exception as exc:  # noqa: BLE001
        check("transformers import", False, repr(exc))

    # 4. 其他依赖
    for pkg in ("PIL", "pandas", "openpyxl", "sentencepiece", "accelerate"):
        try:
            mod = __import__(pkg)
            check(f"import {pkg}", True, getattr(mod, "__version__", ""))
        except Exception as exc:  # noqa: BLE001
            check(f"import {pkg}", False, repr(exc))

    # 5. 仓库代码
    check("MM-Skin 仓库存在", REPO_DIR.is_dir(), str(REPO_DIR))
    check("llava 包存在", (REPO_DIR / "llava" / "model" / "builder.py").is_file())

    # 6. 权重
    if MODEL_DIR.is_dir():
        total = sum(f.stat().st_size for f in MODEL_DIR.rglob("*") if f.is_file())
        check("SkinVL-MM 总大小 >= 14.5 GB", total >= EXPECTED_TOTAL_MIN, f"{total/1e9:.2f} GB")
        for shard, min_size in EXPECTED_SHARDS.items():
            p = MODEL_DIR / shard
            size = p.stat().st_size if p.is_file() else 0
            check(f"shard {shard}", size >= min_size, f"{size/1e9:.2f} GB")
        check("config.json 存在", (MODEL_DIR / "config.json").is_file())
        cfg = MODEL_DIR / "config.json"
        if cfg.is_file():
            text = cfg.read_text(encoding="utf-8")
            check(
                "config.json mm_vision_tower 已改成本地路径",
                "/mnc/zwq" not in text,
                "仍是作者机器路径" if "/mnc/zwq" in text else "已改",
            )
    else:
        check("SkinVL-MM 目录存在", False, str(MODEL_DIR))

    # 7. 视觉塔
    if CLIP_DIR.is_dir():
        check("CLIP config.json", (CLIP_DIR / "config.json").is_file())
        check("CLIP preprocessor_config.json", (CLIP_DIR / "preprocessor_config.json").is_file())
        weights = list(CLIP_DIR.glob("*.bin")) + list(CLIP_DIR.glob("*.safetensors"))
        check("CLIP 权重存在", bool(weights), ", ".join(w.name for w in weights))
        check("未下载 tf_model.h5", not (CLIP_DIR / "tf_model.h5").exists())
    else:
        check("CLIP 目录存在", False, str(CLIP_DIR))

    return finish()


def finish() -> int:
    failed = [name for name, ok, _ in results if not ok]
    print("-" * 72)
    if failed:
        print(f"RESULT: FAIL ({len(failed)} 项未通过): {', '.join(failed)}")
        return 1
    print(f"RESULT: PASS ({len(results)} 项全部通过)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
