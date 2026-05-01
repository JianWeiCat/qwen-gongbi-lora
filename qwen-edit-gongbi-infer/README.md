# Qwen-Image-Edit-2511 Gongbi LoRA Inference

这个目录用于在远程 GPU 或推理机上加载 Qwen-Image-Edit-2511 和工笔画 LoRA，执行单张或批量图片转换。

## 单张推理

```bash
python app/infer.py \
  --input inputs/example.png \
  --lora-path models/lora/gongbi_v1.safetensors \
  --output outputs/example_gongbi.png
```

## 批量推理

```bash
python app/batch_infer.py \
  --input-dir inputs \
  --lora-path models/lora/gongbi_v1.safetensors \
  --output-dir outputs
```

脚本需要在已安装 DiffSynth-Studio 的 Python 环境中运行。

