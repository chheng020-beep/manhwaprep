"""Cross-platform paths and model locations.

Models are NOT bundled into the app — they download on first run into a
per-user data dir (so the Windows .exe stays small). Override the location with
the MANHWAPREP_MODELS environment variable.
"""

from __future__ import annotations

import os
import sys


def _default_models_dir() -> str:
    env = os.environ.get("MANHWAPREP_MODELS")
    if env:
        return env
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ManhwaPrep", "models")
    # macOS / Linux keep the existing location for continuity
    return os.path.expanduser("~/ManhwaPrep/models")


MODELS_DIR = _default_models_dir()


def model_path(name: str) -> str:
    return os.path.join(MODELS_DIR, name)


def default_output_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        return os.path.join(base, "ManhwaPrep", "output")
    return os.path.expanduser("~/ManhwaPrep/output")


# Direct-download URLs (HuggingFace) for the models the app fetches on first run.
MODEL_URLS = {
    "detector_int8.onnx": (
        "https://huggingface.co/ogkalu/comic-text-and-bubble-detector/"
        "resolve/main/detector_int8.onnx"
    ),
    "migan_pipeline_v2.onnx": (
        "https://huggingface.co/andraniksargsyan/migan/"
        "resolve/main/migan_pipeline_v2.onnx"
    ),
    "lama_fp32.onnx": (
        "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx"
    ),
    "comic-text-detector.onnx": (
        "https://huggingface.co/mayocream/comic-text-detector-onnx/"
        "resolve/main/comic-text-detector.onnx"
    ),
}

# The minimum needed for the core cleaner (detection + fast inpaint).
CORE_MODELS = ["detector_int8.onnx", "migan_pipeline_v2.onnx"]
