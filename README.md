# Qwen Gongbi LoRA

面向 Qwen-Image-Edit-2511 的中国传统工笔画风格 LoRA 项目。

本仓库只保存数据准备脚本、训练启动脚本、推理脚本和配置模板。训练数据、模型权重、LoRA 输出和日志默认不提交到 git。

## 项目结构

```text
qwen-edit-gongbi-lora-train/
  configs/
  data/
  scripts/
  outputs/
  logs/

qwen-edit-gongbi-infer/
  app/
  inputs/
  models/
  outputs/
```

## 本地负责

本地 Mac 只做轻量工作：

```text
整理 paired images
生成 metadata.json
检查图片配对与字段
维护训练/推理脚本
提交代码到 GitHub
```

不要在本地下载 Qwen-Image-Edit-2511 大模型，也不要尝试正式 CUDA 训练。

## 远程负责

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

## 第一阶段流程

1. 本地准备 100-200 对数据，命名为 `0001_input.png` / `0001_gongbi.png`。
2. 运行 `prepare_metadata.py` 生成 `metadata.json`。
3. 运行 `check_dataset.py` 检查 paired 数据。
4. 把仓库和数据上传到远程 GPU 服务器。
5. 在远程安装 DiffSynth-Studio。
6. 运行 `scripts/train_gongbi_lora.sh` 做 smoke test。
7. 用 `scripts/validate_lora.py` 生成 validation 输出。

## 数据格式

```text
qwen-edit-gongbi-lora-train/data/gongbi_v1/
  metadata.json
  images/
    0001_input.png
    0001_gongbi.png
```

`metadata.json`:

```json
[
  {
    "image": "images/0001_input.png",
    "edit_image": "images/0001_gongbi.png",
    "prompt": "Convert the input image into traditional Chinese gongbi painting style, preserving the original composition, subject details, and spatial structure."
  }
]
```

## 关键训练参数

DiffSynth-Studio 的 Qwen-Image-Edit-2511 LoRA 示例使用：

```text
data_file_keys: image,edit_image
extra_inputs: edit_image
lora_base_model: dit
zero_cond_t: enabled
```

`zero_cond_t` 是 Qwen-Image-Edit-2511 训练和验证时需要保留的参数。

