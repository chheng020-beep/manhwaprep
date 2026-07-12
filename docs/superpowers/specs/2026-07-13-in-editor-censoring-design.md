# In-Editor Censoring Feature — Design

**Goal:** Add an FB-compliant censoring layer to the typeset editor so 18+ art
can be covered before export, with a one-click auto-censor button plus manual
add/delete and a preview toggle.

**Date:** 2026-07-13

## Problem

The user posts translated manhwa to Facebook. Some panels contain adult content
that violates FB's Adult Nudity & Sexual Activity standards (drawn genitalia,
anus, fully-nude buttocks, female nipples, sexual activity). A static PNG overlay
only *covers* pixels and can slip or be identified as a sticker. The user needs
real, baked-in censoring they can control inside the existing editor.

## Locked Decisions

- **Detection:** NudeNet (`nudenet.NudeDetector`), auto-installed on first use.
- **Censor style:** pixelate / mosaic of the real pixels.
- **Auto-censor scope (FB-safe set):** female genitalia, male genitalia, exposed
  female breast, buttocks, anus.
- **Export safety:** the on/off toggle affects the **editor preview only**;
  export **always** bakes every censor box. The user can never post raw art by
  accident.
- **Detector setup:** first click of the auto button pip-installs `nudenet` into
  the EasyScanlate venv and downloads the model (one time, needs internet).
  Manual add/delete works regardless of whether the detector is available.

## Architecture

All UI/editor code lives in `manhwaprep/typeset_editor.py`. Detection and
pixelation live in a new module `manhwaprep/nsfw.py`. No changes to the Studio
pipeline, the manual splitter, or watermarking.

### 1. Data model — censors as a per-segment layer

Each segment dict gains `seg["_censors"]`: a list of plain dicts
`{"x": int, "y": int, "w": int, "h": int, "source": "auto" | "manual"}`.

- Coordinates are in canvas/scene pixels (same space as `seg["width"]` /
  `seg["height"]` and text-box `x/y/w/h`).
- It is JSON-serializable (no numpy), so it rides along in
  `typeset_project.json` exactly like the existing per-segment `_state`.
- Written in `_commit_items` (alongside `seg["_state"]`), rebuilt in
  `_load_segment` (alongside the text/image items).

### 2. Editor visualization — `CensorItem`

A new `QGraphicsItem` subclass (movable + selectable, with corner resize
handles, mirroring the existing `ImageItem`):

- **Paints a live mosaic of the real pixels beneath it**, read from
  `self._work_np` (the working raster). Because it reads `_work_np`, the preview
  always reflects the current art, including any paint/erase edits.
- Draws a dashed magenta border so it is obviously an editable censor, not part
  of the art.
- **Z-order** sits between the art background (`z = -1`) and the text boxes
  (`z = 0`) — concretely `z = -0.5`. Mosaic covers the art but Khmer text stays
  sharp on top.
- Held in `self.censors`, a list parallel to `self.items` / `self.images`.
- `to_dict()` / rebuild round-trips through the `_censors` data model.

Pixelation for the on-screen preview uses the same `nsfw.pixelate` used at
export, so what the user sees is what gets baked.

### 3. The four controls

- **One-click "Censor 18+" button** (a toolbar *action*, not a tool mode):
  runs `nsfw.detect` on the current segment's art (`_work_np`), maps each
  returned box to a `CensorItem`, and adds them with `source="auto"`. Scope is
  the current segment. If the detector is unavailable and auto-install fails or
  is declined, show a clear message and leave existing censors untouched.
- **Toggle "Censor" button** (checkable): shows/hides the whole censor layer in
  the **editor preview only**. Does not affect export.
- **Add — "Cen" tool mode:** a new tool button. In this mode, dragging on the
  canvas draws a new censor box (`source="manual"`). It is NOT added to
  `BRUSH_TOOLS` (it is not a raster brush; it creates a scene item).
- **Delete:** with the Select tool, selecting a `CensorItem` and pressing
  `Delete` / `Backspace` removes it. Uses the same selection layer as text
  boxes so it feels native.

All four mutations (auto-add, manual-add, move/resize, delete) are undoable —
censors are included in `_snap_state` / `_history` snapshots.

### 4. Export baking (FB-safe)

Export renders the scene via `_render(seg)`. Because `CensorItem` paints a
mosaic of the real pixels and sits under the text, the pixelation bakes into the
exported PNG automatically — it destroys the underlying pixels rather than
laying a removable overlay on top.

To honor "export always bakes regardless of the preview toggle": every export
render path (`render_translated`, `_export`, `_export_all`) forces **all**
censor items visible before rendering and restores their prior preview
visibility afterward. The toggle therefore can never leak uncensored output.

### 5. `manhwaprep/nsfw.py`

- `detect(bgr) -> list[dict]`: lazy-imports `nudenet.NudeDetector`, runs it on
  the given BGR image, filters detections to the FB-safe label set above a
  confidence threshold, and returns boxes as
  `[{"x","y","w","h","source":"auto"}, ...]`. NudeNet is run on a temp PNG or
  array as its API requires.
- `ensure_installed(parent=None) -> bool`: if `import nudenet` fails, runs
  `sys.executable -m pip install nudenet` behind a progress dialog, then imports.
  Returns whether the detector is now usable. Offline and instant after the
  first successful install.
- `pixelate(bgr_region) -> bgr_region`: downscale then `INTER_NEAREST` upscale;
  block size scaled to the region so it fully obscures detail. Used by both the
  editor preview and export baking (single source of truth for the look).

### 6. FB-compliance note

Censoring the FB-safe parts sharply reduces risk, but Facebook can still action
an overtly sexual *composition* even when the parts are covered. The safe move
is to censor **and** skip the most explicit panels. Drawn genitalia and female
nipples are restricted content, which is why they are in the default auto set.
This is guidance surfaced to the user, not enforced by the tool.

## Error Handling

- Detector unavailable / install fails / no internet: auto button shows a clear
  message; manual add/delete unaffected.
- NudeNet returns no boxes: show a brief "no adult regions detected — add
  manually if needed" message; do not error.
- A censor box partially outside the canvas: clamp to canvas bounds on bake.
- Loading an old project with no `_censors` key: treat as empty list.

## Testing (headless, offscreen Qt)

- `nsfw.detect` tested with a **fake detector injected** (monkeypatched) so tests
  need no model download or network; assert label filtering and box mapping.
- `nsfw.pixelate` on a solid-color region produces a blocky output distinct from
  the input (asserts it actually obscures).
- Editor: auto-censor (stubbed detect) creates `CensorItem`s; the "Cen" tool
  drag creates a manual censor; Delete removes a selected censor; toggle changes
  preview visibility only.
- **Export bake (the key test):** place a censor over a solid-color region,
  toggle the preview layer OFF, export, and assert that region in the output PNG
  is pixelated (block pattern / changed pixels) — proving export always censors
  and destroys real pixels.
- Persistence: censors survive a `_commit_items` → `_load_segment` round-trip and
  a project save/reload.

## Out of Scope (YAGNI)

- Auto-censoring every segment in one click (current-segment only; user can walk
  segments).
- Blur/solid-bar styles (pixelate only, per decision).
- Detecting non-FB-safe content (only the five FB-safe labels).
- Any change to the Studio pipeline, splitter, or watermark modules.
