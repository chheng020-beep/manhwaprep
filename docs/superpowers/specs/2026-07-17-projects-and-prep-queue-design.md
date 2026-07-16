# Projects library + background prep queue — design

**Date:** 2026-07-17
**Status:** approved (design), ready for implementation plan
**Modules:** new `series.py`, `projects.py`, `prepqueue.py`; changes to `ui.py`,
`recents.py`, and the Clean-tab prep wiring. Reuses `pipeline.run`, `typeset_editor`.

## Goal

Let the user work through a series without babysitting downloads: every chapter
they prep/clean is filed under a **Project** (the series), and a background
**prep queue** cleans the next chapters while they typeset the current one. Each
chapter in a project shows its status + live progress and has buttons to open the
typeset editor, mark done, re-prep, or remove.

Derived from the current app: the typeset editor already opens as a separate
non-modal window, `pipeline.run()` already reports progress and supports
pause/stop via `Control`, and `recents.py` already persists a flat chapter list.
This feature organizes those pieces; it does not rewrite them.

## Decisions (locked)

- **Grouping:** auto-detect the series from the source.
- **Background prep:** auto-queue several chapters, prepped sequentially.
- **Disk layout:** group into series folders — `output/<series>/<chapter>/…`.
  Existing flat `output/<chapter>/` chapters keep working; only new ones group.
- **Export/done:** "Export & mark done" just sets status `done`; the user exports
  flattened images from the typeset editor as today (no new headless exporter).
- **Add chapters:** the user supplies chapter URLs explicitly (paste one per line)
  or drops folders — no auto-scraping of a series' chapter list.
- **Chapter rows show/do:** status + live progress, Open editor, Export & mark
  done, Re-prep, Remove.

## Components (isolated units)

### `series.py` (pure)
- `detect(source) -> SeriesInfo(series_id, series_name, chapter_number|None, chapter_id, chapter_name)`.
  - comix.to URL `/title/<slug>/<cid>-chapter-<n>` → `series_id="comix:<slug>"`,
    `series_name` = title-cased slug, `chapter_number=n`, `chapter_id="<cid>-chapter-<n>"`.
  - Other toon URLs → `series_id` from host+title path; chapter from `wr_id`/last path segment.
  - Folder path → `series_id="folder:<parent-name>"`, chapter = folder name.
  - Unsure → `series_id="ungrouped"`, `series_name="Ungrouped"`.
- `slugify(name) -> str` for on-disk folder names.
- Unit-tested; no I/O.

### `projects.py` (registry model)
- Owns `projects.json`: load, save (atomic + locked — reuse/relocate
  `recents._locked` + `_atomic_write` into a shared helper), and operations:
  `list_projects()`, `get_project(id)`, `add_chapter(source)->(projId,chapId)`
  (creates the project if new, dedupes by chapter id), `set_status(projId,chapId,status,**fields)`,
  `enqueue(projId,chapId)`, `next_queued()`, `remove_chapter(...,delete_files=False)`.
- Schema per the data model below.
- On first load, best-effort import existing `recent_projects.json` entries into
  projects (series parsed from the chapter name, else "Ungrouped"); non-destructive.
- Unit-tested against a temp registry dir.

### `prepqueue.py` (background engine)
- `PrepQueue(QObject)` moved onto one dedicated `QThread`. Processes the persisted
  queue **one chapter at a time** (heavy OCR/inpaint must not overlap).
- For each job: `set_status(prepping)`, run
  `pipeline.run(source, out_root=<series dir>, control=Control(), on_status=…, on_progress=…)`,
  then `set_status(ready, layout=…, thumb=…)`; on exception `set_status(error, error=msg)`
  and continue to the next job.
- Signals: `status_changed(projId, chapId, status)`, `progress(projId, chapId, stage, done, total)`.
  Persists to `projects.json` on every transition.
- Pause/skip current job via the shared `Control`.
- On startup, reset any `prepping` chapters to `queued` and resume the queue.

### UI (`ui.py` + new `projects_view.py`)
- **Projects tab** becomes two views:
  - *Project list*: one card per project (thumb, name, "N ready · N done · N queued") → open detail.
  - *Project detail*: header with series name + `Add chapters…` + back; chapter rows
    sorted by number. Row = thumb, name, status badge, progress bar (while prepping),
    and state-dependent buttons:
    - queued/prepping → Skip, Remove
    - ready → Open editor, Export & mark done, Re-prep, Remove
    - done → Open editor, Re-prep, Remove
    - error → Re-prep, Remove (error in tooltip)
  - `Add chapters…` = paste chapter URLs (one per line) and/or drop folders → each
    `add_chapter` + `enqueue`.
- Rows subscribe to `PrepQueue` signals → live updates, no manual refresh.
- **Clean tab** unchanged for one-off prep, but its completion now also calls
  `projects.add_chapter` + `set_status(ready)` so every clean lands in a project.

## Data model (`projects.json`)

```json
{
  "projects": [{
    "id": "comix:55kym-why-the-villainess-wields-the-sword",
    "name": "Why the Villainess Wields the Sword",
    "series_url": "https://comix.to/title/55kym-…",
    "created_at": 0, "updated_at": 0,
    "chapters": [{
      "id": "9356816-chapter-1", "number": 1, "name": "Chapter 1",
      "source": "https://comix.to/title/55kym-…/9356816-chapter-1",
      "status": "ready",
      "progress": {"stage": "clean", "done": 174, "total": 174},
      "output_dir": ".../output/why-the-villainess…/chapter-1",
      "layout": ".../typeset/layout.json",
      "thumb": ".../typeset/canvas_001.png",
      "error": null,
      "queued_at": 0, "prepped_at": 0, "done_at": 0
    }]
  }],
  "queue": ["comix:55kym…/9356816-chapter-1"]
}
```

## Data flow

1. User adds chapters (Clean tab, or project *Add chapters…*) → `series.detect` →
   `projects.add_chapter` (creates project if new) → `enqueue`.
2. `PrepQueue` pulls next job → `pipeline.run` into the series folder → emits
   progress → row's bar updates live → on success `status=ready` (`layout`/`thumb` set).
3. User clicks **Open editor** on a ready chapter → existing `TypesetEditor(layout)`
   in its own window; queue keeps prepping other chapters meanwhile.
4. User finishes, exports from the editor, clicks **Export & mark done** → `status=done`.

## Error handling & edge cases

- Prep failure → `status=error` + message; queue continues; Re-prep re-enqueues.
- Duplicate chapter (same id already present) → not re-added; offer Re-prep instead.
- Series detection unsure → chapter lands under the "Ungrouped" project.
- Crash mid-prep → startup resets `prepping`→`queued`, resumes.
- Remove → drops the registry entry, **keeps files**; deleting files is a separate explicit action.
- All registry writes are atomic + cross-process locked (existing pattern).

## Testing

- `test_series.py` — `detect()` for comix URLs, other URLs, folder paths, fallback; `slugify`.
- `test_projects.py` — add/dedupe chapter, status transitions, queue ordering,
  persistence round-trip, legacy-recents import, temp dir.
- `test_prepqueue.py` — state machine with a monkeypatched `pipeline.run`
  (success, error, resume-after-restart); no real downloads.
- UI verified by hand via `run.sh` (drop a folder, queue a couple of URLs, watch
  rows go queued→prepping→ready, open editor, mark done).

## Build stages (single plan, two natural phases)

1. `series.py` + `projects.py` + Clean-tab auto-register + Projects tab grouped
   list & detail with **Open editor** (delivers grouping immediately).
2. `prepqueue.py` + Add chapters + live progress + Re-prep/Remove + Export & mark done.

## Non-goals

- No auto-scraping of a series' full chapter list.
- No headless export engine (export stays in the editor).
- No concurrent multi-chapter prepping.
- No changes to the download/descramble or typeset internals.
