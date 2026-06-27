"""Cooperative pause/stop control for a running pipeline.

The pipeline calls control.checkpoint() at safe points (between pages). That
call blocks while paused and raises PipelineStopped when stop is requested, so
work halts at the next page boundary rather than mid-page.
"""

from __future__ import annotations

import threading


class PipelineStopped(Exception):
    """Raised from checkpoint() when the user requests stop."""


class Control:
    def __init__(self):
        self._stop = threading.Event()
        self._resume = threading.Event()
        self._resume.set()  # start running (not paused)

    def request_stop(self):
        self._stop.set()
        self._resume.set()  # unblock if currently paused

    def pause(self):
        if not self._stop.is_set():
            self._resume.clear()

    def resume(self):
        self._resume.set()

    def is_paused(self) -> bool:
        return not self._resume.is_set()

    def is_stopped(self) -> bool:
        return self._stop.is_set()

    def checkpoint(self):
        """Block while paused; raise PipelineStopped if stop was requested."""
        self._resume.wait()
        if self._stop.is_set():
            raise PipelineStopped()
