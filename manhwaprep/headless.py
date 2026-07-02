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


def _ensure_chromium() -> None:
    """Install Chromium on first use (works inside PyInstaller EXE on Windows too)."""
    import subprocess
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if not os.path.exists(exe):
                raise FileNotFoundError(exe)
    except Exception:
        print("[headless] Chromium not found — downloading (~150 MB, one-time)…")
        # sys.executable inside a PyInstaller EXE is the EXE itself, not python.
        # Use playwright's own bundled driver binary instead.
        try:
            from playwright._impl._driver import compute_driver_executable
            # returns (node_exe, cli_js) — run as: node cli.js install chromium
            node, cli = compute_driver_executable()
            subprocess.run([str(node), str(cli), "install", "chromium"], check=True)
        except Exception:
            # last resort: try the module route (works in normal Python installs)
            import sys
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True,
            )


def _collect(url: str, timeout_ms: int = 90000) -> list[str]:
    _ensure_chromium()
    from playwright.sync_api import sync_playwright

    network: list[str] = []     # image responses seen on the wire
    api_images: list[str] = []  # image URLs found inside JSON API responses

    # Regex to pull image URLs out of JSON/JS payloads
    _IMG_URL_RE = re.compile(
        r'https?://[^\s"\'<>]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\s"\'<>]*)?',
        re.IGNORECASE,
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()

        def on_response(resp):
            try:
                ct = resp.headers.get("content-type", "")
                if ct.startswith("image/"):
                    network.append(resp.url)
                elif "json" in ct or "javascript" in ct:
                    # nuviatoon and similar sites return chapter image lists via
                    # a JSON API — extract any image URLs from the response body.
                    try:
                        body = resp.text()
                        found = _IMG_URL_RE.findall(body)
                        api_images.extend(found)
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        except Exception:
            pass  # timeout is OK — we collect what loaded

        # Also pull image URLs out of inline <script> tags in the rendered DOM.
        try:
            inline_js = page.eval_on_selector_all(
                "script:not([src])",
                "els => els.map(e => e.textContent).join('\\n')"
            )
            api_images.extend(_IMG_URL_RE.findall(inline_js))
        except Exception:
            pass

        # Scroll to trigger lazy-loaded images
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

        # DOM: img tags (currentSrc / data-src / src)
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
        # picture/source srcset
        sources = page.eval_on_selector_all(
            "source[srcset], source[data-srcset]",
            """els => els.map(e => {
                const s = e.getAttribute('srcset') || e.getAttribute('data-srcset') || '';
                return s.split(',').map(x => x.trim().split(' ')[0]).filter(Boolean);
            }).flat()"""
        )
        browser.close()

    # Combine: DOM first (reading order), then API/script URLs, then network.
    dom = [u for u in dom if u and u.startswith("http")]
    dom += [u for u in sources if u and u.startswith("http")]
    all_api = [u for u in api_images if u.startswith("http")]

    dom_pages = _pick_chapter_group(dom)
    if len(dom_pages) >= 3:
        return dom_pages

    api_pages = _pick_chapter_group(all_api)
    if len(api_pages) >= 3:
        return api_pages

    net_pages = _pick_chapter_group(network)
    best = max([dom_pages, api_pages, net_pages], key=len)
    return best


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
