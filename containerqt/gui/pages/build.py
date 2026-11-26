"""Build window: per-environment packaging templates + editor + run in the container."""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
                             QVBoxLayout, QWidget)

from .. import theme
from ... import buildscripts, env_ops, hostutil, profiles
from ...templates import safe_name
from ...runner import Step
from ..context import AppContext
from ..widgets import BashHighlighter, mono


class BuildPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._dirty = False
        split = QSplitter(Qt.Orientation.Horizontal, self)
        outer = QVBoxLayout(self)
        outer.addWidget(split)

        # ---- left: templates ----------------------------------------------------------
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.env_label = QLabel("")
        self.env_label.setStyleSheet(f"color:{theme.MUTED};")
        self.env_label.setWordWrap(True)
        ll.addWidget(self.env_label)
        ll.addWidget(QLabel("Build scripts for this environment"))
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._pick)
        ll.addWidget(self.list, 1)
        self.desc = QLabel("")
        self.desc.setWordWrap(True)
        self.desc.setStyleSheet(f"color:{theme.MUTED};")
        ll.addWidget(self.desc)
        split.addWidget(left)

        # ---- right: settings + editor ----------------------------------------------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        box = QGroupBox("Build settings")
        form = QFormLayout(box)
        self.app_name = QLineEdit("MyApp")
        self.version = QLineEdit("1.0.0")
        proj = QWidget()
        pl = QHBoxLayout(proj)
        pl.setContentsMargins(0, 0, 0, 0)
        self.src = QLineEdit("/workspace")
        pb = QPushButton("Browse...")
        pb.clicked.connect(self._browse_src)
        pl.addWidget(self.src, 1)
        pl.addWidget(pb)
        self.out = QLineEdit("/workspace/dist")
        form.addRow("App name", self.app_name)
        form.addRow("Version", self.version)
        form.addRow("Project folder (SRC_DIR)", proj)
        form.addRow("Output folder (OUT_DIR)", self.out)
        self.extra_env = QLineEdit()
        self.extra_env.setPlaceholderText("extra variables for the script:  NAME=value; OTHER=value")
        form.addRow("Extra variables", self.extra_env)
        prof = QWidget()
        pr = QHBoxLayout(prof)
        pr.setContentsMargins(0, 0, 0, 0)
        self.profile = QComboBox()
        self.profile.setMinimumWidth(200)
        self.profile.activated.connect(self._profile_picked)
        sp = QPushButton("Save profile...")
        sp.setToolTip("Keep the script, name, version, folders and variables so any environment can reuse them")
        sp.clicked.connect(self._save_profile)
        self.del_profile = QPushButton("Delete")
        self.del_profile.clicked.connect(self._delete_profile)
        pr.addWidget(self.profile, 1)
        pr.addWidget(sp)
        pr.addWidget(self.del_profile)
        form.addRow("Build profile", prof)
        rl.addWidget(box)

        self.editor = QPlainTextEdit()
        self.editor.setFont(mono())
        self.editor.setTabStopDistance(32)
        self.editor.textChanged.connect(lambda: setattr(self, "_dirty", True))
        self._hl = BashHighlighter(self.editor.document())
        rl.addWidget(self.editor, 1)

        row = QHBoxLayout()
        self.run_btn = QPushButton("Run build")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self._run)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(ctx.cancel_job)
        self.save_btn = QPushButton("Save as my script...")
        self.save_btn.clicked.connect(self._save)
        self.open_ws = QPushButton("Open workspace")
        self.open_ws.clicked.connect(self._open_ws)
        row.addWidget(self.run_btn)
        row.addWidget(self.stop_btn)
        row.addStretch(1)
        row.addWidget(self.save_btn)
        row.addWidget(self.open_ws)
        rl.addLayout(row)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 700])

        ctx.currentChanged.connect(self.reload_templates)
        ctx.envsChanged.connect(self._update_state)
        ctx.busyChanged.connect(lambda *_: self._update_state())
        ctx.statesChanged.connect(lambda _s: self._update_state())
        self.reload_templates()
        ctx.profilesChanged.connect(self._fill_profiles)
        self._fill_profiles()

    # -- templates -------------------------------------------------------------------------
    def reload_templates(self) -> None:
        env = self.ctx.current_env()
        self.list.blockSignals(True)
        self.list.clear()
        if env:
            for t in buildscripts.templates_for(env.family):
                it = QListWidgetItem(t.title)
                it.setData(Qt.ItemDataRole.UserRole, ("tpl", t.id))
                it.setToolTip(t.description)
                self.list.addItem(it)
            for p in buildscripts.list_user_scripts():
                it = QListWidgetItem("★ " + p.stem)
                it.setData(Qt.ItemDataRole.UserRole, ("user", str(p)))
                self.list.addItem(it)
        self.list.blockSignals(False)
        if self.list.count():
            self.list.setCurrentRow(0)
            self._pick(self.list.item(0), None)
        else:
            self.editor.clear()
            self.desc.setText("")
        self._update_state()

    def _pick(self, item: QListWidgetItem | None, _prev) -> None:
        if not item:
            return
        if self._dirty and self.editor.toPlainText().strip():
            r = QMessageBox.question(self, "Discard edits?", "Replace the script in the editor? Unsaved edits are lost.")
            if r != QMessageBox.StandardButton.Yes:
                return
        kind, ref = item.data(Qt.ItemDataRole.UserRole)
        if kind == "tpl":
            t = buildscripts.get(ref)
            if not t:
                return
            self.editor.setPlainText(t.script)
            self.desc.setText(t.description)
        else:
            self.editor.setPlainText(Path(ref).read_text(encoding="utf-8"))
            self.desc.setText("Your saved script.")
        self._dirty = False

    def _save(self) -> None:
        name, ok = QInputDialog.getText(self, "Save script", "Name:")
        if ok and name.strip():
            p = buildscripts.save_user_script(name.strip(), self.editor.toPlainText())
            self._dirty = False
            self.reload_templates()
            self.ctx.jobOutput.emit(f"Saved {p}")

    def _browse_src(self) -> None:
        env = self.ctx.current_env()
        if not env:
            return
        if env.ephemeral:
            QMessageBox.information(self, "One-time environment",
                                    "A one-time environment has no folder on this computer.\nBring your project in "
                                    "on the Files tab, then type its path here (for example /workspace/myproject).")
            return
        d = QFileDialog.getExistingDirectory(self, "Project folder (inside the workspace)", env.workspace)
        if not d:
            return
        try:
            rel = Path(d).resolve().relative_to(Path(env.workspace).resolve())
        except ValueError:
            QMessageBox.warning(self, "Outside workspace",
                                "Pick a folder inside the workspace, or use the Files tab to import it first.")
            return
        self.src.setText("/workspace" + ("/" + rel.as_posix() if str(rel) != "." else ""))

    def _open_ws(self) -> None:
        env = self.ctx.current_env()
        if env and env.workspace:
            hostutil.open_path(env.workspace)

    # -- build profiles ------------------------------------------------------------------------
    def _fill_profiles(self) -> None:
        self.profile.clear()
        self.profile.addItem("(none - current settings)", "")
        for p in self.ctx.profiles.all():
            self.profile.addItem(p.name, p.name)
        self.del_profile.setEnabled(self.profile.count() > 1)

    def _profile_picked(self, _i: int) -> None:
        name = self.profile.currentData()
        p = self.ctx.profiles.get(name) if name else None
        self.del_profile.setEnabled(p is not None)
        if p is None:
            return
        env = self.ctx.current_env()
        if env:
            ok, why = p.applies_to(env.family)
            if not ok:
                QMessageBox.warning(self, "Build profile", why)
                return
        self.app_name.setText(p.app_name)
        self.version.setText(p.version)
        self.src.setText(p.src_dir)
        self.out.setText(p.out_dir)
        self.extra_env.setText(profiles.format_env_text(p.extra_env))
        self.editor.setPlainText(p.script)
        self._dirty = False

    def _save_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "Save build profile", "Profile name:")
        if not ok or not name.strip():
            return
        try:
            p = profiles.BuildProfile(
                name=safe_name(name), script=self.editor.toPlainText(), template_id=self._current_template_id(),
                app_name=self.app_name.text().strip() or "App", version=self.version.text().strip() or "0.0.1",
                src_dir=self.src.text().strip() or "/workspace", out_dir=self.out.text().strip() or "/workspace/dist",
                extra_env=profiles.parse_env_text(self.extra_env.text()))
            if self.ctx.profiles.get(p.name) and QMessageBox.question(
                    self, "Build profile", f"Replace the profile '{p.name}'?") != QMessageBox.StandardButton.Yes:
                return
            self.ctx.profiles.save(p)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Build profile", str(e))
            return
        self.ctx.profilesChanged.emit()
        self.profile.setCurrentIndex(self.profile.findData(p.name))

    def _current_template_id(self) -> str:
        it = self.list.currentItem()
        data = it.data(Qt.ItemDataRole.UserRole) if it else None
        return data[1] if data and data[0] == "tpl" else ""

    def _delete_profile(self) -> None:
        name = self.profile.currentData()
        if name and QMessageBox.question(self, "Build profile", f"Delete the profile '{name}'?"
                                         ) == QMessageBox.StandardButton.Yes:
            self.ctx.profiles.delete(name)
            self.ctx.profilesChanged.emit()

    # -- run ----------------------------------------------------------------------------------
    def _update_state(self) -> None:
        env = self.ctx.current_env()
        if env:
            self.env_label.setText(f"Environment: {env.name}  ({env.base_image}, {env.family})  -  "
                                   f"{self.ctx.state_of(env)}")
        else:
            self.env_label.setText("Create an environment first (Environments tab).")
        ok = bool(env) and self.ctx.status.ok and not self.ctx.busy
        self.run_btn.setEnabled(ok)
        self.stop_btn.setEnabled(self.ctx.busy)
        self.save_btn.setEnabled(bool(env))
        self.open_ws.setEnabled(bool(env) and bool(env.workspace) and not env.ephemeral)

    def _run(self) -> None:
        env = self.ctx.current_env()
        if not env:
            return
        script = self.editor.toPlainText()
        if not script.strip():
            return
        name = self.app_name.text().strip() or "App"
        version = self.version.text().strip() or "0.0.1"
        src = self.src.text().strip() or "/workspace"
        out_dir = self.out.text().strip() or "/workspace/dist"
        text = script if script.endswith("\n") else script + "\n"
        stamp = f"build-{time.strftime('%Y%m%d-%H%M%S')}.sh"
        c = self.ctx.client
        steps: list[Step] = []
        if not self.ctx.is_running(env):
            if env.ephemeral:
                QMessageBox.information(self, "Start the environment",
                                        "A one-time environment only exists while it is running. Start it on the "
                                        "Environments tab, bring your project in on the Files tab, then build.")
                return
            steps += env_ops.start_steps(c, env)
        if env.ephemeral:  # no host folder: put the script inside the running container
            in_container = f"/tmp/dockerqt/{stamp}"
            steps += env_ops.put_script_steps(c, env, text, in_container)
        else:
            script_dir = Path(env.workspace) / ".dockerqt"
            script_dir.mkdir(parents=True, exist_ok=True)
            script_path = script_dir / stamp
            script_path.write_text(text, encoding="utf-8")
            script_path.chmod(0o755)
            in_container = f"/workspace/.dockerqt/{stamp}"
        try:
            extra = profiles.parse_env_text(self.extra_env.text())
        except ValueError as e:
            QMessageBox.warning(self, "Extra variables", str(e))
            return
        env_vars = {**extra, "APP_NAME": name, "APP_VERSION": version, "SRC_DIR": src, "OUT_DIR": out_dir}
        steps.append(Step("Run build script", argv=c.exec_argv(env.container, "bash", "-l", in_container,
                                                               workdir=src, env=env_vars)))

        def done(ok: bool, _s: str) -> None:
            if not ok:
                self.ctx.requestTab.emit("log")
                return
            r = QMessageBox.question(self, "Build finished",
                                     f"The build finished. Export the results in {out_dir} to this computer now?")
            if r == QMessageBox.StandardButton.Yes:
                self.ctx.requestExport.emit(out_dir)

        self.ctx.start_job(f"Build {name} {version} in '{env.name}'", steps, done)
