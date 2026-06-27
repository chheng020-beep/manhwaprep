"""Comic-text-detector: a manga-specific text segmentation model (ONNX).

Unlike a generic OCR detector, this is trained on manga/comics and segments
both bubble dialogue AND stylized sound-effect / action text. We use its `seg`
output (a text mask) and union it with the OCR stroke mask so SFX that PP-OCR
misses still gets erased.

Model: mayocream/comic-text-detector-onnx (fixed 1024x1024 input).
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import onnxruntime as ort

from . import config

DEFAULT_CTD_MODEL = config.model_path("comic-text-detector.onnx")
_SIZE = 1024


class ComicTextDetector:
    def __init__(self, model_path: str | None = None, thresh: float = 0.3):
        self.model_path = model_path or DEFAULT_CTD_MODEL
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(self.model_path)
        self.thresh = thresh
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = config.make_session(self.model_path, opts)

    def mask(self, img_bgr: np.ndarray) -> np.ndarray:
        """Return a binary text mask (uint8 0/255) at the image's own size."""
        h, w = img_bgr.shape[:2]
        scale = _SIZE / max(h, w)
        nh, nw = int(round(h * scale)), int(round(w * scale))
        canvas = np.zeros((_SIZE, _SIZE, 3), np.uint8)
        canvas[:nh, :nw] = cv2.resize(img_bgr, (nw, nh))
        blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        # outputs: blk, seg, det — seg is the text segmentation map (0..1)
        seg = self.sess.run(None, {"images": blob})[1][0, 0]
        seg = seg[:nh, :nw]
        seg = cv2.resize(seg, (w, h))
        return (seg > self.thresh).astype(np.uint8) * 255
