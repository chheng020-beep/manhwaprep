# manhwaprep/projects_view.py
"""Projects tab: a list of series, each opening a chapter board with per-chapter
status, live progress, and Open-editor / Export & mark done / Re-prep / Remove."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)


class _ChapterRow(QWidget):
    def __init__(self, panel, proj_id, ch):
        super().__init__()
        self.panel = panel
        self.proj_id = proj_id
        self.chap_id = ch["id"]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)

        self.thumb = QLabel()
        if ch.get("thumb") and os.path.exists(ch["thumb"]):
            pm = QPixmap(ch["thumb"])
            if not pm.isNull():
                self.thumb.setPixmap(pm.scaled(48, 34, Qt.KeepAspectRatioByExpanding,
                                               Qt.SmoothTransformation))
        lay.addWidget(self.thumb)

        self.name = QLabel(ch.get("name") or self.chap_id)
        self.name.setMinimumWidth(160)
        lay.addWidget(self.name)

        self.badge = QLabel()
        self.badge.setMinimumWidth(72)
        lay.addWidget(self.badge)

        self.bar = QProgressBar()
        self.bar.setMaximumWidth(140)
        self.bar.setVisible(False)
        lay.addWidget(self.bar)

        lay.addStretch(1)
        self.btns = QHBoxLayout()
        lay.addLayout(self.btns)
        self.apply(ch)

    def _clear_btns(self):
        while self.btns.count():
            it = self.btns.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _btn(self, text, slot):
        b = QPushButton(text)
        b.clicked.connect(slot)
        self.btns.addWidget(b)

    def apply(self, ch):
        status = ch.get("status", "queued")
        self.badge.setText({"queued": "Queued", "prepping": "Prepping…",
                            "ready": "Ready", "done": "Done",
                            "error": "Error"}.get(status, status))
        self.bar.setVisible(status == "prepping")
        if ch.get("thumb") and os.path.exists(ch["thumb"]) and self.thumb.pixmap() is None:
            pm = QPixmap(ch["thumb"])
            if not pm.isNull():
                self.thumb.setPixmap(pm.scaled(48, 34, Qt.KeepAspectRatioByExpanding,
                                               Qt.SmoothTransformation))
        self._clear_btns()
        layout = ch.get("layout")
        if status in ("ready", "done") and layout:
            self._btn("Open editor", lambda: self.panel.open_editor(layout))
        if status == "ready":
            self._btn("Export & mark done", self._mark_done)
        if status in ("ready", "done", "error"):
            self._btn("Re-prep", self._reprep)
        if status in ("queued", "prepping"):
            self._btn("Skip", self.panel.queue.skip_current)
        self._btn("Remove", self._remove)
        if status == "error" and ch.get("error"):
            self.setToolTip(ch["error"])

    def set_progress(self, done, total):
        self.bar.setVisible(True)
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
        else:
            self.bar.setRange(0, 0)

    def _mark_done(self):
        self.panel.store.set_chapter(self.proj_id, self.chap_id, status="done")
        self.panel.open_detail(self.proj_id)

    def _reprep(self):
        self.panel.store.set_chapter(self.proj_id, self.chap_id, status="queued")
        self.panel.queue.enqueue(self.proj_id, self.chap_id)
        self.panel.open_detail(self.proj_id)

    def _remove(self):
        if QMessageBox.question(self, "Remove chapter",
                                "Remove this chapter from the project? "
                                "(files are kept)") != QMessageBox.Yes:
            return
        self.panel.store.remove_chapter(self.proj_id, self.chap_id)
        self.panel.open_detail(self.proj_id)


class ProjectsPanel(QWidget):
    def __init__(self, store, queue, open_editor):
        super().__init__()
        self.store = store
        self.queue = queue
        self.open_editor = open_editor
        self._rows = {}          # chap_id -> _ChapterRow (current detail view)
        self._detail_pid = None

        self.stack = QStackedWidget()
        root = QVBoxLayout(self)
        root.addWidget(self.stack)

        # page 0: project list
        page0 = QWidget(); v0 = QVBoxLayout(page0)
        v0.addWidget(QLabel("<b>Projects</b> — click a series"))
        self.proj_list = QListWidget()
        self.proj_list.itemClicked.connect(
            lambda it: self.open_detail(it.data(Qt.UserRole)))
        v0.addWidget(self.proj_list)
        self.stack.addWidget(page0)

        # page 1: chapter detail
        page1 = QWidget(); self._v1 = QVBoxLayout(page1)
        head = QHBoxLayout()
        back = QPushButton("← Projects"); back.clicked.connect(self._show_list)
        head.addWidget(back)
        self.detail_title = QLabel(); head.addWidget(self.detail_title)
        head.addStretch(1)
        add = QPushButton("Add chapters…"); add.clicked.connect(self._add_chapters)
        head.addWidget(add)
        self._v1.addLayout(head)
        self._chap_area = QScrollArea(); self._chap_area.setWidgetResizable(True)
        self._chap_host = QWidget(); self._chap_v = QVBoxLayout(self._chap_host)
        self._chap_v.addStretch(1)
        self._chap_area.setWidget(self._chap_host)
        self._v1.addWidget(self._chap_area)
        self.stack.addWidget(page1)

        self.queue.status_changed.connect(self._on_status)
        self.queue.progress.connect(self._on_progress)
        self.refresh()

    def refresh(self):
        self.proj_list.clear()
        for p in self.store.list_projects():
            chs = p.get("chapters", [])
            counts = {}
            for c in chs:
                counts[c["status"]] = counts.get(c["status"], 0) + 1
            summary = " · ".join(f"{counts[k]} {k}" for k in
                                 ("queued", "prepping", "ready", "done", "error")
                                 if counts.get(k))
            item = QListWidgetItem(f"{p['name']}    ({summary or 'empty'})")
            thumb = next((c.get("thumb") for c in chs
                          if c.get("thumb") and os.path.exists(c["thumb"])), None)
            if thumb:
                pm = QPixmap(thumb)
                if not pm.isNull():
                    item.setIcon(QIcon(pm.scaled(72, 52, Qt.KeepAspectRatioByExpanding,
                                                 Qt.SmoothTransformation)))
            item.setData(Qt.UserRole, p["id"])
            self.proj_list.addItem(item)

    def _show_list(self):
        self._detail_pid = None
        self.refresh()
        self.stack.setCurrentIndex(0)

    def open_detail(self, proj_id):
        self._detail_pid = proj_id
        proj = self.store.get_project(proj_id)
        if proj is None:
            self._show_list(); return
        self.detail_title.setText(f"<b>{proj['name']}</b>")
        # clear existing rows
        while self._chap_v.count() > 1:            # keep the trailing stretch
            it = self._chap_v.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._rows = {}
        for ch in proj.get("chapters", []):
            row = _ChapterRow(self, proj_id, ch)
            self._rows[ch["id"]] = row
            self._chap_v.insertWidget(self._chap_v.count() - 1, row)
        self.stack.setCurrentIndex(1)

    def _on_status(self, proj_id, chap_id, status):
        if proj_id == self._detail_pid:
            ch = self.store.get_chapter(proj_id, chap_id)
            row = self._rows.get(chap_id)
            if ch and row:
                row.apply(ch)

    def _on_progress(self, proj_id, chap_id, stage, done, total):
        if proj_id == self._detail_pid:
            row = self._rows.get(chap_id)
            if row:
                row.set_progress(done, total)

    def _add_chapters(self):
        if not self._detail_pid:
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "Add chapters",
            "Paste chapter URLs (one per line):", "")
        if not ok or not text.strip():
            return
        for line in text.splitlines():
            url = line.strip()
            if not url:
                continue
            pid, cid = self.store.add_chapter(url)
            self.queue.enqueue(pid, cid)
        self.open_detail(self._detail_pid)
