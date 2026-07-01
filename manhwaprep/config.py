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


class _RobustSession:
    """ONNX session that runs on GPU but auto-falls-back to CPU if a node
    errors at inference (e.g. LaMa's Fast-Fourier-Convolution layers aren't
    supported by DirectML). Proxies the rest of the session API.
    """

    def __init__(self, model_path, sess_options=None, force_cpu=False):
        import onnxruntime as ort

        object.__setattr__(self, "_path", model_path)
        object.__setattr__(self, "_opts", sess_options)
        provs = ["CPUExecutionProvider"] if force_cpu else onnx_providers()
        try:
            s = ort.InferenceSession(model_path, sess_options, providers=provs)
        except Exception:
            s = ort.InferenceSession(
                model_path, sess_options, providers=["CPUExecutionProvider"]
            )
            force_cpu = True
        object.__setattr__(self, "_s", s)
        object.__setattr__(self, "_cpu_only", force_cpu)

    def run(self, output_names, input_feed):
        try:
            return self._s.run(output_names, input_feed)
        except Exception:
            if self._cpu_only:
                raise
            import onnxruntime as ort

            s = ort.InferenceSession(
                self._path, self._opts, providers=["CPUExecutionProvider"]
            )
            object.__setattr__(self, "_s", s)
            object.__setattr__(self, "_cpu_only", True)
            return self._s.run(output_names, input_feed)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_s"), name)


def make_session(model_path: str, sess_options=None, force_cpu=False):
    """Create a GPU-preferred session with automatic CPU fallback."""
    return _RobustSession(model_path, sess_options, force_cpu=force_cpu)


def default_output_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        return os.path.join(base, "ManhwaPrep", "output")
    return os.path.expanduser("~/Desktop/ManhwaPrep/output")


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
