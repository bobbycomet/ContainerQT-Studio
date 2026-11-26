"""Log page: everything that happened, with a failures-only view."""
from __future__ import annotations

import logging
from collections import deque

from PyQt6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout,
                             QWidget)

from ... import hostutil, logger, paths
from ..context import AppContext, LogBridge
from ..widgets import ConsoleView

FILTERS = (
    ("Everything (incl. command output)", logging.DEBUG),
    ("Info and above", logging.INFO),
    ("Warnings and errors", logging.WARNING),
    ("Failures only", logging.ERROR),
)


def _level_of(line: str) -> int:
    for name, lvl in (("[ERROR]", logging.ERROR), ("[WARNING]", logging.WARNING),
                      ("[INFO]", logging.INFO), ("[DEBUG]", logging.DEBUG), ("[CRITICAL]", logging.CRITICAL)):
        if name in line[:40]:
            return lvl
    return logging.DEBUG


class LogPage(QWidget):
    def __init__(self, ctx: AppContext, bridge: LogBridge) -> None:
        super().__init__()
        self.records: deque[tuple[int, str]] = deque(maxlen=20000)
        lay = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.level = QComboBox()
        for label, lvl in FILTERS:
            self.level.addItem(label, lvl)
        self.level.setCurrentIndex(1)
        self.level.currentIndexChanged.connect(self._rebuild)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search...")
        self.search.textChanged.connect(self._rebuild)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.console.toPlainText()))
        save = QPushButton("Save as...")
        save.clicked.connect(self._save)
        clear = QPushButton("Clear view")
        clear.clicked.connect(self._clear)
        folder = QPushButton("Open log folder")
        folder.clicked.connect(lambda: hostutil.open_path(paths.log_dir()))
        bar.addWidget(self.level)
        bar.addWidget(self.search, 1)
        for b in (copy, save, clear, folder):
            bar.addWidget(b)
        lay.addLayout(bar)

        self.console = ConsoleView(max_lines=20000)
        lay.addWidget(self.console, 1)

        for line in logger.read_tail(500):
            self.records.append((_level_of(line), line))
        bridge.record.connect(self._on_record)
        self._rebuild()

    def _passes(self, lvl: int, text: str) -> bool:
        if lvl < self.level.currentData():
            return False
        q = self.search.text().strip().lower()
        return not q or q in text.lower()

    def _on_record(self, lvl: int, text: str) -> None:
        self.records.append((lvl, text))
        if self._passes(lvl, text):
            self.console.add_line(text)

    def _rebuild(self) -> None:
        self.console.clear()
        self.console.setUpdatesEnabled(False)
        try:
            for lvl, text in self.records:
                if self._passes(lvl, text):
                    self.console.add_line(text)
        finally:
            self.console.setUpdatesEnabled(True)

    def _clear(self) -> None:
        self.records.clear()
        self.console.clear()

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save log", "dockerqt-log.txt")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(t for _l, t in self.records))
