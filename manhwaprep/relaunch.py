"""Launch a second, independent copy of the app.

Lets the user open another window (a separate OS process) so they can prep one
chapter while working another. Command differs for source vs frozen builds.
"""

from __future__ import annotations

import sys


def launch_argv() -> list[str]:
    """Argv that relaunches this app, correct for source and frozen builds."""
    if getattr(sys, "frozen", False):
        return [sys.executable]  # PyInstaller .exe relaunches itself
    return [sys.executable, "-m", "manhwaprep"]
