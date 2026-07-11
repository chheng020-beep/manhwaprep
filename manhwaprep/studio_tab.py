"""Studio tab: the queue board that drives chapters through the review gates."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QLabel,
    QTableWidget, QTableWidgetItem,
)

from . import studio as studio_mod
from .control import Control


class _WorkerThread(QThread):
    changed = Signal(str)

    def __init__(self, studio):
        super().__init__()
        self._studio = studio
        self.control = Control()

    def run(self):
        studio_mod.run_queue(self._studio, control=self.control,
                             on_job_change=self.changed.emit)


class StudioTab(QWidget):
    def __init__(self, studio, editor_cls=None, split_cls=None):
        super().__init__()
        self._studio = studio
        # late imports keep construction light and testable
        if editor_cls is None:
            from .typeset_editor import TypesetEditor as editor_cls  # noqa
        if split_cls is None:
            from .manual_split import ManualSplitWidget as split_cls  # noqa
        self._editor_cls = editor_cls
        self._split_cls = split_cls
        self._gate_widget = None
        self._thread = None

        lay = QVBoxLayout(self)
        add_row = QHBoxLayout()
        self._src = QLineEdit(); self._src.setPlaceholderText("Chapter URL or folder path")
        self._title = QLineEdit(); self._title.setPlaceholderText("Title")
        add_btn = QPushButton("Add"); add_btn.clicked.connect(self._on_add)
        add_row.addWidget(self._src); add_row.addWidget(self._title); add_row.addWidget(add_btn)
        lay.addLayout(add_row)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Chapter", "State", "Action"])
        lay.addWidget(self._table)
        self.refresh()

    # --- queue / worker ---
    def _on_add(self):
        src, title = self._src.text().strip(), self._title.text().strip()
        if src:
            self.add_source(src, title or src)

    def add_source(self, source: str, title: str):
        self._studio.add(source, title)
        self.refresh()
        self.start_worker()

    def start_worker(self):
        if self._thread and self._thread.isRunning():
            return
        self._thread = _WorkerThread(self._studio)
        self._thread.changed.connect(lambda slug: self.refresh())
        self._thread.start()

    # --- table ---
    def refresh(self):
        jobs = self._studio.scan()
        self._table.setRowCount(len(jobs))
        for r, job in enumerate(jobs):
            self._table.setItem(r, 0, QTableWidgetItem(job.title))
            self._table.setItem(r, 1, QTableWidgetItem(job.state))
            btn = self._action_button(job)
            self._table.setCellWidget(r, 2, btn)

    def _action_button(self, job) -> QWidget:
        label = {studio_mod.TYPESET: "Typeset", studio_mod.CUT: "Cut",
                 studio_mod.DONE: "Open output", studio_mod.ERROR: "Retry",
                 studio_mod.QUEUED: "Queued…", studio_mod.PREPPING: "Prepping…"}.get(job.state, "")
        b = QPushButton(label)
        if job.state in (studio_mod.TYPESET, studio_mod.CUT):
            b.clicked.connect(lambda _, s=job.slug: self.open_gate(s))
        elif job.state == studio_mod.ERROR:
            b.clicked.connect(lambda _, s=job.slug: (self._studio.retry(s), self.refresh(), self.start_worker()))
        elif job.state == studio_mod.DONE:
            b.clicked.connect(lambda _, s=job.slug: self._open_output(s))
        else:
            b.setEnabled(False)
        return b

    # --- gates ---
    def open_gate(self, slug: str):
        job = studio_mod.ChapterJob.from_status(self._studio.chapter_dir(slug))
        if job.state == studio_mod.TYPESET:
            self._open_typeset(slug)
        elif job.state == studio_mod.CUT:
            self._open_cut(slug)

    def _open_typeset(self, slug: str):
        layout = os.path.join(self._studio.chapter_dir(slug), "typeset", "layout.json")
        ed = self._editor_cls(layout)
        ed.set_ready_callback(lambda s=slug: (self._studio.advance(s), self.refresh()))
        self._gate_widget = ed
        ed.show()

    def _open_cut(self, slug: str):
        cdir = self._studio.chapter_dir(slug)
        layout = os.path.join(cdir, "typeset", "layout.json")
        rendered = os.path.join(cdir, "rendered")
        # render the lettered canvases so the splitter cuts the translated art
        ed = self._editor_cls(layout)
        ed.render_translated(rendered, watermarked=False)
        sp = self._split_cls()
        sp.set_export_dir(os.path.join(cdir, "output"))
        sp.set_export_callback(lambda s=slug: self._on_split_export(s))
        sp.load_folder(rendered)
        self._gate_widget = sp
        sp.show()

    def _on_split_export(self, slug: str):
        job = studio_mod.ChapterJob.from_status(self._studio.chapter_dir(slug))
        if job.state == studio_mod.CUT:
            self._studio.advance(slug)
            self.refresh()

    def _open_output(self, slug: str):
        path = os.path.join(self._studio.chapter_dir(slug), "output")
        os.makedirs(path, exist_ok=True)
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
