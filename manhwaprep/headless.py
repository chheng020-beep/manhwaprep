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


def _blankish(png_bytes: bytes) -> bool:
    """True if a screenshot is (near-)uniform — an unpainted/blank canvas. comix
    clears the `is-loading` class BEFORE the descramble actually paints, so we
    must reject blank grabs and retry while the page is still on screen."""
    try:
        import io
        from PIL import Image, ImageStat
        im = Image.open(io.BytesIO(png_bytes)).convert("L")
        return ImageStat.Stat(im).stddev[0] < 3.0
    except Exception:
        return False


def _capture_comix_pages(pg, page_count: int) -> dict:
    """Scroll the comix reader top-to-bottom and capture every page in order.

    comix.to scrambles roughly every Nth page as pixel tiles and reassembles it
    live onto a <canvas class="rpage-page__img"> (DRM); the rest are plain <img>.
    The scramble is PER PAGE, so there is no single strategy for the whole
    chapter — we decide per page while scrolling:

      * <img>    → record its URL (downloaded later: lossless and fast)
      * <canvas> → screenshot the descrambled pixels. toDataURL returns BLANK on
                   comix's canvas (its context isn't readable), so a screenshot
                   is the only way; blank-check it and keep scrolling so a still-
                   painting page gets more tries before it recycles.

    The reader is a scroll-driven virtual swiper (only ~5 pages live at once), so
    we scroll continuously rather than jump — jumping never triggers its lazy
    render. Returns {data_page: ('url', str) | ('shot', png_bytes)}."""
    captured: dict[int, tuple] = {}

    def _snap():
        rows = pg.evaluate("""() => [...document.querySelectorAll('.rpage-page')].map(w => {
            const r = w.getBoundingClientRect();
            const c = w.querySelector('canvas'), i = w.querySelector('img');
            return {
                dp: w.getAttribute('data-page'),
                loading: w.className.includes('is-loading'),
                errored: w.className.includes('is-errored'),
                kind: c ? 'canvas' : (i ? 'img' : 'none'),
                src: i ? (i.currentSrc || i.src || '') : '',
                onscreen: r.bottom > -200 && r.top < window.innerHeight + 200,
            };
        })""")
        for r in rows:
            if not r["dp"] or r["loading"] or r["errored"] or not r["onscreen"]:
                continue
            idx = int(r["dp"])
            if idx in captured:
                continue
            if r["kind"] == "img" and r["src"].startswith("http"):
                captured[idx] = ("url", r["src"])
            elif r["kind"] == "canvas":
                el = pg.query_selector(f'.rpage-page[data-page="{idx}"] canvas')
                if el is None:
                    continue
                try:
                    shot = el.screenshot(type="png")
                except Exception:
                    continue
                if not _blankish(shot):        # only keep a genuinely painted page
                    captured[idx] = ("shot", shot)

    prev_y, stall = -1, 0
    for _ in range(2000):
        pg.mouse.wheel(0, 550)
        pg.wait_for_timeout(350)
        _snap()
        if page_count and len(captured) >= page_count:
            break
        at_bottom = pg.evaluate(
            "(window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 5)")
        if at_bottom:
            for _ in range(6):     # dwell so the last canvas pages finish painting
                pg.wait_for_timeout(600)
                _snap()
            break
        y = pg.evaluate("window.scrollY")
        stall = stall + 1 if y == prev_y else 0
        if stall > 8:
            break
        prev_y = y

    return captured


def download_via_browser(chapter_url: str, dest_dir: str) -> list[str]:
    """Render the chapter in a headless browser and download its images."""
    os.makedirs(dest_dir, exist_ok=True)
    _ensure_chromium()

    host = urlparse(chapter_url).netloc.lower()
    if "comix.to" in host or "comick.io" in host or "comick.fun" in host:
        # comix.to scrambles ~every Nth page (canvas DRM) and serves the rest as
        # plain images — PER PAGE — so scroll the whole reader and capture each
        # page the right way: screenshot the descrambled canvases, URL for imgs.
        from playwright.sync_api import sync_playwright

        captured_pages = {}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
            ctx = browser.new_context(
                user_agent=UA, viewport={"width": 1000, "height": 1080},
                device_scale_factor=2)
            pg = ctx.new_page()
            pg.add_init_script(_STEALTH_JS)
            pg.add_init_script(_COMIX_HOOK)
            try:
                pg.goto(chapter_url, wait_until="domcontentloaded", timeout=120000)
            except Exception:
                pass
            pg.wait_for_timeout(5000)
            cap = pg.evaluate("window.__comix_pages") or []
            page_count = max((c["count"] for c in cap), default=0)
            captured_pages = _capture_comix_pages(pg, page_count)
            browser.close()

        if len(captured_pages) < 3:
            raise RuntimeError(
                "comix.to rendered too few pages — the reader may be blank or "
                "behind a Cloudflare challenge. Try again, or use HakuNeko.")

        # Materialize in reading order: download the plain-image pages, write the
        # screenshotted (descrambled) canvas pages.
        session = requests.Session()
        headers = {"User-Agent": UA, "Referer": "https://comix.to/"}
        paths = []
        for idx in sorted(captured_pages):
            kind, val = captured_pages[idx]
            if kind == "shot":
                out = os.path.join(dest_dir, f"{idx:03d}.png")
                with open(out, "wb") as f:
                    f.write(val)
                paths.append(out)
                continue
            try:
                resp = session.get(val, headers=headers, timeout=60)
                resp.raise_for_status()
            except Exception as e:
                print(f"[headless] comix page {idx} failed: {e}")
                continue
            ext = os.path.splitext(urlparse(val).path)[1].lower()
            if ext not in IMG_EXTS:
                ext = _CT_EXT.get(
                    resp.headers.get("content-type", "").split(";")[0], ".jpg")
            out = os.path.join(dest_dir, f"{idx:03d}{ext}")
            with open(out, "wb") as f:
                f.write(resp.content)
            paths.append(out)
        if len(paths) >= 3:
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
