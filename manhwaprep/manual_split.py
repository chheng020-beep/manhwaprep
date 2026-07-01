"""Manual image splitter — click to place horizontal cut lines, split into numbered JPGs."""
from __future__ import annotations

import os
import re
import subprocess
import sys

from PIL import Image
from PySide6.QtCore import (
    QPointF,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QPainter,
    QPen,
    QPixmap,
)
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
    QVBoxLayout,
    QWidget,
)

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")

_YELLOW = QColor("#FFD700")
_ORANGE = QColor("#FF8C00")
_BAND = 10   # semi-transparent highlight band px each side


def _natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


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

        self.setPos(0, y)
        self.setFlags(
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemSendsScenePositionChanges
        )
        self.setAcceptHoverEvents(True)
        self.setCursor(QCursor(Qt.SizeVerCursor))
        self.setZValue(10)

    # -- geometry ----------------------------------------------------------
    def boundingRect(self) -> QRectF:
        return QRectF(0, -(_BAND + 2), self._img_w, (_BAND + 2) * 2)

    def paint(self, painter: QPainter, option, widget=None):
        color = _ORANGE if self._hovered else _YELLOW

        # semi-transparent band
        band_color = QColor(color)
        band_color.setAlpha(55)
        painter.fillRect(QRectF(0, -_BAND, self._img_w, _BAND * 2), band_color)

        # dashed line
        pen = QPen(color, 3, Qt.DashLine)
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

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        # clamp X=0, clamp Y within image
        p = self.pos()
        new_y = max(1.0, min(self._img_h - 1, p.y()))
        self.setPos(0, new_y)
        self.setToolTip(f"y: {int(new_y)} px  (double-click to delete)")
        self._scene_ref.cuts_changed.emit()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            # keep x=0
            if isinstance(value, QPointF):
                value.setX(0)
            return value
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

    def set_image_size(self, w: float, h: float):
        self._img_w = w
        self._img_h = h

    def mousePressEvent(self, e):
        # only add line on left-click on empty space
        if e.button() == Qt.LeftButton:
            hit = self.itemAt(e.scenePos(), self.views()[0].transform() if self.views() else __import__("PySide6.QtGui", fromlist=["QTransform"]).QTransform())
            if hit is None or isinstance(hit, QGraphicsPixmapItem):
                y = e.scenePos().y()
                if 0 < y < self._img_h:
                    self._add_line(y)
                    e.accept()
                    return
        super().mousePressEvent(e)

    def _add_line(self, y: float):
        line = CutLineItem(y, self._img_w, self._img_h, self)
        self.addItem(line)
        self._lines.append(line)
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
        self.setCursor(QCursor(Qt.CrossCursor))
        self._panning = False
        self._pan_start = None
        self._space_held = False
        self._scale = 1.0

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            delta = e.angleDelta().y()
            factor = 1.15 if delta > 0 else 1 / 1.15
            new_scale = max(0.1, min(5.0, self._scale * factor))
            self.scale(new_scale / self._scale, new_scale / self._scale)
            self._scale = new_scale
            e.accept()
        else:
            super().wheelEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Space:
            self._space_held = True
            self.setCursor(QCursor(Qt.OpenHandCursor))
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space:
            self._space_held = False
            self.setCursor(QCursor(Qt.CrossCursor))
        super().keyReleaseEvent(e)

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
        self._img_w = 0
        self._img_h = 0

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # drop zone
        self._drop = _DropZone()
        self._drop.dropped.connect(self._on_drop)
        root.addWidget(self._drop)

        browse_row = QHBoxLayout()
        browse_img = QPushButton("🖼 Open image…")
        browse_img.clicked.connect(self._browse_image)
        browse_folder = QPushButton("📁 Open folder…")
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

        hint = QLabel("Click on the image to add a cut line · drag to reposition · double-click to delete")
        hint.setStyleSheet("color:#888;font-size:11px;")
        hint.setAlignment(Qt.AlignCenter)
        hint.setVisible(False)
        self._hint = hint
        root.addWidget(hint)

        # controls row
        ctrl = QHBoxLayout()
        self._add_btn = QPushButton("＋ Add cut line")
        self._add_btn.clicked.connect(self._scene.add_line_at_center)
        self._add_btn.setEnabled(False)
        self._clear_btn = QPushButton("✕ Clear all")
        self._clear_btn.clicked.connect(self._scene.clear_lines)
        self._clear_btn.setEnabled(False)
        ctrl.addWidget(self._add_btn)
        ctrl.addWidget(self._clear_btn)
        ctrl.addStretch(1)
        root.addLayout(ctrl)

        # output row
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output:"))
        default_out = os.path.expanduser("~/Desktop/ManhwaPrep/splits")
        self._out_edit = QLineEdit(default_out)
        out_row.addWidget(self._out_edit, 1)
        out_browse = QPushButton("…")
        out_browse.setFixedWidth(32)
        out_browse.clicked.connect(self._browse_out)
        out_row.addWidget(out_browse)
        root.addLayout(out_row)

        # split button
        self._split_btn = QPushButton("✂️  Split")
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

    def load_image(self, path: str):
        self._image_path = path
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
        self._folder_images = imgs
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

    def _load_pixmap(self, path: str):
        self._image_path = path
        pm = QPixmap(path)
        if pm.isNull():
            self._status.setText(f"Could not load: {path}")
            return

        self._img_w = pm.width()
        self._img_h = pm.height()
        self._scene.clear()
        self._scene.set_image_size(float(self._img_w), float(self._img_h))

        pix_item = QGraphicsPixmapItem(pm)
        pix_item.setZValue(0)
        self._scene.addItem(pix_item)
        self._scene.setSceneRect(0, 0, self._img_w, self._img_h)

        self._view.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        self._view.setVisible(True)
        self._hint.setVisible(True)
        self._drop.setVisible(False)
        self._add_btn.setEnabled(True)
        self._clear_btn.setEnabled(True)
        self._split_btn.setEnabled(True)
        self._on_cuts_changed()

    # -- cuts --------------------------------------------------------------
    def _on_cuts_changed(self):
        n = len(self._scene.cut_ys())
        parts = n + 1
        self._split_btn.setText(f"✂️  Split into {parts} part{'s' if parts != 1 else ''}")

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

        targets = self._folder_images if self._folder_images else [path]
        same_cuts = self._same_cuts_chk.isChecked() if self._folder_images else False
        shared_cuts = self._scene.cut_ys() if same_cuts else None

        total_saved = 0
        for img_path in targets:
            cuts = shared_cuts if (same_cuts and shared_cuts is not None) else self._scene.cut_ys()
            saved = self._split_one(img_path, cuts, out_dir,
                                    prefix=os.path.splitext(os.path.basename(img_path))[0] if len(targets) > 1 else "")
            total_saved += saved

        self._status.setText(f"✓ {total_saved} image(s) saved to {out_dir}")
        self._status.setStyleSheet("font-size:12px;color:#1a9e4b;")
        _open_folder(out_dir)

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


def _open_folder(path: str):
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])
