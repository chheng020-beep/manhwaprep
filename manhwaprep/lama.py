"""LaMa neural inpainting (ONNX), for natural blending over artwork.

The model is fixed at 512x512, so we inpaint region-by-region: for each text
cluster we crop a square with surrounding context, resize to 512, run LaMa,
and paste back ONLY the masked pixels (so unmasked art is never touched and
there are no seams). This also means huge webtoon pages never go to the model
whole — only the small text regions do.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import onnxruntime as ort

from . import config

DEFAULT_LAMA_MODEL = config.model_path("lama_fp32.onnx")

# nearby text components within this many px are inpainted in one tile.
# Fewer tiles = faster (each fixed-512 tile costs ~2.3s on CPU), at the cost of
# slightly softer reconstruction inside large merged regions.
_MERGE_PX = 40
# crop side = text size * this (to give LaMa surrounding context)
_CONTEXT = 2.0
_MIN_SIDE = 64
_TILE = 512
_BATCH = 8  # regions per batched ONNX call


class LamaInpainter:
    def __init__(self, model_path: str | None = None):
        self.model_path = model_path or DEFAULT_LAMA_MODEL
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(self.model_path)
        import multiprocessing

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = multiprocessing.cpu_count()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # LaMa's Fast-Fourier-Convolution layers aren't supported by DirectML,
        # so it always runs on CPU.
        self.sess = config.make_session(self.model_path, opts, force_cpu=True)

    def _regions(self, mask: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Crop boxes (x0,y0,x1,y1) around each merged text cluster."""
        h, w = mask.shape
        merged = cv2.dilate(mask, np.ones((_MERGE_PX, _MERGE_PX), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(merged)
        out = []
        for i in range(1, n):
            x, y, bw, bh, _ = stats[i]
            cx, cy = x + bw // 2, y + bh // 2
            side = max(int(max(bw, bh) * _CONTEXT), _MIN_SIDE)
            x0 = max(0, cx - side // 2)
            y0 = max(0, cy - side // 2)
            x1 = min(w, x0 + side)
            y1 = min(h, y0 + side)
            x0, y0 = max(0, x1 - side), max(0, y1 - side)
            if mask[y0:y1, x0:x1].max() > 0:
                out.append((x0, y0, x1, y1))
        return out

    def inpaint(self, img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
        if mask.max() == 0:
            return img_bgr
        result = img_bgr.copy()
        regions = self._regions(mask)
        if not regions:
            return result

        # Run all regions through LaMa in batched ONNX calls (one call per
        # chunk) instead of one call per region — the main speed win.
        for start in range(0, len(regions), _BATCH):
            chunk = regions[start : start + _BATCH]
            ims, mks, meta = [], [], []
            for (x0, y0, x1, y1) in chunk:
                crop = result[y0:y1, x0:x1]
                cmask = mask[y0:y1, x0:x1]
                ch, cw = crop.shape[:2]
                rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                ims.append(
                    np.transpose(
                        cv2.resize(rgb, (_TILE, _TILE)).astype(np.float32) / 255.0,
                        (2, 0, 1),
                    )
                )
                mk = cv2.resize(cmask, (_TILE, _TILE), interpolation=cv2.INTER_NEAREST)
                mks.append((mk > 0).astype(np.float32)[None])
                meta.append((x0, y0, x1, y1, cw, ch))
            outs = self.sess.run(
                None,
                {"image": np.stack(ims), "mask": np.stack(mks)},
            )[0]
            for k, (x0, y0, x1, y1, cw, ch) in enumerate(meta):
                out = np.clip(np.transpose(outs[k], (1, 2, 0)), 0, 255).astype(np.uint8)
                ob = cv2.cvtColor(cv2.resize(out, (cw, ch)), cv2.COLOR_RGB2BGR)
                crop = result[y0:y1, x0:x1]
                cmask = mask[y0:y1, x0:x1]
                sel = cmask > 0
                crop[sel] = ob[sel]
                result[y0:y1, x0:x1] = crop
        return result
