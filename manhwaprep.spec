# PyInstaller spec for the ManhwaPrep core cleaner (Windows .exe).
# Build:  pyinstaller manhwaprep.spec
# Models are NOT bundled — they download on first run.

block_cipher = None

a = Analysis(
    ["app_entry.py"],
    pathex=["."],
    binaries=[],
    datas=[("manhwaprep/assets/icon.png", "manhwaprep/assets")],
    hiddenimports=[
        "onnxruntime",
        "PySide6.QtSvg",
    ],
    hookspath=[],
    runtime_hooks=[],
    # keep the build lean: translation + OCR-fallback + headless stacks are out
    excludes=[
        "rapidocr", "ctranslate2", "transformers", "torch", "sentencepiece",
        "playwright", "tkinter", "matplotlib", "scipy", "pandas", "PyQt5",
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
