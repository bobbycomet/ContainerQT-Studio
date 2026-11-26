"""Desktop page: the environment's XFCE desktop, embedded via noVNC (QtWebEngine) or in a browser."""
from __future__ import annotations

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from .. import theme
from ... import desktops, env_ops, hostutil
from ...runner import Step
from ..context import AppContext

try:  # optional: bundled in the AppImage, but a source install may lack it
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    HAVE_WEB = True
except Exception:  # noqa: BLE001
    QWebEngineView = None  # type: ignore[assignment,misc]
    HAVE_WEB = False

VIEW_QUERY = "?autoconnect=1&resize=remote&reconnect=1&reconnect_delay=1500&show_dot=1"


class DesktopPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._loaded_url = ""
        lay = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.start_btn = QPushButton("Start desktop")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._start)
        self.restart_btn = QPushButton("Restart desktop")
        self.restart_btn.clicked.connect(lambda: self._start("restart"))
        self.browser_btn = QPushButton("Open in browser")
        self.browser_btn.clicked.connect(self._browser)
        self.term_btn = QPushButton("Terminal")
        self.term_btn.clicked.connect(self._terminal)
        self.reload_btn = QPushButton("Reload view")
        self.reload_btn.clicked.connect(self._reload)
        for b in (self.start_btn, self.restart_btn, self.reload_btn, self.browser_btn, self.term_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{theme.MUTED};")
        bar.addWidget(self.status)
        lay.addLayout(bar)

        self.stack = QStackedWidget()
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setStyleSheet(f"color:{theme.MUTED}; font-size:14px;")
        from PyQt6.QtCore import Qt

        self.msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.msg)
        self.view = None
        if HAVE_WEB:
            self.view = QWebEngineView()
            self.stack.addWidget(self.view)
        lay.addWidget(self.stack, 1)

        self.add_btn = QPushButton("Add a desktop to this environment (rebuild)")
        self.add_btn.clicked.connect(self._add_desktop)
        lay.addWidget(self.add_btn)

        ctx.currentChanged.connect(self._update)
        ctx.statesChanged.connect(lambda _s: self._update())
        ctx.busyChanged.connect(lambda *_: self._update())
        ctx.jobFinished.connect(lambda *_: QTimer.singleShot(500, self._maybe_load))
        self._update()

    # -- state -----------------------------------------------------------------------------
    def _update(self) -> None:
        env = self.ctx.current_env()
        running = self.ctx.is_running(env)
        busy = self.ctx.busy
        has_desktop = bool(env and env.desktop)
        self.add_btn.setVisible(bool(env) and not has_desktop)
        for b in (self.start_btn, self.restart_btn, self.browser_btn, self.reload_btn):
            b.setEnabled(has_desktop and running and not busy if b is not self.start_btn
                         else has_desktop and not busy and self.ctx.status.ok)
        self.term_btn.setEnabled(running)
        if not env:
            self._show_msg("Create an environment first (Environments tab).")
        elif not has_desktop:
            self._show_msg("This environment was created without a desktop.\n"
                           "Use the button below to add one, or work in the terminal and Build tab.")
        elif not running:
            self._show_msg("The environment is not running.\nPress 'Start desktop' to start it and show the desktop.")
        else:
            self.status.setText(f"{env.name} - {env.novnc_url}")
            self._maybe_load()

    def _show_msg(self, text: str) -> None:
        self.msg.setText(text)
        self.stack.setCurrentWidget(self.msg)
        self._loaded_url = ""
        self.status.setText("")

    def _maybe_load(self) -> None:
        env = self.ctx.current_env()
        if not (env and env.desktop and self.ctx.is_running(env)):
            return
        url = env.novnc_url + VIEW_QUERY
        if self.view is None:
            self._show_msg("Embedded view needs QtWebEngine, which is not installed.\n"
                           "Press 'Open in browser' to use the desktop in your web browser.")
            return
        if not hostutil.http_ok(env.novnc_url):
            self.msg.setText("Desktop is starting... press 'Start desktop' if it does not appear.")
            self.stack.setCurrentWidget(self.msg)
            QTimer.singleShot(2500, self._maybe_load)
            return
        if self._loaded_url != url:
            self.view.setUrl(QUrl(url))
            self._loaded_url = url
        self.stack.setCurrentWidget(self.view)

    # -- actions -------------------------------------------------------------------------------
    def _start(self, action: str | bool = "start") -> None:
        env = self.ctx.current_env()
        if not env or not env.desktop:
            return
        action = action if isinstance(action, str) else "start"
        c = self.ctx.client
        steps: list[Step] = []
        if not self.ctx.is_running(env):
            steps += env_ops.start_steps(c, env)
        else:
            steps += env_ops.start_desktop_steps(c, env, action)
        self.ctx.start_job(f"{action.capitalize()} desktop of '{env.name}'", steps)

    def _reload(self) -> None:
        self._loaded_url = ""
        if self.view is not None:
            self.view.reload()
        self._maybe_load()

    def _browser(self) -> None:
        env = self.ctx.current_env()
        if env and not hostutil.open_url(env.novnc_url + VIEW_QUERY):
            QMessageBox.information(self, "Open in browser", f"Open this address in a browser:\n{env.novnc_url}")

    def _terminal(self) -> None:
        env = self.ctx.current_env()
        if env:
            hostutil.launch_terminal(self.ctx.client.exec_argv(env.container, "bash", "-l",
                                                               workdir="/workspace", interactive=True))

    def _add_desktop(self) -> None:
        env = self.ctx.current_env()
        if not env:
            return
        de, server, comp = desktops.normalize(env.de, env.display_server, env.compositor)
        what = desktops.describe(de, server, comp)
        if env.persistent:
            effect = ("Its system files on the drive (installed packages, /usr, /etc, /var, /opt) are replaced by a "
                      "fresh copy that includes the desktop. Your home folder and projects are kept.")
        elif env.ephemeral:
            effect = "The running session is discarded and replaced by a fresh one."
        else:
            effect = ("Container changes outside /workspace are lost (snapshot first to keep them). Your workspace "
                      "folder is untouched.")
        if QMessageBox.question(self, "Add desktop", f"Rebuild '{env.name}' with {what}?\n\n{effect}",
                                ) != QMessageBox.StandardButton.Yes:
            return
        env.desktop, env.de, env.display_server, env.compositor = True, de, server, comp
        self.ctx.store.put(env)
        # persistent: the saved system folders must be replaced, or the new desktop's files stay hidden behind them
        self.ctx.start_job(f"Rebuild '{env.name}' with desktop",
                           env_ops.create_steps(self.ctx.client, self.ctx.store, env, rebuild=env.persistent))
