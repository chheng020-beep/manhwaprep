"""Source-text OCR with script-aware routing.

On an English-scan page, dialogue is Latin (English) and SFX is Hangul (Korean).
A single-language OCR pass mangles both, so we:
  1. detect text regions (slabbed for tall pages, like the cleaning engine),
  2. read each region with BOTH the English and Korean recognizers,
  3. classify by script, and route:
       - regions matching the chosen dialogue language -> kind "dialogue"
       - the other script (e.g. Korean SFX on an English scan) -> kind "sfx"
Dialogue is translated downstream; SFX is left as-is (or glossary-looked-up).

Reuses the detection + recognition models that ship with EasyScanlate.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
from rapidocr import EngineType, RapidOCR

OCR_DIR = os.path.expanduser("~/EasyScanlate/OCR")
DET_MODEL = os.path.join(OCR_DIR, "model", "ch_PP-OCRv5_mobile_det.onnx")

REC_MODELS = {
    "ko": ("korean_PP-OCRv5_rec_mobile_infer.onnx", "korean_dict.txt"),
    "en": ("ch_PP-OCRv5_rec_mobile_infer.onnx", "ppocrv5_dict.txt"),
}

SLAB_HEIGHT = 2000
SLAB_OVERLAP = 250


def _hangul_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    hangul = sum(
        1 for c in chars
        if "가" <= c <= "힣" or "ᄀ" <= c <= "ᇿ"
        or "㄰" <= c <= "㆏"
    )
    return hangul / len(chars)


def _rotate_crop(img: np.ndarray, points) -> np.ndarray:
    pts = np.array(points, dtype=np.float32)
    xs = pts[np.argsort(pts[:, 0]), :]
    left, right = xs[:2, :], xs[2:, :]
    left = left[np.argsort(left[:, 1]), :]
    tl, bl = left
    right = right[np.argsort(right[:, 1]), :]
    tr, br = right
    src = np.array([tl, tr, br, bl], dtype=np.float32)
    w = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    h = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    if w < 2 or h < 2:
        return img[0:1, 0:1]
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(
        img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _rec_engine(lang: str) -> RapidOCR:
    rec_model, rec_dict = REC_MODELS[lang]
    return RapidOCR(
        params={
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.model_path": os.path.join(OCR_DIR, "model", rec_model),
            "Rec.rec_keys_path": os.path.join(OCR_DIR, "dict", rec_dict),
            "Global.use_det": False,
            "Global.use_rec": True,
            "Global.use_cls": False,
        }
    )


class SourceOCR:
    def __init__(self, lang: str = "en"):
        if lang not in REC_MODELS:
            raise ValueError(f"Unsupported OCR language: {lang}")
        self.lang = lang  # the dialogue language to translate
        self._det = RapidOCR(
            params={
                "Det.engine_type": EngineType.ONNXRUNTIME,
                "Det.model_path": DET_MODEL,
                "Global.use_det": True,
                "Global.use_rec": False,
                "Global.use_cls": False,
            }
        )
        # Both recognizers so we can classify Latin vs Hangul per region.
        self._rec_en = _rec_engine("en")
        self._rec_ko = _rec_engine("ko")

    # -- low level -----------------------------------------------------
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

    def _detect(self, img: np.ndarray) -> list[np.ndarray]:
        """Detect boxes, slabbing tall pages so small text isn't downscaled away."""
        h = img.shape[0]
        out_boxes = []
        for y1, y2 in self._slabs(h):
            res = self._det(img[y1:y2])
            boxes = getattr(res, "boxes", None)
            if boxes is None:
                continue
            for b in boxes:
                arr = np.array(b, np.int32)
                if arr.shape == (4, 2):
                    arr = arr.copy()
                    arr[:, 1] += y1
                    out_boxes.append(arr)
        return out_boxes

    @staticmethod
    def _recognize(engine: RapidOCR, crop: np.ndarray) -> tuple[str, float]:
        out = engine(crop)
        txts = getattr(out, "txts", None)
        if txts:
            scores = getattr(out, "scores", [0.0])
            return txts[0], float(scores[0])
        return "", 0.0

    # -- routing -------------------------------------------------------
    def _classify(self, crop: np.ndarray) -> dict:
        """Read with both recognizers, decide dialogue vs sfx for self.lang."""
        en_text, en_conf = self._recognize(self._rec_en, crop)
        ko_text, ko_conf = self._recognize(self._rec_ko, crop)
        ko_is_hangul = _hangul_ratio(ko_text) > 0.3

        if self.lang == "en":
            if ko_is_hangul and ko_conf >= 0.3:
                return {"kind": "sfx", "text": ko_text.strip(), "conf": ko_conf,
                        "script": "hangul"}
            return {"kind": "dialogue", "text": en_text.strip(), "conf": en_conf,
                    "script": "latin"}
        else:  # lang == "ko": Korean dialogue, anything Latin treated as foreign/sfx
            if ko_is_hangul:
                return {"kind": "dialogue", "text": ko_text.strip(), "conf": ko_conf,
                        "script": "hangul"}
            return {"kind": "sfx", "text": en_text.strip(), "conf": en_conf,
                    "script": "latin"}

    @staticmethod
    def _group_lines(lines: list[dict]) -> list[dict]:
        """Merge dialogue lines in the same bubble; SFX entries pass through."""
        dialogue = [x for x in lines if x["kind"] == "dialogue"]
        sfx = [x for x in lines if x["kind"] == "sfx"]

        merged = list(sfx)
        n = len(dialogue)
        if n:
            parent = list(range(n))

            def find(a):
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a

            rects = []
            for it in dialogue:
                x, y, w, h = it["bbox"]
                mx, my = 0.35 * h, 0.8 * h
                rects.append((x - mx, y - my, x + w + mx, y + h + my))
            for i in range(n):
                ax1, ay1, ax2, ay2 = rects[i]
                for j in range(i + 1, n):
                    bx1, by1, bx2, by2 = rects[j]
                    if not (ax2 < bx1 or bx2 < ax1 or ay2 < by1 or by2 < ay1):
                        parent[find(i)] = find(j)
            groups: dict[int, list[dict]] = {}
            for i in range(n):
                groups.setdefault(find(i), []).append(dialogue[i])
            for g in groups.values():
                g.sort(key=lambda it: (it["bbox"][1], it["bbox"][0]))
                xs = [it["bbox"][0] for it in g]
                ys = [it["bbox"][1] for it in g]
                xe = [it["bbox"][0] + it["bbox"][2] for it in g]
                ye = [it["bbox"][1] + it["bbox"][3] for it in g]
                merged.append(
                    {
                        "bbox": [min(xs), min(ys), max(xe) - min(xs), max(ye) - min(ys)],
                        "text": " ".join(it["text"] for it in g),
                        "conf": round(min(it["conf"] for it in g), 3),
                        "kind": "dialogue",
                        "script": "latin",
                    }
                )
        merged.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
        return merged

    def read_page(
        self, img_bgr: np.ndarray, min_conf: float = 0.4, group: bool = True
    ) -> list[dict]:
        """[{bbox, text, conf, kind, script}] in reading order. kind: dialogue|sfx."""
        lines = []
        for box in self._detect(img_bgr):
            crop = _rotate_crop(img_bgr, box)
            cls = self._classify(crop)
            if not cls["text"] or cls["conf"] < min_conf:
                continue
            x, y = int(box[:, 0].min()), int(box[:, 1].min())
            w = int(box[:, 0].max() - x)
            h = int(box[:, 1].max() - y)
            cls["bbox"] = [x, y, w, h]
            cls["conf"] = round(cls["conf"], 3)
            lines.append(cls)
        lines.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
        return self._group_lines(lines) if group else lines
