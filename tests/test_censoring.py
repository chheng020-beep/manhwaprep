import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
import numpy as np
from manhwaprep import nsfw


def test_pixelate_keeps_shape_and_obscures():
    # a smooth horizontal gradient -> pixelation must quantise it into blocks
    region = np.zeros((100, 100, 3), np.uint8)
    region[:, :, 0] = np.linspace(0, 255, 100, dtype=np.uint8)[None, :]
    out = nsfw.pixelate(region, blocks=10)
    assert out.shape == region.shape
    # far fewer distinct columns after mosaicking than the 100-step gradient
    assert len(np.unique(out[50, :, 0])) <= 12
    assert not np.array_equal(out, region)


def test_detect_filters_to_fb_safe_and_maps_boxes():
    class FakeDetector:
        def detect(self, path):
            return [
                {"class": "FEMALE_BREAST_EXPOSED", "score": 0.9, "box": [10, 20, 30, 40]},
                {"class": "FACE_FEMALE", "score": 0.99, "box": [0, 0, 5, 5]},      # not FB-safe
                {"class": "BUTTOCKS_EXPOSED", "score": 0.10, "box": [1, 2, 3, 4]}, # below min_score
            ]
    img = np.zeros((80, 80, 3), np.uint8)
    boxes = nsfw.detect(img, detector=FakeDetector(), min_score=0.35)
    assert boxes == [{"x": 10, "y": 20, "w": 30, "h": 40, "source": "auto"}]


def test_labels_are_the_fb_safe_set():
    assert nsfw.LABELS == {
        "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
        "FEMALE_BREAST_EXPOSED", "BUTTOCKS_EXPOSED", "ANUS_EXPOSED",
    }


import json, tempfile
import cv2
from PySide6.QtWidgets import QApplication, QMessageBox, QInputDialog

_app = QApplication.instance() or QApplication([])
for _m in ("information", "warning", "critical"):
    setattr(QMessageBox, _m, staticmethod(lambda *a, **k: None))
QMessageBox.question = staticmethod(lambda *a, **k: None)
QInputDialog.getText = staticmethod(lambda *a, **k: ("censor-test", True))


def _make_layout(d, segs=1):
    ts = os.path.join(d, "typeset"); os.makedirs(ts, exist_ok=True)
    seglist = []
    for i in range(segs):
        canvas = np.full((120, 100, 3), 255, np.uint8)
        name = f"canvas_{i + 1:03d}.png"
        cv2.imwrite(os.path.join(ts, name), canvas)
        seglist.append({"image": name, "width": 100, "height": 120, "items": []})
    layout = {"chapter": "t", "lang": "en", "segments": seglist}
    p = os.path.join(ts, "layout.json")
    json.dump(layout, open(p, "w", encoding="utf-8"))
    return p


def test_make_censor_adds_item_and_to_dict():
    from manhwaprep.typeset_editor import TypesetEditor, CensorItem
    with tempfile.TemporaryDirectory() as d:
        ed = TypesetEditor(_make_layout(d))
        c = ed._make_censor(10, 20, 30, 40, "manual")
        assert isinstance(c, CensorItem)
        assert c in ed.censors and c in ed.scene.items()
        assert c.to_dict() == {"x": 10, "y": 20, "w": 30, "h": 40, "source": "manual"}


def test_censor_survives_segment_roundtrip():
    from manhwaprep.typeset_editor import TypesetEditor
    with tempfile.TemporaryDirectory() as d:
        ed = TypesetEditor(_make_layout(d))
        ed._make_censor(5, 6, 20, 25, "auto")
        ed._commit_items()
        assert ed.segments[0]["_censors"] == [
            {"x": 5, "y": 6, "w": 20, "h": 25, "source": "auto"}]
        ed._load_segment(0)                      # rebuild from seg["_censors"]
        assert len(ed.censors) == 1
        assert ed.censors[0].to_dict() == {"x": 5, "y": 6, "w": 20, "h": 25, "source": "auto"}


def test_censor_persists_across_project_save_load():
    from manhwaprep.typeset_editor import TypesetEditor
    with tempfile.TemporaryDirectory() as d:
        p = _make_layout(d)
        ed = TypesetEditor(p)
        ed._make_censor(7, 8, 15, 16, "manual")
        assert ed._save() is True
        proj = json.load(open(os.path.join(os.path.dirname(p), "typeset_project.json")))
        assert proj["segments"][0]["censors"] == [
            {"x": 7, "y": 8, "w": 15, "h": 16, "source": "manual"}]
        ed2 = TypesetEditor(p, resume=True)
        assert ed2.segments[0]["_censors"] == [
            {"x": 7, "y": 8, "w": 15, "h": 16, "source": "manual"}]


def test_add_censor_from_drag_and_ignore_tiny():
    from manhwaprep.typeset_editor import TypesetEditor
    with tempfile.TemporaryDirectory() as d:
        ed = TypesetEditor(_make_layout(d))
        ed._add_censor(10, 10, 60, 50)      # 50x40 -> kept
        ed._add_censor(10, 10, 13, 13)      # 3x3 -> ignored
        assert len(ed.censors) == 1
        assert ed.censors[0].to_dict() == {"x": 10, "y": 10, "w": 50, "h": 40, "source": "manual"}


def test_delete_selected_removes_censor():
    from manhwaprep.typeset_editor import TypesetEditor
    with tempfile.TemporaryDirectory() as d:
        ed = TypesetEditor(_make_layout(d))
        c = ed._make_censor(10, 10, 30, 30, "manual")
        c.setSelected(True)
        ed._delete_selected()
        assert ed.censors == [] and c not in ed.scene.items()


def test_undo_redo_censor_add_and_delete():
    from manhwaprep.typeset_editor import TypesetEditor
    with tempfile.TemporaryDirectory() as d:
        ed = TypesetEditor(_make_layout(d))
        ed._add_censor(10, 10, 50, 50)
        assert len(ed.censors) == 1
        ed._undo()
        assert len(ed.censors) == 0
        ed._redo()
        assert len(ed.censors) == 1
        # now delete + undo restores it
        ed.censors[0].setSelected(True)
        ed._delete_selected()
        assert len(ed.censors) == 0
        ed._undo()
        assert len(ed.censors) == 1
