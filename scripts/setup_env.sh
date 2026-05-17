#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.10}"
VENV_DIR="${VENV_DIR:-.venv}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$PYTHON_BIN" -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip
if [[ -f "pyproject.toml" || -f "setup.py" ]]; then
  pip install -e .
else
  echo "Skipping editable install: run from DiffSynth-Studio root to install DiffSynth itself."
fi
pip install -r "$PROJECT_DIR/requirements.txt"
pip install accelerate modelscope safetensors

echo "Environment ready. Activate with: source $VENV_DIR/bin/activate"
