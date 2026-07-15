# Perfect Size Text Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-size and Khmer-word-break each speech-bubble's text to fill its box the instant a translation is pasted in, fully local (no API).

**Architecture:** A new pure module `manhwaprep/perfect_size.py` does the work: `segment()` splits text into legal line-break units (Khmer words via a bundled dictionary, syllable fallback), and `fit()` binary-searches the largest font size whose word-wrapped, line-balanced text fits the box. `TextBoxItem` gains a "fitted" mode that renders those pre-computed lines and holds its target bubble size instead of auto-growing. Triggers: text commit (paste/type) and resize.

**Tech Stack:** Python 3, PySide6 (QFontMetricsF for measuring), pytest.

## Global Constraints

- Repo is **local-only**: commit on this Mac, never `git push` / open PRs.
- **Function and result over optimization**: prefer simple, correct, obviously-working code. A large in-memory wordlist and straightforward binary search are fine. Do not micro-optimize.
- Fully local/offline; **no API or network at runtime**.
- The **syllable segmenter must work with no dictionary** — the dictionary only improves break quality. The feature must function even if `khmer_words.txt` is small or missing.
- Run tests with the EasyScanlate venv python: `~/EasyScanlate/.venv/bin/python -m pytest`.
- Qt tests set `os.environ["QT_QPA_PLATFORM"] = "offscreen"` and `sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")` before importing `manhwaprep`, matching existing tests. The pure segmenter tests (Tasks 1-2) need neither Qt nor a QApplication.
- Khmer Unicode block is U+1780–U+17FF. Consonants U+1780–U+17A2, independent vowels U+17A5–U+17B3, COENG U+17D2, dependent vowels/signs U+17B4–U+17D1 plus U+17DC/U+17DD, Khmer digits U+17E0–U+17E9.

---

### Task 1: Khmer syllable segmenter (`perfect_size.py`)

**Files:**
- Create: `manhwaprep/perfect_size.py`
- Test: `tests/test_perfect_size_segment.py`

**Interfaces:**
- Produces:
  - `syllable_split(khmer_run: str) -> list[str]` — split a run of Khmer into orthographic syllables (KCCs); a base consonant keeps its coeng-subscripts and vowels/signs together.
  - `segment(text: str) -> list[str]` — tile the WHOLE text into break-legal tokens whose concatenation equals `text` exactly (whitespace attaches to the preceding token). V1: Khmer runs → syllables, non-Khmer runs → whitespace-delimited words. Task 2 upgrades Khmer runs to dictionary words.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_perfect_size_segment.py
import sys
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
from manhwaprep import perfect_size as ps


def test_syllable_keeps_coeng_cluster_together():
    # ស + coeng + រ + vowel ុ  must stay in ONE syllable, never split the coeng off
    s = "ស្រុក"          # sruk
    parts = ps.syllable_split(s)
    assert "".join(parts) == s
    assert all("្" != p[0] for p in parts)     # no token starts with a bare coeng
    # the coeng+ro subscript must ride with its base, not be its own token
    assert any("្" in p for p in parts)
    assert all(len(p) >= 1 for p in parts)


def test_segment_tiles_text_exactly():
    text = "លោក ១០០ hello"
    toks = ps.segment(text)
    assert "".join(toks) == text                    # lossless tiling


def test_segment_splits_non_khmer_on_spaces():
    toks = ps.segment("hello world")
    assert [t.strip() for t in toks if t.strip()] == ["hello", "world"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_perfect_size_segment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'manhwaprep.perfect_size'`

- [ ] **Step 3: Write minimal implementation**

```python
# manhwaprep/perfect_size.py
"""Perfect Size Text: fit a translation to a speech-bubble box.

`segment()` splits text into legal line-break units (Khmer words via the bundled
dictionary, syllable fallback so a coeng/vowel is never orphaned); `fit()` picks
the largest font size whose word-wrapped, balanced text fills the box. All local.
"""

from __future__ import annotations

import re

# --- Khmer orthographic-syllable (KCC) splitting -------------------------------
_BASE = r"[ក-អឥ-ឳ]"          # consonant or independent vowel
_SUB = r"្[ក-ឳ]"                  # coeng + subscript base
_SIGN = r"[឴-៑ៜ៝]"           # dependent vowels + signs
_KCC = re.compile(_BASE + r"(?:" + _SUB + r")*" + _SIGN + r"*")
_KHMER_CHAR = re.compile(r"[ក-៿]")


def syllable_split(khmer_run: str) -> list[str]:
    """Split a Khmer run into orthographic syllables (KCCs). Any stray character
    the KCC regex doesn't match (e.g. a lone sign) is kept as its own piece so the
    output always reconstructs the input."""
    out, i = [], 0
    for m in _KCC.finditer(khmer_run):
        if m.start() > i:                 # unmatched gap -> keep verbatim
            out.append(khmer_run[i:m.start()])
        out.append(m.group(0))
        i = m.end()
    if i < len(khmer_run):
        out.append(khmer_run[i:])
    return [p for p in out if p]


def _khmer_runs(text: str):
    """Yield (is_khmer, run) chunks covering `text` in order."""
    if not text:
        return
    cur_khmer = bool(_KHMER_CHAR.match(text[0]))
    start = 0
    for i, ch in enumerate(text):
        k = bool(_KHMER_CHAR.match(ch))
        if k != cur_khmer:
            yield cur_khmer, text[start:i]
            start, cur_khmer = i, k
    yield cur_khmer, text[start:]


def _attach_trailing_space(tokens: list[str], run: str) -> list[str]:
    """Tokens tile `run`; nothing to do here (kept for symmetry)."""
    return tokens


def segment(text: str) -> list[str]:
    """Tile `text` into break-legal tokens; ''.join(segment(text)) == text.
    Whitespace stays attached to the token it follows so lines rebuild exactly."""
    toks: list[str] = []
    for is_khmer, run in _khmer_runs(text):
        if is_khmer:
            toks.extend(syllable_split(run))        # Task 2 upgrades to words
        else:
            # keep each "word + following spaces" as one token (lossless)
            toks.extend(re.findall(r"\S+\s*|\s+", run))
    return toks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_perfect_size_segment.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/perfect_size.py tests/test_perfect_size_segment.py
git commit -m "feat: perfect_size Khmer syllable segmenter + lossless tiling"
```

---

### Task 2: Dictionary word segmentation + bundled wordlist

**Files:**
- Modify: `manhwaprep/perfect_size.py`
- Create: `manhwaprep/assets/khmer_words.txt`
- Test: `tests/test_perfect_size_segment.py`

**Interfaces:**
- Consumes: `syllable_split`, `_khmer_runs`, `segment` from Task 1.
- Produces:
  - `load_words(path=None) -> set[str]` — memoised loader of the bundled wordlist.
  - `segment()` upgraded: Khmer runs are word-segmented by forward **longest-match** against the dictionary; any span with no dictionary match falls back to `syllable_split`.

**Wordlist sourcing:** obtain a large open Khmer wordlist and write it to `manhwaprep/assets/khmer_words.txt` (one word per line, UTF-8, no header). Try, in order: (a) an open Khmer wordlist fetched during implementation (e.g. WebSearch/WebFetch for a public-domain / permissive Khmer word list such as the SBBIC list); (b) if offline, seed the file with a few hundred common words plus every word used across the current `output/*/typeset` layouts, and note in `docs/perfect_size.md` that it should grow. The feature must work with whatever size the file is — the dictionary is an enhancement over syllables.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_perfect_size_segment.py
def test_longest_match_keeps_a_dictionary_word_whole(tmp_path, monkeypatch):
    wl = tmp_path / "words.txt"
    wl.write_text("ស្រុក\nខ្មែរ\n", encoding="utf-8")
    monkeypatch.setattr(ps, "_WORDS", None)          # reset memoised cache
    words = ps.load_words(str(wl))
    assert "ស្រុក" in words
    toks = ps.segment("ស្រុកខ្មែរ", words=words)      # "srok khmer" = two words
    assert toks == ["ស្រុក", "ខ្មែរ"]                 # not split mid-word


def test_segment_falls_back_to_syllables_off_dictionary(tmp_path, monkeypatch):
    wl = tmp_path / "words.txt"
    wl.write_text("ខ្មែរ\n", encoding="utf-8")
    monkeypatch.setattr(ps, "_WORDS", None)
    words = ps.load_words(str(wl))
    # unknown run still tiles losslessly via syllables
    text = "ស្រុក"
    toks = ps.segment(text, words=words)
    assert "".join(toks) == text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_perfect_size_segment.py -k dictionary_word -v`
Expected: FAIL — `segment()` has no `words` kwarg / `load_words` missing.

- [ ] **Step 3: Write minimal implementation**

```python
# add near the top of perfect_size.py
import os

_WORDS = None  # memoised dictionary


def _default_wordlist_path() -> str:
    return os.path.join(os.path.dirname(__file__), "assets", "khmer_words.txt")


def load_words(path: str | None = None) -> set[str]:
    global _WORDS
    if _WORDS is not None and path is None:
        return _WORDS
    p = path or _default_wordlist_path()
    words: set[str] = set()
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                w = line.strip()
                if w:
                    words.add(w)
    except OSError:
        words = set()               # missing dict is fine; syllables still work
    if path is None:
        _WORDS = words
    return words


def _match_words(run: str, words: set[str]) -> list[str]:
    """Forward longest-match segmentation of one Khmer run; spans with no match
    fall back to syllables so output always tiles the run."""
    if not words:
        return syllable_split(run)
    out, i, n = [], 0, len(run)
    # cap lookahead to the longest dictionary word to keep it simple/fast enough
    maxlen = max((len(w) for w in words), default=1)
    while i < n:
        hit = None
        for j in range(min(n, i + maxlen), i, -1):
            if run[i:j] in words:
                hit = run[i:j]
                break
        if hit:
            out.append(hit)
            i += len(hit)
        else:
            # no word starts here: emit one syllable and advance
            syl = syllable_split(run[i:])
            first = syl[0] if syl else run[i]
            out.append(first)
            i += len(first)
    return out
```

Then change `segment` to accept and use the dictionary:

```python
def segment(text: str, words: set[str] | None = None) -> list[str]:
    """Tile `text` into break-legal tokens; ''.join(segment(text)) == text.
    Khmer runs are word-segmented against the dictionary (longest-match), with
    syllable fallback; non-Khmer runs split on whitespace."""
    if words is None:
        words = load_words()
    toks: list[str] = []
    for is_khmer, run in _khmer_runs(text):
        if is_khmer:
            toks.extend(_match_words(run, words))
        else:
            toks.extend(re.findall(r"\S+\s*|\s+", run))
    return toks
```

- [ ] **Step 4: Create the wordlist asset**

Obtain the wordlist per "Wordlist sourcing" above and write it to `manhwaprep/assets/khmer_words.txt`. Verify it loads:

```bash
mkdir -p manhwaprep/assets
# (write khmer_words.txt here)
~/EasyScanlate/.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from manhwaprep import perfect_size as ps; print('words:', len(ps.load_words()))"
```
Expected: prints a word count > 0.

- [ ] **Step 5: Run tests to verify they pass**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_perfect_size_segment.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add manhwaprep/perfect_size.py manhwaprep/assets/khmer_words.txt tests/test_perfect_size_segment.py
git commit -m "feat: dictionary longest-match Khmer word segmentation + bundled wordlist"
```

---

### Task 3: `fit()` — size search, wrap, balance, overflow fallback

**Files:**
- Modify: `manhwaprep/perfect_size.py`
- Test: `tests/test_perfect_size_fit.py`

**Interfaces:**
- Consumes: `segment` (Task 2).
- Produces:
  - `fit(text, box_w, box_h, font_family, margin=0.06, size_min=6.0, size_max=200.0) -> (float, list[str])` — largest font size + chosen lines. Every returned line's advance width ≤ available width and total height ≤ available height, except the documented overflow fallback (returns `size_min`).
  - Helper `_wrap(tokens, avail_w, fm) -> list[str]` and `balance_lines(tokens, n_lines, avail_w, fm) -> list[str]` (kept module-private except `balance_lines`, used by the test).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_perfect_size_fit.py
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontMetricsF, QFont
from manhwaprep import perfect_size as ps
from manhwaprep.typeset_editor import khmer_font

_app = QApplication.instance() or QApplication([])
FAM = khmer_font()


def _lines_fit(lines, size, box_w, box_h, margin=0.06):
    f = QFont(FAM); f.setPointSizeF(size); fm = QFontMetricsF(f)
    aw = box_w * (1 - 2 * margin); ah = box_h * (1 - 2 * margin)
    if any(fm.horizontalAdvance(ln) > aw + 1.0 for ln in lines):
        return False
    return len(lines) * fm.lineSpacing() <= ah + 1.0


def test_fit_result_fits_the_box():
    text = "លោកអ្នកទាំងអស់គ្នាសូមអរគុណ"
    size, lines = ps.fit(text, 300, 200, FAM)
    assert lines and "".join("".join(lines).split()) != ""
    assert _lines_fit(lines, size, 300, 200)


def test_fit_is_maximal():
    text = "លោកអ្នកទាំងអស់គ្នា"
    size, lines = ps.fit(text, 300, 200, FAM)
    # one point larger should NOT fit (the search really maximised)
    bigger = ps._wrap(ps.segment(text), 300 * 0.88, _fm(size + 1))
    from PySide6.QtGui import QFont as _QF
    f = _QF(FAM); f.setPointSizeF(size + 1); fm = QFontMetricsF(f)
    assert len(bigger) * fm.lineSpacing() > 200 * 0.88 or \
        any(fm.horizontalAdvance(l) > 300 * 0.88 + 1 for l in bigger)


def _fm(size):
    f = QFont(FAM); f.setPointSizeF(size); return QFontMetricsF(f)


def test_overflow_fallback_returns_min_size_not_crash():
    text = "លោកអ្នកទាំងអស់គ្នា" * 40
    size, lines = ps.fit(text, 60, 60, FAM)
    assert size == 6.0 and lines           # min size, no exception
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_perfect_size_fit.py -v`
Expected: FAIL — `fit` / `_wrap` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# add to perfect_size.py
from PySide6.QtGui import QFont, QFontMetricsF


def _wrap(tokens, avail_w, fm):
    """Greedily pack tokens into lines no wider than avail_w. A single token wider
    than avail_w is split at syllables, then characters, so width never overflows."""
    lines, cur = [], ""
    for tok in tokens:
        trial = cur + tok
        if fm.horizontalAdvance(trial.rstrip()) <= avail_w or not cur:
            cur = trial
        else:
            lines.append(cur.rstrip())
            cur = tok
        # hard-split an over-wide standalone token
        while fm.horizontalAdvance(cur.rstrip()) > avail_w and len(cur.strip()) > 1:
            pieces = syllable_split(cur) if _KHMER_CHAR.search(cur) else list(cur)
            head = ""
            for p in pieces:
                if fm.horizontalAdvance((head + p).rstrip()) > avail_w and head:
                    break
                head += p
            if not head or head == cur:
                break
            lines.append(head.rstrip())
            cur = cur[len(head):]
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def balance_lines(tokens, n_lines, avail_w, fm):
    """Redistribute tokens across n_lines to even out line widths (reduce the
    widest line), keeping every line within avail_w. Simple greedy target-width
    pass — good enough; not optimised."""
    if n_lines <= 1:
        return [("".join(tokens)).rstrip()]
    total = fm.horizontalAdvance("".join(tokens).rstrip())
    target = total / n_lines
    lines, cur, cur_w = [], "", 0.0
    for tok in tokens:
        tw = fm.horizontalAdvance(tok)
        if cur and cur_w + tw > target * 1.15 and len(lines) < n_lines - 1 \
                and fm.horizontalAdvance((cur).rstrip()) <= avail_w:
            lines.append(cur.rstrip()); cur, cur_w = "", 0.0
        cur += tok; cur_w += tw
    if cur.strip():
        lines.append(cur.rstrip())
    # never exceed n_lines or avail_w -> fall back to plain wrap if we did
    if len(lines) > n_lines or any(fm.horizontalAdvance(l) > avail_w + 1 for l in lines):
        return _wrap(tokens, avail_w, fm)
    return lines


def fit(text, box_w, box_h, font_family, margin=0.06, size_min=6.0, size_max=200.0):
    tokens = segment(text)
    if not "".join(tokens).strip():
        return size_min, []

    def feasible(size, m):
        f = QFont(font_family); f.setPointSizeF(size); fm = QFontMetricsF(f)
        aw, ah = box_w * (1 - 2 * m), box_h * (1 - 2 * m)
        lines = _wrap(tokens, aw, fm)
        ok = len(lines) * fm.lineSpacing() <= ah and \
            all(fm.horizontalAdvance(l) <= aw for l in lines)
        return ok, lines, fm, aw

    # binary search the largest feasible size (0.5pt resolution is plenty)
    lo, hi, best = size_min, size_max, None
    while hi - lo > 0.5:
        mid = (lo + hi) / 2
        ok, lines, fm, aw = feasible(mid, margin)
        if ok:
            best = (mid, lines, fm, aw)
            lo = mid
        else:
            hi = mid
    if best is None:
        # overflow fallback: drop the margin, then accept size_min
        ok, lines, fm, aw = feasible(size_min, 0.0)
        return size_min, lines if lines else [text]
    size, lines, fm, aw = best
    lines = balance_lines(tokens, len(lines), aw, fm)
    return round(size, 1), lines
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_perfect_size_fit.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/perfect_size.py tests/test_perfect_size_fit.py
git commit -m "feat: perfect_size fit() — max font-size search + wrap + balance"
```

---

### Task 4: `TextBoxItem` fitted rendering (hold target size)

**Files:**
- Modify: `manhwaprep/typeset_editor.py` (`TextBoxItem.__init__` ~265-298; `_text_layout` ~334-353; `_refit` ~300-327; `to_dict`/`from` ~785-810 and load ~2014)
- Test: `tests/test_perfect_size_box.py`

**Interfaces:**
- Consumes: `perfect_size.fit` (Task 3).
- Produces on `TextBoxItem`:
  - fields `self.fitted: bool = False`, `self.raw_text: str = text`.
  - `apply_perfect_size(self)` — recompute `fit()` from `self.raw_text` against current `self.w, self.h`; set `self.max_size`, store lines into `self.text` joined by `"\n"`, set `self.fitted = True`; invalidate the pixmap cache and `update()`. No-op on empty text.
  - `_text_layout` uses `QTextOption.NoWrap` when `self.fitted` (our line breaks are authoritative), else the current wrap mode.
  - `_refit` returns early (does not change height) when `self.fitted` — a fitted box holds its target `w×h`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_perfect_size_box.py
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
from PySide6.QtWidgets import QApplication
from manhwaprep.typeset_editor import TextBoxItem
_app = QApplication.instance() or QApplication([])


def test_apply_perfect_size_fits_and_marks_fitted():
    it = TextBoxItem(1, "លោកអ្នកទាំងអស់គ្នាសូមអរគុណ", 0, 0, 300, 200)
    h0 = it.h
    it.raw_text = it.text
    it.apply_perfect_size()
    assert it.fitted is True
    assert it.max_size > 6.0
    assert "\n" in it.text or len(it.raw_text) < 12   # multi-line for long text
    assert abs(it.h - h0) < 1.0        # held its target height (did not auto-grow)


def test_fitted_box_refit_is_noop_on_height():
    it = TextBoxItem(1, "លោក", 0, 0, 200, 120)
    it.raw_text = it.text
    it.apply_perfect_size()
    h = it.h
    it._refit()
    assert abs(it.h - h) < 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_perfect_size_box.py -v`
Expected: FAIL — `apply_perfect_size` / `fitted` missing.

- [ ] **Step 3: Write minimal implementation**

In `TextBoxItem.__init__` (after `self.text = text`), add:

```python
        self.raw_text = text     # unbroken source; fit() re-wraps from this
        self.fitted = False      # True once perfect-sized: holds target w x h
```

Add the import near the other `from . import ...` lines:

```python
from . import perfect_size
```

Add the method to `TextBoxItem`:

```python
    def apply_perfect_size(self):
        """Fill this box: pick the largest font + Khmer word-break layout that fits
        self.w x self.h, from self.raw_text. Holds the box size (no auto-grow)."""
        src = (self.raw_text or self.text or "").strip()
        if not src:
            return
        size, lines = perfect_size.fit(src, self.w, self.h, self.font.family())
        self.max_size = size
        self.font.setPointSizeF(size)
        self.text = "\n".join(lines) if lines else src
        self.fitted = True
        self._px_key = None       # invalidate pixmap cache
        self.update()
```

In `_text_layout`, make wrapping conditional (find `opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)` and replace):

```python
        opt.setWrapMode(QTextOption.NoWrap if self.fitted
                        else QTextOption.WrapAtWordBoundaryOrAnywhere)
```

In `_refit`, add an early return at the very top of the body (after the docstring):

```python
        if self.fitted:
            return            # a perfect-sized box holds its target w x h
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_perfect_size_box.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Persist fitted state (save/load)**

In `to_dict` add `"fitted": self.fitted, "raw_text": self.raw_text,` to the saved dict. Where boxes are rebuilt from a dict (the `d["n"], d["text"], ...` load near line 2014), after constructing `it`, restore:

```python
            it.raw_text = d.get("raw_text", d["text"])
            it.fitted = bool(d.get("fitted", False))
```

Run the full suite to confirm nothing regressed:
Run: `~/EasyScanlate/.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add manhwaprep/typeset_editor.py tests/test_perfect_size_box.py
git commit -m "feat: TextBoxItem perfect-size mode (fitted rendering, holds target size)"
```

---

### Task 5: Triggers — auto-fit on paste/commit and on resize

**Files:**
- Modify: `manhwaprep/typeset_editor.py` (`_commit_inline` ~2319-2337; side-panel text commit ~2130-2135; `TextBoxItem.mouseReleaseEvent` ~770-776 and resize start `_start`/mouseMove)
- Test: `tests/test_perfect_size_box.py`

**Interfaces:**
- Consumes: `TextBoxItem.apply_perfect_size` (Task 4).
- Produces: text commits set `it.raw_text` from the committed text then call `it.apply_perfect_size()`; resizing a **fitted** box adjusts its target rectangle and re-fits on mouse release.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_perfect_size_box.py
def test_resize_refits_a_fitted_box():
    it = TextBoxItem(1, "លោកអ្នកទាំងអស់គ្នាសូមអរគុណ", 0, 0, 300, 200)
    it.raw_text = it.text
    it.apply_perfect_size()
    s1 = it.max_size
    # simulate a resize to a much bigger box, then the release-time re-fit hook
    it.w, it.h = 600, 400
    it.apply_perfect_size()
    assert it.max_size > s1        # bigger box -> bigger font
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_perfect_size_box.py -k resize_refits -v`
Expected: FAIL only if the method regressed; if it passes trivially, still wire the real triggers below (the integration is what Step 3 delivers). Proceed to Step 3.

- [ ] **Step 3: Wire the triggers**

In `_commit_inline`, where it currently does `it.text = proxy.widget().toPlainText()` then `it._refit()`, change to fit from the new text:

```python
                new_text = proxy.widget().toPlainText()
                it.text = new_text
                it.raw_text = new_text
                it.apply_perfect_size()
            it._editing = False
            if not it.fitted:
                it._refit()
            it.update()
```

(Keep the surrounding `shiboken6.isValid` guards intact; only the inner assignment + fit call changes. A box with empty text stays unfitted and still `_refit`s.)

In the side-panel text commit (the `it.text = self.text_edit.toPlainText()` around line 2134), mirror it:

```python
            it.text = self.text_edit.toPlainText()
            it.raw_text = it.text
            it.apply_perfect_size()
```

In `TextBoxItem.mouseReleaseEvent`, re-fit a fitted box after a resize drag ends:

```python
    def mouseReleaseEvent(self, e):
        if self._resize:
            was_resize = self._resize
            self._resize = None
            self._rot_start = None
            if self.fitted and was_resize != "rot":
                self.apply_perfect_size()   # re-fill the new bubble size
            e.accept()
        else:
            super().mouseReleaseEvent(e)
```

Note: during the drag itself, `mouseMoveEvent` for a fitted box may call `_refit`, which now early-returns (Task 4) — so the frame resizes and the text re-fills only on release. That's the intended, simple behavior.

- [ ] **Step 4: Run tests + full suite**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_perfect_size_box.py -v && ~/EasyScanlate/.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/typeset_editor.py tests/test_perfect_size_box.py
git commit -m "feat: auto perfect-size on text commit and on box resize"
```

---

### Task 6: Packaging, docs, memory, skill

**Files:**
- Modify: `manhwaprep.spec` (bundle the wordlist)
- Create: `docs/perfect_size.md`
- Create: `~/.claude/skills/perfect-size-text/SKILL.md`
- Memory: a project/reference entry + MEMORY.md index line

**Interfaces:** none (documentation + build config).

- [ ] **Step 1: Bundle the wordlist in PyInstaller**

In `manhwaprep.spec`, ensure `manhwaprep/assets/khmer_words.txt` ships. Add to the `datas` list (match the existing assets entry pattern):

```python
    ('manhwaprep/assets/khmer_words.txt', 'manhwaprep/assets'),
```

Verify the spec still parses:
Run: `~/EasyScanlate/.venv/bin/python -c "compile(open('manhwaprep.spec').read(), 'manhwaprep.spec', 'exec')"`
Expected: no error.

- [ ] **Step 2: Write the reference doc**

Create `docs/perfect_size.md` covering: what the feature does; the module API (`segment`, `fit`, `apply_perfect_size`); the fit algorithm; the wordlist location + format + how to grow it; tuning knobs (`margin`, `size_min/max`); how to debug a bad fit (render the box, check `it.fitted`, `it.max_size`, `it.text`).

- [ ] **Step 3: Manual verification in the app**

```bash
./run.sh
```
Open a chapter, paste a Khmer translation into a bubble → it fills the bubble with word-boundary line breaks; paste a long and a short one → both fit, no mid-word breaks, no overflow; resize a bubble → text re-fits. Confirm export renders identically.

- [ ] **Step 4: Write memory + skill**

Add a memory entry (type project/reference) recording: feature exists, `manhwaprep/perfect_size.py` + `assets/khmer_words.txt`, the trigger points, the algorithm, and how to extend the wordlist; add its one-line index to `MEMORY.md`. Create `~/.claude/skills/perfect-size-text/SKILL.md` — a short reference skill so future sessions can extend the wordlist or tune fitting without re-deriving the design.

- [ ] **Step 5: Commit**

```bash
git add manhwaprep.spec docs/perfect_size.md
git commit -m "build+docs: bundle Khmer wordlist; perfect-size reference doc"
```

---

## Manual Verification (whole feature)

1. `./run.sh`; paste a Khmer translation into a bubble → instant fill with clean word-boundary breaks.
2. Long translation and short one → both fill their bubbles; nothing overflows; no mid-word breaks.
3. Resize a bubble box → text re-fits to the new size.
4. Save, reopen the chapter → fitted boxes reload at their sizes (fitted state persisted).
5. Export the canvas → text renders identically to the editor (outline/effects intact).
