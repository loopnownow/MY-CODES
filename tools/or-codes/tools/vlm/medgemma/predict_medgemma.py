# =============================================================================
# predict_medgemma.py — MedGemma 批量预测（图片目录 → CSV）
# 运行方式：python {or_codes}\tools\vlm\medgemma\predict_medgemma.py [--data-dir 目录] [--out CSV]
#
# 路径口径：全部绝对路径，锚定项目根，与当前工作目录无关。
#   模型   E:\_models\medgemma-1.5-4b-it          （本地权重，只读）
#   图片   G:\SCC\mlh_SIX\data                     （只读输入）
#   输出   {lab_root}\pre-submit\mlh_SIX\results\medgemma\newpredictions.csv
# =============================================================================

import argparse
import csv
import os
from pathlib import Path

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
import torch
from transformers import AutoProcessor, AutoModelForImageTextToText
from PIL import Image
from tqdm import tqdm

PROJECT_ROOT = Path(r"{lab_root}\pre-submit\mlh_SIX")
MODEL_DIR    = Path(os.environ.get("MODEL_DIR", ""))
DATA_DIR     = Path(os.environ.get("DATA_DIR", ""))
OUT_CSV      = Path(r"{lab_root}\pre-submit\mlh_SIX\results\medgemma\newpredictions.csv")


def run_predictions(data_dir: Path = DATA_DIR, out_csv: Path = OUT_CSV):
    model_id = str(MODEL_DIR)
    
    # 检查数据目录
    if not os.path.exists(data_dir):
        print(f"Error: Data directory {data_dir} not found.")
        return

    # 支持的图片扩展名
    valid_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')
    image_files = [f for f in os.listdir(data_dir) if f.lower().endswith(valid_extensions)]
    
    if not image_files:
        print(f"No images found in {data_dir}.")
        return

    print(f"Found {len(image_files)} images. Loading model and processor from {model_id}...")
    
    try:
        # 加载处理器和模型
        processor = AutoProcessor.from_pretrained(model_id)
        model = AutoModelForImageTextToText.from_pretrained(
            model_id,
            torch_dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=True
        )
        print("Model and Processor loaded successfully!")
        
        # 准备 CSV 文件（父目录不存在则先建，路径已锚定 mlh_SIX\results\medgemma）
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(out_csv, mode='w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['filename', 'prediction'])
            
            # 开始遍历图片
            for filename in tqdm(image_files, desc="Predicting"):
                image_path = os.path.join(data_dir, filename)
                try:
                    image = Image.open(image_path).convert("RGB")
                    
                    messages = [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image", "image": image},
                                {"type": "text", "text": "Identify any visible medical conditions in this image."}
                            ]
                        }
                    ]
                    
                    inputs = processor.apply_chat_template(
                        messages, add_generation_prompt=True, tokenize=True,
                        return_dict=True, return_tensors="pt"
                    ).to(model.device, dtype=torch.bfloat16)
                    
                    input_len = inputs["input_ids"].shape[-1]
                    
                    with torch.inference_mode():
                        generation = model.generate(**inputs, max_new_tokens=200, do_sample=False)
                        generation = generation[0][input_len:]
                    
                    decoded = processor.decode(generation, skip_special_tokens=True).strip()
                    
                    # 写入 CSV
                    writer.writerow([filename, decoded])
                    f.flush() # 实时保存，防止崩溃丢失数据
                    
                except Exception as img_e:
                    print(f"\nError processing {filename}: {img_e}")
                    writer.writerow([filename, f"Error: {str(img_e)}"])
                    
        print(f"\nPredictions complete. Results saved to {out_csv}")
            
    except Exception as e:
        print(f"An error occurred: {e}")

def main():
    parser = argparse.ArgumentParser(description="MedGemma 批量预测")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="图片目录")
    parser.add_argument("--out", default=str(OUT_CSV), help="输出 CSV")
    args = parser.parse_args()
    run_predictions(Path(args.data_dir), Path(args.out))


if __name__ == "__main__":
    main()
