"""Main window: toolbar (environment picker, Fix, status), tabs and the activity console."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (QComboBox, QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSplitter,
                             QTabWidget, QToolBar, QWidget)

from .. import __version__, fixer, hostutil, paths, privilege
from ..runner import Step
from . import theme
from .context import AppContext, LogBridge
from .pages.build import BuildPage
from .pages.desktop import DesktopPage
from .pages.envs import EnvsPage
from .pages.files import FilesPage
from .pages.logpage import LogPage
from .pages.templates import TemplatesPage
from .dialogs import not_a_vm_notice
from .widgets import ConsoleView


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, bridge: LogBridge) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("ContainerQT Studio")
        self.resize(1200, 800)

        # ---- toolbar ----------------------------------------------------------------
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addWidget(QLabel("  Environment: "))
        self.env_combo = QComboBox()
        self.env_combo.setMinimumWidth(240)
        self.env_combo.activated.connect(self._combo_activated)
        tb.addWidget(self.env_combo)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.docker_label = QLabel("Docker: checking...")
        tb.addWidget(self.docker_label)
        tb.addWidget(QLabel("  "))
        self.fix_btn = QPushButton("Fix")
        self.fix_btn.setObjectName("fix")
        self.fix_btn.setToolTip("Diagnose and repair Docker and your environments")
        self.fix_btn.clicked.connect(self._fix)
        tb.addWidget(self.fix_btn)
        tb.addWidget(QLabel(" "))

        # ---- tabs + activity ------------------------------------------------------------
        self.tabs = QTabWidget()
        self.envs_page = EnvsPage(ctx)
        self.templates_page = TemplatesPage(ctx)
        self.build_page = BuildPage(ctx)
        self.desktop_page = DesktopPage(ctx)
        self.files_page = FilesPage(ctx)
        self.log_page = LogPage(ctx, bridge)
        self._tab_index = {}
        for key, page, title in (
            ("envs", self.envs_page, "Environments"), ("templates", self.templates_page, "Templates"),
            ("build", self.build_page, "Build"),
            ("desktop", self.desktop_page, "Desktop"), ("files", self.files_page, "Files"),
            ("log", self.log_page, "Log"),
        ):
            self._tab_index[key] = self.tabs.addTab(page, title)

        self.activity = ConsoleView(max_lines=3000)
        self.activity.setPlaceholderText("Live output of the current task appears here.")
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tabs)
        split.addWidget(self.activity)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        split.setSizes([560, 190])
        self.setCentralWidget(split)

        # ---- status bar ---------------------------------------------------------------------
        sb = self.statusBar()
        self.job_label = QLabel("Ready")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(200)
        self.progress.setVisible(False)
        self.cancel_btn = QPushButton("Cancel task")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(ctx.cancel_job)
        sb.addWidget(self.job_label, 1)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.cancel_btn)

        self._menus()

        ctx.envsChanged.connect(self._fill_combo)
        ctx.currentChanged.connect(self._fill_combo)
        ctx.statesChanged.connect(lambda _s: self._fill_combo())
        ctx.statusChanged.connect(self._status)
        ctx.busyChanged.connect(self._busy)
        ctx.jobOutput.connect(self.activity.add_line)
        ctx.jobStep.connect(self._step)
        ctx.jobFinished.connect(self._finished)
        ctx.requestTab.connect(self.show_tab)
        ctx.requestExport.connect(lambda _p: self.show_tab("files"))
        self._fill_combo()
        self._status(ctx.status)

    # -- menus ------------------------------------------------------------------------------
    def _menus(self) -> None:
        mb = self.menuBar()
        f = mb.addMenu("&File")
        q = QAction("&Quit", self)
        q.setShortcut(QKeySequence.StandardKey.Quit)
        q.triggered.connect(self.close)
        f.addAction(q)

        t = mb.addMenu("&Tools")
        for text, fn in (
            ("Fix everything", self._fix),
            ("Install / repair privileged helper (pkexec + polkit)", self._install_helper),
            ("Remove privileged helper", self._remove_helper),
            (None, None),
            ("Open data folder", lambda: hostutil.open_path(paths.data_dir())),
            ("Open log folder", lambda: hostutil.open_path(paths.log_dir())),
        ):
            if text is None:
                t.addSeparator()
                continue
            a = QAction(text, self)
            a.triggered.connect(lambda _c=False, fn=fn: fn())
            t.addAction(a)

        h = mb.addMenu("&Help")
        v = QAction("Containers are &not virtual machines", self)
        v.triggered.connect(lambda: not_a_vm_notice(self))
        h.addAction(v)
        a = QAction("&About ContainerQT Studio", self)
        a.triggered.connect(self._about)
        h.addAction(a)

    def _about(self) -> None:
        QMessageBox.about(
            self, "About ContainerQT Studio",
            f"<b>ContainerQT Studio {__version__}</b><br>A GUI for developing inside Docker: one-click Linux environments, "
            "a build window for AppImage/deb/rpm and more, a desktop view, file import/export, and a Fix button."
            "<br><br>Logs: " + str(paths.log_file()),
        )

    # -- toolbar behaviour --------------------------------------------------------------------
    def _fill_combo(self) -> None:
        c = self.env_combo
        c.blockSignals(True)
        c.clear()
        for e in self.ctx.store.all():
            c.addItem(f"{e.name}  [{self.ctx.state_of(e)}]", e.name)
        idx = c.findData(self.ctx.current_name)
        if idx >= 0:
            c.setCurrentIndex(idx)
        else:
            c.setPlaceholderText("(no environments yet)")
        c.blockSignals(False)

    def _combo_activated(self, idx: int) -> None:
        self.ctx.set_current(self.env_combo.itemData(idx))

    def _status(self, st) -> None:
        color = theme.OK if st.ok else theme.BAD
        self.docker_label.setText(f'<span style="color:{color}">&#9679;</span> {st.describe()}')
        self.docker_label.setToolTip(st.detail)

    def _busy(self, busy: bool, title: str) -> None:
        self.progress.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        self.fix_btn.setEnabled(not busy)
        if busy:
            self.progress.setRange(0, 0)
            self.job_label.setText(title)
        else:
            self.job_label.setText("Ready")

    def _step(self, i: int, n: int, title: str) -> None:
        self.progress.setRange(0, n)
        self.progress.setValue(i - 1)
        self.job_label.setText(f"[{i}/{n}] {title}")

    def _finished(self, ok: bool, summary: str) -> None:
        self.statusBar().showMessage(("Done" if ok else summary), 8000)
        if not ok and summary != "Cancelled":
            self.activity.add_line(f"!!! {summary} - open the Log tab for details")
            self.show_tab("log")

    def show_tab(self, key: str) -> None:
        if key in self._tab_index:
            self.tabs.setCurrentIndex(self._tab_index[key])

    # -- actions ---------------------------------------------------------------------------------
    def _fix(self) -> None:
        if self.ctx.busy:
            return
        r = QMessageBox.question(
            self, "Fix",
            "ContainerQT Studio will check Docker and every environment and repair what is broken: install/start Docker, "
            "restore permissions, restart or recreate containers, rebuild missing images, restart the desktop.\n\n"
            "You may be asked for your admin password. Continue?",
        )
        if r == QMessageBox.StandardButton.Yes:
            self.ctx.start_job("Fix", fixer.fix_steps(self.ctx.client, self.ctx.store))

    def _install_helper(self) -> None:
        if privilege.helper_installed():
            if QMessageBox.question(self, "Helper", "The helper is already installed. Reinstall it?") != \
                    QMessageBox.StandardButton.Yes:
                return
        QMessageBox.information(
            self, "Administrator access",
            "ContainerQT Studio will install a small root-owned helper in /usr/libexec/dockerqt together with a polkit policy.\n\n"
            "You will see one password prompt that names /bin/sh - that is this one-time installation. Later actions "
            "use the dedicated 'ContainerQT Studio' prompt.",
        )
        self.ctx.start_job("Install privileged helper", [Step("Install helper", fn=privilege.install_helper)])

    def _remove_helper(self) -> None:
        self.ctx.start_job("Remove privileged helper", [Step("Remove helper", fn=privilege.remove_helper)])

    def closeEvent(self, e) -> None:  # noqa: N802
        if self.ctx.busy:
            r = QMessageBox.question(self, "Task running", "A task is still running. Cancel it and quit?")
            if r != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
        self.ctx.shutdown()
        e.accept()
