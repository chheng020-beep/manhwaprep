# In-Editor Censoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an FB-compliant censoring layer to the typeset editor — a pixelated censor you can auto-apply with one click (NudeNet), toggle for preview, and add/delete by hand, baked into the exported PNG.

**Architecture:** A new `manhwaprep/nsfw.py` owns pixelation + NudeNet detection (no Qt). In `manhwaprep/typeset_editor.py`, a `CensorItem` `QGraphicsItem` paints a live mosaic of the real pixels beneath it and persists per-segment as `seg["_censors"]`, exactly like text boxes persist as `seg["_state"]`. Export renders through the existing `_render`/`_save_render` path, so the mosaic bakes automatically; `_save_render` force-shows all censors first so the preview toggle can never leak uncensored output.

**Tech Stack:** Python, PySide6 (Qt), OpenCV (`cv2`), NumPy, NudeNet.

## Global Constraints

- Run every test with: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest <path> -v`
- Tests must NOT hit the network or download models — inject/monkeypatch a fake detector.
- Censor style is **pixelate/mosaic only** (no blur, no solid bar).
- Auto-detect FB-safe label set exactly: `FEMALE_GENITALIA_EXPOSED`, `MALE_GENITALIA_EXPOSED`, `FEMALE_BREAST_EXPOSED`, `BUTTOCKS_EXPOSED`, `ANUS_EXPOSED`.
- Export **always** bakes every censor regardless of the preview toggle.
- Censor data is plain JSON dicts `{"x","y","w","h","source"}` (`source` ∈ `"auto"|"manual"`); no numpy in the persisted structure.
- Do not modify the Studio pipeline, the manual splitter, or `watermark.py`.
- `CensorItem` z-order is `-0.5` (above art background at `-1`, below text boxes at `0`).

## File Structure

- `manhwaprep/nsfw.py` (**new**) — `pixelate`, `detect`, `ensure_installed`, `LABELS`. Pure functions, no Qt.
- `manhwaprep/typeset_editor.py` (**modify**) — `CensorItem` class; `self.censors` list; persistence in `_commit_items`/`_load_segment`/`_save`/`_load_project`; draw/delete/undo; toolbar buttons; export force-visible in `_save_render`.
- `tests/test_censoring.py` (**new**) — all feature tests.

---

### Task 1: `nsfw.py` — pixelation and detection

**Files:**
- Create: `manhwaprep/nsfw.py`
- Test: `tests/test_censoring.py`

**Interfaces:**
- Consumes: nothing (leaf module). `cv2`, `numpy` available in the EasyScanlate venv.
- Produces:
  - `pixelate(region: np.ndarray, blocks: int = 10) -> np.ndarray` — same shape as input, mosaicked.
  - `LABELS: set[str]` — the FB-safe label set.
  - `detect(bgr: np.ndarray, detector=None, min_score: float = 0.35) -> list[dict]` — boxes `{"x","y","w","h","source":"auto"}`. When `detector` is None, loads the real NudeNet; tests pass a fake.
  - `ensure_installed(parent=None) -> bool` — True if `nudenet` importable (installing it if missing).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_censoring.py` with:

```python
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
import numpy as np
from manhwaprep import nsfw


def test_pixelate_keeps_shape_and_obscures():
    # a smooth horizontal gradient -> pixelation must quantise it into blocks
    region = np.zeros((100, 100, 3), np.uint8)
    region[:, :, 0] = np.linspace(0, 255, 100, dtype=np.uint8)[None, :]
    out = nsfw.pixelate(region, blocks=10)
    assert out.shape == region.shape
    # far fewer distinct columns after mosaicking than the 100-step gradient
    assert len(np.unique(out[50, :, 0])) <= 12
    assert not np.array_equal(out, region)


def test_detect_filters_to_fb_safe_and_maps_boxes():
    class FakeDetector:
        def detect(self, path):
            return [
                {"class": "FEMALE_BREAST_EXPOSED", "score": 0.9, "box": [10, 20, 30, 40]},
                {"class": "FACE_FEMALE", "score": 0.99, "box": [0, 0, 5, 5]},      # not FB-safe
                {"class": "BUTTOCKS_EXPOSED", "score": 0.10, "box": [1, 2, 3, 4]}, # below min_score
            ]
    img = np.zeros((80, 80, 3), np.uint8)
    boxes = nsfw.detect(img, detector=FakeDetector(), min_score=0.35)
    assert boxes == [{"x": 10, "y": 20, "w": 30, "h": 40, "source": "auto"}]


def test_labels_are_the_fb_safe_set():
    assert nsfw.LABELS == {
        "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
        "FEMALE_BREAST_EXPOSED", "BUTTOCKS_EXPOSED", "ANUS_EXPOSED",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_censoring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'manhwaprep.nsfw'`.

- [ ] **Step 3: Implement `manhwaprep/nsfw.py`**

```python
"""NSFW detection + pixelation for the in-editor censoring feature.

Pure image/data helpers (no Qt). Detection uses NudeNet, which is installed on
first use. `detect` accepts an injected detector so tests never touch the model
or the network."""
import os
import sys
import subprocess
import tempfile

import cv2
import numpy as np

# Facebook-restricted parts we auto-censor (NudeNet v3 class names).
LABELS = {
    "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
    "FEMALE_BREAST_EXPOSED", "BUTTOCKS_EXPOSED", "ANUS_EXPOSED",
}

_DETECTOR = None  # lazy NudeDetector singleton


def pixelate(region: np.ndarray, blocks: int = 10) -> np.ndarray:
    """Return a blocky mosaic of `region` (BGR), fully obscuring detail.
    Downscale to at most `blocks`x`blocks` then nearest-neighbour upscale."""
    h, w = region.shape[:2]
    if h < 2 or w < 2:
        return region.copy()
    bw = max(1, min(w, blocks))
    bh = max(1, min(h, blocks))
    small = cv2.resize(region, (bw, bh), interpolation=cv2.INTER_LINEAR)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


def _load_detector():
    global _DETECTOR
    if _DETECTOR is None:
        from nudenet import NudeDetector  # noqa: WPS433 (lazy: heavy import)
        _DETECTOR = NudeDetector()
    return _DETECTOR


def detect(bgr: np.ndarray, detector=None, min_score: float = 0.35) -> list:
    """Detect FB-restricted regions in a BGR image. Returns censor boxes
    [{"x","y","w","h","source":"auto"}, ...]. `detector` is injectable for
    tests; when None the real NudeNet model is loaded lazily. NudeNet reads a
    file path, so a temp PNG is written for the real detector."""
    det = detector if detector is not None else _load_detector()
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        cv2.imwrite(tmp, bgr)
        results = det.detect(tmp)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    boxes = []
    for r in results:
        if r.get("class") in LABELS and r.get("score", 0) >= min_score:
            x, y, w, h = r["box"]
            boxes.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                          "source": "auto"})
    return boxes


def ensure_installed(parent=None) -> bool:
    """True if `nudenet` is importable, installing it into the current venv on
    first use. `parent` is an optional Qt widget for messages (unused here so
    the module stays Qt-free; the caller shows UI)."""
    try:
        import nudenet  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "nudenet"])
    except Exception:
        return False
    try:
        import nudenet  # noqa: F401
        return True
    except ImportError:
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_censoring.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/nsfw.py tests/test_censoring.py
git commit -m "feat: nsfw pixelate + NudeNet detect helpers"
```

---

### Task 2: `CensorItem` + per-segment persistence

**Files:**
- Modify: `manhwaprep/typeset_editor.py` (add `CensorItem` after `ImageItem` ~line 900; init `self.censors`; extend `_commit_items` ~1744, `_load_segment` ~1812/1840, `_save` ~3000, `_load_project` ~3053)
- Test: `tests/test_censoring.py`

**Interfaces:**
- Consumes: `nsfw.pixelate` (Task 1); module-level `_bgr_to_qpixmap(bgr)`; `TextBoxItem._CURSORS`.
- Produces:
  - `CensorItem(x, y, w, h, source="manual", provider=None)` with `.w`, `.h`, `.source`, `.to_dict() -> {"x","y","w","h","source"}`.
  - `self.censors: list[CensorItem]` on the editor.
  - `_make_censor(self, x, y, w, h, source="manual") -> CensorItem` — creates, adds to scene + `self.censors`, returns it.
  - `seg["_censors"]` written by `_commit_items`, read by `_load_segment`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_censoring.py`:

```python
import json, tempfile
import cv2
from PySide6.QtWidgets import QApplication, QMessageBox, QInputDialog

_app = QApplication.instance() or QApplication([])
for _m in ("information", "warning", "critical"):
    setattr(QMessageBox, _m, staticmethod(lambda *a, **k: None))
QMessageBox.question = staticmethod(lambda *a, **k: None)
QInputDialog.getText = staticmethod(lambda *a, **k: ("censor-test", True))


def _make_layout(d, segs=1):
    ts = os.path.join(d, "typeset"); os.makedirs(ts, exist_ok=True)
    seglist = []
    for i in range(segs):
        canvas = np.full((120, 100, 3), 255, np.uint8)
        name = f"canvas_{i + 1:03d}.png"
        cv2.imwrite(os.path.join(ts, name), canvas)
        seglist.append({"image": name, "width": 100, "height": 120, "items": []})
    layout = {"chapter": "t", "lang": "en", "segments": seglist}
    p = os.path.join(ts, "layout.json")
    json.dump(layout, open(p, "w", encoding="utf-8"))
    return p


def test_make_censor_adds_item_and_to_dict():
    from manhwaprep.typeset_editor import TypesetEditor, CensorItem
    with tempfile.TemporaryDirectory() as d:
        ed = TypesetEditor(_make_layout(d))
        c = ed._make_censor(10, 20, 30, 40, "manual")
        assert isinstance(c, CensorItem)
        assert c in ed.censors and c in ed.scene.items()
        assert c.to_dict() == {"x": 10, "y": 20, "w": 30, "h": 40, "source": "manual"}


def test_censor_survives_segment_roundtrip():
    from manhwaprep.typeset_editor import TypesetEditor
    with tempfile.TemporaryDirectory() as d:
        ed = TypesetEditor(_make_layout(d))
        ed._make_censor(5, 6, 20, 25, "auto")
        ed._commit_items()
        assert ed.segments[0]["_censors"] == [
            {"x": 5, "y": 6, "w": 20, "h": 25, "source": "auto"}]
        ed._load_segment(0)                      # rebuild from seg["_censors"]
        assert len(ed.censors) == 1
        assert ed.censors[0].to_dict() == {"x": 5, "y": 6, "w": 20, "h": 25, "source": "auto"}


def test_censor_persists_across_project_save_load():
    from manhwaprep.typeset_editor import TypesetEditor
    with tempfile.TemporaryDirectory() as d:
        p = _make_layout(d)
        ed = TypesetEditor(p)
        ed._make_censor(7, 8, 15, 16, "manual")
        assert ed._save() is True
        proj = json.load(open(os.path.join(os.path.dirname(p), "typeset_project.json")))
        assert proj["segments"][0]["censors"] == [
            {"x": 7, "y": 8, "w": 15, "h": 16, "source": "manual"}]
        ed2 = TypesetEditor(p, resume=True)
        assert ed2.segments[0]["_censors"] == [
            {"x": 7, "y": 8, "w": 15, "h": 16, "source": "manual"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_censoring.py -v`
Expected: FAIL — `ImportError: cannot import name 'CensorItem'`.

- [ ] **Step 3a: Import nsfw at the top of `typeset_editor.py`**

Find the existing relative imports near the top of `manhwaprep/typeset_editor.py` (the block with `from . import watermark` — search for `from . import`). Add alongside it:

```python
from . import nsfw
```

If no `from . import ...` line exists, add `from . import nsfw` immediately after the block of `from PySide6...` imports.

- [ ] **Step 3b: Add the `CensorItem` class** immediately after the end of `class ImageItem` (right before `class _CanvasView` at ~line 902)

```python
class CensorItem(QGraphicsItem):
    """A pixelated censor over a region of the art. Paints a live mosaic of the
    real pixels beneath it (read from the editor's working raster via
    `provider`), so what you see on screen is exactly what bakes into export.
    Movable + freely resizable like ImageItem; a dashed magenta border and
    z=-0.5 mark it as a censor sitting above the art but below the text."""

    HANDLE = 11
    EDGE_GRAB = 9.0
    _CURSORS = TextBoxItem._CURSORS

    def __init__(self, x, y, w, h, source="manual", provider=None):
        super().__init__()
        self.w = float(max(1, w))
        self.h = float(max(1, h))
        self.source = source
        self._provider = provider  # () -> current BGR np array, or None
        self.setFlags(
            QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
        )
        self.setAcceptHoverEvents(True)
        self.setPos(x, y)
        self.setZValue(-0.5)
        self._resize = None
        self._start = None

    def boundingRect(self) -> QRectF:
        m = self.HANDLE
        return QRectF(-m, -m, self.w + 2 * m, self.h + 2 * m)

    def _handles(self) -> dict:
        w, h, s = self.w, self.h, self.HANDLE
        pts = {
            "tl": (0, 0), "tr": (w, 0), "bl": (0, h), "br": (w, h),
            "t": (w / 2, 0), "b": (w / 2, h), "l": (0, h / 2), "r": (w, h / 2),
        }
        return {k: QRectF(px - s / 2, py - s / 2, s, s) for k, (px, py) in pts.items()}

    def _handle_at(self, pos):
        hs = self._handles()
        for k in ("tl", "tr", "bl", "br"):
            if hs[k].contains(pos):
                return k
        x, y, w, h, e = pos.x(), pos.y(), self.w, self.h, self.EDGE_GRAB
        if -e <= y <= h + e:
            if abs(x) <= e:
                return "l"
            if abs(x - w) <= e:
                return "r"
        if -e <= x <= w + e:
            if abs(y) <= e:
                return "t"
            if abs(y - h) <= e:
                return "b"
        return None

    def _mosaic_pixmap(self):
        arr = self._provider() if self._provider else None
        if arr is None:
            return None, 0, 0
        H, W = arr.shape[:2]
        x0 = max(0, int(self.x())); y0 = max(0, int(self.y()))
        x1 = min(W, int(self.x() + self.w)); y1 = min(H, int(self.y() + self.h))
        if x1 <= x0 or y1 <= y0:
            return None, 0, 0
        mos = nsfw.pixelate(arr[y0:y1, x0:x1])
        off_x = x0 - self.x()  # where the clamped region sits inside our rect
        off_y = y0 - self.y()
        return _bgr_to_qpixmap(mos), off_x, off_y

    def paint(self, p, opt, widget=None):
        pm, off_x, off_y = self._mosaic_pixmap()
        if pm is not None:
            p.drawPixmap(QRectF(off_x, off_y, pm.width(), pm.height()),
                         pm, QRectF(pm.rect()))
        else:
            p.fillRect(QRectF(0, 0, self.w, self.h), QColor(40, 40, 40))
        pen = QPen(QColor(230, 0, 200))
        pen.setStyle(Qt.DashLine)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(0, 0, self.w, self.h))
        if self.isSelected():
            p.setBrush(QColor(255, 255, 255))
            p.setPen(QPen(QColor(230, 0, 200)))
            for hr in self._handles().values():
                p.drawRect(hr)

    def hoverMoveEvent(self, e):
        k = self._handle_at(e.pos()) if self.isSelected() else None
        self.setCursor(self._CURSORS.get(k, Qt.OpenHandCursor))
        super().hoverMoveEvent(e)

    def mousePressEvent(self, e):
        k = self._handle_at(e.pos()) if self.isSelected() else None
        if k:
            self._resize = k
            self._start = (self.w, self.h, self.x(), self.y(), 0.0, e.scenePos())
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if not self._resize:
            super().mouseMoveEvent(e)
            return
        w0, h0, x0, y0, _, sp0 = self._start
        d = e.scenePos() - sp0
        dx, dy = d.x(), d.y()
        k = self._resize
        MIN = 8.0
        neww, newh, newx, newy = w0, h0, x0, y0
        if k in ("r", "tr", "br"):
            neww, newx = max(MIN, w0 + dx), x0
        elif k in ("l", "tl", "bl"):
            neww = max(MIN, w0 - dx)
            newx = x0 + (w0 - neww)
        if k in ("b", "bl", "br"):
            newh, newy = max(MIN, h0 + dy), y0
        elif k in ("t", "tl", "tr"):
            newh = max(MIN, h0 - dy)
            newy = y0 + (h0 - newh)
        self.prepareGeometryChange()
        self.w, self.h = neww, newh
        self.setPos(newx, newy)
        self.update()
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._resize:
            self._resize = None
            e.accept()
        else:
            super().mouseReleaseEvent(e)

    def to_dict(self):
        return {"x": int(self.x()), "y": int(self.y()),
                "w": int(self.w), "h": int(self.h), "source": self.source}
```

- [ ] **Step 3c: Initialise `self.censors`** — in `TypesetEditor.__init__`, find `self._history = []` (~line 1315) and add right after it:

```python
        self.censors = []      # CensorItem list for the current segment
        self._censor_visible = True
```

- [ ] **Step 3d: Add `_make_censor`** — insert this method just above `_delete_selected` (~line 2168):

```python
    def _censor_provider(self):
        return self._work_np

    def _make_censor(self, x, y, w, h, source="manual"):
        """Create a CensorItem, add it to the scene + self.censors, return it."""
        c = CensorItem(x, y, w, h, source, provider=self._censor_provider)
        c.setVisible(self._censor_visible)
        self.scene.addItem(c)
        self.censors.append(c)
        return c
```

- [ ] **Step 3e: Write censors in `_commit_items`** — in `_commit_items` (~line 1744), inside the `if self.segments:` block, after the `seg["_state"] = (...)` assignment and before the `_work_np` handling, add:

```python
            seg["_censors"] = [c.to_dict() for c in self.censors]
```

- [ ] **Step 3f: Rebuild censors in `_load_segment`** — in `_load_segment` (~line 1812), find where `self.items = []` / `self.images = []` are reset (just after `self.scene.clear()` ~line 1818) and add:

```python
        self.censors = []
```

Then find the end of the state-rebuild block (after the `else:` loop that builds `TextBoxItem`s from `seg["items"]`, just before `self.seg_lbl.setText(...)` ~line 1850) and add:

```python
        for cd in seg.get("_censors", []):
            self._make_censor(cd["x"], cd["y"], cd["w"], cd["h"],
                              cd.get("source", "manual"))
```

- [ ] **Step 3g: Persist censors in `_save`** — in `_save` (~line 3000), find the per-segment entry build:

```python
            entry = {"image": s["image"], "state": s.get("_state", [])}
```

and change it to:

```python
            entry = {"image": s["image"], "state": s.get("_state", []),
                     "censors": s.get("_censors", [])}
```

- [ ] **Step 3h: Restore censors in `_load_project`** — in `_load_project` (~line 3053), inside the `for seg in self.segments:` loop, after the `if sp.get("state"): seg["_state"] = sp["state"]` line, add:

```python
            if sp.get("censors"):
                seg["_censors"] = sp["censors"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_censoring.py -v`
Expected: PASS (6 passed). Also run the existing editor suite to confirm no regressions:
`QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_studio_gates.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/typeset_editor.py tests/test_censoring.py
git commit -m "feat: CensorItem + per-segment/project censor persistence"
```

---

### Task 3: Draw, delete, and undo censors + toolbar tool

**Files:**
- Modify: `manhwaprep/typeset_editor.py` (`_CanvasView` mouse handlers ~938-1014; `_select_tool` ~2304; `_delete_selected` ~2168; history `_snap_state`/`_sig`/`_reset_history`/`_record`/`_apply_snapshot` ~2498-2543; `_rebuild_from_state`; toolbar `_build_panel` ~1464-1476)
- Test: `tests/test_censoring.py`

**Interfaces:**
- Consumes: `_make_censor` (Task 2), `CensorItem`, `_record_if_changed`.
- Produces:
  - `_add_censor(self, x0, y0, x1, y1)` — normalise a drag rect to a censor (ignores boxes < 8px), records undo.
  - `_delete_selected` also removes `CensorItem`s from `self.censors`.
  - Undo snapshots carry censors; `_apply_snapshot` rebuilds them.
  - A `"censor"` tool in the tool group; `_CanvasView` draws a magenta rubber-band and calls `_add_censor` on release.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_censoring.py`:

```python
def test_add_censor_from_drag_and_ignore_tiny():
    from manhwaprep.typeset_editor import TypesetEditor
    with tempfile.TemporaryDirectory() as d:
        ed = TypesetEditor(_make_layout(d))
        ed._add_censor(10, 10, 60, 50)      # 50x40 -> kept
        ed._add_censor(10, 10, 13, 13)      # 3x3 -> ignored
        assert len(ed.censors) == 1
        assert ed.censors[0].to_dict() == {"x": 10, "y": 10, "w": 50, "h": 40, "source": "manual"}


def test_delete_selected_removes_censor():
    from manhwaprep.typeset_editor import TypesetEditor
    with tempfile.TemporaryDirectory() as d:
        ed = TypesetEditor(_make_layout(d))
        c = ed._make_censor(10, 10, 30, 30, "manual")
        c.setSelected(True)
        ed._delete_selected()
        assert ed.censors == [] and c not in ed.scene.items()


def test_undo_redo_censor_add_and_delete():
    from manhwaprep.typeset_editor import TypesetEditor
    with tempfile.TemporaryDirectory() as d:
        ed = TypesetEditor(_make_layout(d))
        ed._add_censor(10, 10, 50, 50)
        assert len(ed.censors) == 1
        ed._undo()
        assert len(ed.censors) == 0
        ed._redo()
        assert len(ed.censors) == 1
        # now delete + undo restores it
        ed.censors[0].setSelected(True)
        ed._delete_selected()
        assert len(ed.censors) == 0
        ed._undo()
        assert len(ed.censors) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_censoring.py -v`
Expected: FAIL — `AttributeError: 'TypesetEditor' object has no attribute '_add_censor'`.

- [ ] **Step 3a: Add `_add_censor`** — insert right after `_make_censor` (added in Task 2):

```python
    def _add_censor(self, x0, y0, x1, y1):
        """Turn a drag rectangle into a manual censor; ignore tiny boxes."""
        x, y = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w < 8 or h < 8:
            return
        self._make_censor(int(x), int(y), int(w), int(h), "manual")
        self._record_if_changed()
```

- [ ] **Step 3b: Delete censors in `_delete_selected`** — in `_delete_selected` (~line 2168), add a `self.censors` removal alongside the existing item/image removals:

```python
    def _delete_selected(self):
        for it in list(self.scene.selectedItems()):
            self.scene.removeItem(it)
            if it in self.items:
                self.items.remove(it)
            if it in self.images:
                self.images.remove(it)
            if it in self.censors:
                self.censors.remove(it)
        self._record_if_changed()
```

- [ ] **Step 3c: Censors in undo snapshots.** In `_snap_state` (~line 2498), the method currently returns text+image dicts. Leave it as-is and instead carry censors in the history dict. Change `_reset_history` (~line 2520) and `_record` (~line 2526) to store a `"censors"` list, and `_apply_snapshot` (~line 2539) to rebuild them.

Replace `_reset_history`:

```python
    def _reset_history(self):
        self._history = [{"state": self._snap_state(), "work": self._work_np,
                          "censors": [c.to_dict() for c in self.censors],
                          "sig": self._sig()}]
        self._hist_idx = 0
        self._update_undo_buttons()
```

Replace `_record`:

```python
    def _record(self):
        self._history = self._history[: self._hist_idx + 1]
        self._history.append({"state": self._snap_state(), "work": self._work_np,
                              "censors": [c.to_dict() for c in self.censors],
                              "sig": self._sig()})
        if len(self._history) > 40:
            self._history.pop(0)
        self._hist_idx = len(self._history) - 1
        self._update_undo_buttons()
```

Add a censor-rebuild helper and call it from `_apply_snapshot`. Replace `_apply_snapshot`:

```python
    def _apply_snapshot(self, snap):
        self._work_np = snap["work"]
        self._bg_pixmap = _bgr_to_qpixmap(self._work_np)
        self._bg_item.setPixmap(self._bg_pixmap)
        self._rebuild_from_state(snap["state"])
        self._rebuild_censors(snap.get("censors", []))

    def _rebuild_censors(self, cdicts):
        for c in list(self.censors):
            self.scene.removeItem(c)
        self.censors = []
        for cd in cdicts:
            self._make_censor(cd["x"], cd["y"], cd["w"], cd["h"],
                              cd.get("source", "manual"))
```

- [ ] **Step 3d: Include censors in the undo signature `_sig`** — in `_sig` (~line 2507), before the final `return (tuple(parts), id(self._work_np))`, add censor geometry so add/delete/move are detected as changes:

```python
        for c in self.censors:
            parts.append(("c", round(c.x()), round(c.y()), round(c.w),
                          round(c.h), c.source))
```

- [ ] **Step 3e: Add the "Cen" tool button** — in `_build_panel` (~line 1472), after the `"Box"`/`"boxremove"` tool button `bar.addWidget(...)` and before `bar.addStretch(1)`, add:

```python
        bar.addWidget(self._tool_button(
            "Cen", "censor",
            "Censor tool — drag a box over 18+ content to pixelate it"))
```

- [ ] **Step 3f: Lock censors while drawing/painting** — in `_select_tool` (~line 2304), change the flag-toggle loop to include censors so a draw-drag never grabs an existing censor, but they stay editable in select mode:

```python
        for it in self.items + self.images + self.censors:
            it.setFlag(QGraphicsItem.ItemIsSelectable, not painting)
            it.setFlag(QGraphicsItem.ItemIsMovable, not painting)
```

- [ ] **Step 3g: Route the censor drag in `_CanvasView`.** The view already rubber-bands for `boxremove` via `_box0`/`_box1`. Extend three handlers so the `"censor"` tool draws the same band but commits to `_add_censor`.

In `_CanvasView.mousePressEvent` (~line 938), change the box-start condition:

```python
            if eff == "boxremove" or self.tool == "censor":
                self._box0 = self._box1 = self.mapToScene(e.position().toPoint())
                self.viewport().update()
                e.accept()
                return
```

In `_CanvasView.drawForeground` (~line 978), the existing red band block draws for any active box drag, which already covers censor. To make the censor band magenta, replace the red-band block:

```python
        if self._box0 is not None and self._box1 is not None:
            censoring = self.tool == "censor"
            col = QColor(230, 0, 200) if censoring else QColor(255, 0, 0)
            pen = QPen(col); pen.setCosmetic(True)
            pen.setStyle(Qt.DashLine); p.setPen(pen)
            p.setBrush(QColor(col.red(), col.green(), col.blue(), 40))
            p.drawRect(QRectF(self._box0, self._box1).normalized())
            return
```

In `_CanvasView.mouseReleaseEvent` (~line 997), the box-release currently always calls `_box_remove`. Branch on the tool:

```python
        if self._box0 is not None:
            a, b = self._box0, self._box1 or self._box0
            self._box0 = self._box1 = None
            self.viewport().update()
            if self.editor:
                if self.tool == "censor":
                    self.editor._add_censor(a.x(), a.y(), b.x(), b.y())
                else:
                    self.editor._box_remove(a.x(), a.y(), b.x(), b.y())
            e.accept()
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_censoring.py tests/test_studio_gates.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/typeset_editor.py tests/test_censoring.py
git commit -m "feat: draw/delete/undo censors + Cen tool"
```

---

### Task 4: One-click auto-censor + preview toggle

**Files:**
- Modify: `manhwaprep/typeset_editor.py` (`_build_panel` ~1476 — add action buttons; add `_auto_censor` and `_toggle_censor_layer` methods)
- Test: `tests/test_censoring.py`

**Interfaces:**
- Consumes: `nsfw.ensure_installed`, `nsfw.detect` (Task 1); `_make_censor`, `_record_if_changed` (Task 2/3); `self._censor_visible`, `self.censors`.
- Produces:
  - `_auto_censor(self)` — ensures NudeNet, detects on `self._work_np`, adds `source="auto"` censors.
  - `_toggle_censor_layer(self, checked)` — sets `self._censor_visible` and each censor item's preview visibility.
  - Two buttons in the panel: "Censor 18+" (auto) and a checkable "Censor" (toggle, checked by default).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_censoring.py`:

```python
def test_auto_censor_adds_detected_boxes(monkeypatch):
    from manhwaprep.typeset_editor import TypesetEditor
    from manhwaprep import nsfw
    with tempfile.TemporaryDirectory() as d:
        ed = TypesetEditor(_make_layout(d))
        monkeypatch.setattr(nsfw, "ensure_installed", lambda parent=None: True)
        monkeypatch.setattr(nsfw, "detect", lambda bgr, **k: [
            {"x": 10, "y": 12, "w": 20, "h": 22, "source": "auto"}])
        ed._auto_censor()
        assert len(ed.censors) == 1
        assert ed.censors[0].source == "auto"
        assert ed.censors[0].to_dict() == {"x": 10, "y": 12, "w": 20, "h": 22, "source": "auto"}


def test_auto_censor_noop_when_detector_unavailable(monkeypatch):
    from manhwaprep.typeset_editor import TypesetEditor
    from manhwaprep import nsfw
    with tempfile.TemporaryDirectory() as d:
        ed = TypesetEditor(_make_layout(d))
        monkeypatch.setattr(nsfw, "ensure_installed", lambda parent=None: False)
        ed._auto_censor()
        assert ed.censors == []


def test_toggle_hides_preview_only(monkeypatch):
    from manhwaprep.typeset_editor import TypesetEditor
    with tempfile.TemporaryDirectory() as d:
        ed = TypesetEditor(_make_layout(d))
        c = ed._make_censor(10, 10, 30, 30, "manual")
        ed._toggle_censor_layer(False)
        assert ed._censor_visible is False and c.isVisible() is False
        ed._toggle_censor_layer(True)
        assert ed._censor_visible is True and c.isVisible() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_censoring.py -v`
Expected: FAIL — `AttributeError: ... '_auto_censor'`.

- [ ] **Step 3a: Add the action methods** — insert after `_add_censor`:

```python
    def _auto_censor(self):
        """One-click: detect FB-restricted parts on the current canvas and drop
        a pixelated censor on each. Manual add/delete still works if the
        detector is unavailable."""
        if self._work_np is None:
            return
        if not nsfw.ensure_installed(self):
            QMessageBox.warning(
                self, "Censor 18+",
                "The NudeNet detector isn't available (install failed or no "
                "internet). You can still draw censors by hand with the Cen tool.")
            return
        try:
            boxes = nsfw.detect(self._work_np)
        except Exception as e:
            QMessageBox.warning(self, "Censor 18+", f"Detection failed:\n{e}")
            return
        if not boxes:
            QMessageBox.information(
                self, "Censor 18+",
                "No adult regions detected on this canvas — add any by hand "
                "with the Cen tool if needed.")
            return
        for b in boxes:
            self._make_censor(b["x"], b["y"], b["w"], b["h"], "auto")
        self._record_if_changed()

    def _toggle_censor_layer(self, checked):
        """Show/hide the censor layer in the EDITOR PREVIEW only. Export always
        bakes every censor regardless of this toggle."""
        self._censor_visible = bool(checked)
        for c in self.censors:
            c.setVisible(self._censor_visible)
```

- [ ] **Step 3b: Add the two buttons** — in `_build_panel`, right after `col.addLayout(bar)` and the `self._tool_buttons["select"].setChecked(True)` line (~line 1478), add a censor action row:

```python
        crow = QHBoxLayout()
        crow.setSpacing(4)
        self.auto_censor_btn = QPushButton("Censor 18+")
        self.auto_censor_btn.setToolTip(
            "One-click: pixelate all detected adult parts on this canvas")
        self.auto_censor_btn.clicked.connect(self._auto_censor)
        self.censor_toggle_btn = QPushButton("Censor")
        self.censor_toggle_btn.setCheckable(True)
        self.censor_toggle_btn.setChecked(True)
        self.censor_toggle_btn.setToolTip(
            "Show/hide censors in the preview (export always censors)")
        self.censor_toggle_btn.toggled.connect(self._toggle_censor_layer)
        crow.addWidget(self.auto_censor_btn, 1)
        crow.addWidget(self.censor_toggle_btn)
        col.addLayout(crow)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_censoring.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add manhwaprep/typeset_editor.py tests/test_censoring.py
git commit -m "feat: one-click auto-censor + preview toggle"
```

---

### Task 5: Export always bakes censors (FB-safe)

**Files:**
- Modify: `manhwaprep/typeset_editor.py` (`_save_render` ~2762)
- Test: `tests/test_censoring.py`

**Interfaces:**
- Consumes: `self.censors`, `_render` (unchanged), `render_translated` (unchanged, calls `_save_render`).
- Produces: `_save_render` forces all censors visible for the render, then restores their preview visibility — so export output is always censored even with the toggle off.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_censoring.py`. The canvas is a horizontal gradient so pixelation is visibly detectable, and the preview toggle is OFF to prove export ignores it:

```python
def test_export_bakes_censor_even_with_preview_off():
    from manhwaprep.typeset_editor import TypesetEditor
    with tempfile.TemporaryDirectory() as d:
        ts = os.path.join(d, "typeset"); os.makedirs(ts, exist_ok=True)
        grad = np.zeros((120, 100, 3), np.uint8)
        grad[:, :, 1] = np.linspace(0, 255, 100, dtype=np.uint8)[None, :]  # G ramp L->R
        cv2.imwrite(os.path.join(ts, "canvas_001.png"), grad)
        layout = {"chapter": "t", "lang": "en", "segments": [
            {"image": "canvas_001.png", "width": 100, "height": 120, "items": []}]}
        p = os.path.join(ts, "layout.json")
        json.dump(layout, open(p, "w", encoding="utf-8"))

        ed = TypesetEditor(p)
        ed._make_censor(20, 30, 50, 50, "manual")   # covers x20..70, y30..80
        ed._toggle_censor_layer(False)              # preview OFF -> export must still bake
        out = os.path.join(d, "rendered")
        paths = ed.render_translated(out)
        baked = cv2.imread(paths[0])

        # inside the censor the smooth gradient is quantised into <=12 columns
        region = baked[35:75, 25:65]
        assert len(np.unique(region[:, :, 1])) <= 12
        # a strip far outside the censor keeps the fine gradient (many values)
        outside = baked[100:115, 5:95]
        assert len(np.unique(outside[:, :, 1])) > 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_censoring.py::test_export_bakes_censor_even_with_preview_off -v`
Expected: FAIL — the region still shows the full gradient (many unique values) because the hidden censor didn't render.

- [ ] **Step 3: Force censors visible during render** — replace `_save_render` (~line 2762):

```python
    def _save_render(self, seg, out: str, watermarked: bool):
        """Render a canvas to disk, optionally stamping the corner logo. All
        censors are forced visible for the render so export ALWAYS bakes them,
        regardless of the editor's preview toggle; prior visibility is restored
        afterward."""
        prev_vis = [(c, c.isVisible()) for c in self.censors]
        for c in self.censors:
            c.setVisible(True)
        try:
            img = self._render(seg)
        finally:
            for c, v in prev_vis:
                c.setVisible(v)
        if watermarked:
            from PIL import Image as PILImage
            from . import watermark
            rgb = np.ascontiguousarray(self._qimage_to_bgr(img)[:, :, ::-1])
            watermark.stamp(PILImage.fromarray(rgb)).save(out)
        else:
            img.save(out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_censoring.py -v`
Expected: PASS (all).

- [ ] **Step 5: Full regression + commit**

Run the whole affected suite:
`QT_QPA_PLATFORM=offscreen /Users/leapheakuoch/EasyScanlate/.venv/bin/python3 -m pytest tests/test_censoring.py tests/test_studio_gates.py tests/test_studio.py -v`
Expected: PASS (all).

```bash
git add manhwaprep/typeset_editor.py tests/test_censoring.py
git commit -m "feat: export always bakes censors regardless of preview toggle"
```

---

## Self-Review

**Spec coverage:**
- FB-safe label set / NudeNet detect → Task 1 (`detect`, `LABELS`). ✓
- Pixelate/mosaic style → Task 1 (`pixelate`), used by `CensorItem` + export. ✓
- Auto-install on first use → Task 1 (`ensure_installed`), wired in Task 4. ✓
- Censor data model per-segment + project persistence → Task 2. ✓
- CensorItem live mosaic, z=-0.5, magenta border → Task 2. ✓
- One-click button → Task 4 (`_auto_censor`). ✓
- Toggle (preview only) → Task 4 (`_toggle_censor_layer`). ✓
- Add (Cen tool drag) → Task 3. ✓
- Delete (select + Delete) → Task 3 (`_delete_selected`). ✓
- Undo/redo → Task 3 (history snapshots). ✓
- Export always bakes regardless of toggle → Task 5 (`_save_render`). ✓
- Error handling (detector unavailable, no boxes, off-canvas clamp) → Task 4 messages, Task 2 `_mosaic_pixmap` clamp. ✓

**Placeholder scan:** No TBD/TODO; every code step has full code. ✓

**Type consistency:** `_make_censor(x,y,w,h,source)` used identically in Tasks 2/3/4; censor dict keys `{"x","y","w","h","source"}` consistent across `to_dict`, persistence, detect, and tests; `provider` is `self._censor_provider` (a method, stable identity) everywhere. ✓

## Notes for the implementer

- `_bgr_to_qpixmap` is an existing module-level function in `typeset_editor.py` — do not redefine it.
- `CensorItem` must be defined at module level (after `ImageItem`), so `_bgr_to_qpixmap` and `nsfw` are in scope.
- Do not add `"censor"` to `BRUSH_TOOLS` — it is a rubber-band draw tool, not a raster brush. The `brush_group.setVisible(...)` check in `_select_tool` already excludes it (it only lists blend/erase/paint/remove), so the brush panel stays hidden in censor mode.
- FB-compliance reminder to surface to the user in the final report: censoring the FB-safe parts sharply reduces risk, but Facebook can still action an overtly sexual composition even when parts are covered — censor **and** skip the most explicit panels.
