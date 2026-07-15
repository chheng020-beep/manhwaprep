"""Ask an LLM (via OpenRouter) where Khmer speech-bubble lines should break.

Font SIZE is decided locally by pixel math (perfect_size.fit_lines); the LLM only
decides WHERE the Khmer breaks — the linguistic call a dictionary does badly. One
request handles a whole canvas of bubbles. Degrades to None (caller falls back to
the local segmenter) whenever there's no key, no network, or a bad reply, so the
app stays usable offline.

Setup: put your OpenRouter key in ~/ManhwaPrep/openrouter_key.txt (or set
OPENROUTER_API_KEY). Optionally override the model in
~/ManhwaPrep/openrouter_model.txt (default: google/gemini-2.5-flash).
"""

from __future__ import annotations

import json
import os
import urllib.request

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.5-flash"


def _cfg_path(name: str) -> str:
    return os.path.expanduser(f"~/ManhwaPrep/{name}")


def api_key() -> str:
    k = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if k:
        return k
    try:
        return open(_cfg_path("openrouter_key.txt"), encoding="utf-8").read().strip()
    except OSError:
        return ""


def model() -> str:
    try:
        m = open(_cfg_path("openrouter_model.txt"), encoding="utf-8").read().strip()
        if m:
            return m
    except OSError:
        pass
    return DEFAULT_MODEL


_PROMPT = (
    "You are typesetting Khmer manhwa speech bubbles. For each bubble, split its "
    "Khmer text into display lines. Break ONLY at natural Khmer word / phrase "
    "boundaries — never inside a word or a coeng (subscript) cluster. Balance the "
    "lines so the block roughly matches the given pixel box: a wider, shorter box "
    "wants fewer and longer lines; a narrow, tall box wants more lines. Keep EVERY "
    "character exactly — do not translate, add, remove, or reorder anything; only "
    "choose where the newlines go. Return ONLY a JSON object mapping each bubble "
    'number (as a string) to an array of line strings. Bubbles:\n'
)


def break_bubbles(items, key: str | None = None, mdl: str | None = None,
                  timeout: float = 60.0):
    """items: [{"n": int, "text": str, "w": float, "h": float}].
    Returns {n: [line, ...]} for the bubbles the LLM broke, or None on any failure.
    A bubble is dropped from the result if the LLM changed its characters."""
    key = key or api_key()
    if not key:
        return None
    payload = [
        {"n": it["n"], "text": it["text"], "w": round(it["w"]), "h": round(it["h"])}
        for it in items if (it.get("text") or "").strip()
    ]
    if not payload:
        return None
    body = json.dumps({
        "model": mdl or model(),
        "messages": [{"role": "user",
                      "content": _PROMPT + json.dumps(payload, ensure_ascii=False)}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(OPENROUTER_URL, data=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://manhwaprep.local",
        "X-Title": "ManhwaPrep",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        parsed = json.loads(data["choices"][0]["message"]["content"])
    except Exception:
        return None

    out = {}
    for it in payload:
        lines = parsed.get(str(it["n"]))
        if not isinstance(lines, list) or not lines:
            continue
        lines = [str(x) for x in lines]
        # sanity: the LLM must preserve every non-space character
        if "".join("".join(l.split()) for l in lines) == "".join(it["text"].split()):
            out[it["n"]] = lines
    return out or None
