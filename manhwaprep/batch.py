"""Batch chapter downloader: auto-increments chapter number in URL."""
from __future__ import annotations

import os
import re
import zipfile
from typing import Callable

_PATTERNS = [
    r'(?i)(chapter[-_/])(\d+)',
    r'(?i)(-ep[-_]?)(\d+)',
    r'(?i)(/ep[-_]?)(\d+)',
    r'(?i)([\?&]ch(?:apter)?=)(\d+)',
    r'(?i)(/c)(\d+)(?=[/\-_]|$)',
]


def detect_chapter(url: str):
    """Return (chapter_num, match) or (None, None) if not found."""
    for pat in _PATTERNS:
        m = re.search(pat, url)
        if m:
            return int(m.group(2)), m
    path = url.split('?')[0].split('#')[0]
    m = re.search(r'(\D)(\d{1,4})(?=\D*$)', path)
    if m:
        try:
            n = int(m.group(2))
            if 1 <= n <= 9999:
                return n, m
        except ValueError:
            pass
    return None, None


def make_url(url: str, match, chapter: int) -> str:
    """Replace the matched chapter number with `chapter`."""
    s, e = match.span(2)
    return url[:s] + str(chapter) + url[e:]


def zip_folder(folder: str, zip_path: str) -> None:
    """Zip everything in `folder` into `zip_path`."""
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(folder):
            for fn in sorted(files):
                fp = os.path.join(root, fn)
                zf.write(fp, os.path.relpath(fp, folder))


def batch_download(
    url: str,
    start: int,
    count: int,
    out_dir: str,
    run_pipeline,
    progress_cb: Callable[[int, int, str], None] | None = None,
    stop_flag=None,
) -> list[str]:
    """Download+clean `count` chapters starting at `start`.

    run_pipeline(chapter_url) -> str | None
        Returns the directory containing the cleaned images, or None on failure.
    Returns list of created zip paths.
    """
    _, match = detect_chapter(url)
    if match is None:
        raise ValueError("Could not detect chapter number in URL")

    os.makedirs(out_dir, exist_ok=True)
    zips = []
    for i in range(count):
        if stop_flag and stop_flag():
            break
        ch = start + i
        ch_url = make_url(url, match, ch)
        ch_label = f"Chapter {ch}"
        if progress_cb:
            progress_cb(i, count, f"Downloading {ch_label}…")

        try:
            ch_out = run_pipeline(ch_url)
        except Exception as exc:
            if progress_cb:
                progress_cb(i, count, f"{ch_label} failed: {exc}")
            continue

        if not ch_out or not os.path.isdir(ch_out):
            if progress_cb:
                progress_cb(i, count, f"{ch_label} skipped (no pages)")
            continue

        zip_path = os.path.join(out_dir, f"chapter_{ch:04d}.zip")
        if progress_cb:
            progress_cb(i, count, f"Zipping {ch_label}…")
        zip_folder(ch_out, zip_path)
        zips.append(zip_path)
        if progress_cb:
            progress_cb(i + 1, count, f"{ch_label} ✓ → {os.path.basename(zip_path)}")

    return zips
