import json
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "/Users/leapheakuoch/ManhwaPrep")
from manhwaprep import config, recents


def _point_registry_at(tmp_path, monkeypatch):
    # recents uses dirname(default_output_dir()) as the base for its JSON files
    out = tmp_path / "ManhwaPrep" / "output"
    out.mkdir(parents=True)
    monkeypatch.setattr(config, "default_output_dir", lambda: str(out))


def test_two_writes_both_survive(tmp_path, monkeypatch):
    _point_registry_at(tmp_path, monkeypatch)
    a = tmp_path / "ch4" / "layout.json"; a.parent.mkdir(parents=True); a.write_text("{}")
    b = tmp_path / "ch5" / "layout.json"; b.parent.mkdir(parents=True); b.write_text("{}")
    recents.add_recent(str(a), chapter="ch4")
    recents.add_recent(str(b), chapter="ch5")
    layouts = {e["chapter"] for e in recents.list_recent()}
    assert layouts == {"ch4", "ch5"}


def test_merge_against_external_write(tmp_path, monkeypatch):
    # Simulate a second process having written entry B to disk *after* this
    # process last read: add_recent must merge, not clobber.
    _point_registry_at(tmp_path, monkeypatch)
    a = tmp_path / "ch4" / "layout.json"; a.parent.mkdir(parents=True); a.write_text("{}")
    recents.add_recent(str(a), chapter="ch4")
    # external process appends entry B directly to the registry file
    reg = recents._registry_path()
    data = json.load(open(reg))
    b = tmp_path / "ch5" / "layout.json"; b.parent.mkdir(parents=True); b.write_text("{}")
    data.insert(0, {"layout": os.path.abspath(str(b)), "chapter": "ch5",
                    "thumb": "", "saved_at": 1.0})
    json.dump(data, open(reg, "w"))
    # now this process bumps ch4 again — must preserve ch5
    recents.add_recent(str(a), chapter="ch4")
    chapters = {e["chapter"] for e in recents.list_recent()}
    assert chapters == {"ch4", "ch5"}


def test_no_leftover_temp_file(tmp_path, monkeypatch):
    _point_registry_at(tmp_path, monkeypatch)
    a = tmp_path / "ch4" / "layout.json"; a.parent.mkdir(parents=True); a.write_text("{}")
    recents.add_recent(str(a), chapter="ch4")
    base = os.path.dirname(recents._registry_path())
    leftovers = [f for f in os.listdir(base) if f.endswith(".tmp")]
    assert leftovers == []


def test_add_font_dedupe_and_cap_under_new_path(tmp_path, monkeypatch):
    _point_registry_at(tmp_path, monkeypatch)
    for i in range(15):
        recents.add_font(f"Font{i}")
    recents.add_font("Font0")  # re-adding bumps to front, no dupe
    fonts = recents.list_fonts()
    assert fonts[0] == "Font0"
    assert len(fonts) <= 10
    assert len(fonts) == len(set(fonts))


def test_add_recent_degrades_when_lock_file_fails(tmp_path, monkeypatch):
    """Verify that _locked() degrades to no-op if lock file can't be opened.

    This is the key resilience test: if the lock file itself fails to open
    (permissions, disk full, network FS readonly), add_recent() must still
    complete and write the entry rather than crash.
    """
    _point_registry_at(tmp_path, monkeypatch)
    a = tmp_path / "ch4" / "layout.json"; a.parent.mkdir(parents=True); a.write_text("{}")

    # Monkeypatch builtins.open to raise OSError only for .lock paths
    import builtins
    original_open = builtins.open
    def fail_on_lock(*args, **kwargs):
        path = args[0] if args else kwargs.get("file")
        if isinstance(path, str) and path.endswith(".lock"):
            raise OSError("Simulated lock file open failure (permission denied)")
        return original_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", fail_on_lock)

    # This should NOT crash even though lock file open fails
    recents.add_recent(str(a), chapter="ch4")

    # Verify the entry was still written
    entries = recents.list_recent()
    assert len(entries) == 1
    assert entries[0]["chapter"] == "ch4"
    assert entries[0]["layout"] == os.path.abspath(str(a))
