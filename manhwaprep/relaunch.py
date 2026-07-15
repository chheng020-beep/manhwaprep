"""Launch a second, independent copy of the app.

Lets the user open another window (a separate OS process) so they can prep one
chapter while working another. Command differs for source vs frozen builds.
"""

from __future__ import annotations

import subprocess
import sys


def launch_argv() -> list[str]:
    """Argv that relaunches this app, correct for source and frozen builds."""
    if getattr(sys, "frozen", False):
        return [sys.executable]  # PyInstaller .exe relaunches itself
    return [sys.executable, "-m", "manhwaprep"]


def spawn_new_window() -> None:
    """Launch a detached, fully independent copy of the app.

    The new process survives after the launching window closes.
    """
    argv = launch_argv()
    if sys.platform == "win32":
        # DETACHED_PROCESS (0x8) | CREATE_NEW_PROCESS_GROUP (0x200)
        flags = 0x00000008 | 0x00000200
        subprocess.Popen(argv, creationflags=flags, close_fds=True)
    else:
        subprocess.Popen(argv, start_new_session=True, close_fds=True)
