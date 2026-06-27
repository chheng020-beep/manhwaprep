"""Single-window PySide6 UI: drop a folder or paste a link, press Go."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .control import Control, PipelineStopped
from .engine import TextCleaner
from .pipeline import run


def _translation_available() -> bool:
    """The translate feature needs CTranslate2; the Windows core build omits it."""
    try:
        import ctranslate2  # noqa: F401

        return True
    except Exception:
        return False


def _ocr_available() -> bool:
    """Transcript/translate OCR needs rapidocr; the Windows core build omits it."""
    try:
        import rapidocr  # noqa: F401

        return True
    except Exception:
        return False


class Worker(QObject):
    status = Signal(str)
    progress = Signal(str, int, int)
    done = Signal(str, list)
    failed = Signal(str)
    stopped = Signal()

    def __init__(
        self,
        source: str,
        segments: int,
        clean: bool,
        inpaint: str,
        keep_sfx: bool,
        translate,
        transcript,
        control,
    ):
        super().__init__()
        self.source = source
        self.segments = segments
        self.clean = clean
        self.inpaint = inpaint
        self.keep_sfx = keep_sfx
        self.translate = translate
        self.transcript = transcript
        self.control = control

    def go(self):
        try:
            cleaner = None
            if self.clean:
                # Build the cleaner here so model load happens off the UI thread.
                self.status.emit("Loading model…")
                cleaner = TextCleaner(
                    inpaint=self.inpaint, include_sfx=not self.keep_sfx
                )
                self.status.emit(
                    f"inpaint: {cleaner.backend} · detect: {cleaner.detectors}"
                )
            out_dir, outputs = run(
                self.source,
                segments=self.segments,
                clean=self.clean,
                inpaint=self.inpaint,
                keep_sfx=self.keep_sfx,
                translate=self.translate,
                transcript=self.transcript,
                cleaner=cleaner,
                control=self.control,
                on_status=self.status.emit,
                on_progress=lambda s, d, t: self.progress.emit(s, d, t),
            )
            self.done.emit(out_dir, outputs)
        except PipelineStopped:
            self.stopped.emit()
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(str(e))


class DropZone(QFrame):
    """A dashed box that accepts a dropped folder."""

    dropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setFixedHeight(120)
        self.setStyleSheet(
            "QFrame{border:2px dashed #8a8a8a;border-radius:12px;"
            "background:#fafafa;}"
        )
        lay = QVBoxLayout(self)
        lab = QLabel("Drop a chapter folder here")
        lab.setAlignment(Qt.AlignCenter)
        lab.setStyleSheet("border:none;color:#666;font-size:15px;")
        lay.addWidget(lab)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path):
                self.dropped.emit(path)
                return
            if os.path.isfile(path):  # a file -> use its folder
                self.dropped.emit(os.path.dirname(path))
                return


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ManhwaPrep")
        self.resize(560, 560)
        self._thread = None
        self._worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("ManhwaPrep")
        title.setFont(QFont("", 22, QFont.Bold))
        root.addWidget(title)
        sub = QLabel("Download → erase text → stitch into long images.")
        sub.setStyleSheet("color:#777;")
        root.addWidget(sub)

        self.drop = DropZone()
        self.drop.dropped.connect(self._on_folder)
        root.addWidget(self.drop)

        browse = QPushButton("…or choose a folder")
        browse.clicked.connect(self._browse)
        root.addWidget(browse)

        # URL row
        url_row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText("…or paste a chapter URL (e.g. 11toon)")
        url_row.addWidget(self.url)
        root.addLayout(url_row)

        # segments row
        seg_row = QHBoxLayout()
        seg_row.addWidget(QLabel("Stitch into ~"))
        self.segments = QSpinBox()
        self.segments.setRange(1, 50)
        self.segments.setValue(5)
        seg_row.addWidget(self.segments)
        seg_row.addWidget(QLabel("long images"))
        seg_row.addStretch(1)
        root.addLayout(seg_row)

        self.remove_text = QCheckBox("Remove text (erase original speech)")
        self.remove_text.setChecked(True)
        root.addWidget(self.remove_text)

        self.keep_sfx = QCheckBox("Keep SFX / action text (erase speech bubbles only)")
        self.keep_sfx.setChecked(False)
        root.addWidget(self.keep_sfx)

        q_row = QHBoxLayout()
        q_row.addWidget(QLabel("Cleaning quality:"))
        self.quality = QComboBox()
        self.quality.addItem("Balanced — MI-GAN (GPU, fast)", "migan")
        self.quality.addItem("Best — LaMa (CPU, slow)", "lama")
        self.quality.addItem("Fast — Telea (no model)", "telea")
        q_row.addWidget(self.quality)
        q_row.addStretch(1)
        root.addLayout(q_row)

        # Show which detection model is active (RT-DETR is automatic, not a choice).
        has_rtdetr = os.path.exists(
            os.path.expanduser("~/ManhwaPrep/models/detector_int8.onnx")
        )
        det_name = "comic-translate RT-DETR ✓" if has_rtdetr else "PP-OCR (fallback)"
        self.det_info = QLabel(f"🧠 Detection: {det_name}")
        self.det_info.setStyleSheet(
            "color:#1a9e4b;" if has_rtdetr else "color:#a06000;"
        )
        root.addWidget(self.det_info)

        # These only matter when removing text.
        self.remove_text.toggled.connect(self.quality.setEnabled)
        self.remove_text.toggled.connect(self.keep_sfx.setEnabled)

        # translate row — only when the translation backend is installed
        # (the Windows "core cleaner" build ships without it).
        self.translate = None
        if _translation_available():
            tr_row = QHBoxLayout()
            tr_row.addWidget(QLabel("Translate to Khmer:"))
            self.translate = QComboBox()
            self.translate.addItem("Off", None)
            self.translate.addItem("from Korean", "ko")
            self.translate.addItem("from English", "en")
            tr_row.addWidget(self.translate)
            tr_row.addStretch(1)
            self.edit_btn = QPushButton("Open translation editor")
            self.edit_btn.clicked.connect(self._open_editor)
            tr_row.addWidget(self.edit_btn)
            root.addLayout(tr_row)

        # transcript row — export numbered text for translating in Claude
        self.transcript = None
        if _ocr_available():
            tx_row = QHBoxLayout()
            tx_row.addWidget(QLabel("Transcript (for Claude):"))
            self.transcript = QComboBox()
            self.transcript.addItem("Off", None)
            self.transcript.addItem("from Korean", "ko")
            self.transcript.addItem("from English", "en")
            tx_row.addWidget(self.transcript)
            tx_row.addStretch(1)
            self.psd_btn = QPushButton("Make Photoshop script…")
            self.psd_btn.clicked.connect(self._make_psd)
            tx_row.addWidget(self.psd_btn)
            root.addLayout(tx_row)

        # action buttons: Go / Pause / Stop
        btn_row = QHBoxLayout()
        self.go = QPushButton("Go")
        self.go.setFixedHeight(40)
        self.go.setStyleSheet(
            "QPushButton{background:#2d7ff9;color:white;border-radius:8px;"
            "font-size:16px;font-weight:bold;}"
            "QPushButton:disabled{background:#9bbcf0;}"
        )
        self.go.clicked.connect(self._start_from_url)
        btn_row.addWidget(self.go, 2)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setFixedHeight(40)
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._toggle_pause)
        btn_row.addWidget(self.pause_btn, 1)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setFixedHeight(40)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(
            "QPushButton{background:#d12d2d;color:white;border-radius:8px;"
            "font-weight:bold;} QPushButton:disabled{background:#e8a3a3;}"
        )
        self.stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self.stop_btn, 1)
        root.addLayout(btn_row)

        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        root.addWidget(self.bar)

        # Live "it's alive" indicator: spinner + elapsed time, driven by a timer
        # so it keeps moving even during a slow single step (e.g. a LaMa page).
        self.activity = QLabel("")
        self.activity.setStyleSheet("color:#2d7ff9;font-size:14px;font-weight:bold;")
        root.addWidget(self.activity)
        self._spin_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._spin_i = 0
        self._t0 = None
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._tick)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(150)
        root.addWidget(self.log)

        self.open_btn = QPushButton("Open output folder")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_output)
        root.addWidget(self.open_btn)

        self._last_out = None
        self._control = None
        self._pause_accum = 0.0
        self._pause_start = None

    # -- actions -------------------------------------------------------
    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Choose chapter folder")
        if path:
            self._on_folder(path)

    def _on_folder(self, path: str):
        self.url.clear()
        self._start(path)

    def _start_from_url(self):
        src = self.url.text().strip()
        if not src:
            self._append("Paste a URL or drop a folder first.")
            return
        self._start(src)

    def _start(self, source: str):
        self.log.clear()
        self.bar.setRange(0, 0)  # busy until first progress
        self.go.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setText("Pause")
        self.stop_btn.setEnabled(True)
        self._append(f"Source: {source}")
        self.activity.setStyleSheet(
            "color:#2d7ff9;font-size:14px;font-weight:bold;"
        )
        self._t0 = time.monotonic()
        self._spin_i = 0
        self._pause_accum = 0.0
        self._pause_start = None
        self._control = Control()
        self._timer.start()
        self._tick()

        self._thread = QThread()
        self._worker = Worker(
            source,
            self.segments.value(),
            self.remove_text.isChecked(),
            self.quality.currentData(),
            self.keep_sfx.isChecked(),
            self.translate.currentData() if self.translate else None,
            self.transcript.currentData() if self.transcript else None,
            self._control,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.go)
        self._worker.status.connect(self._append)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.stopped.connect(self._on_stopped)
        self._thread.start()

    def _toggle_pause(self):
        if not self._control:
            return
        if self._control.is_paused():
            self._pause_accum += time.monotonic() - self._pause_start
            self._pause_start = None
            self._control.resume()
            self.pause_btn.setText("Pause")
        else:
            self._pause_start = time.monotonic()
            self._control.pause()
            self.pause_btn.setText("Resume")

    def _stop(self):
        if self._control:
            self._control.request_stop()
        self._append("Stopping after the current page…")
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)

    def _make_psd(self):
        from PySide6.QtWidgets import QMessageBox

        from . import psgen

        start = self._last_out or os.path.expanduser("~/ManhwaPrep/output")
        khmer_file, _ = QFileDialog.getOpenFileName(
            self, "Select Claude's numbered Khmer file", start, "Text (*.txt *.md)"
        )
        if not khmer_file:
            return
        out_dir = os.path.dirname(khmer_file)
        if not os.path.exists(os.path.join(out_dir, "transcript.json")):
            QMessageBox.warning(
                self, "No transcript.json",
                "Put the Khmer file in the chapter's output folder — the one "
                "that has transcript.json (from the Transcript run).",
            )
            return
        try:
            with open(khmer_file, encoding="utf-8") as f:
                km = psgen.parse_khmer_list(f.read())
            if not km:
                QMessageBox.warning(
                    self, "No numbered lines",
                    "Couldn't find any 'N. text' lines in that file.",
                )
                return
            written = psgen.generate(out_dir, km)
            total = sum(n for _, n in written)
            jdir = os.path.join(out_dir, "photoshop")
            QMessageBox.information(
                self, "Photoshop scripts ready",
                f"Wrote {len(written)} script(s), {total} text layers.\n\n"
                f"In Photoshop: open a cleaned page → File ▸ Scripts ▸ Browse ▸ "
                f"page_001.jsx",
            )
            _open_folder(jdir)
        except Exception as e:
            QMessageBox.critical(self, "Failed", str(e))

    def _open_editor(self):
        from .editor import EditorWindow

        start = self._last_out or os.path.expanduser("~/ManhwaPrep/output")
        cand = os.path.join(start, "translation.json") if self._last_out else ""
        if not (cand and os.path.exists(cand)):
            cand, _ = QFileDialog.getOpenFileName(
                self, "Open translation.json", start, "Translation (translation.json)"
            )
        if cand and os.path.exists(cand):
            self._editor = EditorWindow(cand)
            self._editor.show()

    # -- signals -------------------------------------------------------
    @staticmethod
    def _fmt(seconds: float) -> str:
        s = int(seconds)
        return f"{s // 60:02d}:{s % 60:02d}"

    def _elapsed(self) -> float:
        if self._t0 is None:
            return 0.0
        paused = self._pause_accum
        if self._pause_start is not None:  # currently paused
            paused += time.monotonic() - self._pause_start
        return time.monotonic() - self._t0 - paused

    def _tick(self):
        if self._t0 is None:
            return
        if self._control and self._control.is_paused():
            self.activity.setText(f"⏸  paused  {self._fmt(self._elapsed())}")
            return
        frame = self._spin_frames[self._spin_i % len(self._spin_frames)]
        self._spin_i += 1
        self.activity.setText(f"{frame}  running…  {self._fmt(self._elapsed())}")

    def _append(self, msg: str):
        self.log.appendPlainText(msg)

    def _on_progress(self, stage: str, done: int, total: int):
        self.bar.setRange(0, total)
        self.bar.setValue(done)
        self.bar.setFormat(f"{stage}: {done}/{total}")

    def _finish(self):
        self._timer.stop()
        elapsed = self._fmt(self._elapsed()) if self._t0 else "00:00"
        self._t0 = None
        if self._thread:
            self._thread.quit()
            self._thread.wait()
        self.go.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("Pause")
        self.stop_btn.setEnabled(False)
        self.bar.setRange(0, 1)
        return elapsed

    def _on_done(self, out_dir: str, outputs: list):
        self._last_out = out_dir
        self.bar.setValue(self.bar.maximum())
        elapsed = self._finish()
        self._append(f"✓ {len(outputs)} image(s) ready in {elapsed}.")
        self.activity.setText(f"✓ done in {elapsed}")
        self.activity.setStyleSheet("color:#1a9e4b;font-size:14px;font-weight:bold;")
        self.open_btn.setEnabled(True)

    def _on_failed(self, err: str):
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        elapsed = self._finish()
        self._append(f"✗ {err}")
        self.activity.setText(f"✗ failed after {elapsed}")
        self.activity.setStyleSheet("color:#d12d2d;font-size:14px;font-weight:bold;")

    def _on_stopped(self):
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        elapsed = self._finish()
        self._append("■ stopped by user.")
        self.activity.setText(f"■ stopped after {elapsed}")
        self.activity.setStyleSheet("color:#a06000;font-size:14px;font-weight:bold;")

    def _open_output(self):
        if self._last_out and os.path.isdir(self._last_out):
            _open_folder(self._last_out)


def _open_folder(path: str):
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.run(["open", path])
    else:
        subprocess.run(["xdg-open", path])


def _ensure_models_or_quit(app) -> bool:
    """Download missing core models on the MAIN thread (no QThread).

    Updating a widget from a background thread is a common cause of hard
    crashes in packaged apps, so we download here and keep the dialog alive
    with processEvents() between chunks instead.
    """
    from PySide6.QtWidgets import QMessageBox

    from .setup_models import download_model, missing_core_models

    missing = missing_core_models()
    if not missing:
        return True

    dlg = QProgressDialog("Preparing models (first run)…", None, 0, 100)
    dlg.setWindowTitle("ManhwaPrep — first-time setup")
    dlg.setCancelButton(None)
    dlg.setMinimumWidth(440)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setValue(0)
    dlg.show()
    app.processEvents()

    count = len(missing)
    try:
        for i, name in enumerate(missing):
            def on_prog(n, d, t, i=i):
                overall = int(((i + d / max(t, 1)) / count) * 100)
                dlg.setLabelText(f"Downloading {n}\n{d // 1_000_000} / {t // 1_000_000} MB")
                dlg.setValue(min(99, overall))
                app.processEvents()

            download_model(name, on_progress=on_prog)
        dlg.setValue(100)
        dlg.close()
        return True
    except Exception as e:
        dlg.close()
        QMessageBox.critical(
            None,
            "Model download failed",
            f"Could not download the required models:\n\n{e}\n\n"
            "Check your internet connection and reopen the app.",
        )
        return False


def main():
    app = QApplication(sys.argv)
    icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.png")
    if os.path.exists(icon_path):
        from PySide6.QtGui import QIcon

        app.setWindowIcon(QIcon(icon_path))
    if not _ensure_models_or_quit(app):
        return
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
