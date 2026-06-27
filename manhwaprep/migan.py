"""MI-GAN inpainting (ONNX) — fast neural inpaint, near-LaMa quality.

Uses the MI-GAN pipeline_v2 export: uint8 RGB image + uint8 mask in, uint8 RGB
out, with dynamic resolution (it handles normalization and its hi-res trick
internally). So we can inpaint a whole page in one call — no 512 tiling — at
roughly 20x LaMa's speed on CPU.

Mask convention for this model: 0 = hole to inpaint, 255 = keep. Our masks use
255 = text, so we invert. We also paste back only masked pixels, leaving the
rest of the page pixel-identical.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import onnxruntime as ort

from . import config

DEFAULT_MIGAN_MODEL = config.model_path("migan_pipeline_v2.onnx")


class MiganInpainter:
    def __init__(self, model_path: str | None = None):
        self.model_path = model_path or DEFAULT_MIGAN_MODEL
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(self.model_path)
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = config.make_session(self.model_path, opts)

    def inpaint(self, img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
        if mask.max() == 0:
            return img_bgr
        rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        keep = 255 - mask  # MI-GAN: hole=0, keep=255
        out = self.sess.run(
            None,
            {
                "image": rgb.transpose(2, 0, 1)[None].astype(np.uint8),
                "mask": keep[None, None].astype(np.uint8),
            },
        )[0][0]
        res = cv2.cvtColor(out.transpose(1, 2, 0).astype(np.uint8), cv2.COLOR_RGB2BGR)
        if res.shape[:2] != img_bgr.shape[:2]:
            res = cv2.resize(res, (img_bgr.shape[1], img_bgr.shape[0]))
        result = img_bgr.copy()
        sel = mask > 0
        result[sel] = res[sel]
        return result
