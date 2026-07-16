import os
from manhwaprep import series
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


def test_import_recents_does_not_clobber_done_status(tmp_path):
    """import_recents runs on every launch; a chapter the user later marked
    `done` must NOT be reverted to `ready` on the next import."""
    s = _store(tmp_path)
    layout = str(tmp_path / "MySeries" / "ch3" / "typeset" / "layout.json")
    out_dir = os.path.dirname(os.path.dirname(layout))          # .../MySeries/ch3
    entry = {"layout": layout, "chapter": "ch3", "thumb": ""}
    s.import_recents([entry])                                   # first launch -> ready
    info = series.detect(out_dir)
    pid, cid = info.series_id, info.chapter_id
    assert s.get_chapter(pid, cid)["status"] == "ready"
    s.set_chapter(pid, cid, status="done")                     # user finishes it
    s.import_recents([entry])                                   # next launch
    assert s.get_chapter(pid, cid)["status"] == "done"         # preserved, not reverted


def test_sidecar_unifies_url_and_folder_identity(tmp_path):
    """A chapter prepped from a URL, then re-imported from its output folder,
    must land in the SAME project (not a separate `folder:` one)."""
    s = _store(tmp_path)
    pid, cid = s.add_chapter(COMIX)
    assert pid.startswith("comix:")
    out_dir = tmp_path / "why-the-villainess" / "9356816-chapter-1"
    (out_dir / "typeset").mkdir(parents=True)
    layout = str(out_dir / "typeset" / "layout.json")
    # reaching ready with an output_dir stamps the identity sidecar there
    s.set_chapter(pid, cid, status="ready", output_dir=str(out_dir), layout=layout)
    # re-import the same chapter from its folder (as import_recents does)
    s.import_recents([{"layout": layout, "chapter": "ch1", "thumb": ""}])
    assert len(s.list_projects()) == 1                       # not split in two
    assert len(s.get_project(pid)["chapters"]) == 1          # not duplicated


def test_folder_without_sidecar_falls_back_to_folder_id(tmp_path):
    """No sidecar -> the plain folder heuristic still applies."""
    s = _store(tmp_path)
    folder = tmp_path / "Some Series" / "ch9"
    (folder / "typeset").mkdir(parents=True)
    layout = str(folder / "typeset" / "layout.json")
    s.import_recents([{"layout": layout, "chapter": "ch9", "thumb": ""}])
    assert any(p["id"].startswith("folder:") for p in s.list_projects())
