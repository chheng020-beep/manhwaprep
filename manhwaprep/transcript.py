"""Transcript export — pull all text out of a chapter for Claude translation.

Uses RT-DETR to find bubble/SFX regions (clean, numbered) and OCR to read each
one, then writes a numbered transcript (paste into Claude) plus numbered overlay
images (so you can match Claude's numbered Khmer back to each bubble when you
typeset in Photoshop). No cleaning, no machine translation.
"""

from __future__ import annotations

import json
import os

import cv2

from . import sheet
from .comicdetector import ComicDetector
from .ocr import SourceOCR


class Transcriber:
    def __init__(self, lang: str = "en"):
        # SourceOCR does proper line detection -> recognition -> bubble grouping
        # (accurate text). RT-DETR is used only to tag each line bubble vs SFX.
        self.ocr = SourceOCR(lang)
        self.det = ComicDetector()

    def page(self, img_bgr) -> list[dict]:
        """Return [{bbox:[x,y,w,h], text, kind, conf}] in reading order."""
        items = self.ocr.read_page(img_bgr)  # {bbox, text, conf, kind(script)}
        dets = self.det.detect(img_bgr)
        bubble_boxes = dets["bubble"] + dets["text_bubble"]
        for it in items:
            x, y, w, h = it["bbox"]
            cx, cy = x + w // 2, y + h // 2
            in_bubble = any(
                bx1 <= cx <= bx2 and by1 <= cy <= by2
                for bx1, by1, bx2, by2 in bubble_boxes
            )
            it["kind"] = "bubble" if in_bubble else "sfx"
        return items


_CLAUDE_PROMPT = (
    "Translate each numbered line below into natural Khmer for a manhwa. "
    "Keep the numbers and the [bubble]/[sfx] tags, one line each."
)


def write_transcript(out_dir: str, chapter: str, lang: str, pages: list[dict]) -> dict:
    """pages: [{"page": int, "img": ndarray, "items": [...]}]. Returns paths."""
    odir = os.path.join(out_dir, "_transcript", "overlays")
    os.makedirs(odir, exist_ok=True)

    md = [f"# {chapter} — transcript ({lang})", "", "> " + _CLAUDE_PROMPT, ""]
    data = {"chapter": chapter, "lang": lang, "pages": []}
    for p in pages:
        md.append(f"## Page {p['page']}")
        cv2.imwrite(
            os.path.join(odir, f"{p['page']:03d}.png"),
            sheet.build_overlay(p["img"], p["items"]),
        )
        for it in p["items"]:
            md.append(f"{it['n']}. [{it['kind']}] {it['text']}")
        md.append("")
        data["pages"].append(
            {
                "page": p["page"],
                "items": [
                    {k: it[k] for k in ("n", "kind", "bbox", "text", "conf")}
                    for it in p["items"]
                ],
            }
        )

    md_path = os.path.join(out_dir, "transcript.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    with open(os.path.join(out_dir, "transcript.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"md": md_path, "overlays": odir}
