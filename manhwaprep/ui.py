"""Single-window PySide6 UI: drop a folder or paste a link, press Go."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .batch import batch_download, detect_chapter
from .control import Control, PipelineStopped
from .engine import TextCleaner
from .manual_split import ManualSplitWidget
from .pipeline import run, run_for_batch


def _ocr_available() -> bool:
    """Transcript/typeset OCR needs rapidocr; the Windows core build omits it."""
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
        transcript,
        typeset,
        control,
    ):
        super().__init__()
        self.source = source
        self.segments = segments
        self.clean = clean
        self.inpaint = inpaint
        self.keep_sfx = keep_sfx
        self.transcript = transcript
        self.typeset = typeset
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
                transcript=self.transcript,
                typeset=self.typeset,
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


class BatchWorker(QObject):
    progress = Signal(int, int, str)
    done = Signal(list)
    failed = Signal(str)

    def __init__(self, url, start, count, out_dir, inpaint="migan"):
        super().__init__()
        self.url = url
        self.start = start
        self.count = count
        self.out_dir = out_dir
        self.inpaint = inpaint
        self._stop = False

    def stop(self):
        self._stop = True

    def go(self):
        try:
            # Load models ONCE — reused across every chapter in the batch.
            self.progress.emit(0, self.count, "Loading models…")
            cleaner = TextCleaner(inpaint=self.inpaint, include_sfx=True)
            self.progress.emit(0, self.count,
                               f"Models ready ({cleaner.backend}). Starting batch…")
            zips = batch_download(
                self.url,
                self.start,
                self.count,
                self.out_dir,
                lambda url: run_for_batch(url, self.out_dir, cleaner=cleaner),
                progress_cb=lambda i, n, msg: self.progress.emit(i, n, msg),
                stop_flag=lambda: self._stop,
            )
            self.done.emit(zips)
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(str(e))


class DropZone(QFrame):
    """A dashed box that accepts a dropped folder."""

    dropped = Signal(str)

    def __init__(self, label="Drop a chapter folder here", files_ok=False):
        super().__init__()
        self.files_ok = files_ok  # True -> emit a dropped file's own path
        self.setAcceptDrops(True)
        self.setFixedHeight(120)
        self.setStyleSheet(
            "QFrame{border:2px dashed #8a8a8a;border-radius:12px;"
            "background:#fafafa;}"
        )
        lay = QVBoxLayout(self)
        lab = QLabel(label)
        lab.setAlignment(Qt.AlignCenter)
        lab.setWordWrap(True)
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
            if os.path.isfile(path):
                # split mode keeps the file; clean mode uses its folder
                self.dropped.emit(path if self.files_ok else os.path.dirname(path))
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
        sub = QLabel("Pick a job below, then drop a folder/image (or paste a link).")
        sub.setStyleSheet("color:#777;")
        root.addWidget(sub)

        # Pick the job up top; each tab keeps its own settings so you never
        # re-toggle anything to do a different task.
        self.tabs = QTabWidget()
        self._projects_tab = self._build_projects_tab()
        self._clean_tab = self._build_clean_tab()
        self._split_tab = ManualSplitWidget()
        self.tabs.addTab(self._projects_tab, "🗂 Projects")
        self.tabs.addTab(self._clean_tab, "🧹 Clean & Prepare")
        self.tabs.addTab(self._split_tab, "✂️ Manual Split")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tabs)

        # action buttons: Go / Pause / Stop
        btn_row = QHBoxLayout()
        self.go = QPushButton("Go")
        self.go.setFixedHeight(40)
        self.go.setStyleSheet(
            "QPushButton{background:#2d7ff9;color:white;border-radius:8px;"
            "font-size:16px;font-weight:bold;}"
            "QPushButton:disabled{background:#9bbcf0;}"
        )
        self.go.clicked.connect(self._on_go)
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
        self._batch_thread = None
        self._batch_worker = None
        self._batch_last_out = None

    # -- tab construction ---------------------------------------------
    def _build_projects_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)
        head = QHBoxLayout()
        head.addWidget(QLabel("<b>Recent projects</b> — click to continue"))
        head.addStretch(1)
        refresh = QPushButton("↻ Refresh")
        refresh.clicked.connect(self._refresh_projects)
        head.addWidget(refresh)
        v.addLayout(head)

        self.proj_list = QListWidget()
        self.proj_list.setIconSize(QSize(72, 52))
        self.proj_list.setSpacing(2)
        self.proj_list.itemClicked.connect(self._open_project_item)
        v.addWidget(self.proj_list, 1)
        self._refresh_projects()
        return tab

    def _refresh_projects(self):
        import time as _t

        from . import recents

        self.proj_list.clear()
        entries = recents.list_recent()
        if not entries:
            placeholder = QListWidgetItem(
                "No saved projects yet — run a Typeset job, then Save project.")
            placeholder.setFlags(Qt.NoItemFlags)
            self.proj_list.addItem(placeholder)
            return
        for e in entries:
            name = e.get("chapter") or os.path.basename(
                os.path.dirname(os.path.dirname(e.get("layout", ""))))
            when = e.get("saved_at", 0)
            ds = _t.strftime("%b %d, %H:%M", _t.localtime(when)) if when else ""
            item = QListWidgetItem(f"{name}    ·  {ds}")
            thumb = e.get("thumb", "")
            if thumb and os.path.exists(thumb):
                pm = QPixmap(thumb)
                if not pm.isNull():
                    side = min(pm.width(), pm.height())
                    pm = pm.copy(0, 0, pm.width(), side).scaled(
                        72, 52, Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation)
                    item.setIcon(QIcon(pm))
            item.setData(Qt.UserRole, e.get("layout"))
            item.setToolTip(e.get("layout", ""))
            self.proj_list.addItem(item)

    def _open_project_item(self, item):
        path = item.data(Qt.UserRole)
        if path:
            self._open_typeset(path)

    def _build_clean_tab(self) -> QWidget:
        tab = QWidget()
        cl = QVBoxLayout(tab)
        cl.setSpacing(10)

        self.drop = DropZone("Drop a chapter folder here")
        self.drop.dropped.connect(self._on_clean_drop)
        cl.addWidget(self.drop)

        browse = QPushButton("…or choose a folder")
        browse.clicked.connect(self._browse)
        cl.addWidget(browse)

        self.url = QLineEdit()
        self.url.setPlaceholderText("…or paste a chapter URL (e.g. 11toon)")
        cl.addWidget(self.url)
        self.url.textChanged.connect(self._on_url_changed)

        # -- Batch download section ------------------------------------
        self.batch_chk = QCheckBox("📦 Batch mode — download multiple chapters")
        cl.addWidget(self.batch_chk)

        self._batch_box = QWidget()
        blay = QVBoxLayout(self._batch_box)
        blay.setContentsMargins(12, 0, 0, 0)
        blay.setSpacing(6)

        ch_row = QHBoxLayout()
        ch_row.addWidget(QLabel("Chapters:"))
        self.batch_from = QSpinBox()
        self.batch_from.setRange(1, 9999)
        self.batch_from.setValue(1)
        ch_row.addWidget(self.batch_from)
        ch_row.addWidget(QLabel("to"))
        self.batch_to = QSpinBox()
        self.batch_to.setRange(1, 9999)
        self.batch_to.setValue(10)
        ch_row.addWidget(self.batch_to)
        self.batch_detected = QLabel("")
        self.batch_detected.setStyleSheet("color:#888;font-size:12px;")
        ch_row.addWidget(self.batch_detected)
        ch_row.addStretch(1)
        blay.addLayout(ch_row)

        self.batch_go_btn = QPushButton("📦 Download 10 chapters")
        self.batch_go_btn.setFixedHeight(34)
        self.batch_go_btn.setStyleSheet(
            "QPushButton{background:#6c47d4;color:white;border-radius:6px;font-weight:bold;}"
            "QPushButton:disabled{background:#b8a0e8;}"
        )
        self.batch_go_btn.clicked.connect(self._start_batch)
        blay.addWidget(self.batch_go_btn)

        self.batch_progress = QLabel("")
        self.batch_progress.setWordWrap(True)
        self.batch_progress.setStyleSheet("color:#444;font-size:12px;")
        blay.addWidget(self.batch_progress)

        self.batch_stop_btn = QPushButton("■ Stop batch")
        self.batch_stop_btn.setVisible(False)
        self.batch_stop_btn.clicked.connect(self._stop_batch)
        blay.addWidget(self.batch_stop_btn)

        self._batch_box.setVisible(False)
        cl.addWidget(self._batch_box)

        self.batch_chk.toggled.connect(self._batch_box.setVisible)
        self.batch_from.valueChanged.connect(self._update_batch_btn)
        self.batch_to.valueChanged.connect(self._update_batch_btn)
        # --------------------------------------------------------------

        seg_row = QHBoxLayout()
        seg_row.addWidget(QLabel("Stitch into ~"))
        self.segments = QSpinBox()
        self.segments.setRange(1, 50)
        self.segments.setValue(5)
        seg_row.addWidget(self.segments)
        seg_row.addWidget(QLabel("long images"))
        seg_row.addStretch(1)
        cl.addLayout(seg_row)

        self.remove_text = QCheckBox("Remove text (erase original speech)")
        self.remove_text.setChecked(True)
        cl.addWidget(self.remove_text)

        self.keep_sfx = QCheckBox("Keep SFX / action text (erase speech bubbles only)")
        self.keep_sfx.setChecked(False)
        cl.addWidget(self.keep_sfx)

        q_row = QHBoxLayout()
        q_row.addWidget(QLabel("Cleaning quality:"))
        self.quality = QComboBox()
        self.quality.addItem("Balanced — MI-GAN (GPU, fast)", "migan")
        self.quality.addItem("Best — LaMa (CPU, slow)", "lama")
        self.quality.addItem("Fast — Telea (no model)", "telea")
        q_row.addWidget(self.quality)
        q_row.addStretch(1)
        cl.addLayout(q_row)

        # Show which detection model is active (RT-DETR is automatic, not a choice).
        has_rtdetr = os.path.exists(
            os.path.expanduser("~/ManhwaPrep/models/detector_int8.onnx")
        )
        det_name = "comic-translate RT-DETR ✓" if has_rtdetr else "PP-OCR (fallback)"
        self.det_info = QLabel(f"🧠 Detection: {det_name}")
        self.det_info.setStyleSheet("color:#1a9e4b;" if has_rtdetr else "color:#a06000;")
        cl.addWidget(self.det_info)

        # These only matter when removing text.
        self.remove_text.toggled.connect(self.quality.setEnabled)
        self.remove_text.toggled.connect(self.keep_sfx.setEnabled)

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
            cl.addLayout(tx_row)

        # native typesetting: clean + long canvas + open the Khmer editor
        self.typeset = None
        if _ocr_available():
            ts_row = QHBoxLayout()
            ts_row.addWidget(QLabel("Typeset Khmer (native):"))
            self.typeset = QComboBox()
            self.typeset.addItem("Off", None)
            self.typeset.addItem("from Korean", "ko")
            self.typeset.addItem("from English", "en")
            ts_row.addWidget(self.typeset)
            ts_row.addStretch(1)
            self.edit_ts_btn = QPushButton("Open typeset editor…")
            self.edit_ts_btn.clicked.connect(self._open_typeset)
            ts_row.addWidget(self.edit_ts_btn)
            cl.addLayout(ts_row)

        cl.addStretch(1)
        return tab

    def _on_tab_changed(self, idx: int):
        w = self.tabs.currentWidget()
        on_projects = w is self._projects_tab
        on_manual_split = w is self._split_tab
        # Manual Split tab has its own controls — hide the global Go/Pause/Stop bar
        self.go.setVisible(not on_manual_split)
        self.pause_btn.setVisible(not on_manual_split)
        self.stop_btn.setVisible(not on_manual_split)
        self.bar.setVisible(not on_manual_split)
        self.go.setEnabled(not on_projects and not on_manual_split)
        if on_projects:
            self._refresh_projects()

    # -- actions -------------------------------------------------------
    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Choose chapter folder")
        if path:
            self._on_clean_drop(path)

    def _on_url_changed(self, text: str):
        n, _ = detect_chapter(text.strip())
        if n is not None:
            self.batch_from.setValue(n)
            self.batch_to.setValue(n + 9)
            self.batch_detected.setText(f"(detected ch. {n})")
        else:
            self.batch_detected.setText("")
        self._update_batch_btn()

    def _update_batch_btn(self):
        count = max(0, self.batch_to.value() - self.batch_from.value() + 1)
        self.batch_go_btn.setText(
            f"📦 Download {count} chapter{'s' if count != 1 else ''}"
        )

    def _start_batch(self):
        url = self.url.text().strip()
        if not url:
            self.batch_progress.setText("Paste a URL first.")
            return
        n, _ = detect_chapter(url)
        if n is None:
            self.batch_progress.setText("Could not detect chapter number in URL.")
            return
        start = self.batch_from.value()
        end = self.batch_to.value()
        count = max(1, end - start + 1)

        ts = time.strftime("%Y-%m-%d_%H-%M")
        out_dir = os.path.expanduser(f"~/Desktop/ManhwaPrep/batch/{ts}")
        os.makedirs(out_dir, exist_ok=True)
        self._batch_last_out = out_dir

        self.batch_go_btn.setEnabled(False)
        self.batch_stop_btn.setVisible(True)
        self.batch_stop_btn.setEnabled(True)
        self.batch_progress.setText(f"Starting: chapters {start}–{end} → {out_dir}")
        self._append(f"Batch: chapters {start}–{end} from {url}")

        self._batch_thread = QThread()
        self._batch_worker = BatchWorker(url, start, count, out_dir)
        self._batch_worker.moveToThread(self._batch_thread)
        self._batch_thread.started.connect(self._batch_worker.go)
        self._batch_worker.progress.connect(self._on_batch_progress)
        self._batch_worker.done.connect(self._on_batch_done)
        self._batch_worker.failed.connect(self._on_batch_failed)
        self._batch_thread.start()

    def _stop_batch(self):
        if self._batch_worker:
            self._batch_worker.stop()
        self.batch_stop_btn.setEnabled(False)

    def _on_batch_progress(self, done: int, total: int, msg: str):
        self.batch_progress.setText(f"[{done}/{total}] {msg}")
        self._append(msg)

    def _on_batch_done(self, zips: list):
        if self._batch_thread:
            self._batch_thread.quit()
            self._batch_thread.wait()
        self.batch_go_btn.setEnabled(True)
        self.batch_stop_btn.setVisible(False)
        out = self._batch_last_out or ""
        msg = f"✓ Done — {len(zips)} ZIP(s) saved to {out}"
        self.batch_progress.setText(msg)
        self.batch_progress.setStyleSheet("color:#1a9e4b;font-size:12px;")
        self._append(msg)
        if out and os.path.isdir(out):
            _open_folder(out)

    def _on_batch_failed(self, err: str):
        if self._batch_thread:
            self._batch_thread.quit()
            self._batch_thread.wait()
        self.batch_go_btn.setEnabled(True)
        self.batch_stop_btn.setVisible(False)
        self.batch_progress.setText(f"✗ {err}")
        self.batch_progress.setStyleSheet("color:#d12d2d;font-size:12px;")
        self._append(f"Batch failed: {err}")

    def _on_clean_drop(self, path: str):
        self.url.clear()
        self._start_clean(path)

    def _on_go(self):
        if self.tabs.currentWidget() is self._projects_tab:
            return
        src = self.url.text().strip()
        if not src:
            self._append("Paste a URL or drop a folder first.")
            return
        self._start_clean(src)

    def _start_clean(self, source: str):
        self._control = Control()
        worker = Worker(
            source,
            self.segments.value(),
            self.remove_text.isChecked(),
            self.quality.currentData(),
            self.keep_sfx.isChecked(),
            self.transcript.currentData() if self.transcript else None,
            self.typeset.currentData() if self.typeset else None,
            self._control,
        )
        self._begin(source, worker,
                    typeset_active=bool(self.typeset and self.typeset.currentData()))

    def _begin(self, source: str, worker, typeset_active: bool = False):
        """Shared run lifecycle: lock the UI, start the timer, run the worker
        on its own thread, and wire its signals."""
        self.log.clear()
        self.bar.setRange(0, 0)  # busy until first progress
        self.go.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setText("Pause")
        self.stop_btn.setEnabled(True)
        self._append(f"Source: {source}")
        self.activity.setStyleSheet("color:#2d7ff9;font-size:14px;font-weight:bold;")
        self._t0 = time.monotonic()
        self._spin_i = 0
        self._pause_accum = 0.0
        self._pause_start = None
        self._typeset_active = typeset_active
        self._timer.start()
        self._tick()

        self._thread = QThread()
        self._worker = worker
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.go)
        worker.status.connect(self._append)
        worker.progress.connect(self._on_progress)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.stopped.connect(self._on_stopped)
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
        self.activity.setText(f"✓ done in {elapsed}")
        self.activity.setStyleSheet("color:#1a9e4b;font-size:14px;font-weight:bold;")
        self.open_btn.setEnabled(True)
        # typeset run -> open the native editor on the generated layout
        if getattr(self, "_typeset_active", False) and outputs:
            self._append(f"✓ typeset canvas ready in {elapsed} — opening editor…")
            self._open_typeset(outputs[0])
        else:
            self._append(f"✓ {len(outputs)} image(s) ready in {elapsed}.")

    def _open_typeset(self, layout_path=None):
        from .typeset_editor import TypesetEditor

        if not layout_path or not isinstance(layout_path, str):
            start = self._last_out or os.path.expanduser("~/ManhwaPrep/output")
            layout_path, _ = QFileDialog.getOpenFileName(
                self, "Open typeset layout.json", start, "Layout (layout.json)"
            )
        if layout_path and os.path.exists(layout_path):
            self._ts_editor = TypesetEditor(layout_path)
            self._ts_editor.show()

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
    from PySide6.QtGui import QPixmapCache
    QPixmapCache.setCacheLimit(256 * 1024)  # 256 MB (default ~10 MB)
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
