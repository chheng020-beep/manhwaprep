# ManhwaPrep — design spec

**Date:** 2026-06-24
**Status:** built (v0.1.0)

## Goal

One-click batch prep for Korean manhwa chapters. The user drops a folder or
pastes a chapter link; the tool downloads the pages, erases the original text,
and stitches the many weirdly-chopped pages into ~5 long vertical images.

This is **not** a typesetting/Canva editor. The pain being solved is the manual
grind of downloading + cleaning + re-stitching, not text editing. Typesetting
(Khmer) happens elsewhere.

## Decisions (locked during brainstorming)

- **Reuse, don't rebuild the engine.** Point at EasyScanlate's existing ONNX
  detection model (`~/EasyScanlate/OCR/model/ch_PP-OCRv5_mobile_det.onnx`) and
  port its Telea-inpaint cleaning, decoupled from Qt. Only detection is needed
  (we erase, not read).
- **Clean = erase original text** → blank pages. No translation.
- **Stitch = glue-all-then-recut** into ~5 equal long images, capped at
  ~12000px each (a huge chapter may produce a few more than 5).
- **No hardcoded domain.** 11toon-style sites rotate domains, so the user
  pastes the live chapter URL and a generic scraper extracts the images.
- **UI = one PySide6 window** reusing EasyScanlate's venv.

## Architecture

```
input (folder | URL)
   │
   ├─ URL → downloader.download()  → raw page images (ordered)
   │        folder → list_folder_images() (natural sort)
   │
   ├─ engine.TextCleaner.clean_file()  per page  (detect slabs → mask → inpaint)
   │
   └─ stitcher.stitch()  → normalize width → vstack → re-cut into ~N long .jpg
            → output/<chapter>/NN.jpg
```

Units: `engine.py`, `downloader.py`, `stitcher.py`, `pipeline.py`, `ui.py`,
`__main__.py` (CLI + GUI dispatch).

## Error handling

- Download finds no images → clear message suggesting HakuNeko + folder drop.
- A page that fails OCR/inpaint → skipped with a warning; pipeline continues.
- All pages fail → hard error.
- UI runs the pipeline on a `QThread`; model load happens off the UI thread.

## Verification done

- Detection API shape confirmed against rapidocr 3.6.0 (`out.boxes`, (4,2)).
- Full folder pipeline: 6 synthetic pages → 3 stitched long images.
- Text erased: 3 boxes → 0 remaining after cleaning.
- Stitch conserves height exactly (8200px in = 8200px out).
- Scraper extracts lazy-load + script image URLs, filters logo/ad, keeps order.
- GUI window constructs (offscreen).

## Not yet verified

- A **live download** against a real 11toon URL (needs current domain +
  network). The scraper is the fragile part; tune against a real link.

## Possible next steps

- Add a "clean only" / "stitch only" toggle.
- Per-page preview before stitch.
- Optional bubble-aware masking (only erase inside detected speech bubbles).
