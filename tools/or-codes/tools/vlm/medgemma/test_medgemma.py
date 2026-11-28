# =============================================================================
# test_medgemma.py — 本地 MedGemma 加载 + 单图推理冒烟测试
# 运行方式：python {or_codes}\tools\vlm\medgemma\test_medgemma.py
#
# 路径口径：绝对路径，不依赖当前工作目录。
#   模型   E:\_models\medgemma-1.5-4b-it   （只读）
#   测试图 G:\SCC\mlh_SIX\data\03.jpg        （只读）
# =============================================================================

import os
from pathlib import Path

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
import torch
from transformers import AutoProcessor, AutoModelForImageTextToText
from PIL import Image

PROJECT_ROOT = Path(r"{lab_root}\pre-submit\mlh_SIX")
MODEL_DIR    = Path(os.environ.get("MODEL_DIR", ""))
TEST_IMAGE   = Path(os.environ.get("TEST_IMAGE", ""))


def test_load_medgemma():
    model_id = str(MODEL_DIR)  # 指向本地目录
    
    print(f"Loading model and processor from {model_id}...")
    
    try:
        # 加载处理器
        processor = AutoProcessor.from_pretrained(model_id)
        
        # 加载模型
        # 使用 bfloat16 节省显存，device_map="auto" 自动分配 GPU/CPU
        model = AutoModelForImageTextToText.from_pretrained(
            model_id,
            torch_dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=True
        )
        
        print("Model and Processor loaded successfully!")
        print(f"Model device: {model.device}")
        
        # 准备一个简单的推断测试（使用本地图片）
        test_image_path = str(TEST_IMAGE)
        if os.path.exists(test_image_path):
            image = Image.open(test_image_path)
            
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image},
                        {"type": "text", "text": "Identify any visible medical conditions in this image."}
                    ]
                }
            ]
            
            print("Preparing inputs...")
            inputs = processor.apply_chat_template(
                messages, add_generation_prompt=True, tokenize=True,
                return_dict=True, return_tensors="pt"
            ).to(model.device, dtype=torch.bfloat16)
            
            input_len = inputs["input_ids"].shape[-1]
            
            print("Generating output...")
            with torch.inference_mode():
                generation = model.generate(**inputs, max_new_tokens=200, do_sample=False)
                generation = generation[0][input_len:]
            
            decoded = processor.decode(generation, skip_special_tokens=True)
            print("-" * 30)
            print("Model Output:")
            print(decoded)
            print("-" * 30)
        else:
            print(f"Test image not found at {test_image_path}, skipping inference test.")
            
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    test_load_medgemma()
