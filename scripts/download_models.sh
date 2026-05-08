#!/usr/bin/env bash
set -euo pipefail

LOCAL_DIR="${LOCAL_DIR:-./models/base}"

mkdir -p "$LOCAL_DIR"

modelscope download --model Qwen/Qwen-Image-Edit-2511 \
  --include "transformer/diffusion_pytorch_model*.safetensors" \
  --local_dir "$LOCAL_DIR/Qwen-Image-Edit-2511"

modelscope download --model Qwen/Qwen-Image \
  --include "text_encoder/model*.safetensors" \
  --include "vae/diffusion_pytorch_model.safetensors" \
  --local_dir "$LOCAL_DIR/Qwen-Image"

modelscope download --model Qwen/Qwen-Image-Edit \
  --include "processor/*" \
  --local_dir "$LOCAL_DIR/Qwen-Image-Edit"

