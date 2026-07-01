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
import re
import sys

import cv2
import numpy as np

from PySide6.QtCore import (
    QBuffer, QByteArray, QIODevice, QPointF, QRectF, QSize, Qt)
import math

from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QIcon,
    QImage,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
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
    QButtonGroup,
    QFontComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

# Khmer has no spaces between words, so word-wrap alone leaves it as one huge
# unbreakable line. WrapAtWordBoundaryOrAnywhere wraps English at spaces and
# Khmer wherever it must, so both fit and measure correctly.
WRAP_FLAGS = int(Qt.TextWordWrap) | int(Qt.TextWrapAnywhere)

# ── Luxe dark theme ──────────────────────────────────────────────────────────
_DARK_BG   = "#1a1a1f"
_PANEL_BG  = "#22222a"
_CARD_BG   = "#2a2a35"
_ACCENT    = "#a78bfa"
_ACCENT2   = "#f472b6"
_TEXT_MAIN = "#f1f0f5"
_TEXT_DIM  = "#6b6b80"
_BORDER    = "#333342"

_SIDEBAR_QSS = f"""
QWidget {{ background: {_DARK_BG}; color: {_TEXT_MAIN}; font-size: 12px; }}
QGroupBox {{
    border: 1px solid {_BORDER}; border-radius: 8px;
    margin-top: 10px; padding: 8px; color: {_TEXT_DIM}; font-size: 11px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
QPushButton {{
    background: {_CARD_BG}; color: {_TEXT_MAIN}; border: 1px solid {_BORDER};
    border-radius: 8px; padding: 5px 10px; font-size: 12px;
}}
QPushButton:hover {{ background: #363645; border-color: {_ACCENT}; }}
QPushButton:pressed {{ background: {_ACCENT}; color: white; }}
QPushButton:checked {{ background: {_ACCENT}; color: white; border-color: {_ACCENT}; }}
QToolButton {{
    background: {_CARD_BG}; color: {_TEXT_MAIN}; border: 1px solid {_BORDER};
    border-radius: 6px; padding: 4px 8px; font-size: 12px;
}}
QToolButton:hover {{ background: #363645; border-color: {_ACCENT}; }}
QToolButton:checked {{ background: {_ACCENT}; color: white; border-color: {_ACCENT}; }}
QComboBox {{
    background: {_CARD_BG}; color: {_TEXT_MAIN}; border: 1px solid {_BORDER};
    border-radius: 6px; padding: 4px 8px;
}}
QComboBox::drop-down {{ border: none; }}
QSpinBox {{
    background: {_CARD_BG}; color: {_TEXT_MAIN}; border: 1px solid {_BORDER};
    border-radius: 6px; padding: 3px 6px;
}}
QLabel {{ color: {_TEXT_DIM}; background: transparent; }}
QPlainTextEdit, QTextEdit {{
    background: {_CARD_BG}; color: {_TEXT_MAIN}; border: 1px solid {_BORDER};
    border-radius: 6px;
}}
QScrollArea {{ border: none; background: {_DARK_BG}; }}
QListWidget {{
    background: {_CARD_BG}; border: 1px solid {_BORDER}; border-radius: 6px;
}}
QFrame[frameShape="4"] {{ color: {_BORDER}; }}
"""

# ── Gradient presets ──────────────────────────────────────────────────────────
GRADIENT_PRESETS = {
    "Manhwa": [
        ["#FF6B9D", "#8B5CF6"],
        ["#DC143C", "#1a0a0a"],
        ["#C084FC", "#818CF8", "#BAE6FD"],
        ["#FB7185", "#FBBF24"],
        ["#3B82F6", "#06B6D4"],
        ["#991B1B", "#000000"],
        ["#FEF3C7", "#FBCFE8"],
        ["#2563EB", "#1E3A5F"],
        ["#F59E0B", "#92400E"],
        ["#0F0C29", "#302B63", "#24243E"],
        ["#FFFFFF", "#C0C0C0"],
        ["#FF0080", "#7928CA"],
    ],
    "Basics": [
        ["#000000", "#FFFFFF"],
        ["#FFFFFF", "#000000"],
        ["#000000", "#434343"],
        ["#808080", "#000000"],
        ["#434343", "#000000"],
        ["#FFFFFF", "#F0F0F0"],
    ],
    "Blues": [
        ["#0575E6", "#021B79"],
        ["#00C6FF", "#0072FF"],
        ["#4facfe", "#00f2fe"],
        ["#a1c4fd", "#c2e9fb"],
        ["#667eea", "#764ba2"],
        ["#0052D4", "#4364F7", "#6FB1FC"],
    ],
    "Pinks": [
        ["#f953c6", "#b91d73"],
        ["#ee0979", "#ff6a00"],
        ["#ffecd2", "#fcb69f"],
        ["#ff9a9e", "#fecfef"],
        ["#f6d365", "#fda085"],
        ["#fbc2eb", "#a6c1ee"],
        ["#fddb92", "#d1fdff"],
        ["#a18cd1", "#fbc2eb"],
        ["#fad0c4", "#ffd1ff"],
    ],
    "Purples": [
        ["#a18cd1", "#fbc2eb"],
        ["#764ba2", "#667eea"],
        ["#6a3093", "#a044ff"],
        ["#c471ed", "#f64f59"],
        ["#7b4397", "#dc2430"],
        ["#360033", "#0b8793"],
    ],
}

# story-heuristic post sizing, as multiples of canvas width (short, FB-friendly)
IDEAL_FB = 0.8
MAX_FB = 1.25

_KHMER_FONT = None


def khmer_font() -> str:
    """Resolve the default Khmer font, registering the bundled fonts first.
    Prefers 'Kh Ang MuraFastHand' (if the user has installed it, or it's bundled),
    then the bundled open handwriting font 'Fasthand' as a close fallback, then
    other Khmer fonts. Memoised; must be called after a QApplication exists."""
    global _KHMER_FONT
    if _KHMER_FONT is not None:
        return _KHMER_FONT
    # user-chosen default (written by "Set as default font" button)
    _default_path = os.path.expanduser("~/ManhwaPrep/default_font.txt")
    if os.path.exists(_default_path):
        try:
            _user_default = open(_default_path).read().strip()
            if _user_default:
                base0 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
                if os.path.isdir(base0):
                    for fn0 in sorted(os.listdir(base0)):
                        if fn0.lower().endswith((".ttf", ".otf")):
                            QFontDatabase.addApplicationFont(os.path.join(base0, fn0))
                if _user_default in set(QFontDatabase.families()):
                    _KHMER_FONT = _user_default
                    return _KHMER_FONT
        except Exception:
            pass
    registered = []
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
    if os.path.isdir(base):
        for fn in sorted(os.listdir(base)):
            if fn.lower().endswith((".ttf", ".otf")):
                fid = QFontDatabase.addApplicationFont(os.path.join(base, fn))
                registered += QFontDatabase.applicationFontFamilies(fid)
    fams = list(QFontDatabase.families())
    low = {f.lower(): f for f in fams}
    # 1) the requested Kh Ang MuraFastHand, however it's spelled, if available
    for f in fams:
        fl = f.lower()
        if "mura" in fl and "fast" in fl:
            _KHMER_FONT = f
            return f
    # 2) preference chain — bundled open 'Fasthand' is the close handwriting match
    for cand in ("Kh Ang MuraFastHand", "Fasthand", "Hanuman", *registered,
                 "Khmer Sangam MN", "Khmer OS", "Leelawadee UI", "Khmer UI",
                 "Noto Sans Khmer"):
        if cand.lower() in low:
            _KHMER_FONT = low[cand.lower()]
            return _KHMER_FONT
    _KHMER_FONT = registered[0] if registered else "Sans Serif"
    return _KHMER_FONT


class TextBoxItem(QGraphicsItem):
    """An editable text frame, Canva-style. The font size is fixed; the box
    height auto-grows to fit the wrapped text. Drag the body to move; drag a
    left/right edge to change width (text wraps, box gets taller, font unchanged);
    drag a corner to scale the font; drag top/bottom for a manual taller box."""

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
        self.font = QFont(khmer_font())
        # Canva model: the FONT is fixed (this size) and the box HEIGHT auto-grows
        # to fit the wrapped text. A corner drag or the Size box changes the font;
        # narrowing the width just wraps the text and makes the box taller.
        self.max_size = 30.0
        self.fill = QColor(0, 0, 0)
        self.outline = QColor(255, 255, 255)
        self.outline_w = 3
        self.align = Qt.AlignHCenter | Qt.AlignVCenter
        self.gradient_colors = None   # list[str] hex colors, or None for solid fill
        self.gradient_angle = 90.0    # degrees: 0=L→R, 90=T→B
        self.effect = "none"          # none|drop|glow|echo|background|hollow|neon
        self.effect_color = "#000000"
        self.on_edit = None  # set by the editor: callback(item) for inline edit
        self._editing = False  # True while the inline editor overlays this box
        self.setFlags(
            QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
        )
        # Cache the (expensive outlined-Khmer) render to a pixmap so scrolling /
        # zooming a page full of boxes doesn't re-shape every glyph each repaint.
        self.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
        self.setAcceptHoverEvents(True)
        self.setPos(x, y)
        self.setTransformOriginPoint(self.w / 2, self.h / 2)
        self._resize = None
        self._start = None
        self._refit()

    def _refit(self, top=None, bottom=None, min_h=None):
        """Canva-style AUTO-HEIGHT: keep the font fixed (max_size) and grow the
        box height to fit the wrapped text at the current width — so narrowing the
        width wraps the text and makes the box TALLER; the font never shrinks.
        `top`/`bottom` anchor that edge while it grows; `min_h` lets a top/bottom
        drag make the frame taller than the text."""
        self.font.setPointSizeF(
            max(self.FONT_MIN, min(self.max_size, self.FONT_MAX)))
        cy = self.y() + self.h / 2
        fm = QFontMetricsF(self.font)
        flags = int(Qt.AlignHCenter) | WRAP_FLAGS
        r = fm.boundingRect(
            QRectF(0, 0, max(8.0, self.w), 1e7), flags, self.text or " ")
        self.prepareGeometryChange()
        self.h = max(8.0, r.height() + 6)
        if min_h:
            self.h = max(self.h, min_h)
        if top is not None:
            self.setY(top)
        elif bottom is not None:
            self.setY(bottom - self.h)
        else:
            self.setY(cy - self.h / 2)  # keep the vertical centre
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

    def _make_gradient(self, rect: QRectF) -> QLinearGradient | None:
        if not self.gradient_colors or len(self.gradient_colors) < 2:
            return None
        angle = math.radians(self.gradient_angle)
        cx, cy = rect.center().x(), rect.center().y()
        hw = rect.width() / 2 * abs(math.cos(angle)) + rect.height() / 2 * abs(math.sin(angle))
        hh = rect.width() / 2 * abs(math.sin(angle)) + rect.height() / 2 * abs(math.cos(angle))
        start = QPointF(cx - hw * math.cos(angle), cy - hh * math.sin(angle))
        end   = QPointF(cx + hw * math.cos(angle), cy + hh * math.sin(angle))
        g = QLinearGradient(start, end)
        for i, c in enumerate(self.gradient_colors):
            g.setColorAt(i / (len(self.gradient_colors) - 1), QColor(c))
        return g

    def _draw_gradient_text(self, p: QPainter, r: QRectF, flags: int) -> None:
        """Draw text with a gradient fill using QImage alpha compositing."""
        g = self._make_gradient(r)
        if g is None:
            return
        iw, ih = max(1, int(r.width())), max(1, int(r.height()))
        # 1. Render text as white-on-transparent mask
        mask = QImage(iw, ih, QImage.Format_ARGB32_Premultiplied)
        mask.fill(Qt.transparent)
        mp = QPainter(mask)
        mp.setFont(self.font)
        mp.setPen(QColor(255, 255, 255, 255))
        mp.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        mp.drawText(QRectF(0, 0, iw, ih), flags, self.text or "")
        mp.end()
        # 2. Fill gradient onto solid image
        grad_img = QImage(iw, ih, QImage.Format_ARGB32_Premultiplied)
        grad_img.fill(Qt.transparent)
        gp = QPainter(grad_img)
        gp.fillRect(0, 0, iw, ih, QBrush(g))
        gp.end()
        # 3. Mask gradient by text alpha (DestinationIn = keep dst where src is opaque)
        gp2 = QPainter(grad_img)
        gp2.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        gp2.drawImage(0, 0, mask)
        gp2.end()
        # 4. Composite onto scene
        p.drawImage(r.topLeft(), grad_img)

    def paint(self, p, opt, widget=None):
        r = QRectF(0, 0, self.w, self.h)
        if self._editing:
            return  # the inline overlay draws the text in our place (WYSIWYG)
        p.save()
        p.setClipRect(r)
        p.setFont(self.font)
        flags = int(self.align) | WRAP_FLAGS
        ec = QColor(self.effect_color)

        # ── Effects: pre-pass (rendered before main text) ─────────────────
        eff = self.effect
        if eff == "background" and self.text:
            bg = QColor(ec); bg.setAlpha(180)
            p.setBrush(bg); p.setPen(Qt.NoPen)
            p.drawRoundedRect(r, 8, 8)
        if eff == "drop" and self.text:
            shadow = QColor(0, 0, 0, 120)
            p.setPen(shadow)
            p.drawText(r.translated(3, 3), flags, self.text)
        if eff == "echo" and self.text:
            echo_c = QColor(self.fill); echo_c.setAlpha(80)
            p.setPen(echo_c)
            p.drawText(r.translated(4, 4), flags, self.text)
        if eff == "glow" and self.text:
            for gx, gy in ((-2,0),(2,0),(0,-2),(0,2),(-1,-1),(1,-1),(-1,1),(1,1)):
                gc = QColor(ec); gc.setAlpha(60)
                p.setPen(gc)
                p.drawText(r.translated(gx, gy), flags, self.text)
        if eff == "neon" and self.text:
            for alpha, offs in ((40, 3), (60, 2), (80, 1)):
                for gx, gy in ((-offs,0),(offs,0),(0,-offs),(0,offs)):
                    gc = QColor(ec); gc.setAlpha(alpha)
                    p.setPen(gc)
                    p.drawText(r.translated(gx, gy), flags, self.text)

        # ── Outline (white halo, or explicit outline effect) ───────────────
        ow = min(12, max(self.outline_w, round(self.font.pointSizeF() * 0.10)))
        if eff == "outline":
            ow = max(ow, 3)
        if ow > 0 and self.text and eff != "hollow":
            p.setPen(self.outline)
            for dx in range(-ow, ow + 1):
                for dy in range(-ow, ow + 1):
                    if (dx or dy) and dx * dx + dy * dy <= ow * ow:
                        p.drawText(r.translated(dx, dy), flags, self.text)

        # ── Main text fill ─────────────────────────────────────────────────
        if eff == "hollow" and self.text:
            # stroke-only: build path and stroke it
            path = QPainterPath()
            fm = QFontMetricsF(self.font)
            lh = fm.lineSpacing()
            total_h = fm.boundingRect(QRectF(0, 0, self.w, 1e7), flags, self.text).height()
            y0 = max(fm.ascent(), (self.h - total_h) / 2 + fm.ascent())
            for line in (self.text or "").split("\n") or [""]:
                lw = fm.horizontalAdvance(line)
                ha = int(self.align) & (int(Qt.AlignLeft)|int(Qt.AlignHCenter)|int(Qt.AlignRight))
                x0 = (self.w - lw)/2 if ha == int(Qt.AlignHCenter) else (
                    self.w - lw if ha == int(Qt.AlignRight) else 0.0)
                path.addText(x0, y0, self.font, line)
                y0 += lh
            pen = QPen(self.fill, max(1, ow)); pen.setJoinStyle(Qt.RoundJoin)
            p.strokePath(path, pen)
        elif self.gradient_colors:
            self._draw_gradient_text(p, r, flags)
        else:
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
        """Canva-style resizing:
        • corners scale the FONT (and width together); height auto-follows;
        • left/right sides change only the WIDTH — the font stays, the text
          wraps, and the box grows TALLER to fit;
        • top/bottom set a manual height (a box taller than its text)."""
        if not self._resize:
            super().mouseMoveEvent(e)
            return
        w0, h0, x0, y0, ms0, sp0 = self._start
        d = e.scenePos() - sp0
        dx, dy = d.x(), d.y()
        k = self._resize
        MIN = 24.0

        if k in ("tl", "tr", "bl", "br"):  # CORNER -> scale font + width
            grow = dx if k in ("br", "tr") else -dx
            scale = max(0.15, (w0 + grow) / w0) if w0 else 1.0
            self.max_size = max(self.FONT_MIN, min(self.FONT_MAX, ms0 * scale))
            self.prepareGeometryChange()
            self.w = max(MIN, w0 * scale)
            if k in ("bl", "tl"):
                self.setX(x0 + (w0 - self.w))
            else:
                self.setX(x0)
            self._refit(top=y0 if k in ("br", "bl") else None,
                        bottom=(y0 + h0) if k in ("tr", "tl") else None)
        elif k == "r":  # SIDE -> width only, font fixed, height auto-grows
            self.prepareGeometryChange()
            self.w = max(MIN, w0 + dx)
            self.setX(x0)
            self._refit(top=y0)
        elif k == "l":
            nw = max(MIN, w0 - dx)
            self.prepareGeometryChange()
            self.setX(x0 + (w0 - nw))
            self.w = nw
            self._refit(top=y0)
        elif k == "b":  # bottom -> taller box (manual min height)
            self._refit(top=y0, min_h=max(MIN, h0 + dy))
        elif k == "t":
            self._refit(bottom=y0 + h0, min_h=max(MIN, h0 - dy))
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
            "kind": "text",
            "n": self.n, "text": self.text, "x": self.x(), "y": self.y(),
            "w": self.w, "h": self.h, "font": self.font.family(),
            "size": self.max_size, "fill": self.fill.name(),
            "outline": self.outline.name(), "outline_w": self.outline_w,
            "bold": self.font.bold(), "italic": self.font.italic(),
            "underline": self.font.underline(), "align": int(self.align),
            "rot": self.rotation(),
            "gradient_colors": self.gradient_colors,
            "gradient_angle": self.gradient_angle,
            "effect": self.effect,
            "effect_color": self.effect_color,
        }


SFX_LIB_DIR = os.path.expanduser("~/ManhwaPrep/sfx_library")
LIB_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _pixmap_to_b64(pix: QPixmap) -> str:
    """Encode a pixmap as a base64 PNG (keeps transparency) for the project file."""
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    pix.save(buf, "PNG")
    buf.close()
    return bytes(ba.toBase64()).decode("ascii")


def _b64_to_pixmap(s: str) -> QPixmap:
    pix = QPixmap()
    pix.loadFromData(QByteArray.fromBase64(s.encode("ascii")), "PNG")
    return pix


def _bgr_to_qpixmap(arr: np.ndarray) -> QPixmap:
    """BGR uint8 array -> QPixmap (detached from the numpy buffer)."""
    h, w = arr.shape[:2]
    rgb = np.ascontiguousarray(arr[:, :, ::-1])
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
    return QPixmap.fromImage(img.copy())


class ImageItem(QGraphicsItem):
    """A pasted / loaded image — an SFX or sticker. Move by dragging the body,
    resize FREELY via the 8 handles (stretch allowed), rotate. Composites over
    the art with full transparency."""

    HANDLE = 11
    EDGE_GRAB = 9.0
    _CURSORS = TextBoxItem._CURSORS

    def __init__(self, pixmap: QPixmap, x, y, w=None, h=None):
        super().__init__()
        self._pix = pixmap
        self.w = float(w) if w else float(max(1, pixmap.width()))
        self.h = float(h) if h else float(max(1, pixmap.height()))
        self.setFlags(
            QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
        )
        self.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
        self.setAcceptHoverEvents(True)
        self.setPos(x, y)
        self.setTransformOriginPoint(self.w / 2, self.h / 2)
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

    def paint(self, p, opt, widget=None):
        p.drawPixmap(QRectF(0, 0, self.w, self.h), self._pix,
                     QRectF(self._pix.rect()))
        if self.isSelected():
            pen = QPen(QColor(0, 150, 255))
            pen.setStyle(Qt.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(0, 0, self.w, self.h))
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
        MIN = 12.0
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
        self.setTransformOriginPoint(self.w / 2, self.h / 2)
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
            "kind": "image", "x": self.x(), "y": self.y(),
            "w": self.w, "h": self.h, "rot": self.rotation(),
            "data": _pixmap_to_b64(self._pix),
        }


class _CanvasView(QGraphicsView):
    """Graphics view with Ctrl+wheel zoom (plain wheel scrolls). In a paint tool
    (blend / erase / paint) a left-drag paints onto the canvas instead of moving
    items; in select mode it behaves normally."""

    BRUSH_TOOLS = ("blend", "erase", "paint", "remove")

    def __init__(self, scene):
        super().__init__(scene)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)  # hover events even with no button down
        # -- speed & smoothness (raster; per-item pixmap cache does the rest) --
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                            | QPainter.SmoothPixmapTransform)
        self.setOptimizationFlag(QGraphicsView.DontSavePainterState, True)
        self.setOptimizationFlag(QGraphicsView.DontAdjustForAntialiasing, True)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.tool = "select"
        self.editor = None
        self._painting = False
        self._brush_pt = None  # scene pos of the brush-size preview ring
        self._box0 = None      # box-remove rubber-band start
        self._box1 = None

    def _xy(self, e):
        p = self.mapToScene(e.position().toPoint())
        return p.x(), p.y()

    def _eff_tool(self, mods):
        """Spring-loaded secondary tool: while ⌘ (Ctrl on Win/Linux) is held over
        the move tool, a drag becomes the box-mask — released, it's the mover
        again. No real tool switch, so it springs back on its own."""
        if self.tool == "select" and (mods & Qt.ControlModifier):
            return "boxremove"
        return self.tool

    def mousePressEvent(self, e):
        if self.editor and e.button() == Qt.LeftButton:
            eff = self._eff_tool(e.modifiers())
            if eff == "boxremove":
                self._box0 = self._box1 = self.mapToScene(e.position().toPoint())
                self.viewport().update()
                e.accept()
                return
            if eff in self.BRUSH_TOOLS:
                self._painting = True
                self.editor._paint_begin(*self._xy(e))
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        # spring cursor hint: crosshair while ⌘ is held over the move tool
        if (self.tool == "select" and self._box0 is None and not self._painting):
            self.viewport().setCursor(
                Qt.CrossCursor if (e.modifiers() & Qt.ControlModifier)
                else Qt.ArrowCursor)
        if self.tool in self.BRUSH_TOOLS:  # show the brush footprint
            self._brush_pt = self.mapToScene(e.position().toPoint())
            self.viewport().update()
        if self._box0 is not None:
            self._box1 = self.mapToScene(e.position().toPoint())
            self.viewport().update()
            e.accept()
            return
        if self._painting and self.editor:
            self.editor._paint_move(*self._xy(e))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._brush_pt = None
        self.viewport().update()
        super().leaveEvent(e)

    def drawForeground(self, p, rect):
        super().drawForeground(p, rect)
        if not self.editor:
            return
        if self._box0 is not None and self._box1 is not None:
            pen = QPen(QColor(255, 0, 0)); pen.setCosmetic(True)
            pen.setStyle(Qt.DashLine); p.setPen(pen)
            p.setBrush(QColor(255, 0, 0, 40))
            p.drawRect(QRectF(self._box0, self._box1).normalized())
            return
        if self.tool in self.BRUSH_TOOLS and self._brush_pt is not None:
            r = self.editor._brush_size / 2.0
            pen = QPen(QColor(0, 0, 0)); pen.setCosmetic(True)
            p.setPen(pen); p.setBrush(Qt.NoBrush)
            p.drawEllipse(self._brush_pt, r, r)
            pen2 = QPen(QColor(255, 255, 255)); pen2.setCosmetic(True)
            pen2.setStyle(Qt.DashLine); p.setPen(pen2)
            p.drawEllipse(self._brush_pt, r, r)

    def mouseReleaseEvent(self, e):
        if self._box0 is not None:
            a, b = self._box0, self._box1 or self._box0
            self._box0 = self._box1 = None
            self.viewport().update()
            if self.editor:
                self.editor._box_remove(a.x(), a.y(), b.x(), b.y())
            e.accept()
            return
        if self._painting:
            self._painting = False
            if self.editor:
                self.editor._paint_end()
            e.accept()
            return
        super().mouseReleaseEvent(e)
        if self.tool == "select" and self.editor:
            self.editor._record_if_changed()  # capture a move/resize for undo

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            self.scale(f, f)
            e.accept()
        else:
            super().wheelEvent(e)
        if self.editor:
            self.editor._position_box_bar()


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
        # Enter / Shift+Enter → insert a newline (multi-line editing).
        # Ctrl/Cmd+Enter or Escape → commit.
        if e.key() == Qt.Key_Escape:
            self._on_done()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            if e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
                self._on_done()
                return
            # plain Enter and Shift+Enter both insert a newline
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


class _GradientSwatch(QPushButton):
    def __init__(self, colors, callback):
        super().__init__()
        self.colors = colors
        self.setFixedSize(64, 40)
        self.setFlat(True)
        self.setToolTip(" → ".join(colors))
        self.clicked.connect(callback)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("border-radius:6px; border:1px solid #333342;")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        g = QLinearGradient(r.left(), 0, r.right(), 0)
        for i, c in enumerate(self.colors):
            g.setColorAt(i / (len(self.colors) - 1), QColor(c))
        path = QPainterPath()
        path.addRoundedRect(QRectF(r), 5, 5)
        p.fillPath(path, QBrush(g))
        p.end()


class _GradientPanel(QWidget):
    """Collapsible gradient preset picker with angle slider."""
    gradient_picked = None  # set by TypesetEditor after construction

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # Angle row
        angle_row = QHBoxLayout()
        angle_row.addWidget(QLabel("Angle"))
        self._angle_slider = QSlider(Qt.Horizontal)
        self._angle_slider.setRange(0, 360)
        self._angle_slider.setValue(90)
        self._angle_lbl = QLabel("90°")
        self._angle_lbl.setFixedWidth(34)
        self._angle_slider.valueChanged.connect(
            lambda v: self._angle_lbl.setText(f"{v}°"))
        angle_row.addWidget(self._angle_slider, 1)
        angle_row.addWidget(self._angle_lbl)
        lay.addLayout(angle_row)

        # Clear button
        clear_btn = QPushButton("✕ Clear gradient")
        clear_btn.clicked.connect(lambda: self.gradient_picked and
                                  self.gradient_picked(None, 90))
        lay.addWidget(clear_btn)

        # Scrollable preset grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedHeight(260)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(4)
        inner_lay.setContentsMargins(0, 0, 4, 0)

        self._sections = {}
        for cat, presets in GRADIENT_PRESETS.items():
            # Header button (expand/collapse)
            hdr = QPushButton(f"▾  {cat}")
            hdr.setStyleSheet(
                f"QPushButton{{background:{_CARD_BG};color:{_TEXT_MAIN};"
                f"border-left:3px solid {_ACCENT};border-radius:4px;"
                f"padding:4px 8px;text-align:left;font-weight:bold;}}"
                f"QPushButton:hover{{background:#363645;}}")
            grid_w = QWidget()
            grid_lay = QHBoxLayout(grid_w)
            grid_lay.setSpacing(4)
            grid_lay.setContentsMargins(0, 2, 0, 2)
            # 3-column flow
            col_widgets = [QVBoxLayout() for _ in range(3)]
            for j, colors in enumerate(presets):
                def _cb(c=colors):
                    if self.gradient_picked:
                        self.gradient_picked(c, self._angle_slider.value())
                swatch = _GradientSwatch(colors, _cb)
                col_widgets[j % 3].addWidget(swatch)
            for cw in col_widgets:
                cw.addStretch()
                w = QWidget(); w.setLayout(cw)
                grid_lay.addWidget(w)

            self._sections[cat] = grid_w
            hdr.clicked.connect(lambda _, gw=grid_w, b=hdr: (
                gw.setVisible(not gw.isVisible()),
                b.setText(("▾  " if not gw.isVisible() else "▸  ") + b.text()[3:])
            ))
            inner_lay.addWidget(hdr)
            inner_lay.addWidget(grid_w)

        inner_lay.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll)


class _EffectsPanel(QWidget):
    """Floating effects picker panel (frameless popup)."""
    effect_picked = None  # set by TypesetEditor
    effect_color_picked = None

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget{{background:{_PANEL_BG};border:1px solid {_BORDER};"
            f"border-radius:12px;}}"
            f"QLabel{{color:{_TEXT_DIM};background:transparent;}}"
            f"QPushButton{{background:{_CARD_BG};color:{_TEXT_MAIN};"
            f"border:1px solid {_BORDER};border-radius:8px;padding:5px 10px;}}"
            f"QPushButton:hover{{background:#363645;border-color:{_ACCENT};}}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        # Title row
        title_row = QHBoxLayout()
        title_lbl = QLabel("✨ Effects")
        title_lbl.setStyleSheet(f"color:{_TEXT_MAIN};font-weight:bold;font-size:13px;")
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{_TEXT_DIM};}}"
            f"QPushButton:hover{{color:white;}}")
        close_btn.clicked.connect(self.hide)
        title_row.addWidget(title_lbl, 1)
        title_row.addWidget(close_btn)
        lay.addLayout(title_row)

        # Effect tiles 3×3
        EFFECTS = [
            ("none",       "None",       "#888888"),
            ("drop",       "Drop",       "#333333"),
            ("glow",       "Glow",       "#7c3aed"),
            ("echo",       "Echo",       "#555555"),
            ("outline",    "Outline",    "#1d4ed8"),
            ("background", "Background", "#6d28d9"),
            ("hollow",     "Hollow",     "#059669"),
            ("neon",       "Neon",       "#db2777"),
        ]
        grid_lay = QHBoxLayout()
        grid_lay.setSpacing(6)
        cols = [QVBoxLayout() for _ in range(3)]
        for i, (key, label, preview_color) in enumerate(EFFECTS):
            tile = self._make_tile(key, label, preview_color)
            cols[i % 3].addWidget(tile)
        for c in cols:
            c.addStretch()
            w = QWidget(); w.setLayout(c)
            grid_lay.addWidget(w)
        lay.addLayout(grid_lay)

        # Effect color row
        ec_row = QHBoxLayout()
        ec_row.addWidget(QLabel("Effect color"))
        self._ec_btn = QPushButton()
        self._ec_btn.setFixedSize(32, 24)
        self._ec_btn.setStyleSheet(
            "background:#000000;border-radius:4px;border:1px solid #333342;")
        self._ec_btn.clicked.connect(self._pick_ec)
        ec_row.addWidget(self._ec_btn)
        ec_row.addStretch()
        lay.addLayout(ec_row)

        self._ec_color = "#000000"
        self.resize(280, 360)

    def _make_tile(self, key, label, preview_color):
        btn = QPushButton()
        btn.setFixedSize(80, 80)
        btn.setStyleSheet(
            f"QPushButton{{background:{_CARD_BG};border:1px solid {_BORDER};"
            f"border-radius:10px;}}"
            f"QPushButton:hover{{border-color:{_ACCENT};background:#363645;}}"
            f"QPushButton:pressed{{background:{_ACCENT};}}")
        vl = QVBoxLayout(btn)
        vl.setSpacing(2)
        vl.setContentsMargins(4, 8, 4, 4)
        ltr = QLabel("ក")
        ltr.setAlignment(Qt.AlignCenter)
        ltr.setStyleSheet(
            f"color:{preview_color};font-size:28px;font-weight:bold;"
            "background:transparent;border:none;")
        name_lbl = QLabel(label)
        name_lbl.setAlignment(Qt.AlignCenter)
        name_lbl.setStyleSheet(
            f"color:{_TEXT_DIM};font-size:10px;background:transparent;border:none;")
        vl.addWidget(ltr, 1)
        vl.addWidget(name_lbl)
        btn.clicked.connect(lambda _, k=key: self._on_effect(k))
        return btn

    def _on_effect(self, key):
        if self.effect_picked:
            self.effect_picked(key)
        self.hide()

    def _pick_ec(self):
        c = QColorDialog.getColor(QColor(self._ec_color), self, "Effect colour")
        if c.isValid():
            self._ec_color = c.name()
            self._ec_btn.setStyleSheet(
                f"background:{self._ec_color};border-radius:4px;border:1px solid #333342;")
            if self.effect_color_picked:
                self.effect_color_picked(self._ec_color)

    def set_color(self, hex_color):
        self._ec_color = hex_color
        self._ec_btn.setStyleSheet(
            f"background:{self._ec_color};border-radius:4px;border:1px solid #333342;")


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
        self.images: list[ImageItem] = []
        self._inline_proxy = None
        self._inline_item = None
        self._post_groups = []  # Claude's story grouping: [(first_n, last_n), ...]
        self._project_name = None  # user-given name shown in the home library
        # touch-up painting (blend / erase / paint) + undo history
        self._tool = "select"
        self._brush_size = 28
        self._paint_color = QColor(0, 0, 0)
        self._orig_np = None   # pristine canvas (eraser restores from this)
        self._work_np = None   # working canvas (edits are baked here)
        self._bg_pixmap = None
        self._bg_item = None
        self._last_paint = None
        self._remove_mask = None  # accumulates the removal-brush highlight
        self._hl_pixmap = None
        self._hl_item = None
        self._history = []
        self._hist_idx = -1

        self.setWindowTitle(f"Typeset — {self.layout.get('chapter', '')}")
        self.resize(1200, 860)
        root = QHBoxLayout(self)

        self.scene = QGraphicsScene()
        self.view = _CanvasView(self.scene)
        self.view.editor = self
        self.view.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                                 | QPainter.SmoothPixmapTransform)
        self.view.setStyleSheet("QGraphicsView { background: #0f0f14; border: none; }")
        self.scene.selectionChanged.connect(self._sync_panel)
        root.addWidget(self.view, 4)

        root.addWidget(self._build_panel(), 0)
        self._build_box_bar()  # floating quick-controls over the selected box
        self.scene.changed.connect(self._position_box_bar)
        self.view.horizontalScrollBar().valueChanged.connect(self._position_box_bar)
        self.view.verticalScrollBar().valueChanged.connect(self._position_box_bar)
        self._load_project()  # offer to resume a saved project (sets seg_idx etc.)
        self._load_segment(self.seg_idx)
        self._register_recent()  # show this chapter on the home screen

    def _build_box_bar(self):
        """A small floating toolbar that hovers above the selected text box with
        quick font sizes and 1/2/3-line options."""
        bar = QWidget(self.view.viewport())
        bar.setStyleSheet(
            f"QWidget{{background:rgba(18,18,24,0.96);border:1px solid {_BORDER};"
            f"border-radius:12px;}}"
            f"QToolButton{{color:{_TEXT_MAIN};background:{_CARD_BG};border:1px solid {_BORDER};"
            f"border-radius:6px;padding:3px 8px;font-size:12px;font-weight:600;min-width:32px;}}"
            f"QToolButton:hover{{background:#363645;border-color:{_ACCENT};}}"
            f"QToolButton:pressed{{background:{_ACCENT};color:white;}}"
            f"QLabel{{color:{_TEXT_DIM};background:transparent;border:none;}}")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(18); shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 140))
        bar.setGraphicsEffect(shadow)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(1)
        for sz in (25, 30, 35, 40):
            b = QToolButton(); b.setText(str(sz))
            b.setToolTip(f"Font size {sz}")
            b.clicked.connect(lambda _=False, s=sz: self._set_size(s))
            lay.addWidget(b)
        lay.addWidget(QLabel("│"))
        for n, lbl in ((1, "1·line"), (2, "2·line"), (3, "3·line")):
            b = QToolButton(); b.setText(lbl)
            b.setToolTip(f"Fit the text on {n} line(s)")
            b.clicked.connect(lambda _=False, k=n: self._set_lines(k))
            lay.addWidget(b)
        bar.hide()
        self._box_bar = bar

    def _position_box_bar(self, *args):
        bar = getattr(self, "_box_bar", None)
        if bar is None:
            return
        try:
            sel = self._selected()
        except RuntimeError:
            return  # scene torn down (window closing) — ignore
        if len(sel) != 1 or self._tool != "select":
            bar.hide()
            return
        it = sel[0]
        bar.adjustSize()
        vp = self.view.mapFromScene(QRectF(it.x(), it.y(), it.w, it.h).topLeft()
                                    + QPointF(it.w / 2, 0))
        bw, bh = bar.width(), bar.height()
        x = max(2, min(int(vp.x() - bw / 2),
                       self.view.viewport().width() - bw - 2))
        y = int(vp.y()) - bh - 8
        if y < 2:
            y = int(vp.y()) + 8  # no room above -> tuck just below the top edge
        bar.move(x, y)
        bar.show()
        bar.raise_()

    def _register_recent(self):
        try:
            from . import recents
            thumb = (os.path.join(self.base, self.segments[0]["image"])
                     if self.segments else "")
            name = (self._project_name or self.layout.get("chapter", "")
                    or os.path.basename(self.base))
            recents.add_recent(self.layout_path, name, thumb)
        except Exception:
            pass

    # -- side panel ----------------------------------------------------
    @staticmethod
    def _hline():
        ln = QFrame()
        ln.setFrameShape(QFrame.HLine)
        ln.setStyleSheet("color:#ddd;")
        return ln

    def _tool_button(self, glyph, name, tip):
        b = QToolButton()
        b.setText(glyph)
        b.setCheckable(True)
        b.setToolTip(tip)
        b.setFixedSize(40, 36)
        b.setStyleSheet("QToolButton{font-size:18px;border:1px solid #ccc;"
                        "border-radius:6px;}"
                        "QToolButton:checked{background:#2d7ff9;color:white;"
                        "border:1px solid #2d7ff9;}")
        b.clicked.connect(lambda: self._select_tool(name))
        self._tool_group.addButton(b)
        self._tool_buttons[name] = b
        return b

    def _build_panel(self):
        col = QVBoxLayout()
        col.setSpacing(8)

        # canvas navigation + undo/redo (kept together so they always fit)
        nav = QHBoxLayout()
        nav.setSpacing(4)
        self.prev = QToolButton(); self.prev.setText("‹"); self.prev.setFixedWidth(30)
        self.next = QToolButton(); self.next.setText("›"); self.next.setFixedWidth(30)
        self.prev.clicked.connect(lambda: self._go(-1))
        self.next.clicked.connect(lambda: self._go(1))
        self.seg_lbl = QLabel("")
        self.undo_btn = QToolButton(); self.undo_btn.setText("↶")
        self.undo_btn.setFixedWidth(30); self.undo_btn.setToolTip("Undo (⌘Z)")
        self.undo_btn.clicked.connect(self._undo)
        self.redo_btn = QToolButton(); self.redo_btn.setText("↷")
        self.redo_btn.setFixedWidth(30); self.redo_btn.setToolTip("Redo (⇧⌘Z)")
        self.redo_btn.clicked.connect(self._redo)
        nav.addWidget(self.prev)
        nav.addWidget(self.seg_lbl, 1, Qt.AlignCenter)
        nav.addWidget(self.next)
        nav.addSpacing(8)
        nav.addWidget(self.undo_btn)
        nav.addWidget(self.redo_btn)
        col.addLayout(nav)

        # tool toolbar (icons, not a dropdown)
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_buttons = {}
        bar = QHBoxLayout()
        bar.setSpacing(3)
        bar.addWidget(self._tool_button("⤢", "select", "Select / move (V)"))
        bar.addWidget(self._tool_button("💧", "blend", "Blend / smudge"))
        bar.addWidget(self._tool_button("🧽", "erase", "Erase — restores original art"))
        bar.addWidget(self._tool_button("🖌", "paint", "Paint a colour"))
        bar.addWidget(self._tool_button(
            "🩹", "remove",
            "Remove brush — paint over a watermark / SFX to erase it (rebuilds "
            "the background)"))
        bar.addWidget(self._tool_button(
            "⬚", "boxremove",
            "Box detect-remove — drag a box over a watermark; only the mark "
            "inside is erased, the art is kept"))
        bar.addStretch(1)
        col.addLayout(bar)
        self._tool_buttons["select"].setChecked(True)

        # brush group — only visible while a paint tool is active
        self.brush_group = QGroupBox("Brush")
        bg = QVBoxLayout(self.brush_group)
        brow = QHBoxLayout()
        brow.addWidget(QLabel("Size"))
        self.brush_spin = QSpinBox()
        self.brush_spin.setRange(3, 400)
        self.brush_spin.setSuffix(" px")
        self.brush_spin.setValue(self._brush_size)
        self.brush_spin.valueChanged.connect(self._brush_changed)
        brow.addWidget(self.brush_spin, 1)
        self.paint_color_btn = QPushButton("Colour")
        self.paint_color_btn.clicked.connect(self._pick_paint_color)
        brow.addWidget(self.paint_color_btn)
        bg.addLayout(brow)
        # Remove tool: highlight, then commit/clear.
        erow = QHBoxLayout()
        self.erase_hl_btn = QPushButton("🩹 Erase highlighted")
        self.erase_hl_btn.setToolTip("Inpaint everything you've highlighted")
        self.erase_hl_btn.clicked.connect(self._erase_highlight)
        self.erase_hl_btn.setEnabled(False)
        self.clear_hl_btn = QPushButton("Clear")
        self.clear_hl_btn.clicked.connect(self._clear_highlight)
        self.clear_hl_btn.setEnabled(False)
        erow.addWidget(self.erase_hl_btn, 1)
        erow.addWidget(self.clear_hl_btn)
        bg.addLayout(erow)
        self.brush_group.setVisible(False)
        col.addWidget(self.brush_group)

        # text group — only visible when a text box is selected
        self.text_group = QGroupBox("Text")
        tg = QVBoxLayout(self.text_group)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setFixedHeight(70)
        self.text_edit.setFont(QFont(khmer_font(), 15))
        self.text_edit.textChanged.connect(self._text_changed)
        tg.addWidget(self.text_edit)
        self.recent_fonts = QComboBox()
        self.recent_fonts.setToolTip("Recently used fonts")
        self.recent_fonts.activated.connect(self._recent_font_picked)
        tg.addWidget(self.recent_fonts)
        self.fontbox = QFontComboBox()
        self.fontbox.setCurrentFont(QFont(khmer_font()))
        self.fontbox.currentFontChanged.connect(self._font_changed)
        tg.addWidget(self.fontbox)
        self._refresh_recent_fonts()
        self.apply_font_all_btn = QPushButton("Apply this font to ALL canvases")
        self.apply_font_all_btn.setToolTip(
            "Set every text box on every canvas to the font above")
        self.apply_font_all_btn.clicked.connect(self._apply_font_all)
        tg.addWidget(self.apply_font_all_btn)
        self.default_font_btn = QPushButton("⭐ Set as default font")
        self.default_font_btn.setToolTip(
            "Save this font as the default for all new text boxes")
        self.default_font_btn.clicked.connect(self._save_default_font)
        tg.addWidget(self.default_font_btn)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Size"))
        self.size = QSpinBox(); self.size.setRange(6, 400); self.size.setValue(24)
        self.size.valueChanged.connect(self._size_changed)
        srow.addWidget(self.size)
        srow.addWidget(QLabel("Outline"))
        self.ow = QSpinBox(); self.ow.setRange(0, 12); self.ow.setValue(3)
        self.ow.valueChanged.connect(self._ow_changed)
        srow.addWidget(self.ow)
        tg.addLayout(srow)
        frow = QHBoxLayout()
        self.bold_btn = self._fmt_toggle("B", "font-weight:bold;", self._toggle_bold)
        self.italic_btn = self._fmt_toggle("I", "font-style:italic;", self._toggle_italic)
        self.underline_btn = self._fmt_toggle(
            "U", "text-decoration:underline;", self._toggle_underline)
        frow.addWidget(self.bold_btn)
        frow.addWidget(self.italic_btn)
        frow.addWidget(self.underline_btn)
        self.align_combo = QComboBox()
        self.align_combo.addItems(["⬅ Left", "⬌ Center", "➡ Right"])
        self.align_combo.setCurrentIndex(1)
        self.align_combo.currentIndexChanged.connect(self._align_changed)
        frow.addWidget(self.align_combo, 1)
        tg.addLayout(frow)
        crow = QHBoxLayout()
        self.fill_btn = QPushButton("Text colour")
        self.fill_btn.clicked.connect(self._pick_fill)
        self.outline_btn = QPushButton("Outline colour")
        self.outline_btn.clicked.connect(self._pick_outline)
        crow.addWidget(self.fill_btn)
        crow.addWidget(self.outline_btn)
        tg.addLayout(crow)

        # Effects button
        self._effects_panel = _EffectsPanel(self)
        self._effects_panel.effect_picked = self._apply_effect
        self._effects_panel.effect_color_picked = self._set_effect_color
        effects_btn = QPushButton("✨ Effects")
        effects_btn.clicked.connect(lambda: self._show_effects_panel(effects_btn))
        tg.addWidget(effects_btn)

        # Gradient section header (collapsible)
        grad_hdr = QPushButton("🎨 Gradient  ▾")
        grad_hdr.setStyleSheet(
            f"QPushButton{{background:{_CARD_BG};color:{_TEXT_MAIN};"
            f"border-left:3px solid {_ACCENT2};border-radius:6px;"
            f"padding:5px 10px;text-align:left;font-weight:bold;}}"
            f"QPushButton:hover{{background:#363645;}}")
        self._grad_panel = _GradientPanel()
        self._grad_panel.gradient_picked = self._apply_gradient
        grad_hdr.clicked.connect(lambda: (
            self._grad_panel.setVisible(not self._grad_panel.isVisible()),
            grad_hdr.setText(
                ("🎨 Gradient  ▾" if self._grad_panel.isVisible()
                 else "🎨 Gradient  ▸"))
        ))
        tg.addWidget(grad_hdr)
        tg.addWidget(self._grad_panel)

        rrow = QHBoxLayout()
        rrow.addWidget(QLabel("Rotate"))
        self.rot = QSpinBox(); self.rot.setRange(-180, 180); self.rot.setSuffix("°")
        self.rot.valueChanged.connect(self._rot_changed)
        rrow.addWidget(self.rot)
        rrow.addStretch(1)
        tg.addLayout(rrow)
        self.text_group.setVisible(False)
        col.addWidget(self.text_group)

        # insert / Khmer workflow
        ins = QGroupBox("Insert")
        ig = QVBoxLayout(ins)
        arow = QHBoxLayout()
        add_btn = QPushButton("➕ Text box"); add_btn.clicked.connect(self._add_box)
        del_btn = QPushButton("🗑 Delete"); del_btn.clicked.connect(self._delete_selected)
        arow.addWidget(add_btn); arow.addWidget(del_btn)
        ig.addLayout(arow)
        img_btn = QPushButton("🖼 Add image  (or ⌘V)")
        img_btn.clicked.connect(self._add_image)
        ig.addWidget(img_btn)
        krow = QHBoxLayout()
        self.copy_btn = QPushButton("1️⃣ Copy for Claude")
        self.copy_btn.clicked.connect(self._copy_for_claude)
        self.paste_btn = QPushButton("2️⃣ Paste Khmer")
        self.paste_btn.clicked.connect(self._paste)
        krow.addWidget(self.copy_btn); krow.addWidget(self.paste_btn)
        ig.addLayout(krow)
        ig.addWidget(QLabel("SFX library — click to place, right-click to delete:"))
        self.lib = QListWidget()
        self.lib.setViewMode(QListWidget.IconMode)
        self.lib.setIconSize(QSize(52, 52))
        self.lib.setResizeMode(QListWidget.Adjust)
        self.lib.setMovement(QListWidget.Static)
        self.lib.setSpacing(4)
        self.lib.setFixedHeight(120)
        self.lib.itemClicked.connect(self._lib_clicked)
        self.lib.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lib.customContextMenuRequested.connect(self._lib_menu)
        ig.addWidget(self.lib)
        lib_up = QPushButton("⬆ Upload SFX…")
        lib_up.clicked.connect(self._upload_sfx)
        ig.addWidget(lib_up)
        self._refresh_library()
        col.addWidget(ins)

        # export
        exp = QGroupBox("Export")
        eg = QVBoxLayout(exp)
        self.export_btn = QPushButton("💾 Export this canvas")
        self.export_btn.clicked.connect(self._export)
        eg.addWidget(self.export_btn)
        self.export_all_btn = QPushButton("Export ALL canvases")
        self.export_all_btn.clicked.connect(self._export_all)
        eg.addWidget(self.export_all_btn)
        self.pdf_btn = QPushButton("📄 Save as one PDF")
        self.pdf_btn.setToolTip("Combine every canvas into a single PDF")
        self.pdf_btn.clicked.connect(self._export_pdf)
        eg.addWidget(self.pdf_btn)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("FB split"))
        self.split_mode = QComboBox()
        self.split_mode.addItem("Auto (story → heuristic)", "auto")
        self.split_mode.addItem("Heuristic beats only", "heuristic")
        self.split_mode.addItem("Visual gutters only", "visual")
        self.split_mode.setToolTip(
            "Auto: use Claude's pasted POSTS grouping, else fall back to the "
            "heuristic.\nHeuristic: ignore any grouping and cut at sentence ends / "
            "scene gaps.\nVisual: ignore the story, cut only by gutters + size.")
        srow.addWidget(self.split_mode, 1)
        eg.addLayout(srow)
        fbrow = QHBoxLayout()
        self.fb_btn = QPushButton("✂️ FB panels")
        self.fb_btn.setToolTip(
            "Slice this canvas into Facebook-sized panels, cutting only at safe "
            "gutters — never through a text box or the middle of a panel.")
        self.fb_btn.clicked.connect(self._export_fb)
        self.fb_all_btn = QPushButton("✂️ FB (all)")
        self.fb_all_btn.clicked.connect(self._export_fb_all)
        fbrow.addWidget(self.fb_btn); fbrow.addWidget(self.fb_all_btn)
        eg.addLayout(fbrow)
        save = QPushButton("Save project")
        save.clicked.connect(self._save)
        eg.addWidget(save)
        col.addWidget(exp)

        col.addStretch(1)

        inner = QWidget()
        inner.setLayout(col)
        inner.setStyleSheet(_SIDEBAR_QSS)
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(340)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"QScrollArea{{background:{_DARK_BG};border:none;}}")
        return scroll

    def _fmt_toggle(self, label, style, slot):
        b = QPushButton(label)
        b.setCheckable(True)
        b.setFixedWidth(32)
        b.setStyleSheet(style)
        b.clicked.connect(slot)
        return b

    # -- segment handling ----------------------------------------------
    def _commit_items(self):
        if self.segments:
            seg = self.segments[self.seg_idx]
            seg["_state"] = (
                [it.to_dict() for it in self.items]
                + [im.to_dict() for im in self.images]
            )
            # keep the painted canvas only when it actually differs from the art
            if (self._work_np is not None and self._orig_np is not None
                    and not np.array_equal(self._work_np, self._orig_np)):
                seg["_work_np"] = self._work_np
            else:
                seg.pop("_work_np", None)

    def _go(self, d):
        self._commit_items()
        self.seg_idx = max(0, min(len(self.segments) - 1, self.seg_idx + d))
        self._load_segment(self.seg_idx)

    def _rebuild_from_state(self, state):
        """(Re)build the text + image items from a state list, replacing any
        current ones. Accepts both base64 ('data') and in-memory ('pix') images
        so it serves project-load AND undo snapshots."""
        for it in self.items + self.images:
            self.scene.removeItem(it)
        self.items = []
        self.images = []
        for d in state:
            if d.get("kind") == "image":
                pix = d["pix"] if "pix" in d else _b64_to_pixmap(d["data"])
                im = ImageItem(pix, d["x"], d["y"], d["w"], d["h"])
                self.scene.addItem(im)
                if d.get("rot"):
                    im.setTransformOriginPoint(im.w / 2, im.h / 2)
                    im.setRotation(d["rot"])
                self.images.append(im)
                continue
            it = TextBoxItem(d["n"], d["text"], d["x"], d["y"], d["w"], d["h"])
            it.font = QFont(d["font"])
            it.max_size = float(d["size"])
            it.font.setBold(d.get("bold", False))
            it.font.setItalic(d.get("italic", False))
            it.font.setUnderline(d.get("underline", False))
            it.fill = QColor(d["fill"])
            it.outline = QColor(d["outline"])
            it.outline_w = d["outline_w"]
            if "align" in d:
                it.align = Qt.AlignmentFlag(d["align"])
            it.gradient_colors = d.get("gradient_colors")
            it.gradient_angle = d.get("gradient_angle", 90.0)
            it.effect = d.get("effect", "none")
            it.effect_color = d.get("effect_color", "#000000")
            self.scene.addItem(it)
            it._refit()
            if d.get("rot"):
                it.setTransformOriginPoint(it.w / 2, it.h / 2)
                it.setRotation(d["rot"])
            self.items.append(it)
        for it in self.items:
            it.on_edit = self._start_inline_edit

    def _load_segment(self, idx):
        if not self.segments:
            return
        seg = self.segments[idx]
        self.scene.clear()
        self.items = []
        self.images = []
        # working raster: edits (blend/paint) bake here; eraser restores _orig_np.
        self._orig_np = cv2.imread(os.path.join(self.base, seg["image"]))
        if self._orig_np is None:
            self._orig_np = np.full((int(seg["height"]), int(seg["width"]), 3),
                                    245, np.uint8)
        cached = seg.get("_work_np")
        self._work_np = cached.copy() if cached is not None else self._orig_np.copy()
        self._bg_pixmap = _bgr_to_qpixmap(self._work_np)
        self._bg_item = QGraphicsPixmapItem(self._bg_pixmap)
        self._bg_item.setZValue(-1)
        self.scene.addItem(self._bg_item)
        self.scene.setSceneRect(0, 0, seg["width"], seg["height"])
        # removal-brush highlight overlay (red marks-to-erase, above the art)
        self._remove_mask = None
        self._hl_pixmap = QPixmap(int(seg["width"]), int(seg["height"]))
        self._hl_pixmap.fill(Qt.transparent)
        self._hl_item = QGraphicsPixmapItem(self._hl_pixmap)
        self._hl_item.setZValue(-0.5)
        self.scene.addItem(self._hl_item)

        state = seg.get("_state")
        if state:
            self._rebuild_from_state(state)
        else:
            for b in seg["items"]:
                x, y, w, h = b["bbox"]
                it = TextBoxItem(b["n"], b["src"], x, y, w, h)
                it.on_edit = self._start_inline_edit
                self.scene.addItem(it)
                self.items.append(it)
        self.seg_lbl.setText(f"Canvas {idx + 1}/{len(self.segments)}")
        self.prev.setEnabled(idx > 0)
        self.next.setEnabled(idx < len(self.segments) - 1)
        self._reset_history()
        # start each canvas at the TOP (centred horizontally), not wherever the
        # previous canvas was scrolled to.
        vbar = self.view.verticalScrollBar()
        hbar = self.view.horizontalScrollBar()
        vbar.setValue(vbar.minimum())
        hbar.setValue((hbar.minimum() + hbar.maximum()) // 2)

    # -- editing -------------------------------------------------------
    def _selected(self):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, TextBoxItem)]
        return sel

    def _sync_panel(self):
        sel = self._selected()
        self.text_group.setVisible(bool(sel))  # only show text controls in context
        self._position_box_bar()  # float the quick-controls over the selected box
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
        self._record_if_changed()

    def _refresh_recent_fonts(self):
        from . import recents
        self.recent_fonts.blockSignals(True)
        self.recent_fonts.clear()
        self.recent_fonts.addItem("Recent fonts…")
        for f in recents.list_fonts():
            self.recent_fonts.addItem(f)
        self.recent_fonts.setCurrentIndex(0)
        self.recent_fonts.blockSignals(False)

    def _recent_font_picked(self, idx):
        if idx <= 0:
            return
        fam = self.recent_fonts.itemText(idx)
        self.fontbox.setCurrentFont(QFont(fam))  # triggers _font_changed

    def _remember_font(self, fam):
        try:
            from . import recents
            recents.add_font(fam)
            self._refresh_recent_fonts()
        except Exception:
            pass

    def _font_changed(self, font):
        for it in self._selected():
            nf = QFont(font.family())
            nf.setPointSizeF(it.font.pointSizeF())
            it.font = nf
            it._refit()
            it.update()
        if self._selected():
            self._remember_font(font.family())
        self._record_if_changed()

    def _apply_font_all(self):
        """Set the font (the one in the picker) on every text box across every
        canvas — keeping each box's size and bold/italic/underline."""
        fam = self.fontbox.currentFont().family()
        self._commit_items()
        cur = self.seg_idx
        count = 0
        for i in range(len(self.segments)):
            self.seg_idx = i
            self._load_segment(i)
            for it in self.items:
                nf = QFont(fam)
                nf.setBold(it.font.bold())
                nf.setItalic(it.font.italic())
                nf.setUnderline(it.font.underline())
                it.font = nf
                it._refit()
                it.update()
                count += 1
            if self.items:
                self._commit_items()
        self.seg_idx = cur
        self._load_segment(cur)
        self._remember_font(fam)
        QMessageBox.information(
            self, "Font applied",
            f"Applied “{fam}” to {count} text box(es) across "
            f"{len(self.segments)} canvas(es).")

    def _save_default_font(self):
        """Save the currently-selected font as the default for all new boxes."""
        global _KHMER_FONT
        fam = self.fontbox.currentFont().family()
        path = os.path.expanduser("~/ManhwaPrep/default_font.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w") as f:
                f.write(fam)
            _KHMER_FONT = None  # clear memo so next box picks up the new default
            QMessageBox.information(self, "Default font saved",
                                    f'"{fam}" is now the default font for new boxes.')
        except Exception as exc:
            QMessageBox.warning(self, "Could not save default", str(exc))

    def _size_changed(self, v):
        # Set the font size; the box height auto-grows to fit (Canva-style).
        for it in self._selected():
            it.max_size = float(v)
            it._refit()
            it.update()
        self._record_if_changed()

    def _set_size(self, s):
        """Quick-size preset button: apply size s to the selected box(es) and
        reflect it in the Size spin box."""
        if not self._selected():
            return
        self.size.blockSignals(True)
        self.size.setValue(s)
        self.size.blockSignals(False)
        self._size_changed(s)

    def _set_lines(self, n):
        """Reflow the selected box(es) onto exactly n lines by widening/narrowing
        the box (font stays the same; the box auto-heights)."""
        for it in self._selected():
            fm = QFontMetricsF(it.font)
            text = it.text or " "
            line_h = fm.height()
            single_w = fm.horizontalAdvance(text) + 16
            if n <= 1:
                w = single_w
            else:
                # Binary-search: find narrowest width that still wraps to <= n lines
                # using pixel height (reliable for Khmer which has no word-spaces).
                lo, hi = 20.0, max(20.0, single_w)
                for _ in range(24):
                    mid = (lo + hi) / 2
                    r = fm.boundingRect(
                        QRectF(0, 0, mid, 1e7),
                        int(Qt.AlignHCenter) | WRAP_FLAGS, text)
                    actual = r.height() / line_h if line_h > 0 else 1
                    if actual <= n + 0.3:
                        hi = mid
                    else:
                        lo = mid
                w = hi
            it.prepareGeometryChange()
            it.w = max(20.0, w)
            it._refit(top=it.y())
            it.update()
        self._record_if_changed()
        self._position_box_bar()

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
        # Halo in the outline colour so the text stays visible while typing even
        # on a black panel (mirrors the box's final outline).
        glow = QGraphicsDropShadowEffect()
        glow.setOffset(0, 0)
        glow.setBlurRadius(16)
        glow.setColor(item.outline if item.outline else QColor(255, 255, 255))
        proxy.setGraphicsEffect(glow)
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
        self._record_if_changed()

    def _ow_changed(self, v):
        for it in self._selected():
            it.prepareGeometryChange()
            it.outline_w = v
            it.update()
        self._record_if_changed()

    def _add_box(self):
        center = self.view.mapToScene(self.view.viewport().rect().center())
        n = max([it.n for it in self.items], default=0) + 1
        it = TextBoxItem(n, "text", center.x() - 120, center.y() - 40, 240, 80)
        it.on_edit = self._start_inline_edit
        self.scene.addItem(it)
        self.items.append(it)
        self.scene.clearSelection()
        it.setSelected(True)
        self._record_if_changed()

    def _delete_selected(self):
        for it in list(self.scene.selectedItems()):
            self.scene.removeItem(it)
            if it in self.items:
                self.items.remove(it)
            if it in self.images:
                self.images.remove(it)
        self._record_if_changed()

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Undo):  # Cmd/Ctrl+Z
            self._undo()
            return
        if e.matches(QKeySequence.Redo):  # Cmd+Shift+Z / Ctrl+Y
            self._redo()
            return
        if e.matches(QKeySequence.Paste) or (
            e.key() == Qt.Key_V and e.modifiers() & Qt.ControlModifier
        ):
            self._paste_clipboard_image()
            return
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.scene.selectedItems():
            self._delete_selected()
            return
        super().keyPressEvent(e)

    # -- images (SFX / stickers) ---------------------------------------
    def _place_image(self, pixmap: QPixmap):
        """Drop a pixmap on the canvas, centred in the current view and scaled
        down if it's bigger than the canvas. Selected and ready to drag."""
        if pixmap.isNull():
            return None
        w, h = float(pixmap.width()), float(pixmap.height())
        limit = max(64.0, self.scene.width() * 0.6)  # don't dwarf the page
        if w > limit:
            h *= limit / w
            w = limit
        center = self.view.mapToScene(self.view.viewport().rect().center())
        im = ImageItem(pixmap, center.x() - w / 2, center.y() - h / 2, w, h)
        self.scene.addItem(im)
        self.images.append(im)
        self.scene.clearSelection()
        im.setSelected(True)
        self._record_if_changed()
        return im

    def _add_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Add image (SFX / sticker)", SFX_LIB_DIR,
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if path:
            self._place_image(QPixmap(path))

    def _paste_clipboard_image(self):
        img = QApplication.clipboard().image()
        if not img.isNull():
            self._place_image(QPixmap.fromImage(img))
        else:
            self._append_status("Clipboard has no image to paste.")

    def _append_status(self, msg):
        # transient feedback without a modal; reuse the window title briefly.
        self.setWindowTitle(msg)

    # -- SFX library ---------------------------------------------------
    def _refresh_library(self):
        self.lib.clear()
        if not os.path.isdir(SFX_LIB_DIR):
            return
        for name in sorted(os.listdir(SFX_LIB_DIR)):
            if not name.lower().endswith(LIB_EXTS):
                continue
            path = os.path.join(SFX_LIB_DIR, name)
            icon = QIcon(QPixmap(path))
            item = QListWidgetItem(icon, "")
            item.setToolTip(name)
            item.setData(Qt.UserRole, path)
            self.lib.addItem(item)

    def _upload_sfx(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Upload SFX to library", os.path.expanduser("~"),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not paths:
            return
        import shutil

        os.makedirs(SFX_LIB_DIR, exist_ok=True)
        for p in paths:
            try:
                shutil.copy2(p, os.path.join(SFX_LIB_DIR, os.path.basename(p)))
            except Exception:
                pass
        self._refresh_library()

    def _lib_clicked(self, item):
        path = item.data(Qt.UserRole)
        if path and os.path.exists(path):
            self._place_image(QPixmap(path))

    def _lib_menu(self, pos):
        item = self.lib.itemAt(pos)
        if item is None:
            return
        path = item.data(Qt.UserRole)
        menu = QMenu(self)
        act = menu.addAction("🗑 Delete from library")
        if menu.exec(self.lib.mapToGlobal(pos)) is act and path:
            if QMessageBox.question(
                    self, "Delete SFX",
                    f"Remove “{os.path.basename(path)}” from your SFX library?\n"
                    "(This deletes the saved file.)") == QMessageBox.Yes:
                try:
                    os.remove(path)
                except Exception:
                    pass
                self._refresh_library()

    # -- touch-up painting (blend / erase / paint) ---------------------
    def _select_tool(self, name):
        self._tool = name
        self.view.tool = name
        if name in self._tool_buttons:
            self._tool_buttons[name].setChecked(True)
        painting = name != "select"
        # While painting, clicks paint the canvas rather than moving items.
        for it in self.items + self.images:
            it.setFlag(QGraphicsItem.ItemIsSelectable, not painting)
            it.setFlag(QGraphicsItem.ItemIsMovable, not painting)
        if painting:
            self.scene.clearSelection()
        self.brush_group.setVisible(name in ("blend", "erase", "paint", "remove"))
        self.paint_color_btn.setVisible(name == "paint")
        self.erase_hl_btn.setVisible(name == "remove")
        self.clear_hl_btn.setVisible(name == "remove")
        if name != "remove":
            self._clear_highlight()  # drop any pending marks when switching away
        self.view.setCursor(Qt.CrossCursor if painting else Qt.ArrowCursor)
        self.view.viewport().update()

    @staticmethod
    def _detect_marks(region_bgr):
        """Mask the watermark/text pixels inside a region: those that deviate from
        their LOCAL colour (Lab distance) — the same cue the SFX cleaner uses —
        then fill enclosed interiors so solid marks are erased whole, not ringed."""
        lab = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
        local = cv2.GaussianBlur(lab, (21, 21), 0)
        dist = np.sqrt(((lab - local) ** 2).sum(axis=2))
        m = (dist > 16).astype(np.uint8) * 255
        m = cv2.morphologyEx(
            m, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
        # flood the outside from a guaranteed-background border; the un-flooded
        # remainder is the enclosed interior of a solid mark -> add it back.
        b = cv2.copyMakeBorder(m, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
        ff = b.copy()
        cv2.floodFill(ff, np.zeros((b.shape[0] + 2, b.shape[1] + 2), np.uint8),
                      (0, 0), 255)
        filled = b | cv2.bitwise_not(ff)
        return filled[1:-1, 1:-1]

    def _box_remove(self, x0, y0, x1, y1):
        """Drag-a-box removal: detect the mark inside the box and inpaint only
        those pixels, leaving the surrounding art intact."""
        if self._work_np is None:
            return
        H, W = self._work_np.shape[:2]
        x0, x1 = sorted((int(x0), int(x1)))
        y0, y1 = sorted((int(y0), int(y1)))
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(W, x1), min(H, y1)
        if x1 - x0 < 4 or y1 - y0 < 4:
            return
        region = self._work_np[y0:y1, x0:x1]
        m = self._detect_marks(region)
        if not m.any():
            return
        full = np.zeros((H, W), np.uint8)
        full[y0:y1, x0:x1] = m
        full = cv2.dilate(
            full, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        self._work_np = self._work_np.copy()  # copy-on-write for undo
        self._work_np = cv2.inpaint(self._work_np, full, 4, cv2.INPAINT_TELEA)
        self._bg_pixmap = _bgr_to_qpixmap(self._work_np)
        self._bg_item.setPixmap(self._bg_pixmap)
        self._record_if_changed()

    def _brush_changed(self, v):
        self._brush_size = int(v)
        self.view.viewport().update()  # resize the hover preview ring live

    def _pick_paint_color(self):
        c = QColorDialog.getColor(self._paint_color, self, "Paint colour")
        if c.isValid():
            self._paint_color = c

    @staticmethod
    def _brush_mask(h, w, cx, cy, r):
        yy, xx = np.ogrid[:h, :w]
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        m = np.clip(1.0 - dist / max(1.0, r), 0.0, 1.0) ** 0.6
        return m[:, :, None].astype(np.float32)

    def _stamp(self, fx, fy):
        if self._work_np is None or self._tool == "select":
            return
        r = max(2, self._brush_size // 2)
        x, y = int(round(fx)), int(round(fy))
        H, W = self._work_np.shape[:2]
        if self._tool == "remove":
            # Highlight what will be erased — accumulate across strokes into a
            # mask + a red overlay. Nothing is inpainted until "Erase" is pressed.
            if self._remove_mask is None:
                self._remove_mask = np.zeros((H, W), np.uint8)
            cv2.circle(self._remove_mask, (x, y), r, 255, -1)
            p = QPainter(self._hl_pixmap)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 0, 0, 120))
            p.drawEllipse(QRectF(x - r, y - r, 2 * r, 2 * r))
            p.end()
            self._hl_item.setPixmap(self._hl_pixmap)
            return
        x0, x1 = max(0, x - r), min(W, x + r)
        y0, y1 = max(0, y - r), min(H, y + r)
        if x1 <= x0 or y1 <= y0:
            return
        patch = self._work_np[y0:y1, x0:x1].astype(np.float32)
        mask = self._brush_mask(y1 - y0, x1 - x0, x - x0, y - y0, r)
        if self._tool == "blend":
            k = r if r % 2 == 1 else r + 1
            k = max(3, k)
            blur = cv2.GaussianBlur(self._work_np[y0:y1, x0:x1], (k, k), 0)
            out = mask * blur.astype(np.float32) + (1 - mask) * patch
        elif self._tool == "erase":
            orig = self._orig_np[y0:y1, x0:x1].astype(np.float32)
            out = mask * orig + (1 - mask) * patch
        elif self._tool == "paint":
            col = np.array([self._paint_color.blue(), self._paint_color.green(),
                            self._paint_color.red()], np.float32)
            out = mask * col + (1 - mask) * patch
        else:
            return
        self._work_np[y0:y1, x0:x1] = np.clip(out, 0, 255).astype(np.uint8)
        self._update_bg_patch(x0, y0, x1, y1)

    def _update_bg_patch(self, x0, y0, x1, y1):
        patch = np.ascontiguousarray(self._work_np[y0:y1, x0:x1, ::-1])
        h, w = patch.shape[:2]
        qimg = QImage(patch.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        p = QPainter(self._bg_pixmap)
        p.drawImage(x0, y0, qimg)
        p.end()
        self._bg_item.setPixmap(self._bg_pixmap)

    def _paint_begin(self, x, y):
        if self._work_np is None:
            return
        # Remove just paints a highlight (no canvas change until Erase); the
        # other brushes edit the canvas, so copy-on-write for undo.
        if self._tool != "remove":
            self._work_np = self._work_np.copy()
        self._last_paint = None
        self._paint_move(x, y)

    def _paint_move(self, x, y):
        last = self._last_paint
        if last is None:
            self._stamp(x, y)
        else:
            import math
            dx, dy = x - last[0], y - last[1]
            dist = math.hypot(dx, dy)
            step = max(1.0, self._brush_size * 0.25)
            n = max(1, int(dist / step))
            for i in range(1, n + 1):
                self._stamp(last[0] + dx * i / n, last[1] + dy * i / n)
        self._last_paint = (x, y)

    def _paint_end(self):
        self._last_paint = None
        if self._tool == "remove":
            # update the Erase button's enabled state; no canvas change yet
            has = self._remove_mask is not None and bool(self._remove_mask.any())
            self.erase_hl_btn.setEnabled(has)
            self.clear_hl_btn.setEnabled(has)
            return
        self._record_if_changed()

    def _clear_highlight(self):
        self._remove_mask = None
        if self._hl_pixmap is not None:
            self._hl_pixmap.fill(Qt.transparent)
            self._hl_item.setPixmap(self._hl_pixmap)
        if hasattr(self, "erase_hl_btn"):
            self.erase_hl_btn.setEnabled(False)
            self.clear_hl_btn.setEnabled(False)

    def _erase_highlight(self):
        """Inpaint every highlighted region at once (Telea), then clear it."""
        if self._remove_mask is None or not self._remove_mask.any():
            return
        self._work_np = self._work_np.copy()  # copy-on-write for undo
        m = cv2.dilate(
            self._remove_mask,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        self._work_np = cv2.inpaint(self._work_np, m, 4, cv2.INPAINT_TELEA)
        self._clear_highlight()
        self._bg_pixmap = _bgr_to_qpixmap(self._work_np)
        self._bg_item.setPixmap(self._bg_pixmap)
        self._record_if_changed()

    # -- undo / redo ---------------------------------------------------
    def _snap_state(self):
        out = []
        for it in self.items:
            out.append(it.to_dict())
        for im in self.images:
            out.append({"kind": "image", "pix": im._pix, "x": im.x(), "y": im.y(),
                        "w": im.w, "h": im.h, "rot": im.rotation()})
        return out

    def _sig(self):
        parts = []
        for it in self.items:
            parts.append(("t", round(it.x()), round(it.y()), round(it.w),
                          round(it.h), it.text, round(it.rotation()),
                          round(it.max_size), it.fill.name(), it.outline.name(),
                          it.outline_w, it.font.family(), it.font.bold(),
                          it.font.italic(), it.font.underline(), int(it.align)))
        for im in self.images:
            parts.append(("i", round(im.x()), round(im.y()), round(im.w),
                          round(im.h), round(im.rotation()), id(im._pix)))
        return (tuple(parts), id(self._work_np))

    def _reset_history(self):
        self._history = [{"state": self._snap_state(), "work": self._work_np,
                          "sig": self._sig()}]
        self._hist_idx = 0
        self._update_undo_buttons()

    def _record(self):
        self._history = self._history[: self._hist_idx + 1]
        self._history.append({"state": self._snap_state(), "work": self._work_np,
                              "sig": self._sig()})
        if len(self._history) > 40:
            self._history.pop(0)
        self._hist_idx = len(self._history) - 1
        self._update_undo_buttons()

    def _record_if_changed(self):
        if not self._history or self._sig() != self._history[self._hist_idx]["sig"]:
            self._record()

    def _apply_snapshot(self, snap):
        self._work_np = snap["work"]
        self._bg_pixmap = _bgr_to_qpixmap(self._work_np)
        self._bg_item.setPixmap(self._bg_pixmap)
        self._rebuild_from_state(snap["state"])

    def _undo(self):
        if self._hist_idx > 0:
            self._commit_inline()
            self._hist_idx -= 1
            self._apply_snapshot(self._history[self._hist_idx])
            self._update_undo_buttons()

    def _redo(self):
        if self._hist_idx < len(self._history) - 1:
            self._hist_idx += 1
            self._apply_snapshot(self._history[self._hist_idx])
            self._update_undo_buttons()

    def _update_undo_buttons(self):
        self.undo_btn.setEnabled(self._hist_idx > 0)
        self.redo_btn.setEnabled(self._hist_idx < len(self._history) - 1)

    def _toggle_bold(self):
        for it in self._selected():
            it.font.setBold(self.bold_btn.isChecked())
            it._refit()
            it.update()
        self._record_if_changed()

    def _toggle_italic(self):
        for it in self._selected():
            it.font.setItalic(self.italic_btn.isChecked())
            it._refit()
            it.update()
        self._record_if_changed()

    def _toggle_underline(self):
        for it in self._selected():
            it.font.setUnderline(self.underline_btn.isChecked())
            it.update()
        self._record_if_changed()

    def _align_changed(self, i):
        a = [Qt.AlignLeft, Qt.AlignHCenter, Qt.AlignRight][i] | Qt.AlignVCenter
        for it in self._selected():
            it.align = a
            it._refit()
            it.update()
        self._record_if_changed()

    def _rot_changed(self, v):
        for it in self._selected():
            it.setTransformOriginPoint(it.w / 2, it.h / 2)
            it.setRotation(v)
        self._record_if_changed()

    def _pick_fill(self):
        c = QColorDialog.getColor(QColor(0, 0, 0), self, "Text colour")
        if c.isValid():
            for it in self._selected():
                it.fill = c
                it.update()
            self._record_if_changed()

    def _pick_outline(self):
        c = QColorDialog.getColor(QColor(255, 255, 255), self, "Outline colour")
        if c.isValid():
            for it in self._selected():
                it.outline = c
                it.update()
            self._record_if_changed()

    def _apply_gradient(self, colors, angle):
        """Apply (or clear) a gradient fill on selected boxes."""
        for it in self._selected():
            it.gradient_colors = colors
            it.gradient_angle = float(angle)
            it.update()
        self._record_if_changed()

    def _apply_effect(self, key: str):
        for it in self._selected():
            it.effect = key
            it.update()
        self._record_if_changed()

    def _set_effect_color(self, hex_color: str):
        for it in self._selected():
            it.effect_color = hex_color
            it.update()
        self._record_if_changed()

    def _show_effects_panel(self, btn):
        # Sync color to first selected box
        sel = self._selected()
        if sel:
            self._effects_panel.set_color(sel[0].effect_color)
        gp = btn.mapToGlobal(QPointF(0, btn.height() + 4).toPoint())
        self._effects_panel.move(gp)
        self._effects_panel.show()
        self._effects_panel.raise_()

    def _copy_for_claude(self):
        lines = []
        for seg in self.segments:
            for it in seg["items"]:
                lines.append((it["n"], f"{it['n']}. [{it['kind']}] {it['src']}"))
        lines.sort(key=lambda t: t[0])
        body = "\n".join(s for _, s in lines)
        text = (
            "Translate each numbered line below into natural Khmer for a manhwa. "
            "Keep the numbers and the [bubble]/[sfx] tags, one line each.\n\n"
            "Then, on a final line, group the bubbles into Facebook posts — each "
            "post a coherent emotional beat that ends on a little hook when it can "
            "— as:\n"
            "POSTS: 1-4 | 5-9 | 10-13\n\n" + body
        )
        QApplication.clipboard().setText(text)
        QMessageBox.information(
            self, "Copied",
            f"Copied {len(lines)} numbered lines (+ a prompt) to the clipboard.\n\n"
            "Paste into Claude, then paste the reply back with “2️⃣ Paste Khmer "
            "list” — it fills the Khmer AND reads the POSTS line for story splitting.",
        )

    @staticmethod
    def _parse_posts(text):
        """Parse a 'POSTS: 1-4 | 5-9 | 10-13' line into [(1,4),(5,9),(10,13)]."""
        import re

        m = re.search(r"POSTS?\s*:\s*([0-9\-\s|,]+)", text, re.I)
        if not m:
            return []
        groups = []
        for part in re.split(r"[|,]", m.group(1)):
            mm = re.match(r"\s*(\d+)\s*-\s*(\d+)", part)
            if mm:
                groups.append((int(mm.group(1)), int(mm.group(2))))
            elif part.strip().isdigit():
                n = int(part.strip())
                groups.append((n, n))
        return groups

    def _paste(self):
        from .psgen import parse_khmer_list

        dlg = PasteDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        raw = dlg.text()
        km = parse_khmer_list(raw)
        posts = self._parse_posts(raw)
        if posts:
            self._post_groups = posts
        if not km:
            if posts:
                QMessageBox.information(
                    self, "Story split set",
                    f"Saved {len(posts)} post groups — FB panels will follow them.")
                return
            QMessageBox.warning(self, "No lines", "No 'N. text' lines found.")
            return
        # Box numbers are unique across the whole chapter, so fill EVERY canvas in
        # one go — not just the one on screen — by visiting each segment, filling
        # its matching boxes, and saving its state.
        self._commit_items()
        cur = self.seg_idx
        filled = 0
        for i in range(len(self.segments)):
            self.seg_idx = i  # so _commit_items writes the RIGHT canvas
            self._load_segment(i)
            hit = False
            for it in self.items:
                if it.n in km:
                    raw = km[it.n] or ""
                    # Detect **name** markers Claude adds for proper nouns.
                    has_name = bool(re.search(r'\*\*[^*]+\*\*', raw))
                    it.text = re.sub(r'\*\*([^*]+)\*\*', r'\1', raw)
                    if has_name:
                        it.font.setBold(True)
                        it.font.setItalic(True)
                    it._refit()
                    it.update()
                    filled += 1
                    hit = True
            if hit:
                self._commit_items()  # persist this canvas's Khmer
        self.seg_idx = cur
        self._load_segment(cur)  # return to where the user was
        QMessageBox.information(
            self, "Filled",
            f"Filled {filled} text box(es) across {len(self.segments)} canvas(es).",
        )

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

    @staticmethod
    def _has_khmer(text: str) -> bool:
        return any("ក" <= c <= "៿" for c in text or "")

    def _is_translated(self) -> bool:
        """True if this canvas is done: either it has no text boxes (art-only
        page) or at least one box has Khmer in it. A canvas with boxes that are
        still empty / source text counts as NOT translated yet."""
        if not self.items:
            return True
        return any(self._has_khmer(it.text) for it in self.items)

    def _export_all(self):
        self._commit_items()
        cur = self.seg_idx
        done, pending = [], []
        clean_dir = os.path.join(self.base, "clean_untranslated")
        for i, seg in enumerate(self.segments):
            self.seg_idx = i
            self._load_segment(i)
            translated = self._is_translated()
            sub = self.base if translated else clean_dir
            os.makedirs(sub, exist_ok=True)
            out = os.path.join(sub, seg["image"].replace(".png", "_kh.png"))
            self._render(seg).save(out)
            (done if translated else pending).append(out)
        self.seg_idx = cur
        self._load_segment(cur)
        msg = f"{len(done)} translated canvas(es) → {self.base}"
        if pending:
            msg += (f"\n{len(pending)} not-yet-translated → {clean_dir}")
        QMessageBox.information(self, "Exported all", msg)

    def _export_pdf(self):
        """Render every canvas (Khmer + edits baked in) into one multi-page PDF."""
        try:
            from PIL import Image
        except Exception:
            QMessageBox.warning(self, "PDF", "Pillow is required to save a PDF.")
            return
        chapter = self.layout.get("chapter", "") or "chapter"
        default = os.path.join(self.base, f"{chapter}.pdf")
        out, _ = QFileDialog.getSaveFileName(self, "Save as PDF", default,
                                             "PDF (*.pdf)")
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        self._commit_items()
        cur = self.seg_idx
        pages, skipped = [], 0
        for i, seg in enumerate(self.segments):
            self.seg_idx = i
            self._load_segment(i)
            if not self._is_translated():   # leave un-translated pages out
                skipped += 1
                continue
            bgr = self._qimage_to_bgr(self._render(seg))
            pages.append(Image.fromarray(np.ascontiguousarray(bgr[:, :, ::-1])))
        self.seg_idx = cur
        self._load_segment(cur)
        if not pages:
            QMessageBox.warning(
                self, "PDF", "No translated canvases yet — nothing to put in the PDF.")
            return
        pages[0].save(out, "PDF", save_all=True, append_images=pages[1:])
        note = f"\n({skipped} un-translated page(s) skipped)" if skipped else ""
        QMessageBox.information(self, "PDF saved", out + note)

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

    def _story_cuts(self, seg):
        """Target cut rows that follow the story, for the boxes on THIS canvas.
        Uses Claude's pasted POSTS grouping when present; otherwise falls back to
        a transcript heuristic (sentence ends + scene-gap silences + size). Each
        target is later snapped to the nearest safe gutter by the splitter."""
        mode = self.split_mode.currentData()
        if mode == "visual":
            return None
        boxes = sorted(self.items, key=lambda it: it.y())
        if len(boxes) < 2:
            return None
        H = float(seg["height"])
        targets = []
        use_groups = self._post_groups and mode != "heuristic"
        if use_groups:
            cut_after = {b for _, b in self._post_groups[:-1]}  # last n of each post
            for i, it in enumerate(boxes[:-1]):
                if it.n in cut_after:
                    nxt = boxes[i + 1]
                    targets.append((it.y() + it.h + nxt.y()) / 2)
        else:
            W = float(seg["width"])
            ideal, hard = W * IDEAL_FB, W * MAX_FB
            last = 0.0
            for i, it in enumerate(boxes[:-1]):
                nxt = boxes[i + 1]
                bottom = it.y() + it.h
                gap = nxt.y() - bottom
                grown = bottom - last
                ends = (it.text.strip()[-1:] in ".!?…។៕") if it.text.strip() else False
                big_gap = gap > W * 0.5
                if (grown >= ideal and (ends or big_gap)) or grown >= hard:
                    cut = (bottom + nxt.y()) / 2
                    targets.append(cut)
                    last = cut
        targets = sorted(t for t in targets if 8 < t < H - 8)
        return targets or None

    def _slice_canvas(self, seg):
        """Render the current canvas and compute safe panel slices (story beats
        steer the cuts; text boxes are never sliced)."""
        from . import splitter

        bgr = self._qimage_to_bgr(self._render(seg))
        protect = [(it.y(), it.y() + it.h) for it in self.items]
        slices = splitter.split_panels(
            bgr, protect=protect, desired_cuts=self._story_cuts(seg))
        return bgr, slices

    def _export_fb(self):
        from . import splitter

        out_dir = os.path.join(self.base, "fb_panels")
        splitter.clear_panels(out_dir)
        bgr, slices = self._slice_canvas(self.segments[self.seg_idx])
        paths = splitter.write_panels(bgr, slices, out_dir, "panel", 1)
        if not paths:
            QMessageBox.warning(self, "No panels", "Nothing to split.")
            return
        QMessageBox.information(
            self, "Facebook panels",
            f"{len(paths)} panel(s) →\n{out_dir}",
        )

    def _export_fb_all(self):
        from . import splitter

        self._commit_items()
        out_dir = os.path.join(self.base, "fb_panels")
        splitter.clear_panels(out_dir)
        idx, total = 1, 0  # one continuous numbering across all canvases
        for i, seg in enumerate(self.segments):
            self.seg_idx = i
            self._load_segment(i)
            bgr, slices = self._slice_canvas(seg)
            wrote = splitter.write_panels(bgr, slices, out_dir, "panel", idx)
            idx += len(wrote)
            total += len(wrote)
        QMessageBox.information(
            self, "Facebook panels",
            f"{total} panel(s) across {len(self.segments)} canvas(es)\n→ {out_dir}",
        )

    def _save(self):
        default = (self._project_name or self.layout.get("chapter", "")
                   or os.path.basename(self.base))
        name, ok = QInputDialog.getText(
            self, "Save project", "Project name:", text=default)
        if not ok:
            return
        self._project_name = name.strip() or default
        self._commit_items()
        segs = []
        for s in self.segments:
            entry = {"image": s["image"], "state": s.get("_state", [])}
            work = s.get("_work_np")  # painted / watermark-removed canvas
            if work is not None:
                wname = os.path.splitext(s["image"])[0] + "_work.png"
                cv2.imwrite(os.path.join(self.base, wname), work)
                entry["work"] = wname
            segs.append(entry)
        proj = {
            "layout": os.path.basename(self.layout_path),
            "name": self._project_name,
            "seg_idx": self.seg_idx,
            "post_groups": [list(g) for g in self._post_groups],
            "segments": segs,
        }
        path = os.path.join(self.base, "typeset_project.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(proj, f, ensure_ascii=False, indent=2)
        self._register_recent()  # bump it to the top of the home screen
        QMessageBox.information(
            self, "Saved",
            f"Project saved →\n{path}\n\nReopen it from the app's home screen, or "
            "this chapter's layout.json, to resume where you left off.")

    def _load_project(self):
        """If a saved project exists for this chapter, offer to resume it —
        restoring text, images, paint/removal edits, story grouping and the
        canvas you were on."""
        path = os.path.join(self.base, "typeset_project.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                proj = json.load(f)
        except Exception:
            return
        if QMessageBox.question(
                self, "Resume project?",
                "A saved project was found for this chapter.\n"
                "Resume where you left off?") != QMessageBox.Yes:
            return
        by_image = {s.get("image"): s for s in proj.get("segments", [])}
        for seg in self.segments:
            sp = by_image.get(seg["image"])
            if not sp:
                continue
            if sp.get("state"):
                seg["_state"] = sp["state"]
            if sp.get("work"):
                wp = os.path.join(self.base, sp["work"])
                arr = cv2.imread(wp) if os.path.exists(wp) else None
                if arr is not None:
                    seg["_work_np"] = arr
        self._post_groups = [tuple(g) for g in proj.get("post_groups", [])]
        if proj.get("name"):
            self._project_name = proj["name"]
        if self.segments:
            self.seg_idx = min(max(0, int(proj.get("seg_idx", 0))),
                               len(self.segments) - 1)


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
