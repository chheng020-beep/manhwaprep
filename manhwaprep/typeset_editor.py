"""Native Khmer typesetting editor.

Opens a chapter's typeset layout (long cleaned canvas + bubble positions),
auto-places an editable text box on each bubble, lets you paste Claude's
numbered Khmer to fill them, then drag / retype / restyle (font, size, colour,
white outline) and export a flattened image. Replaces the Photoshop step.

  python -m manhwaprep.typeset_editor [path/to/typeset/layout.json]
"""

from __future__ import annotations

import json
import os
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

KHMER_FONT = "Khmer Sangam MN"


class TextBoxItem(QGraphicsItem):
    """An editable text frame: move by dragging the body, resize via 8 handles.
    Corner handles scale the text (font grows/shrinks); side handles change one
    dimension and the text reflows to fit."""

    HANDLE = 11  # handle square size, in item/scene px
    _CURSORS = {
        "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
        "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
        "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
        "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
    }

    def __init__(self, n, text, x, y, w, h):
        super().__init__()
        self.n = n
        self.text = text
        self.w = float(w)
        self.h = float(h)
        self.font = QFont(KHMER_FONT)
        self.font.setPointSizeF(max(10.0, h * 0.30))
        self.fill = QColor(0, 0, 0)
        self.outline = QColor(255, 255, 255)
        self.outline_w = 3
        self.align = Qt.AlignHCenter | Qt.AlignVCenter
        self.setFlags(
            QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
        )
        self.setAcceptHoverEvents(True)
        self.setPos(x, y)
        self._resize = None
        self._start = None

    def boundingRect(self) -> QRectF:
        m = self.outline_w + self.HANDLE
        return QRectF(-m, -m, self.w + 2 * m, self.h + 2 * m)

    def _handles(self) -> dict:
        w, h, s = self.w, self.h, self.HANDLE
        pts = {
            "tl": (0, 0), "tr": (w, 0), "bl": (0, h), "br": (w, h),
            "t": (w / 2, 0), "b": (w / 2, h), "l": (0, h / 2), "r": (w, h / 2),
        }
        return {k: QRectF(px - s / 2, py - s / 2, s, s) for k, (px, py) in pts.items()}

    def _handle_at(self, pos):
        for k, r in self._handles().items():
            if r.contains(pos):
                return k
        return None

    def paint(self, p, opt, widget=None):
        p.setFont(self.font)
        r = QRectF(0, 0, self.w, self.h)
        flags = int(self.align) | int(Qt.TextWordWrap)
        ow = self.outline_w
        if ow > 0 and self.text:
            p.setPen(self.outline)
            for dx in range(-ow, ow + 1):
                for dy in range(-ow, ow + 1):
                    if (dx or dy) and dx * dx + dy * dy <= ow * ow:
                        p.drawText(r.translated(dx, dy), flags, self.text)
        p.setPen(self.fill)
        p.drawText(r, flags, self.text)
        if self.isSelected():
            pen = QPen(QColor(0, 150, 255))
            pen.setStyle(Qt.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            p.setBrush(QColor(255, 255, 255))
            p.setPen(QPen(QColor(0, 150, 255)))
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
            self._start = (self.w, self.h, self.x(), self.y(),
                           self.font.pointSizeF(), e.scenePos())
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if not self._resize:
            super().mouseMoveEvent(e)
            return
        self.prepareGeometryChange()
        w0, h0, x0, y0, fs0, sp0 = self._start
        d = e.scenePos() - sp0
        dx, dy = d.x(), d.y()
        k = self._resize
        MIN = 20.0

        if k in ("l", "r", "t", "b"):  # sides: resize one axis, reflow
            if k == "r":
                self.w = max(MIN, w0 + dx)
            elif k == "l":
                nw = max(MIN, w0 - dx)
                self.setX(x0 + (w0 - nw))
                self.w = nw
            elif k == "b":
                self.h = max(MIN, h0 + dy)
            elif k == "t":
                nh = max(MIN, h0 - dy)
                self.setY(y0 + (h0 - nh))
                self.h = nh
        else:  # corners: scale text + box proportionally
            if k in ("br", "tr"):
                nh = h0 + dy if k == "br" else h0 - dy
            else:  # bl, tl
                nh = h0 + dy if k == "bl" else h0 - dy
            nh = max(MIN, nh)
            scale = nh / h0 if h0 else 1.0
            nw = max(MIN, w0 * scale)
            # anchor the opposite corner
            if k == "br":
                pass
            elif k == "tr":
                self.setY(y0 + (h0 - nh))
            elif k == "bl":
                self.setX(x0 + (w0 - nw))
            elif k == "tl":
                self.setX(x0 + (w0 - nw))
                self.setY(y0 + (h0 - nh))
            self.w, self.h = nw, nh
            self.font.setPointSizeF(max(6.0, fs0 * scale))
        self.update()
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._resize:
            self._resize = None
            e.accept()
        else:
            super().mouseReleaseEvent(e)

    def to_dict(self):
        return {
            "n": self.n, "text": self.text, "x": self.x(), "y": self.y(),
            "w": self.w, "h": self.h, "font": self.font.family(),
            "size": self.font.pointSizeF(), "fill": self.fill.name(),
            "outline": self.outline.name(), "outline_w": self.outline_w,
        }


class _CanvasView(QGraphicsView):
    """Graphics view with Ctrl+wheel zoom (plain wheel scrolls)."""

    def __init__(self, scene):
        super().__init__(scene)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            self.scale(f, f)
            e.accept()
        else:
            super().wheelEvent(e)


class PasteDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Paste Claude's numbered Khmer")
        self.resize(460, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Paste the numbered Khmer list (e.g. '7. [bubble] …'):"))
        self.edit = QPlainTextEdit()
        lay.addWidget(self.edit)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def text(self):
        return self.edit.toPlainText()


class TypesetEditor(QWidget):
    def __init__(self, layout_path: str):
        super().__init__()
        self.layout_path = layout_path
        self.base = os.path.dirname(layout_path)
        with open(layout_path, encoding="utf-8") as f:
            self.layout = json.load(f)
        self.segments = self.layout.get("segments", [])
        self.seg_idx = 0
        self.items: list[TextBoxItem] = []

        self.setWindowTitle(f"Typeset — {self.layout.get('chapter', '')}")
        self.resize(1200, 860)
        root = QHBoxLayout(self)

        self.scene = QGraphicsScene()
        self.view = _CanvasView(self.scene)
        self.view.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.scene.selectionChanged.connect(self._sync_panel)
        root.addWidget(self.view, 4)

        root.addLayout(self._build_panel(), 0)
        self._load_segment(0)

    # -- side panel ----------------------------------------------------
    def _build_panel(self):
        col = QVBoxLayout()

        nav = QHBoxLayout()
        self.prev = QPushButton("‹")
        self.next = QPushButton("›")
        self.prev.clicked.connect(lambda: self._go(-1))
        self.next.clicked.connect(lambda: self._go(1))
        self.seg_lbl = QLabel("")
        nav.addWidget(self.prev)
        nav.addWidget(self.seg_lbl, 1, Qt.AlignCenter)
        nav.addWidget(self.next)
        col.addLayout(nav)

        self.copy_btn = QPushButton("1️⃣ Copy text for Claude")
        self.copy_btn.clicked.connect(self._copy_for_claude)
        col.addWidget(self.copy_btn)

        self.paste_btn = QPushButton("2️⃣ Paste Khmer list…")
        self.paste_btn.clicked.connect(self._paste)
        col.addWidget(self.paste_btn)

        col.addWidget(QLabel("Selected text:"))
        self.text_edit = QPlainTextEdit()
        self.text_edit.setFixedHeight(80)
        self.text_edit.setFont(QFont(KHMER_FONT, 15))
        self.text_edit.textChanged.connect(self._text_changed)
        col.addWidget(self.text_edit)

        self.fontbox = QFontComboBox()
        self.fontbox.setCurrentFont(QFont(KHMER_FONT))
        self.fontbox.currentFontChanged.connect(self._font_changed)
        col.addWidget(self.fontbox)

        srow = QHBoxLayout()
        srow.addWidget(QLabel("Size"))
        self.size = QSpinBox()
        self.size.setRange(6, 400)
        self.size.setValue(24)
        self.size.valueChanged.connect(self._size_changed)
        srow.addWidget(self.size)
        srow.addWidget(QLabel("Outline"))
        self.ow = QSpinBox()
        self.ow.setRange(0, 12)
        self.ow.setValue(3)
        self.ow.valueChanged.connect(self._ow_changed)
        srow.addWidget(self.ow)
        col.addLayout(srow)

        crow = QHBoxLayout()
        self.fill_btn = QPushButton("Text colour")
        self.fill_btn.clicked.connect(self._pick_fill)
        self.outline_btn = QPushButton("Outline colour")
        self.outline_btn.clicked.connect(self._pick_outline)
        crow.addWidget(self.fill_btn)
        crow.addWidget(self.outline_btn)
        col.addLayout(crow)

        col.addStretch(1)
        self.export_btn = QPushButton("💾 Export this canvas (PNG)")
        self.export_btn.clicked.connect(self._export)
        col.addWidget(self.export_btn)
        self.export_all_btn = QPushButton("Export ALL canvases")
        self.export_all_btn.clicked.connect(self._export_all)
        col.addWidget(self.export_all_btn)
        save = QPushButton("Save project")
        save.clicked.connect(self._save)
        col.addWidget(save)

        wrap = QWidget()
        wrap.setLayout(col)
        wrap.setFixedWidth(300)
        outer = QVBoxLayout()
        outer.addWidget(wrap)
        return outer

    # -- segment handling ----------------------------------------------
    def _commit_items(self):
        if self.segments:
            self.segments[self.seg_idx]["_state"] = [it.to_dict() for it in self.items]

    def _go(self, d):
        self._commit_items()
        self.seg_idx = max(0, min(len(self.segments) - 1, self.seg_idx + d))
        self._load_segment(self.seg_idx)

    def _load_segment(self, idx):
        if not self.segments:
            return
        seg = self.segments[idx]
        self.scene.clear()
        self.items = []
        pix = QPixmap(os.path.join(self.base, seg["image"]))
        bg = QGraphicsPixmapItem(pix)
        bg.setZValue(-1)
        self.scene.addItem(bg)
        self.scene.setSceneRect(0, 0, seg["width"], seg["height"])
        state = seg.get("_state")
        if state:
            for d in state:
                it = TextBoxItem(d["n"], d["text"], d["x"], d["y"], d["w"], d["h"])
                it.font = QFont(d["font"])
                it.font.setPointSizeF(float(d["size"]))
                it.fill = QColor(d["fill"])
                it.outline = QColor(d["outline"])
                it.outline_w = d["outline_w"]
                self.scene.addItem(it)
                self.items.append(it)
        else:
            for b in seg["items"]:
                x, y, w, h = b["bbox"]
                it = TextBoxItem(b["n"], b["src"], x, y, w, h)
                self.scene.addItem(it)
                self.items.append(it)
        self.seg_lbl.setText(f"Canvas {idx + 1}/{len(self.segments)}")
        self.prev.setEnabled(idx > 0)
        self.next.setEnabled(idx < len(self.segments) - 1)

    # -- editing -------------------------------------------------------
    def _selected(self):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, TextBoxItem)]
        return sel

    def _sync_panel(self):
        sel = self._selected()
        if not sel:
            return
        it = sel[0]
        self.text_edit.blockSignals(True)
        self.text_edit.setPlainText(it.text)
        self.text_edit.blockSignals(False)
        self.size.blockSignals(True)
        self.size.setValue(max(6, round(it.font.pointSizeF())))
        self.size.blockSignals(False)
        self.ow.blockSignals(True)
        self.ow.setValue(it.outline_w)
        self.ow.blockSignals(False)

    def _text_changed(self):
        for it in self._selected():
            it.text = self.text_edit.toPlainText()
            it.update()

    def _font_changed(self, font):
        for it in self._selected():
            nf = QFont(font.family())
            nf.setPointSizeF(it.font.pointSizeF())
            it.font = nf
            it.update()

    def _size_changed(self, v):
        for it in self._selected():
            it.font.setPointSizeF(float(v))
            it.update()

    def _ow_changed(self, v):
        for it in self._selected():
            it.prepareGeometryChange()
            it.outline_w = v
            it.update()

    def _pick_fill(self):
        c = QColorDialog.getColor(QColor(0, 0, 0), self, "Text colour")
        if c.isValid():
            for it in self._selected():
                it.fill = c
                it.update()

    def _pick_outline(self):
        c = QColorDialog.getColor(QColor(255, 255, 255), self, "Outline colour")
        if c.isValid():
            for it in self._selected():
                it.outline = c
                it.update()

    def _copy_for_claude(self):
        lines = []
        for seg in self.segments:
            for it in seg["items"]:
                lines.append((it["n"], f"{it['n']}. [{it['kind']}] {it['src']}"))
        lines.sort(key=lambda t: t[0])
        body = "\n".join(s for _, s in lines)
        text = (
            "Translate each numbered line below into natural Khmer for a manhwa. "
            "Keep the numbers and the [bubble]/[sfx] tags, one line each.\n\n" + body
        )
        QApplication.clipboard().setText(text)
        QMessageBox.information(
            self, "Copied",
            f"Copied {len(lines)} numbered lines (+ a prompt) to the clipboard.\n\n"
            "Paste into Claude, then paste the Khmer back with “2️⃣ Paste Khmer list”.",
        )

    def _paste(self):
        from .psgen import parse_khmer_list

        dlg = PasteDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        km = parse_khmer_list(dlg.text())
        if not km:
            QMessageBox.warning(self, "No lines", "No 'N. text' lines found.")
            return
        filled = 0
        for it in self.items:
            if it.n in km:
                it.text = km[it.n]
                it.update()
                filled += 1
        QMessageBox.information(self, "Filled", f"Filled {filled} text boxes.")

    # -- export / save -------------------------------------------------
    def _render(self, seg) -> QImage:
        img = QImage(int(seg["width"]), int(seg["height"]), QImage.Format_RGB32)
        img.fill(Qt.white)
        p = QPainter(img)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.scene.clearSelection()
        self.scene.render(
            p,
            QRectF(0, 0, seg["width"], seg["height"]),
            QRectF(0, 0, seg["width"], seg["height"]),
        )
        p.end()
        return img

    def _export(self):
        seg = self.segments[self.seg_idx]
        out = os.path.join(self.base, seg["image"].replace(".png", "_kh.png"))
        self._render(seg).save(out)
        QMessageBox.information(self, "Exported", out)

    def _export_all(self):
        self._commit_items()
        done = []
        for i, seg in enumerate(self.segments):
            self._load_segment(i)
            out = os.path.join(self.base, seg["image"].replace(".png", "_kh.png"))
            self._render(seg).save(out)
            done.append(out)
        QMessageBox.information(self, "Exported all", "\n".join(done))

    def _save(self):
        self._commit_items()
        proj = {
            "layout": os.path.basename(self.layout_path),
            "segments": [
                {"image": s["image"], "state": s.get("_state", [])}
                for s in self.segments
            ],
        }
        path = os.path.join(self.base, "typeset_project.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(proj, f, ensure_ascii=False, indent=2)
        QMessageBox.information(self, "Saved", path)


def main():
    app = QApplication(sys.argv)
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        path, _ = QFileDialog.getOpenFileName(
            None, "Open typeset layout.json",
            os.path.expanduser("~/ManhwaPrep/output"), "Layout (layout.json)",
        )
        if not path:
            return
    win = TypesetEditor(path)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
