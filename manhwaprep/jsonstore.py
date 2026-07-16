"""Locked, atomic JSON storage shared by the app's on-disk registries.

Advisory cross-process lock (fcntl/msvcrt) + temp-file-then-os.replace writes,
degrading gracefully so the app never crashes over registry bookkeeping.
"""

from __future__ import annotations

import contextlib
import json
import os


@contextlib.contextmanager
def locked(path: str):
    lock_path = path + ".lock"
    f = None
    try:
        try:
            f = open(lock_path, "a+")
        except Exception:
            pass
        if f is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass
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


def read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def atomic_write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
