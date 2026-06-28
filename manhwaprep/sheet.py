"""Numbered overlay rendering for the transcript export (boxes + indices)."""

from __future__ import annotations

import cv2
import numpy as np


def build_overlay(img: np.ndarray, bubbles: list[dict]) -> np.ndarray:
    ov = img.copy()
    for b in bubbles:
        x, y, w, h = b["bbox"]
        cv2.rectangle(ov, (x, y), (x + w, y + h), (0, 0, 255), 2)
        label = str(b["n"])
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
        cv2.rectangle(ov, (x, max(0, y - th - 6)), (x + tw + 6, y), (0, 0, 255), -1)
        cv2.putText(
            ov, label, (x + 3, max(th, y - 4)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2,
        )
    return ov
