"""Files page: bring files in (drag & drop / browse) and export build results to the host."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QDir
from PyQt6.QtGui import QFileSystemModel
from PyQt6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QSplitter, QTreeView, QVBoxLayout, QWidget)

from PyQt6.QtCore import Qt

from .. import theme
from ... import env_ops, hostutil
from ..context import AppContext
from ..widgets import ContainerBrowser, DropZone


class FilesPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        split = QSplitter(Qt.Orientation.Horizontal, self)
        outer = QVBoxLayout(self)
        outer.addWidget(split)

        # ---- import -------------------------------------------------------------------
        imp = QGroupBox("Bring files into the environment")
        il = QVBoxLayout(imp)
        self.drop = DropZone("Drop files or folders here\n(copied into /workspace)")
        self.drop.pathsDropped.connect(self._import)
        self.drop.clicked.connect(self._browse_files)
        il.addWidget(self.drop)
        row = QHBoxLayout()
        bf = QPushButton("Browse files...")
        bf.clicked.connect(self._browse_files)
        bd = QPushButton("Browse folder...")
        bd.clicked.connect(self._browse_folder)
        row.addWidget(bf)
        row.addWidget(bd)
        il.addLayout(row)
        sub = QHBoxLayout()
        sub.addWidget(QLabel("Into subfolder:"))
        self.subdir = QLineEdit()
        self.subdir.setPlaceholderText("(workspace root)")
        sub.addWidget(self.subdir, 1)
        il.addLayout(sub)

        self.model = QFileSystemModel()
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setColumnWidth(0, 260)
        for col in (2, 3):
            self.tree.hideColumn(col)
        self.tree.doubleClicked.connect(lambda idx: hostutil.open_path(self.model.filePath(idx)))
        self.tree_label = QLabel("Workspace contents (double-click to open on this computer):")
        self.tree_label.setWordWrap(True)
        il.addWidget(self.tree_label)
        il.addWidget(self.tree, 1)
        split.addWidget(imp)

        # ---- export -------------------------------------------------------------------
        exp = QGroupBox("Export to this computer")
        form = QFormLayout(exp)
        src_row = QWidget()
        sl = QHBoxLayout(src_row)
        sl.setContentsMargins(0, 0, 0, 0)
        self.src = QLineEdit("/workspace/dist")
        sb = QPushButton("Browse environment...")
        sb.clicked.connect(self._browse_container)
        sl.addWidget(self.src, 1)
        sl.addWidget(sb)
        form.addRow("From (in environment)", src_row)

        dst_row = QWidget()
        dl = QHBoxLayout(dst_row)
        dl.setContentsMargins(0, 0, 0, 0)
        self.dst = QLineEdit(str(Path.home() / "Downloads"))
        db = QPushButton("Browse...")
        db.clicked.connect(self._browse_dest)
        dl.addWidget(self.dst, 1)
        dl.addWidget(db)
        form.addRow("To (this computer)", dst_row)

        self.contents_only = QCheckBox("If it is a folder, copy only its contents")
        self.contents_only.setChecked(True)
        self.open_after = QCheckBox("Open the destination folder afterwards")
        self.open_after.setChecked(True)
        form.addRow("", self.contents_only)
        form.addRow("", self.open_after)
        self.export_btn = QPushButton("Export")
        self.export_btn.setObjectName("primary")
        self.export_btn.clicked.connect(self._export)
        form.addRow(self.export_btn)
        self._TIP = ("Tip: the workspace folder is shared with the environment, so anything saved in "
                     "/workspace is already on this computer. Export is for files elsewhere in the container.")
        self.tip = QLabel(self._TIP)
        self.tip.setWordWrap(True)
        self.tip.setStyleSheet(f"color:{theme.MUTED};")
        form.addRow(self.tip)
        split.addWidget(exp)

        ctx.currentChanged.connect(self._env_changed)
        ctx.busyChanged.connect(lambda *_: self._update())
        ctx.statesChanged.connect(lambda _s: self._update())
        ctx.requestExport.connect(self.prefill_export)
        self._env_changed()

    # -- state -----------------------------------------------------------------------------
    def _env_changed(self) -> None:
        env = self.ctx.current_env()
        one_time = bool(env and env.ephemeral)
        self.tree.setVisible(not one_time)
        if one_time:
            self.tree_label.setText(
                "One-time environment: files are copied straight into the running environment (nothing is kept on "
                "this computer). Start it first, then Export what you want to keep before you stop it.")
            self.tip.setText("Stopping a one-time environment discards everything inside it - export first.")
        else:
            self.tree_label.setText("Workspace contents (double-click to open on this computer):")
            self.tip.setText(self._TIP)
        root = env.workspace if env and env.workspace else str(Path.home())
        if env and env.workspace:
            Path(root).mkdir(parents=True, exist_ok=True)
        self.model.setRootPath(root)
        self.model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        self.tree.setRootIndex(self.model.index(root))
        self._update()

    def _update(self) -> None:
        env = self.ctx.current_env()
        self.drop.setEnabled(bool(env))
        self.export_btn.setEnabled(bool(env) and self.ctx.is_running(env) and not self.ctx.busy)

    def prefill_export(self, path: str) -> None:
        self.src.setText(path)

    # -- import --------------------------------------------------------------------------------
    def _browse_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Choose files to bring in")
        if files:
            self._import(files)

    def _browse_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose a folder to bring in")
        if d:
            self._import([d])

    def _import(self, sources: list[str]) -> None:
        env = self.ctx.current_env()
        if not env:
            QMessageBox.information(self, "No environment", "Create an environment first.")
            return
        if env.ephemeral and not self.ctx.is_running(env):
            QMessageBox.information(self, "Start the environment",
                                    "A one-time environment only exists while it is running.\n"
                                    "Start it on the Environments tab, then bring your files in.")
            return
        self.ctx.start_job(f"Import {len(sources)} item(s) into '{env.name}'",
                           env_ops.import_steps(env, sources, self.subdir.text(), self.ctx.client))

    # -- export ---------------------------------------------------------------------------------
    def _browse_container(self) -> None:
        env = self.ctx.current_env()
        if not env or not self.ctx.is_running(env):
            QMessageBox.information(self, "Environment not running", "Start the environment to browse it.")
            return
        dlg = ContainerBrowser(self.ctx.client, env.container, self.src.text() or "/workspace", self)
        if dlg.exec():
            self.src.setText(dlg.selected)

    def _browse_dest(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Export to", self.dst.text())
        if d:
            self.dst.setText(d)

    def _export(self) -> None:
        env = self.ctx.current_env()
        if not env:
            return
        src, dst = self.src.text().strip(), self.dst.text().strip()
        if not src.startswith("/") or not dst:
            QMessageBox.warning(self, "Export", "Enter an absolute path inside the environment and a destination folder.")
            return
        ok_dir, _ = self.ctx.client.list_dir(env.container, src)
        steps = env_ops.export_steps(self.ctx.client, env, src, dst, self.contents_only.isChecked() and ok_dir)

        def done(ok: bool, _s: str) -> None:
            if ok and self.open_after.isChecked():
                hostutil.open_path(dst)

        self.ctx.start_job(f"Export {src} from '{env.name}'", steps, done)
