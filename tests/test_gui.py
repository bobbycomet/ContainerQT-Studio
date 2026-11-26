"""Headless GUI smoke test (offscreen Qt, fake Docker status)."""
import os
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
HOME = tempfile.mkdtemp(prefix="dqt-gui-")
os.environ["HOME"] = HOME
os.environ["XDG_DATA_HOME"] = HOME + "/data"
os.environ["XDG_CONFIG_HOME"] = HOME + "/config"

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from containerqt import logger  # noqa: E402
from containerqt.client import DockerStatus  # noqa: E402
from containerqt.gui import theme  # noqa: E402
from containerqt.gui.context import AppContext, LogBridge  # noqa: E402
from containerqt.gui.main_window import MainWindow  # noqa: E402
from containerqt.models import EnvSpec  # noqa: E402
from containerqt.runner import Step  # noqa: E402


def spin(ms=200):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_window_and_job_flow():
    logger.setup()
    # Same ordering as containerqt/gui/app.py: WebEngine (if installed) must be imported before the QApplication.
    try:
        from PyQt6 import QtWebEngineWidgets  # noqa: F401
        from PyQt6.QtCore import QCoreApplication, Qt
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    except Exception:  # noqa: BLE001
        pass
    app = QApplication.instance() or QApplication(["dockerqt-test"])
    theme.apply(app)
    bridge = LogBridge()
    ctx = AppContext()
    win = MainWindow(ctx, bridge)
    win.show()
    spin()

    # tabs exist
    assert [win.tabs.tabText(i) for i in range(win.tabs.count())] == \
        ["Environments", "Templates", "Build", "Desktop", "Files", "Log"]

    # no docker daemon here: pretend it is OK and register an env
    ctx._on_poll(DockerStatus("ok", version="99.0"), {})
    spec = EnvSpec(name="Ubuntu 22.04 LTS", slug="ubuntu-22-04-lts", distro_key="ubuntu-2204",
                   base_image="ubuntu:22.04", family="apt", workspace=HOME + "/ws")
    ctx.store.put(spec)
    ctx.set_current(spec.name)
    ctx.env_changed()
    ctx._on_poll(DockerStatus("ok", version="99.0"), {spec.name: "running"})
    spin()
    assert win.envs_page.table.rowCount() == 1
    assert win.build_page.list.count() >= 8          # templates for apt
    assert "dpkg-deb" in "".join(win.build_page.list.item(i).text() for i in range(win.build_page.list.count())) \
        or any("Debian" in win.build_page.list.item(i).text() for i in range(win.build_page.list.count()))
    assert win.build_page.run_btn.isEnabled()

    # a job streams output into the activity pane and the log page
    results = []
    ok = ctx.start_job("demo", [Step("echo", argv=["sh", "-c", "echo hello-from-job"])],
                       lambda o, s: results.append(o))
    assert ok and ctx.busy
    assert not ctx.start_job("second", [Step("x", argv=["true"])])   # busy -> refused
    for _ in range(30):
        spin(100)
        if results:
            break
    assert results == [True]
    assert "hello-from-job" in win.activity.toPlainText()
    assert "Job finished: demo" in win.log_page.console.toPlainText()

    # failing job -> log failure shown, switches to log tab
    results.clear()
    ctx.start_job("failing", [Step("bad", argv=["sh", "-c", "echo oops; exit 2"])], lambda o, s: results.append(o))
    for _ in range(30):
        spin(100)
        if results:
            break
    assert results == [False]
    assert win.tabs.currentIndex() == win._tab_index["log"]
    win.log_page.level.setCurrentIndex(3)  # failures only
    text = win.log_page.console.toPlainText()
    assert "FAILED" in text and "oops" in text

    # drop zone import
    src = HOME + "/file.txt"
    open(src, "w").write("data")
    win.files_page._import([src])
    for _ in range(30):
        spin(100)
        if not ctx.busy:
            break
    assert os.path.exists(HOME + "/ws/file.txt")

    # grab each tab to make sure nothing crashes when rendered
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        spin(50)
        win.grab()
    ctx.shutdown()
