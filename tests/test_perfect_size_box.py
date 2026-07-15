import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
from PySide6.QtGui import QFont
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


def test_fitted_box_restores_font_size_after_reload():
    """Regression: a saved fitted box reconstructed via the editor's load path
    (TextBoxItem(...) -> font rebuilt from d["font"] -> _refit()) must restore
    the perfect-sized font, not silently keep Qt's default 12pt. _refit()
    early-returns for fitted boxes, so it must be the one place that reapplies
    max_size to the rebuilt font before returning."""
    it = TextBoxItem(1, "លោកអ្នកទាំងអស់គ្នាសូមអរគុណ", 0, 0, 300, 200)
    it.raw_text = it.text
    it.apply_perfect_size()
    saved_size = it.max_size
    assert saved_size > 12.0   # sanity: perfect-size actually picked a real size

    d = it.to_dict()

    # Mirror the editor's load path (_rebuild_from_state) exactly:
    reloaded = TextBoxItem(d["n"], d["text"], d["x"], d["y"], d["w"], d["h"])
    reloaded.raw_text = d.get("raw_text", d["text"])
    reloaded.fitted = bool(d.get("fitted", False))
    reloaded.font = QFont(d["font"])          # resets pointSizeF to Qt default (12.0)
    reloaded.max_size = float(d["size"])
    reloaded._refit()

    assert reloaded.fitted is True
    assert abs(reloaded.font.pointSizeF() - saved_size) < 0.01


def test_resize_refits_a_fitted_box():
    it = TextBoxItem(1, "លោកអ្នកទាំងអស់គ្នាសូមអរគុណ", 0, 0, 300, 200)
    it.raw_text = it.text
    it.apply_perfect_size()
    s1 = it.max_size
    # simulate a resize to a much bigger box, then the release-time re-fit hook
    it.w, it.h = 600, 400
    it.apply_perfect_size()
    assert it.max_size > s1        # bigger box -> bigger font
