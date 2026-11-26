"""Reusable widgets: console, drop zone, bash highlighter, container directory browser."""
from __future__ import annotations

import re

from PyQt6.QtCore import QRegularExpression, Qt, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QFontDatabase, QSyntaxHighlighter, QTextCharFormat)
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPlainTextEdit, QVBoxLayout, QHBoxLayout, QPushButton)

from ..client import DockerClient

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def mono() -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPointSize(10)
    return f


class ConsoleView(QPlainTextEdit):
    """Read-only, auto-scrolling output pane."""

    def __init__(self, max_lines: int = 5000, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(mono())
        self.setMaximumBlockCount(max_lines)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

    def add_line(self, text: str) -> None:
        text = _ANSI.sub("", text)
        bar = self.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.appendPlainText(text)
        if at_end:
            bar.setValue(bar.maximum())


class DropZone(QFrame):
    pathsDropped = pyqtSignal(list)
    clicked = pyqtSignal()

    def __init__(self, text: str = "Drop files or folders here", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(120)
        lay = QVBoxLayout(self)
        self.label = QLabel(text)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("color:#8b93a1; font-size:14px;")
        lay.addWidget(self.label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _hover(self, on: bool) -> None:
        self.setProperty("hover", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._hover(True)

    def dragLeaveEvent(self, e) -> None:  # noqa: N802
        self._hover(False)

    def dropEvent(self, e) -> None:  # noqa: N802
        self._hover(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.pathsDropped.emit(paths)
            e.acceptProposedAction()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.clicked.emit()


class BashHighlighter(QSyntaxHighlighter):
    def __init__(self, doc) -> None:
        super().__init__(doc)

        def fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            f.setFontItalic(italic)
            return f

        kw = r"\b(if|then|else|elif|fi|for|while|do|done|case|esac|function|in|return|exit|set|export|local|source)\b"
        self.rules = [
            (QRegularExpression(kw), fmt("#c792ea", bold=True)),
            (QRegularExpression(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|\$\(|\$[0-9@#?]"), fmt("#82aaff")),
            (QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), fmt("#c3e88d")),
            (QRegularExpression(r"'[^']*'"), fmt("#c3e88d")),
            (QRegularExpression(r"(^|\s)#[^\n]*"), fmt("#6b7280", italic=True)),
        ]

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)


class ContainerBrowser(QDialog):
    """Pick a file or folder inside a running container."""

    def __init__(self, client: DockerClient, container: str, start: str = "/workspace", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Browse environment")
        self.resize(560, 460)
        self.client, self.container = client, container
        self.cwd = start
        self.selected = start

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.path_edit = QLineEdit(start)
        self.path_edit.returnPressed.connect(lambda: self._go(self.path_edit.text().strip() or "/"))
        up = QPushButton("Up")
        up.clicked.connect(self._up)
        row.addWidget(up)
        row.addWidget(self.path_edit, 1)
        lay.addLayout(row)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._open)
        self.list.itemSelectionChanged.connect(self._sel)
        lay.addWidget(self.list, 1)
        self.msg = QLabel("")
        self.msg.setStyleSheet("color:#f87171;")
        lay.addWidget(self.msg)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._go(start)

    def _join(self, name: str) -> str:
        return (self.cwd.rstrip("/") + "/" + name) if self.cwd != "/" else "/" + name

    def _go(self, path: str) -> None:
        ok, res = self.client.list_dir(self.container, path)
        if not ok:
            self.msg.setText(str(res))
            return
        self.msg.setText("")
        self.cwd = path
        self.selected = path
        self.path_edit.setText(path)
        self.list.clear()
        items = sorted(res, key=lambda t: (not t[1], t[0].lower()))  # type: ignore[arg-type]
        for name, is_dir in items:
            it = QListWidgetItem(("\U0001F4C1 " if is_dir else "\U0001F4C4 ") + name)
            it.setData(Qt.ItemDataRole.UserRole, (name, is_dir))
            self.list.addItem(it)

    def _up(self) -> None:
        parent = self.cwd.rstrip("/").rsplit("/", 1)[0] or "/"
        self._go(parent)

    def _open(self, item: QListWidgetItem) -> None:
        name, is_dir = item.data(Qt.ItemDataRole.UserRole)
        if is_dir:
            self._go(self._join(name))

    def _sel(self) -> None:
        items = self.list.selectedItems()
        if items:
            name, _ = items[0].data(Qt.ItemDataRole.UserRole)
            self.selected = self._join(name)
            self.path_edit.setText(self.selected)
        else:
            self.selected = self.cwd
