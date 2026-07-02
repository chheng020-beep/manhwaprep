"""Download all page images from a manhwa chapter URL.

A generic scraper: it does not hardcode a domain (11toon and similar Korean
toon sites rotate domains), so you paste the live chapter URL from your
browser. It collects <img> sources (including lazy-load attributes) plus any
image URLs embedded in inline scripts, filters out obvious non-content
(logos/icons/banners/ads), and downloads in document order.

Sites that build the image list purely in JavaScript after load may not work
with a static scrape; that's the known fragile part of any downloader.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
LAZY_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-url", "src")
# image URLs embedded in inline scripts
URL_IN_SCRIPT = re.compile(
    r"https?://[^\s\"'<>]+?\.(?:jpg|jpeg|png|webp)", re.IGNORECASE
)


# WordPress resized thumbnails end in e.g. "-75x106.jpg" — never real pages.
THUMB_RE = re.compile(r"-\d+x\d+\.(?:jpg|jpeg|png|webp|gif)$", re.IGNORECASE)


def _from_img_tag(tag, base: str) -> str | None:
    for attr in LAZY_ATTRS:
        v = tag.get(attr)
        if v and not v.strip().startswith("data:"):
            return urljoin(base, v.strip())
    return None


def _is_reader_img(tag) -> bool:
    # Madara / WP-manga reader pages tag each page <img class="...chapter-img">.
    return any("chapter-img" in c for c in (tag.get("class") or []))


def _is_image_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(IMG_EXTS) and not THUMB_RE.search(path)


def _dedupe_images(urls: list[str]) -> list[str]:
    seen, out = set(), []
    for u in urls:
        if _is_image_url(u) and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def fetch_image_urls(chapter_url: str, session: requests.Session) -> list[str]:
    orig_host = urlparse(chapter_url).netloc
    try:
        r = session.get(chapter_url, headers=HEADERS, timeout=30, allow_redirects=True)
        r.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        # Redirect to a dead host (e.g. nuviatoon → discord.gg) fails here.
        raise RuntimeError(
            f"Site appears to have moved or shut down (connection error: {exc})"
        )
    # If we were redirected off the original domain (e.g. to discord.gg),
    # the page is gone — bail immediately rather than scraping a wrong page.
    final_host = urlparse(r.url).netloc
    if orig_host and final_host and orig_host != final_host:
        raise RuntimeError(
            f"Site redirected to {final_host} — it may have moved or shut down."
        )
    html = r.text
    soup = BeautifulSoup(html, "html.parser")

    # 1. Preferred: explicit reader images (Madara/WP-manga, the common case).
    reader = [t for t in soup.find_all("img") if _is_reader_img(t)]
    if reader:
        urls = _dedupe_images(
            [u for t in reader if (u := _from_img_tag(t, chapter_url))]
        )
        if urls:
            return urls

    # 2. Fallback: every image + script-embedded URLs, then take the largest
    #    group sharing one directory (excludes one-off logos/cursors/avatars
    #    without a fragile keyword blocklist like "ads" matching "uploads").
    found: list[str] = []
    for img in soup.find_all("img"):
        if u := _from_img_tag(img, chapter_url):
            found.append(u)
    found.extend(URL_IN_SCRIPT.findall(html))
    candidates = _dedupe_images(found)
    if not candidates:
        return []

    groups: dict[str, list[str]] = {}
    for u in candidates:
        groups.setdefault(u.rsplit("/", 1)[0], []).append(u)
    best = max(groups.values(), key=len)
    if len(best) < 3 and len(candidates) >= 3:
        return candidates
    return best


def download_via_gallery_dl(chapter_url: str, dest_dir: str) -> list[str]:
    """Download a chapter with gallery-dl (broad, maintained, handles many sites).

    Returns ordered image paths. Raises if gallery-dl isn't available or finds
    nothing, so the caller can fall back to the built-in scraper.
    """
    import subprocess
    import sys

    os.makedirs(dest_dir, exist_ok=True)
    _cache_dir = os.path.expanduser("~/ManhwaPrep/.cache/gallery-dl")
    os.makedirs(_cache_dir, exist_ok=True)
    cmd = [
        sys.executable, "-m", "gallery_dl",
        "-D", dest_dir,                 # flat directory, no per-site subfolders
        "--no-part",
        "--cache-file", os.path.join(_cache_dir, "cache.sqlite"),
        chapter_url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    exts = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")

    # PAGE ORDER: gallery-dl prints each file it writes, in download (page) order.
    # Trust that — sorting by filename breaks when a site names pages with
    # timestamps / random IDs (which is what scrambles the chapter order).
    ordered, seen = [], set()
    for line in (proc.stdout or "").splitlines():
        p = line.strip().lstrip("# ").strip().strip('"')
        if not p.lower().endswith(exts):
            continue
        cand = p if os.path.isfile(p) else os.path.join(
            dest_dir, os.path.basename(p))
        if os.path.isfile(cand) and cand not in seen:
            ordered.append(cand)
            seen.add(cand)
    if ordered:
        return ordered

    # fallback only if we couldn't read the order from stdout
    imgs = [
        os.path.join(dest_dir, f)
        for f in os.listdir(dest_dir)
        if f.lower().endswith(exts)
    ]
    if not imgs:
        tail = (proc.stderr or proc.stdout or "")[-300:]
        raise RuntimeError(f"gallery-dl downloaded no images. {tail}")
    imgs.sort(key=lambda p: _natural(os.path.basename(p)))
    return imgs


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def download(chapter_url: str, dest_dir: str, progress=None, control=None) -> list[str]:
    """Download every chapter image into dest_dir. Returns ordered paths."""
    os.makedirs(dest_dir, exist_ok=True)
    session = requests.Session()
    urls = fetch_image_urls(chapter_url, session)
    if not urls:
        raise RuntimeError(
            "No page images found on that URL. The site may load images via "
            "JavaScript, or the link isn't a chapter reader page. Try the "
            "direct chapter page, or download with HakuNeko and drop the folder."
        )

    paths = []
    headers = {**HEADERS, "Referer": chapter_url}
    for i, u in enumerate(urls):
        if control is not None:
            control.checkpoint()
        ext = os.path.splitext(urlparse(u).path)[1].lower() or ".jpg"
        out = os.path.join(dest_dir, f"{i + 1:03d}{ext}")
        resp = session.get(u, headers=headers, timeout=60)
        resp.raise_for_status()
        with open(out, "wb") as f:
            f.write(resp.content)
        paths.append(out)
        if progress:
            progress(i + 1, len(urls))
    return paths
