"""Korean → Khmer SFX glossary.

Onomatopoeia is a localization task, not translation, so we never send SFX
through NLLB. Instead we look it up here. This is a STARTER set — extend it by
editing ~/ManhwaPrep/sfx_glossary.json (merged over these defaults). Keys are
the Korean SFX; values are the Khmer rendering. You (Khmer-native) will know the
right renderings better than these meaning-based defaults.
"""

from __future__ import annotations

import json
import os

USER_GLOSSARY = os.path.expanduser("~/ManhwaPrep/sfx_glossary.json")

# Korean SFX -> Khmer (meaning/sound based; refine freely). Romanization noted.
DEFAULT_GLOSSARY = {
    "두근": "ប៉ុក",        # dugeun — heartbeat
    "두근두근": "ប៉ុក​ៗ",   # dugeun-dugeun — heart pounding
    "쿵": "ភ្លុង",         # kung — thud
    "쿵쿵": "ភ្លុង​ៗ",      # kung-kung — heavy steps
    "쾅": "ផ្គាំង",        # kwang — bang/slam
    "펑": "ផេង",          # peong — pop/boom
    "탁": "តាក់",         # tak — tap/click
    "툭": " tuk ",        # tuk — light tap
    "톡": "តុក",          # tok — tap
    "짝": "ផ្លាក",         # jjak — clap/slap
    "철썩": "ផ្លាស់",       # cheolseok — splash/slap
    "슥": "ស៊ូ",          # seuk — swish
    "스윽": "ស៊ូ",         # seu-euk — slow swish
    "휙": "វ៉ូស",         # hwik — whoosh
    "헉": "ហឹក",          # heok — gasp
    "꿀꺽": " អឹក ",       # kkulkkeok — gulp
    "삐걱": "កៀក",        # ppigeok — creak
    "째깍": "ទិក​ tak",    # tick-tock
    "우르릉": "គ្រឹម",      # ureureung — rumble
    "부들부들": "ញ័រ​ៗ",    # trembling
}


def load_glossary() -> dict:
    g = dict(DEFAULT_GLOSSARY)
    if os.path.exists(USER_GLOSSARY):
        try:
            with open(USER_GLOSSARY, encoding="utf-8") as f:
                g.update(json.load(f))
        except Exception:
            pass
    return g


def lookup_sfx(text: str, glossary: dict) -> str | None:
    """Return Khmer for a Korean SFX, or None to leave it as-is."""
    t = (text or "").strip()
    if not t:
        return None
    if t in glossary:
        return glossary[t]
    # collapse simple repetition (두근두근 -> 두근) and retry
    half = len(t) // 2
    if len(t) % 2 == 0 and t[:half] == t[half:] and t[:half] in glossary:
        return glossary[t[:half]] + "​ៗ"
    # any glossary key contained in the SFX
    for k, v in glossary.items():
        if k and k in t:
            return v
    return None
