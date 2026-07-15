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


def test_spawn_new_window_uses_launch_argv(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", "/venv/bin/python")
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(relaunch.subprocess, "Popen", fake_popen)
    relaunch.spawn_new_window()
    assert captured["argv"] == ["/venv/bin/python", "-m", "manhwaprep"]
    # detached: posix uses start_new_session, win32 uses creationflags
    if sys.platform == "win32":
        assert captured["kwargs"].get("creationflags", 0) != 0
    else:
        assert captured["kwargs"].get("start_new_session") is True
