# Perfect Size Text — Design Spec

**Date:** 2026-07-15
**Status:** Approved, ready for planning
**Repo:** ManhwaPrep (local-only; do not push to GitHub)

## Problem

Fitting the Khmer translation into each speech bubble by hand — nudging the font
size and eyeballing where lines should break — is the biggest time-sink in
typesetting a chapter (~90 bubbles). Today `TextBoxItem` keeps the font fixed
(`max_size`, default 30) and grows the box height to fit; wrapping uses
`TextWrapAnywhere`, which for Khmer (no spaces between words) breaks mid-word and
mid-cluster. So the user manually sizes every bubble.

## Goal

**Perfect Size**: given a bubble-sized box, automatically pick the largest font
that fills the box without overflowing, and break the Khmer into lines at real
word boundaries — instantly, the moment the translation is pasted in. Fully
local: no API, offline, deterministic.

Guiding preference (from the user): **function and result over optimization** —
prefer simple, correct, obviously-working code; a big bundled wordlist and
straightforward searches are fine. Verify the *rendered result* looks right.

## Non-Goals

- **Style Capture** (remembering the original English text's colour/weight/effect
  and applying it to the Khmer) is a separate, later spec. It reuses the same
  detected bbox foundation but touches the clean/detect stage.
- No API / LLM in this feature (the $25 OpenRouter credit is reserved for
  translation, which the user does on ChatGPT desktop).
- No change to detection, cleaning, export, outline, or effects.

## Design

### 1. New module: `manhwaprep/perfect_size.py`

Keeps the already-large `typeset_editor.py` from growing. Two pure, testable
functions plus a small segmenter:

```python
def segment(text: str) -> list[str]:
    """Split into tokens that are legal line-break points. Khmer runs are
    word-segmented via the bundled dictionary (longest-match), falling back to
    orthographic-syllable breaking for out-of-dictionary runs; Latin/number runs
    split on whitespace. Never returns a token that would break a Khmer cluster."""

def fit(text: str, box_w: float, box_h: float, font_family: str,
        margin: float = 0.06,
        size_min: float = 6.0, size_max: float = 200.0) -> tuple[float, list[str]]:
    """Return (font_size, lines): the largest font size at which `text`, wrapped
    at word boundaries, fits inside box_w x box_h (minus margin), and the chosen
    lines. Guarantees each line's width <= available width and total height <=
    available height (except the documented overflow fallback)."""
```

### 2. Khmer line-breaking (the "when to drop a line" part)

- **Bundled wordlist** `manhwaprep/assets/khmer_words.txt` — one Khmer word per
  line, UTF-8. Storage is not a concern, so ship a **large** list (target 100k+
  entries, merged from open Khmer wordlists) and let it grow over time; a bigger
  dictionary means fewer wrong breaks. Segmentation is forward **longest-match**
  over each maximal Khmer run.
- **Syllable fallback:** any Khmer run (or sub-run) not covered by the dictionary
  is split into Khmer orthographic syllables by rule (a base consonant plus its
  following coeng/vowel/sign cluster stays together). Breaking between syllables
  is always safe — strictly better than today's mid-cluster `TextWrapAnywhere`.
- **Mixed text:** Latin words, digits, and Khmer numerals (១០០) are their own
  tokens, split on whitespace. Spaces between tokens are collapsible break points.

### 3. The fit algorithm (simple and correct, not optimized)

Given box `w×h`, margin `m` (default 6% each side → `avail_w`, `avail_h`):

1. `tokens = segment(text)`.
2. **Search the font size.** Binary-search `s` in `[size_min, size_max]`. For each
   `s`: measure token widths with `QFontMetricsF(font @ s)`, greedily pack tokens
   into lines of width ≤ `avail_w` (if a single token exceeds `avail_w`, split it
   at syllable then character boundaries). Height = `line_count *
   fm.lineSpacing()`. `s` is feasible when height ≤ `avail_h`. Keep the largest
   feasible `s`. (Binary search is plenty; do not over-engineer.)
3. **Balance the lines** at the chosen `s`: redistribute tokens across the same
   line count to even out line widths (minimize the widest line), so the block
   sits centred in the oval instead of "long line + orphan word". Keep every line
   ≤ `avail_w`.
4. Return `(s, lines)`.

**Overflow fallback:** if the text does not fit even at `size_min` with `m`
reduced to 0, use `size_min` and the wrapped lines and let the box grow taller
than the bubble — better a slightly oversized bubble than illegible or clipped
text. This is rare and logged.

### 4. Applying the fit to a box (`TextBoxItem`)

- Store the raw pasted text as `self.text` (unchanged) so re-fitting always works
  from the clean source. Add `self.fitted: bool`, `self.target_w`, `self.target_h`
  (the bubble the box should fill), and cache the fit result `self._fit_lines`,
  `self.max_size = size`.
- When `fitted`, the box **renders the pre-computed `_fit_lines`** (joined with
  hard newlines, wrapping disabled so our breaks are authoritative) and **holds
  its target `w×h`** — it does NOT auto-grow via `_refit`. Outline, effects,
  gradient, censor, and export all keep working unchanged (they already render
  from the layout / `self.text`).
- A non-fitted box behaves exactly as today (auto-grow height, `TextWrapAnywhere`).

### 5. Triggers

- **Auto on paste / text entry:** when the inline editor commits new text into a
  box (paste from ChatGPT or typing), run `fit()` against the box's current
  `w×h`, set `fitted=True`, apply size + lines.
- **Auto on resize:** when a box is resized (dragging the bubble to match the
  balloon), re-run `fit()` from the raw text against the new `w×h`. This is the
  natural "adjust the target" gesture; no extra clicks.
- Newly auto-created boxes (from detected bboxes) adopt the bbox as their target
  `w×h`; the first paste fits into it.

### 6. Edge cases

- **Empty text:** no-op; box stays unfitted.
- **Single very long word** (no legal internal break, wider than `avail_w`): split
  at syllables, then characters, as a last resort so it never overflows width.
- **Tiny box / huge text:** overflow fallback (§3).
- **Latin-only or number-only text:** segments on whitespace; same fit path.
- **Re-editing text:** re-runs fit from the new raw text.

## File Changes

| File | Change |
|------|--------|
| `manhwaprep/perfect_size.py` | New: `segment()`, `fit()`, syllable splitter, dictionary loader (memoised) |
| `manhwaprep/assets/khmer_words.txt` | New: large bundled Khmer wordlist (100k+ target) |
| `manhwaprep/typeset_editor.py` | `TextBoxItem`: `fitted`/target fields, apply fit, render `_fit_lines` with wrapping off, skip auto-grow when fitted; wire paste-commit and resize to `fit()` |
| `manhwaprep.spec` | Bundle `assets/khmer_words.txt` into the PyInstaller build |
| `tests/test_perfect_size.py` | New tests (below) |
| `docs/perfect_size.md` | New reference doc (algorithm, wordlist, how to extend/debug) |
| `~/.claude/skills/perfect-size-text/SKILL.md` + memory | Capture the feature so it stays solid across sessions |

## Testing

- **segment():** a known Khmer sentence splits into expected words; a known
  multi-syllable word is never split; mixed Khmer + numerals tokenise correctly;
  out-of-dictionary run falls back to syllables without orphaning a coeng/vowel.
- **fit():** for several box sizes the returned lines all fit `avail_w` and total
  height ≤ `avail_h`; the size is **maximal** (size + 1pt would overflow); no line
  breaks inside a dictionary word; balanced (widest/narrowest line ratio bounded).
- **overflow fallback:** absurdly long text in a tiny box returns `size_min`
  without raising.
- **TextBoxItem integration** (offscreen Qt): committing Khmer into a box sets
  `fitted`, a sane size, and lines that fit; resizing re-fits.

## Documentation deliverable ("keep it solid")

- **Memory:** a `project`/`reference` entry — feature exists, what it does, module
  paths, the fit algorithm, wordlist location and how to grow it. Linked from
  [[project-manhwaprep]].
- **`docs/perfect_size.md`:** in-repo reference: the algorithm, the wordlist
  format/source, tuning knobs (margin, size bounds), and how to debug a bad fit.
- **`perfect-size-text` skill:** short reference skill so future sessions can
  extend the wordlist or adjust fitting without re-deriving the design.

## Manual Verification

1. Open a chapter, paste a Khmer translation into a bubble → it instantly sizes to
   fill the bubble with clean word-boundary line breaks.
2. Paste a long translation and a short one → both fill their bubbles, neither
   overflows; no mid-word breaks.
3. Resize a bubble box → text re-fits to the new size.
4. Export the canvas → text renders identically to the editor (outline/effects
   intact).
