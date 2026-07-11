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


def test_tab_routes_typeset_to_editor():
    import manhwaprep.studio as studio
    from manhwaprep.studio_tab import StudioTab
    from manhwaprep.typeset_editor import TypesetEditor

    with tempfile.TemporaryDirectory() as root:
        st = studio.Studio(root)
        # build a chapter already in 'typeset' with a real layout on disk
        j = st.add("http://x/1", "one")
        cdir = st.chapter_dir(j.slug)
        _make_layout(cdir)              # writes typeset/layout.json + canvas
        st.set_state(j.slug, studio.TYPESET)

        tab = StudioTab(st)
        tab.refresh()
        tab.open_gate(j.slug)
        assert isinstance(tab._gate_widget, TypesetEditor)


def test_tab_cut_gate_opens_splitter_and_advance_on_export():
    import manhwaprep.studio as studio
    from manhwaprep.studio_tab import StudioTab
    from manhwaprep.manual_split import ManualSplitWidget

    with tempfile.TemporaryDirectory() as root:
        st = studio.Studio(root)
        j = st.add("http://x/1", "one")
        cdir = st.chapter_dir(j.slug)
        _make_layout(cdir)
        st.set_state(j.slug, studio.CUT)

        tab = StudioTab(st)
        tab.refresh()
        tab.open_gate(j.slug)
        assert isinstance(tab._gate_widget, ManualSplitWidget)
        # simulate splitter finishing an export
        tab._on_split_export(j.slug)
        assert studio.ChapterJob.from_status(cdir).state == studio.DONE


def test_mainwindow_has_studio_tab():
    from manhwaprep.ui import MainWindow
    w = MainWindow()
    titles = [w.tabs.tabText(i) for i in range(w.tabs.count())]
    assert "Studio" in titles
