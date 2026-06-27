"""CLI / GUI entry point.

  python -m manhwaprep                      # launch the GUI
  python -m manhwaprep <folder|url>         # run headless
  python -m manhwaprep <src> --segments 6 --out ~/Desktop/out
"""

from __future__ import annotations

import argparse
import sys


def main():
    ap = argparse.ArgumentParser(prog="manhwaprep")
    ap.add_argument("source", nargs="?", help="chapter folder path or URL")
    ap.add_argument("--out", help="output root folder")
    ap.add_argument("--segments", type=int, default=5, help="target long-image count")
    ap.add_argument("--max-height", type=int, default=12000, help="max px per output")
    ap.add_argument(
        "--no-clean",
        action="store_true",
        help="skip text removal; download + stitch only",
    )
    ap.add_argument(
        "--inpaint",
        choices=["migan", "lama", "telea"],
        default="migan",
        help="repaint engine: migan (fast+good, default), lama (best/slow), telea (fastest)",
    )
    ap.add_argument(
        "--fast",
        action="store_true",
        help="alias for --inpaint telea (fastest, lowest quality)",
    )
    ap.add_argument(
        "--keep-sfx",
        action="store_true",
        help="erase only speech bubbles; keep SFX / action text",
    )
    ap.add_argument(
        "--translate",
        choices=["ko", "en"],
        help="also write a Khmer translation sheet from this source language",
    )
    args = ap.parse_args()

    if not args.source:
        from .ui import main as gui_main

        gui_main()
        return

    from .pipeline import run

    out_dir, outputs = run(
        args.source,
        out_root=args.out,
        segments=args.segments,
        max_height=args.max_height,
        clean=not args.no_clean,
        inpaint="telea" if args.fast else args.inpaint,
        keep_sfx=args.keep_sfx,
        translate=args.translate,
        on_status=lambda m: print(m, flush=True),
    )
    print(f"\nOutput: {out_dir}")
    for p in outputs:
        print("  ", p)
    if not outputs:
        sys.exit(1)


if __name__ == "__main__":
    main()
