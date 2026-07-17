"""Perfect Size Text: fit a translation to a speech-bubble box.

`segment()` splits text into legal line-break units (Khmer words via the bundled
dictionary, syllable fallback so a coeng/vowel is never orphaned); `fit()` picks
the largest font size whose word-wrapped, balanced text fills the box. All local.
"""

from __future__ import annotations

import os
import re

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
            # keep each "word + following spaces" as one token (lossless)
            toks.extend(re.findall(r"\S+\s*|\s+", run))
    return toks


# --- fit(): largest font size whose wrapped, balanced text fills a box --------
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


def fit(text, box_w, box_h, font_family, margin=0.06, size_min=6.0, size_max=200.0,
        line_spacing: float = 1.0, bold: bool = False, italic: bool = False):
    tokens = segment(text)
    if not "".join(tokens).strip():
        return size_min, []

    def feasible(size, m):
        f = QFont(font_family); f.setPointSizeF(size)
        f.setBold(bold); f.setItalic(italic)
        fm = QFontMetricsF(f)
        aw, ah = box_w * (1 - 2 * m), box_h * (1 - 2 * m)
        lines = _wrap(tokens, aw, fm)
        ok = len(lines) * fm.lineSpacing() * line_spacing <= ah and \
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


def fit_lines(lines, box_w, box_h, font_family, margin=0.06,
              size_min=6.0, size_max=200.0, line_spacing: float = 1.0,
              bold: bool = False, italic: bool = False):
    """Largest font size at which a FIXED set of lines (e.g. chosen by the LLM)
    fits box_w x box_h. Unlike fit(), the line breaks are not re-decided here —
    only the size is searched. Returns the size."""
    lines = [l for l in (lines or []) if l is not None]
    if not "".join(lines).strip():
        return size_min

    def feasible(size, m):
        f = QFont(font_family); f.setPointSizeF(size)
        f.setBold(bold); f.setItalic(italic)
        fm = QFontMetricsF(f)
        aw, ah = box_w * (1 - 2 * m), box_h * (1 - 2 * m)
        h_ok = len(lines) * fm.lineSpacing() * line_spacing <= ah
        w_ok = all(fm.horizontalAdvance(l) <= aw for l in lines)
        return h_ok and w_ok

    lo, hi, best = size_min, size_max, None
    while hi - lo > 0.5:
        mid = (lo + hi) / 2
        if feasible(mid, margin):
            best = mid
            lo = mid
        else:
            hi = mid
    return round(best if best is not None else size_min, 1)


# Preferred fixed font sizes, largest first. apply_perfect_size targets these:
# fix the size, wrap to the box WIDTH, and let the box grow in height — so the
# text comes out big and readable instead of shrinking to a short box.
# (cap, floor): perfect-size targets a single 40 pt (matches the default box
# size). Text that doesn't fit at 40 grows the box taller rather than shrinking.
PREFER_SIZES = (40.0, 40.0)


def wrap_at_size(text, box_w, size, font_family, margin=0.06,
                 bold: bool = False, italic: bool = False, prefer_lines=None):
    """Wrap `text` to fit box_w at a FIXED font size. If prefer_lines is given
    (e.g. the LLM's chosen breaks), use those as the primary line boundaries and
    only sub-wrap a preferred line that is still too wide at this size. Returns
    (lines, fits); fits is False only when a single un-splittable token exceeds
    the available width (meaning this size is too big for the box)."""
    f = QFont(font_family); f.setPointSizeF(size)
    f.setBold(bold); f.setItalic(italic)
    fm = QFontMetricsF(f)
    aw = box_w * (1 - 2 * margin)
    groups = [g for g in (prefer_lines or [text]) if str(g).strip()]
    out, fits = [], True
    for g in groups:
        toks = segment(str(g))
        lines = _wrap(toks, aw, fm)
        out.extend(lines)
        if any(fm.horizontalAdvance(l) > aw + 1 for l in lines):
            fits = False
    return (out or [text]), fits


def _balanced_lines(tokens, n, fm):
    """Partition `tokens` (whole words, in order) into at most n lines so the
    WIDEST line is as narrow as possible — the most balanced/symmetrical split.
    Binary-searches the minimal feasible max-width, then greedy-fills to it."""
    adv = [fm.horizontalAdvance(t) for t in tokens]
    if not adv:
        return [""]
    widest, total = max(adv), sum(adv)

    def lines_at(w):
        out, cur, cw = [], "", 0.0
        for t, a in zip(tokens, adv):
            if cur and cw + a > w:
                out.append(cur); cur, cw = t, a
            else:
                cur += t; cw += a
        if cur:
            out.append(cur)
        return out

    lo, hi = widest, max(widest, total)
    while lo < hi:
        mid = (lo + hi) / 2
        if len(lines_at(mid)) <= n:
            hi = mid
        else:
            lo = mid + 1
    return lines_at(lo)


def fit_balanced(text, box_w, box_h, font_family, margin=0.06, size_min=6.0,
                 size_max=None, line_spacing: float = 1.0,
                 bold: bool = False, italic: bool = False, max_lines: int = 14):
    """Fill box_w x box_h with the largest font whose lines are SYMMETRICAL
    (balanced widths) and never split a Khmer word. Tries each line count, picks
    the one that yields the biggest font. Returns (size, lines)."""
    if size_max is None:
        size_max = PREFER_SIZES[0]
    tokens = [t for t in segment(text) if t.strip()]
    if not tokens:
        return size_min, []
    REF = 100.0
    f = QFont(font_family); f.setPointSizeF(REF); f.setBold(bold); f.setItalic(italic)
    fm = QFontMetricsF(f)
    aw, ah = box_w * (1 - 2 * margin), box_h * (1 - 2 * margin)
    lh = fm.lineSpacing() * line_spacing
    best = None
    prev_lines = None
    for n in range(1, max_lines + 1):
        lines = _balanced_lines(tokens, n, fm)
        if lines == prev_lines:      # can't split further (fewer real lines than n)
            continue
        prev_lines = lines
        maxw = max(fm.horizontalAdvance(l) for l in lines) or 1.0
        s = min(size_max, REF * aw / maxw, REF * ah / (len(lines) * lh))
        if best is None or s > best[0]:
            best = (s, lines)
    size, lines = best
    return max(size_min, round(size, 1)), lines
