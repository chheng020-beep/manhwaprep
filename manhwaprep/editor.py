"""Side-by-side translation editor.

Left: the page with numbered bubbles. Right: one editable row per bubble
(original text + an editable Khmer field). Navigate pages, edit the Khmer,
Save writes back to translation.json.

  python -m manhwaprep.editor [path/to/translation.json]
"""

from __future__ import annotations

import json
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

KHMER_FONT = "Khmer Sangam MN"  # ships with macOS


class BubbleRow(QWidget):
    def __init__(self, bubble: dict):
        super().__init__()
        self.bubble = bubble
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        kind = bubble.get("kind", "dialogue")
        tag = "🗨️" if kind == "dialogue" else "💥SFX"
        head = QLabel(f"#{bubble['n']} {tag}  ·  {bubble.get('src','')}")
        head.setWordWrap(True)
        head.setStyleSheet("color:#555;" if kind == "dialogue" else "color:#a06000;")
        head.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(head)
        self.edit = QPlainTextEdit(bubble.get("khm", ""))
        self.edit.setFont(QFont(KHMER_FONT, 15))
        self.edit.setFixedHeight(60)
        lay.addWidget(self.edit)
        self.setStyleSheet("BubbleRow{border-bottom:1px solid #eee;}")

    def value(self) -> str:
        return self.edit.toPlainText().strip()


class EditorWindow(QWidget):
    def __init__(self, json_path: str):
        super().__init__()
        self.json_path = json_path
        self.base = os.path.dirname(json_path)
        with open(json_path, encoding="utf-8") as f:
            self.data = json.load(f)
        self.pages = self.data.get("pages", [])
        self.idx = 0
        self.rows: list[BubbleRow] = []

        self.setWindowTitle(f"Translation editor — {self.data.get('chapter','')}")
        self.resize(1100, 800)
        root = QHBoxLayout(self)

        # left: page image
        self.img_scroll = QScrollArea()
        self.img_scroll.setWidgetResizable(True)
        self.img_label = QLabel("no image")
        self.img_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.img_scroll.setWidget(self.img_label)
        root.addWidget(self.img_scroll, 3)

        # right: nav + rows + save
        right = QVBoxLayout()
        nav = QHBoxLayout()
        self.prev = QPushButton("‹ Prev")
        self.next = QPushButton("Next ›")
        self.prev.clicked.connect(lambda: self._go(-1))
        self.next.clicked.connect(lambda: self._go(1))
        self.page_lbl = QLabel("")
        nav.addWidget(self.prev)
        nav.addWidget(self.page_lbl, 1, Qt.AlignCenter)
        nav.addWidget(self.next)
        right.addLayout(nav)

        self.rows_scroll = QScrollArea()
        self.rows_scroll.setWidgetResizable(True)
        self.rows_host = QWidget()
        self.rows_lay = QVBoxLayout(self.rows_host)
        self.rows_lay.addStretch(1)
        self.rows_scroll.setWidget(self.rows_host)
        right.addWidget(self.rows_scroll, 1)

        self.save_btn = QPushButton("💾 Save translation.json")
        self.save_btn.setFixedHeight(38)
        self.save_btn.setStyleSheet(
            "QPushButton{background:#1a9e4b;color:white;border-radius:8px;"
            "font-weight:bold;}"
        )
        self.save_btn.clicked.connect(self._save)
        right.addWidget(self.save_btn)
        self.status = QLabel("")
        right.addWidget(self.status)

        wrap = QWidget()
        wrap.setLayout(right)
        wrap.setFixedWidth(440)
        root.addWidget(wrap)

        self._load_page()

    # -- page handling -------------------------------------------------
    def _commit_rows(self):
        """Copy current edits into self.data for the current page."""
        if not self.pages:
            return
        bubbles = self.pages[self.idx].get("bubbles", [])
        for row in self.rows:
            row.bubble["khm"] = row.value()
        _ = bubbles  # bubbles hold references to the same dicts

    def _go(self, delta: int):
        self._commit_rows()
        self.idx = max(0, min(len(self.pages) - 1, self.idx + delta))
        self._load_page()

    def _overlay_path(self, page_num: int) -> str:
        cand = os.path.join(self.base, "_translate", "overlays", f"{page_num:03d}.png")
        if os.path.exists(cand):
            return cand
        # fall back to the stored raw page image
        return os.path.join(self.base, self.pages[self.idx].get("image", ""))

    def _load_page(self):
        if not self.pages:
            self.page_lbl.setText("no pages")
            return
        page = self.pages[self.idx]
        self.page_lbl.setText(f"Page {page['page']}  ({self.idx + 1}/{len(self.pages)})")
        self.prev.setEnabled(self.idx > 0)
        self.next.setEnabled(self.idx < len(self.pages) - 1)

        pix = QPixmap(self._overlay_path(page["page"]))
        if not pix.isNull():
            if pix.width() > 720:
                pix = pix.scaledToWidth(720, Qt.SmoothTransformation)
            self.img_label.setPixmap(pix)
        else:
            self.img_label.setText("image missing")

        # rebuild rows
        for r in self.rows:
            r.setParent(None)
        self.rows = []
        for b in page.get("bubbles", []):
            row = BubbleRow(b)
            self.rows.append(row)
            self.rows_lay.insertWidget(self.rows_lay.count() - 1, row)
        if not page.get("bubbles"):
            empty = QLabel("(no text detected on this page)")
            empty.setStyleSheet("color:#999;padding:12px;")
            self.rows_lay.insertWidget(0, empty)

    def _save(self):
        self._commit_rows()
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        self.status.setText("Saved ✓")


def main():
    app = QApplication(sys.argv)
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        path, _ = QFileDialog.getOpenFileName(
            None, "Open translation.json", os.path.expanduser("~/ManhwaPrep/output"),
            "Translation (translation.json)",
        )
        if not path:
            return
    win = EditorWindow(path)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
