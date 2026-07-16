"""Regression: rebuilding a canvas's items (panel switch / export) must restore
each text box to the EXACT position it was saved at — the user's drag is final.

Before the fix, TextBoxItem's constructor ran _refit() while the box was still
non-fitted with the default font, re-centring it vertically; _rebuild_from_state
restored height but not position, so every rebuild shifted the box (and the
shift accumulated across navigations)."""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")

from PySide6.QtWidgets import QApplication, QGraphicsScene
from manhwaprep.typeset_editor import TextBoxItem, TypesetEditor

_app = QApplication.instance() or QApplication([])


class _Stub:
    """Minimal host exposing exactly what _rebuild_from_state touches, so we can
    exercise the real editor method without constructing a full TypesetEditor."""
    _inline_proxy = None

    def __init__(self):
        self.scene = QGraphicsScene()
        self.items = []
        self.images = []

    def _commit_inline(self):
        pass

    def _start_inline_edit(self, it):
        pass


def _rebuild(state):
    stub = _Stub()
    TypesetEditor._rebuild_from_state(stub, state)
    return stub.items


def test_rebuild_preserves_dragged_position_fitted():
    sc = QGraphicsScene()
    it = TextBoxItem(1, "លោកអ្នកទាំងអស់គ្នាសូមអរគុណ", 100, 500, 200, 80)
    sc.addItem(it)
    it.raw_text = it.text
    it.apply_perfect_size()
    it.setPos(100, 500)              # user drags it here
    d = it.to_dict()
    r = _rebuild([d])[0]
    assert abs(r.x() - 100) < 0.5
    assert abs(r.y() - 500) < 0.5    # must not drift on rebuild


def test_rebuild_position_stable_across_two_roundtrips():
    sc = QGraphicsScene()
    it = TextBoxItem(1, "លោកអ្នកទាំងអស់គ្នា", 120, 480, 200, 80)
    sc.addItem(it)
    it.raw_text = it.text
    it.apply_perfect_size()
    it.setPos(120, 480)
    r1 = _rebuild([it.to_dict()])[0]
    r2 = _rebuild([r1.to_dict()])[0]
    assert abs(r2.x() - 120) < 0.5
    assert abs(r2.y() - 480) < 0.5   # no cumulative drift across navigations


def test_rebuild_preserves_position_nonfitted():
    sc = QGraphicsScene()
    it = TextBoxItem(2, "កម្ម", 300, 700, 180, 60)   # never perfect-sized
    sc.addItem(it)
    it.raw_text = it.text
    it.setPos(300, 700)
    r = _rebuild([it.to_dict()])[0]
    assert abs(r.x() - 300) < 0.5
    assert abs(r.y() - 700) < 0.5
