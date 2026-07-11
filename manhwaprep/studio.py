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
from . import typeset_prep

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
        final = os.path.join(dir_path, STATUS_FILE)
        tmp = final + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
        os.replace(tmp, final)

    @classmethod
    def from_status(cls, dir_path: str) -> "ChapterJob":
        with open(os.path.join(dir_path, STATUS_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return cls(**{k: data.get(k) for k in
                      ("title", "source", "slug", "state", "error", "updated_at")})


class Studio:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def chapter_dir(self, slug: str) -> str:
        return os.path.join(self.root, slug)

    def _unique_slug(self, base: str) -> str:
        slug, i = base, 2
        while os.path.exists(self.chapter_dir(slug)):
            slug = f"{base}-{i}"
            i += 1
        return slug

    def add(self, source: str, title: str) -> ChapterJob:
        slug = self._unique_slug(slugify(title))
        job = ChapterJob(title=title, source=source, slug=slug, state=QUEUED)
        job.to_status(self.chapter_dir(slug))
        return job

    def scan(self) -> list[ChapterJob]:
        jobs = []
        for name in os.listdir(self.root):
            d = self.chapter_dir(name)
            if not os.path.isfile(os.path.join(d, STATUS_FILE)):
                continue
            try:
                job = ChapterJob.from_status(d)
            except Exception:
                continue
            jobs.append(job)
        jobs.sort(key=lambda j: j.updated_at)
        return jobs

    def recover(self) -> None:
        """One-shot startup recovery: any job left 'prepping' (app died
        mid-prep) is re-queued. Call once on launch, never from refresh."""
        for name in os.listdir(self.root):
            d = self.chapter_dir(name)
            if not os.path.isfile(os.path.join(d, STATUS_FILE)):
                continue
            try:
                job = ChapterJob.from_status(d)
            except Exception:
                continue
            if job.state == PREPPING:
                job.state = QUEUED
                job.to_status(d)

    def set_state(self, slug: str, state: str) -> ChapterJob:
        assert state in VALID_STATES, state
        d = self.chapter_dir(slug)
        job = ChapterJob.from_status(d)
        job.state = state
        job.to_status(d)
        return job

    def advance(self, slug: str) -> ChapterJob:
        d = self.chapter_dir(slug)
        job = ChapterJob.from_status(d)
        if job.state not in NEXT_STATE:
            raise ValueError(f"cannot advance from state {job.state!r}")
        job.state = NEXT_STATE[job.state]
        job.error = None
        job.to_status(d)
        return job

    def set_error(self, slug: str, msg: str) -> ChapterJob:
        d = self.chapter_dir(slug)
        job = ChapterJob.from_status(d)
        job.state = ERROR
        job.error = msg
        job.to_status(d)
        return job

    def retry(self, slug: str) -> ChapterJob:
        d = self.chapter_dir(slug)
        job = ChapterJob.from_status(d)
        job.state = QUEUED
        job.error = None
        job.to_status(d)
        return job


def write_transcript_txt(layout_path: str) -> str:
    with open(layout_path, encoding="utf-8") as f:
        layout = json.load(f)
    lines = []
    for seg in layout.get("segments", []):
        for it in seg.get("items", []):
            lines.append(f"{it['n']}. [{it['kind']}] {it['src']}")
    out = os.path.join(os.path.dirname(layout_path), "transcript.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out


def prep_job(studio: "Studio", slug: str, prep_fn=typeset_prep.prep,
             control=None, on_status=None) -> None:
    d = studio.chapter_dir(slug)
    job = ChapterJob.from_status(d)
    studio.set_state(slug, PREPPING)
    try:
        layout_path = prep_fn(out_dir=d, source=job.source,
                              control=control, on_status=on_status)
        write_transcript_txt(layout_path)
        studio.advance(slug)  # prepping -> typeset
    except Exception as e:
        studio.set_error(slug, str(e))


def run_queue(studio: "Studio", prep_fn=typeset_prep.prep, control=None,
              on_status=None, on_job_change=None) -> int:
    processed = 0
    while True:
        if control is not None and control.is_stopped():
            break
        queued = [j for j in studio.scan() if j.state == QUEUED]
        if not queued:
            break
        slug = queued[0].slug
        prep_job(studio, slug, prep_fn=prep_fn, control=control, on_status=on_status)
        processed += 1
        if on_job_change:
            on_job_change(slug)
    return processed
