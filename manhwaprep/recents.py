"""Recently-opened typeset projects, for the app's home screen.

A tiny JSON registry (in the app data dir) of chapters you've opened/saved in the
typeset editor, so the launcher can show clickable cards to continue where you
left off.
"""

from __future__ import annotations

import contextlib
import json
import os
import time

from . import config


@contextlib.contextmanager
def _locked(path: str):
    """Advisory cross-process lock around a registry read-merge-write.

    Uses fcntl on posix and msvcrt on Windows; degrades to a no-op if locking
    is unavailable so the app never crashes over recents bookkeeping.
    """
    lock_path = path + ".lock"
    f = None
    try:
        f = open(lock_path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass  # best-effort lock; still safe-ish via atomic replace
        yield
    finally:
        if f is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            f.close()


def _atomic_write(path: str, data) -> None:
    """Write JSON to a temp file in the same dir, then os.replace() onto path."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _registry_path() -> str:
    base = os.path.dirname(config.default_output_dir())  # ~/ManhwaPrep
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "recent_projects.json")


def _read() -> list[dict]:
    p = _registry_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def list_recent() -> list[dict]:
    """Most-recent-first, dropping any whose layout no longer exists."""
    out = [e for e in _read() if os.path.exists(e.get("layout", ""))]
    out.sort(key=lambda e: e.get("saved_at", 0), reverse=True)
    return out


def _fonts_path() -> str:
    base = os.path.dirname(config.default_output_dir())
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "recent_fonts.json")


def list_fonts() -> list[str]:
    p = _fonts_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            return [str(x) for x in json.load(f)]
    except Exception:
        return []


def add_font(name: str) -> None:
    """Push a font family to the front of the recently-used list."""
    if not name:
        return
    fp = _fonts_path()
    with _locked(fp):
        data = [f for f in list_fonts() if f != name]
        data.insert(0, name)
        try:
            _atomic_write(fp, data[:10])
        except Exception:
            pass


def add_recent(layout_path: str, chapter: str = "", thumb: str = "") -> None:
    """Record (or bump) a project. layout_path is the chapter's layout.json."""
    layout_path = os.path.abspath(layout_path)
    reg = _registry_path()
    with _locked(reg):
        data = [e for e in _read() if e.get("layout") != layout_path]
        data.insert(0, {
            "layout": layout_path,
            "chapter": chapter or "",
            "thumb": thumb or "",
            "saved_at": time.time(),
        })
        try:
            _atomic_write(reg, data[:30])
        except Exception:
            pass
