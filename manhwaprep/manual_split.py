"""Manual image splitter — click to place horizontal cut lines, split into numbered JPGs."""
from __future__ import annotations

import os
import re
import subprocess
import sys

import numpy as np
from PIL import Image
from PySide6.QtCore import (
    QPointF,
    QRectF,
    QSettings,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QTransform,
)

# Stitched chapter strips can exceed PIL's decompression-bomb threshold
# (~178 MP); these are local, user-chosen files.
Image.MAX_IMAGE_PIXELS = None

# Qt cannot create pixmaps taller/wider than 32767 px — long webtoon strips
# exceed that, so anything bigger is displayed downscaled and cut positions
# are mapped back to full resolution when splitting.
_MAX_DISPLAY_PX = 30000
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")

_YELLOW = QColor("#FFD700")
_ORANGE = QColor("#FF8C00")
_GREEN = QColor("#27ae60")   # cut sits in a blank gutter — safe
_RED = QColor("#e74c3c")     # cut slices through artwork
_BAND = 10   # semi-transparent highlight band px each side


def _natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


class _GhostLine(QGraphicsItem):
    """Preview line that follows the cursor before a click places a real cut."""

    def __init__(self, img_w: float, scene_ref):
        super().__init__()
        self._img_w = img_w
        self._scene_ref = scene_ref
        self._quiet: bool | None = None
        self._label = ""
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setAcceptHoverEvents(False)
        self.setZValue(5)

    def boundingRect(self) -> QRectF:
        return QRectF(0, -18, self._img_w, 36)

    def paint(self, painter: QPainter, option, widget=None):
        if self._quiet is None:
            color = QColor(_YELLOW)
        else:
            color = QColor(_GREEN if self._quiet else _RED)
        color.setAlpha(180)
        pen = QPen(color, 2, Qt.DashLine)
        pen.setDashPattern([6, 6])
        painter.setPen(pen)
        painter.drawLine(QPointF(0, 0), QPointF(self._img_w, 0))

        if self._label:
            font = QFont()
            font.setPointSize(9)
            painter.setFont(font)
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(self._label)
            th = fm.height()
            pad = 3
            box = QRectF(self._img_w - tw - pad * 2 - 6, -(th // 2) - pad,
                         tw + pad * 2, th + pad * 2)
            painter.setBrush(QBrush(QColor(30, 30, 30, 190)))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(box, 3, 3)
            painter.setPen(QPen(color))
            painter.drawText(box, Qt.AlignCenter, self._label)


class CutLineItem(QGraphicsItem):
    """A draggable horizontal cut line that spans the image width."""

    def __init__(self, y: float, img_w: float, img_h: float, scene_ref):
        super().__init__()
        self._y = 0.0  # local y always 0; item is positioned via setPos
        self._img_w = img_w
        self._img_h = img_h
        self._scene_ref = scene_ref   # ManualSplitScene
        self._hovered = False
        self._number = 1

        # Set flags BEFORE setPos so itemChange fires and clamps on creation
        self.setFlags(
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemSendsScenePositionChanges
        )
        y = max(1.0, min(img_h - 1, y))
        self.setPos(0, y)
        self.setAcceptHoverEvents(True)
        self.setCursor(QCursor(Qt.SizeVerCursor))
        self.setZValue(10)

    # -- geometry ----------------------------------------------------------
    def boundingRect(self) -> QRectF:
        return QRectF(0, -(_BAND + 2), self._img_w, (_BAND + 2) * 2)

    def paint(self, painter: QPainter, option, widget=None):
        quiet = self._scene_ref.is_quiet(self.pos().y())
        if quiet is None:
            color = _ORANGE if self._hovered else _YELLOW
        else:
            color = _GREEN if quiet else _RED
            if self._hovered:
                color = color.lighter(125)

        active = self._hovered or self.isSelected()

        # semi-transparent band
        band_color = QColor(color)
        band_color.setAlpha(85 if active else 50)
        painter.fillRect(QRectF(0, -_BAND, self._img_w, _BAND * 2), band_color)

        # dashed line (solid + thicker when selected so Delete targeting is obvious)
        pen = QPen(color, 4 if self.isSelected() else 3,
                   Qt.SolidLine if self.isSelected() else Qt.DashLine)
        if not self.isSelected():
            pen.setDashPattern([8, 4])
        painter.setPen(pen)
        painter.drawLine(QPointF(0, 0), QPointF(self._img_w, 0))

        # label box
        label = f"— {self._number} —"
        font = QFont()
        font.setBold(True)
        font.setPointSize(10)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(label)
        th = fm.height()
        pad = 4
        box = QRectF(4, -(th // 2) - pad, tw + pad * 2, th + pad * 2)
        painter.setBrush(QBrush(QColor(30, 30, 30, 200)))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QPen(Qt.white))
        painter.drawText(box, Qt.AlignCenter, label)

    # -- interaction -------------------------------------------------------
    def hoverEnterEvent(self, e):
        self._hovered = True
        self.update()
        super().hoverEnterEvent(e)

    def hoverLeaveEvent(self, e):
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(e)

    def mouseDoubleClickEvent(self, e):
        self._scene_ref.remove_line(self)
        super().mouseDoubleClickEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            e.accept()
            # deferred: removing an item from inside its own handler is unsafe
            QTimer.singleShot(0, lambda: self._scene_ref.remove_line(self))
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        self._scene_ref._renumber()  # keep labels ordered while dragging past others
        self._scene_ref.cuts_changed.emit()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and isinstance(value, QPointF):
            new_y = value.y()
            # magnetic snap to nearby blank gutters while dragging with the
            # mouse (Alt = free placement); programmatic moves stay exact.
            if (QApplication.mouseButtons() & Qt.LeftButton
                    and not QApplication.keyboardModifiers() & Qt.AltModifier):
                new_y = self._scene_ref.snap_y(new_y)
            # clamp X=0, clamp Y inside image bounds
            new_y = max(1.0, min(self._img_h - 1, new_y))
            scale = self._scene_ref._display_scale or 1.0
            self.setToolTip(
                f"y: {int(round(new_y / scale))} px  (right-click to delete)")
            return QPointF(0.0, new_y)
        return super().itemChange(change, value)

    def scene_y(self) -> float:
        return self.pos().y()


class ManualSplitScene(QGraphicsScene):
    cuts_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lines: list[CutLineItem] = []
        self._img_w = 0.0
        self._img_h = 0.0
        self._press_pos = None  # scene pos of a press on empty space / the image
        self._ghost: _GhostLine | None = None
        self._row_cost: np.ndarray | None = None  # per-display-row slice cost
        self._quiet_thr = 0.0
        self._display_scale = 1.0
        self._snap_enabled = True

    def reset(self, w: float, h: float):
        """Clear all items AND the tracked line list, then set new image size."""
        self._lines.clear()
        self._ghost = None       # C++ side dies in clear(); drop the wrapper first
        self._row_cost = None
        self.clear()  # QGraphicsScene.clear() — removes all QGraphicsItems
        self._img_w = w
        self._img_h = h
        ghost = _GhostLine(w, self)
        ghost.setVisible(False)
        self.addItem(ghost)
        self._ghost = ghost

    def set_image_size(self, w: float, h: float):
        self._img_w = w
        self._img_h = h

    def set_display_scale(self, s: float):
        self._display_scale = s

    def set_snap_enabled(self, on: bool):
        self._snap_enabled = bool(on)

    def set_analysis(self, cost: np.ndarray | None, thr: float):
        """Per-display-row busyness profile: drives green/red line colour and
        snap-to-gutter. None disables both (falls back to yellow lines)."""
        self._row_cost = cost
        self._quiet_thr = thr

    # -- gutter intelligence ------------------------------------------------
    def is_quiet(self, y: float) -> bool | None:
        """True if row y sits in a blank band, False if in artwork, None if
        no profile is available."""
        if self._row_cost is None or len(self._row_cost) == 0:
            return None
        i = int(max(0, min(len(self._row_cost) - 1, y)))
        return bool(self._row_cost[i] < self._quiet_thr)

    def snap_y(self, y: float) -> float:
        """Magnet a desired row toward the centre of the nearest blank gutter
        within a small radius. Returns y unchanged if snapping is off, no
        profile exists, or no quiet row is nearby."""
        if not self._snap_enabled or self._row_cost is None:
            return y
        c = self._row_cost
        h = len(c)
        if h == 0:
            return y
        r = int(max(16, min(80, h * 0.008)))
        lo = max(0, int(y) - r)
        hi = min(h, int(y) + r + 1)
        if hi <= lo:
            return y
        seg = c[lo:hi]
        quiet = seg < self._quiet_thr
        if not quiet.any():
            return y
        idx = np.arange(lo, hi)
        spread = float(seg.max() - seg.min()) + 1.0
        score = seg + spread * 0.02 * np.abs(idx - y)
        score = np.where(quiet, score, np.inf)
        return float(idx[int(np.argmin(score))])

    # -- ghost preview -------------------------------------------------------
    def _update_ghost(self, y: float):
        if self._ghost is None:
            return
        scale = self._display_scale or 1.0
        self._ghost._quiet = self.is_quiet(y)
        self._ghost._label = f"{int(round(y / scale)):,} px"
        self._ghost.setPos(0, y)
        self._ghost.setVisible(True)
        self._ghost.update()

    def _set_ghost_visible(self, vis: bool):
        if self._ghost is not None:
            self._ghost.setVisible(vis)

    # -- mouse ---------------------------------------------------------------
    def mousePressEvent(self, e):
        # Remember presses on empty space / the image; the line is added on
        # release only if the mouse didn't move (a real click, not a drag).
        self._press_pos = None
        if e.button() == Qt.LeftButton:
            tr = self.views()[0].transform() if self.views() else QTransform()
            hit = self.itemAt(e.scenePos(), tr)
            if hit is None or isinstance(hit, (QGraphicsPixmapItem, _GhostLine)):
                self._press_pos = e.scenePos()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons():
            self._set_ghost_visible(False)
        else:
            pos = e.scenePos()
            tr = self.views()[0].transform() if self.views() else QTransform()
            hit = self.itemAt(pos, tr)
            if (not isinstance(hit, CutLineItem)
                    and 0 < pos.y() < self._img_h
                    and 0 <= pos.x() <= self._img_w):
                self._update_ghost(self.snap_y(pos.y()))
            else:
                self._set_ghost_visible(False)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._press_pos is not None:
            d = e.scenePos() - self._press_pos
            self._press_pos = None
            if abs(d.x()) < 4 and abs(d.y()) < 4:
                y = e.scenePos().y()
                if 0 < y < self._img_h:
                    if not e.modifiers() & Qt.AltModifier:
                        y = self.snap_y(y)
                    self._add_line(y)
                    e.accept()
                    return
        super().mouseReleaseEvent(e)

    # -- line management -------------------------------------------------------
    def _add_line(self, y: float):
        y = max(1.0, min(self._img_h - 1, y))
        line = CutLineItem(y, self._img_w, self._img_h, self)
        self.addItem(line)
        self._lines.append(line)
        self._set_ghost_visible(False)
        self._renumber()
        self.cuts_changed.emit()

    def add_line_at_center(self):
        y = self._img_h / 2
        # offset slightly if a line is already there
        existing = {int(l.scene_y()) for l in self._lines}
        while int(y) in existing:
            y += 20
        self._add_line(y)

    def remove_line(self, line: CutLineItem):
        if line in self._lines:
            self._lines.remove(line)
        self.removeItem(line)
        self._renumber()
        self.cuts_changed.emit()

    def clear_lines(self):
        for line in list(self._lines):
            self.removeItem(line)
        self._lines.clear()
        self.cuts_changed.emit()

    def _renumber(self):
        sorted_lines = sorted(self._lines, key=lambda l: l.scene_y())
        for i, l in enumerate(sorted_lines, 1):
            l._number = i
            l.update()

    def cut_ys(self) -> list[int]:
        return sorted(int(l.scene_y()) for l in self._lines)


class ManualSplitView(QGraphicsView):
    def __init__(self, scene: ManualSplitScene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(QCursor(Qt.CrossCursor))
        # hover moves must reach the scene for the ghost preview line
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._panning = False
        self._pan_start = None
        self._space_held = False

    def wheelEvent(self, e):
        # Plain wheel scrolls (tall strips need it); Ctrl/Cmd+wheel zooms —
        # same convention as the typeset editor.
        if e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            delta = e.angleDelta().y()
            if delta != 0:
                factor = 1.15 if delta > 0 else 1 / 1.15
                current = self.transform().m11()  # actual current scale
                new_scale = max(0.05, min(8.0, current * factor))
                self.scale(new_scale / current, new_scale / current)
            e.accept()
        else:
            super().wheelEvent(e)

    def _selected_lines(self) -> list[CutLineItem]:
        sc = self.scene()
        if not isinstance(sc, ManualSplitScene):
            return []
        return [i for i in sc.selectedItems() if isinstance(i, CutLineItem)]

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Space:
            self._space_held = True
            self.setCursor(QCursor(Qt.OpenHandCursor))
        elif e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            sel = self._selected_lines()
            if sel:
                for it in sel:
                    self.scene().remove_line(it)
                e.accept()
                return
        elif e.key() in (Qt.Key_Up, Qt.Key_Down):
            sel = self._selected_lines()
            if sel:
                step = 10 if e.modifiers() & Qt.ShiftModifier else 1
                dy = -step if e.key() == Qt.Key_Up else step
                for it in sel:
                    it.setPos(0, it.pos().y() + dy)  # itemChange clamps
                sc = self.scene()
                sc._renumber()
                sc.cuts_changed.emit()
                e.accept()
                return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space:
            self._space_held = False
            self.setCursor(QCursor(Qt.CrossCursor))
        super().keyReleaseEvent(e)

    def leaveEvent(self, e):
        sc = self.scene()
        if isinstance(sc, ManualSplitScene):
            sc._set_ghost_visible(False)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton or (self._space_held and e.button() == Qt.LeftButton):
            self._panning = True
            self._pan_start = e.position().toPoint()
            self.setCursor(QCursor(Qt.ClosedHandCursor))
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._panning and self._pan_start is not None:
            delta = e.position().toPoint() - self._pan_start
            self._pan_start = e.position().toPoint()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._panning:
            self._panning = False
            self.setCursor(
                QCursor(Qt.OpenHandCursor if self._space_held else Qt.CrossCursor))
            e.accept()
            return
        super().mouseReleaseEvent(e)


class _DropZone(QFrame):
    dropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setFixedHeight(120)
        self.setStyleSheet(
            "QFrame{border:2px dashed #8a8a8a;border-radius:12px;background:#fafafa;}"
        )
        lay = QVBoxLayout(self)
        lab = QLabel("Drop an image or folder here")
        lab.setAlignment(Qt.AlignCenter)
        lab.setStyleSheet("border:none;color:#666;font-size:15px;")
        lay.addWidget(lab)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if os.path.isdir(p) or (os.path.isfile(p) and p.lower().endswith(IMAGE_EXTS)):
                self.dropped.emit(p)
                return


class ManualSplitWidget(QWidget):
    """Tab widget: drop/open an image, click to place cut lines, split into numbered JPGs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image_path: str | None = None
        self._folder_images: list[str] = []
        self._img_w = 0            # full-resolution image size
        self._img_h = 0
        self._display_scale = 1.0  # display px = image px * scale (≤ 1.0)
        self._cuts_by_path: dict[str, list[int]] = {}  # full-res cut ys per image
        self._shared_cuts: list[int] = []  # cuts shown on every image in same-cuts mode
        self._settings = QSettings("ManhwaPrep", "ManhwaPrep")

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # drop zone
        self._drop = _DropZone()
        self._drop.dropped.connect(self._on_drop)
        root.addWidget(self._drop)

        browse_row = QHBoxLayout()
        browse_img = QPushButton("Open image…")
        browse_img.clicked.connect(self._browse_image)
        browse_folder = QPushButton("Open folder…")
        browse_folder.clicked.connect(self._browse_folder)
        browse_row.addWidget(browse_img)
        browse_row.addWidget(browse_folder)
        browse_row.addStretch(1)
        root.addLayout(browse_row)

        # folder combo (hidden unless folder loaded)
        self._folder_bar = QWidget()
        fb_lay = QHBoxLayout(self._folder_bar)
        fb_lay.setContentsMargins(0, 0, 0, 0)
        fb_lay.addWidget(QLabel("Image:"))
        self._img_combo = QComboBox()
        self._img_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._img_combo.currentIndexChanged.connect(self._on_combo_changed)
        fb_lay.addWidget(self._img_combo)
        self._same_cuts_chk = QCheckBox("Same cuts for all images")
        self._same_cuts_chk.setChecked(True)
        self._same_cuts_chk.toggled.connect(self._on_same_cuts_toggled)
        fb_lay.addWidget(self._same_cuts_chk)
        self._folder_bar.setVisible(False)
        root.addWidget(self._folder_bar)

        # scene + view
        self._scene = ManualSplitScene(self)
        self._view = ManualSplitView(self._scene)
        self._view.setMinimumHeight(300)
        self._view.setVisible(False)
        self._scene.cuts_changed.connect(self._on_cuts_changed)
        root.addWidget(self._view, 1)

        hint = QLabel(
            "Click to add a cut (snaps to blank gaps — hold Alt to place freely) · "
            "drag to move · right-click or double-click to delete\n"
            "Green line = safe gap, red = cutting through art · "
            "Delete key removes selected · ↑/↓ nudge (Shift = 10 px)"
        )
        hint.setStyleSheet("color:#888;font-size:11px;")
        hint.setAlignment(Qt.AlignCenter)
        hint.setVisible(False)
        self._hint = hint
        root.addWidget(hint)

        # controls row
        ctrl = QHBoxLayout()
        self._add_btn = QPushButton("+ Add cut line")
        self._add_btn.clicked.connect(self._add_line_at_view_center)
        self._add_btn.setEnabled(False)
        self._clear_btn = QPushButton("Clear all")
        self._clear_btn.clicked.connect(self._scene.clear_lines)
        self._clear_btn.setEnabled(False)
        ctrl.addWidget(self._add_btn)
        ctrl.addWidget(self._clear_btn)
        self._snap_chk = QCheckBox("Snap to gaps")
        self._snap_chk.setChecked(True)
        self._snap_chk.toggled.connect(self._scene.set_snap_enabled)
        ctrl.addWidget(self._snap_chk)
        ctrl.addSpacing(16)
        ctrl.addWidget(QLabel("Auto:"))
        self._auto_spin = QSpinBox()
        self._auto_spin.setRange(2, 30)
        self._auto_spin.setValue(5)
        self._auto_spin.setSuffix(" parts")
        ctrl.addWidget(self._auto_spin)
        self._auto_btn = QPushButton("Auto place cuts")
        self._auto_btn.clicked.connect(self._auto_cuts)
        self._auto_btn.setEnabled(False)
        ctrl.addWidget(self._auto_btn)
        ctrl.addStretch(1)
        root.addLayout(ctrl)

        # live part-height readout
        self._parts_lab = QLabel("")
        self._parts_lab.setWordWrap(True)
        self._parts_lab.setStyleSheet("color:#666;font-size:11px;")
        root.addWidget(self._parts_lab)

        # output row
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output:"))
        default_out = self._settings.value(
            "manual_split/out_dir",
            os.path.expanduser("~/Desktop/ManhwaPrep/splits"))
        self._out_edit = QLineEdit(default_out)
        out_row.addWidget(self._out_edit, 1)
        out_browse = QPushButton("…")
        out_browse.setFixedWidth(32)
        out_browse.clicked.connect(self._browse_out)
        out_row.addWidget(out_browse)
        root.addLayout(out_row)

        # split button
        self._split_btn = QPushButton("Split")
        self._split_btn.setFixedHeight(40)
        self._split_btn.setEnabled(False)
        self._split_btn.setStyleSheet(
            "QPushButton{background:#9b59b6;color:white;border-radius:8px;"
            "font-size:15px;font-weight:bold;}"
            "QPushButton:disabled{background:#cca8dc;}"
        )
        self._split_btn.clicked.connect(self._split)
        root.addWidget(self._split_btn)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("font-size:12px;color:#444;")
        root.addWidget(self._status)

    # -- loading -----------------------------------------------------------
    def _on_drop(self, path: str):
        if os.path.isdir(path):
            self.load_folder(path)
        else:
            self.load_image(path)

    def _browse_image(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Open image", os.path.expanduser("~"),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if p:
            self.load_image(p)

    def _browse_folder(self):
        p = QFileDialog.getExistingDirectory(self, "Open folder")
        if p:
            self.load_folder(p)

    def _browse_out(self):
        p = QFileDialog.getExistingDirectory(self, "Output folder",
                                              self._out_edit.text())
        if p:
            self._out_edit.setText(p)
            self._settings.setValue("manual_split/out_dir", p)

    def load_image(self, path: str):
        # NOTE: _image_path must NOT be set here — _store_current_cuts (inside
        # _load_pixmap) still needs the OLD path so the old image's cuts are
        # saved under the right key. _load_pixmap_inner sets the new path.
        self._folder_images = []
        self._folder_bar.setVisible(False)
        self._load_pixmap(path)

    def load_folder(self, folder: str):
        imgs = sorted(
            [os.path.join(folder, f) for f in os.listdir(folder)
             if f.lower().endswith(IMAGE_EXTS)],
            key=lambda p: _natural_key(os.path.basename(p))
        )
        if not imgs:
            self._status.setText("No images found in folder.")
            return
        self._store_current_cuts()   # save the previous image under its own path
        self._folder_images = imgs
        self._shared_cuts = []       # fresh folder starts with no shared cuts
        self._img_combo.blockSignals(True)
        self._img_combo.clear()
        for p in imgs:
            self._img_combo.addItem(os.path.basename(p), p)
        self._img_combo.blockSignals(False)
        self._folder_bar.setVisible(True)
        self._load_pixmap(imgs[0])

    def _on_combo_changed(self, idx: int):
        if idx < 0 or idx >= len(self._folder_images):
            return
        self._load_pixmap(self._folder_images[idx])

    def _store_current_cuts(self):
        """Remember the current image's cuts (full-resolution coords) so they
        survive switching images in folder mode."""
        if self._image_path and self._display_scale > 0:
            cuts = [round(y / self._display_scale) for y in self._scene.cut_ys()]
            # same-cuts mode: the on-screen cuts ARE the shared set and follow
            # the user to whichever image is shown next; per-image sets stay
            # untouched so unchecking brings each image's own cuts back
            if self._folder_images and self._same_cuts_chk.isChecked():
                self._shared_cuts = list(cuts)
            else:
                self._cuts_by_path[self._image_path] = cuts

    def _on_same_cuts_toggled(self, on: bool):
        if not self._folder_images:
            return
        if on:
            self._store_current_cuts()  # adopt the current cuts as the shared set
        else:
            # back to per-image mode: show this image's own cuts again
            scale = self._display_scale or 1.0
            self._scene.clear_lines()
            for y in self._cuts_by_path.get(self._image_path, []):
                self._scene._add_line(y * scale)

    def _load_pixmap(self, path: str):
        self._store_current_cuts()  # keep the cuts of the image we're leaving

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._load_pixmap_inner(path)
        finally:
            QApplication.restoreOverrideCursor()

    def _load_pixmap_inner(self, path: str):
        # Load with PIL: Qt refuses images taller than 32767 px, which long
        # stitched strips routinely exceed. Oversized images are displayed
        # downscaled; cuts are mapped back to full resolution when splitting.
        try:
            img = Image.open(path)
            img.load()
        except Exception as e:
            self._status.setText(f"Could not load: {path} ({e})")
            return
        if img.mode != "RGB":
            img = img.convert("RGB")

        self._image_path = path
        self._img_w, self._img_h = img.size
        scale = min(1.0, _MAX_DISPLAY_PX / max(1, self._img_h),
                    _MAX_DISPLAY_PX / max(1, self._img_w))
        self._display_scale = scale
        if scale < 1.0:
            img = img.resize((max(1, int(self._img_w * scale)),
                              max(1, int(self._img_h * scale))), Image.LANCZOS)
        disp_w, disp_h = img.size
        qimg = QImage(img.tobytes("raw", "RGB"), disp_w, disp_h,
                      3 * disp_w, QImage.Format_RGB888).copy()
        pm = QPixmap.fromImage(qimg)

        # reset() clears _lines list AND calls QGraphicsScene.clear() together
        # so no stale C++ item references remain
        self._scene.reset(float(disp_w), float(disp_h))
        self._scene.set_display_scale(scale)
        self._scene.set_analysis(*self._analyze_rows(img))

        pix_item = QGraphicsPixmapItem(pm)
        pix_item.setZValue(0)
        self._scene.addItem(pix_item)
        self._scene.setSceneRect(0, 0, disp_w, disp_h)

        # restore cuts (full-res → display coords): in same-cuts folder mode
        # the shared set follows the user to every image; otherwise each image
        # shows the cuts that were placed on it
        if self._folder_images and self._same_cuts_chk.isChecked():
            cuts = self._shared_cuts
        else:
            cuts = self._cuts_by_path.get(path, [])
        for y in cuts:
            self._scene._add_line(y * scale)  # _add_line clamps into bounds

        self._view.setVisible(True)
        self._view.setFocus()
        self._hint.setVisible(True)
        self._drop.setVisible(False)
        self._add_btn.setEnabled(True)
        self._clear_btn.setEnabled(True)
        self._auto_btn.setEnabled(True)
        self._split_btn.setEnabled(True)
        self._on_cuts_changed()
        # Fit AFTER the layout pass — fitting while the view is still hidden /
        # unsized produced a broken initial zoom. Tall strips fit to width and
        # start at the top (fitting the whole strip makes it a useless sliver).
        QTimer.singleShot(0, self._fit_initial)

    @staticmethod
    def _analyze_rows(img: Image.Image):
        """Per-display-row slice cost (low in blank gutters, high in artwork)
        plus a 'quiet' threshold — drives green/red lines, snapping, auto-cuts."""
        try:
            w, h = img.size
            aw = max(2, min(w, 220))  # narrow strip is plenty for row stats
            small = img.resize((aw, h), Image.BILINEAR).convert("L")
            g = np.asarray(small, dtype=np.float32)
            var = g.var(axis=1) / 255.0
            grad = np.abs(np.diff(g, axis=1)).mean(axis=1)
            cost = np.convolve(var + grad, np.ones(5) / 5.0, mode="same")
            p10, p90 = np.percentile(cost, [10, 90])
            thr = float(max(2.0, min(6.0, p10 + 0.10 * (p90 - p10))))
            return cost, thr
        except Exception:
            return None, 0.0

    def _fit_initial(self):
        sr = self._scene.sceneRect()
        vp = self._view.viewport()
        if sr.width() <= 0 or vp.width() <= 2:
            return
        self._view.resetTransform()
        if sr.height() > sr.width() * 3:  # tall strip → fit width, top
            f = vp.width() / sr.width()
            self._view.scale(f, f)
            self._view.verticalScrollBar().setValue(0)
        else:
            self._view.fitInView(sr, Qt.KeepAspectRatio)

    # -- cuts --------------------------------------------------------------
    def _add_line_at_view_center(self):
        """Add a cut at the middle of what's currently on screen (snapped),
        not the middle of a 30k-px strip the user would have to hunt for."""
        h = self._scene._img_h
        if h <= 0:
            return
        vp = self._view.viewport()
        y = self._view.mapToScene(vp.rect().center()).y()
        if not (0 < y < h):
            y = h / 2
        y = self._scene.snap_y(y)
        existing = {int(l.scene_y()) for l in self._scene._lines}
        while int(y) in existing:
            y += 20
        self._scene._add_line(y)

    def _auto_cuts(self):
        """Place N−1 cuts at quiet rows near equal spacing (replaces current
        cuts). Falls back to exact equal spacing when no profile exists."""
        h = self._scene._img_h
        if h <= 0:
            return
        n = self._auto_spin.value()
        cost = self._scene._row_cost
        self._scene.clear_lines()
        for i in range(1, n):
            target = h * i / n
            if cost is not None and len(cost) > 0:
                r = max(4.0, h / (2 * n) * 0.9)
                lo = int(max(1, target - r))
                hi = int(min(h - 1, target + r))
                if hi > lo:
                    seg = cost[lo:hi].astype(np.float64)
                    spread = float(seg.max() - seg.min()) or 1.0
                    dist = np.abs(np.arange(lo, hi) - target) / max(1, hi - lo)
                    target = lo + int(np.argmin(seg + 0.6 * spread * dist))
            self._scene._add_line(float(target))

    def _on_cuts_changed(self):
        ys = self._scene.cut_ys()
        parts = len(ys) + 1
        self._split_btn.setText(f"Split into {parts} part{'s' if parts != 1 else ''}")
        # live part-height readout in full-resolution pixels
        if ys and self._img_h > 0:
            scale = self._display_scale or 1.0
            bounds = [0] + [round(y / scale) for y in ys] + [self._img_h]
            heights = [b - a for a, b in zip(bounds, bounds[1:]) if b > a]
            self._parts_lab.setText(
                "Part heights: " + "  ·  ".join(f"{h:,} px" for h in heights))
        else:
            self._parts_lab.setText("")

    # -- splitting ---------------------------------------------------------
    def _split(self):
        path = self._image_path
        if not path or not os.path.exists(path):
            self._status.setText("No image loaded.")
            return

        out_dir = self._out_edit.text().strip()
        if not out_dir:
            out_dir = os.path.expanduser("~/Desktop/ManhwaPrep/splits")
        os.makedirs(out_dir, exist_ok=True)

        self._store_current_cuts()  # cuts on screen → full-res (shared or per-path)
        targets = self._folder_images if self._folder_images else [path]
        same_cuts = self._same_cuts_chk.isChecked() if self._folder_images else False
        current_cuts = self._shared_cuts if same_cuts else \
            self._cuts_by_path.get(self._image_path, [])

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            total_saved = 0
            for img_path in targets:
                # same-cuts: the cuts on screen apply to every image; otherwise
                # each image uses the cuts that were placed on it.
                cuts = current_cuts if same_cuts else self._cuts_by_path.get(img_path, [])
                saved = self._split_one(img_path, cuts, out_dir,
                                        prefix=os.path.splitext(os.path.basename(img_path))[0] if len(targets) > 1 else "")
                total_saved += saved
        finally:
            QApplication.restoreOverrideCursor()

        self._settings.setValue("manual_split/out_dir", out_dir)
        self._status.setText(f"✓ {total_saved} image(s) saved to {out_dir}")
        self._status.setStyleSheet("font-size:12px;color:#1a9e4b;")
        _open_folder(out_dir)
        if getattr(self, "_export_cb", None):
            self._export_cb()

    def _split_one(self, img_path: str, cuts: list[int], out_dir: str, prefix: str = "") -> int:
        try:
            img = Image.open(img_path)
        except Exception as e:
            self._status.setText(f"Could not open {img_path}: {e}")
            return 0

        w, h = img.size
        ys = sorted(y for y in cuts if 0 < y < h)
        boundaries = [0] + ys + [h]

        saved = 0
        for i in range(len(boundaries) - 1):
            y0, y1 = boundaries[i], boundaries[i + 1]
            if y1 <= y0:
                continue
            crop = img.crop((0, y0, w, y1))
            idx = i + 1
            fname = f"{prefix + '_' if prefix else ''}{idx:03d}.jpg"
            crop.save(os.path.join(out_dir, fname), "JPEG", quality=92)
            saved += 1

        return saved

    def set_export_dir(self, path: str):
        self._out_edit.setText(path)

    def set_export_callback(self, fn):
        self._export_cb = fn


def _open_folder(path: str):
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])
