"""Templates tab: saved, repeatable environment configurations (built-in and your own)."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QMessageBox, QPushButton, QTextBrowser, QVBoxLayout, QWidget)

from .. import theme
from ...templates import EnvTemplate
from ..context import AppContext


class TemplatesPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        root = QHBoxLayout(self)
        left = QVBoxLayout()
        intro = QLabel("Templates remember a known-good setup (Linux, desktop, toolkits, resources) so you can "
                       "recreate it whenever you need it - e.g. the same Ubuntu with KDE Plasma 5 and with "
                       "Plasma 6 side by side.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{theme.MUTED};")
        left.addWidget(intro)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.currentItemChanged.connect(lambda *_: self._show())
        self.list.itemDoubleClicked.connect(lambda _i: self._use())
        left.addWidget(self.list, 1)
        root.addLayout(left, 1)

        right = QVBoxLayout()
        self.detail = QTextBrowser()
        right.addWidget(self.detail, 1)
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        self.use_btn = QPushButton("Use template")
        self.use_btn.setObjectName("primary")
        self.use_btn.setToolTip("Fill the New environment form from this template")
        self.use_btn.clicked.connect(self._use)
        self.export_btn = QPushButton("Export...")
        self.export_btn.clicked.connect(self._export)
        self.import_btn = QPushButton("Import...")
        self.import_btn.clicked.connect(self._import)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.clicked.connect(self._delete)
        row1.addWidget(self.use_btn)
        for b in (self.export_btn, self.import_btn, self.delete_btn):
            row2.addWidget(b)
        right.addLayout(row1)
        right.addLayout(row2)
        root.addLayout(right, 1)
        ctx.templatesChanged.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        keep = self.current_name()
        self.list.clear()
        for t in self.ctx.templates.all():
            it = QListWidgetItem(("[built-in]  " if t.builtin else "") + t.name)
            it.setData(Qt.ItemDataRole.UserRole, t.name)
            self.list.addItem(it)
            if t.name == keep:
                self.list.setCurrentItem(it)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self._show()

    def current_name(self) -> str:
        it = self.list.currentItem() if hasattr(self, "list") else None
        return it.data(Qt.ItemDataRole.UserRole) if it else ""

    def current(self) -> EnvTemplate | None:
        n = self.current_name()
        return self.ctx.templates.get(n) if n else None

    def _show(self) -> None:
        t = self.current()
        have = t is not None
        self.use_btn.setEnabled(have)
        self.export_btn.setEnabled(have)
        self.delete_btn.setEnabled(have and not t.builtin)
        self.delete_btn.setToolTip("Built-in templates cannot be deleted" if have and t.builtin else "")
        if not t:
            self.detail.setHtml("<i>No template selected.</i>")
            return
        esc = lambda x: x.replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
        probs = t.problems()
        warn = ("<p style='color:#e0a030'><b>Cannot be used here:</b> " + esc("; ".join(probs)) + "</p>") if probs else ""
        self.detail.setHtml(f"<h3>{esc(t.name)}</h3><p>{esc(t.description)}</p>{warn}<p>{esc(t.summary())}</p>")

    def _use(self) -> None:
        t = self.current()
        if t is None:
            return
        self.ctx.requestTab.emit("envs")
        self.ctx.useTemplate.emit(t.name)

    def _export(self) -> None:
        t = self.current()
        if not t:
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Export template", str(Path.home() / f"{t.slug}.dockerqt-template.json"),
                                              "ContainerQT Studio template (*.json)")
        if dest:
            try:
                self.ctx.templates.export_to(t, Path(dest))
            except OSError as e:
                QMessageBox.warning(self, "Export", str(e))

    def _import(self) -> None:
        src, _ = QFileDialog.getOpenFileName(self, "Import template", str(Path.home()), "ContainerQT Studio template (*.json)")
        if not src:
            return
        try:
            try:
                t = self.ctx.templates.import_from(Path(src))
            except FileExistsError:
                if QMessageBox.question(self, "Import", "A template with that name exists. Replace it?"
                                        ) != QMessageBox.StandardButton.Yes:
                    return
                t = self.ctx.templates.import_from(Path(src), overwrite=True)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Import template", str(e))
            return
        self.ctx.templatesChanged.emit()
        self.current_name_select(t.name)

    def current_name_select(self, name: str) -> None:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == name:
                self.list.setCurrentRow(i)

    def _delete(self) -> None:
        t = self.current()
        if t and not t.builtin and QMessageBox.question(
                self, "Delete template", f"Delete the template '{t.name}'?\n(Environments made from it are unaffected.)"
        ) == QMessageBox.StandardButton.Yes:
            self.ctx.templates.delete(t.name)
            self.ctx.templatesChanged.emit()
