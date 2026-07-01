# PyInstaller spec for the ManhwaPrep Windows .exe (cleaner + Khmer typeset).
# Build:  pyinstaller manhwaprep.spec
# ONNX detection/inpaint models download on first run; the small PP-OCR models
# and the Khmer fonts are vendored and bundled so typeset works offline.

from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas, binaries, hidden = [], [], []
# Collect onnxruntime (DirectML GPU DLLs) and the rapidocr stack — including its
# native geometry deps and the config/grammar packages it loads dynamically — so
# the typeset transcript OCR runs inside the .exe.
for pkg in ("onnxruntime", "rapidocr", "shapely", "pyclipper", "omegaconf"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += h
    except Exception:
        pass

a = Analysis(
    ["app_entry.py"],
    pathex=["."],
    binaries=binaries,
    datas=[
        ("manhwaprep/assets/icon.png", "manhwaprep/assets"),
        ("manhwaprep/assets/fonts", "manhwaprep/assets/fonts"),
        ("manhwaprep/ocr_models", "manhwaprep/ocr_models"),
    ] + datas,
    hiddenimports=[
        "onnxruntime",
        "PySide6.QtSvg",
        # lazily imported by the GUI — name them so PyInstaller bundles them
        "manhwaprep.typeset_editor",
        "manhwaprep.typeset_prep",
        "manhwaprep.transcript",
        "manhwaprep.comicdetector",
        "manhwaprep.ocr",
        "manhwaprep.batch",
        "manhwaprep.manual_split",
        # rapidocr's runtime-imported helpers PyInstaller can miss
        "colorlog",
        "tqdm",
        "six",
        "antlr4",
        "zipfile",
    ] + hidden,
    hookspath=[],
    runtime_hooks=[],
    # keep the build lean: translation + headless + unused ML stacks are out
    excludes=[
        "ctranslate2", "transformers", "torch", "sentencepiece",
        "playwright", "tkinter", "matplotlib", "pandas", "PyQt5",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ManhwaPrep",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # GUI app, no console window
    icon="manhwaprep/assets/icon.ico",
)
