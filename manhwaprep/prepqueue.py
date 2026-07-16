"""Background prep queue: clean chapters one at a time while the user typesets.

`prep_chapter` is the testable core (drives one chapter's status through a real
pipeline.run). `PrepQueue` wraps it in a single worker thread that drains the
ProjectStore queue and emits Qt signals for the UI."""

from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import QObject, QThread, Signal

from . import pipeline
from .control import Control, PipelineStopped


def prep_chapter(store, proj_id, chap_id, control=None,
                 on_status=None, on_progress=None) -> str:
    ch = store.get_chapter(proj_id, chap_id)
    if ch is None:
        return "missing"
    store.set_chapter(proj_id, chap_id, status="prepping", error=None)
    if on_status:
        on_status(proj_id, chap_id, "prepping")
    proj = store.get_project(proj_id)
    lang = (proj or {}).get("lang") or "ko"
    out_root = store.series_dir(proj_id)
    try:
        out_dir, outputs = pipeline.run(
            ch["source"], out_root=out_root, clean=True, inpaint="migan",
            typeset=lang, control=control,
            on_progress=(lambda st, d, t: on_progress(proj_id, chap_id, st, d, t))
            if on_progress else None,
        )
    except PipelineStopped:
        store.set_chapter(proj_id, chap_id, status="queued")
        if on_status:
            on_status(proj_id, chap_id, "queued")
        return "queued"
    except Exception as e:
        store.set_chapter(proj_id, chap_id, status="error", error=str(e))
        if on_status:
            on_status(proj_id, chap_id, "error")
        return "error"
    layout = outputs[0] if outputs else None
    thumb = os.path.join(out_dir, "typeset", "canvas_001.png")
    store.set_chapter(proj_id, chap_id, status="ready", layout=layout,
                      thumb=thumb, output_dir=out_dir, prepped_at=time.time())
    if on_status:
        on_status(proj_id, chap_id, "ready")
    return "ready"


class PrepQueue(QObject):
    status_changed = Signal(str, str, str)          # proj_id, chap_id, status
    progress = Signal(str, str, str, int, int)      # proj_id, chap_id, stage, done, total

    def __init__(self, store):
        super().__init__()
        self._store = store
        self._wake = threading.Event()
        self._running = True
        self._control = None
        self._thread = QThread()
        self.moveToThread(self._thread)
        self._thread.started.connect(self._loop)

    def start(self) -> None:
        self._thread.start()

    def enqueue(self, proj_id: str, chap_id: str) -> None:
        self._store.enqueue(proj_id, chap_id)
        self._wake.set()

    def skip_current(self) -> None:
        c = self._control          # snapshot: _loop may null it concurrently
        if c is not None:
            c.request_stop()

    def stop(self) -> None:
        self._running = False
        c = self._control          # snapshot: _loop may null it concurrently
        if c is not None:
            c.request_stop()
        self._wake.set()
        self._thread.quit()
        self._thread.wait(3000)

    def _loop(self) -> None:
        while self._running:
            job = self._store.pop_next()
            if job is None:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            proj_id, chap_id = job
            self._control = Control()
            prep_chapter(
                self._store, proj_id, chap_id, control=self._control,
                on_status=lambda p, c, s: self.status_changed.emit(p, c, s),
                on_progress=lambda p, c, st, d, t: self.progress.emit(p, c, st, d, t),
            )
            self._control = None
