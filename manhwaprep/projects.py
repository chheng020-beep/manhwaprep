"""On-disk registry of Projects (series) and their chapters, plus a prep queue.

A Project groups chapters detected from the same series. Each chapter carries a
status (queued|prepping|ready|done|error), live progress, and the paths the
typeset editor needs. Backed by projects.json via jsonstore (atomic + locked)."""

from __future__ import annotations

import os
import shutil
import time

from . import config, jsonstore, series


def registry_path() -> str:
    base = os.path.dirname(config.default_output_dir())  # ~/Desktop/ManhwaPrep
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "projects.json")


# A chapter's output folder carries this sidecar recording the series/chapter
# identity it was prepped under. It lets a later folder-based re-import (from the
# recents list) recover the SAME identity a URL prep used, instead of detecting a
# separate `folder:<parent>` project — so one physical chapter is never split
# across two projects.
_SIDECAR = ".mp_project.json"


def _read_sidecar(folder: str) -> dict | None:
    try:
        path = os.path.join(folder, _SIDECAR)
        if os.path.isfile(path):
            d = jsonstore.read_json(path, None)
            if isinstance(d, dict) and d.get("series_id") and d.get("chapter_id"):
                return d
    except Exception:
        pass
    return None


def _identify(source: str) -> series.SeriesInfo:
    """Series/chapter identity for a source. A chapter output folder that carries
    an identity sidecar reuses that recorded identity; everything else falls back
    to the pure URL/folder heuristic in series.detect()."""
    try:
        if source and os.path.isdir(source):
            d = _read_sidecar(source)
            if d:
                return series.SeriesInfo(
                    series_id=d["series_id"],
                    series_name=d.get("series_name") or d["series_id"],
                    chapter_id=d["chapter_id"],
                    chapter_name=d.get("chapter_name") or d["chapter_id"],
                    chapter_number=d.get("chapter_number"),
                    series_url=d.get("series_url"),
                )
    except Exception:
        pass
    return series.detect(source)


class ProjectStore:
    def __init__(self, path: str):
        self.path = path

    # -- low level ----------------------------------------------------
    def _load(self) -> dict:
        data = jsonstore.read_json(self.path, None)
        if not isinstance(data, dict):
            return {"projects": [], "queue": []}
        data.setdefault("projects", [])
        data.setdefault("queue", [])
        return data

    def _save(self, data: dict) -> None:
        try:
            jsonstore.atomic_write(self.path, data)
        except Exception:
            pass

    def _find_project(self, data: dict, proj_id: str) -> dict | None:
        for p in data["projects"]:
            if p["id"] == proj_id:
                return p
        return None

    @staticmethod
    def _find_chapter(proj: dict, chap_id: str) -> dict | None:
        for c in proj.get("chapters", []):
            if c["id"] == chap_id:
                return c
        return None

    # -- queries (fresh read each call) -------------------------------
    def list_projects(self) -> list[dict]:
        return self._load()["projects"]

    def get_project(self, proj_id: str) -> dict | None:
        return self._find_project(self._load(), proj_id)

    def get_chapter(self, proj_id: str, chap_id: str) -> dict | None:
        proj = self.get_project(proj_id)
        return self._find_chapter(proj, chap_id) if proj else None

    def series_dir(self, proj_id: str) -> str:
        proj = self.get_project(proj_id)
        name = proj["name"] if proj else proj_id
        return os.path.join(config.default_output_dir(), series.slugify(name))

    # -- mutations (read-modify-write under lock) ---------------------
    def add_chapter(self, source: str, lang: str = "ko") -> tuple[str, str]:
        info = _identify(source)
        with jsonstore.locked(self.path):
            data = self._load()
            proj = self._find_project(data, info.series_id)
            if proj is None:
                proj = {
                    "id": info.series_id, "name": info.series_name,
                    "series_url": info.series_url, "lang": lang,
                    "created_at": time.time(), "updated_at": time.time(),
                    "chapters": [],
                }
                data["projects"].append(proj)
            if self._find_chapter(proj, info.chapter_id) is None:
                proj["chapters"].append({
                    "id": info.chapter_id, "name": info.chapter_name,
                    "number": info.chapter_number, "source": source,
                    "status": "queued", "progress": None,
                    "output_dir": None, "layout": None, "thumb": None,
                    "error": None, "queued_at": time.time(),
                    "prepped_at": None, "done_at": None,
                })
                proj["chapters"].sort(key=lambda c: (c["number"] is None, c["number"] or 0))
                proj["updated_at"] = time.time()
                self._save(data)
            return info.series_id, info.chapter_id

    def set_chapter(self, proj_id: str, chap_id: str, **fields) -> None:
        with jsonstore.locked(self.path):
            data = self._load()
            proj = self._find_project(data, proj_id)
            if proj is None:
                return
            ch = self._find_chapter(proj, chap_id)
            if ch is None:
                return
            ch.update(fields)
            proj["updated_at"] = time.time()
            self._save(data)
            if "output_dir" in fields:
                self._write_sidecar(proj, ch)

    def _write_sidecar(self, proj: dict, ch: dict) -> None:
        """Stamp the chapter's output folder with its series/chapter identity, so
        a later folder-based re-import groups it under this same project."""
        out = ch.get("output_dir")
        if not out or not os.path.isdir(out):
            return
        path = os.path.join(out, _SIDECAR)
        if os.path.exists(path):
            return
        try:
            jsonstore.atomic_write(path, {
                "series_id": proj["id"], "series_name": proj["name"],
                "series_url": proj.get("series_url"),
                "chapter_id": ch["id"], "chapter_name": ch.get("name"),
                "chapter_number": ch.get("number"),
            })
        except Exception:
            pass

    def enqueue(self, proj_id: str, chap_id: str) -> None:
        with jsonstore.locked(self.path):
            data = self._load()
            entry = [proj_id, chap_id]
            if entry not in data["queue"]:
                data["queue"].append(entry)
                self._save(data)

    def pop_next(self) -> tuple[str, str] | None:
        with jsonstore.locked(self.path):
            data = self._load()
            if not data["queue"]:
                return None
            proj_id, chap_id = data["queue"].pop(0)
            self._save(data)
            return proj_id, chap_id

    def remove_chapter(self, proj_id: str, chap_id: str,
                       delete_files: bool = False) -> None:
        with jsonstore.locked(self.path):
            data = self._load()
            proj = self._find_project(data, proj_id)
            if proj is None:
                return
            ch = self._find_chapter(proj, chap_id)
            if ch is not None:
                if delete_files and ch.get("output_dir") and os.path.isdir(ch["output_dir"]):
                    shutil.rmtree(ch["output_dir"], ignore_errors=True)
                proj["chapters"] = [c for c in proj["chapters"] if c["id"] != chap_id]
            data["queue"] = [e for e in data["queue"] if e != [proj_id, chap_id]]
            self._save(data)

    def reset_prepping(self) -> None:
        with jsonstore.locked(self.path):
            data = self._load()
            for proj in data["projects"]:
                for ch in proj.get("chapters", []):
                    if ch.get("status") == "prepping":
                        ch["status"] = "queued"
                        entry = [proj["id"], ch["id"]]
                        if entry not in data["queue"]:
                            data["queue"].insert(0, entry)
            self._save(data)

    def import_recents(self, entries: list[dict]) -> None:
        for e in entries:
            layout = e.get("layout", "")
            if not layout:
                continue
            # source = the chapter's output folder (parent of typeset/)
            out_dir = os.path.dirname(os.path.dirname(layout))
            # Import each recents entry only ONCE. import_recents runs on every
            # launch, so if the chapter is already tracked (possibly marked
            # `done`), leave it alone — never clobber the user's status/fields.
            info = _identify(out_dir)
            if self.get_chapter(info.series_id, info.chapter_id) is not None:
                continue
            pid, cid = self.add_chapter(out_dir)
            self.set_chapter(pid, cid, status="ready", layout=layout,
                             thumb=e.get("thumb", ""), output_dir=out_dir,
                             name=e.get("chapter") or None)
