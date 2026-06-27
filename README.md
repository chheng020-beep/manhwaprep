# ManhwaPrep

Drop a chapter folder **or** paste a chapter link → it **downloads**, **erases
the original text** on every page, and **stitches** them into a few long
vertical images.

Built on the EasyScanlate blueprint: it reuses EasyScanlate's ONNX text-
detection model and the Telea inpaint, decoupled from the Qt app.

## Run

```bash
~/ManhwaPrep/run.sh                 # opens the window
~/ManhwaPrep/run.sh ~/some/folder   # headless: clean+stitch a folder
~/ManhwaPrep/run.sh "https://…11toon…chapter…"   # download+clean+stitch
~/ManhwaPrep/run.sh ~/folder --segments 6        # ~6 long images instead of 5
~/ManhwaPrep/run.sh "<url>" --inpaint lama        # best quality (slow); default is migan
~/ManhwaPrep/run.sh "<url>" --keep-sfx           # erase speech bubbles only, keep SFX
~/ManhwaPrep/run.sh "<url>" --translate ko       # also write a Khmer translation sheet
```

In the **GUI** you also get: a **Keep SFX** checkbox, a **Translate to Khmer**
dropdown (Off / Korean / English), an **Open translation editor** button, and
**Pause / Stop** buttons (they take effect at the next page boundary).

## Khmer translation (optional, off by default)

**English base is best** — NLLB's strongest Khmer direction is `eng_Latn→khm`,
so prefer English scanlations over Korean raws and use `--translate en`.

The translate step is **script-aware**: it reads each region with both the
English and Korean recognizers and routes by script — English dialogue is
translated, while Korean SFX is left as-is (or looked up in the SFX glossary),
because onomatopoeia mangles through MT. Dialogue lines are grouped per bubble.

Korean→Khmer SFX glossary: starter set in `glossary.py`; extend it by editing
`~/ManhwaPrep/sfx_glossary.json` (merged over the defaults).

When enabled, ManhwaPrep OCRs each bubble, translates dialogue to Khmer offline
with **NLLB-200** (CTranslate2), and writes a **translation sheet** — it does
NOT burn text onto the page. Outputs land in the chapter's output folder:

- `translation.json` — the editable data (source of truth)
- `translation.md` — readable sheet (page · bubble # · original → Khmer)
- `_translate/overlays/NNN.png` — pages with numbered bubbles

Edit the rough machine Khmer in the **side-by-side editor**, then typeset in
EasyScanlate:
```bash
~/EasyScanlate/.venv/bin/python -m manhwaprep.editor   # pick a translation.json
```
Left = page with numbered bubbles; right = editable original/Khmer rows; Save
writes back to `translation.json`.

Setup (one-time): needs `ctranslate2 transformers sentencepiece huggingface_hub`
in the venv and the NLLB CT2 model at `models/nllb-600m-ct2/`. The Khmer is
rough (NLLB's limit) — it's a draft to refine in the editor.

## Cleaning quality (3 repaint engines)

Text detection uses **comic-translate's RT-DETR-v2 detector**
(`models/detector_int8.onnx`, 44MB), which classifies each region as
**`bubble`**, **`text_bubble`** (dialogue), or **`text_free`** (SFX/action
text). Dialogue is always erased; SFX is erased too unless **Keep SFX** is on.
This trained model replaced the old PP-OCR + comic-text-detector + whiteness
heuristics (it separates dialogue from SFX far more reliably). The repaint
engine is selectable:

| Engine | Flag | Speed | Quality | Model |
|--------|------|-------|---------|-------|
| **MI-GAN** (default) | `--inpaint migan` | ~1–2s/page | neural, near-LaMa | `migan_pipeline_v2.onnx` ~28MB |
| **LaMa** (best) | `--inpaint lama` | ~10s/page | best | `lama_fp32.onnx` ~200MB |
| **Telea** (fastest) | `--inpaint telea` / `--fast` | <1s/page | smears over art | none (OpenCV) |

MI-GAN is the default — it reconstructs artwork behind text like LaMa but ~20×
faster (handles a whole webtoon strip in one pass). In the GUI, pick it from the
**Cleaning quality** dropdown. Any engine falls back to Telea if its model is
missing.

Note: EasyScanlate itself uses Telea — it looks good there only because it's a
*manual* editor on precise, flat regions. For automatic SFX-over-art cleaning,
MI-GAN/LaMa are needed.

`run.sh` uses EasyScanlate's existing virtualenv (it already has rapidocr,
onnxruntime, opencv, PySide6 and the OCR models). Nothing else to install.

## Downloading

URL downloads try three tiers in order, accepting the first that returns a real
chapter (≥3 pages, so a lone cover image doesn't count):

1. **gallery-dl** — broad, maintained; handles many manga/webtoon sites.
2. **built-in scraper** — static HTML (Madara/WordPress etc.).
3. **headless browser** (Playwright + Chromium) — for **JS-rendered / bot-
   protected** sites (e.g. nuviatoon) where the images load client-side. Renders
   the page, scrolls to lazy-load, grabs the chapter images. ~60–90s/chapter.

You paste the live chapter URL either way. Headless needs a one-time setup:
```bash
~/EasyScanlate/.venv/bin/python -m pip install playwright
~/EasyScanlate/.venv/bin/python -m playwright install chromium
```

## Windows build (.exe via GitHub Actions)

The Windows **core cleaner** build (download → clean → stitch; no Khmer
translation, no headless browser) is produced in the cloud — no Windows PC needed:

1. Create an empty repo on GitHub (e.g. `manhwaprep`).
2. Push this project:
   ```bash
   cd ~/ManhwaPrep
   git remote add origin https://github.com/<you>/manhwaprep.git
   git push -u origin main
   ```
3. GitHub Actions runs `.github/workflows/build-windows.yml` on a Windows runner
   automatically (or trigger it: **Actions** tab → *Build Windows EXE* → *Run
   workflow*).
4. Open the finished run and download the **`ManhwaPrep-windows`** artifact — it
   contains `ManhwaPrep.exe`.

The `.exe` ships **without models** (keeps it small). On first launch it shows a
setup window and downloads the core models (RT-DETR + MI-GAN, ~72 MB) into
`%LOCALAPPDATA%\ManhwaPrep\models`. Output → `%USERPROFILE%\ManhwaPrep\output`.

Not in the Windows core build: Khmer translation (NLLB) and the headless-browser
downloader (JS/bot-protected sites like nuviatoon) — use the macOS app for those.

## What each piece does

| File | Job |
|------|-----|
| `engine.py`     | detect text → stroke-accurate mask, **unioned with the comic-text-detector mask** → inpaint |
| `ctd.py`        | comic-text-detector (ONNX) — manga text segmentation; catches **SFX/stylized text** PP-OCR misses |
| `lama.py`       | LaMa neural inpaint (ONNX, region-wise 512 tiles) — natural blend over artwork |
| `downloader.py` | scrape a chapter URL for page images (lazy-load attrs + inline scripts), filter junk, download in order |
| `stitcher.py`   | normalize widths, glue all pages, re-cut into ~N long images (height-capped) |
| `pipeline.py`   | orchestrates: acquire → clean → stitch → write `output/<chapter>/` |
| `ui.py`         | one PySide6 window (drop / paste / Go / progress) |

## Known limits

- **Cleaning erases *all* detected text** (it does not translate). Blank pages
  come out ready for you to typeset elsewhere.
- **The downloader is the fragile part.** It scrapes static HTML. Sites that
  build the image list purely in JavaScript, or that rotate domains / add
  bot-blocks, may need the connector tuned against a live URL. If a link
  fails, download with HakuNeko and drop the folder instead.
- Output is JPG (quality 92). Change in `stitcher.py` if you want PNG.
