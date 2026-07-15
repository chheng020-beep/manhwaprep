import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontMetricsF
from PySide6.QtCore import Qt

_app = QApplication.instance() or QApplication([])

from manhwaprep.typeset_editor import TextBoxItem


def _long_box():
    # Long dialogue that must wrap several times inside a narrow bubble — the
    # everyday case for Khmer speech balloons.
    text = "សួស្តី​អ្នក​ទាំង​អស់​គ្នា " * 8
    return TextBoxItem(1, text, 0, 0, 140, 60)


def test_glyph_lines_wrap_like_the_fill():
    """The outline/hollow glyph layout must word-wrap at the box width exactly
    like the fill. The perf refactor split only on '\\n', so long text produced
    ONE overflowing outline line while the fill wrapped into many — the outline
    ghosted apart. Guard: same number of visual lines as the fill layout."""
    it = _long_box()
    lines = it._glyph_lines()
    layout, _ = it._text_layout()          # the fill's wrapped layout
    assert len(lines) == layout.lineCount()
    assert len(lines) > 1                   # it genuinely wrapped


def test_each_outline_line_fits_the_box_width():
    it = _long_box()
    fm = QFontMetricsF(it.font)
    for seg, x0, base in it._glyph_lines():
        assert fm.horizontalAdvance(seg) <= it.w + 1.0
        assert 0.0 - 1.0 <= x0 <= it.w      # centered/aligned within the box


def test_segments_reconstruct_the_text():
    it = _long_box()
    joined = "".join(seg for seg, _, _ in it._glyph_lines())
    # wrapping may drop the space at a break; ignore whitespace when comparing
    assert "".join(joined.split()) == "".join(it._plain_text.split())
