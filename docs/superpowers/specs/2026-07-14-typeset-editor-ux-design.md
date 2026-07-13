# Typeset Editor UX Pass — Design

**Goal:** Make the ManhwaPrep typeset editor fit-to-frame and responsive, remove
the broken gradient feature, add `Cmd+C`/`Cmd+V` text-box copy/paste, and make
interaction smoother.

**Date:** 2026-07-14

## Problem

Four editor pain points, all in `manhwaprep/typeset_editor.py`:

1. **The canvas never fits the frame.** The view (`_CanvasView`) has no
   fit-to-viewport logic — no `fitInView`, no `resizeEvent` re-fit — and both
   scrollbars are forced off. Stitched manhwa canvases are wider than the window
   at raw 1:1, so the art overflows sideways and the user has to pan left/right
   to see a panel. `_load_segment` centers the horizontal scroll on load
   (`typeset_editor.py:2033`) — a workaround for content that was never fit.
   Resizing the window (full screen or not) never re-fits.
2. **The gradient fill is broken and unused.** It also costs performance:
   `_draw_gradient_text` allocates and alpha-composites a fresh `QImage` on every
   repaint.
3. **No fast way to reuse a styled text box.** Adding and restyling a box from
   scratch is tedious; the user wants to copy an existing box and paste it.
4. **General stutter** when interacting with styled boxes.

## Locked Decisions

- **Fit mode:** fit **width** — the canvas fills the frame side-to-side at a
  readable size; the wheel scrolls down the tall page; never sideways. Auto-refit
  on window resize (works windowed and full-screen). `Ctrl`+wheel still zooms.
- **Gradient:** **remove entirely** — fields, UI panel, paint path, persistence,
  and duplicate handling.
- **Copy/paste scope:** **text boxes only** (images already have `Cmd+V` image
  paste and `Cmd+D` duplicate).
- **`Cmd+V` behavior:** **box-first, else image** — if the copy buffer holds
  boxes, paste them; otherwise fall through to the existing
  `_paste_clipboard_image()`. Nothing that works today breaks.
- **Cross-canvas:** the copy buffer persists across canvas/segment switches — copy
  a styled box on one page, paste it on any other page.
- **Paste placement:** `+20px` offset, **cascading** on repeated paste so copies
  fan out instead of stacking. Full style cloned. Undoable.

## Global Constraints

- Do not change the Studio pipeline, the manual splitter, `watermark.py`, or the
  censoring feature (`nsfw.py` / `CensorItem` / export bake).
- Copy buffer is **in-memory only** — never written to `typeset_project.json`.
- Loading an **old project** whose saved boxes still carry `gradient_colors` /
  `gradient_angle` must not crash: those keys are simply ignored, and the box
  renders as a solid fill.
- Fit-width must not fight `Ctrl`+wheel zoom: after the user manually zooms, a
  window resize re-fits to width (fit is the baseline; manual zoom is transient).
- Test command (headless): `QT_QPA_PLATFORM=offscreen
  /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest <path> -v`. No test
  may hit the network.

## Architecture

### 1. Fit-to-width + responsive (`_CanvasView` + `_load_segment`)

Add a `fit_width()` method to `_CanvasView` that scales the view so the scene
rect's **width** maps to the viewport width (accounting for the frame margin),
leaving the tall canvas scrollable vertically. Concretely: reset the transform,
compute `scale = viewport_width / scene_width`, and `self.scale(scale, scale)`;
then align the view to the top of the scene.

Call `fit_width()`:
- at the end of `_load_segment` (replacing the manual horizontal-scroll centering
  at `typeset_editor.py:2030-2033`, which becomes unnecessary), and
- from a new `resizeEvent` override on `_CanvasView` (so the fit tracks every
  window resize, windowed or full-screen), and
- from a `showEvent` override (the first real viewport size is only known once the
  view is shown; fitting in `__init__` uses a stale size).

`Ctrl`+wheel zoom (`wheelEvent`, `typeset_editor.py:1164`) is unchanged; manual
zoom is transient and the next resize re-fits to width. Vertical scrolling by
plain wheel continues to work (scrollbars stay visually off, scroll offset still
functions).

**Data flow:** window/segment change → `fit_width()` reads viewport + scene rect →
sets the view transform. No model state; purely presentational.

### 2. Remove gradient (`TextBoxItem`, `_GradPanel`, editor wiring)

Delete every gradient touch-point found in the code:
- `TextBoxItem`: remove `self.gradient_colors` / `self.gradient_angle`
  (`~280-281`), `_make_gradient` (`~391`), `_draw_gradient_text` (`~405`), the
  `elif self.gradient_colors:` branch in `paint` (`~612`), and the two
  `gradient_*` keys in `to_dict` (`~743-744`).
- Loading: drop the `gradient_colors` / `gradient_angle` reads in
  `_rebuild_from_state` (`~1973-1974`) — old saved values are ignored.
- Duplicate: drop the gradient copy lines in `_duplicate_selected`
  (`~2332-2333`).
- UI: remove the `_GradPanel` class (`~1267`), its construction and the
  `self._grad_panel.gradient_picked = self._apply_gradient` wiring (`~1773`), and
  the `_apply_gradient` method (`~2875`).

After removal, `paint` falls back to the existing solid-fill / gradient-free path.
This is the primary smoothness win — no per-repaint `QImage` allocation.

### 3. Copy/paste text boxes (`TypesetEditor`)

**DRY refactor first.** `_duplicate_selected` (`~2300`) already clones a text box
with full style. Extract two helpers so copy/paste and duplicate share one
code path:
- `_spec_from_box(item) -> dict`: capture text + geometry + every style attribute
  (font, `max_size`, fill, outline, outline_w, align, line_spacing, effect,
  effect_color, rotation) as a plain dict. **No gradient keys** (removed in §2).
- `_box_from_spec(spec, dx, dy) -> TextBoxItem`: build a `TextBoxItem` from a spec
  offset by `(dx, dy)`, assign a fresh box number, apply style, wire `on_edit`,
  `_refit()`, restore rotation, add to scene + `self.items`.

`_duplicate_selected` is rewritten to use these (paste at `+20`).

**New state on `TypesetEditor.__init__`:** `self._copy_buffer = []` (list of specs)
and `self._paste_seq = 0` (cascade counter). The buffer is **not** cleared in
`_load_segment` (cross-canvas), and **not** persisted.

- `_copy_selected()` (`Cmd+C`): set `self._copy_buffer = [_spec_from_box(it) for
  it in selected TextBoxItems]`; reset `self._paste_seq = 0`. No-op if nothing
  selected (leaves any prior buffer intact).
- `_paste_boxes()` (`Cmd+V` dispatch): if `self._copy_buffer` is non-empty,
  `self._paste_seq += 1`; offset `= 20 * self._paste_seq`; clear selection,
  create one box per spec via `_box_from_spec(spec, offset, offset)`, select the
  new boxes, `_record_if_changed()`. If the buffer is empty, call
  `_paste_clipboard_image()` (today's behavior).

**Key wiring** (`keyPressEvent`, `~2407`):
- Add a `Cmd/Ctrl+C` branch → `_copy_selected()`.
- Change the existing Paste branch (`~2414`) to call `_paste_boxes()` instead of
  `_paste_clipboard_image()` directly.

### 4. Smoothness

Removing the gradient `QImage` path (§2) is the main win. If a box with an
**effect** (shadow/glow/outline) still stutters after that, cache its rendered
pixmap keyed by (text, style, size) and invalidate on change — but only if
measured stutter remains. YAGNI otherwise.

## Error Handling / Edge Cases

- **Empty copy buffer + `Cmd+V`:** falls through to image paste (no error).
- **`Cmd+C` with nothing selected:** no-op; prior buffer preserved.
- **Paste onto a different-sized canvas:** the pasted box keeps its absolute
  scene coordinates + offset; if that lands partly off-canvas the user drags it in
  (matches current `_duplicate_selected` behavior — not clamped).
- **Old project with gradient keys:** keys ignored on load; box renders solid.
- **`fit_width` before the view is shown:** guarded — skip if viewport width or
  scene width is 0; `showEvent` fits once a real size exists.

## Testing (headless, offscreen Qt)

**Fit-to-width:**
- After `_load_segment`, the view transform's horizontal scale ≈
  `viewport_width / scene_width` (within tolerance). Assert on `view.transform().m11()`.
- Simulate a resize (`view.resize(w, h)` → `resizeEvent`) and assert the scale
  tracks the new width.

**Gradient removed:**
- `TextBoxItem` has no `gradient_colors` attribute; `to_dict()` has no
  `gradient_*` keys.
- Loading a legacy state dict that *contains* `gradient_colors` rebuilds a box
  without error and without a gradient attribute.

**Copy/paste:**
- Copy a styled box (custom font size, fill, effect) → `_paste_boxes()` on the
  **same** canvas yields a new box with identical text + style, a new `n`, offset
  `+20`; a second paste is offset `+40` (cascade).
- Copy on canvas 0, `_load_segment(1)`, `_paste_boxes()` → box appears on canvas 1
  (cross-canvas persistence).
- `_paste_boxes()` with an empty buffer calls `_paste_clipboard_image` (monkeypatch
  it and assert it was invoked).
- Paste is undoable: after paste, `_undo()` removes the new box(es).

## Out of Scope (YAGNI)

- Copy/paste for images or censors (text boxes only).
- Persisting the copy buffer across app restarts.
- A "fit whole page" mode or a fit/zoom toggle button (fit-width is the baseline;
  `Ctrl`+wheel already zooms).
- Re-implementing gradients in any form.
- Effect-pixmap caching unless measured stutter remains after gradient removal.
