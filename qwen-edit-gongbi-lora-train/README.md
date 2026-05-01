# Qwen-Image-Edit-2511 Gongbi LoRA Training

这个目录用于准备 Qwen-Image-Edit-2511 工笔画风格 LoRA 的训练数据、配置和启动脚本。

## 本地数据准备

把图片放到：

```text
data/gongbi_v1/images/
```

命名规则：

```text
0001_input.png
0001_gongbi.png
0002_input.png
0002_gongbi.png
```

生成 metadata：

```bash
python scripts/prepare_metadata.py \
  --dataset-dir data/gongbi_v1
```

检查数据：

```bash
python scripts/check_dataset.py \
  --dataset-dir data/gongbi_v1
```

## 远程训练

在远程 GPU 服务器安装 DiffSynth-Studio 后，从 DiffSynth-Studio 根目录运行本项目脚本：

```bash
export PROJECT_DIR=/path/to/qwen-gongbi-lora/qwen-edit-gongbi-lora-train

bash "$PROJECT_DIR/scripts/train_gongbi_lora.sh" \
  --dataset_base_path "$PROJECT_DIR/data/gongbi_v1" \
  --dataset_metadata_path "$PROJECT_DIR/data/gongbi_v1/metadata.json" \
  --output_path "$PROJECT_DIR/outputs/lora_v1" \
  --max_pixels 786432 \
  --learning_rate 1e-4 \
  --num_epochs 5 \
  --lora_rank 16
```

脚本默认调用：

```text
examples/qwen_image/model_training/train.py
```

并固定启用：

```text
--data_file_keys image,edit_image
--extra_inputs edit_image
--lora_base_model dit
--zero_cond_t
```

## 验证 LoRA

```bash
python scripts/validate_lora.py \
  --lora-path outputs/lora_v1/epoch-4.safetensors \
  --input-dir validation_inputs \
  --output-dir outputs/validation
```

验证脚本需要在已经安装 DiffSynth-Studio 的 Python 环境中运行。

