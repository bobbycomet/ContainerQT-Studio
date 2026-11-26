"""Application entry point."""
from __future__ import annotations

import argparse
import os
import sys

from .. import __version__, logger, paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="containerqt", description="Develop inside Docker with a GUI.")
    ap.add_argument("--version", action="version", version=f"ContainerQT Studio {__version__}")
    ap.add_argument("--debug", action="store_true", help="also print the log to the terminal")
    args, qt_args = ap.parse_known_args(argv if argv is not None else sys.argv[1:])

    log = logger.setup(debug=args.debug)

    if paths.in_appimage():
        # Chromium's sandbox cannot work from inside an AppImage's nosuid FUSE mount.
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    from PyQt6.QtCore import QCoreApplication, Qt

    try:  # must be imported before QApplication is created
        from PyQt6 import QtWebEngineWidgets  # noqa: F401
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    except Exception:  # noqa: BLE001
        pass

    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    from . import theme
    from .context import AppContext, LogBridge
    from .main_window import MainWindow

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("ContainerQT Studio")
    app.setDesktopFileName(paths.APP_ID)
    icon = paths.resource_dir() / "dockerqt.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    theme.apply(app)

    bridge = LogBridge()
    log.info("ContainerQT Studio %s starting (appimage=%s)", __version__, paths.in_appimage())
    ctx = AppContext()
    win = MainWindow(ctx, bridge)
    win.show()
    ctx.start()
    from PyQt6.QtCore import QSettings, QTimer

    settings = QSettings("DockerQT", "DockerQT")
    if not settings.value("ack_not_a_vm", False, type=bool):
        from .dialogs import not_a_vm_notice

        QTimer.singleShot(400, lambda: not_a_vm_notice(win, settings))
    code = app.exec()
    log.info("ContainerQT Studio exited with code %s", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
