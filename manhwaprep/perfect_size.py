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
