"""Stitch cleaned pages into a small number of long vertical images.

Strategy (the one you asked for): normalize every page to a common width,
glue them all into one tall strip, then re-cut into ~N equal-height long
images. A safety cap keeps any single output from getting absurdly tall, so
a huge chapter may produce a few more than N rather than one giant file.
"""

from __future__ import annotations

import os
from collections import Counter

import cv2
import numpy as np


def _common_width(images: list[np.ndarray]) -> int:
    return Counter(im.shape[1] for im in images).most_common(1)[0][0]


def stitch(
    image_paths: list[str],
    out_dir: str,
    target_segments: int = 5,
    max_height: int = 12000,
    jpg_quality: int = 92,
    progress=None,
) -> list[str]:
    """Stitch images at image_paths into long images written to out_dir.

    Returns the list of output file paths.
    """
    images = []
    for p in image_paths:
        im = cv2.imread(p, cv2.IMREAD_COLOR)
        if im is not None:
            images.append(im)
    if not images:
        raise ValueError("No readable images to stitch.")

    target_w = _common_width(images)
    normalized = []
    for im in images:
        h, w = im.shape[:2]
        if w != target_w:
            new_h = max(1, round(h * target_w / w))
            im = cv2.resize(im, (target_w, new_h), interpolation=cv2.INTER_AREA)
        normalized.append(im)

    big = np.vstack(normalized)
    total_h = big.shape[0]

    n_seg = max(target_segments, int(np.ceil(total_h / max_height)))
    seg_h = int(np.ceil(total_h / n_seg))

    os.makedirs(out_dir, exist_ok=True)
    outputs = []
    idx = 0
    for i in range(n_seg):
        y1 = i * seg_h
        y2 = min((i + 1) * seg_h, total_h)
        if y1 >= y2:
            break
        idx += 1
        out_path = os.path.join(out_dir, f"{idx:02d}.jpg")
        cv2.imwrite(out_path, big[y1:y2], [cv2.IMWRITE_JPEG_QUALITY, jpg_quality])
        outputs.append(out_path)
        if progress:
            progress(idx, n_seg)

    return outputs
