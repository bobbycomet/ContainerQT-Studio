"""Shared dialogs: findings list (compatibility / health) and the 'a container is not a VM' notice."""
from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QMessageBox,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import theme
from ..findings import ERROR, INFO, OK, WARN, Finding

COLORS = {ERROR: theme.BAD, WARN: theme.WARN, INFO: theme.ACCENT, OK: theme.OK}
GLYPH = {ERROR: "✖", WARN: "▲", INFO: "●", OK: "✔"}


class FindingsDialog(QDialog):
    """Lists findings. `chosen_fix` is the action key of a pressed fix button (or '' when just closed/continued)."""

    def __init__(self, title: str, intro: str, findings: list[Finding], parent=None, continue_text: str = "",
                 close_text: str = "Close") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 480)
        self.chosen_fix = ""
        self.proceed = False
        lay = QVBoxLayout(self)
        if intro:
            lb = QLabel(intro)
            lb.setWordWrap(True)
            lay.addWidget(lb)
        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        il = QVBoxLayout(inner)
        for f in findings:
            il.addWidget(self._row(f))
        il.addStretch(1)
        area.setWidget(inner)
        lay.addWidget(area, 1)
        btns = QDialogButtonBox()
        if continue_text:
            go = btns.addButton(continue_text, QDialogButtonBox.ButtonRole.AcceptRole)
            go.setObjectName("primary")
            go.clicked.connect(self._go)
        close = btns.addButton(close_text, QDialogButtonBox.ButtonRole.RejectRole)
        close.clicked.connect(self.reject)
        lay.addWidget(btns)

    def _go(self) -> None:
        self.proceed = True
        self.accept()

    def _row(self, f: Finding) -> QWidget:
        fr = QFrame()
        fr.setStyleSheet("QFrame{border:1px solid #303644;border-radius:6px;}")
        h = QHBoxLayout(fr)
        glyph = QLabel(GLYPH.get(f.level, ""))
        glyph.setStyleSheet(f"color:{COLORS.get(f.level, theme.MUTED)};font-size:16px;border:none;")
        h.addWidget(glyph)
        text = QLabel(f"<b>{_esc(f.title)}</b>" + (f"<br>{_esc(f.detail)}" if f.detail else ""))
        text.setWordWrap(True)
        text.setStyleSheet("border:none;")
        h.addWidget(text, 1)
        if f.fix and f.fix_label:
            b = QPushButton(f.fix_label)
            b.clicked.connect(lambda _c=False, k=f.fix: self._fix(k))
            h.addWidget(b)
        return fr

    def _fix(self, key: str) -> None:
        self.chosen_fix = key
        self.accept()


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>")


NOT_A_VM = (
    "<b>ContainerQT Studio environments are containers, not virtual machines.</b><br><br>"
    "A container shares your computer's kernel. It is great for convenient, disposable or persistent Linux "
    "development without maintaining several full operating systems, but it does <b>not</b> give the isolation a VM "
    "does: a hostile program, or a kernel/Docker vulnerability, can reach further than it could from inside a VM.<br><br>"
    "&bull; Only run software and open files from sources you trust.<br>"
    "&bull; Do not use it to examine malware or to run unknown code.<br>"
    "&bull; Anyone in the <code>docker</code> group effectively has root-equivalent control of this computer - "
    "that is how Docker works, not specific to ContainerQT Studio."
)


def not_a_vm_notice(parent, settings=None) -> None:
    """Shows the notice; with `settings` (QSettings-like) offers 'don't show again'."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Containers are not virtual machines")
    box.setTextFormat(box.textFormat().RichText)
    box.setText(NOT_A_VM)
    cb = None
    if settings is not None:
        cb = QCheckBox("Do not show this at start-up (it stays in the Help menu)")
        box.setCheckBox(cb)
    box.exec()
    if settings is not None and cb is not None and cb.isChecked():
        settings.setValue("ack_not_a_vm", True)
