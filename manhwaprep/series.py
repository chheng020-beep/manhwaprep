"""Detect which series (Project) and chapter a download source belongs to.

Pure functions, no I/O. A source is a chapter URL or a local folder path."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class SeriesInfo:
    series_id: str
    series_name: str
    chapter_id: str
    chapter_name: str
    chapter_number: float | None
    series_url: str | None


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "untitled"


def _number_in(text: str) -> float | None:
    # Prefer the number that follows "chapter" so a leading numeric id
    # (e.g. "9356816-chapter-1") doesn't get mistaken for the chapter number.
    m = re.search(r"chapter[\s._-]*(\d+(?:\.\d+)?)", text or "", re.IGNORECASE)
    if m is None:
        m = re.search(r"(\d+(?:\.\d+)?)", text or "")
    return float(m.group(1)) if m else None


def _humanize_slug(slug: str) -> str:
    parts = [p for p in slug.split("-") if p]
    # drop a leading short id-code like "55kym" (contains a digit, <= 6 chars)
    if parts and len(parts[0]) <= 6 and any(c.isdigit() for c in parts[0]):
        parts = parts[1:]
    return " ".join(parts).title() if parts else slug


def detect(source: str) -> SeriesInfo:
    src = (source or "").strip()
    if not src:
        return SeriesInfo("ungrouped", "Ungrouped", "", "", None, None)

    if src.startswith("http://") or src.startswith("https://"):
        u = urlparse(src)
        host = u.netloc.lower()
        # comix.to / comick: /title/<slug>/<chapter-seg>
        m = re.search(r"/title/([^/]+)/([^/?#]+)", u.path)
        if "comix.to" in host and m:
            slug, last = m.group(1), m.group(2)
            num = None
            cm = re.search(r"chapter-(\d+(?:\.\d+)?)", last)
            if cm:
                num = float(cm.group(1))
            return SeriesInfo(
                series_id=f"comix:{slug}",
                series_name=_humanize_slug(slug),
                chapter_id=last,
                chapter_name=f"Chapter {num:g}" if num is not None else last,
                chapter_number=num,
                series_url=f"{u.scheme}://{host}/title/{slug}",
            )
        # generic URL: first meaningful path segment = series, last = chapter
        segs = [s for s in u.path.split("/") if s]
        series_seg = segs[0] if segs else host
        last = segs[-1] if segs else "chapter"
        return SeriesInfo(
            series_id=f"{host}:{series_seg}",
            series_name=_humanize_slug(series_seg),
            chapter_id=last,
            chapter_name=last,
            chapter_number=_number_in(last),
            series_url=None,
        )

    # local folder: parent = series, basename = chapter
    path = src.rstrip("/")
    parent = os.path.basename(os.path.dirname(path)) or "Ungrouped"
    base = os.path.basename(path) or "chapter"
    return SeriesInfo(
        series_id=f"folder:{parent}",
        series_name=parent,
        chapter_id=base,
        chapter_name=base,
        chapter_number=_number_in(base),
        series_url=None,
    )
