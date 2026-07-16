# comix.to descramble-aware download — design

**Date:** 2026-07-16
**Module touched:** `manhwaprep/headless.py` (only)
**Status:** approved, ready for implementation

## Problem

Downloading a comix.to chapter yields pages that look like a "sliding puzzle."

comix.to protects pages with **pixel-tile scrambling (DRM)**. The server only
ever serves the *scrambled* tile images. The reader's JavaScript descrambles
them at runtime and paints the correct page onto a
`<canvas class="rpage-page__img">`. Confirmed against the live `ReadPage` chunk:
each page wrapper is a `.rpage-page` element carrying a stable **`data-page`**
index and an **`is-loading`** class that clears once the page is descrambled.

### Root cause

`headless.py`'s `download_via_browser` comix branch carries a now-stale comment
("comix.to no longer scrambles pages — download URLs directly"). It uses
`_collect_comix` (an `Array.prototype.map` hook) to grab the page-list **URLs**
— which are the **scrambled tile files** — and downloads them directly. Because
that returns ≥3 URLs, the "fast path" succeeds and returns the puzzle; the
existing `_render_comix` canvas-capture fallback never runs.

## Approach (approved)

- **Capture:** lossless-first, screenshot-fallback.
- **Routing:** auto-detect scramble on every run (survives comix toggling DRM).

## Design

One headless (Playwright) browser session per chapter, reusing the existing
stealth init scripts and `_COMIX_HOOK`.

1. **Load + detect.** Open the reader, let it initialize. Detect mode:
   - Reader has drawn large `<canvas>` (`canvas.rpage-page__img`) → **scrambled**
     → canvas-capture path.
   - Only plain `<img>` pages → **not scrambled** → keep today's fast path:
     use the `_COMIX_HOOK` URLs and direct-download (lossless + fast).

2. **Canvas-capture path (scrambled).** Scroll through the reader. Key each page
   by its **`data-page`** attribute (stable across virtual scrolling; replaces
   the current fragile group-by-Y dedup). Capture a page only once its
   `.rpage-page` wrapper no longer has `is-loading` (fully descrambled). Per page:
   - Try **`canvas.toDataURL('image/png')`** → full-resolution lossless pixels.
   - On failure (canvas CORS-tainted) → **screenshot** that canvas at an
     increased device scale factor for best-effort quality.
   Return pages ordered by `data-page`.

3. **Error handling.**
   - Blank render / Cloudflare Turnstile → fewer than 3 pages captured → raise,
     so the pipeline shows a clear "couldn't render comix.to" message (same
     contract as today).
   - A page stuck `is-loading` past a per-page timeout → skip it but continue,
     and report which `data-page` indexes are missing instead of silently
     returning a short chapter.

## Non-goals

- No reverse-engineering of the scramble algorithm.
- No changes to `downloader.py`, the pipeline's 3-tier fallback, or the
  typeset / FB paths.
- No new dependencies (Playwright + requests already present).

## Verification

No unit-testable seam — this is live browser I/O. Verify by running the real
download on a known-scrambled chapter URL, then building a contact sheet of the
first ~6 pages to confirm visually that they are un-puzzled and in order, and
that the captured page count matches the reader.
