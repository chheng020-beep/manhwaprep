"""NSFW detection + pixelation for the in-editor censoring feature.

Pure image/data helpers (no Qt). Detection uses NudeNet, which is installed on
first use. `detect` accepts an injected detector so tests never touch the model
or the network."""
import os
import sys
import subprocess
import tempfile

import cv2
import numpy as np

# Facebook-restricted parts we auto-censor (NudeNet v3 class names).
LABELS = {
    "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
    "FEMALE_BREAST_EXPOSED", "BUTTOCKS_EXPOSED", "ANUS_EXPOSED",
}

_DETECTOR = None  # lazy NudeDetector singleton


def pixelate(region: np.ndarray, blocks: int = 10) -> np.ndarray:
    """Return a blocky mosaic of `region` (BGR), fully obscuring detail.
    Downscale to at most `blocks`x`blocks` then nearest-neighbour upscale."""
    h, w = region.shape[:2]
    if h < 2 or w < 2:
        return region.copy()
    bw = max(1, min(w, blocks))
    bh = max(1, min(h, blocks))
    small = cv2.resize(region, (bw, bh), interpolation=cv2.INTER_LINEAR)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


def _load_detector():
    global _DETECTOR
    if _DETECTOR is None:
        from nudenet import NudeDetector  # noqa: WPS433 (lazy: heavy import)
        _DETECTOR = NudeDetector()
    return _DETECTOR


def detect(bgr: np.ndarray, detector=None, min_score: float = 0.35) -> list:
    """Detect FB-restricted regions in a BGR image. Returns censor boxes
    [{"x","y","w","h","source":"auto"}, ...]. `detector` is injectable for
    tests; when None the real NudeNet model is loaded lazily. NudeNet reads a
    file path, so a temp PNG is written for the real detector."""
    det = detector if detector is not None else _load_detector()
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        cv2.imwrite(tmp, bgr)
        results = det.detect(tmp)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    boxes = []
    for r in results:
        if r.get("class") in LABELS and r.get("score", 0) >= min_score:
            x, y, w, h = r["box"]
            boxes.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                          "source": "auto"})
    return boxes


def ensure_installed(parent=None) -> bool:
    """True if `nudenet` is importable, installing it into the current venv on
    first use. `parent` is an optional Qt widget for messages (unused here so
    the module stays Qt-free; the caller shows UI)."""
    try:
        import nudenet  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "nudenet"])
    except Exception:
        return False
    try:
        import nudenet  # noqa: F401
        return True
    except ImportError:
        return False
