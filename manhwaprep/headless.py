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

# comix.to (and similar) now block obvious headless browsers — the reader
# refuses to render and the page stays blank. These launch args + init script
# mask the automation fingerprint so the reader runs normally.
_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = {runtime: {}};
"""
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


_COMIX_HOOK = """
window.__comix_pages = [];
const _origMap = Array.prototype.map;
Array.prototype.map = function(fn, thisArg) {
    const result = _origMap.call(this, fn, thisArg);
    if (result && result.length > 2) {
        const first = result[0];
        if (first && typeof first === 'object' && 'url' in first && 'width' in first && 'height' in first) {
            const u = String(first.url);
            if (u.startsWith('http') || u.startsWith('/')) {
                window.__comix_pages.push({
                    count: result.length,
                    items: _origMap.call(result, function(x) {
                        return {url: x.url, w: x.width || 0, h: x.height || 0};
                    })
                });
            }
        }
    }
    return result;
};
"""


def _collect_comix(url: str, timeout_ms: int = 60000) -> list[str]:
    """comix.to uses encrypted XHR responses decrypted by secure.js at runtime.
    Hooking Array.prototype.map intercepts the page list just after decryption,
    before the reader renders (which only shows 4 images at a time)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1200, "height": 1000})
        page = ctx.new_page()
        page.add_init_script(_STEALTH_JS)
        page.add_init_script(_COMIX_HOOK)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        # the reader decrypts and builds the page-list array shortly after load;
        # the map hook captures it. give it a moment, then retry if it's slow.
        captured = None
        for _ in range(8):
            page.wait_for_timeout(1500)
            captured = page.evaluate("window.__comix_pages")
            if captured:
                break
        browser.close()

    # Pick the largest captured array (the chapter page list, not UI arrays)
    if not captured:
        return []
    best = max(captured, key=lambda c: c["count"])

    # Compute the median height/width ratio across pages that have valid dimensions.
    # Real webtoon strips are very tall (ratio >= 2); chapter-navigation/thumbnail
    # gallery pages inserted by comix.to tend to be near-square or shorter.
    items = best["items"]
    ratios = [it["h"] / it["w"] for it in items
              if isinstance(it, dict) and it.get("w", 0) > 0 and it.get("h", 0) > 0]
    if ratios:
        ratios_sorted = sorted(ratios)
        median_ratio = ratios_sorted[len(ratios_sorted) // 2]
        # Keep only pages whose ratio is at least 40% of the median.
        # This drops near-square or landscape thumbnail-grid pages while keeping
        # pages that happen to be shorter (e.g. a short epilogue panel).
        min_ratio = max(1.0, median_ratio * 0.40)
    else:
        min_ratio = 1.0  # no dimension data — keep everything portrait-ish

    seen: set[str] = set()
    result = []
    for it in items:
        if isinstance(it, dict):
            u = str(it.get("url", ""))
            w, h = it.get("w", 0), it.get("h", 0)
        else:
            u = str(it)
            w = h = 0
        if not u or u in seen:
            continue
        if w > 0 and h > 0 and (h / w) < min_ratio:
            continue  # skip thumbnail-grid / landscape nav page
        seen.add(u)
        result.append(u)
    return result


def _collect(url: str, timeout_ms: int = 90000) -> list[str]:
    _ensure_chromium()
    from playwright.sync_api import sync_playwright

    # comix.to fast path: use Array.map hook to get all pages from encrypted API
    host = urlparse(url).netloc.lower()
    if "comix.to" in host or "comick.io" in host or "comick.fun" in host:
        pages = _collect_comix(url, timeout_ms=60000)
        if pages:
            return pages

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


def _render_comix(url: str, timeout_ms: int = 180000) -> list[bytes]:
    """Screenshot each comix.to page after the browser renders it.

    comix.to serves chapter images with pixel-level tile scrambling (DRM).
    The JS reader draws the correct tile order onto a <canvas> element.
    Screenshotting the canvas via Playwright captures what the user sees —
    bypassing the scrambling entirely without needing to reverse-engineer it.

    Scrolls slowly through the reader so lazy-loaded pages have time to render,
    capturing each large canvas/image at its document-relative position so that
    virtual-scrolling readers (where elements are removed from the DOM once
    off-screen) still produce a complete set.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 940, "height": 1080})
        pg = ctx.new_page()
        pg.add_init_script(_STEALTH_JS)
        pg.add_init_script(_COMIX_HOOK)

        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        pg.wait_for_timeout(5000)

        shots_by_y: dict[int, bytes] = {}  # doc-relative Y → screenshot bytes

        def _snap():
            """Screenshot every large canvas/img currently in the DOM."""
            for sel in ("canvas", "img"):
                try:
                    for el in pg.query_selector_all(sel):
                        try:
                            bb = el.bounding_box()
                            if not bb or bb["height"] < 400 or bb["width"] < 400:
                                continue
                            # document-relative centre Y for stable deduplication
                            doc_y = int(el.evaluate(
                                "e => Math.round(e.getBoundingClientRect().top"
                                " + window.scrollY + e.getBoundingClientRect().height / 2)"
                            ))
                            if doc_y in shots_by_y:
                                continue
                            shots_by_y[doc_y] = el.screenshot(type="jpeg", quality=90)
                        except Exception:
                            pass
                except Exception:
                    pass

        prev_h = -1
        for _ in range(300):
            pg.mouse.wheel(0, 1200)
            pg.wait_for_timeout(200)
            _snap()
            h = pg.evaluate("document.body.scrollHeight")
            if h == prev_h:
                pg.wait_for_timeout(2000)
                if pg.evaluate("document.body.scrollHeight") == prev_h:
                    break
            prev_h = h

        pg.wait_for_timeout(2000)
        _snap()  # final pass after all content settles
        browser.close()

    return [v for _, v in sorted(shots_by_y.items())]


def download_via_browser(chapter_url: str, dest_dir: str) -> list[str]:
    """Render the chapter in a headless browser and download its images."""
    os.makedirs(dest_dir, exist_ok=True)
    _ensure_chromium()

    host = urlparse(chapter_url).netloc.lower()
    if "comix.to" in host or "comick.io" in host or "comick.fun" in host:
        # comix.to no longer scrambles pages (no more canvas DRM) — the reader
        # loads plain image URLs, which the map hook captures in full. Download
        # them directly: far faster and lossless vs. screenshotting each page.
        urls = _collect_comix(chapter_url, timeout_ms=90000)
        if len(urls) >= 3:
            paths = _download_urls(urls, dest_dir, referer="https://comix.to/")
            if paths:
                return paths
        # Fallback for any title that still uses canvas tile-scrambling:
        # screenshot each page after the JS reader renders it.
        shots = _render_comix(chapter_url)
        if shots:
            paths = []
            for i, data in enumerate(shots):
                out = os.path.join(dest_dir, f"{i + 1:03d}.jpg")
                with open(out, "wb") as f:
                    f.write(data)
                paths.append(out)
            return paths

    urls = _collect(chapter_url)
    if not urls:
        raise RuntimeError("headless browser found no chapter images on the page.")
    return _download_urls(urls, dest_dir, referer=chapter_url)


# content-type → extension, so extension-less CDN URLs (comix.to's image host
# returns webp with no path suffix) are still saved with a correct extension.
_CT_EXT = {"image/webp": ".webp", "image/jpeg": ".jpg", "image/png": ".png",
           "image/gif": ".gif"}


def _download_urls(urls: list[str], dest_dir: str, referer: str) -> list[str]:
    """Download image URLs to dest_dir as 001.ext, 002.ext … in order."""
    os.makedirs(dest_dir, exist_ok=True)
    session = requests.Session()
    headers = {"User-Agent": UA, "Referer": referer}
    paths = []
    for i, u in enumerate(urls):
        try:
            resp = session.get(u, headers=headers, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            print(f"[headless] page {i + 1} failed: {e}")
            continue
        ext = os.path.splitext(urlparse(u).path)[1].lower()
        if ext not in IMG_EXTS:
            ext = _CT_EXT.get(resp.headers.get("content-type", "").split(";")[0], ".jpg")
        out = os.path.join(dest_dir, f"{i + 1:03d}{ext}")
        with open(out, "wb") as f:
            f.write(resp.content)
        paths.append(out)
    return paths
