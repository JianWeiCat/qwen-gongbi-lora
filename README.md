# Qwen-Image-Edit-2511 Gongbi LoRA Training

这个项目用于为 Qwen-Image-Edit-2511 训练中国传统工笔画风格 LoRA。完整链路包括：

```text
开放授权原图收集 / 手动导入
        |
        v
豆包 SeedEdit 批量工笔风转换
        |
        v
人工审核候选图
        |
        v
导出原图+工笔图成对训练集
        |
        v
DiffSynth-Studio 启动 LoRA 训练
```

图片、候选图、审核结果、训练输出默认不进入 Git，只提交脚本、配置模板和占位文件。

## 目录

```text
configs/
  pipeline_gongbi_v1.json.example     # 数据生产配置模板
  train_gongbi_v1.env.example         # 训练参数模板
data/gongbi_v1/
  raw/images/                         # 原始素材图，不入 Git
  candidates/images/                  # API 生成候选图，不入 Git
  manifests/*.jsonl                   # 数据生产与审核记录，不入 Git
  images/                             # 最终训练图，不入 Git
  metadata.json                       # DiffSynth 训练 metadata，不入 Git
scripts/
  collect_openverse.py                # Openverse 收集原图
  import_raw_assets.py                # 手动导入原图
  generate_doubao_candidates.py       # 豆包批量生成工笔候选
  review_candidates.py                # HTML 审核服务
  build_dataset.py                    # 导出训练集
  train_gongbi_lora.sh                # DiffSynth 训练入口封装
  run_training_job.sh                 # 远程训练启动脚本
qwen-edit-gongbi-infer/
  app/                                # 推理脚本
  inputs/                             # 推理输入，不入 Git
  models/                             # 本地模型或 LoRA，不入 Git
  outputs/                            # 推理输出，不入 Git
```

## 本地与远程分工

本地 Mac 只做轻量工作：

```text
整理 paired images
生成 metadata.json
检查图片配对与字段
维护训练/推理脚本
提交代码到 GitHub
```

不要在本地下载 Qwen-Image-Edit-2511 大模型，也不要尝试正式 CUDA 训练。

远程 GPU 服务器负责：

```text
安装 DiffSynth-Studio
下载 Qwen/Qwen-Image-Edit-2511 与 Qwen/Qwen-Image
运行 LoRA 训练
运行验证推理
保存 LoRA 与 validation 输出
```

推荐环境：

```text
Ubuntu 22.04
Python 3.10
CUDA 12.x
L40S 48GB / A100 40GB
```

## 1. 准备配置

```bash
cp configs/pipeline_gongbi_v1.json.example configs/pipeline_gongbi_v1.json
```

默认目标是 500 对最终素材：

```text
person: 200
landscape: 200
other: 100
```

每张原图默认生成 2 张候选。

## 2. 收集或导入原图

从 Openverse 收集开放授权图片：

```bash
python scripts/collect_openverse.py \
  --config configs/pipeline_gongbi_v1.json \
  --dataset-dir data/gongbi_v1
```

手动导入远程已有图片：

```bash
python scripts/import_raw_assets.py \
  --input-dir /path/to/source_images \
  --category person \
  --dataset-dir data/gongbi_v1
```

原图记录写入：

```text
data/gongbi_v1/manifests/raw_assets.jsonl
```

## 3. 调用豆包生成候选图

设置火山方舟 API Key：

```bash
export ARK_API_KEY="your_api_key"
```

生成候选图：

```bash
python scripts/generate_doubao_candidates.py \
  --config configs/pipeline_gongbi_v1.json \
  --dataset-dir data/gongbi_v1
```

离线自测可使用 mock 模式，不会调用 API：

```bash
python scripts/generate_doubao_candidates.py \
  --config configs/pipeline_gongbi_v1.json \
  --dataset-dir data/gongbi_v1 \
  --mock \
  --limit 6
```

候选记录写入：

```text
data/gongbi_v1/manifests/candidates.jsonl
```

## 4. 人工审核

远程服务器启动审核服务：

```bash
python scripts/review_candidates.py \
  --dataset-dir data/gongbi_v1 \
  --host 127.0.0.1 \
  --port 7860
```

本地终端做 SSH 端口转发：

```bash
ssh -L 7860:127.0.0.1:7860 workspace.featurize.cn
```

本地浏览器打开：

```text
http://127.0.0.1:7860
```

审核界面示意：

```text
+-----------------------------------------------------------+
| 类别 person | 原图来源 | 授权 | 进度 123 / 1000          |
+----------------------+----------------------+-------------+
| 原图                 | 候选 1               | 候选 2      |
| [image]              | [image]              | [image]     |
+----------------------+----------------------+-------------+
| [Accept 1] [Accept 2] [Reject] [Maybe] [原因选择]        |
+-----------------------------------------------------------+
```

审核记录写入：

```text
data/gongbi_v1/manifests/reviews.jsonl
```

## 5. 导出训练集

```bash
python scripts/build_dataset.py \
  --config configs/pipeline_gongbi_v1.json \
  --dataset-dir data/gongbi_v1 \
  --clear-output
```

导出结果：

```text
data/gongbi_v1/images/0001_input.png
data/gongbi_v1/images/0001_gongbi.png
data/gongbi_v1/metadata.json
data/gongbi_v1/manifests/dataset_manifest.jsonl
```

检查数据：

```bash
python scripts/check_dataset.py \
  --dataset-dir data/gongbi_v1
```

## 6. 远程训练

训练需要在已安装 DiffSynth-Studio 的环境中运行。默认远程路径：

```text
项目目录: /home/featurize/work/qwen-gongbi-lora
DiffSynth: /home/featurize/work/DiffSynth-Studio
conda env: qwen-gongbi
```

启动训练：

```bash
PROJECT_DIR=/home/featurize/work/qwen-gongbi-lora \
DIFFSYNTH_DIR=/home/featurize/work/DiffSynth-Studio \
CONDA_ENV=qwen-gongbi \
bash scripts/run_training_job.sh \
  --max_pixels 786432 \
  --dataset_repeat 20 \
  --num_epochs 3 \
  --lora_rank 16
```

训练日志写入：

```text
logs/train_YYYYmmdd_HHMMSS.log
```

## 7. 验证 LoRA

```bash
python scripts/validate_lora.py \
  --lora-path outputs/lora_v1/epoch-2.safetensors \
  --input-dir validation_inputs \
  --output-dir outputs/validation
```

验证脚本需要在已经安装 DiffSynth-Studio 的 Python 环境中运行。

## 关键训练参数

DiffSynth-Studio 的 Qwen-Image-Edit-2511 LoRA 示例使用：

```text
data_file_keys: image,edit_image
extra_inputs: edit_image
lora_base_model: dit
zero_cond_t: enabled
```

`zero_cond_t` 是 Qwen-Image-Edit-2511 训练和验证时需要保留的参数。

## 快速本地自测

```bash
python -m py_compile scripts/*.py
bash -n scripts/*.sh
python scripts/check_dataset.py --dataset-dir data/gongbi_v1 --skip-image-open
```
