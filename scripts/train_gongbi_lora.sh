#!/usr/bin/env bash
set -euo pipefail

DATASET_BASE_PATH="${DATASET_BASE_PATH:-data/gongbi_v1}"
DATASET_METADATA_PATH="${DATASET_METADATA_PATH:-data/gongbi_v1/metadata.json}"
OUTPUT_PATH="${OUTPUT_PATH:-./models/train/Qwen-Image-Edit-2511_gongbi_lora}"
MAX_PIXELS="${MAX_PIXELS:-786432}"
DATASET_REPEAT="${DATASET_REPEAT:-50}"
LEARNING_RATE="${LEARNING_RATE:-1e-4}"
NUM_EPOCHS="${NUM_EPOCHS:-5}"
LORA_RANK="${LORA_RANK:-16}"
DATASET_NUM_WORKERS="${DATASET_NUM_WORKERS:-8}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dataset_base_path)
      DATASET_BASE_PATH="$2"
      shift 2
      ;;
    --dataset_metadata_path)
      DATASET_METADATA_PATH="$2"
      shift 2
      ;;
    --output_path)
      OUTPUT_PATH="$2"
      shift 2
      ;;
    --max_pixels)
      MAX_PIXELS="$2"
      shift 2
      ;;
    --dataset_repeat)
      DATASET_REPEAT="$2"
      shift 2
      ;;
    --learning_rate)
      LEARNING_RATE="$2"
      shift 2
      ;;
    --num_epochs)
      NUM_EPOCHS="$2"
      shift 2
      ;;
    --lora_rank)
      LORA_RANK="$2"
      shift 2
      ;;
    --dataset_num_workers)
      DATASET_NUM_WORKERS="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "examples/qwen_image/model_training/train.py" ]]; then
  echo "Run this script from the DiffSynth-Studio repository root." >&2
  echo "Current directory: $(pwd)" >&2
  exit 1
fi

if [[ ! -f "$DATASET_METADATA_PATH" ]]; then
  echo "metadata not found: $DATASET_METADATA_PATH" >&2
  exit 1
fi

mkdir -p "$OUTPUT_PATH"

accelerate launch examples/qwen_image/model_training/train.py \
  --dataset_base_path "$DATASET_BASE_PATH" \
  --dataset_metadata_path "$DATASET_METADATA_PATH" \
  --data_file_keys "image,edit_image" \
  --extra_inputs "edit_image" \
  --max_pixels "$MAX_PIXELS" \
  --dataset_repeat "$DATASET_REPEAT" \
  --model_id_with_origin_paths "Qwen/Qwen-Image-Edit-2511:transformer/diffusion_pytorch_model*.safetensors,Qwen/Qwen-Image:text_encoder/model*.safetensors,Qwen/Qwen-Image:vae/diffusion_pytorch_model.safetensors" \
  --learning_rate "$LEARNING_RATE" \
  --num_epochs "$NUM_EPOCHS" \
  --remove_prefix_in_ckpt "pipe.dit." \
  --output_path "$OUTPUT_PATH" \
  --lora_base_model "dit" \
  --lora_target_modules "to_q,to_k,to_v,add_q_proj,add_k_proj,add_v_proj,to_out.0,to_add_out,img_mlp.net.2,img_mod.1,txt_mlp.net.2,txt_mod.1" \
  --lora_rank "$LORA_RANK" \
  --use_gradient_checkpointing \
  --dataset_num_workers "$DATASET_NUM_WORKERS" \
  --find_unused_parameters \
  --zero_cond_t
