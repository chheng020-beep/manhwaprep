"""Studio: batch-prep + review-gated pipeline brain (no Qt in the core).

A ChapterJob is one manhwa chapter moving through:
    queued -> prepping -> typeset -> cut -> done   (+ error)
Its truth lives in <chapter_dir>/status.json so nothing is lost on restart.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime

QUEUED = "queued"
PREPPING = "prepping"
TYPESET = "typeset"
CUT = "cut"
DONE = "done"
ERROR = "error"

VALID_STATES = {QUEUED, PREPPING, TYPESET, CUT, DONE, ERROR}
NEXT_STATE = {PREPPING: TYPESET, TYPESET: CUT, CUT: DONE}

STATUS_FILE = "status.json"


def slugify(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s or "chapter"


@dataclass
class ChapterJob:
    title: str
    source: str
    slug: str
    state: str = QUEUED
    error: str | None = None
    updated_at: str = ""

    def to_status(self, dir_path: str) -> None:
        os.makedirs(dir_path, exist_ok=True)
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        with open(os.path.join(dir_path, STATUS_FILE), "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    @classmethod
    def from_status(cls, dir_path: str) -> "ChapterJob":
        with open(os.path.join(dir_path, STATUS_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return cls(**{k: data.get(k) for k in
                      ("title", "source", "slug", "state", "error", "updated_at")})
