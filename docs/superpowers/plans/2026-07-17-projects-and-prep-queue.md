# Projects Library + Background Prep Queue — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group every prepped chapter under its series ("Project"), and add a background queue that cleans chapters sequentially while the user typesets, each chapter showing status + live progress with Open-editor / Export-mark-done / Re-prep / Remove actions.

**Architecture:** Four new isolated modules — `jsonstore.py` (locked/atomic JSON I/O), `series.py` (pure source→series detection), `projects.py` (`ProjectStore` registry over `projects.json`), `prepqueue.py` (`prep_chapter` core + `PrepQueue` thread) — plus a `projects_view.py` Qt panel and wiring in `ui.py`. Reuses the existing `pipeline.run()`, `Control`, and separate-window `TypesetEditor`.

**Tech Stack:** Python 3.12, PySide6 (Qt), pytest. Runs under `~/EasyScanlate/.venv`.

## Global Constraints

- Python interpreter for all commands: `~/EasyScanlate/.venv/bin/python`.
- App data dir (where registries live): `os.path.dirname(config.default_output_dir())` — i.e. `~/Desktop/ManhwaPrep`. Base output dir: `config.default_output_dir()` = `~/Desktop/ManhwaPrep/output`.
- `pipeline.run(source, out_root=…, clean=True, inpaint="migan", typeset=<lang>, control=…, on_status=…, on_progress=…)` returns `(out_dir, outputs)`; with `typeset` set, `outputs[0]` is the `layout.json` path and `<out_dir>/typeset/canvas_001.png` is the thumb. `typeset` lang values: `"ko"` or `"en"` (default `"ko"`).
- One prep at a time (OCR/inpaint are heavy). Never run two `pipeline.run` concurrently.
- All registry writes go through `jsonstore` (atomic + cross-process locked). Never write `projects.json` directly.
- Removing a chapter keeps files unless `delete_files=True`.
- Follow existing style: no type-checker required, plain functions/classes, `from __future__ import annotations` at top of new modules.

---

### Task 1: `jsonstore.py` — locked, atomic JSON I/O

**Files:**
- Create: `manhwaprep/jsonstore.py`
- Test: `tests/test_jsonstore.py`

**Interfaces:**
- Produces:
  - `locked(path: str)` — context manager taking an advisory cross-process lock on `path + ".lock"`.
  - `read_json(path: str, default)` — return parsed JSON or `default` if missing/corrupt.
  - `atomic_write(path: str, data)` — write JSON to `path` atomically (temp file + `os.replace`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jsonstore.py
import os
from manhwaprep import jsonstore


def test_roundtrip_and_default(tmp_path):
    p = os.path.join(tmp_path, "reg.json")
    assert jsonstore.read_json(p, {"x": 1}) == {"x": 1}      # missing -> default
    jsonstore.atomic_write(p, {"a": [1, 2], "b": "café"})
    assert jsonstore.read_json(p, None) == {"a": [1, 2], "b": "café"}


def test_corrupt_returns_default(tmp_path):
    p = os.path.join(tmp_path, "bad.json")
    with open(p, "w") as f:
        f.write("{not json")
    assert jsonstore.read_json(p, []) == []


def test_locked_is_reentrant_across_calls(tmp_path):
    p = os.path.join(tmp_path, "reg.json")
    with jsonstore.locked(p):
        jsonstore.atomic_write(p, {"n": 1})
    with jsonstore.locked(p):
        assert jsonstore.read_json(p, None) == {"n": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_jsonstore.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'manhwaprep.jsonstore'`

- [ ] **Step 3: Write minimal implementation**

```python
# manhwaprep/jsonstore.py
"""Locked, atomic JSON storage shared by the app's on-disk registries.

Advisory cross-process lock (fcntl/msvcrt) + temp-file-then-os.replace writes,
degrading gracefully so the app never crashes over registry bookkeeping.
"""

from __future__ import annotations

import contextlib
import json
import os


@contextlib.contextmanager
def locked(path: str):
    lock_path = path + ".lock"
    f = None
    try:
        try:
            f = open(lock_path, "a+")
        except Exception:
            pass
        if f is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass
        yield
    finally:
        if f is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            f.close()


def read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def atomic_write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_jsonstore.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/jsonstore.py tests/test_jsonstore.py
git commit -m "feat: add jsonstore (locked, atomic JSON registry I/O)"
```

---

### Task 2: `series.py` — detect series/chapter from a source

**Files:**
- Create: `manhwaprep/series.py`
- Test: `tests/test_series.py`

**Interfaces:**
- Produces:
  - `SeriesInfo` dataclass with fields: `series_id: str`, `series_name: str`, `chapter_id: str`, `chapter_name: str`, `chapter_number: float | None`, `series_url: str | None`.
  - `detect(source: str) -> SeriesInfo`.
  - `slugify(name: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_series.py
from manhwaprep import series


def test_comix_url():
    s = series.detect(
        "https://comix.to/title/55kym-why-the-villainess-wields-the-sword/9356816-chapter-1")
    assert s.series_id == "comix:55kym-why-the-villainess-wields-the-sword"
    assert s.series_name == "Why The Villainess Wields The Sword"
    assert s.chapter_number == 1.0
    assert s.chapter_id == "9356816-chapter-1"
    assert s.series_url == "https://comix.to/title/55kym-why-the-villainess-wields-the-sword"


def test_folder_source():
    s = series.detect("/Users/me/Desktop/ManhwaPrep/output/White Demon/chapter-5")
    assert s.series_id == "folder:White Demon"
    assert s.series_name == "White Demon"
    assert s.chapter_id == "chapter-5"
    assert s.chapter_number == 5.0
    assert s.series_url is None


def test_unknown_is_ungrouped():
    s = series.detect("")
    assert s.series_id == "ungrouped"
    assert s.series_name == "Ungrouped"


def test_slugify():
    assert series.slugify("Why the Villainess Wields the Sword!") == \
        "why-the-villainess-wields-the-sword"
    assert series.slugify("") == "untitled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_series.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'manhwaprep.series'`

- [ ] **Step 3: Write minimal implementation**

```python
# manhwaprep/series.py
"""Detect which series (Project) and chapter a download source belongs to.

Pure functions, no I/O. A source is a chapter URL or a local folder path."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class SeriesInfo:
    series_id: str
    series_name: str
    chapter_id: str
    chapter_name: str
    chapter_number: float | None
    series_url: str | None


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "untitled"


def _number_in(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", text or "")
    return float(m.group(1)) if m else None


def _humanize_slug(slug: str) -> str:
    parts = [p for p in slug.split("-") if p]
    # drop a leading short id-code like "55kym" (contains a digit, <= 6 chars)
    if parts and len(parts[0]) <= 6 and any(c.isdigit() for c in parts[0]):
        parts = parts[1:]
    return " ".join(parts).title() if parts else slug


def detect(source: str) -> SeriesInfo:
    src = (source or "").strip()
    if not src:
        return SeriesInfo("ungrouped", "Ungrouped", "", "", None, None)

    if src.startswith("http://") or src.startswith("https://"):
        u = urlparse(src)
        host = u.netloc.lower()
        # comix.to / comick: /title/<slug>/<chapter-seg>
        m = re.search(r"/title/([^/]+)/([^/?#]+)", u.path)
        if "comix.to" in host and m:
            slug, last = m.group(1), m.group(2)
            num = None
            cm = re.search(r"chapter-(\d+(?:\.\d+)?)", last)
            if cm:
                num = float(cm.group(1))
            return SeriesInfo(
                series_id=f"comix:{slug}",
                series_name=_humanize_slug(slug),
                chapter_id=last,
                chapter_name=f"Chapter {num:g}" if num is not None else last,
                chapter_number=num,
                series_url=f"{u.scheme}://{host}/title/{slug}",
            )
        # generic URL: first meaningful path segment = series, last = chapter
        segs = [s for s in u.path.split("/") if s]
        series_seg = segs[0] if segs else host
        last = segs[-1] if segs else "chapter"
        return SeriesInfo(
            series_id=f"{host}:{series_seg}",
            series_name=_humanize_slug(series_seg),
            chapter_id=last,
            chapter_name=last,
            chapter_number=_number_in(last),
            series_url=None,
        )

    # local folder: parent = series, basename = chapter
    path = src.rstrip("/")
    parent = os.path.basename(os.path.dirname(path)) or "Ungrouped"
    base = os.path.basename(path) or "chapter"
    return SeriesInfo(
        series_id=f"folder:{parent}",
        series_name=parent,
        chapter_id=base,
        chapter_name=base,
        chapter_number=_number_in(base),
        series_url=None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_series.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/series.py tests/test_series.py
git commit -m "feat: add series detection (source -> series/chapter)"
```

---

### Task 3: `projects.py` — `ProjectStore` registry

**Files:**
- Create: `manhwaprep/projects.py`
- Test: `tests/test_projects.py`

**Interfaces:**
- Consumes: `manhwaprep.jsonstore` (`locked`, `read_json`, `atomic_write`); `manhwaprep.series` (`detect`, `slugify`); `manhwaprep.config` (`default_output_dir`).
- Produces:
  - `registry_path() -> str`
  - `class ProjectStore:`
    - `__init__(self, path: str)`
    - `add_chapter(self, source: str, lang: str = "ko") -> tuple[str, str]` → `(proj_id, chap_id)`; creates the project if new, dedupes by chapter id (existing chapter is left untouched), new chapters get `status="queued"`.
    - `get_project(self, proj_id: str) -> dict | None`
    - `get_chapter(self, proj_id: str, chap_id: str) -> dict | None`
    - `list_projects(self) -> list[dict]`
    - `set_chapter(self, proj_id: str, chap_id: str, **fields) -> None`
    - `enqueue(self, proj_id: str, chap_id: str) -> None`
    - `pop_next(self) -> tuple[str, str] | None`
    - `remove_chapter(self, proj_id: str, chap_id: str, delete_files: bool = False) -> None`
    - `reset_prepping(self) -> None` — set every `prepping` chapter back to `queued` and ensure it's queued.
    - `series_dir(self, proj_id: str) -> str` — `<default_output_dir>/<slugified series name>`.
    - `import_recents(self, entries: list[dict]) -> None` — add each recents entry as a `ready` chapter (idempotent).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_projects.py
import os
from manhwaprep.projects import ProjectStore

COMIX = "https://comix.to/title/55kym-why-the-villainess-wields-the-sword/9356816-chapter-1"
COMIX2 = "https://comix.to/title/55kym-why-the-villainess-wields-the-sword/9400000-chapter-2"


def _store(tmp_path):
    return ProjectStore(os.path.join(tmp_path, "projects.json"))


def test_add_chapter_creates_project_and_dedupes(tmp_path):
    s = _store(tmp_path)
    pid, cid = s.add_chapter(COMIX)
    assert pid == "comix:55kym-why-the-villainess-wields-the-sword"
    assert cid == "9356816-chapter-1"
    assert len(s.list_projects()) == 1
    assert s.get_chapter(pid, cid)["status"] == "queued"
    pid2, cid2 = s.add_chapter(COMIX)              # same chapter again
    assert (pid2, cid2) == (pid, cid)
    assert len(s.get_project(pid)["chapters"]) == 1  # not duplicated


def test_set_chapter_and_queue_order(tmp_path):
    s = _store(tmp_path)
    p1, c1 = s.add_chapter(COMIX)
    p2, c2 = s.add_chapter(COMIX2)
    s.enqueue(p1, c1)
    s.enqueue(p2, c2)
    s.enqueue(p1, c1)                              # duplicate enqueue ignored
    assert s.pop_next() == (p1, c1)
    assert s.pop_next() == (p2, c2)
    assert s.pop_next() is None
    s.set_chapter(p1, c1, status="ready", layout="/x/layout.json")
    assert s.get_chapter(p1, c1)["status"] == "ready"
    assert s.get_chapter(p1, c1)["layout"] == "/x/layout.json"


def test_reset_prepping(tmp_path):
    s = _store(tmp_path)
    p1, c1 = s.add_chapter(COMIX)
    s.set_chapter(p1, c1, status="prepping")
    s.reset_prepping()
    assert s.get_chapter(p1, c1)["status"] == "queued"
    assert s.pop_next() == (p1, c1)               # re-queued


def test_remove_chapter_keeps_files_by_default(tmp_path):
    s = _store(tmp_path)
    p1, c1 = s.add_chapter(COMIX)
    outdir = os.path.join(tmp_path, "out"); os.makedirs(outdir)
    marker = os.path.join(outdir, "keep.txt"); open(marker, "w").close()
    s.set_chapter(p1, c1, output_dir=outdir)
    s.remove_chapter(p1, c1)
    assert s.get_chapter(p1, c1) is None
    assert os.path.exists(marker)                 # files kept


def test_persistence_roundtrip(tmp_path):
    path = os.path.join(tmp_path, "projects.json")
    a = ProjectStore(path); a.add_chapter(COMIX)
    b = ProjectStore(path)                         # fresh instance, same file
    assert len(b.list_projects()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_projects.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'manhwaprep.projects'`

- [ ] **Step 3: Write minimal implementation**

```python
# manhwaprep/projects.py
"""On-disk registry of Projects (series) and their chapters, plus a prep queue.

A Project groups chapters detected from the same series. Each chapter carries a
status (queued|prepping|ready|done|error), live progress, and the paths the
typeset editor needs. Backed by projects.json via jsonstore (atomic + locked)."""

from __future__ import annotations

import os
import shutil
import time

from . import config, jsonstore, series


def registry_path() -> str:
    base = os.path.dirname(config.default_output_dir())  # ~/Desktop/ManhwaPrep
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "projects.json")


_EMPTY = {"projects": [], "queue": []}


class ProjectStore:
    def __init__(self, path: str):
        self.path = path

    # -- low level ----------------------------------------------------
    def _load(self) -> dict:
        data = jsonstore.read_json(self.path, None)
        if not isinstance(data, dict):
            return {"projects": [], "queue": []}
        data.setdefault("projects", [])
        data.setdefault("queue", [])
        return data

    def _save(self, data: dict) -> None:
        try:
            jsonstore.atomic_write(self.path, data)
        except Exception:
            pass

    def _find_project(self, data: dict, proj_id: str) -> dict | None:
        for p in data["projects"]:
            if p["id"] == proj_id:
                return p
        return None

    @staticmethod
    def _find_chapter(proj: dict, chap_id: str) -> dict | None:
        for c in proj.get("chapters", []):
            if c["id"] == chap_id:
                return c
        return None

    # -- queries (fresh read each call) -------------------------------
    def list_projects(self) -> list[dict]:
        return self._load()["projects"]

    def get_project(self, proj_id: str) -> dict | None:
        return self._find_project(self._load(), proj_id)

    def get_chapter(self, proj_id: str, chap_id: str) -> dict | None:
        proj = self.get_project(proj_id)
        return self._find_chapter(proj, chap_id) if proj else None

    def series_dir(self, proj_id: str) -> str:
        proj = self.get_project(proj_id)
        name = proj["name"] if proj else proj_id
        return os.path.join(config.default_output_dir(), series.slugify(name))

    # -- mutations (read-modify-write under lock) ---------------------
    def add_chapter(self, source: str, lang: str = "ko") -> tuple[str, str]:
        info = series.detect(source)
        with jsonstore.locked(self.path):
            data = self._load()
            proj = self._find_project(data, info.series_id)
            if proj is None:
                proj = {
                    "id": info.series_id, "name": info.series_name,
                    "series_url": info.series_url, "lang": lang,
                    "created_at": time.time(), "updated_at": time.time(),
                    "chapters": [],
                }
                data["projects"].append(proj)
            if self._find_chapter(proj, info.chapter_id) is None:
                proj["chapters"].append({
                    "id": info.chapter_id, "name": info.chapter_name,
                    "number": info.chapter_number, "source": source,
                    "status": "queued", "progress": None,
                    "output_dir": None, "layout": None, "thumb": None,
                    "error": None, "queued_at": time.time(),
                    "prepped_at": None, "done_at": None,
                })
                proj["chapters"].sort(key=lambda c: (c["number"] is None, c["number"] or 0))
                proj["updated_at"] = time.time()
                self._save(data)
            return info.series_id, info.chapter_id

    def set_chapter(self, proj_id: str, chap_id: str, **fields) -> None:
        with jsonstore.locked(self.path):
            data = self._load()
            proj = self._find_project(data, proj_id)
            if proj is None:
                return
            ch = self._find_chapter(proj, chap_id)
            if ch is None:
                return
            ch.update(fields)
            proj["updated_at"] = time.time()
            self._save(data)

    def enqueue(self, proj_id: str, chap_id: str) -> None:
        with jsonstore.locked(self.path):
            data = self._load()
            entry = [proj_id, chap_id]
            if entry not in data["queue"]:
                data["queue"].append(entry)
                self._save(data)

    def pop_next(self) -> tuple[str, str] | None:
        with jsonstore.locked(self.path):
            data = self._load()
            if not data["queue"]:
                return None
            proj_id, chap_id = data["queue"].pop(0)
            self._save(data)
            return proj_id, chap_id

    def remove_chapter(self, proj_id: str, chap_id: str,
                       delete_files: bool = False) -> None:
        with jsonstore.locked(self.path):
            data = self._load()
            proj = self._find_project(data, proj_id)
            if proj is None:
                return
            ch = self._find_chapter(proj, chap_id)
            if ch is not None:
                if delete_files and ch.get("output_dir") and os.path.isdir(ch["output_dir"]):
                    shutil.rmtree(ch["output_dir"], ignore_errors=True)
                proj["chapters"] = [c for c in proj["chapters"] if c["id"] != chap_id]
            data["queue"] = [e for e in data["queue"] if e != [proj_id, chap_id]]
            self._save(data)

    def reset_prepping(self) -> None:
        with jsonstore.locked(self.path):
            data = self._load()
            for proj in data["projects"]:
                for ch in proj.get("chapters", []):
                    if ch.get("status") == "prepping":
                        ch["status"] = "queued"
                        entry = [proj["id"], ch["id"]]
                        if entry not in data["queue"]:
                            data["queue"].insert(0, entry)
            self._save(data)

    def import_recents(self, entries: list[dict]) -> None:
        for e in entries:
            layout = e.get("layout", "")
            if not layout:
                continue
            # source = the chapter's output folder (parent of typeset/)
            out_dir = os.path.dirname(os.path.dirname(layout))
            pid, cid = self.add_chapter(out_dir)
            self.set_chapter(pid, cid, status="ready", layout=layout,
                             thumb=e.get("thumb", ""), output_dir=out_dir,
                             name=e.get("chapter") or None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_projects.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/projects.py tests/test_projects.py
git commit -m "feat: add ProjectStore registry (projects.json + queue)"
```

---

### Task 4: `prepqueue.py` — `prep_chapter` core + `PrepQueue` thread

**Files:**
- Create: `manhwaprep/prepqueue.py`
- Test: `tests/test_prepqueue.py`

**Interfaces:**
- Consumes: `manhwaprep.projects.ProjectStore`; `manhwaprep.pipeline.run`; `manhwaprep.control.Control`, `manhwaprep.control.PipelineStopped`.
- Produces:
  - `prep_chapter(store, proj_id, chap_id, control=None, on_status=None, on_progress=None) -> str` — runs the pipeline for one chapter and drives its status; returns the final status (`"ready"|"error"|"queued"|"missing"`). `on_status(proj_id, chap_id, status)`, `on_progress(proj_id, chap_id, stage, done, total)`.
  - `class PrepQueue(QObject)` with signals `status_changed(str, str, str)` and `progress(str, str, str, int, int)`; methods `start()`, `enqueue(proj_id, chap_id)`, `skip_current()`, `stop()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prepqueue.py
import os
import pytest
from manhwaprep import prepqueue
from manhwaprep.control import PipelineStopped
from manhwaprep.projects import ProjectStore

COMIX = "https://comix.to/title/55kym-why-the-villainess-wields-the-sword/9356816-chapter-1"


def _store(tmp_path):
    return ProjectStore(os.path.join(tmp_path, "projects.json"))


def test_prep_chapter_success(tmp_path, monkeypatch):
    s = _store(tmp_path)
    pid, cid = s.add_chapter(COMIX)
    seen = []
    monkeypatch.setattr(prepqueue.pipeline, "run",
                        lambda *a, **k: ("/out/dir", ["/out/dir/typeset/layout.json"]))
    status = prepqueue.prep_chapter(
        s, pid, cid, on_status=lambda p, c, st: seen.append(st))
    assert status == "ready"
    ch = s.get_chapter(pid, cid)
    assert ch["status"] == "ready"
    assert ch["layout"] == "/out/dir/typeset/layout.json"
    assert ch["thumb"] == os.path.join("/out/dir", "typeset", "canvas_001.png")
    assert seen == ["prepping", "ready"]


def test_prep_chapter_error_is_caught(tmp_path, monkeypatch):
    s = _store(tmp_path)
    pid, cid = s.add_chapter(COMIX)

    def boom(*a, **k):
        raise RuntimeError("download failed")
    monkeypatch.setattr(prepqueue.pipeline, "run", boom)
    status = prepqueue.prep_chapter(s, pid, cid)
    assert status == "error"
    ch = s.get_chapter(pid, cid)
    assert ch["status"] == "error"
    assert "download failed" in ch["error"]


def test_prep_chapter_stop_requeues(tmp_path, monkeypatch):
    s = _store(tmp_path)
    pid, cid = s.add_chapter(COMIX)

    def stopped(*a, **k):
        raise PipelineStopped()
    monkeypatch.setattr(prepqueue.pipeline, "run", stopped)
    status = prepqueue.prep_chapter(s, pid, cid)
    assert status == "queued"
    assert s.get_chapter(pid, cid)["status"] == "queued"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_prepqueue.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'manhwaprep.prepqueue'`

- [ ] **Step 3: Write minimal implementation**

```python
# manhwaprep/prepqueue.py
"""Background prep queue: clean chapters one at a time while the user typesets.

`prep_chapter` is the testable core (drives one chapter's status through a real
pipeline.run). `PrepQueue` wraps it in a single worker thread that drains the
ProjectStore queue and emits Qt signals for the UI."""

from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import QObject, QThread, Signal

from . import pipeline
from .control import Control, PipelineStopped


def prep_chapter(store, proj_id, chap_id, control=None,
                 on_status=None, on_progress=None) -> str:
    ch = store.get_chapter(proj_id, chap_id)
    if ch is None:
        return "missing"
    store.set_chapter(proj_id, chap_id, status="prepping", error=None)
    if on_status:
        on_status(proj_id, chap_id, "prepping")
    proj = store.get_project(proj_id)
    lang = (proj or {}).get("lang") or "ko"
    out_root = store.series_dir(proj_id)
    try:
        out_dir, outputs = pipeline.run(
            ch["source"], out_root=out_root, clean=True, inpaint="migan",
            typeset=lang, control=control,
            on_progress=(lambda st, d, t: on_progress(proj_id, chap_id, st, d, t))
            if on_progress else None,
        )
    except PipelineStopped:
        store.set_chapter(proj_id, chap_id, status="queued")
        if on_status:
            on_status(proj_id, chap_id, "queued")
        return "queued"
    except Exception as e:
        store.set_chapter(proj_id, chap_id, status="error", error=str(e))
        if on_status:
            on_status(proj_id, chap_id, "error")
        return "error"
    layout = outputs[0] if outputs else None
    thumb = os.path.join(out_dir, "typeset", "canvas_001.png")
    store.set_chapter(proj_id, chap_id, status="ready", layout=layout,
                      thumb=thumb, output_dir=out_dir, prepped_at=time.time())
    if on_status:
        on_status(proj_id, chap_id, "ready")
    return "ready"


class PrepQueue(QObject):
    status_changed = Signal(str, str, str)          # proj_id, chap_id, status
    progress = Signal(str, str, str, int, int)      # proj_id, chap_id, stage, done, total

    def __init__(self, store):
        super().__init__()
        self._store = store
        self._wake = threading.Event()
        self._running = True
        self._control = None
        self._thread = QThread()
        self.moveToThread(self._thread)
        self._thread.started.connect(self._loop)

    def start(self) -> None:
        self._thread.start()

    def enqueue(self, proj_id: str, chap_id: str) -> None:
        self._store.enqueue(proj_id, chap_id)
        self._wake.set()

    def skip_current(self) -> None:
        if self._control is not None:
            self._control.request_stop()

    def stop(self) -> None:
        self._running = False
        if self._control is not None:
            self._control.request_stop()
        self._wake.set()
        self._thread.quit()
        self._thread.wait(3000)

    def _loop(self) -> None:
        while self._running:
            job = self._store.pop_next()
            if job is None:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            proj_id, chap_id = job
            self._control = Control()
            prep_chapter(
                self._store, proj_id, chap_id, control=self._control,
                on_status=lambda p, c, s: self.status_changed.emit(p, c, s),
                on_progress=lambda p, c, st, d, t: self.progress.emit(p, c, st, d, t),
            )
            self._control = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_prepqueue.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/prepqueue.py tests/test_prepqueue.py
git commit -m "feat: add prep queue (prep_chapter core + PrepQueue thread)"
```

---

### Task 5: `projects_view.py` — Projects panel (list + detail)

**Files:**
- Create: `manhwaprep/projects_view.py`
- Test: manual (Qt UI) — verification steps in Step 4.

**Interfaces:**
- Consumes: `manhwaprep.projects.ProjectStore`; `manhwaprep.prepqueue.PrepQueue` (signals `status_changed`, `progress`); a callback `open_editor(layout_path: str)`.
- Produces:
  - `class ProjectsPanel(QWidget)`:
    - `__init__(self, store, queue, open_editor)`
    - `refresh(self) -> None` — rebuild the project list.
    - reacts to `queue.status_changed` / `queue.progress` to update the open detail view live.

- [ ] **Step 1: Write the widget**

```python
# manhwaprep/projects_view.py
"""Projects tab: a list of series, each opening a chapter board with per-chapter
status, live progress, and Open-editor / Export & mark done / Re-prep / Remove."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)


class _ChapterRow(QWidget):
    def __init__(self, panel, proj_id, ch):
        super().__init__()
        self.panel = panel
        self.proj_id = proj_id
        self.chap_id = ch["id"]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)

        self.thumb = QLabel()
        if ch.get("thumb") and os.path.exists(ch["thumb"]):
            pm = QPixmap(ch["thumb"])
            if not pm.isNull():
                self.thumb.setPixmap(pm.scaled(48, 34, Qt.KeepAspectRatioByExpanding,
                                               Qt.SmoothTransformation))
        lay.addWidget(self.thumb)

        self.name = QLabel(ch.get("name") or self.chap_id)
        self.name.setMinimumWidth(160)
        lay.addWidget(self.name)

        self.badge = QLabel()
        self.badge.setMinimumWidth(72)
        lay.addWidget(self.badge)

        self.bar = QProgressBar()
        self.bar.setMaximumWidth(140)
        self.bar.setVisible(False)
        lay.addWidget(self.bar)

        lay.addStretch(1)
        self.btns = QHBoxLayout()
        lay.addLayout(self.btns)
        self.apply(ch)

    def _clear_btns(self):
        while self.btns.count():
            it = self.btns.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _btn(self, text, slot):
        b = QPushButton(text)
        b.clicked.connect(slot)
        self.btns.addWidget(b)

    def apply(self, ch):
        status = ch.get("status", "queued")
        self.badge.setText({"queued": "Queued", "prepping": "Prepping…",
                            "ready": "Ready", "done": "Done",
                            "error": "Error"}.get(status, status))
        self.bar.setVisible(status == "prepping")
        if ch.get("thumb") and os.path.exists(ch["thumb"]) and self.thumb.pixmap() is None:
            pm = QPixmap(ch["thumb"])
            if not pm.isNull():
                self.thumb.setPixmap(pm.scaled(48, 34, Qt.KeepAspectRatioByExpanding,
                                               Qt.SmoothTransformation))
        self._clear_btns()
        layout = ch.get("layout")
        if status in ("ready", "done") and layout:
            self._btn("Open editor", lambda: self.panel.open_editor(layout))
        if status == "ready":
            self._btn("Export & mark done", self._mark_done)
        if status in ("ready", "done", "error"):
            self._btn("Re-prep", self._reprep)
        if status in ("queued", "prepping"):
            self._btn("Skip", self.panel.queue.skip_current)
        self._btn("Remove", self._remove)
        if status == "error" and ch.get("error"):
            self.setToolTip(ch["error"])

    def set_progress(self, done, total):
        self.bar.setVisible(True)
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
        else:
            self.bar.setRange(0, 0)

    def _mark_done(self):
        self.panel.store.set_chapter(self.proj_id, self.chap_id, status="done")
        self.panel.open_detail(self.proj_id)

    def _reprep(self):
        self.panel.store.set_chapter(self.proj_id, self.chap_id, status="queued")
        self.panel.queue.enqueue(self.proj_id, self.chap_id)
        self.panel.open_detail(self.proj_id)

    def _remove(self):
        if QMessageBox.question(self, "Remove chapter",
                                "Remove this chapter from the project? "
                                "(files are kept)") != QMessageBox.Yes:
            return
        self.panel.store.remove_chapter(self.proj_id, self.chap_id)
        self.panel.open_detail(self.proj_id)


class ProjectsPanel(QWidget):
    def __init__(self, store, queue, open_editor):
        super().__init__()
        self.store = store
        self.queue = queue
        self.open_editor = open_editor
        self._rows = {}          # chap_id -> _ChapterRow (current detail view)
        self._detail_pid = None

        self.stack = QStackedWidget()
        root = QVBoxLayout(self)
        root.addWidget(self.stack)

        # page 0: project list
        page0 = QWidget(); v0 = QVBoxLayout(page0)
        v0.addWidget(QLabel("<b>Projects</b> — click a series"))
        self.proj_list = QListWidget()
        self.proj_list.itemClicked.connect(
            lambda it: self.open_detail(it.data(Qt.UserRole)))
        v0.addWidget(self.proj_list)
        self.stack.addWidget(page0)

        # page 1: chapter detail
        page1 = QWidget(); self._v1 = QVBoxLayout(page1)
        head = QHBoxLayout()
        back = QPushButton("← Projects"); back.clicked.connect(self._show_list)
        head.addWidget(back)
        self.detail_title = QLabel(); head.addWidget(self.detail_title)
        head.addStretch(1)
        add = QPushButton("Add chapters…"); add.clicked.connect(self._add_chapters)
        head.addWidget(add)
        self._v1.addLayout(head)
        self._chap_area = QScrollArea(); self._chap_area.setWidgetResizable(True)
        self._chap_host = QWidget(); self._chap_v = QVBoxLayout(self._chap_host)
        self._chap_v.addStretch(1)
        self._chap_area.setWidget(self._chap_host)
        self._v1.addWidget(self._chap_area)
        self.stack.addWidget(page1)

        self.queue.status_changed.connect(self._on_status)
        self.queue.progress.connect(self._on_progress)
        self.refresh()

    def refresh(self):
        self.proj_list.clear()
        for p in self.store.list_projects():
            chs = p.get("chapters", [])
            counts = {}
            for c in chs:
                counts[c["status"]] = counts.get(c["status"], 0) + 1
            summary = " · ".join(f"{counts[k]} {k}" for k in
                                 ("queued", "prepping", "ready", "done", "error")
                                 if counts.get(k))
            item = QListWidgetItem(f"{p['name']}    ({summary or 'empty'})")
            thumb = next((c.get("thumb") for c in chs
                          if c.get("thumb") and os.path.exists(c["thumb"])), None)
            if thumb:
                pm = QPixmap(thumb)
                if not pm.isNull():
                    item.setIcon(QIcon(pm.scaled(72, 52, Qt.KeepAspectRatioByExpanding,
                                                 Qt.SmoothTransformation)))
            item.setData(Qt.UserRole, p["id"])
            self.proj_list.addItem(item)

    def _show_list(self):
        self._detail_pid = None
        self.refresh()
        self.stack.setCurrentIndex(0)

    def open_detail(self, proj_id):
        self._detail_pid = proj_id
        proj = self.store.get_project(proj_id)
        if proj is None:
            self._show_list(); return
        self.detail_title.setText(f"<b>{proj['name']}</b>")
        # clear existing rows
        while self._chap_v.count() > 1:            # keep the trailing stretch
            it = self._chap_v.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._rows = {}
        for ch in proj.get("chapters", []):
            row = _ChapterRow(self, proj_id, ch)
            self._rows[ch["id"]] = row
            self._chap_v.insertWidget(self._chap_v.count() - 1, row)
        self.stack.setCurrentIndex(1)

    def _on_status(self, proj_id, chap_id, status):
        if proj_id == self._detail_pid:
            ch = self.store.get_chapter(proj_id, chap_id)
            row = self._rows.get(chap_id)
            if ch and row:
                row.apply(ch)

    def _on_progress(self, proj_id, chap_id, stage, done, total):
        if proj_id == self._detail_pid:
            row = self._rows.get(chap_id)
            if row:
                row.set_progress(done, total)

    def _add_chapters(self):
        if not self._detail_pid:
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "Add chapters",
            "Paste chapter URLs (one per line):", "")
        if not ok or not text.strip():
            return
        for line in text.splitlines():
            url = line.strip()
            if not url:
                continue
            pid, cid = self.store.add_chapter(url)
            self.queue.enqueue(pid, cid)
        self.open_detail(self._detail_pid)
```

- [ ] **Step 2: Import-check the module**

Run:
```bash
cd ~/ManhwaPrep && QT_QPA_PLATFORM=offscreen ~/EasyScanlate/.venv/bin/python -c "from manhwaprep import projects_view; print('import OK')"
```
Expected: `import OK`

- [ ] **Step 3: Smoke-test the panel renders (offscreen)**

Run:
```bash
cd ~/ManhwaPrep && QT_QPA_PLATFORM=offscreen ~/EasyScanlate/.venv/bin/python -c "
import os, tempfile
from PySide6.QtWidgets import QApplication
from manhwaprep.projects import ProjectStore
from manhwaprep.prepqueue import PrepQueue
from manhwaprep.projects_view import ProjectsPanel
app = QApplication([])
s = ProjectStore(os.path.join(tempfile.mkdtemp(), 'projects.json'))
s.add_chapter('https://comix.to/title/55kym-why-the-villainess-wields-the-sword/9356816-chapter-1')
q = PrepQueue(s)
panel = ProjectsPanel(s, q, open_editor=lambda p: None)
panel.open_detail('comix:55kym-why-the-villainess-wields-the-sword')
assert panel.stack.currentIndex() == 1
print('panel OK, rows:', len(panel._rows))
"
```
Expected: `panel OK, rows: 1`

- [ ] **Step 4: Commit**

```bash
git add manhwaprep/projects_view.py
git commit -m "feat: add Projects panel (series list + chapter board)"
```

---

### Task 6: Wire into `ui.py` — replace Projects tab, auto-register on prep

**Files:**
- Modify: `manhwaprep/ui.py` (`MainWindow.__init__`, `_build_projects_tab`, `_refresh_projects`/`_open_project_item` removal, `_on_done`)
- Test: manual (run.sh) — verification steps in Step 3.

**Interfaces:**
- Consumes: `ProjectStore`, `PrepQueue`, `ProjectsPanel`, `projects.registry_path`, `recents.list_recent`.

- [ ] **Step 1: Create the store/queue and use the new panel**

In `manhwaprep/ui.py`, add imports near the other `from .` imports (top of file):

```python
from . import projects as projects_mod
from .prepqueue import PrepQueue
from .projects_view import ProjectsPanel
```

In `MainWindow.__init__`, immediately before the line
`self._projects_tab = self._build_projects_tab()`, insert:

```python
        # Project registry + single background prep queue (started once).
        self._store = projects_mod.ProjectStore(projects_mod.registry_path())
        self._store.reset_prepping()          # recover a crash mid-prep
        from . import recents as _recents
        self._store.import_recents(_recents.list_recent())
        self._prep_queue = PrepQueue(self._store)
        self._prep_queue.start()
```

Replace the whole body of `_build_projects_tab` with:

```python
    def _build_projects_tab(self) -> QWidget:
        self._projects_panel = ProjectsPanel(
            self._store, self._prep_queue, open_editor=self._open_typeset)
        return self._projects_panel
```

Delete the now-unused methods `_refresh_projects` and `_open_project_item` (the panel owns refresh/open). In `_on_tab_changed`, replace the `if on_projects: self._refresh_projects()` call with `if on_projects: self._projects_panel.refresh()`.

- [ ] **Step 2: Auto-register a finished Clean-tab prep into its project**

In `_on_done` (the typeset branch), after `self._open_typeset(outputs[0])`, add registration. Change:

```python
        if getattr(self, "_typeset_active", False) and outputs:
            self._append(f"✓ typeset canvas ready in {elapsed} — opening editor…")
            self._open_typeset(outputs[0])
```

to:

```python
        if getattr(self, "_typeset_active", False) and outputs:
            self._append(f"✓ typeset canvas ready in {elapsed} — opening editor…")
            layout_path = outputs[0]
            out_dir = os.path.dirname(os.path.dirname(layout_path))
            pid, cid = self._store.add_chapter(self._last_source or out_dir)
            self._store.set_chapter(
                pid, cid, status="ready", layout=layout_path,
                thumb=os.path.join(out_dir, "typeset", "canvas_001.png"),
                output_dir=out_dir)
            self._open_typeset(layout_path)
```

In `_start_clean`, record the source so registration can detect the series — add `self._last_source = source` as the first line of `_start_clean`.

- [ ] **Step 3: Manual verification via run.sh**

Run: `~/ManhwaPrep/run.sh`

Verify:
1. The **Projects** tab shows a series list (imported recents appear grouped).
2. Open a project → chapter rows show status badges; a **ready** row has *Open editor / Export & mark done / Re-prep / Remove*.
3. Click **Open editor** → the Khmer typeset editor opens in its own window.
4. In a project, **Add chapters…** → paste a comix chapter URL → a row appears as **Queued**, then **Prepping…** with a moving progress bar, then **Ready** — while the editor window stays open and usable.
5. **Export & mark done** flips the row to **Done**; **Re-prep** puts it back to Queued; **Remove** drops the row (files remain on disk).

Confirm each of the five behaviors before committing.

- [ ] **Step 4: Run the full unit-test suite (no regressions)**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/ -q`
Expected: all pass (existing suite + the new `test_jsonstore`, `test_series`, `test_projects`, `test_prepqueue`).

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/ui.py
git commit -m "feat: wire Projects panel + prep queue into the app"
```

---

### Task 7: Windows compatibility pass + final commit

**Files:**
- Modify (if needed): `manhwaprep.spec` (hidden imports)
- Test: import checks + full suite

The new modules are already cross-platform: `jsonstore` uses `msvcrt` on Windows
and `fcntl` elsewhere; `config.default_output_dir()` already branches on
`sys.platform == "win32"` (→ `%USERPROFILE%\ManhwaPrep\output`); `PrepQueue`
uses Qt threads; paths are built with `os.path.join`. So this task is a
verification + packaging pass, not a rewrite.

- [ ] **Step 1: Confirm no POSIX-only assumptions leaked in**

Run:
```bash
cd ~/ManhwaPrep && grep -nE "/tmp|fcntl\.|os\.uname|posix" manhwaprep/series.py manhwaprep/projects.py manhwaprep/prepqueue.py manhwaprep/projects_view.py manhwaprep/jsonstore.py
```
Expected: the only `fcntl` hit is inside `jsonstore.locked` guarded by `if os.name == "nt": msvcrt … else: fcntl …`. No bare `/tmp` or `os.uname`.

- [ ] **Step 2: Ensure the frozen build bundles the new modules**

The new modules are imported normally from `ui.py`, so PyInstaller's dependency
graph picks them up automatically. Confirm `manhwaprep.spec`'s `hiddenimports`
list (the block that names lazily-imported GUI modules) includes the new ones
only if any are imported lazily. Since `ui.py` imports them at module top,
**no `.spec` change is required**; verify by reading the spec's hiddenimports
block and confirming `ui` is reachable (it already is). Only add
`"manhwaprep.projects_view"`, `"manhwaprep.prepqueue"`, `"manhwaprep.projects"`,
`"manhwaprep.series"`, `"manhwaprep.jsonstore"` to `hiddenimports` if a build
warns they are missing.

- [ ] **Step 3: Full suite + import sanity**

Run:
```bash
cd ~/ManhwaPrep && ~/EasyScanlate/.venv/bin/python -m pytest tests/ -q && \
QT_QPA_PLATFORM=offscreen ~/EasyScanlate/.venv/bin/python -c "from manhwaprep import ui, projects, series, prepqueue, projects_view, jsonstore; print('all import OK')"
```
Expected: suite passes; `all import OK`.

- [ ] **Step 4: Final commit of all new features**

```bash
cd ~/ManhwaPrep && git add -A && git commit -m "feat: projects library + background prep queue (Windows-verified)"
```
(Local commit only — do not push. Rebuilding the macOS app and/or the Windows
.exe is a separate, user-initiated step.)

---

## Self-Review

**Spec coverage:**
- Auto-detect series → Task 2 (`series.detect`). ✓
- Group into series folders on disk → Task 4 `series_dir` + `pipeline.run(out_root=…)`. ✓
- Auto-queue several, sequential background prep → Task 4 `PrepQueue`. ✓
- Projects list + detail with per-chapter buttons → Task 5. ✓
- Status + live progress → Task 4 signals + Task 5 rows. ✓
- Open editor / Export & mark done / Re-prep / Remove → Task 5 `_ChapterRow`. ✓
- Every clean lands in a project → Task 6 Step 2 auto-register. ✓
- Explicit URL add (no scraping) → Task 5 `_add_chapters`. ✓
- Resume after crash → Task 3 `reset_prepping` + Task 6 Step 1. ✓
- Remove keeps files → Task 3 `remove_chapter(delete_files=False)`. ✓
- Legacy recents import → Task 3 `import_recents` + Task 6 Step 1. ✓
- Atomic/locked writes → Task 1 `jsonstore`. ✓
- Tests for pure/near-pure units → Tasks 1–4. ✓

**Placeholder scan:** none — every code step is complete.

**Type consistency:** `ProjectStore` method names/signatures used in Tasks 4–6 match Task 3 (`add_chapter`, `set_chapter`, `enqueue`, `pop_next`, `get_project`, `get_chapter`, `series_dir`, `remove_chapter`, `reset_prepping`, `import_recents`). `PrepQueue` signals (`status_changed(str,str,str)`, `progress(str,str,str,int,int)`) match between Task 4 and Task 5. `prep_chapter` callback shapes (`on_status(p,c,s)`, `on_progress(p,c,st,d,t)`) match Task 4 usage.

**Open item folded in:** `pipeline.run` returns `outputs[0]` = layout when `typeset` is set (Global Constraints), relied on by Tasks 4 and 6.
