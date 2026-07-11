# ManhwaPrep Studio Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Studio" tab that batch-prepares many manhwa chapters unattended, then routes the operator through three review gates (translation + fitment in the typeset editor, panel cutting in the splitter) to finished long images.

**Architecture:** A pure-logic brain (`studio.py`) owns a per-chapter state machine persisted as `status.json` files; a background `PrepWorker` runs the already-automated download→clean→stitch→transcript stretch one chapter at a time; a `StudioTab` Qt widget shows the queue and opens the existing editor/splitter at each chapter's current gate. Chapters are self-contained folders; nothing shares mutable memory.

**Tech Stack:** Python 3, PySide6 (Qt), existing ManhwaPrep modules (`pipeline`, `typeset_prep`, `transcript`, `typeset_editor`, `manual_split`, `control`, `config`). Tests run under the EasyScanlate venv with `QT_QPA_PLATFORM=offscreen`.

## Global Constraints

- Python interpreter for running/tests: `/Users/leapheakuoch/EasyScanlate/.venv/bin/python3`.
- Qt tests MUST set `QT_QPA_PLATFORM=offscreen` and monkeypatch modal dialogs (`QMessageBox.*`, `QInputDialog.getText`, `QFileDialog.*`) BEFORE constructing widgets, or `.exec()` hangs.
- Tests import the package via `sys.path.insert(0, '/Users/leapheakuoch/ManhwaPrep')`.
- Heavy ONNX model work (`typeset_prep.prep`) MUST be dependency-injected into the orchestrator so state-machine/worker tests run without models or network.
- `status.json` is the single source of truth; no separate database.
- Valid states: `queued`, `prepping`, `typeset`, `cut`, `done`, `error`. No other strings.
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Studio root default: `os.path.join(config.default_output_dir(), "studio")`.

---

## File structure

| File | Responsibility |
|------|---------------|
| `manhwaprep/studio.py` (create) | `ChapterJob` dataclass, state constants, `status.json` I/O, `Studio` queue manager, `write_transcript_txt`, `prep_job`, `PrepWorker` |
| `manhwaprep/studio_tab.py` (create) | `StudioTab` QWidget: queue table, add-work controls, action routing to gates |
| `manhwaprep/typeset_editor.py` (modify) | Add `render_translated(out_dir)` public method + "Ready to cut" button hook |
| `manhwaprep/ui.py` (modify) | Register the Studio tab in `MainWindow` |
| `tests/test_studio.py` (create) | Headless tests for job I/O, queue, prep orchestration, worker |
| `tests/test_studio_gates.py` (create) | Offscreen tests for `render_translated`, tab construction, action routing |

---

### Task 1: ChapterJob + status.json I/O

**Files:**
- Create: `manhwaprep/studio.py`
- Test: `tests/test_studio.py`

**Interfaces:**
- Produces:
  - State constants: `QUEUED="queued"`, `PREPPING="prepping"`, `TYPESET="typeset"`, `CUT="cut"`, `DONE="done"`, `ERROR="error"`.
  - `slugify(title: str) -> str`
  - `@dataclass ChapterJob` with fields `title:str, source:str, slug:str, state:str="queued", error:str|None=None, updated_at:str=""`.
  - `ChapterJob.to_status(dir_path: str) -> None` writes `<dir_path>/status.json`.
  - `ChapterJob.from_status(dir_path: str) -> ChapterJob` reads it.
  - `NEXT_STATE = {"prepping":"typeset", "typeset":"cut", "cut":"done"}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_studio.py
import os, sys, tempfile
sys.path.insert(0, '/Users/leapheakuoch/ManhwaPrep')
from manhwaprep import studio


def test_slugify_is_filesystem_safe():
    assert studio.slugify("The Broken Ring: ch 3!") == "the-broken-ring-ch-3"
    assert studio.slugify("  多 spaces  ") != ""  # never empty


def test_job_status_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        job = studio.ChapterJob(title="T", source="http://x/c3", slug="t",
                                state=studio.TYPESET)
        job.to_status(d)
        assert os.path.exists(os.path.join(d, "status.json"))
        back = studio.ChapterJob.from_status(d)
        assert back.title == "T" and back.source == "http://x/c3"
        assert back.state == studio.TYPESET
        assert back.updated_at  # stamped on write
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'manhwaprep.studio'`

- [ ] **Step 3: Write minimal implementation**

```python
# manhwaprep/studio.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd /Users/leapheakuoch/ManhwaPrep
git add manhwaprep/studio.py tests/test_studio.py
git commit -m "feat(studio): ChapterJob + status.json persistence

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Studio queue manager (scan / add / advance / retry)

**Files:**
- Modify: `manhwaprep/studio.py`
- Test: `tests/test_studio.py`

**Interfaces:**
- Consumes: `ChapterJob`, state constants, `NEXT_STATE` from Task 1.
- Produces:
  - `class Studio(root: str)`
  - `Studio.chapter_dir(slug: str) -> str` → `<root>/<slug>`
  - `Studio.add(source: str, title: str) -> ChapterJob` (creates dir + status.json in `queued`; unique slug on collision)
  - `Studio.scan() -> list[ChapterJob]` (rebuild from `<root>/*/status.json`; any `prepping` reset to `queued`; sorted by `updated_at`)
  - `Studio.advance(slug: str) -> ChapterJob` (state → `NEXT_STATE[state]`, clears error)
  - `Studio.set_error(slug: str, msg: str) -> ChapterJob`
  - `Studio.retry(slug: str) -> ChapterJob` (error → queued, clears error)
  - `Studio.set_state(slug: str, state: str) -> ChapterJob`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_studio.py
def test_add_scan_advance_and_prepping_reset():
    with tempfile.TemporaryDirectory() as root:
        st = studio.Studio(root)
        j = st.add("http://x/ch3", "Broken Ring ch3")
        assert j.state == studio.QUEUED
        assert os.path.isdir(st.chapter_dir(j.slug))

        # simulate a crash mid-prep
        st.set_state(j.slug, studio.PREPPING)
        jobs = st.scan()
        assert len(jobs) == 1
        assert jobs[0].state == studio.QUEUED  # prepping reset on scan

        st.set_state(j.slug, studio.PREPPING)
        st.advance(j.slug)
        assert studio.ChapterJob.from_status(st.chapter_dir(j.slug)).state == studio.TYPESET


def test_error_and_retry():
    with tempfile.TemporaryDirectory() as root:
        st = studio.Studio(root)
        j = st.add("http://x/ch4", "ch4")
        st.set_error(j.slug, "download failed")
        assert studio.ChapterJob.from_status(st.chapter_dir(j.slug)).state == studio.ERROR
        st.retry(j.slug)
        got = studio.ChapterJob.from_status(st.chapter_dir(j.slug))
        assert got.state == studio.QUEUED and got.error is None


def test_slug_collision_is_unique():
    with tempfile.TemporaryDirectory() as root:
        st = studio.Studio(root)
        a = st.add("http://x/1", "Same Title")
        b = st.add("http://x/2", "Same Title")
        assert a.slug != b.slug
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio.py -v`
Expected: FAIL with `AttributeError: module 'manhwaprep.studio' has no attribute 'Studio'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to manhwaprep/studio.py

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
            if job.state == PREPPING:      # app died mid-prep -> re-queue
                job.state = QUEUED
                job.to_status(d)
            jobs.append(job)
        jobs.sort(key=lambda j: j.updated_at)
        return jobs

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
cd /Users/leapheakuoch/ManhwaPrep
git add manhwaprep/studio.py tests/test_studio.py
git commit -m "feat(studio): queue manager with scan/add/advance/retry

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Prep orchestration (`prep_job` + `write_transcript_txt`)

**Files:**
- Modify: `manhwaprep/studio.py`
- Test: `tests/test_studio.py`

**Interfaces:**
- Consumes: `Studio`, state constants from Tasks 1-2.
- Produces:
  - `write_transcript_txt(layout_path: str) -> str` — reads `layout.json`, writes `transcript.txt` (one numbered line per item: `"{n}. [{kind}] {src}"`) next to it, returns its path.
  - `prep_job(studio: Studio, slug: str, prep_fn=typeset_prep.prep, control=None, on_status=None) -> None` — sets `prepping`, calls `prep_fn(out_dir=chapter_dir, source=job.source, control=..., on_status=...)`, writes `transcript.txt`, advances to `typeset`. On exception sets `error`. `prep_fn` is injected so tests skip the ONNX models.
- Note: `prep_fn` must return the `layout.json` path (matches `typeset_prep.prep`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_studio.py
import json

def _fake_prep(out_dir, source=None, control=None, on_status=None, **kw):
    """Stand-in for typeset_prep.prep: writes a minimal typeset/ layout."""
    ts = os.path.join(out_dir, "typeset")
    os.makedirs(ts, exist_ok=True)
    layout = {"chapter": os.path.basename(out_dir), "lang": "en", "segments": [
        {"image": "canvas_001.png", "width": 800, "height": 1200, "items": [
            {"n": 1, "bbox": [10, 10, 100, 40], "src": "Hello", "kind": "bubble"},
            {"n": 2, "bbox": [10, 80, 90, 30], "src": "BOOM", "kind": "sfx"},
        ]}]}
    p = os.path.join(ts, "layout.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(layout, f)
    return p


def test_prep_job_success_advances_to_typeset():
    with tempfile.TemporaryDirectory() as root:
        st = studio.Studio(root)
        j = st.add("http://x/ch3", "ch3")
        studio.prep_job(st, j.slug, prep_fn=_fake_prep)
        job = studio.ChapterJob.from_status(st.chapter_dir(j.slug))
        assert job.state == studio.TYPESET
        tx = os.path.join(st.chapter_dir(j.slug), "typeset", "transcript.txt")
        assert os.path.exists(tx)
        body = open(tx, encoding="utf-8").read()
        assert "1. [bubble] Hello" in body and "2. [sfx] BOOM" in body


def test_prep_job_failure_sets_error():
    with tempfile.TemporaryDirectory() as root:
        st = studio.Studio(root)
        j = st.add("http://x/bad", "bad")
        def boom(**kw): raise RuntimeError("no pages")
        studio.prep_job(st, j.slug, prep_fn=boom)
        job = studio.ChapterJob.from_status(st.chapter_dir(j.slug))
        assert job.state == studio.ERROR and "no pages" in job.error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio.py -k prep_job -v`
Expected: FAIL with `AttributeError: module 'manhwaprep.studio' has no attribute 'prep_job'`

- [ ] **Step 3: Write minimal implementation**

```python
# add near the top imports of manhwaprep/studio.py
from . import typeset_prep

# append to manhwaprep/studio.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
cd /Users/leapheakuoch/ManhwaPrep
git add manhwaprep/studio.py tests/test_studio.py
git commit -m "feat(studio): prep_job orchestration + transcript.txt writer

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Background PrepWorker

**Files:**
- Modify: `manhwaprep/studio.py`
- Test: `tests/test_studio.py`

**Interfaces:**
- Consumes: `Studio`, `prep_job` from Task 3.
- Produces:
  - `run_queue(studio: Studio, prep_fn=typeset_prep.prep, control=None, on_status=None, on_job_change=None) -> int` — loops: scan for the first `queued` job, `prep_job` it, repeat until none remain or `control.is_stopped()`. Calls `on_job_change(slug)` after each state change. Returns count processed. This is the pure worker loop (no Qt), so it is unit-testable.
- Note: the Qt thread wrapper is created in Task 6's tab (a `QThread` that just calls `run_queue`), so no Qt here.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_studio.py
def test_run_queue_processes_all_queued():
    with tempfile.TemporaryDirectory() as root:
        st = studio.Studio(root)
        a = st.add("http://x/1", "one")
        b = st.add("http://x/2", "two")
        seen = []
        n = studio.run_queue(st, prep_fn=_fake_prep, on_job_change=seen.append)
        assert n == 2
        assert studio.ChapterJob.from_status(st.chapter_dir(a.slug)).state == studio.TYPESET
        assert studio.ChapterJob.from_status(st.chapter_dir(b.slug)).state == studio.TYPESET
        assert a.slug in seen and b.slug in seen


def test_run_queue_stops_on_control():
    with tempfile.TemporaryDirectory() as root:
        st = studio.Studio(root)
        st.add("http://x/1", "one")
        from manhwaprep.control import Control
        ctl = Control(); ctl.request_stop()
        n = studio.run_queue(st, prep_fn=_fake_prep, control=ctl)
        assert n == 0  # stopped before processing
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio.py -k run_queue -v`
Expected: FAIL with `AttributeError: module 'manhwaprep.studio' has no attribute 'run_queue'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to manhwaprep/studio.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
cd /Users/leapheakuoch/ManhwaPrep
git add manhwaprep/studio.py tests/test_studio.py
git commit -m "feat(studio): run_queue worker loop with stop control

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `render_translated` on the typeset editor + "Ready to cut" hook

**Files:**
- Modify: `manhwaprep/typeset_editor.py`
- Test: `tests/test_studio_gates.py`

**Interfaces:**
- Consumes: existing `TypesetEditor(layout_path)`, its per-segment render (`_save_render(seg, out, watermarked)` or the export path).
- Produces:
  - `TypesetEditor.render_translated(out_dir: str, watermarked: bool = False) -> list[str]` — renders every segment's current canvas (with Khmer boxes burned in) to `<out_dir>/rendered_001.png …`, returns the paths in order. This is the bridge feeding the cut gate.
  - `TypesetEditor.set_ready_callback(fn)` — stores a 0-arg callback invoked when the operator clicks a new **"✅ Ready to cut"** toolbar button. (Studio wires this to `advance`.)
- Note: reuse the exact segment list the editor already iterates in its export methods; do not re-implement rendering.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_studio_gates.py
import os, sys, json, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, '/Users/leapheakuoch/ManhwaPrep')
import numpy as np, cv2
from PySide6.QtWidgets import QApplication, QMessageBox, QInputDialog, QFileDialog

_app = QApplication.instance() or QApplication([])
# neutralise modal dialogs before any editor is built
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: None)
QInputDialog.getText = staticmethod(lambda *a, **k: ("", True))


def _make_layout(d):
    ts = os.path.join(d, "typeset"); os.makedirs(ts, exist_ok=True)
    canvas = np.full((1200, 800, 3), 255, np.uint8)
    cv2.imwrite(os.path.join(ts, "canvas_001.png"), canvas)
    layout = {"chapter": "t", "lang": "en", "segments": [
        {"image": "canvas_001.png", "width": 800, "height": 1200, "items": [
            {"n": 1, "bbox": [20, 20, 200, 60], "src": "Hi", "kind": "bubble"}]}]}
    p = os.path.join(ts, "layout.json")
    json.dump(layout, open(p, "w", encoding="utf-8"))
    return p


def test_render_translated_writes_images():
    with tempfile.TemporaryDirectory() as d:
        from manhwaprep.typeset_editor import TypesetEditor
        ed = TypesetEditor(_make_layout(d))
        out = os.path.join(d, "rendered")
        paths = ed.render_translated(out)
        assert len(paths) == 1
        assert os.path.exists(paths[0])
        assert cv2.imread(paths[0]) is not None


def test_ready_callback_fires():
    with tempfile.TemporaryDirectory() as d:
        from manhwaprep.typeset_editor import TypesetEditor
        ed = TypesetEditor(_make_layout(d))
        fired = []
        ed.set_ready_callback(lambda: fired.append(True))
        ed._on_ready_to_cut()   # the button's slot
        assert fired == [True]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio_gates.py -k "render_translated or ready_callback" -v`
Expected: FAIL with `AttributeError: 'TypesetEditor' object has no attribute 'render_translated'`

- [ ] **Step 3: Write minimal implementation**

First locate the editor's segment list and its existing single-segment render. In `manhwaprep/typeset_editor.py`, `_save_render(self, seg, out, watermarked)` renders one segment and the export loop iterates `self.segments` (or `self.layout["segments"]`). Add these methods to the `TypesetEditor` class (place near `_export_all`):

```python
    def render_translated(self, out_dir: str, watermarked: bool = False) -> list[str]:
        """Render each segment's canvas with Khmer boxes burned in to
        <out_dir>/rendered_NNN.png; return the paths in order."""
        os.makedirs(out_dir, exist_ok=True)
        paths = []
        for i, seg in enumerate(self.segments, 1):   # same list export uses
            out = os.path.join(out_dir, f"rendered_{i:03d}.png")
            self._save_render(seg, out, watermarked)
            paths.append(out)
        return paths

    def set_ready_callback(self, fn):
        self._ready_cb = fn

    def _on_ready_to_cut(self):
        self._save()                       # persist project.json first
        if getattr(self, "_ready_cb", None):
            self._ready_cb()
```

Then wire a toolbar button in `__init__` (next to the existing export buttons):

```python
        self.ready_btn = QPushButton("✅ Ready to cut")
        self.ready_btn.setToolTip("Save and hand this chapter to the panel-cutting gate")
        self.ready_btn.clicked.connect(self._on_ready_to_cut)
        # add to the same layout/row that holds the export buttons, e.g.:
        export_row.addWidget(self.ready_btn)
```

If the segment attribute is named differently (e.g. `self.layout["segments"]`), use that exact name; verify by reading the export method before editing.

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio_gates.py -k "render_translated or ready_callback" -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd /Users/leapheakuoch/ManhwaPrep
git add manhwaprep/typeset_editor.py tests/test_studio_gates.py
git commit -m "feat(typeset): render_translated + Ready-to-cut hook for Studio

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: StudioTab — queue table, add-work, gate routing, background thread

**Files:**
- Create: `manhwaprep/studio_tab.py`
- Test: `tests/test_studio_gates.py`

**Interfaces:**
- Consumes: `Studio`, `run_queue`, state constants (Tasks 1-4); `TypesetEditor` + `render_translated` + `set_ready_callback` (Task 5); existing `ManualSplitWidget`.
- Produces:
  - `class StudioTab(QWidget)`
  - `StudioTab.add_source(source: str, title: str)` — `studio.add` + refresh + (re)start worker thread.
  - `StudioTab.refresh()` — rebuild the table from `studio.scan()`.
  - `StudioTab.open_gate(slug: str)` — routes by state: `typeset` → open editor (wired so its Ready-to-cut advances the job); `cut` → render translated canvases and open `ManualSplitWidget` on them; `done`/`error` handled by dedicated buttons.
  - `StudioTab._on_split_export(slug)` — after the splitter writes panels to `<chapter>/output/`, `studio.advance(slug)` (cut → done).
- Interface detail: `open_gate` must be callable headlessly. It stores the opened widget on `self._gate_widget` (so tests can assert type) and must accept an injected launcher via `StudioTab(studio, editor_cls=TypesetEditor, split_cls=ManualSplitWidget)` for testability.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_studio_gates.py
def test_tab_routes_typeset_to_editor(monkeypatch=None):
    import manhwaprep.studio as studio
    from manhwaprep.studio_tab import StudioTab
    from manhwaprep.typeset_editor import TypesetEditor

    with tempfile.TemporaryDirectory() as root:
        st = studio.Studio(root)
        # build a chapter already in 'typeset' with a real layout on disk
        j = st.add("http://x/1", "one")
        cdir = st.chapter_dir(j.slug)
        _make_layout(cdir)              # writes typeset/layout.json + canvas
        st.set_state(j.slug, studio.TYPESET)

        tab = StudioTab(st)
        tab.refresh()
        tab.open_gate(j.slug)
        assert isinstance(tab._gate_widget, TypesetEditor)


def test_tab_cut_gate_opens_splitter_and_advance_on_export():
    import manhwaprep.studio as studio
    from manhwaprep.studio_tab import StudioTab
    from manhwaprep.manual_split import ManualSplitWidget

    with tempfile.TemporaryDirectory() as root:
        st = studio.Studio(root)
        j = st.add("http://x/1", "one")
        cdir = st.chapter_dir(j.slug)
        _make_layout(cdir)
        st.set_state(j.slug, studio.CUT)

        tab = StudioTab(st)
        tab.refresh()
        tab.open_gate(j.slug)
        assert isinstance(tab._gate_widget, ManualSplitWidget)
        # simulate splitter finishing an export
        tab._on_split_export(j.slug)
        assert studio.ChapterJob.from_status(cdir).state == studio.DONE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio_gates.py -k tab -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'manhwaprep.studio_tab'`

- [ ] **Step 3: Write minimal implementation**

```python
# manhwaprep/studio_tab.py
"""Studio tab: the queue board that drives chapters through the review gates."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QLabel,
    QTableWidget, QTableWidgetItem,
)

from . import studio as studio_mod
from .control import Control


class _WorkerThread(QThread):
    changed = Signal(str)

    def __init__(self, studio):
        super().__init__()
        self._studio = studio
        self.control = Control()

    def run(self):
        studio_mod.run_queue(self._studio, control=self.control,
                             on_job_change=self.changed.emit)


class StudioTab(QWidget):
    def __init__(self, studio, editor_cls=None, split_cls=None):
        super().__init__()
        self._studio = studio
        # late imports keep construction light and testable
        if editor_cls is None:
            from .typeset_editor import TypesetEditor as editor_cls  # noqa
        if split_cls is None:
            from .manual_split import ManualSplitWidget as split_cls  # noqa
        self._editor_cls = editor_cls
        self._split_cls = split_cls
        self._gate_widget = None
        self._thread = None

        lay = QVBoxLayout(self)
        add_row = QHBoxLayout()
        self._src = QLineEdit(); self._src.setPlaceholderText("Chapter URL or folder path")
        self._title = QLineEdit(); self._title.setPlaceholderText("Title")
        add_btn = QPushButton("Add"); add_btn.clicked.connect(self._on_add)
        add_row.addWidget(self._src); add_row.addWidget(self._title); add_row.addWidget(add_btn)
        lay.addLayout(add_row)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Chapter", "State", "Action"])
        lay.addWidget(self._table)
        self.refresh()

    # --- queue / worker ---
    def _on_add(self):
        src, title = self._src.text().strip(), self._title.text().strip()
        if src:
            self.add_source(src, title or src)

    def add_source(self, source: str, title: str):
        self._studio.add(source, title)
        self.refresh()
        self.start_worker()

    def start_worker(self):
        if self._thread and self._thread.isRunning():
            return
        self._thread = _WorkerThread(self._studio)
        self._thread.changed.connect(lambda slug: self.refresh())
        self._thread.start()

    # --- table ---
    def refresh(self):
        jobs = self._studio.scan()
        self._table.setRowCount(len(jobs))
        for r, job in enumerate(jobs):
            self._table.setItem(r, 0, QTableWidgetItem(job.title))
            self._table.setItem(r, 1, QTableWidgetItem(job.state))
            btn = self._action_button(job)
            self._table.setCellWidget(r, 2, btn)

    def _action_button(self, job) -> QWidget:
        label = {studio_mod.TYPESET: "Typeset", studio_mod.CUT: "Cut",
                 studio_mod.DONE: "Open output", studio_mod.ERROR: "Retry",
                 studio_mod.QUEUED: "Queued…", studio_mod.PREPPING: "Prepping…"}.get(job.state, "")
        b = QPushButton(label)
        if job.state in (studio_mod.TYPESET, studio_mod.CUT):
            b.clicked.connect(lambda _, s=job.slug: self.open_gate(s))
        elif job.state == studio_mod.ERROR:
            b.clicked.connect(lambda _, s=job.slug: (self._studio.retry(s), self.refresh(), self.start_worker()))
        elif job.state == studio_mod.DONE:
            b.clicked.connect(lambda _, s=job.slug: self._open_output(s))
        else:
            b.setEnabled(False)
        return b

    # --- gates ---
    def open_gate(self, slug: str):
        job = studio_mod.ChapterJob.from_status(self._studio.chapter_dir(slug))
        if job.state == studio_mod.TYPESET:
            self._open_typeset(slug)
        elif job.state == studio_mod.CUT:
            self._open_cut(slug)

    def _open_typeset(self, slug: str):
        layout = os.path.join(self._studio.chapter_dir(slug), "typeset", "layout.json")
        ed = self._editor_cls(layout)
        ed.set_ready_callback(lambda s=slug: (self._studio.advance(s), self.refresh()))
        self._gate_widget = ed
        ed.show()

    def _open_cut(self, slug: str):
        cdir = self._studio.chapter_dir(slug)
        layout = os.path.join(cdir, "typeset", "layout.json")
        rendered = os.path.join(cdir, "rendered")
        # render the lettered canvases so the splitter cuts the translated art
        ed = self._editor_cls(layout)
        imgs = ed.render_translated(rendered, watermarked=False)
        sp = self._split_cls()
        if hasattr(sp, "load_images"):
            sp.load_images(imgs)
        elif imgs and hasattr(sp, "load_image"):
            sp.load_image(imgs[0])
        # advance to done once the splitter exports panels to output/
        if hasattr(sp, "set_export_dir"):
            sp.set_export_dir(os.path.join(cdir, "output"))
        if hasattr(sp, "set_export_callback"):
            sp.set_export_callback(lambda s=slug: self._on_split_export(s))
        self._gate_widget = sp
        sp.show()

    def _on_split_export(self, slug: str):
        job = studio_mod.ChapterJob.from_status(self._studio.chapter_dir(slug))
        if job.state == studio_mod.CUT:
            self._studio.advance(slug)
            self.refresh()

    def _open_output(self, slug: str):
        path = os.path.join(self._studio.chapter_dir(slug), "output")
        os.makedirs(path, exist_ok=True)
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
```

Note on `ManualSplitWidget` hooks: if it lacks `load_images`/`set_export_dir`/`set_export_callback`, add those thin methods to `manual_split.py` in this task (a `set_export_callback(fn)` stored on the widget and invoked at the end of its existing export routine; a `load_images(list)` that loads the first and queues the rest; a `set_export_dir(path)` that overrides its output folder). Keep them additive so the standalone Split tab is unchanged. Verify the existing export method name by reading `manual_split.py` before wiring.

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio_gates.py -k tab -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd /Users/leapheakuoch/ManhwaPrep
git add manhwaprep/studio_tab.py manhwaprep/manual_split.py tests/test_studio_gates.py
git commit -m "feat(studio): StudioTab board with gate routing + worker thread

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Register the Studio tab in the main window

**Files:**
- Modify: `manhwaprep/ui.py` (near line 209-215, the `QTabWidget` setup)
- Test: `tests/test_studio_gates.py`

**Interfaces:**
- Consumes: `StudioTab` (Task 6), `Studio` (Task 1), `config.default_output_dir` (existing).
- Produces: a "Studio" tab in `MainWindow`, backed by a `Studio` rooted at `<default_output_dir>/studio`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_studio_gates.py
def test_mainwindow_has_studio_tab():
    from manhwaprep.ui import MainWindow
    w = MainWindow()
    titles = [w.tabs.tabText(i) for i in range(w.tabs.count())]
    assert "Studio" in titles
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio_gates.py -k mainwindow -v`
Expected: FAIL with `AssertionError` (no "Studio" tab)

- [ ] **Step 3: Write minimal implementation**

In `manhwaprep/ui.py`, add imports at top:

```python
import os
from . import config
from .studio import Studio
from .studio_tab import StudioTab
```

In `MainWindow.__init__`, right after `self.tabs.addTab(self._split_tab, "Manual Split")`:

```python
        studio_root = os.path.join(config.default_output_dir(), "studio")
        self._studio = Studio(studio_root)
        self._studio_tab = StudioTab(self._studio)
        self.tabs.addTab(self._studio_tab, "Studio")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio_gates.py -v`
Expected: PASS (all gate tests)

- [ ] **Step 5: Full suite + commit**

```bash
cd /Users/leapheakuoch/ManhwaPrep
QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio.py tests/test_studio_gates.py -v
git add manhwaprep/ui.py tests/test_studio_gates.py
git commit -m "feat(ui): register Studio tab in main window

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage:** state machine (T1-2), status.json truth + launch scan + prepping-reset (T2), prep worker unattended + error-continue (T3-4), workspace layout (T1/T3), typeset gate (T5-6), explicit Ready-to-cut advance (T5-6), cut gate → output/ → done (T6), board with per-row action (T6), tab integration (T7), testing strategy (all tasks headless/offscreen). Error handling: prep failure→error+retry (T3, T6), crash mid-prep reset (T2).
- **Dependency injection** (`prep_fn`, `editor_cls`, `split_cls`) keeps every orchestration test free of ONNX models and network.
- **Verify-before-edit reminders** are called out where private attribute/method names in `typeset_editor.py` and `manual_split.py` must be confirmed by reading the file first (segment list name, export routine name).
- **Deferred to execution:** exact `manual_split.py` hook method names — the plan specifies additive `load_images`/`set_export_dir`/`set_export_callback` and to confirm the existing export method before wiring.
