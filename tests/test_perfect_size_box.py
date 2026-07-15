import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
from PySide6.QtWidgets import QApplication
from manhwaprep.typeset_editor import TextBoxItem
_app = QApplication.instance() or QApplication([])


def test_apply_perfect_size_fits_and_marks_fitted():
    it = TextBoxItem(1, "លោកអ្នកទាំងអស់គ្នាសូមអរគុណ", 0, 0, 300, 200)
    h0 = it.h
    it.raw_text = it.text
    it.apply_perfect_size()
    assert it.fitted is True
    assert it.max_size > 6.0
    assert "\n" in it.text or len(it.raw_text) < 12   # multi-line for long text
    assert abs(it.h - h0) < 1.0        # held its target height (did not auto-grow)


def test_fitted_box_refit_is_noop_on_height():
    it = TextBoxItem(1, "លោក", 0, 0, 200, 120)
    it.raw_text = it.text
    it.apply_perfect_size()
    h = it.h
    it._refit()
    assert abs(it.h - h) < 1.0
