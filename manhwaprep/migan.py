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

# Region-wise processing: nearby text within _MERGE px is repainted together;
# each region is cropped with context, never the whole (tall) page — this kills
# the vertical seam lines MI-GAN's internal tiling leaves on big images.
_MERGE = 30
_CONTEXT = 1.8        # crop side = text size * this (surrounding context)
_MIN_SIDE = 256       # never feed the model a tiny crop
_PAD_MULT = 8         # pad crop dims to a multiple of this


def _round_up(v: int, m: int) -> int:
    return ((v + m - 1) // m) * m


class MiganInpainter:
    def __init__(self, model_path: str | None = None):
        self.model_path = model_path or DEFAULT_MIGAN_MODEL
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(self.model_path)
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = config.make_session(self.model_path, opts)

    def _infer_crop(self, crop_bgr: np.ndarray, cmask: np.ndarray) -> np.ndarray:
        """Repaint one small crop; returns same-size BGR."""
        ch, cw = crop_bgr.shape[:2]
        # pad to a multiple of 8 so the model's internal scaling is exact
        ph, pw = _round_up(ch, _PAD_MULT), _round_up(cw, _PAD_MULT)
        img = cv2.copyMakeBorder(crop_bgr, 0, ph - ch, 0, pw - cw, cv2.BORDER_REFLECT)
        m = cv2.copyMakeBorder(cmask, 0, ph - ch, 0, pw - cw, cv2.BORDER_CONSTANT, value=0)
        # pre-clear the hole so the model can't ghost the text, then refine
        seed = cv2.inpaint(img, cv2.dilate(m, np.ones((3, 3), np.uint8)), 2,
                           cv2.INPAINT_TELEA)
        rgb = cv2.cvtColor(seed, cv2.COLOR_BGR2RGB)
        keep = 255 - m
        out = self.sess.run(
            None,
            {
                "image": rgb.transpose(2, 0, 1)[None].astype(np.uint8),
                "mask": keep[None, None].astype(np.uint8),
            },
        )[0][0]
        res = cv2.cvtColor(out.transpose(1, 2, 0).astype(np.uint8), cv2.COLOR_RGB2BGR)
        if res.shape[:2] != (ph, pw):
            res = cv2.resize(res, (pw, ph))
        return res[:ch, :cw]

    def inpaint(self, img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
        if mask.max() == 0:
            return img_bgr
        result = img_bgr.copy()
        h, w = mask.shape
        merged = cv2.dilate(mask, np.ones((_MERGE, _MERGE), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(merged)
        for i in range(1, n):
            x, y, bw, bh, _ = stats[i]
            cx, cy = x + bw // 2, y + bh // 2
            side = max(int(max(bw, bh) * _CONTEXT), _MIN_SIDE)
            x0 = max(0, cx - side // 2)
            y0 = max(0, cy - side // 2)
            x1 = min(w, x0 + side)
            y1 = min(h, y0 + side)
            x0, y0 = max(0, x1 - side), max(0, y1 - side)
            cmask = mask[y0:y1, x0:x1]
            if cmask.max() == 0:
                continue
            crop = result[y0:y1, x0:x1]
            out = self._infer_crop(crop, cmask)
            sel = cmask > 0
            crop[sel] = out[sel]
            result[y0:y1, x0:x1] = crop
        return result
