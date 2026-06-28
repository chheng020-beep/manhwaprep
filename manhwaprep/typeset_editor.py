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
    QFontMetricsF,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QTextCursor,
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

KHMER_FONT = "Khmer Sangam MN"
# Khmer has no spaces between words, so word-wrap alone leaves it as one huge
# unbreakable line. WrapAtWordBoundaryOrAnywhere wraps English at spaces and
# Khmer wherever it must, so both fit and measure correctly.
WRAP_FLAGS = int(Qt.TextWordWrap) | int(Qt.TextWrapAnywhere)


class TextBoxItem(QGraphicsItem):
    """An editable text frame. The BOX is the boss: you set its size (drag the
    body to move, drag any edge or corner to resize) and the font auto-fits so
    the text always sits inside — shrinking when it would overflow, growing to
    fill, never spilling out. Corners also scale the font cap, so a corner drag
    sizes the letters along with the box (Canva-style)."""

    HANDLE = 11  # corner handle square size, in item/scene px
    EDGE_GRAB = 9.0  # how far from an edge still counts as grabbing that edge
    FONT_MIN = 6.0   # never shrink the font below this
    FONT_MAX = 200.0  # never grow the font above this
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
        # The BOX is the boss: the font auto-fits inside w×h, never larger than
        # this cap. A high default lets the text fill the box; the Size spin box
        # (or a corner drag) lowers/raises the cap.
        self.max_size = self.FONT_MAX
        self.font.setPointSizeF(20.0)
        self.fill = QColor(0, 0, 0)
        self.outline = QColor(255, 255, 255)
        self.outline_w = 3
        self.align = Qt.AlignHCenter | Qt.AlignVCenter
        self.on_edit = None  # set by the editor: callback(item) for inline edit
        self._editing = False  # True while the inline editor overlays this box
        self.setFlags(
            QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
        )
        self.setAcceptHoverEvents(True)
        self.setPos(x, y)
        self.setTransformOriginPoint(self.w / 2, self.h / 2)
        self._resize = None
        self._start = None
        self._refit()

    def _fits(self, text: str, size: float) -> bool:
        """Does the wrapped text fit inside the box at this font size?"""
        f = QFont(self.font)
        f.setPointSizeF(size)
        fm = QFontMetricsF(f)
        flags = int(Qt.AlignHCenter) | WRAP_FLAGS
        r = fm.boundingRect(QRectF(0, 0, max(8.0, self.w), 1e7), flags, text)
        return r.height() <= self.h - 2 and r.width() <= self.w + 0.5

    def _refit(self):
        """The BOX is the boss: pick the largest font (<= max_size) whose wrapped
        text fits inside the current w×h. Letters shrink to fit and grow to fill,
        but never spill outside the box. The box itself is NOT resized here."""
        text = self.text or " "
        cap = max(self.FONT_MIN, min(self.max_size, self.FONT_MAX))
        lo, hi, fit = self.FONT_MIN, cap, self.FONT_MIN
        if self._fits(text, cap):
            fit = cap  # text already fits at the cap — use it, don't search
        else:
            for _ in range(18):  # binary search for the largest fitting size
                mid = (lo + hi) / 2
                if self._fits(text, mid):
                    fit, lo = mid, mid
                else:
                    hi = mid
        self.font.setPointSizeF(fit)
        self.setTransformOriginPoint(self.w / 2, self.h / 2)

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
        # Corners first — they scale the font (and need a precise target).
        hs = self._handles()
        for k in ("tl", "tr", "bl", "br"):
            if hs[k].contains(pos):
                return k
        # The WHOLE side is grabbable, like Canva — not just a tiny mid-handle.
        # Drag any point along the left/right edge to reshape (and auto-grow).
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

    def paint(self, p, opt, widget=None):
        r = QRectF(0, 0, self.w, self.h)
        if self._editing:
            return  # the inline overlay draws the text in our place (WYSIWYG)
        p.save()
        p.setClipRect(r)  # text can never render outside the box
        p.setFont(self.font)
        flags = int(self.align) | WRAP_FLAGS
        # Keep the halo proportional to the text so it stays readable on dark
        # art even at large sizes (a fixed 3px ring vanishes behind big glyphs).
        ow = max(self.outline_w, round(self.font.pointSizeF() * 0.10))
        if ow > 0 and self.text:
            p.setPen(self.outline)
            for dx in range(-ow, ow + 1):
                for dy in range(-ow, ow + 1):
                    if (dx or dy) and dx * dx + dy * dy <= ow * ow:
                        p.drawText(r.translated(dx, dy), flags, self.text)
        p.setPen(self.fill)
        p.drawText(r, flags, self.text)
        p.restore()
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
                           self.max_size, e.scenePos())
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        """Every handle just RESIZES THE BOX (the boss). The font then auto-fits
        inside it via _refit — letters shrink/grow, the box is what you set.
        Corners also scale the font cap so dragging a corner sizes the letters
        too (Canva-style); sides only reshape and let the font re-fit."""
        if not self._resize:
            super().mouseMoveEvent(e)
            return
        w0, h0, x0, y0, ms0, sp0 = self._start
        d = e.scenePos() - sp0
        dx, dy = d.x(), d.y()
        k = self._resize
        MIN = 24.0
        neww, newh, newx, newy = w0, h0, x0, y0

        if k in ("r", "tr", "br"):
            neww = max(MIN, w0 + dx)
            newx = x0
        elif k in ("l", "tl", "bl"):
            neww = max(MIN, w0 - dx)
            newx = x0 + (w0 - neww)
        if k in ("b", "bl", "br"):
            newh = max(MIN, h0 + dy)
            newy = y0
        elif k in ("t", "tl", "tr"):
            newh = max(MIN, h0 - dy)
            newy = y0 + (h0 - newh)

        self.prepareGeometryChange()
        self.w, self.h = neww, newh
        self.setPos(newx, newy)
        if k in ("tl", "tr", "bl", "br") and h0:  # corner -> scale the font cap
            self.max_size = max(self.FONT_MIN, min(self.FONT_MAX, ms0 * newh / h0))
        self._refit()
        self.update()
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._resize:
            self._resize = None
            e.accept()
        else:
            super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.on_edit:
            self.on_edit(self)
            e.accept()
        else:
            super().mouseDoubleClickEvent(e)

    def to_dict(self):
        return {
            "n": self.n, "text": self.text, "x": self.x(), "y": self.y(),
            "w": self.w, "h": self.h, "font": self.font.family(),
            "size": self.max_size, "fill": self.fill.name(),
            "outline": self.outline.name(), "outline_w": self.outline_w,
            "bold": self.font.bold(), "italic": self.font.italic(),
            "underline": self.font.underline(), "align": int(self.align),
            "rot": self.rotation(),
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


class _InlineEdit(QTextEdit):
    """Temporary on-canvas editor drawn transparently right over the text box,
    so what you type looks like the final result. Commits on focus-out or Esc;
    grows to fit so text never hides while typing."""

    def __init__(self, on_done, on_grow=None):
        super().__init__()
        self._on_done = on_done
        self._on_grow = on_grow
        self.setAcceptRichText(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)
        if on_grow is not None:
            self.textChanged.connect(on_grow)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._on_done()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._on_done()
            return
        super().keyPressEvent(e)


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
        self._inline_proxy = None
        self._inline_item = None

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

        addrow = QHBoxLayout()
        add_btn = QPushButton("➕ Add box")
        add_btn.clicked.connect(self._add_box)
        del_btn = QPushButton("🗑 Delete box")
        del_btn.clicked.connect(self._delete_box)
        addrow.addWidget(add_btn)
        addrow.addWidget(del_btn)
        col.addLayout(addrow)

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

        fmt = QHBoxLayout()
        self.bold_btn = QPushButton("B")
        self.bold_btn.setCheckable(True)
        self.bold_btn.setFixedWidth(32)
        self.bold_btn.setStyleSheet("font-weight:bold;")
        self.italic_btn = QPushButton("I")
        self.italic_btn.setCheckable(True)
        self.italic_btn.setFixedWidth(32)
        self.italic_btn.setStyleSheet("font-style:italic;")
        self.underline_btn = QPushButton("U")
        self.underline_btn.setCheckable(True)
        self.underline_btn.setFixedWidth(32)
        self.underline_btn.setStyleSheet("text-decoration:underline;")
        self.bold_btn.clicked.connect(self._toggle_bold)
        self.italic_btn.clicked.connect(self._toggle_italic)
        self.underline_btn.clicked.connect(self._toggle_underline)
        fmt.addWidget(self.bold_btn)
        fmt.addWidget(self.italic_btn)
        fmt.addWidget(self.underline_btn)
        self.align_combo = QComboBox()
        self.align_combo.addItems(["⬅ Left", "⬌ Center", "➡ Right"])
        self.align_combo.setCurrentIndex(1)
        self.align_combo.currentIndexChanged.connect(self._align_changed)
        fmt.addWidget(self.align_combo)
        col.addLayout(fmt)

        rrow = QHBoxLayout()
        rrow.addWidget(QLabel("Rotate"))
        self.rot = QSpinBox()
        self.rot.setRange(-180, 180)
        self.rot.setSuffix("°")
        self.rot.valueChanged.connect(self._rot_changed)
        rrow.addWidget(self.rot)
        rrow.addStretch(1)
        col.addLayout(rrow)

        col.addStretch(1)
        self.export_btn = QPushButton("💾 Export this canvas (PNG)")
        self.export_btn.clicked.connect(self._export)
        col.addWidget(self.export_btn)
        self.export_all_btn = QPushButton("Export ALL canvases")
        self.export_all_btn.clicked.connect(self._export_all)
        col.addWidget(self.export_all_btn)

        self.fb_btn = QPushButton("✂️ FB panels (this canvas)")
        self.fb_btn.setToolTip(
            "Slice this canvas into Facebook-sized panels, cutting only at safe "
            "gutters — never through a text box or the middle of a panel."
        )
        self.fb_btn.clicked.connect(self._export_fb)
        col.addWidget(self.fb_btn)
        self.fb_all_btn = QPushButton("✂️ FB panels (ALL canvases)")
        self.fb_all_btn.clicked.connect(self._export_fb_all)
        col.addWidget(self.fb_all_btn)

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
                it.max_size = float(d["size"])  # restore the font cap
                it.font.setBold(d.get("bold", False))
                it.font.setItalic(d.get("italic", False))
                it.font.setUnderline(d.get("underline", False))
                it.fill = QColor(d["fill"])
                it.outline = QColor(d["outline"])
                it.outline_w = d["outline_w"]
                if "align" in d:
                    it.align = Qt.AlignmentFlag(d["align"])
                self.scene.addItem(it)
                it._refit()
                if d.get("rot"):
                    it.setTransformOriginPoint(it.w / 2, it.h / 2)
                    it.setRotation(d["rot"])
                self.items.append(it)
        else:
            for b in seg["items"]:
                x, y, w, h = b["bbox"]
                it = TextBoxItem(b["n"], b["src"], x, y, w, h)
                self.scene.addItem(it)
                self.items.append(it)
        for it in self.items:
            it.on_edit = self._start_inline_edit
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
        self.bold_btn.setChecked(it.font.bold())
        self.italic_btn.setChecked(it.font.italic())
        self.underline_btn.setChecked(it.font.underline())
        amap = {int(Qt.AlignLeft): 0, int(Qt.AlignHCenter): 1, int(Qt.AlignRight): 2}
        ha = int(it.align) & (
            int(Qt.AlignLeft) | int(Qt.AlignHCenter) | int(Qt.AlignRight)
        )
        self.align_combo.blockSignals(True)
        self.align_combo.setCurrentIndex(amap.get(ha, 1))
        self.align_combo.blockSignals(False)
        self.rot.blockSignals(True)
        self.rot.setValue(int(it.rotation()))
        self.rot.blockSignals(False)

    def _text_changed(self):
        for it in self._selected():
            it.text = self.text_edit.toPlainText()
            it._refit()
            it.update()

    def _font_changed(self, font):
        for it in self._selected():
            nf = QFont(font.family())
            nf.setPointSizeF(it.font.pointSizeF())
            it.font = nf
            it._refit()
            it.update()

    def _size_changed(self, v):
        # Size sets the font CAP; the box is the boss, so the font still shrinks
        # below this if the text wouldn't otherwise fit inside the box.
        for it in self._selected():
            it.max_size = float(v)
            it._refit()
            it.update()

    # -- inline (double-click) editing ---------------------------------
    def _start_inline_edit(self, item):
        self._commit_inline()

        def grow():
            # Keep the overlay the size of the box (so editing looks like the
            # final), but vertically centre the text the way the box does so it
            # doesn't visibly jump up when you start editing. Grow only if the
            # text is genuinely taller than the box.
            te = self._inline_proxy.widget() if self._inline_proxy else None
            if te is None:
                return
            doc_h = te.document().size().height()
            h = max(item.h, doc_h + 2)
            te.setFixedHeight(int(h))
            top = max(0, int((item.h - doc_h) / 2))  # match the box's vcentre
            te.setViewportMargins(0, top, 0, 0)

        te = _InlineEdit(self._commit_inline, on_grow=grow)
        te.setFont(QFont(item.font))
        te.setPlainText(item.text)
        te.setAlignment(Qt.AlignHCenter)  # match the box's centred layout
        col = item.fill.name()
        # transparent background → no white block; text colour matches the final.
        te.setStyleSheet(
            f"QTextEdit{{background:transparent;border:1px dashed #2d7ff9;"
            f"color:{col};padding:0px;}}"
        )
        proxy = self.scene.addWidget(te)
        proxy.setZValue(1000)
        proxy.setPos(item.x(), item.y())
        te.setFixedWidth(int(max(24, item.w)))  # same width → same wrapping
        item._editing = True  # stop the box drawing its own copy of the text
        item.update()
        self._inline_proxy = proxy
        self._inline_item = item
        grow()  # size to the existing text right away
        te.setFocus()
        te.moveCursor(QTextCursor.End)  # caret at end, no destructive select-all

    def _commit_inline(self):
        if not self._inline_proxy:
            return
        proxy, it = self._inline_proxy, self._inline_item
        self._inline_proxy, self._inline_item = None, None
        it.text = proxy.widget().toPlainText()
        it._editing = False  # box paints its own (outlined) text again
        it._refit()
        it.update()
        if proxy.scene():
            proxy.scene().removeItem(proxy)
        self._sync_panel()

    def _ow_changed(self, v):
        for it in self._selected():
            it.prepareGeometryChange()
            it.outline_w = v
            it.update()

    def _add_box(self):
        center = self.view.mapToScene(self.view.viewport().rect().center())
        n = max([it.n for it in self.items], default=0) + 1
        it = TextBoxItem(n, "text", center.x() - 120, center.y() - 40, 240, 80)
        it.on_edit = self._start_inline_edit
        self.scene.addItem(it)
        self.items.append(it)
        self.scene.clearSelection()
        it.setSelected(True)

    def _delete_box(self):
        for it in list(self._selected()):
            self.scene.removeItem(it)
            if it in self.items:
                self.items.remove(it)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self._selected():
            self._delete_box()
        else:
            super().keyPressEvent(e)

    def _toggle_bold(self):
        for it in self._selected():
            it.font.setBold(self.bold_btn.isChecked())
            it._refit()
            it.update()

    def _toggle_italic(self):
        for it in self._selected():
            it.font.setItalic(self.italic_btn.isChecked())
            it._refit()
            it.update()

    def _toggle_underline(self):
        for it in self._selected():
            it.font.setUnderline(self.underline_btn.isChecked())
            it.update()

    def _align_changed(self, i):
        a = [Qt.AlignLeft, Qt.AlignHCenter, Qt.AlignRight][i] | Qt.AlignVCenter
        for it in self._selected():
            it.align = a
            it._refit()
            it.update()

    def _rot_changed(self, v):
        for it in self._selected():
            it.setTransformOriginPoint(it.w / 2, it.h / 2)
            it.setRotation(v)

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
                it._refit()
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

    # -- Facebook panel split ------------------------------------------
    @staticmethod
    def _qimage_to_bgr(img: QImage):
        """QImage -> contiguous H×W×3 BGR uint8 array for OpenCV."""
        import numpy as np

        img = img.convertToFormat(QImage.Format_RGB888)
        w, h, bpl = img.width(), img.height(), img.bytesPerLine()
        buf = np.frombuffer(img.constBits(), np.uint8, count=bpl * h)
        rgb = buf.reshape(h, bpl)[:, : w * 3].reshape(h, w, 3)
        return rgb[:, :, ::-1].copy()  # RGB -> BGR

    def _split_segment(self, seg) -> list[str]:
        """Render the current canvas and cut it into FB panels at safe seams.
        Text-box rects are passed as forbidden zones so no line is ever sliced."""
        from . import splitter

        bgr = self._qimage_to_bgr(self._render(seg))
        protect = [(it.y(), it.y() + it.h) for it in self.items]
        slices = splitter.split_panels(bgr, protect=protect)
        out_dir = os.path.join(self.base, "fb_panels")
        prefix = os.path.splitext(seg["image"])[0]  # canvas_001 -> canvas_001_NNN
        return splitter.write_panels(bgr, slices, out_dir, prefix=prefix)

    def _export_fb(self):
        paths = self._split_segment(self.segments[self.seg_idx])
        if not paths:
            QMessageBox.warning(self, "No panels", "Nothing to split.")
            return
        QMessageBox.information(
            self, "Facebook panels",
            f"{len(paths)} panel(s) →\n{os.path.dirname(paths[0])}",
        )

    def _export_fb_all(self):
        self._commit_items()
        total, folder = 0, None
        for i, seg in enumerate(self.segments):
            self._load_segment(i)
            paths = self._split_segment(seg)
            total += len(paths)
            if paths:
                folder = os.path.dirname(paths[0])
        QMessageBox.information(
            self, "Facebook panels",
            f"{total} panel(s) across {len(self.segments)} canvas(es)\n→ {folder}",
        )

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
