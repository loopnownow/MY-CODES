"""SkinVL-MM 单图冒烟测试。

用法：
    python smoke_test.py [图片路径] [--prompt "自定义问题"]

设计要点（与官方 VQA_test.py 的差异，均已核实原因）：
1. 自己做 expand2square 再送 image_processor。
   仓库 llava/mm_utils.py 的 process_images 在 pad 分支把 expand2square 注释掉了，
   直接 preprocess 会 center-crop 掉宽幅图的边缘（病变可能被裁）。
2. 贪心解码（do_sample=False），不用 beam，冒烟阶段优先可复现。
3. 显式传 max_new_tokens，因为 generation_config.json 里没有该字段（默认 20）。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import torch
from PIL import Image

REPO_DIR = Path(r"{or_codes}\external\MM-Skin")
sys.path.insert(0, str(REPO_DIR))

from llava.constants import DEFAULT_IMAGE_TOKEN, IMAGE_TOKEN_INDEX  # noqa: E402
from llava.conversation import conv_templates  # noqa: E402
from llava.mm_utils import tokenizer_image_token  # noqa: E402
from llava.model.builder import load_pretrained_model  # noqa: E402

DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "")
DEFAULT_IMAGE = os.environ.get("DEFAULT_IMAGE", "")
DEFAULT_QUESTION = (
    "Describe this dermatological image in detail, including the lesion type, "
    "color, border, distribution, and the most likely diagnosis."
)


def expand2square(pil_img: Image.Image, background_color: tuple[int, int, int]) -> Image.Image:
    """把图像铺成正方形，避免 CLIPImageProcessor 的 center-crop 裁掉边缘。"""
    width, height = pil_img.size
    if width == height:
        return pil_img
    if width > height:
        canvas = Image.new(pil_img.mode, (width, width), background_color)
        canvas.paste(pil_img, (0, (width - height) // 2))
        return canvas
    canvas = Image.new(pil_img.mode, (height, height), background_color)
    canvas.paste(pil_img, ((height - width) // 2, 0))
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", nargs="?", default=DEFAULT_IMAGE)
    parser.add_argument("--model-path", default=DEFAULT_MODEL)
    parser.add_argument("--prompt", default=DEFAULT_QUESTION)
    parser.add_argument("--max-new-tokens", type=int, default=200)
    parser.add_argument("--conv-mode", default="mistral_instruct")
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.is_file():
        print(f"FAIL: image not found: {image_path}")
        return 1

    print(f"[1/5] loading model from {args.model_path}")
    t0 = time.time()
    tokenizer, model, image_processor, context_len = load_pretrained_model(
        args.model_path,
        None,
        "LlavaMistralForCausalLM",
        device_map="cuda",
    )
    model.eval()
    print(f"      loaded in {time.time() - t0:.1f}s, context_len={context_len}")

    vision_tower = model.get_vision_tower()
    if not vision_tower.is_loaded:
        vision_tower.load_model(device_map="cuda")
    vision_tower.to(device="cuda", dtype=torch.float16)
    image_processor = vision_tower.image_processor

    print(f"[2/5] preprocessing {image_path.name}")
    image = Image.open(image_path).convert("RGB")
    origin_size = image.size
    background = tuple(int(x * 255) for x in image_processor.image_mean)
    square = expand2square(image, background)
    image_tensor = image_processor.preprocess(square, return_tensors="pt")["pixel_values"][0]
    print(f"      origin={origin_size} -> square={square.size} -> tensor={tuple(image_tensor.shape)}")

    print("[3/5] building prompt")
    conv = conv_templates[args.conv_mode].copy()
    conv.append_message(conv.roles[0], DEFAULT_IMAGE_TOKEN + "\n" + args.prompt)
    conv.append_message(conv.roles[1], None)
    prompt = conv.get_prompt()
    input_ids = tokenizer_image_token(
        prompt, tokenizer, IMAGE_TOKEN_INDEX, return_tensors="pt"
    ).unsqueeze(0).cuda()
    print(f"      prompt={prompt!r}")
    print(f"      input tokens={input_ids.shape[-1]}")

    print("[4/5] generating")
    torch.cuda.reset_peak_memory_stats()
    t1 = time.time()
    with torch.inference_mode():
        output_ids = model.generate(
            input_ids,
            images=image_tensor.unsqueeze(0).half().cuda(),
            do_sample=False,
            num_beams=1,
            max_new_tokens=args.max_new_tokens,
            use_cache=True,
            pad_token_id=tokenizer.eos_token_id,
        )
    elapsed = time.time() - t1
    n_new = output_ids.shape[-1] - input_ids.shape[-1]
    answer = tokenizer.batch_decode(output_ids, skip_special_tokens=True)[0].strip()

    peak_gb = torch.cuda.max_memory_allocated() / 1024**3
    print("[5/5] result")
    print("-" * 72)
    print(answer)
    print("-" * 72)
    print(f"new tokens={n_new}, elapsed={elapsed:.2f}s, {n_new / max(elapsed, 1e-6):.1f} tok/s")
    print(f"peak VRAM={peak_gb:.2f} GB")

    words = len(answer.split())
    if not answer:
        print("CHECK: FAIL — empty answer")
        return 1
    if words < 20:
        print(f"CHECK: WARN — answer only {words} words, model may be under-trained on this prompt")
        return 2
    if "!!!!" in answer or answer.count(answer.split()[0]) > words / 2:
        print("CHECK: WARN — possible degenerate repetition")
        return 2
    print(f"CHECK: PASS — {words} words")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
