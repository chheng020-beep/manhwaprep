"""Cut a tall typeset canvas into Facebook-friendly panels at safe seams.

A "safe" cut is a horizontal row that

  1. does NOT pass through any protected rectangle — the Khmer text boxes, whose
     pixel positions the editor knows exactly, so we never need detection; and
  2. lands in low-detail artwork — ideally a gutter band between panels (the flat
     white/black strips webtoons use) rather than the middle of a drawing.

Sizing is flexible: each panel's height is chosen freely inside a [min, max]
window derived from the image width, so the aspect ratio stays Facebook-friendly
without being rigid. When no clean gutter exists in the window, we still cut at
the least-busy row (never through a text box) so panels don't grow unbounded.

Pure NumPy/OpenCV — no Qt — so it is unit-testable in isolation.
"""

from __future__ import annotations

import os

import cv2
import numpy as np

# Facebook over-compresses extreme ratios, so cap each panel relative to width.
MAX_RATIO = 2.5   # max panel height = 2.5 * width
IDEAL_RATIO = 1.4
MIN_RATIO = 0.6
FORBID_MARGIN = 4  # px of breathing room kept around each text box


def _row_cost(gray: np.ndarray) -> np.ndarray:
    """Per-row cost of slicing there. Low in flat bands (gutters / plain colour
    fills), high through busy artwork. Length == image height."""
    # spread of values along the row: a solid gutter band is ~0, art is high.
    var = gray.var(axis=1)
    # average horizontal detail along the row (texture, line art).
    grad = np.abs(np.diff(gray.astype(np.int32), axis=1)).mean(axis=1)
    cost = var / 255.0 + grad
    # smooth vertically so the centre of a multi-row gutter scores best.
    k = np.ones(5) / 5.0
    return np.convolve(cost, k, mode="same")


def split_panels(
    image_bgr: np.ndarray,
    protect: list[tuple[float, float]] | None = None,
    min_h: int | None = None,
    max_h: int | None = None,
    ideal_h: int | None = None,
) -> list[tuple[int, int]]:
    """Return a list of (y0, y1) slices covering the whole image top-to-bottom.

    protect : (y0, y1) row ranges that a cut must never fall inside (text boxes).
    min_h/max_h/ideal_h : panel-height window in px (defaults scale with width).
    """
    H, W = image_bgr.shape[:2]
    max_h = int(max_h or W * MAX_RATIO)
    ideal_h = int(ideal_h or W * IDEAL_RATIO)
    min_h = int(min_h or W * MIN_RATIO)
    min_h = max(1, min(min_h, max_h))
    ideal_h = max(min_h, min(ideal_h, max_h))

    if H <= max_h:
        return [(0, H)]

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    cost = _row_cost(gray)

    forbidden = np.zeros(H, dtype=bool)
    for y0, y1 in protect or []:
        a = max(0, int(y0) - FORBID_MARGIN)
        b = min(H, int(y1) + FORBID_MARGIN)
        forbidden[a:b] = True

    cuts = [0]
    pos = 0
    while H - pos > max_h:
        lo = pos + min_h
        # keep the final panel >= min_h by not cutting in the last min_h rows.
        hi = min(pos + max_h, H - min_h)
        if hi <= lo:
            break  # remainder too small to split — leave one last panel

        window = np.arange(lo, hi)
        c = cost[lo:hi].astype(np.float64).copy()
        c[forbidden[lo:hi]] = np.inf

        if not np.isfinite(c).any():
            # a text box covers the whole window: grow the panel to the first
            # free row below it rather than ever cutting the text.
            free = np.where(~forbidden[hi:])[0]
            if free.size == 0:
                break
            best = hi + int(free[0])
        else:
            # gentle pull toward the ideal height so ties resolve sensibly,
            # but a clearly cleaner seam still wins.
            finite = c[np.isfinite(c)]
            spread = float(finite.max() - finite.min()) or 1.0
            dist = np.abs(window - (pos + ideal_h)) / max(1, hi - lo)
            best = lo + int(np.argmin(c + 0.25 * spread * dist))

        if best <= pos:  # safety: never stall
            best = min(pos + max_h, H)
        cuts.append(best)
        pos = best

    cuts.append(H)
    return [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]


def write_panels(
    image_bgr: np.ndarray,
    slices: list[tuple[int, int]],
    out_dir: str,
    prefix: str = "panel",
    quality: int = 92,
) -> list[str]:
    """Write each slice as a JPG; returns the written paths in order."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for i, (y0, y1) in enumerate(slices, 1):
        crop = image_bgr[y0:y1]
        path = os.path.join(out_dir, f"{prefix}_{i:03d}.jpg")
        cv2.imwrite(path, crop, [cv2.IMWRITE_JPEG_QUALITY, quality])
        paths.append(path)
    return paths
