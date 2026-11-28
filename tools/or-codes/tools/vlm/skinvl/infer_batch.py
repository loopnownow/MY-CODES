"""SkinVL-MM 批量推理。

输入两种模式：
  A. 标注表模式（默认）：读 xlsx 的图片列，按文件名匹配图片目录，逐行输出
  B. 目录模式：给 --image-dir 且不给 --xlsx，扫目录里所有 jpg/jpeg

输出：xlsx（不覆盖源表），每 20 条落盘一次，支持 --resume 断点续跑。

不变式：
  - 源 xlsx 只读，绝不写回。
  - 输出表不含姓名、门诊号、身份证、电话、地址。
  - 单条失败只记 status/error，不中断整批。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import pandas as pd
import torch
from PIL import Image

REPO_DIR = Path(r"{or_codes}\external\MM-Skin")
sys.path.insert(0, str(REPO_DIR))

from llava.constants import DEFAULT_IMAGE_TOKEN, IMAGE_TOKEN_INDEX  # noqa: E402
from llava.conversation import conv_templates  # noqa: E402
from llava.mm_utils import tokenizer_image_token  # noqa: E402
from llava.model.builder import load_pretrained_model  # noqa: E402

MODEL_PATH = os.environ.get("MODEL_PATH", "")
DEFAULT_XLSX = r"{lab_root}\pre-submit\mlh_SIX\data\wj_SC.xlsx"
DEFAULT_SHEET = "jpgnames"
DEFAULT_IMAGE_COL = "files"
DEFAULT_IMAGE_DIR = os.environ.get("DEFAULT_IMAGE_DIR", "")
DEFAULT_OUT = r"{lab_root}\pre-submit\mlh_SIX\results\skinvl\skinvl_caption.xlsx"
FLUSH_EVERY = 20
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

PROMPTS = {
    # 与 MM-Skin-C 教科书图文语料同口径的长描述
    "caption": (
        "Describe this dermatological image in detail, including the lesion type, "
        "color, border, distribution, and the most likely diagnosis."
    ),
    # 与 9 类金标准口径对齐的受限分类
    "diagnosis": (
        "Look at this dermatological image and give the single most likely diagnosis. "
        "Choose exactly one from this list: basal cell carcinoma, squamous cell carcinoma, "
        "melanoma, nevus, cyst, seborrheic keratosis, actinic keratosis, "
        "other benign lesion, unknown. Answer with the label only."
    ),
}

# 模型输出 -> 9 类标签（顺序即优先级，长词在前避免被短词吃掉）
DIAGNOSIS_RULES: list[tuple[str, list[str]]] = [
    ("MM", ["melanoma", "melanocytic malignan"]),
    ("SCC", ["squamous cell carcinoma", "scc", "bowen"]),
    ("BCC", ["basal cell carcinoma", "bcc"]),
    ("AK", ["actinic keratosis", "solar keratosis"]),
    ("SK", ["seborrheic keratosis", "seborrhoeic keratosis", "seborrheic wart"]),
    ("CY", ["cyst", "epidermoid cyst", "epidermal cyst"]),
    ("NV", ["nevus", "naevus", "mole", "melanocytic nevus"]),
    ("BN", ["other benign", "benign lesion", "benign"]),
    ("UN", ["unknown", "uncertain", "not sure"]),
]
LABELS = ["BCC", "SCC", "MM", "NV", "CY", "SK", "AK", "BN", "UN"]


def expand2square(pil_img: Image.Image, background_color: tuple[int, int, int]) -> Image.Image:
    """铺成正方形，规避 CLIPImageProcessor 的 center-crop 裁边。"""
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


def parse_label(answer: str) -> str:
    """把自由文本答案映射到 9 类；匹配不到返回 UN。"""
    low = answer.lower()
    for label, keys in DIAGNOSIS_RULES:
        if any(k in low for k in keys):
            return label
    return "UN"


def build_prompt(conv_mode: str, question: str) -> str:
    conv = conv_templates[conv_mode].copy()
    conv.append_message(conv.roles[0], DEFAULT_IMAGE_TOKEN + "\n" + question)
    conv.append_message(conv.roles[1], None)
    return conv.get_prompt()


def collect_tasks(args) -> pd.DataFrame:
    """返回含 name / source_row 的任务表；name 是磁盘上的实际文件名。"""
    image_dir = Path(args.image_dir)
    if not image_dir.is_dir():
        raise SystemExit(f"图片目录不存在: {image_dir}")

    if args.xlsx:
        xlsx = Path(args.xlsx)
        if not xlsx.is_file():
            raise SystemExit(f"标注表不存在: {xlsx}")
        src = pd.read_excel(xlsx, sheet_name=args.sheet)
        if args.image_col not in src.columns:
            raise SystemExit(f"列 {args.image_col!r} 不在表中，现有列: {list(src.columns)}")
        names = src[args.image_col].astype(str).map(lambda s: os.path.basename(s.strip()))
        disk_lookup = {
            p.name.lower(): p.name
            for p in image_dir.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        }
        names = names.map(lambda n: disk_lookup.get(n.lower(), n))
        tasks = pd.DataFrame({"name": names, "source_row": range(len(src))})
        tasks["in_table"] = True
        missing = ~tasks["name"].map(lambda n: (image_dir / n).is_file())
        print(f"[tasks] 标注表 {len(tasks)} 行，磁盘缺失 {int(missing.sum())} 张")
    else:
        files = sorted(
            p.name for p in image_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        )
        tasks = pd.DataFrame({"name": files, "source_row": range(len(files)), "in_table": False})
        print(f"[tasks] 目录模式 {len(tasks)} 张图")

    if args.start or args.end or args.limit:
        lo = args.start or 0
        hi = args.end if args.end else len(tasks)
        tasks = tasks.iloc[lo:hi]
        if args.limit:
            tasks = tasks.iloc[: args.limit]
        print(f"[tasks] 取子集 rows [{lo}, {lo + len(tasks)})")
    return tasks.reset_index(drop=True)


def load_model(model_path: str):
    print(f"[model] loading {model_path}")
    t0 = time.time()
    tokenizer, model, image_processor, context_len = load_pretrained_model(
        model_path, None, "LlavaMistralForCausalLM", device_map="cuda"
    )
    model.eval()
    vision_tower = model.get_vision_tower()
    if not vision_tower.is_loaded:
        vision_tower.load_model(device_map="cuda")
    vision_tower.to(device="cuda", dtype=torch.float16)
    image_processor = vision_tower.image_processor
    tokenizer.padding_side = "left"
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    print(f"[model] loaded in {time.time() - t0:.1f}s, context_len={context_len}")
    return tokenizer, model, image_processor


def run_batch(
    tokenizer,
    model,
    image_processor,
    conv_mode: str,
    names: list[str],
    image_dir: Path,
    question: str,
    max_new_tokens: int,
) -> list[dict]:
    """跑一个 batch，返回与 names 等长的记录列表。"""
    pad_id = tokenizer.pad_token_id or tokenizer.eos_token_id
    background = tuple(int(x * 255) for x in image_processor.image_mean)

    records: list[dict] = []
    valid_names: list[str] = []
    tensors: list[torch.Tensor] = []
    prompts: list[str] = []
    size_map: dict[str, tuple[int, int]] = {}

    for name in names:
        try:
            img = Image.open(image_dir / name).convert("RGB")
            size_map[name] = img.size
            tensors.append(
                image_processor.preprocess(expand2square(img, background), return_tensors="pt")[
                    "pixel_values"
                ][0]
            )
            prompts.append(build_prompt(conv_mode, question))
            valid_names.append(name)
        except Exception as exc:  # noqa: BLE001
            records.append(
                {"name": name, "answer": "", "status": "bad_image", "error": repr(exc)}
            )

    if not valid_names:
        return records

    seqs = [
        tokenizer_image_token(p, tokenizer, IMAGE_TOKEN_INDEX, return_tensors="pt") for p in prompts
    ]
    max_len = max(int(s.shape[0]) for s in seqs)
    input_ids = torch.full((len(seqs), max_len), pad_id, dtype=torch.long)
    attention_mask = torch.zeros((len(seqs), max_len), dtype=torch.long)
    for i, s in enumerate(seqs):
        n = int(s.shape[0])
        input_ids[i, max_len - n :] = s
        attention_mask[i, max_len - n :] = 1

    images = torch.stack(tensors, dim=0).half().cuda()
    t0 = time.time()
    with torch.inference_mode():
        output_ids = model.generate(
            inputs=input_ids.cuda(),
            attention_mask=attention_mask.cuda(),
            images=images,
            do_sample=False,
            num_beams=1,
            max_new_tokens=max_new_tokens,
            use_cache=True,
            pad_token_id=pad_id,
        )
    elapsed = time.time() - t0

    texts = tokenizer.batch_decode(output_ids, skip_special_tokens=True)
    per_item = elapsed / max(len(valid_names), 1)
    ok_records = []
    for name, text in zip(valid_names, texts):
        w, h = size_map.get(name, (0, 0))
        answer = text.strip()
        ok_records.append(
            {
                "name": name,
                "answer": answer,
                "label": parse_label(answer),
                "image_w": w,
                "image_h": h,
                "status": "ok",
                "error": "",
                "elapsed_s": round(per_item, 3),
            }
        )
    return records + ok_records


def flush(rows: list[dict], out_path: Path, prompt_mode: str) -> None:
    df = pd.DataFrame(rows)
    for col in ("label", "answer", "error"):
        if col not in df.columns:
            df[col] = ""
    df["prompt_mode"] = prompt_mode
    if "source_row" in df.columns:
        df = df.sort_values("source_row", kind="stable")
    order = [
        "source_row",
        "name",
        "status",
        "label",
        "answer",
        "image_w",
        "image_h",
        "elapsed_s",
        "error",
        "prompt_mode",
    ]
    df = df[[c for c in order if c in df.columns]]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(out_path, index=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="SkinVL-MM batch inference")
    parser.add_argument("--model-path", default=MODEL_PATH)
    parser.add_argument("--xlsx", default=DEFAULT_XLSX, help="标注表；给空字符串则走目录模式")
    parser.add_argument("--sheet", default=DEFAULT_SHEET)
    parser.add_argument("--image-col", default=DEFAULT_IMAGE_COL, help="图片文件名所在列")
    parser.add_argument("--image-dir", default=DEFAULT_IMAGE_DIR)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--prompt-mode", choices=sorted(PROMPTS), default="caption")
    parser.add_argument("--prompt", default=None, help="自定义问题，覆盖 --prompt-mode")
    parser.add_argument("--conv-mode", default="mistral_instruct")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-new-tokens", type=int, default=160)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    question = args.prompt or PROMPTS[args.prompt_mode]
    out_path = Path(args.out)

    tasks = collect_tasks(args)
    tasks["name"] = tasks["name"].astype(str)

    rows_by_key: dict[str, dict] = {}
    if args.resume and out_path.is_file():
        prev = pd.read_excel(out_path)
        if "name" in prev.columns:
            for rec in prev.to_dict("records"):
                rows_by_key[str(rec["name"])] = rec
        done = {
            k for k, v in rows_by_key.items() if str(v.get("status", "")).strip().lower() == "ok"
        }
        print(f"[resume] 读回 {len(rows_by_key)} 条，其中 success {len(done)} 条")
    else:
        done = set()

    todo = [(int(i), n) for i, n in zip(tasks.index, tasks["name"]) if n not in done]
    print(f"[run] 待推理 {len(todo)} / {len(tasks)}，batch={args.batch_size}，mode={args.prompt_mode}")

    if not todo:
        flush(list(rows_by_key.values()), out_path, args.prompt_mode)
        print("[done] 没有待处理项")
        return 0

    tokenizer, model, image_processor = load_model(args.model_path)
    image_dir = Path(args.image_dir)

    t_start = time.time()
    n_ok = 0
    n_fail = 0
    for bi in range(0, len(todo), args.batch_size):
        chunk = todo[bi : bi + args.batch_size]
        names = [n for _, n in chunk]
        try:
            recs = run_batch(
                tokenizer,
                model,
                image_processor,
                args.conv_mode,
                names,
                image_dir,
                question,
                args.max_new_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            recs = [
                {"name": n, "answer": "", "label": "", "status": "batch_error", "error": repr(exc)}
                for n in names
            ]

        by_name = {str(r["name"]): r for r in recs}
        for idx, name in chunk:
            rec = by_name.get(name) or {
                "name": name,
                "answer": "",
                "label": "",
                "status": "missing_record",
                "error": "",
            }
            rec["source_row"] = int(tasks.at[idx, "source_row"])
            rows_by_key[name] = rec
            if str(rec.get("status")) == "ok":
                n_ok += 1
            else:
                n_fail += 1

        done_n = bi + len(chunk)
        rate = done_n / max(time.time() - t_start, 1e-6)
        print(
            f"[{done_n}/{len(todo)}] ok={n_ok} fail={n_fail} "
            f"{rate:.2f} img/s eta={(len(todo)-done_n)/max(rate,1e-6)/60:.1f} min"
        )
        if done_n % FLUSH_EVERY == 0 or done_n == len(todo):
            flush(list(rows_by_key.values()), out_path, args.prompt_mode)

    flush(list(rows_by_key.values()), out_path, args.prompt_mode)
    total = time.time() - t_start
    print("-" * 72)
    print(f"done: ok={n_ok} fail={n_fail} elapsed={total/60:.1f} min")
    print(f"throughput: {len(todo)/max(total, 1e-6):.2f} img/s")
    print(f"peak VRAM: {torch.cuda.max_memory_allocated()/1024**3:.2f} GB")
    print(f"output: {out_path}")
    return 0 if n_fail == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())


