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


def onnx_providers() -> list[str]:
    """Prefer GPU (CUDA / DirectML) when available, always fall back to CPU.

    On Windows with onnxruntime-directml this returns DmlExecutionProvider so the
    GPU is used; on the Mac (CPU-only onnxruntime) it returns just CPU.
    """
    try:
        import onnxruntime as ort

        avail = set(ort.get_available_providers())
    except Exception:
        return ["CPUExecutionProvider"]
    out = [p for p in ("CUDAExecutionProvider", "DmlExecutionProvider") if p in avail]
    out.append("CPUExecutionProvider")
    return out


def make_session(model_path: str, sess_options=None):
    """Create an ONNX session on the best provider, falling back to CPU."""
    import onnxruntime as ort

    try:
        return ort.InferenceSession(
            model_path, sess_options, providers=onnx_providers()
        )
    except Exception:
        return ort.InferenceSession(
            model_path, sess_options, providers=["CPUExecutionProvider"]
        )


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

# The minimum needed for the core cleaner: detection + MI-GAN (fast) + LaMa
# (best, GPU-accelerated when available).
CORE_MODELS = [
    "detector_int8.onnx",
    "migan_pipeline_v2.onnx",
    "lama_fp32.onnx",
]
