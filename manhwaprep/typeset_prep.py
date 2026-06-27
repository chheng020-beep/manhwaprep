"""Prepare a chapter for native Khmer typesetting.

Cleans every page, stitches the cleaned pages into one (or a few) long canvas
images, and records each bubble's position IN CANVAS COORDINATES — so the
native editor can place Khmer text boxes right on the bubbles. Splits into
segments at page boundaries when a canvas would get too tall.

Output (under <out_dir>/typeset/):
  canvas_001.png, canvas_002.png, ...   the cleaned long canvases
  layout.json                            { segments:[{image,width,height,items}] }
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter

import cv2
import numpy as np

from . import pipeline
from .engine import TextCleaner
from .transcript import Transcriber

SEG_MAX = 16000  # max canvas height before splitting at a page boundary


def prep(
    out_dir: str,
    pages: list[str] | None = None,
    source: str | None = None,
    lang: str = "en",
    inpaint: str = "migan",
    keep_sfx: bool = False,
    control=None,
    on_status=None,
    on_progress=None,
) -> str:
    def status(m):
        if on_status:
            on_status(m)

    if pages is None:
        work = tempfile.mkdtemp(prefix="manhwaprep_ts_")
        if pipeline.is_url(source):
            status("Downloading chapter…")
            pages = pipeline._acquire_url(source, work, status, on_progress, control)
        elif source and os.path.isdir(source):
            pages = pipeline.list_folder_images(source)
        else:
            raise RuntimeError(f"Not a folder or URL: {source}")
    if not pages:
        raise RuntimeError("No images found in source.")

    status(f"Cleaning + transcribing {len(pages)} page(s)…")
    cleaner = TextCleaner(inpaint=inpaint, include_sfx=not keep_sfx)
    tr = Transcriber(lang)

    page_data = []
    n = 0
    for i, p in enumerate(pages, 1):
        if control is not None:
            control.checkpoint()
        raw = cv2.imread(p)
        if raw is None:
            continue
        items = tr.page(raw)
        for it in items:
            n += 1
            it["n"] = n
        cleaned, _ = cleaner.clean(raw)
        page_data.append(
            {"cleaned": cleaned, "items": items, "w": raw.shape[1], "h": raw.shape[0]}
        )
        if on_progress:
            on_progress("typeset", i, len(pages))

    if not page_data:
        raise RuntimeError("No readable pages.")

    common_w = Counter(pd["w"] for pd in page_data).most_common(1)[0][0]
    ts_dir = os.path.join(out_dir, "typeset")
    os.makedirs(ts_dir, exist_ok=True)

    def flush(seg, idx):
        imgs, off, sitems = [], 0, []
        for pd in seg:
            scale = common_w / pd["w"]
            ch = int(round(pd["h"] * scale))
            ci = (
                pd["cleaned"]
                if pd["w"] == common_w
                else cv2.resize(pd["cleaned"], (common_w, ch))
            )
            imgs.append(ci)
            for it in pd["items"]:
                x, y, w, h = it["bbox"]
                sitems.append(
                    {
                        "n": it["n"],
                        "bbox": [
                            int(x * scale), int(off + y * scale),
                            int(w * scale), int(h * scale),
                        ],
                        "src": it["text"],
                        "kind": it["kind"],
                    }
                )
            off += ch
        canvas = np.vstack(imgs)
        name = f"canvas_{idx:03d}.png"
        cv2.imwrite(os.path.join(ts_dir, name), canvas)
        return {
            "image": name,
            "width": common_w,
            "height": int(canvas.shape[0]),
            "items": sitems,
        }

    segments, cur, cur_h, idx = [], [], 0, 0
    for pd in page_data:
        ph = int(round(pd["h"] * common_w / pd["w"]))
        if cur and cur_h + ph > SEG_MAX:
            idx += 1
            segments.append(flush(cur, idx))
            cur, cur_h = [], 0
        cur.append(pd)
        cur_h += ph
    if cur:
        idx += 1
        segments.append(flush(cur, idx))

    layout = {
        "chapter": os.path.basename(os.path.normpath(out_dir)),
        "lang": lang,
        "segments": segments,
    }
    path = os.path.join(ts_dir, "layout.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False, indent=2)
    status(f"Typeset prep → {path}  ({len(segments)} canvas, {n} text boxes)")
    return path
