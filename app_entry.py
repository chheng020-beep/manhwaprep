"""PyInstaller entry point for the ManhwaPrep GUI (Windows .exe).

Wraps startup with a crash logger: hard crashes (faulthandler) and Python
exceptions are written to <LOCALAPPDATA>/ManhwaPrep/crash.log so failures on
machines we can't debug directly are still diagnosable.
"""

import faulthandler
import os
import sys
import traceback


def _crash_log_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "ManhwaPrep")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "crash.log")


def main():
    log = _crash_log_path()
    try:
        faulthandler.enable(open(log, "w"))  # captures C-level crashes (segfaults)
    except Exception:
        pass
    try:
        from manhwaprep.ui import main as gui_main

        gui_main()
    except Exception:
        with open(log, "a", encoding="utf-8") as f:
            f.write("\n\n=== PYTHON EXCEPTION ===\n")
            f.write(traceback.format_exc())
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(
                None,
                "ManhwaPrep crashed",
                f"Error (also saved to {log}):\n\n{traceback.format_exc()[-1500:]}",
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()
