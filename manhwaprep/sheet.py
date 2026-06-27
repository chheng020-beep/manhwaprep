"""Write the translation sheet: translation.json (+ .md) and numbered overlays.

translation.json is the source of truth the side-by-side editor reads and
writes back. Raw page images are copied next to it so the editor can show them.
"""

from __future__ import annotations

import json
import os

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


def write_translation(
    out_dir: str, chapter: str, source_lang: str, pages: list[dict]
) -> str:
    """pages: [{"page": int, "img": np.ndarray, "bubbles": [...]}]. Returns json path."""
    tdir = os.path.join(out_dir, "_translate")
    pdir = os.path.join(tdir, "pages")
    odir = os.path.join(tdir, "overlays")
    os.makedirs(pdir, exist_ok=True)
    os.makedirs(odir, exist_ok=True)

    data = {"chapter": chapter, "source_lang": source_lang, "pages": []}
    md = [f"# {chapter} — Khmer translation ({source_lang}→khm)\n"]

    for p in pages:
        n, img = p["page"], p["img"]
        page_img = os.path.join(pdir, f"{n:03d}.jpg")
        cv2.imwrite(page_img, img)
        cv2.imwrite(os.path.join(odir, f"{n:03d}.png"), build_overlay(img, p["bubbles"]))
        data["pages"].append(
            {
                "page": n,
                "image": os.path.relpath(page_img, out_dir),
                "bubbles": p["bubbles"],
            }
        )
        md.append(f"\n## Page {n}\n")
        for b in p["bubbles"]:
            md.append(f"{b['n']}. {b['src']}\n   → {b['khm']}")

    json_path = os.path.join(out_dir, "translation.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "translation.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return json_path
