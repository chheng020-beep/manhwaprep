# Perfect Size Text

Auto-fits a Khmer translation into a speech-bubble box: pick the largest font
size that word-wraps the text so it fills the box without overflowing and
without breaking mid-word/mid-syllable. Runs entirely locally — no network,
no API — and only touches the box it's applied to.

## Where it lives

- `manhwaprep/perfect_size.py` — the algorithm (`segment`, `fit`, and their
  helpers). Pure functions, no Qt widgets beyond `QFont`/`QFontMetricsF` for
  measurement.
- `manhwaprep/assets/khmer_words.txt` — the bundled Khmer dictionary
  (33,856 words, one per line, UTF-8, no header). Sourced from SIL
  International's `khmerlbdict` (github.com/silnrsi/khmerlbdict, MIT
  licensed); see `.superpowers/sdd/task-2-report.md` for the full provenance
  and cleaning steps.
- `manhwaprep/typeset_editor.py` — `TextBoxItem.apply_perfect_size()` wires
  the algorithm into the editor (the only caller of `fit()`).

## Module API

### `segment(text, words=None) -> list[str]`

Splits `text` into legal line-break units such that `"".join(segment(text))
== text` exactly (lossless tiling). Khmer runs are segmented against the
dictionary using **forward longest-match**: at each position, try the
longest dictionary word starting there, shrinking one character at a time
until a match is found; if nothing matches, fall back to a single
orthographic syllable (see `syllable_split`) so a coeng cluster or dependent
vowel is never orphaned at a line break. Non-Khmer runs (Latin text, digits,
punctuation, spaces) split on whitespace, keeping each word with its
trailing spaces as one token.

`words` defaults to `load_words()` (the memoised bundled dictionary); pass
an explicit set to test against a different wordlist.

### `syllable_split(khmer_run) -> list[str]`

Splits a run of Khmer characters into orthographic syllables (Khmer
Cluster/Character-style: base consonant or independent vowel, optional
`្`+subscript-consonant sequences, optional dependent vowels/signs). This is
the fallback unit whenever a span of text isn't a whole dictionary word —
it's finer-grained than a word but still never severs a coeng cluster.

### `load_words(path=None) -> set[str]`

Loads and memoises the dictionary from `manhwaprep/assets/khmer_words.txt`
(or an explicit `path` for testing, which is not memoised). A missing file
degrades gracefully to an empty set — `segment()` then falls back to
syllable-only splitting everywhere, so the feature keeps working (just with
coarser break points) even if the wordlist doesn't ship.

### `fit(text, box_w, box_h, font_family, margin=0.06, size_min=6.0, size_max=200.0) -> (size, lines)`

Returns the largest font size (rounded to 0.1pt) and the wrapped/balanced
line list that fits `text` inside a `box_w × box_h` box. See "The fit
algorithm" below for how.

### `TextBoxItem.apply_perfect_size(self)`

Editor-side glue in `manhwaprep/typeset_editor.py`. Reads `self.raw_text`
(the unbroken source string — not the already-wrapped `self.text`), calls
`fit(src, self.w, self.h, self.font.family())`, then:
- sets `self.max_size` and `self.font`'s point size to the returned `size`
- sets `self.text` to the returned lines joined with `\n`
- sets `self.fitted = True` — this flips the box into "held size" mode:
  `_refit()` (the normal Canva-style auto-grow-height behavior) becomes a
  no-op and the box keeps exactly the `w × h` it was fit to.
- invalidates the render pixmap cache (`self._px_key = None`) so the next
  paint picks up the new font/text.

No-ops (returns without touching state) if `raw_text`/`text` is blank.

## The fit algorithm

1. **Segment** `text` into break-legal tokens via `segment()`.
2. **Binary-search the largest feasible font size** in `[size_min, size_max]`
   (0.5pt resolution). At each candidate size, build a `QFont`/
   `QFontMetricsF`, shrink the box by `margin` on each side
   (`avail_w, avail_h = box_w*(1-2*margin), box_h*(1-2*margin)`), and
   greedily word-wrap the tokens (`_wrap`) into lines no wider than
   `avail_w`. A size is "feasible" if `len(lines) * fm.lineSpacing() <=
   avail_h` and every line's rendered width fits `avail_w`. Keep the largest
   feasible size found.
   - `_wrap` also hard-splits any single token wider than `avail_w`
     (falls back to syllables, then raw characters) so width never overflows
     even for a pathologically long unbroken run.
3. **Balance lines** (`balance_lines`) at the winning size: redistribute
   tokens across the same number of lines to even out line widths (targets
   `total_width / n_lines` per line, ±15% tolerance), so a 2-line result
   isn't lopsided ("very long first line, three characters on the second").
   Falls back to the plain greedy wrap if balancing would exceed the line
   count or overflow `avail_w`.
4. **Overflow fallback**: if no size in `[size_min, size_max]` is feasible
   even with margins removed (`margin=0.0`) — text still doesn't fit at the
   floor size — return `size_min` with whatever wrap `_wrap` produces (never
   raises; may render clipped/overflowing in that box).

The box's own size is never changed by `fit()` — the caller
(`apply_perfect_size`) holds `self.w`/`self.h` fixed and just changes font
size + line breaks. Growing/shrinking the box is what triggers a re-fit
(see Triggers below), not the other way around.

## The wordlist

- **Location**: `manhwaprep/assets/khmer_words.txt`
- **Format**: one Khmer word per line, UTF-8, no header, no trailing
  metadata — exactly what `load_words()` expects (blank lines are skipped).
- **Current size**: 33,856 words, sourced from SIL's `khmerlbdict` (MIT).
- **Growing it**: append new words (one per line) and dedupe, e.g.:
  ```bash
  sort -u manhwaprep/assets/khmer_words.txt -o manhwaprep/assets/khmer_words.txt
  ```
  after appending. Keep entries to Khmer-script codepoints (U+1780–U+17FF)
  only — non-Khmer or mixed-script lines just add dead weight since
  `_khmer_runs()` only sends Khmer runs through the dictionary matcher.
  Restart the app (or clear the `_WORDS` memo, e.g. reload the process) to
  pick up changes — `load_words()` caches on first call.
- **Packaging**: bundled in `manhwaprep.spec`'s `datas` list
  (`("manhwaprep/assets/khmer_words.txt", "manhwaprep/assets")`) alongside
  the other `manhwaprep/assets/*` entries, so it ships in the PyInstaller
  `.exe` and is found at the same relative path via
  `_default_wordlist_path()` (`os.path.dirname(__file__)/assets/...`).

## Tuning knobs

- `margin` (default `0.06`): fraction of `box_w`/`box_h` reserved as
  breathing room on each side before wrapping. Raise it if fitted text looks
  too tight against the bubble edge; lower it to pack bubbles more densely.
- `size_min` / `size_max` (defaults `6.0` / `200.0`): binary-search bounds.
  These mirror `TextBoxItem.FONT_MIN` / `FONT_MAX` (also `6.0`/`200.0`) —
  keep them in sync if either changes, since `apply_perfect_size` clamps
  `max_size` through those same constants elsewhere in `_refit()`.

## Triggers (when `apply_perfect_size()` runs automatically)

- **Text commit**: in `typeset_editor.py`, both the side-panel text editor
  (`_text_changed`) and the inline on-canvas editor (`_commit_inline`) set
  `it.raw_text = <new text>` and immediately call `it.apply_perfect_size()`.
  If the new text is blank, `apply_perfect_size` no-ops and the caller falls
  back to `it._refit()` (plain auto-grow) since `fitted` state is unaffected.
- **Box resize**: `TextBoxItem.mouseReleaseEvent` calls
  `self.apply_perfect_size()` after any drag-resize (`was_resize != "rot"`,
  i.e. any edge/corner resize but not a rotation drag) **if the box was
  already fitted** (`self.fitted` is `True`) — so once a box has been
  perfect-sized once, resizing it re-fits to the new dimensions instead of
  reverting to auto-grow-height.

Not triggered by rotation drags, and not triggered on a fresh (never
committed) box until its first text commit.

## Debugging a bad fit

1. Check `it.fitted` — if `False`, the box is still in auto-grow mode and
   `fit()` was never called (e.g. text was blank when last committed).
2. Check `it.max_size` — the font size `fit()` picked. If it's pinned at
   `FONT_MIN` (6.0), the text didn't fit at any size — likely `box_w`/
   `box_h` is too small for the translation, or a single token is
   pathologically long (check for missing spaces / a run-on word that
   isn't in the dictionary and isn't splitting at syllables as expected).
3. Check `it.text` — the wrapped lines `fit()` produced, joined with `\n`.
   Inspect line breaks for mid-word splits (a dictionary gap) or lopsided
   line lengths (a `balance_lines` edge case).
4. Check `it.raw_text` — the untouched source string `fit()` was run
   against. If `it.text` looks wrong but `it.raw_text` is fine, the bug is
   in `fit()`/`segment()`, not in what the user typed.
5. Render the box (open the chapter in the editor, or inspect the exported
   canvas) to see the actual painted result — outline/effects sometimes make
   a technically-correct wrap look tight because of `outline_w` eating into
   the visual margin.
6. To isolate the algorithm from the editor, call `fit()` directly:
   ```python
   from manhwaprep import perfect_size as ps
   size, lines = ps.fit("<khmer text>", box_w, box_h, "Battambang")
   print(size, lines)
   ```
