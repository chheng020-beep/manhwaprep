"""Text-cleaning engine: detect text, build a stroke-accurate mask, inpaint.

Pipeline:
  1. Detect text regions with RapidOCR (PP-OCRv5 mobile det), tuned to catch
     stylized / SFX text, in vertical slabs so small text on tall pages isn't
     lost to the detector's internal downscaling.
  2. Build a STROKE mask: within each (tight) detection box, isolate the actual
     glyph strokes with morphological top-hat/black-hat. This removes only the
     text — not the artwork inside the box — and catches faint anti-aliased
     edges (kills the residue Telea-on-boxes used to leave).
  3. Inpaint: LaMa (neural, natural blend over art) when the model is present;
     otherwise fall back to OpenCV Telea.
"""

from __future__ import annotations

import os

import cv2
import numpy as np

# NOTE: rapidocr is imported lazily inside the fallback path only, so the core
# Windows build (RT-DETR detection) doesn't need it installed.

DEFAULT_DET_MODEL = os.path.expanduser(
    "~/EasyScanlate/OCR/model/ch_PP-OCRv5_mobile_det.onnx"
)

SLAB_HEIGHT = 2000
SLAB_OVERLAP = 250

# stroke-mask tuning
_STROKE_KERNEL = 15   # > stroke width, < text height
_STROKE_THRESH = 38   # top/black-hat response above this = a stroke pixel
_MASK_DILATE = 2      # cover anti-aliased halo


class TextCleaner:
    def __init__(
        self,
        det_model_path: str | None = None,
        inpaint: str = "migan",
        include_sfx: bool = True,
    ):
        # inpaint: "migan" (fast+good, default) | "lama" (best, slow) | "telea" (fastest)
        # include_sfx=False -> erase only speech-bubble text, keep SFX/action text
        self.include_sfx = include_sfx
        self.det_model_path = det_model_path or DEFAULT_DET_MODEL
        self._engine = None  # PP-OCR detector, created only for the fallback path

        # Inpainter: requested engine, falling back to Telea (always available).
        self._inpainter = None
        self._backend = "telea"
        if inpaint == "migan":
            try:
                from .migan import MiganInpainter

                self._inpainter = MiganInpainter()
                self._backend = "migan"
            except Exception as e:
                print(f"[engine] MI-GAN unavailable ({e}); using Telea fallback.")
        elif inpaint == "lama":
            try:
                from .lama import LamaInpainter

                self._inpainter = LamaInpainter()
                self._backend = "lama"
            except Exception as e:
                print(f"[engine] LaMa unavailable ({e}); using Telea fallback.")

        # Primary detection: comic-translate's RT-DETR (bubble/dialogue/SFX).
        self._detector = None
        try:
            from .comicdetector import ComicDetector

            self._detector = ComicDetector()
        except Exception as e:
            print(f"[engine] RT-DETR detector unavailable ({e}); using OCR fallback.")

        # Fallback only: PP-OCR detector + legacy SFX detector. Built lazily
        # when RT-DETR is unavailable so we don't load models we won't use.
        self._ctd = None
        if self._detector is None:
            if not os.path.exists(self.det_model_path):
                raise FileNotFoundError(
                    f"No RT-DETR model and no PP-OCR detector at {self.det_model_path}"
                )
            from rapidocr import EngineType, RapidOCR

            self._engine = RapidOCR(
                params={
                    "Det.engine_type": EngineType.ONNXRUNTIME,
                    "Det.model_path": self.det_model_path,
                    "Global.use_det": True,
                    "Global.use_rec": False,
                    "Global.use_cls": False,
                    "Det.box_thresh": 0.3,
                    "Det.unclip_ratio": 1.6,
                    "Det.limit_type": "max",
                    "Det.limit_side_len": 1280,
                }
            )
            try:
                from .ctd import ComicTextDetector

                self._ctd = ComicTextDetector()
            except Exception:
                pass

    @property
    def detectors(self) -> str:
        if self._detector is not None:
            return "rtdetr(bubble+sfx)" if self.include_sfx else "rtdetr(bubbles)"
        return "ocr+ctd(sfx)" if (self._ctd and self.include_sfx) else "ocr(bubbles)"

    @staticmethod
    def _rect_to_poly(box: list[int]) -> np.ndarray:
        x1, y1, x2, y2 = box
        return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], np.int32)

    @property
    def backend(self) -> str:
        return self._backend

    # -- detection -----------------------------------------------------
    def _detect_boxes(self, img_bgr: np.ndarray) -> list[np.ndarray]:
        out = self._engine(img_bgr)
        boxes = getattr(out, "boxes", None)
        if boxes is None:
            return []
        return [
            np.array(b, dtype=np.int32)
            for b in boxes
            if np.array(b).shape == (4, 2)
        ]

    @staticmethod
    def _slabs(height: int) -> list[tuple[int, int]]:
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

    # -- mask ----------------------------------------------------------
    @staticmethod
    def _stroke_mask(img_bgr: np.ndarray, boxes: list[np.ndarray]) -> np.ndarray:
        h, w = img_bgr.shape[:2]
        if not boxes:
            return np.zeros((h, w), np.uint8)
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        el = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (_STROKE_KERNEL, _STROKE_KERNEL)
        )
        strokes = np.maximum(
            cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, el),     # bright text
            cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, el),   # dark text
        )
        poly = np.zeros((h, w), np.uint8)
        for b in boxes:
            cv2.fillPoly(poly, [b], 255)
        mask = np.where((strokes > _STROKE_THRESH) & (poly > 0), 255, 0).astype(
            np.uint8
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        if _MASK_DILATE:
            mask = cv2.dilate(
                mask,
                cv2.getStructuringElement(
                    cv2.MORPH_ELLIPSE, (_MASK_DILATE * 2 + 1,) * 2
                ),
            )
        return mask

    @staticmethod
    def _bubble_mask(crop_bgr: np.ndarray) -> np.ndarray:
        """Detect speech bubbles as filled regions (tight closed perimeters).

        White (low-sat/high-val) areas are closed so the text inside merges into
        a solid blob, then the external contours are kept if they're big and
        reasonably filled (bubbles), giving a precise per-bubble perimeter.
        """
        h, w = crop_bgr.shape[:2]
        hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
        # near-pure white only (excludes skin, which is brighter-but-saturated)
        white = ((hsv[:, :, 1] < 28) & (hsv[:, :, 2] > 205)).astype(np.uint8) * 255
        white = cv2.morphologyEx(
            white, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25)),
        )
        white = cv2.morphologyEx(
            white, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
        )
        contours, _ = cv2.findContours(
            white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        bmask = np.zeros((h, w), np.uint8)
        min_area = max(1200.0, 0.0006 * h * w)
        for c in contours:
            area = cv2.contourArea(c)
            if area < min_area:
                continue
            bx, by, bw, bh = cv2.boundingRect(c)
            if bw < 25 or bh < 18 or area / (bw * bh) < 0.55:
                continue  # reject thin / non-bubble shapes
            hull_area = cv2.contourArea(cv2.convexHull(c))
            if hull_area <= 0 or area / hull_area < 0.78:
                continue  # bubbles are convex; sprawling skin/art is not
            cv2.drawContours(bmask, [c], -1, 255, -1)
        return bmask

    @staticmethod
    def _box_in_bubble(box: np.ndarray, bubble_mask: np.ndarray) -> bool:
        h, w = bubble_mask.shape
        cx = int(np.clip(box[:, 0].mean(), 0, w - 1))
        cy = int(np.clip(box[:, 1].mean(), 0, h - 1))
        return bubble_mask[cy, cx] > 0

    # -- mask building -------------------------------------------------
    def _build_mask(self, img_bgr: np.ndarray) -> tuple[np.ndarray, int]:
        """Mask of text to erase. Uses RT-DETR classes when available."""
        h, w = img_bgr.shape[:2]
        if self._detector is not None:
            dets = self._detector.detect(img_bgr)
            # text_bubble = dialogue (always erase); text_free = SFX (only if
            # include_sfx). The model already separates them — no heuristics.
            text_boxes = list(dets["text_bubble"])
            if self.include_sfx:
                text_boxes += dets["text_free"]
            polys = [self._rect_to_poly(b) for b in text_boxes]
            return self._stroke_mask(img_bgr, polys), len(text_boxes)

        # -- fallback: OCR detection + (heuristic) bubble/SFX handling --
        mask = np.zeros((h, w), np.uint8)
        n_boxes = 0
        for y1, y2 in self._slabs(h):
            crop = img_bgr[y1:y2]
            boxes = self._detect_boxes(crop)
            n_boxes += len(boxes)
            if self.include_sfx:
                sm = self._stroke_mask(crop, boxes)
                if self._ctd is not None:
                    sm = np.maximum(sm, self._ctd.mask(crop))
            else:
                bub = self._bubble_mask(crop)
                bubble_boxes = [b for b in boxes if self._box_in_bubble(b, bub)]
                sm = self._stroke_mask(crop, bubble_boxes)
            mask[y1:y2] = np.maximum(mask[y1:y2], sm)
        return mask, n_boxes

    # -- cleaning ------------------------------------------------------
    def clean(self, img_bgr: np.ndarray) -> tuple[np.ndarray, int]:
        mask, n_boxes = self._build_mask(img_bgr)
        if mask.max() == 0:
            return img_bgr, 0

        if self._inpainter is not None:
            cleaned = self._inpainter.inpaint(img_bgr, mask)
        else:
            # Telea: the stroke mask is already tight, so add only a tiny margin
            # and use a small radius — filling thin strokes from close neighbors
            # avoids the big blurry smear a fat mask + large radius produces.
            m = cv2.dilate(
                mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            )
            cleaned = cv2.inpaint(img_bgr, m, 2, cv2.INPAINT_TELEA)
        return cleaned, n_boxes

    def clean_file(self, in_path: str, out_path: str) -> int:
        img = cv2.imread(in_path, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"Could not read image: {in_path}")
        cleaned, n = self.clean(img)
        if not cv2.imwrite(out_path, cleaned):
            raise IOError(f"Could not write image: {out_path}")
        return n
