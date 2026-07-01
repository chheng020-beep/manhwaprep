"""Orchestrates the whole job: acquire pages -> clean -> stitch -> write."""

from __future__ import annotations

import os
import re
import tempfile
from urllib.parse import parse_qs, urlparse

from . import downloader
from .engine import TextCleaner
from .stitcher import stitch

from . import config

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
DEFAULT_OUT_ROOT = config.default_output_dir()


def _natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def list_folder_images(folder: str) -> list[str]:
    files = [f for f in os.listdir(folder) if f.lower().endswith(IMAGE_EXTS)]
    files.sort(key=_natural_key)
    return [os.path.join(folder, f) for f in files]


def is_url(source: str) -> bool:
    return source.strip().lower().startswith(("http://", "https://"))


def _safe(name: str) -> str:
    name = re.sub(r"[^\w.\- ]+", "_", name).strip().strip(".")
    return name[:80] or "chapter"


def _name_from_url(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    for key in ("wr_id", "is", "id"):
        if key in q and q[key]:
            return f"chapter_{q[key][0]}"
    last = urlparse(url).path.rstrip("/").split("/")[-1]
    return last or "chapter"


def _acquire_url(source, work, status, on_progress, control):
    """Download a chapter URL, trying each backend until one yields a real
    chapter. A tier that returns too few images (e.g. just a cover) is treated
    as insufficient, so we fall through to the next tier."""
    MIN_PAGES = 3
    best = []

    def consider(pages, label):
        nonlocal best
        status(f"  ({label}: {len(pages)} pages)")
        if len(pages) > len(best):
            best = pages
        return len(pages) >= MIN_PAGES

    # tier 1: gallery-dl
    try:
        p = downloader.download_via_gallery_dl(source, os.path.join(work, "raw"))
        if consider(p, "gallery-dl"):
            return p
    except Exception as e:
        status(f"  gallery-dl: {e}")
    # tier 2: built-in static scraper
    try:
        p = downloader.download(
            source,
            os.path.join(work, "raw2"),
            progress=lambda d, t: on_progress and on_progress("download", d, t),
            control=control,
        )
        if consider(p, "scraper"):
            return p
    except Exception as e:
        status(f"  scraper: {e}")
    # tier 3: headless browser (JS-rendered / bot-protected sites)
    try:
        status("  trying headless browser (JS site)…")
        from . import headless

        p = headless.download_via_browser(source, os.path.join(work, "raw3"))
        if consider(p, "headless"):
            return p
    except Exception as e:
        status(f"  headless: {e}")

    if best:
        return best
    raise RuntimeError("No chapter images found by any download backend.")


def _transcript_pages(pages, out_dir, name, lang, ckpt, status, on_progress):
    """OCR every bubble/SFX and write a numbered transcript (+ overlays) for
    translating in Claude. No machine translation, no cleaning."""
    import cv2

    from .transcript import Transcriber, write_transcript

    status(f"Transcribing ({lang}) — RT-DETR + OCR…")
    tr = Transcriber(lang)
    tpages, n = [], 0
    for i, p in enumerate(pages, 1):
        ckpt()
        img = cv2.imread(p)
        if img is None:
            continue
        items = tr.page(img)
        for it in items:
            n += 1
            it["n"] = n
        tpages.append({"page": i, "img": img, "items": items})
        if on_progress:
            on_progress("transcript", i, len(pages))

    paths = write_transcript(out_dir, name, lang, tpages)
    status(f"Transcript ({n} lines) → {paths['md']}")
    return paths


def run(
    source: str,
    out_root: str | None = None,
    segments: int = 5,
    max_height: int = 12000,
    clean: bool = True,
    inpaint: str = "migan",
    keep_sfx: bool = False,
    transcript: str | None = None,
    typeset: str | None = None,
    cleaner: TextCleaner | None = None,
    control=None,
    on_status=None,
    on_progress=None,
) -> tuple[str, list[str]]:
    """Run the full pipeline.

    source      : a folder path OR a chapter URL
    clean       : if False, skip text erasure (download + stitch only)
    inpaint     : "migan" (fast+good) | "lama" (best, slow) | "telea" (fastest)
    keep_sfx    : if True, erase only speech bubbles and keep SFX/action text
    control     : optional Control for cooperative pause/stop
    on_status   : callable(str) for human-readable progress lines
    on_progress : callable(stage, done, total) where stage in
                  {"download","transcript","clean","stitch"}
    Returns (output_dir, list_of_output_paths).
    """

    def status(msg: str):
        if on_status:
            on_status(msg)

    def ckpt():
        if control is not None:
            control.checkpoint()

    out_root = out_root or DEFAULT_OUT_ROOT
    work = tempfile.mkdtemp(prefix="manhwaprep_")

    # 1. acquire pages — try tiers in order: gallery-dl, static scraper,
    #    headless browser (for JS-rendered / bot-protected sites).
    if is_url(source):
        status("Downloading chapter…")
        pages = _acquire_url(source, work, status, on_progress, control)
        name = _name_from_url(source)
    else:
        if not os.path.isdir(source):
            raise RuntimeError(f"Not a folder or URL: {source}")
        pages = list_folder_images(source)
        name = os.path.basename(os.path.normpath(source))
    if not pages:
        raise RuntimeError("No images found in source.")

    out_dir = os.path.join(out_root, _safe(name))

    # typeset (optional) — clean + long canvas + positions for the native editor
    if typeset:
        from . import typeset_prep

        lp = typeset_prep.prep(
            out_dir, pages=pages, lang=typeset, inpaint=inpaint,
            keep_sfx=keep_sfx, control=control,
            on_status=status, on_progress=on_progress,
        )
        status(f"Done — typeset canvas ready: {lp}")
        return out_dir, [lp]

    # 2. transcript (optional) — pull all text out for Claude translation
    if transcript:
        paths = _transcript_pages(
            pages, out_dir, name, transcript, ckpt, status, on_progress
        )
        if not clean:  # transcript-only run: done, nothing to stitch
            status(f"Done — transcript in {out_dir}")
            return out_dir, [paths["md"]]

    # 3. clean each page (optional)
    if clean:
        status(f"Cleaning text on {len(pages)} page(s)…")
        cleaner = cleaner or TextCleaner(inpaint=inpaint, include_sfx=not keep_sfx)
        status(f"  (inpaint: {cleaner.backend} · detect: {cleaner.detectors})")
        clean_dir = os.path.join(work, "clean")
        os.makedirs(clean_dir, exist_ok=True)
        cleaned_paths = []
        for i, p in enumerate(pages):
            ckpt()
            out_p = os.path.join(clean_dir, f"{i + 1:04d}.png")
            try:
                cleaner.clean_file(p, out_p)
                cleaned_paths.append(out_p)
            except Exception as e:  # skip a bad page, keep going
                status(f"  ! skipped {os.path.basename(p)}: {e}")
            if on_progress:
                on_progress("clean", i + 1, len(pages))
        if not cleaned_paths:
            raise RuntimeError("Every page failed to clean.")
    else:
        status(f"Skipping text removal — stitching {len(pages)} raw page(s).")
        cleaned_paths = pages

    # 4. stitch into long images
    ckpt()
    status("Stitching long images…")
    outputs = stitch(
        cleaned_paths,
        out_dir,
        target_segments=segments,
        max_height=max_height,
        progress=lambda d, t: on_progress and on_progress("stitch", d, t),
    )
    status(f"Done — {len(outputs)} image(s) → {out_dir}")
    return out_dir, outputs


def run_for_batch(url: str, out_root: str) -> str | None:
    """Run pipeline for one chapter URL. Returns output dir on success, None on failure."""
    try:
        out_dir, outputs = run(url, out_root=out_root)
        return out_dir if outputs else None
    except Exception:
        return None
