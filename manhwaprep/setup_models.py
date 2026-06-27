"""First-run model downloader.

The app ships without models (keeps the .exe small); on first launch it fetches
the core ONNX models into the per-user models dir. Used by the GUI's download
window and callable headless.
"""

from __future__ import annotations

import os

import requests

from . import config


def missing_core_models() -> list[str]:
    return [
        n for n in config.CORE_MODELS
        if not os.path.exists(config.model_path(n))
    ]


def download_model(name: str, on_progress=None, on_status=None):
    """Download one model by name into the models dir."""
    url = config.MODEL_URLS[name]
    dest = config.model_path(name)
    os.makedirs(config.MODELS_DIR, exist_ok=True)
    if on_status:
        on_status(f"Downloading {name}…")
    r = requests.get(url, stream=True, timeout=120)
    r.raise_for_status()
    total = int(r.headers.get("content-length", 0))
    done = 0
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if on_progress and total:
                on_progress(name, done, total)
    os.replace(tmp, dest)


def ensure_core_models(on_progress=None, on_status=None):
    """Download any missing core models. Returns the list that was fetched."""
    fetched = []
    for name in missing_core_models():
        download_model(name, on_progress=on_progress, on_status=on_status)
        fetched.append(name)
    return fetched
