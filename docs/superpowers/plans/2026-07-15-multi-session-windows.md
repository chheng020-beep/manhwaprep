# Multi-Session Windows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user open a second independent app window from inside ManhwaPrep so they can prep one chapter while translating another, with both windows safely sharing the same on-disk database.

**Architecture:** A `＋ New Window` button in the header launches a fully independent OS process via a new `relaunch` module. The two processes already share the same output dir and JSON registries (`recent_projects.json`, `recent_fonts.json`); we make the registry writers concurrency-safe (advisory lock + merge-on-write + atomic replace) so simultaneous saves never clobber each other.

**Tech Stack:** Python 3, PySide6 (Qt Widgets), `subprocess`, `fcntl`/`msvcrt` for locking, pytest.

## Global Constraints

- Tests set `os.environ["QT_QPA_PLATFORM"] = "offscreen"` and `sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")` at the top, before importing `manhwaprep`, matching existing tests in `tests/`.
- Run tests with the EasyScanlate venv python: `~/EasyScanlate/.venv/bin/python -m pytest`.
- Support both run modes: source (`python -m manhwaprep`, via `run.sh`) and frozen PyInstaller `.exe` (`getattr(sys, "frozen", False)`).
- Cross-platform: macOS (`posix`, primary dev) and Windows (`.exe` target). Locking/detach must degrade gracefully, never crash the app.
- Shared state paths are unchanged: `config.default_output_dir()`, and `recent_projects.json` / `recent_fonts.json` under `os.path.dirname(config.default_output_dir())`.
- Follow existing file style: module docstring, `from __future__ import annotations` where helpful, small focused functions.

---

### Task 1: `relaunch` module — build launch argv

**Files:**
- Create: `manhwaprep/relaunch.py`
- Test: `tests/test_relaunch.py`

**Interfaces:**
- Produces: `launch_argv() -> list[str]` — argv to relaunch the app, correct for source vs frozen builds.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_relaunch.py
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
from manhwaprep import relaunch


def test_launch_argv_from_source(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", "/venv/bin/python")
    assert relaunch.launch_argv() == ["/venv/bin/python", "-m", "manhwaprep"]


def test_launch_argv_when_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/apps/ManhwaPrep.exe")
    assert relaunch.launch_argv() == ["/apps/ManhwaPrep.exe"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_relaunch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'manhwaprep.relaunch'`

- [ ] **Step 3: Write minimal implementation**

```python
# manhwaprep/relaunch.py
"""Launch a second, independent copy of the app.

Lets the user open another window (a separate OS process) so they can prep one
chapter while working another. Command differs for source vs frozen builds.
"""

from __future__ import annotations

import sys


def launch_argv() -> list[str]:
    """Argv that relaunches this app, correct for source and frozen builds."""
    if getattr(sys, "frozen", False):
        return [sys.executable]  # PyInstaller .exe relaunches itself
    return [sys.executable, "-m", "manhwaprep"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_relaunch.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/relaunch.py tests/test_relaunch.py
git commit -m "feat: relaunch.launch_argv for source/frozen builds"
```

---

### Task 2: `relaunch` module — detached spawn

**Files:**
- Modify: `manhwaprep/relaunch.py`
- Test: `tests/test_relaunch.py`

**Interfaces:**
- Consumes: `launch_argv()` from Task 1.
- Produces: `spawn_new_window() -> None` — launches a detached, independent copy; uses `launch_argv()` and platform-appropriate detach flags. Lets `subprocess.Popen` exceptions propagate so the UI caller can warn the user.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_relaunch.py
def test_spawn_new_window_uses_launch_argv(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", "/venv/bin/python")
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(relaunch.subprocess, "Popen", fake_popen)
    relaunch.spawn_new_window()
    assert captured["argv"] == ["/venv/bin/python", "-m", "manhwaprep"]
    # detached: posix uses start_new_session, win32 uses creationflags
    if sys.platform == "win32":
        assert captured["kwargs"].get("creationflags", 0) != 0
    else:
        assert captured["kwargs"].get("start_new_session") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_relaunch.py::test_spawn_new_window_uses_launch_argv -v`
Expected: FAIL — `AttributeError: module 'manhwaprep.relaunch' has no attribute 'subprocess'` (and no `spawn_new_window`)

- [ ] **Step 3: Write minimal implementation**

Add to `manhwaprep/relaunch.py`:

```python
import subprocess


def spawn_new_window() -> None:
    """Launch a detached, fully independent copy of the app.

    The new process survives after the launching window closes.
    """
    argv = launch_argv()
    if sys.platform == "win32":
        # DETACHED_PROCESS (0x8) | CREATE_NEW_PROCESS_GROUP (0x200)
        flags = 0x00000008 | 0x00000200
        subprocess.Popen(argv, creationflags=flags, close_fds=True)
    else:
        subprocess.Popen(argv, start_new_session=True, close_fds=True)
```

(Place the `import subprocess` with the other top-level imports.)

- [ ] **Step 4: Run test to verify it passes**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_relaunch.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/relaunch.py tests/test_relaunch.py
git commit -m "feat: relaunch.spawn_new_window launches detached copy"
```

---

### Task 3: Concurrency-safe registry writes in `recents`

**Files:**
- Modify: `manhwaprep/recents.py`
- Test: `tests/test_recents.py`

**Interfaces:**
- Consumes: existing `recents.add_recent(layout_path, chapter="", thumb="")`, `recents.add_font(name)`, `recents.list_recent()`, `recents.list_fonts()`, `recents._registry_path()`, `recents._fonts_path()`.
- Produces: internal `_locked(path)` context manager; `add_recent`/`add_font` now re-read from disk under a lock, merge, and `os.replace()` atomically. Public signatures unchanged.

Note: tests point the registry at a temp dir by monkeypatching `config.default_output_dir` (recents derives paths from `os.path.dirname(config.default_output_dir())`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recents.py
import json
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
from manhwaprep import config, recents


def _point_registry_at(tmp_path, monkeypatch):
    # recents uses dirname(default_output_dir()) as the base for its JSON files
    out = tmp_path / "ManhwaPrep" / "output"
    out.mkdir(parents=True)
    monkeypatch.setattr(config, "default_output_dir", lambda: str(out))


def test_two_writes_both_survive(tmp_path, monkeypatch):
    _point_registry_at(tmp_path, monkeypatch)
    a = tmp_path / "ch4" / "layout.json"; a.parent.mkdir(parents=True); a.write_text("{}")
    b = tmp_path / "ch5" / "layout.json"; b.parent.mkdir(parents=True); b.write_text("{}")
    recents.add_recent(str(a), chapter="ch4")
    recents.add_recent(str(b), chapter="ch5")
    layouts = {e["chapter"] for e in recents.list_recent()}
    assert layouts == {"ch4", "ch5"}


def test_merge_against_external_write(tmp_path, monkeypatch):
    # Simulate a second process having written entry B to disk *after* this
    # process last read: add_recent must merge, not clobber.
    _point_registry_at(tmp_path, monkeypatch)
    a = tmp_path / "ch4" / "layout.json"; a.parent.mkdir(parents=True); a.write_text("{}")
    recents.add_recent(str(a), chapter="ch4")
    # external process appends entry B directly to the registry file
    reg = recents._registry_path()
    data = json.load(open(reg))
    b = tmp_path / "ch5" / "layout.json"; b.parent.mkdir(parents=True); b.write_text("{}")
    data.insert(0, {"layout": os.path.abspath(str(b)), "chapter": "ch5",
                    "thumb": "", "saved_at": 1.0})
    json.dump(data, open(reg, "w"))
    # now this process bumps ch4 again — must preserve ch5
    recents.add_recent(str(a), chapter="ch4")
    chapters = {e["chapter"] for e in recents.list_recent()}
    assert chapters == {"ch4", "ch5"}


def test_no_leftover_temp_file(tmp_path, monkeypatch):
    _point_registry_at(tmp_path, monkeypatch)
    a = tmp_path / "ch4" / "layout.json"; a.parent.mkdir(parents=True); a.write_text("{}")
    recents.add_recent(str(a), chapter="ch4")
    base = os.path.dirname(recents._registry_path())
    leftovers = [f for f in os.listdir(base) if f.endswith(".tmp")]
    assert leftovers == []


def test_add_font_dedupe_and_cap_under_new_path(tmp_path, monkeypatch):
    _point_registry_at(tmp_path, monkeypatch)
    for i in range(15):
        recents.add_font(f"Font{i}")
    recents.add_font("Font0")  # re-adding bumps to front, no dupe
    fonts = recents.list_fonts()
    assert fonts[0] == "Font0"
    assert len(fonts) <= 10
    assert len(fonts) == len(set(fonts))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_recents.py -v`
Expected: FAIL — `test_merge_against_external_write` fails (ch5 clobbered), and `test_no_leftover_temp_file` may error since `_locked` doesn't exist yet (only if imported). The clobber test is the key red failure.

- [ ] **Step 3: Write minimal implementation**

Add a locking helper near the top of `manhwaprep/recents.py` (after imports):

```python
import contextlib


@contextlib.contextmanager
def _locked(path: str):
    """Advisory cross-process lock around a registry read-merge-write.

    Uses fcntl on posix and msvcrt on Windows; degrades to a no-op if locking
    is unavailable so the app never crashes over recents bookkeeping.
    """
    lock_path = path + ".lock"
    f = None
    try:
        f = open(lock_path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass  # best-effort lock; still safe-ish via atomic replace
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


def _atomic_write(path: str, data) -> None:
    """Write JSON to a temp file in the same dir, then os.replace() onto path."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
```

Rewrite `add_recent` to lock + re-read + merge + atomic replace:

```python
def add_recent(layout_path: str, chapter: str = "", thumb: str = "") -> None:
    """Record (or bump) a project. layout_path is the chapter's layout.json."""
    layout_path = os.path.abspath(layout_path)
    reg = _registry_path()
    with _locked(reg):
        data = [e for e in _read() if e.get("layout") != layout_path]
        data.insert(0, {
            "layout": layout_path,
            "chapter": chapter or "",
            "thumb": thumb or "",
            "saved_at": time.time(),
        })
        try:
            _atomic_write(reg, data[:30])
        except Exception:
            pass
```

Rewrite `add_font` the same way:

```python
def add_font(name: str) -> None:
    """Push a font family to the front of the recently-used list."""
    if not name:
        return
    fp = _fonts_path()
    with _locked(fp):
        data = [f for f in list_fonts() if f != name]
        data.insert(0, name)
        try:
            _atomic_write(fp, data[:10])
        except Exception:
            pass
```

(`_read` and `list_fonts` already read fresh from disk each call, so calling them inside the lock gives the merge its up-to-date base.)

- [ ] **Step 4: Run test to verify it passes**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_recents.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/recents.py tests/test_recents.py
git commit -m "fix: concurrency-safe recents writes (lock + merge + atomic replace)"
```

---

### Task 4: `＋ New Window` button in the header

**Files:**
- Modify: `manhwaprep/ui.py` (header construction in `MainWindow.__init__`, around lines 203-208; add a handler method)
- Test: `tests/test_ui_new_window.py`

**Interfaces:**
- Consumes: `relaunch.spawn_new_window()` from Task 2.
- Produces: `MainWindow._on_new_window()` handler; a `self.new_window_btn` button wired to it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ui_new_window.py
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
import pytest
from PySide6.QtWidgets import QApplication
from manhwaprep import ui


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_new_window_button_spawns(app, monkeypatch):
    # Avoid loading models / heavy tabs is not needed: MainWindow builds tabs,
    # but we only assert the button calls relaunch.spawn_new_window once.
    calls = {"n": 0}
    monkeypatch.setattr(ui.relaunch, "spawn_new_window", lambda: calls.__setitem__("n", calls["n"] + 1))
    win = ui.MainWindow()
    assert hasattr(win, "new_window_btn")
    win.new_window_btn.click()
    assert calls["n"] == 1


def test_new_window_warns_on_failure(app, monkeypatch):
    def boom():
        raise RuntimeError("no exec")
    monkeypatch.setattr(ui.relaunch, "spawn_new_window", boom)
    warned = {"n": 0}
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a, **k: warned.__setitem__("n", warned["n"] + 1))
    win = ui.MainWindow()
    win.new_window_btn.click()  # must not raise
    assert warned["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_ui_new_window.py -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute 'new_window_btn'`

- [ ] **Step 3: Write minimal implementation**

At the top of `manhwaprep/ui.py`, ensure imports exist: add `relaunch` to the `from . import ...` / module imports, and confirm `QMessageBox` and `QPushButton` are imported from `PySide6.QtWidgets` (QPushButton already is; add QMessageBox if missing).

In `MainWindow.__init__`, replace the title block (currently `title = QLabel(...)` then `root.addWidget(title)`) with a header row that keeps the title and adds the button on the right:

```python
        header = QHBoxLayout()
        title = QLabel("ManhwaPrep")
        title.setFont(QFont("", 22, QFont.Bold))
        header.addWidget(title)
        header.addStretch(1)
        self.new_window_btn = QPushButton("＋ New Window")
        self.new_window_btn.setToolTip(
            "Open a second, independent app window — prep one chapter while working another."
        )
        self.new_window_btn.clicked.connect(self._on_new_window)
        header.addWidget(self.new_window_btn)
        root.addLayout(header)
```

Add the handler method to `MainWindow`:

```python
    def _on_new_window(self):
        try:
            relaunch.spawn_new_window()
        except Exception as e:
            QMessageBox.warning(
                self,
                "Couldn't open a new window",
                f"Failed to launch a second copy of ManhwaPrep:\n\n{e}",
            )
```

Add the import near the other `from . import ...` lines:

```python
from . import relaunch
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/EasyScanlate/.venv/bin/python -m pytest tests/test_ui_new_window.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full suite**

Run: `~/EasyScanlate/.venv/bin/python -m pytest -v`
Expected: all pass (existing + new).

- [ ] **Step 6: Commit**

```bash
git add manhwaprep/ui.py tests/test_ui_new_window.py
git commit -m "feat: + New Window button opens an independent app window"
```

---

## Manual Verification (after all tasks)

1. `./run.sh`, click `＋ New Window` → a second app window appears.
2. Window A: open a chapter in the editor. Window B: run Clean & Prepare on a different chapter — both run at once.
3. Save/open a project in each; refresh Projects in both windows → both chapters appear, none lost.
4. Close window A mid-job → window B keeps running.
