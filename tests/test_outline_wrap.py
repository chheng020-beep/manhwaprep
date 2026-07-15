import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontMetricsF, QImage, QPainter, QColor
from PySide6.QtCore import Qt, QRectF

_app = QApplication.instance() or QApplication([])

from manhwaprep.typeset_editor import TextBoxItem, WRAP_FLAGS


def _long_box():
    # Long dialogue that must wrap several times inside a narrow bubble — the
    # everyday case for Khmer speech balloons.
    text = "សួស្តី​អ្នក​ទាំង​អស់​គ្នា " * 8
    return TextBoxItem(1, text, 0, 0, 140, 60)


def _rect(it):
    return QRectF(0, 0, it.w, it.h)


def test_shaped_path_wraps_inside_the_box():
    """The outline/hollow glyph path must wrap at the box width just like the
    fill. The pre-fix code laid the outline out with a second, hand-rolled pass
    that centred each line on ``fm.horizontalAdvance`` — for centred, multi-line
    Khmer it drifted off the fill (the ghosting bug). Building the path from the
    fill's own ``QTextLayout`` guarantees it occupies the same wrapped area."""
    it = _long_box()
    path = it._shaped_text_path(_rect(it))
    assert path is not None
    br = path.boundingRect()
    # Wrapped, not one overflowing line: fits the box width and is taller than a
    # single line of text.
    assert br.width() <= it.w + 2.0
    fm = QFontMetricsF(it.font)
    assert br.height() >= fm.lineSpacing() * 1.5


def test_shaped_path_matches_the_fill_layout_line_count():
    """Same number of visual lines as the fill's wrapped layout."""
    it = _long_box()
    layout, _ = it._text_layout()
    assert layout.lineCount() > 1  # genuinely wrapped
    path = it._shaped_text_path(_rect(it))
    fm = QFontMetricsF(it.font)
    # Path height spans roughly lineCount lines (± part of a line for ascﾃnders/
    # descenders), proving every wrapped line contributed glyphs.
    lines_spanned = path.boundingRect().height() / fm.lineSpacing()
    assert layout.lineCount() - 1 <= lines_spanned <= layout.lineCount() + 1


def test_shaped_path_is_horizontally_centered_for_centered_text():
    """Centred text: the outline path must sit centred in the box (the ghosting
    bug pushed it sideways because it re-centred each line on a flat advance sum
    that disagreed with the layout's shaped centring)."""
    it = _long_box()
    assert int(it.align) & int(Qt.AlignHCenter)
    br = it._shaped_text_path(_rect(it)).boundingRect()
    box_center = it.w / 2
    path_center = br.center().x()
    assert abs(path_center - box_center) <= 6.0


def test_empty_text_has_no_path():
    it = TextBoxItem(1, "", 0, 0, 140, 60)
    assert it._shaped_text_path(_rect(it)) is None


def _coverage_mask(it, draw):
    W, H = int(it.w), int(it.h)
    m = QImage(W, H, QImage.Format_ARGB32)
    m.fill(0)
    p = QPainter(m)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    p.setFont(it.font)          # _paint_text_body sets the painter font before drawing
    draw(p)
    p.end()
    return m, W, H


def test_fill_and_outline_render_aligned_when_heavily_wrapped():
    """The fill and the outline stroke must be built from the SAME layout, so
    they sit on top of each other. They used to diverge: the fill went through
    QPainter.drawText (spacing lines by the font's natural height) while the
    outline used QTextLayout (fm.lineSpacing()). On a narrow, many-line Khmer box
    the two walked apart line by line and the halo detached — the resize bug.
    Guard: fill and outline glyph coverage overlap heavily (IoU)."""
    it = TextBoxItem(1, "នាក់ប្រាក់ ១០០ ថង់ និងគ្រិណាត់ ស្ត្រ ៣០០ ឆ្នាំង",
                     0, 0, 150, 100)
    it.font.setPointSizeF(34)
    it.h = 589                  # a tall, narrow box: forces ~9 wrapped lines
    it.fill = QColor(0, 0, 0)
    r = _rect(it)
    mf, W, H = _coverage_mask(
        it, lambda p: it._draw_text_fill(p, r, int(it.align) | WRAP_FLAGS))
    mo, _, _ = _coverage_mask(
        it, lambda p: p.fillPath(it._shaped_text_path(r), QColor(0, 0, 0)))

    inter = union = 0
    for y in range(H):
        for x in range(W):
            a = mf.pixelColor(x, y).alpha() > 20
            b = mo.pixelColor(x, y).alpha() > 20
            inter += a and b
            union += a or b
    iou = inter / max(1, union)
    # Aligned overlap sits ~0.8; a drifting outline (the bug) falls far below this.
    assert iou > 0.7, f"fill/outline drifted apart: IoU={iou:.3f}"
