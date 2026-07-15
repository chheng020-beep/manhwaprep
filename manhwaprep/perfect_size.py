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
