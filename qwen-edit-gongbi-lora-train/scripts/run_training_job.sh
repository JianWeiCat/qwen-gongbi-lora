#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/featurize/work/qwen-gongbi-lora}"
DIFFSYNTH_DIR="${DIFFSYNTH_DIR:-/home/featurize/work/DiffSynth-Studio}"
CONDA_SH="${CONDA_SH:-/environment/miniconda3/etc/profile.d/conda.sh}"
CONDA_ENV="${CONDA_ENV:-qwen-gongbi}"

DATASET_BASE_PATH="${DATASET_BASE_PATH:-$PROJECT_DIR/data/gongbi_v1}"
DATASET_METADATA_PATH="${DATASET_METADATA_PATH:-$PROJECT_DIR/data/gongbi_v1/metadata.json}"
OUTPUT_PATH="${OUTPUT_PATH:-$PROJECT_DIR/outputs/lora_v1}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"

if [[ ! -f "$CONDA_SH" ]]; then
  echo "conda profile not found: $CONDA_SH" >&2
  exit 1
fi

if [[ ! -d "$DIFFSYNTH_DIR" ]]; then
  echo "DiffSynth-Studio directory not found: $DIFFSYNTH_DIR" >&2
  exit 1
fi

if [[ ! -f "$PROJECT_DIR/scripts/train_gongbi_lora.sh" ]]; then
  echo "project training script not found: $PROJECT_DIR/scripts/train_gongbi_lora.sh" >&2
  exit 1
fi

if [[ ! -f "$DATASET_METADATA_PATH" ]]; then
  echo "metadata not found: $DATASET_METADATA_PATH" >&2
  exit 1
fi

mkdir -p "$LOG_DIR" "$OUTPUT_PATH"

# shellcheck source=/dev/null
source "$CONDA_SH"
conda activate "$CONDA_ENV"

cd "$DIFFSYNTH_DIR"

RUN_ID="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/train_${RUN_ID}.log"

echo "project: $PROJECT_DIR"
echo "diffsynth: $DIFFSYNTH_DIR"
echo "conda env: $CONDA_ENV"
echo "dataset: $DATASET_BASE_PATH"
echo "metadata: $DATASET_METADATA_PATH"
echo "output: $OUTPUT_PATH"
echo "log: $LOG_FILE"

bash "$PROJECT_DIR/scripts/train_gongbi_lora.sh" \
  --dataset_base_path "$DATASET_BASE_PATH" \
  --dataset_metadata_path "$DATASET_METADATA_PATH" \
  --output_path "$OUTPUT_PATH" \
  "$@" 2>&1 | tee "$LOG_FILE"
