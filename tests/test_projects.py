import os
from manhwaprep.projects import ProjectStore

COMIX = "https://comix.to/title/55kym-why-the-villainess-wields-the-sword/9356816-chapter-1"
COMIX2 = "https://comix.to/title/55kym-why-the-villainess-wields-the-sword/9400000-chapter-2"


def _store(tmp_path):
    return ProjectStore(os.path.join(tmp_path, "projects.json"))


def test_add_chapter_creates_project_and_dedupes(tmp_path):
    s = _store(tmp_path)
    pid, cid = s.add_chapter(COMIX)
    assert pid == "comix:55kym-why-the-villainess-wields-the-sword"
    assert cid == "9356816-chapter-1"
    assert len(s.list_projects()) == 1
    assert s.get_chapter(pid, cid)["status"] == "queued"
    pid2, cid2 = s.add_chapter(COMIX)              # same chapter again
    assert (pid2, cid2) == (pid, cid)
    assert len(s.get_project(pid)["chapters"]) == 1  # not duplicated


def test_set_chapter_and_queue_order(tmp_path):
    s = _store(tmp_path)
    p1, c1 = s.add_chapter(COMIX)
    p2, c2 = s.add_chapter(COMIX2)
    s.enqueue(p1, c1)
    s.enqueue(p2, c2)
    s.enqueue(p1, c1)                              # duplicate enqueue ignored
    assert s.pop_next() == (p1, c1)
    assert s.pop_next() == (p2, c2)
    assert s.pop_next() is None
    s.set_chapter(p1, c1, status="ready", layout="/x/layout.json")
    assert s.get_chapter(p1, c1)["status"] == "ready"
    assert s.get_chapter(p1, c1)["layout"] == "/x/layout.json"


def test_reset_prepping(tmp_path):
    s = _store(tmp_path)
    p1, c1 = s.add_chapter(COMIX)
    s.set_chapter(p1, c1, status="prepping")
    s.reset_prepping()
    assert s.get_chapter(p1, c1)["status"] == "queued"
    assert s.pop_next() == (p1, c1)               # re-queued


def test_remove_chapter_keeps_files_by_default(tmp_path):
    s = _store(tmp_path)
    p1, c1 = s.add_chapter(COMIX)
    outdir = os.path.join(tmp_path, "out"); os.makedirs(outdir)
    marker = os.path.join(outdir, "keep.txt"); open(marker, "w").close()
    s.set_chapter(p1, c1, output_dir=outdir)
    s.remove_chapter(p1, c1)
    assert s.get_chapter(p1, c1) is None
    assert os.path.exists(marker)                 # files kept


def test_persistence_roundtrip(tmp_path):
    path = os.path.join(tmp_path, "projects.json")
    a = ProjectStore(path); a.add_chapter(COMIX)
    b = ProjectStore(path)                         # fresh instance, same file
    assert len(b.list_projects()) == 1
