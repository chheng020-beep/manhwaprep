import os
import pytest
from manhwaprep import prepqueue
from manhwaprep.control import PipelineStopped
from manhwaprep.projects import ProjectStore

COMIX = "https://comix.to/title/55kym-why-the-villainess-wields-the-sword/9356816-chapter-1"


def _store(tmp_path):
    return ProjectStore(os.path.join(tmp_path, "projects.json"))


def test_prep_chapter_success(tmp_path, monkeypatch):
    s = _store(tmp_path)
    pid, cid = s.add_chapter(COMIX)
    seen = []
    monkeypatch.setattr(prepqueue.pipeline, "run",
                        lambda *a, **k: ("/out/dir", ["/out/dir/typeset/layout.json"]))
    status = prepqueue.prep_chapter(
        s, pid, cid, on_status=lambda p, c, st: seen.append(st))
    assert status == "ready"
    ch = s.get_chapter(pid, cid)
    assert ch["status"] == "ready"
    assert ch["layout"] == "/out/dir/typeset/layout.json"
    assert ch["thumb"] == os.path.join("/out/dir", "typeset", "canvas_001.png")
    assert seen == ["prepping", "ready"]


def test_prep_chapter_error_is_caught(tmp_path, monkeypatch):
    s = _store(tmp_path)
    pid, cid = s.add_chapter(COMIX)

    def boom(*a, **k):
        raise RuntimeError("download failed")
    monkeypatch.setattr(prepqueue.pipeline, "run", boom)
    status = prepqueue.prep_chapter(s, pid, cid)
    assert status == "error"
    ch = s.get_chapter(pid, cid)
    assert ch["status"] == "error"
    assert "download failed" in ch["error"]


def test_prep_chapter_stop_requeues(tmp_path, monkeypatch):
    s = _store(tmp_path)
    pid, cid = s.add_chapter(COMIX)

    def stopped(*a, **k):
        raise PipelineStopped()
    monkeypatch.setattr(prepqueue.pipeline, "run", stopped)
    status = prepqueue.prep_chapter(s, pid, cid)
    assert status == "queued"
    assert s.get_chapter(pid, cid)["status"] == "queued"
