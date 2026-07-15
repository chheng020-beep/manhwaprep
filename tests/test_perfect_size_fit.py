import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontMetricsF, QFont
from manhwaprep import perfect_size as ps
from manhwaprep.typeset_editor import khmer_font

_app = QApplication.instance() or QApplication([])
FAM = khmer_font()


def _lines_fit(lines, size, box_w, box_h, margin=0.06):
    f = QFont(FAM); f.setPointSizeF(size); fm = QFontMetricsF(f)
    aw = box_w * (1 - 2 * margin); ah = box_h * (1 - 2 * margin)
    if any(fm.horizontalAdvance(ln) > aw + 1.0 for ln in lines):
        return False
    return len(lines) * fm.lineSpacing() <= ah + 1.0


def test_fit_result_fits_the_box():
    text = "លោកអ្នកទាំងអស់គ្នាសូមអរគុណ"
    size, lines = ps.fit(text, 300, 200, FAM)
    assert lines and "".join("".join(lines).split()) != ""
    assert _lines_fit(lines, size, 300, 200)


def test_fit_is_maximal():
    text = "លោកអ្នកទាំងអស់គ្នា"
    size, lines = ps.fit(text, 300, 200, FAM)
    # one point larger should NOT fit (the search really maximised)
    bigger = ps._wrap(ps.segment(text), 300 * 0.88, _fm(size + 1))
    from PySide6.QtGui import QFont as _QF
    f = _QF(FAM); f.setPointSizeF(size + 1); fm = QFontMetricsF(f)
    assert len(bigger) * fm.lineSpacing() > 200 * 0.88 or \
        any(fm.horizontalAdvance(l) > 300 * 0.88 + 1 for l in bigger)


def _fm(size):
    f = QFont(FAM); f.setPointSizeF(size); return QFontMetricsF(f)


def test_overflow_fallback_returns_min_size_not_crash():
    text = "លោកអ្នកទាំងអស់គ្នា" * 40
    size, lines = ps.fit(text, 60, 60, FAM)
    assert size == 6.0 and lines           # min size, no exception


def test_fit_honors_line_spacing():
    """Regression: fit() used to measure with a hardcoded line-spacing of 1.0,
    ignoring the box's real line_spacing. A box with 2.0x spacing must not
    overflow its height -- the chosen size (with spacing accounted for) must
    fit, and it must differ from the size chosen at spacing=1.0 for a
    spacing-sensitive (multi-line) box."""
    text = "លោកអ្នកទាំងអស់គ្នាសូមអរគុណដែលបានចូលរួមក្នុងកម្មវិធីនេះ"
    box_w, box_h, margin = 250, 260, 0.06

    size1, lines1 = ps.fit(text, box_w, box_h, FAM, margin=margin, line_spacing=1.0)
    size2, lines2 = ps.fit(text, box_w, box_h, FAM, margin=margin, line_spacing=2.0)

    assert len(lines1) >= 2 and len(lines2) >= 2   # sanity: multi-line, spacing-sensitive

    # the 2.0x-spacing fit must actually respect that spacing in its own box
    fm2 = _fm(size2)
    ah = box_h * (1 - 2 * margin)
    assert len(lines2) * fm2.lineSpacing() * 2.0 <= ah + 1.0

    # accounting for spacing must change the outcome vs. the unspaced fit
    assert size2 < size1
