"""GUI behaviour of the new-environment form and of one-time environments on the other tabs."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PyQt6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

try:  # same ordering as containerqt/gui/app.py
    from PyQt6 import QtWebEngineWidgets  # noqa: F401
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
except Exception:  # noqa: BLE001
    pass

from containerqt import logger  # noqa: E402
from containerqt.client import DockerStatus  # noqa: E402
from containerqt.gui import theme  # noqa: E402
from containerqt.gui.context import AppContext, LogBridge  # noqa: E402
from containerqt.gui.main_window import MainWindow  # noqa: E402
from containerqt.models import EPHEMERAL, EnvSpec  # noqa: E402


def spin(ms=100):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture()
def win(tmp_path, monkeypatch):
    for k, v in (("HOME", tmp_path), ("XDG_DATA_HOME", tmp_path / "data"), ("XDG_CONFIG_HOME", tmp_path / "cfg")):
        monkeypatch.setenv(k, str(v))
    logger.setup()
    app = QApplication.instance() or QApplication(["dockerqt-test"])
    theme.apply(app)
    from containerqt import compat
    monkeypatch.setattr(compat, "gather_facts", lambda client=None, ports=(): compat.HostFacts(
        docker_path="/usr/bin/docker", docker_info={"ServerVersion": "27"}, docker_root_free=500 * compat.GB, home=str(tmp_path)))
    ctx = AppContext()
    w = MainWindow(ctx, LogBridge())
    w.show()
    ctx._on_poll(DockerStatus("ok", version="99.0"), {})
    spin()
    yield w
    ctx.shutdown()
    w.close()


def enabled(combo, key):
    i = combo.findData(key)
    return combo.model().item(i).isEnabled(), combo.itemData(i, Qt.ItemDataRole.ToolTipRole)


def pick(combo, key):
    combo.setCurrentIndex(combo.findData(key))
    spin(20)


def test_choices_are_greyed_out_with_a_reason(win):
    f = win.envs_page
    pick(f.de, "kde")
    assert f.server.currentData() == "x11"
    ok, why = enabled(f.server, "wayland")
    assert not ok and "seat" in why                      # KDE Wayland needs a GPU seat
    assert enabled(f.comp, "kwin")[0] and not enabled(f.comp, "muffin")[0]
    assert not enabled(f.comp, "sway")[0] and "Wayland" in enabled(f.comp, "sway")[1]

    assert f.comp.currentData() == "kwin"               # a desktop starts with its own compositor

    pick(f.de, "xfce")
    assert f.comp.currentData() == "xfwm4"
    assert enabled(f.comp, "mutter")[0] and enabled(f.comp, "kwin")[0] and enabled(f.comp, "marco")[0]
    pick(f.comp, "mutter")                              # a deliberate choice sticks ...
    assert f.comp.currentData() == "mutter"
    pick(f.de, "kde")                                   # ... until the desktop changes
    assert f.comp.currentData() == "kwin"
    pick(f.de, "xfce")

    pick(f.de, "sway")                                   # Wayland-only desktop: X11 greys out, compositor follows
    assert f.server.currentData() == "wayland" and not enabled(f.server, "x11")[0]
    assert f.comp.currentData() == "sway" and not enabled(f.comp, "labwc")[0]

    pick(f.de, "cinnamon")                               # back to X11; only its own compositor works
    assert f.server.currentData() == "x11" and f.comp.currentData() == "muffin"
    assert not enabled(f.comp, "kwin")[0]


def test_distro_greys_out_unavailable_desktops(win):
    f = win.envs_page
    f.distro.setCurrentIndex(f.distro.findData("rocky-9"))
    spin(20)
    ok, why = enabled(f.de, "kde")
    assert not ok and "RHEL" in why
    assert f.de.currentData() != "kde"
    f.distro.setCurrentIndex(f.distro.findData("ubuntu-2404"))
    spin(20)
    assert enabled(f.de, "kde")[0]


def test_desktop_checkbox_disables_the_choices(win):
    f = win.envs_page
    f.desktop.setChecked(False)
    assert not f.de.isEnabled() and not f.server.isEnabled() and not f.comp.isEnabled()
    f.desktop.setChecked(True)
    assert f.de.isEnabled() and f.comp.isEnabled()


def test_persistent_toggle_shows_and_hides_the_drive_choice(win):
    f = win.envs_page
    assert f.persist.isChecked() and not f.drive_row.isHidden() and f.drive.count() >= 1
    f.persist.setChecked(False)
    assert f.drive_row.isHidden() and f.ws_row.isHidden() and "One-time" in f.drive_info.text()
    f.persist.setChecked(True)
    assert not f.drive_row.isHidden()


def test_create_one_time_and_persistent_environments(win, monkeypatch, tmp_path):
    f, ctx = win.envs_page, win.ctx
    jobs = []
    monkeypatch.setattr(ctx, "start_job", lambda title, steps, done=None: jobs.append((title, steps)) or True)

    def register():  # the environment is registered by the job's first real step; run it here
        _title, steps = jobs[-1]
        assert next(st for st in steps if st.title == "Prepare build context").fn(lambda _m: None)

    f.persist.setChecked(False)
    f.name.setText("Quick test")
    pick(f.de, "i3")
    f._create()
    register()
    quick = ctx.store.get("Quick test")
    assert quick.storage == EPHEMERAL and quick.storage_root == "" and quick.workspace == ""
    assert (quick.de, quick.display_server, quick.compositor) == ("i3", "x11", "i3")

    f.persist.setChecked(True)
    root = tmp_path / "bigdrive" / "DockerQT"
    f._custom_roots.append(str(root))
    f._fill_drives(select=str(root))
    f.name.setText("Real work")
    pick(f.de, "kde")
    f._create()
    register()
    real = ctx.store.get("Real work")
    assert real.persistent and real.storage_root == str(root) and real.workspace == str(root / "real-work" / "workspace")
    assert (real.de, real.compositor) == ("kde", "kwin")
    assert [t for t, _ in jobs] == ["Create environment 'Quick test'", "Create environment 'Real work'"]
    assert real.container != quick.container and real.port != quick.port


def test_one_time_environment_on_files_and_build_tabs(win, monkeypatch):
    ctx = win.ctx
    spec = EnvSpec(name="Scratch", slug="scratch", distro_key="ubuntu-2204", base_image="ubuntu:22.04",
                   family="apt", workspace="", storage=EPHEMERAL)
    ctx.store.put(spec)
    ctx.set_current("Scratch")
    ctx.env_changed()
    spin()
    files, build = win.files_page, win.build_page

    assert files.tree.isHidden() and "One-time" in files.tree_label.text()
    assert not build.open_ws.isEnabled()

    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    files._import(["/etc/hostname"])                       # not running -> explained, nothing queued
    assert shown and "Start" in shown[-1] and not ctx.busy
    build._run()
    assert len(shown) == 2 and "Start" in shown[-1] and not ctx.busy

    # running: the import is queued as a docker cp, not a write to a host folder
    jobs = []
    ctx._on_poll(DockerStatus("ok", version="99.0"), {"Scratch": "running"})
    monkeypatch.setattr(ctx, "start_job", lambda title, steps, done=None: jobs.append((title, steps)) or True)
    files._import(["/etc/hostname"])
    assert jobs and any(s.argv and "cp" in s.argv for s in jobs[0][1])
    assert not os.path.exists(os.path.join(os.getcwd(), ".dockerqt"))          # nothing leaked into the cwd


def test_adding_a_desktop_to_a_persistent_environment_replaces_the_saved_system_files(win, monkeypatch, tmp_path):
    """Otherwise the new desktop's files stay hidden behind the old saved /usr."""
    from containerqt.gui.pages import desktop as desktop_page

    ctx = win.ctx
    spec = EnvSpec(name="Saved", slug="saved", distro_key="ubuntu-2204", base_image="ubuntu:22.04", family="apt",
                   workspace=str(tmp_path / "ws"), desktop=False, de="kde", storage_root=str(tmp_path))
    ctx.store.put(spec)
    ctx.set_current("Saved")
    ctx.env_changed()
    seen = {}
    monkeypatch.setattr(desktop_page.env_ops, "create_steps",
                        lambda c, st, env, rebuild=False: seen.update(rebuild=rebuild, env=env) or [])
    monkeypatch.setattr(ctx, "start_job", lambda *a, **k: True)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes)
    win.desktop_page._add_desktop()
    assert seen["rebuild"] is True and seen["env"].desktop and seen["env"].de == "kde"
    assert "KDE Plasma" in asked[0] and "home folder and projects are kept" in asked[0]


# ------------------------------------------------------------------ templates, resources, profiles
def test_use_template_fills_the_form_and_creates_with_resources(win, monkeypatch, tmp_path):
    f, ctx = win.envs_page, win.ctx
    t = next(x for x in ctx.templates.all() if "Plasma 5" in x.name and "24.04" in x.name)
    jobs = []
    monkeypatch.setattr(ctx, "start_job", lambda title, steps, done=None: jobs.append((title, steps)) or True)
    f.load_template(t)
    assert f.distro.currentData() == t.distro_key
    assert f.de.currentData() == "kde"
    assert f.toolkit_boxes["qt5"].isChecked()
    f.persist.setChecked(False)
    f.resources.set_values(2, 4096, "custom")
    f._create()
    _title, steps = jobs[-1]
    next(st for st in steps if st.title == "Prepare build context").fn(lambda _m: None)
    made = ctx.store.get(f.name.text()) or ctx.store.all()[-1]
    assert made.template == t.name and made.cpus == 2 and made.memory_mb == 4096
    assert made.extras == t.extras


def test_templates_page_lists_builtins_and_use_switches_tab(win):
    page = win.templates_page
    assert page.list.count() >= 8
    page.list.setCurrentRow(0)
    page._use()
    spin(20)
    assert win.tabs.currentIndex() == win._tab_index["envs"]


def test_save_as_template_roundtrip(win, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    answers = iter([("My-KDE-Qt6", True), ("works", True)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: next(answers))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    win.envs_page.persist.setChecked(False)
    win.envs_page._save_template()
    assert win.ctx.templates.get("My-KDE-Qt6").description == "works"


def test_bad_extra_packages_are_rejected(win, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    f = win.envs_page
    f.persist.setChecked(False)
    f.extra_pkgs.setText("git; rm -rf /")
    assert f._build_spec() is None and shown


def test_unmounted_media_folder_is_blocked(win):
    from containerqt import compat, storage
    assert storage.unmounted_warning("/mnt/definitely-not-a-drive/x")
    assert storage.unmounted_warning("/home/x") == ""


def test_build_profile_roundtrip_in_gui(win, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    b = win.build_page
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Release", True))
    b.editor.setPlainText("echo hi")
    b.app_name.setText("Foo")
    b.extra_env.setText("MODE=release")
    b._save_profile()
    assert win.ctx.profiles.get("Release").extra_env == {"MODE": "release"}
    b.editor.setPlainText("")
    b.app_name.setText("x")
    b.profile.setCurrentIndex(b.profile.findData("Release"))
    b._profile_picked(0)
    assert b.app_name.text() == "Foo" and b.editor.toPlainText() == "echo hi"
