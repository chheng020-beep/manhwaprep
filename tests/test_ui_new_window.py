import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
import pytest
from PySide6.QtWidgets import QApplication
from manhwaprep import ui


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_new_window_button_spawns(app, monkeypatch):
    # Avoid loading models / heavy tabs is not needed: MainWindow builds tabs,
    # but we only assert the button calls relaunch.spawn_new_window once.
    calls = {"n": 0}
    monkeypatch.setattr(ui.relaunch, "spawn_new_window", lambda: calls.__setitem__("n", calls["n"] + 1))
    win = ui.MainWindow()
    assert hasattr(win, "new_window_btn")
    win.new_window_btn.click()
    assert calls["n"] == 1


def test_new_window_warns_on_failure(app, monkeypatch):
    def boom():
        raise RuntimeError("no exec")
    monkeypatch.setattr(ui.relaunch, "spawn_new_window", boom)
    warned = {"n": 0}
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a, **k: warned.__setitem__("n", warned["n"] + 1))
    win = ui.MainWindow()
    win.new_window_btn.click()  # must not raise
    assert warned["n"] == 1
