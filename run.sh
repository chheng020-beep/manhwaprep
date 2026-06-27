#!/bin/bash
# Launch ManhwaPrep using EasyScanlate's existing venv (has all deps + models).
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$HOME/EasyScanlate/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "EasyScanlate venv not found at $PY" >&2
  echo "Edit run.sh to point PY at a python with rapidocr/onnxruntime/opencv/PySide6." >&2
  exit 1
fi
cd "$DIR"
exec "$PY" -m manhwaprep "$@"
