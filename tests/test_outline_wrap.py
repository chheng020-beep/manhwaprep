import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontMetricsF
from PySide6.QtCore import Qt, QRectF

_app = QApplication.instance() or QApplication([])

from manhwaprep.typeset_editor import TextBoxItem


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
