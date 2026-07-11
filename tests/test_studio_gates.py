# tests/test_studio_gates.py
import os, sys, json, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, '/Users/leapheakuoch/ManhwaPrep')
import numpy as np, cv2
from PySide6.QtWidgets import QApplication, QMessageBox, QInputDialog, QFileDialog

_app = QApplication.instance() or QApplication([])
# neutralise modal dialogs before any editor is built
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: None)
QInputDialog.getText = staticmethod(lambda *a, **k: ("", True))


def _make_layout(d):
    ts = os.path.join(d, "typeset"); os.makedirs(ts, exist_ok=True)
    canvas = np.full((1200, 800, 3), 255, np.uint8)
    cv2.imwrite(os.path.join(ts, "canvas_001.png"), canvas)
    layout = {"chapter": "t", "lang": "en", "segments": [
        {"image": "canvas_001.png", "width": 800, "height": 1200, "items": [
            {"n": 1, "bbox": [20, 20, 200, 60], "src": "Hi", "kind": "bubble"}]}]}
    p = os.path.join(ts, "layout.json")
    json.dump(layout, open(p, "w", encoding="utf-8"))
    return p


def test_render_translated_writes_images():
    with tempfile.TemporaryDirectory() as d:
        from manhwaprep.typeset_editor import TypesetEditor
        ed = TypesetEditor(_make_layout(d))
        out = os.path.join(d, "rendered")
        paths = ed.render_translated(out)
        assert len(paths) == 1
        assert os.path.exists(paths[0])
        assert cv2.imread(paths[0]) is not None


def test_ready_callback_fires():
    with tempfile.TemporaryDirectory() as d:
        from manhwaprep.typeset_editor import TypesetEditor
        ed = TypesetEditor(_make_layout(d))
        fired = []
        ed.set_ready_callback(lambda: fired.append(True))
        ed._on_ready_to_cut()   # the button's slot
        assert fired == [True]
