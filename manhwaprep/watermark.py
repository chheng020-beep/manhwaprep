"""Circular semi-transparent logo watermark stamped on exported pages/panels.

The logo lives at assets/logo.png. It is cropped to a soft-edged circle once
(cached), then resized and alpha-composited into a corner of each exported
image — visible enough to deter reposts, subtle enough not to fight the art.
"""
from __future__ import annotations

import os
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw

_LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "assets", "logo.png")

OPACITY = 0.45       # 0..1
SIZE_FRAC = 0.14     # badge diameter as a fraction of image width
MARGIN_FRAC = 0.03   # corner margin as a fraction of image width
MIN_D, MAX_D = 56, 240  # px clamps so tiny/huge panels stay sane


@lru_cache(maxsize=1)
def _badge_master() -> Image.Image | None:
    """The logo cropped to an antialiased circle, full opacity, RGBA."""
    try:
        logo = Image.open(_LOGO_PATH).convert("RGB")
    except Exception:
        return None
    side = min(logo.size)
    logo = logo.crop(((logo.width - side) // 2, (logo.height - side) // 2,
                      (logo.width + side) // 2, (logo.height + side) // 2))
    big = side * 4  # draw the mask oversized then shrink for smooth edges
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    logo.putalpha(mask.resize((side, side), Image.LANCZOS))
    return logo


def stamp(img: Image.Image, corner: str = "br") -> Image.Image:
    """Return img with the circular logo composited into a corner
    (br/bl/tr/tl). Preserves the input mode. No-op if the logo is missing
    or the image is too small to carry a readable badge."""
    master = _badge_master()
    if master is None:
        return img
    w, h = img.size
    d = int(max(MIN_D, min(MAX_D, w * SIZE_FRAC)))
    d = min(d, w // 3, h // 3)  # never dominate a tiny panel
    if d < 24:
        return img
    m = int(round(w * MARGIN_FRAC))
    badge = master.resize((d, d), Image.LANCZOS)
    badge.putalpha(badge.getchannel("A").point(lambda v: int(v * OPACITY)))
    x = m if corner in ("bl", "tl") else w - d - m
    y = m if corner in ("tr", "tl") else h - d - m
    mode = img.mode
    out = img if mode == "RGBA" else img.convert("RGBA")
    out.alpha_composite(badge, (x, y))
    return out if mode == "RGBA" else out.convert(mode)


def stamp_bgr(bgr: np.ndarray, corner: str = "br") -> np.ndarray:
    """OpenCV-style H×W×3 BGR array in, watermarked copy out."""
    pil = Image.fromarray(np.ascontiguousarray(bgr[:, :, ::-1]))
    return np.asarray(stamp(pil, corner))[:, :, ::-1].copy()


def stamp_files(paths: list[str], corner: str = "br"):
    """Watermark already-written image files in place (JPGs re-saved q92)."""
    for p in paths:
        try:
            img = Image.open(p)
            img.load()
            out = stamp(img, corner)
            if p.lower().endswith((".jpg", ".jpeg")):
                out.convert("RGB").save(p, "JPEG", quality=92)
            else:
                out.save(p)
        except Exception:
            continue  # a failed stamp must never lose an exported panel
