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


def _filter_comix_items(captured) -> list[str]:
    """Turn the captured page-list arrays into ordered image URLs, dropping the
    thumbnail-grid / landscape navigation pages comix.to mixes in. Shared by the
    scramble-free direct-download path and _collect_comix."""
    if not captured:
        return []
    best = max(captured, key=lambda c: c["count"])
    # Median height/width ratio: real webtoon strips are tall (ratio >= 2);
    # chapter-nav / thumbnail-gallery pages tend to be near-square or shorter.
    items = best["items"]
    ratios = [it["h"] / it["w"] for it in items
              if isinstance(it, dict) and it.get("w", 0) > 0 and it.get("h", 0) > 0]
    if ratios:
        ratios_sorted = sorted(ratios)
        median_ratio = ratios_sorted[len(ratios_sorted) // 2]
        # Keep pages at least 40% as tall (per width) as the median — drops
        # near-square/landscape thumbnail pages, keeps short epilogue panels.
        min_ratio = max(1.0, median_ratio * 0.40)
    else:
        min_ratio = 1.0  # no dimension data — keep everything portrait-ish
    seen: set[str] = set()
    result: list[str] = []
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

    # captured holds the largest page-list array; filter out nav/thumbnail pages.
    return _filter_comix_items(captured)


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


def _reader_has_canvas(pg) -> bool:
    """True if the comix reader has painted a real page onto a <canvas> — i.e.
    it is tile-scrambling. Decided per run so it survives comix.to switching the
    scramble DRM on or off between chapters."""
    try:
        return bool(pg.evaluate(
            "() => [...document.querySelectorAll('canvas')]"
            ".some(c => c.width > 300 && c.height > 300)"
        ))
    except Exception:
        return False


def _capture_scrambled_pages(pg) -> list[bytes]:
    """Scroll the already-open comix reader and capture each descrambled page.

    comix.to scrambles page tiles (DRM); its JS reassembles them onto a
    <canvas class="rpage-page__img">. We read that canvas — lossless via
    toDataURL when it isn't CORS-tainted, otherwise a full-resolution PNG
    screenshot of the same element. Pages are keyed by the reader's stable
    `data-page` index (robust against virtual scrolling) and captured only once
    their `.rpage-page` wrapper clears the `is-loading` class (fully drawn).
    Returns PNG bytes ordered by page index.
    """
    import base64

    captured: dict[int, bytes] = {}   # data-page index → PNG bytes

    def _snap():
        # only wrappers that finished descrambling and didn't error
        for wrap in pg.query_selector_all(
                ".rpage-page:not(.is-loading):not(.is-errored)"):
            try:
                raw = wrap.get_attribute("data-page")
                if raw is None:
                    continue
                idx = int(raw)
                if idx in captured:
                    continue
                el = (wrap.query_selector("canvas.rpage-page__img")
                      or wrap.query_selector("canvas"))
                is_canvas = el is not None
                if el is None:
                    el = wrap.query_selector("img")
                if el is None:
                    continue
                bb = el.bounding_box()
                if not bb or bb["height"] < 200 or bb["width"] < 200:
                    continue
                data = None
                if is_canvas:
                    try:  # lossless read of the descrambled pixels
                        durl = el.evaluate("c => c.toDataURL('image/png')")
                        if isinstance(durl, str) and durl.startswith("data:image"):
                            data = base64.b64decode(durl.split(",", 1)[1])
                    except Exception:
                        data = None            # CORS-tainted → screenshot below
                if data is None:
                    data = el.screenshot(type="png")
                captured[idx] = data
            except Exception:
                pass

    prev_h = -1
    for _ in range(400):
        pg.mouse.wheel(0, 1000)
        pg.wait_for_timeout(200)
        _snap()
        h = pg.evaluate("document.body.scrollHeight")
        if h == prev_h:
            pg.wait_for_timeout(1500)
            _snap()
            if pg.evaluate("document.body.scrollHeight") == prev_h:
                break
        prev_h = h

    pg.wait_for_timeout(1500)
    _snap()  # final pass after everything settles

    if captured:
        lo, hi = min(captured), max(captured)
        missing = [i for i in range(lo, hi + 1) if i not in captured]
        if missing:
            print(f"[headless] comix: pages not captured (still loading?): {missing}")
    return [v for _, v in sorted(captured.items())]


def download_via_browser(chapter_url: str, dest_dir: str) -> list[str]:
    """Render the chapter in a headless browser and download its images."""
    os.makedirs(dest_dir, exist_ok=True)
    _ensure_chromium()

    host = urlparse(chapter_url).netloc.lower()
    if "comix.to" in host or "comick.io" in host or "comick.fun" in host:
        # comix.to may or may not tile-scramble a given chapter (DRM toggles over
        # time), so decide fresh in one browser session: if the reader paints
        # pages onto <canvas>, capture the descrambled canvas; otherwise the
        # reader serves plain image URLs we can direct-download (faster, lossless).
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
            ctx = browser.new_context(
                user_agent=UA, viewport={"width": 1000, "height": 1080},
                device_scale_factor=2)  # sharper screenshot fallback if tainted
            pg = ctx.new_page()
            pg.add_init_script(_STEALTH_JS)
            pg.add_init_script(_COMIX_HOOK)
            try:
                pg.goto(chapter_url, wait_until="domcontentloaded", timeout=120000)
            except Exception:
                pass
            pg.wait_for_timeout(5000)

            # Nudge the first page to paint before deciding scramble vs. plain.
            scrambled = _reader_has_canvas(pg)
            if not scrambled:
                pg.mouse.wheel(0, 800)
                pg.wait_for_timeout(2500)
                scrambled = _reader_has_canvas(pg)

            if scrambled:
                shots = _capture_scrambled_pages(pg)
                browser.close()
                if len(shots) >= 3:
                    paths = []
                    for i, data in enumerate(shots):
                        out = os.path.join(dest_dir, f"{i + 1:03d}.png")
                        with open(out, "wb") as f:
                            f.write(data)
                        paths.append(out)
                    return paths
                raise RuntimeError(
                    "comix.to rendered too few pages — the reader may be blank or "
                    "behind a Cloudflare challenge. Try again, or use HakuNeko.")

            # not scrambled: the map hook captured plain image URLs
            captured = pg.evaluate("window.__comix_pages")
            browser.close()

        urls = _filter_comix_items(captured)
        if len(urls) >= 3:
            paths = _download_urls(urls, dest_dir, referer="https://comix.to/")
            if paths:
                return paths
        raise RuntimeError("comix.to: no chapter pages found on that URL.")

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
