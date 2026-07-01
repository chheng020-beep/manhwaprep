"""Headless-browser downloader for JS-rendered / bot-protected toon sites.

Sites like nuviatoon serve a JS app (often behind a bot check) and load the
chapter images client-side, so static scraping and gallery-dl see nothing. Here
we render the page in headless Chromium, scroll to trigger lazy-loading, collect
the images the browser actually loaded (network responses + the rendered DOM, in
reading order), pick the chapter group, and download them.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from urllib.parse import urlparse

import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
THUMB_RE = re.compile(r"-\d+x\d+\.(?:jpg|jpeg|png|webp|gif)$", re.IGNORECASE)
SKIP_HINTS = ("/covers/", "/cover/", "avatar", "logo", "favicon", "icon", "banner")


def _looks_like_page(url: str) -> bool:
    low = url.lower()
    if any(h in low for h in SKIP_HINTS):
        return False
    if THUMB_RE.search(urlparse(url).path):
        return False
    return True


def _pick_chapter_group(ordered_urls: list[str]) -> list[str]:
    """Keep the largest group of images sharing one directory, in given order."""
    seen, cands = set(), []
    for u in ordered_urls:
        if u and u not in seen and _looks_like_page(u):
            seen.add(u)
            cands.append(u)
    if not cands:
        return []
    groups: dict[str, list[str]] = {}
    for u in cands:
        groups.setdefault(u.rsplit("/", 1)[0], []).append(u)
    best = max(groups.values(), key=len)
    return best if len(best) >= 2 else cands


def _collect(url: str, timeout_ms: int = 90000) -> list[str]:
    from playwright.sync_api import sync_playwright

    network: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()

        def on_response(resp):
            try:
                ct = resp.headers.get("content-type", "")
                if ct.startswith("image/"):
                    network.append(resp.url)
            except Exception:
                pass

        page.on("response", on_response)
        # networkidle catches sites (comix.to etc.) that finish XHR after DOM load
        try:
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        except Exception:
            pass  # timeout is OK — we'll still collect what loaded

        # scroll to bottom in steps to trigger lazy-loaded images
        prev = -1
        for _ in range(80):
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(300)
            h = page.evaluate("document.body.scrollHeight")
            if h == prev:
                page.wait_for_timeout(1000)
                if page.evaluate("document.body.scrollHeight") == prev:
                    break
            prev = h
        page.wait_for_timeout(2000)

        # Collect img srcs: check currentSrc, src, AND data-src / data-original
        # (comix.to uses data-src for lazy-loaded chapter images).
        dom = page.eval_on_selector_all(
            "img",
            """els => els.map(e =>
                e.currentSrc ||
                e.getAttribute('data-src') ||
                e.getAttribute('data-original') ||
                e.getAttribute('data-lazy-src') ||
                e.src || ''
            )"""
        )
        # Also grab any high-resolution src from picture/source elements
        sources = page.eval_on_selector_all(
            "source[srcset], source[data-srcset]",
            """els => els.map(e => {
                const s = e.getAttribute('srcset') || e.getAttribute('data-srcset') || '';
                return s.split(',').map(x => x.trim().split(' ')[0]).filter(Boolean);
            }).flat()"""
        )
        browser.close()

    # Prefer DOM order (reading order); supplement with network captures.
    dom = [u for u in dom if u and u.startswith("http")]
    dom += [u for u in sources if u and u.startswith("http")]
    dom_pages = _pick_chapter_group(dom)
    if len(dom_pages) >= 3:
        return dom_pages
    # Fall back to network order if DOM extraction was sparse
    net_pages = _pick_chapter_group(network)
    return net_pages if len(net_pages) >= len(dom_pages) else dom_pages


def download_via_browser(chapter_url: str, dest_dir: str) -> list[str]:
    """Render the chapter in a headless browser and download its images."""
    os.makedirs(dest_dir, exist_ok=True)
    urls = _collect(chapter_url)
    if not urls:
        raise RuntimeError("headless browser found no chapter images on the page.")

    session = requests.Session()
    headers = {"User-Agent": UA, "Referer": chapter_url}
    paths = []
    for i, u in enumerate(urls):
        ext = os.path.splitext(urlparse(u).path)[1].lower()
        if ext not in IMG_EXTS:
            ext = ".jpg"
        out = os.path.join(dest_dir, f"{i + 1:03d}{ext}")
        resp = session.get(u, headers=headers, timeout=60)
        resp.raise_for_status()
        with open(out, "wb") as f:
            f.write(resp.content)
        paths.append(out)
    return paths
