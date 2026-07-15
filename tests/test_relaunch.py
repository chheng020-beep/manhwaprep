import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
from manhwaprep import relaunch


def test_launch_argv_from_source(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", "/venv/bin/python")
    assert relaunch.launch_argv() == ["/venv/bin/python", "-m", "manhwaprep"]


def test_launch_argv_when_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/apps/ManhwaPrep.exe")
    assert relaunch.launch_argv() == ["/apps/ManhwaPrep.exe"]
