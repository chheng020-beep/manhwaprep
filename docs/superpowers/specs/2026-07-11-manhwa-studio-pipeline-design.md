# ManhwaPrep Studio — review-gated automation pipeline

**Date:** 2026-07-11
**Status:** Design approved, pending spec review

## Goal

Turn ManhwaPrep from a set of manual tools into an assembly-line pipeline where
the operator only performs three reviews per chapter:

1. **Translation quality** — is the Khmer good?
2. **Lettering fitment** — does the Khmer sit right in each bubble?
3. **Panel cutting** — are the final long-image seams in the right places?

Everything else (download, text erase, stitch, bubble detection, transcript,
text-box placement, auto-fit, safe-seam cut placement, watermarking) runs
automatically. Target content is BL manhwa, but nothing in the pipeline is
genre-specific — "BL" only affects translation tone, which is handled by the
human's own Claude translation step.

## Decisions locked during brainstorming

- **Translation: semi-auto paste-back.** No API cost. The app hands the operator
  a numbered transcript for their own Claude subscription; the operator pastes
  the Khmer back and the app maps numbered lines to bubbles. (This flow already
  exists in the typeset editor — Copy / Paste Khmer.)
- **Workflow: hybrid — batch prep, guided review.** A background worker
  downloads/cleans/stitches many chapters unattended; the operator then reviews
  each chapter guided through its gates.
- **Final output: ~5 long stitched images** per chapter (for a reader app /
  ManhwaBot PDF library), cut at safe seams. Panel cutting = choosing those
  seams via the existing manual splitter.
- **Orchestration: Studio status-board.** A queue tab showing each chapter's
  gate status; clicking a chapter opens the right existing tool at its current
  gate.
- **Gate granularity: combined typeset gate.** Translation and lettering-fitment
  happen in one editor session (two review moments, one tool). Board states:
  `queued → prepping → typeset → cut → done` (+ `error`).

## Architecture

Three new pieces on top of the existing scripts, each with one responsibility:

### 1. `studio.py` — the brain (no Qt)

Owns the queue of `ChapterJob`s and the per-chapter state machine. Pure logic,
fully testable headless.

State machine (per chapter):

```
queued → prepping → typeset → cut → done
                 ↘  error  ↙ (retry re-queues)
```

- `queued` — accepted into the queue, not yet processed.
- `prepping` — the worker is running acquire → clean → stitch → layout +
  transcript.
- `typeset` — ready for the operator: paste-back translation + review fitment in
  the editor.
- `cut` — ready for the operator: review/adjust safe-seam cuts, export.
- `done` — final long images written to `output/`.
- `error` — prep failed; message stored; Retry re-queues.

Each job's truth lives in a `status.json` on disk. Studio has no in-memory
database — on launch it scans `<studio_root>/*/status.json` and rebuilds the
queue. A job found in `prepping` on launch (i.e. the app died mid-prep) is reset
to `queued`.

`ChapterJob` (dataclass, serialized to `status.json`):

```json
{
  "title": "The Broken Ring — ch 3",
  "source": "https://comix.to/…/chapter-3",
  "slug": "the-broken-ring-ch-3",
  "state": "typeset",
  "error": null,
  "updated_at": "2026-07-11T09:00:00"
}
```

### 2. Prep worker (background thread)

Pulls `queued` jobs one at a time (the ONNX detect/inpaint models are heavy —
no parallelism) and runs the already-automated stretch:

```
acquire (pipeline._acquire_*) → clean + stitch + layout (typeset_prep.prep)
  → numbered transcript (transcript.Transcriber) written to transcript.txt
  → state := typeset
```

- Honors the existing Pause/Stop-at-page-boundary control object.
- On any exception: `state := error`, message saved, worker continues to the
  next job. One bad chapter never stalls the batch.
- Prep is idempotent per folder: re-running regenerates `typeset/` cleanly.

### 3. `studio_tab.py` — the board (Qt)

A table, one row per chapter: title, state, and a single action button that
always means "do the next thing":

- `prepping` — spinner / progress, no action.
- `typeset` — **Typeset** → opens the typeset editor on this chapter's
  `layout.json`.
- `cut` — **Cut** → renders the lettered canvases flat and opens the manual
  splitter with auto safe-seam cuts pre-placed.
- `done` — **Open output folder**; right-click → Reopen at typeset/cut.
- `error` — red row; **Retry** re-queues.

Adding work: drop a folder / paste a chapter URL (or a series URL + chapter
range, reusing the comix chapter-list capability) → jobs created as `queued`.

Data flows one direction: worker writes files + `status.json` → Studio reads
state → board shows it → operator clicks → a gate tool opens on that chapter's
folder → on finish it writes back + advances state. Chapters are independent
folders; nothing shares mutable state in memory.

## Per-chapter workspace

```
<studio_root>/<chapter_slug>/
  typeset/                    ← exactly what typeset_prep.prep() already writes
    canvas_001.png …          cleaned, stitched long canvases
    layout.json               { segments:[{image,width,height,items}] }  bubble boxes
    transcript.txt            numbered lines for paste-back   (worker writes)
    project.json              editor's saved boxes + Khmer text (editor already writes here)
  output/
    long_001.png …            final ~5 watermarked long images
  status.json                 { title, source, slug, state, error, updated_at }  ← only new file
```

`status.json` is the only new artifact. Everything else is produced/consumed by
existing tools.

## The gates (reuse existing tools)

### Typeset gate (`state: typeset`)

Opens the **typeset editor** (`typeset_editor.py`) on `layout.json`. One sitting,
two reviews:

1. **Copy** → numbered transcript to clipboard → operator pastes into their
   Claude → **Paste Khmer** back → auto-maps numbered lines to bubbles. →
   *translation-quality review.*
2. Boxes auto-fit; operator nudges any that need it. → *lettering-fitment
   review.*

Soft warning on line/bubble count mismatch ("12 Khmer lines, 14 bubbles — 2 left
untranslated") to catch a mis-paste. Operator may still proceed.

**Advancing is explicit, not on window-close.** Closing the editor only saves
`project.json` (the operator may be taking a break with work unfinished). The
job moves to `cut` only when the operator clicks an explicit **Ready to cut**
action (a button in the editor, mirrored as a board row action). This prevents
half-lettered chapters from silently advancing.

### Cut gate (`state: cut`)

Renders the finished lettered canvases flat and opens the **manual splitter**
(`manual_split.py`) with auto safe-seam cuts pre-placed (~5 parts, never through
a bubble). Operator adjusts, hits **Export** → watermarked `long_001…png` written
to `output/`, state → `done`.

### Automatic (no review)

Runs before/within the gates, already built: bubble detection, text-box
placement, number→box mapping, Khmer auto-fit, auto safe-seam cut placement,
logo watermark on export.

## Error handling & resumability

- **Prep failure** → `error` + message; Retry re-queues; batch continues.
- **Crash mid-prep** → `prepping` jobs reset to `queued` on launch; partial
  `typeset/` regenerated.
- **Bad paste-back** → soft mismatch warning; partial mapping allowed.
- **Re-review** → a `done` chapter reopens at any gate from the board without
  re-prepping (the folder holds everything).
- **Interrupted acquire** → existing multi-tier downloader fallback + Pause/Stop
  reused as-is.

## Testing

- **`studio.py` state machine (headless, pure):** every transition
  (queued→prepping→typeset→cut→done), error/retry paths, and the launch scan that
  rebuilds the queue from `status.json`. No Qt, no network.
- **Worker on a tiny fixture:** one synthetic 2-page local-folder chapter (no
  download) driven through prep; assert it reaches `typeset` with `layout.json`,
  `transcript.txt`, and canvases present. Reuses the offscreen-Qt + EasyScanlate
  venv methodology.
- **Gate advance:** simulate editor/splitter finishing (write `project.json` /
  `output/*.png`); assert `status.json` advances correctly.
- **Board UI:** offscreen construction test (builds without crashing) + manual
  smoke test of rendering and button routing.

## Component boundaries (isolation)

| Unit | Responsibility | Depends on | Testable |
|------|---------------|------------|----------|
| `studio.py` | Queue + state machine + status.json I/O | stdlib only | headless, pure |
| prep worker | Run acquire→clean→stitch→transcript per job | pipeline, typeset_prep, transcript | fixture-driven |
| `studio_tab.py` | Board table + action routing | studio.py, Qt, existing editor/splitter | offscreen build + manual |
| existing tools | Gate implementations | unchanged | already covered |

## Out of scope (YAGNI)

- Claude API translation (chose paste-back).
- Local MT model.
- Parallel prep (models too heavy; sequential is fine).
- FB-panel output (chose long images).
- Any new review tool — the board only orchestrates existing ones.
