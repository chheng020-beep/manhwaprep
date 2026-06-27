"""Comic-translate's RT-DETR-v2 detector (ONNX), used as our detection engine.

Faithfully replicates comic-translate's ONNX inference (modules/detection/
rtdetr_v2_onnx.py): resize to 640x640, /255, NCHW, pass orig_target_sizes as
[[width, height]], confidence threshold 0.3. Boxes come back as [x1,y1,x2,y2]
in image coordinates.

Classes (from the model config):
  0 = bubble       (the speech balloon)
  1 = text_bubble  (dialogue text inside a bubble)
  2 = text_free    (free text outside bubbles = SFX / action text)

Trained on ~11k comic/manga/webtoon images, so it separates dialogue from SFX
far better than the hand-rolled heuristics it replaces.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import onnxruntime as ort

from . import config

DEFAULT_MODEL = config.model_path("detector_int8.onnx")
CLASS_NAMES = {0: "bubble", 1: "text_bubble", 2: "text_free"}

# tall pages are detected in slabs so 640x640 resize doesn't lose small text
SLAB_HEIGHT = 1600
SLAB_OVERLAP = 200
CONF = 0.3


class ComicDetector:
    def __init__(self, model_path: str | None = None, conf: float = CONF):
        self.model_path = model_path or DEFAULT_MODEL
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(self.model_path)
        self.conf = conf
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(
            self.model_path, opts, providers=["CPUExecutionProvider"]
        )

    @staticmethod
    def _slabs(height: int):
        if height <= SLAB_HEIGHT:
            return [(0, height)]
        slabs, y = [], 0
        while y < height:
            y2 = min(y + SLAB_HEIGHT, height)
            slabs.append((y, y2))
            if y2 >= height:
                break
            y = y2 - SLAB_OVERLAP
        return slabs

    def _infer(self, img_bgr: np.ndarray):
        h, w = img_bgr.shape[:2]
        rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        blob = cv2.resize(rgb, (640, 640)).astype(np.float32) / 255.0
        blob = np.transpose(blob, (2, 0, 1))[None]
        labels, boxes, scores = self.sess.run(
            None,
            {"images": blob, "orig_target_sizes": np.array([[w, h]], np.int64)},
        )
        labels = np.array(labels).reshape(-1)
        boxes = np.array(boxes).reshape(-1, 4)
        scores = np.array(scores).reshape(-1)
        out = []
        for lab, box, scr in zip(labels, boxes, scores):
            if float(scr) < self.conf:
                continue
            x1, y1, x2, y2 = (int(round(v)) for v in box)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 - x1 < 2 or y2 - y1 < 2:
                continue
            out.append((int(lab), [x1, y1, x2, y2]))
        return out

    def detect(self, img_bgr: np.ndarray) -> dict:
        """Return {'bubble':[box], 'text_bubble':[box], 'text_free':[box]}."""
        res = {"bubble": [], "text_bubble": [], "text_free": []}
        h = img_bgr.shape[0]
        for y1, y2 in self._slabs(h):
            for lab, box in self._infer(img_bgr[y1:y2]):
                box = [box[0], box[1] + y1, box[2], box[3] + y1]
                res[CLASS_NAMES.get(lab, "text_free")].append(box)
        return res
