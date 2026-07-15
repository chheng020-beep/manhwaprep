# Multi-Session Windows — Design Spec

**Date:** 2026-07-15
**Status:** Approved, ready for planning

## Problem

You can only comfortably work one chapter at a time. While translating/typesetting
chapter 4 in the Studio/typeset editor, you want chapter 5 already running through
Clean & Prepare (download → erase → stitch) in a *second copy of the app*.

Today the app is a single-window PySide6 desktop app (`MainWindow`, a `QWidget`)
with one background worker thread. Nothing stops you from launching `run.sh` a
second time from another terminal — but there's no in-app way to do it, and the
two copies share on-disk state that is not written safely for concurrent access.

## Goal

Let the user open a second (or third) independent app window from inside the app,
with **both copies sharing the same database and output folder**, and make that
shared state safe to write from multiple processes at once.

- Two+ independent OS processes (full memory isolation; each loads its own models).
- Shared on-disk state: they already point at the same paths
  (`config.default_output_dir()`, `recent_projects.json`, `recent_fonts.json`).
  No change needed to *share* — the work is making concurrent writes safe.
- One-click "New Window" from any tab.

## Non-Goals (YAGNI)

- No shared-model / in-process second window (chose full process isolation).
- No in-app session list, cross-window messaging, or job coordination.
- No single-instance guard or instance limit.
- No change to where output is saved — it already lands in the shared
  `default_output_dir()`, and both windows see it.

## Design

### 1. "New Window" button (UI) — `manhwaprep/ui.py`

`MainWindow` is a plain `QWidget` (no native menu bar), so add a small
**`＋ New Window`** `QPushButton` to the existing header row, next to the
"ManhwaPrep" title label (top of the root `QVBoxLayout`). Placing it in the
header — not inside the Projects tab — keeps it reachable from every tab.

- On click: call `relaunch.spawn_new_window()` and return immediately.
- The current window keeps working; the new process is fully independent.
- On spawn failure, surface a non-fatal `QMessageBox.warning` (don't crash the
  running window).

### 2. Detached process spawn — `manhwaprep/relaunch.py` (new module)

Two small functions so the command logic is unit-testable without spawning:

```python
def launch_argv() -> list[str]:
    """Argv to relaunch the app, correct for source vs frozen builds."""
    if getattr(sys, "frozen", False):
        return [sys.executable]              # PyInstaller .exe relaunches itself
    return [sys.executable, "-m", "manhwaprep"]  # e.g. run.sh / dev

def spawn_new_window() -> None:
    """Launch an independent, detached copy of the app."""
    # posix (macOS): start_new_session=True
    # windows: creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
```

`spawn_new_window` uses `subprocess.Popen` with platform-appropriate detach flags
so the second window survives after the first window closes. `cwd` is left at the
default (project state uses absolute paths from `config`, so cwd is irrelevant).

### 3. Concurrency-safe shared database — `manhwaprep/recents.py`

The shared JSON registries (`recent_projects.json`, `recent_fonts.json`) currently
do read → modify-in-memory → overwrite. With two processes, whoever saves last
wipes the other's new entry, and a reader can catch a half-written file. Fix all
mutating writers (`add_recent`, `add_font`):

1. **Cross-process advisory lock** held around the whole read-merge-write:
   `fcntl.flock` on posix (macOS), `msvcrt.locking` on Windows, via a small
   context-manager helper `_locked(path)` that no-ops gracefully if locking is
   unavailable. Lock file lives next to the registry (e.g. `<name>.lock`).
2. **Merge-on-write**: inside the lock, re-read the file from disk *now* (not a
   stale in-memory copy), insert/bump the current entry, dedupe, cap length.
3. **Atomic replace**: write to a temp file in the same dir, `os.replace()` onto
   the target so readers only ever see a complete file.

Read paths (`list_recent`, `list_fonts`) already tolerate a missing/corrupt file
(try/except → `[]`); with atomic replace they'll always read a complete file.

## File Changes

| File | Change |
|------|--------|
| `manhwaprep/relaunch.py` | New: `launch_argv()`, `spawn_new_window()` |
| `manhwaprep/ui.py` | Add `＋ New Window` button to header; wire to `relaunch.spawn_new_window()` with warning-on-failure |
| `manhwaprep/recents.py` | Add `_locked()` helper; make `add_recent`/`add_font` lock + merge-on-write + atomic replace |
| `tests/` | New tests for `relaunch.launch_argv` and concurrent `recents` writes |

## Testing

**`relaunch`** (no real process spawned):
- `launch_argv()` returns `[sys.executable, "-m", "manhwaprep"]` when not frozen.
- With `sys.frozen` monkeypatched true, returns `[sys.executable]`.

**`recents`** (the actual bug being fixed):
- Two sequential `add_recent` calls to different chapters → **both** survive
  (regression guard for clobbering).
- Simulate interleaved write: write entry A, then a second "process" reads-merges
  entry B against the on-disk A → file contains both.
- Atomic-replace: assert no leftover temp file and file is always valid JSON.
- `add_font` dedupe + cap still hold under the new write path.

## Manual Verification

1. `./run.sh`, click `＋ New Window` → a second app window appears.
2. In window A, open a chapter in the editor; in window B, run Clean & Prepare on
   a different chapter — both run at once.
3. Save/open a project in each; confirm both chapters appear in the Projects tab
   of **both** windows after refresh (shared DB, no lost entries).
4. Close window A while window B is mid-job → window B keeps running.
