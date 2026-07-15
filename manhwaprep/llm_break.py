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
    "You are typesetting Khmer manhwa speech bubbles. You read Khmer fluently, so "
    "you know where each Khmer word ends. For each bubble, split its Khmer text "
    "into display lines with these rules, in priority order:\n"
    "1. NEVER split a Khmer word across two lines. A line break may only fall at a "
    "real word boundary (or an existing space / punctuation). Keeping whole words "
    "together matters more than any other rule.\n"
    "2. Keep each line at or under the bubble's max_chars budget so the text stays "
    "big; use as FEW lines as that allows.\n"
    "3. Make the lines SYMMETRICAL — as close to equal length as possible — for a "
    "balanced, tidy look (avoid a long line followed by one short orphan word).\n"
    "4. Prefer breaks that keep a phrase or grammatical unit intact.\n"
    "Keep EVERY character exactly — do not translate, add, remove, or reorder "
    "anything; only choose where the newlines go. Return ONLY a JSON object "
    "mapping each bubble number (as a string) to an array of line strings.\n"
    "Bubbles:\n"
)


def break_bubbles(items, key: str | None = None, mdl: str | None = None,
                  timeout: float = 60.0):
    """items: [{"n": int, "text": str, "w": float, "h": float, "max_chars": int?}].
    Returns {n: [line, ...]} for the bubbles the LLM broke, or None on any failure.
    A bubble is dropped from the result if the LLM changed its characters."""
    key = key or api_key()
    if not key:
        return None
    payload = []
    for it in items:
        if not (it.get("text") or "").strip():
            continue
        p = {"n": it["n"], "text": it["text"], "w": round(it["w"]),
             "h": round(it["h"])}
        if it.get("max_chars"):
            p["max_chars"] = int(it["max_chars"])
        payload.append(p)
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
