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
ART_MIN = 0.012   # min fraction of non-text "drawn detail" for a panel to stand alone


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
    slices = [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]
    return _merge_lonely(slices, gray, forbidden, min_h)


def _art_score(gray: np.ndarray, y0: int, y1: int, forbidden: np.ndarray) -> float:
    """Fraction of a slice that is actual drawn artwork — i.e. detailed pixels
    OUTSIDE the text rows. A lone speech bubble (flat background + text) scores
    near zero; a panel with scenery/characters scores high."""
    region = gray[y0:y1]
    if region.shape[0] < 2:
        return 0.0
    gx = cv2.Sobel(region, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(region, cv2.CV_32F, 0, 1)
    detail = (np.abs(gx) + np.abs(gy)) > 30
    keep = ~forbidden[y0:y1]  # drop text rows so their edges don't read as "art"
    if keep.sum() == 0:
        return 0.0
    return float(detail[keep, :].mean())


def _merge_lonely(slices, gray, forbidden, min_h):
    """Merge any panel that is too short OR is just a text bubble with no real
    artwork into a neighbour, so every posted image carries some art and the
    story flows. Merges toward the neighbour that already has more art."""
    if len(slices) <= 1:
        return slices
    changed = True
    while changed and len(slices) > 1:
        changed = False
        scores = [_art_score(gray, a, b, forbidden) for a, b in slices]
        for i, (a, b) in enumerate(slices):
            lonely = (b - a) < min_h or scores[i] < ART_MIN
            if not lonely:
                continue
            if i == 0:
                j = 1
            elif i == len(slices) - 1:
                j = i - 1
            else:
                j = i - 1 if scores[i - 1] >= scores[i + 1] else i + 1
            lo = min(slices[i][0], slices[j][0])
            hi = max(slices[i][1], slices[j][1])
            k = min(i, j)
            slices = slices[:k] + [(lo, hi)] + slices[max(i, j) + 1:]
            changed = True
            break
    return slices


IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def _gather_images(source: str) -> list[str]:
    if os.path.isfile(source):
        return [source]
    if os.path.isdir(source):
        return [
            os.path.join(source, f)
            for f in sorted(os.listdir(source))
            if f.lower().endswith(IMAGE_EXTS)
        ]
    return []


def split_source(
    source: str,
    out_dir: str | None = None,
    detect: bool = True,
    on_status=None,
    on_progress=None,
    control=None,
) -> tuple[str, list[str]]:
    """Standalone splitter: split a single image OR every image in a folder into
    Facebook panels. When `detect`, run the RT-DETR detector so cuts avoid speech
    bubbles / SFX even though we have no typeset boxes to go by. No download, no
    cleaning — just safe panel cuts. Returns (out_dir, panel_paths)."""
    images = _gather_images(source)
    if not images:
        raise RuntimeError("No image(s) to split (need a file or a folder of images).")

    det = None
    if detect:
        from .comicdetector import ComicDetector

        if on_status:
            on_status("Loading text detector…")
        det = ComicDetector()

    if out_dir is None:
        root = os.path.dirname(images[0]) if os.path.isfile(source) else source
        out_dir = os.path.join(root, "fb_panels")

    paths: list[str] = []
    for i, img_path in enumerate(images, 1):
        if control is not None:
            control.checkpoint()
        im = cv2.imread(img_path)
        if im is None:
            if on_status:
                on_status(f"  ! unreadable, skipped: {os.path.basename(img_path)}")
            continue
        protect: list[tuple[float, float]] = []
        if det is not None:
            res = det.detect(im)
            for boxes in res.values():
                for x1, y1, x2, y2 in boxes:
                    protect.append((y1, y2))
        slices = split_panels(im, protect=protect)
        stem = os.path.splitext(os.path.basename(img_path))[0]
        wrote = write_panels(im, slices, out_dir, prefix=stem)
        paths.extend(wrote)
        if on_status:
            note = f" ({len(protect)} text region(s) protected)" if det else ""
            on_status(f"  {os.path.basename(img_path)} → {len(wrote)} panel(s){note}")
        if on_progress:
            on_progress("split", i, len(images))
    if not paths:
        raise RuntimeError("Nothing was split.")
    return out_dir, paths


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
