"""Environments page: one-click creation + management of existing environments."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import theme
from ... import compat, desktops, distros, env_ops, fixer, health, hostutil, paths, storage, toolkits
from ...findings import has_errors
from ...templates import EnvTemplate
from ..dialogs import FindingsDialog
from ..resource_panel import ResourcePanel
from ...models import EPHEMERAL, PERSISTENT, EnvSpec, slugify
from ..context import AppContext

STATE_COLORS = {"running": theme.OK, "exited": theme.WARN, "created": theme.WARN, "paused": theme.WARN,
                "missing": theme.BAD, "dead": theme.BAD, "unknown": theme.MUTED}


class EnvsPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        root = QHBoxLayout(self)

        # ---- left: new environment form -----------------------------------------
        box = QGroupBox("New environment")
        form = QFormLayout(box)
        self.form = form
        self.distro = QComboBox()
        for d in distros.CATALOG:
            self.distro.addItem(d.label, d.key)
        self.distro.addItem("Custom image...", distros.CUSTOM_KEY)
        self.distro.currentIndexChanged.connect(self._distro_changed)
        form.addRow("Linux", self.distro)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color:{theme.MUTED};")
        form.addRow("", self.note)

        self.custom_image = QLineEdit()
        self.custom_image.setPlaceholderText("e.g. linuxmintd/mint22-amd64 or ghcr.io/org/image:tag")
        self.custom_family = QComboBox()
        self.custom_family.addItems(distros.FAMILIES)
        self.custom_row = QWidget()
        cr = QHBoxLayout(self.custom_row)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.addWidget(self.custom_image, 1)
        cr.addWidget(self.custom_family)
        form.addRow("Image / pkg mgr", self.custom_row)

        self.name = QLineEdit()
        self.name.textEdited.connect(lambda _t: setattr(self, "_name_touched", True))
        self._name_touched = False
        form.addRow("Name", self.name)

        # ---- desktop: environment, display server, compositor ------------------------------------
        self.desktop = QCheckBox("Desktop (use it like a VM)")
        self.desktop.setChecked(True)
        self.desktop.toggled.connect(lambda _on: self._sync_desktop())
        form.addRow("", self.desktop)

        self._last_de = ""
        self.de = QComboBox()
        for key in desktops.DE_ORDER:
            d = desktops.DESKTOPS[key]
            self.de.addItem(d.label + (" (experimental)" if d.experimental else ""), key)
        self.server = QComboBox()
        for key in desktops.SERVERS:
            self.server.addItem(desktops.SERVER_LABEL[key], key)
        self.comp = QComboBox()
        for key, c in desktops.COMPOSITORS.items():
            self.comp.addItem(c.label, key)
        for w in (self.de, self.server, self.comp):
            w.currentIndexChanged.connect(lambda _i: self._sync_desktop())
        form.addRow("Desktop environment", self.de)
        form.addRow("Display server", self.server)
        form.addRow("Compositor / WM", self.comp)
        self.desk_note = QLabel("")
        self.desk_note.setWordWrap(True)
        self.desk_note.setStyleSheet(f"color:{theme.MUTED};")
        form.addRow("", self.desk_note)

        # ---- storage: persistent on a chosen drive, or one-time -------------------------------------
        self.persist = QCheckBox("Persistent: keep installed packages, settings and files")
        self.persist.setChecked(True)
        self.persist.setToolTip("On: everything inside the environment survives restarts and is stored on the drive "
                                "you pick.\nOff: a one-time environment for quick tests; it is discarded when stopped.")
        self.persist.toggled.connect(lambda _on: self._storage_changed())
        form.addRow("", self.persist)

        drive_row = QWidget()
        dl = QHBoxLayout(drive_row)
        dl.setContentsMargins(0, 0, 0, 0)
        self.drive = QComboBox()
        self.drive.currentIndexChanged.connect(lambda _i: self._storage_changed())
        self.drive.setToolTip("ContainerQT Studio remembers which drive holds an environment and refuses to start it if that "
                              "drive is not mounted, so it can never silently start an empty copy on your system drive.\n"
                              "Keep the drive connected while the environment is in use.")
        self.drive_browse = QPushButton("Folder...")
        self.drive_browse.setToolTip("Store it in a folder you choose (the drive is detected from the folder)")
        self.drive_browse.clicked.connect(self._browse_storage)
        self.drive_rescan = QPushButton("Rescan")
        self.drive_rescan.setToolTip("Look for newly connected drives")
        self.drive_rescan.clicked.connect(lambda: self._fill_drives())
        dl.addWidget(self.drive, 1)
        dl.addWidget(self.drive_browse)
        dl.addWidget(self.drive_rescan)
        self.drive_row = drive_row
        form.addRow("Store on", drive_row)
        self.drive_info = QLabel("")
        self.drive_info.setWordWrap(True)
        self.drive_info.setStyleSheet(f"color:{theme.MUTED};")
        form.addRow("", self.drive_info)
        self._custom_roots: list[str] = []

        ws_row = QWidget()
        wl = QHBoxLayout(ws_row)
        wl.setContentsMargins(0, 0, 0, 0)
        self.workspace = QLineEdit()
        self.workspace.setPlaceholderText("(default: inside the storage folder)")
        b = QPushButton("Browse...")
        b.clicked.connect(self._browse_ws)
        wl.addWidget(self.workspace, 1)
        wl.addWidget(b)
        self.ws_row = ws_row
        form.addRow("Project folder", ws_row)

        self.toolchain = QCheckBox("Compilers && build tools (gcc, cmake, meson...)")
        self.toolchain.setChecked(True)
        self.packaging = QCheckBox("Packaging tools (dpkg, rpm, squashfs, fakeroot...)")
        self.packaging.setChecked(True)
        self.fuse = QCheckBox("Allow FUSE mounts (adds SYS_ADMIN capability)")
        for w in (self.toolchain, self.packaging, self.fuse):
            form.addRow("", w)

        self.res = QComboBox()
        self.res.setEditable(True)
        self.res.addItems(["1280x800", "1440x900", "1920x1080", "2560x1440"])
        self.res.setCurrentText("1440x900")
        form.addRow("Desktop size", self.res)

        self.toolkit_boxes: dict[str, QCheckBox] = {}
        for key in toolkits.ORDER:
            cb = QCheckBox(toolkits.TOOLKITS[key].label)
            cb.setToolTip(toolkits.TOOLKITS[key].note)
            self.toolkit_boxes[key] = cb
        from PyQt6.QtWidgets import QGridLayout
        tk = QWidget()
        tkl = QGridLayout(tk)
        tkl.setContentsMargins(0, 0, 0, 0)
        for n, cb in enumerate(self.toolkit_boxes.values()):
            tkl.addWidget(cb, n // 2, n % 2)
        form.addRow("Toolkits", tk)
        self.extra_pkgs = QLineEdit()
        self.extra_pkgs.setPlaceholderText("extra packages, space separated (installed with the distro's package manager)")
        form.addRow("Extra packages", self.extra_pkgs)

        self.resources = ResourcePanel()
        form.addRow(self.resources)

        self.check_btn = QPushButton("Check compatibility")
        self.check_btn.setToolTip("Look for missing dependencies and unsupported settings before building")
        self.check_btn.clicked.connect(lambda: self._compat(show_ok=True))
        self.save_tpl_btn = QPushButton("Save as template...")
        self.save_tpl_btn.setToolTip("Remember these settings so you can recreate the same environment later")
        self.save_tpl_btn.clicked.connect(self._save_template)
        self.create_btn = QPushButton("Create environment")
        self.create_btn.setObjectName("primary")
        self.create_btn.clicked.connect(self._create)
        brow = QHBoxLayout()
        brow.addWidget(self.check_btn)
        brow.addWidget(self.save_tpl_btn)
        brow.addWidget(self.create_btn, 1)
        form.addRow(brow)

        # ---- right: existing environments --------------------------------------------
        right = QVBoxLayout()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Name", "Linux", "State", "Desktop", "Storage"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selected)
        right.addWidget(self.table, 1)

        self.buttons: dict[str, QPushButton] = {}
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        for key, label, row in (
            ("start", "Start", row1), ("stop", "Stop", row1), ("restart", "Restart", row1),
            ("terminal", "Terminal", row1), ("folder", "Workspace folder", row1),
            ("snapshot", "Snapshot", row2), ("restore", "Restore...", row2), ("rebuild", "Rebuild", row2),
            ("resources", "Resources...", row2), ("clone", "Clone...", row2), ("health", "Health check", row2),
            ("fix", "Fix this one", row2), ("delete", "Delete", row2),
        ):
            btn = QPushButton(label)
            if key == "delete":
                btn.setObjectName("danger")
            btn.clicked.connect(lambda _=False, k=key: self._action(k))
            self.buttons[key] = btn
            row.addWidget(btn)
        right.addLayout(row1)
        right.addLayout(row2)
        self.hint = QLabel("")
        self.hint.setStyleSheet(f"color:{theme.MUTED};")
        self.hint.setWordWrap(True)
        right.addWidget(self.hint)

        from PyQt6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(box)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(520)
        root.addWidget(scroll, 3)
        root.addLayout(right, 2)

        ctx.envsChanged.connect(self.refresh)
        ctx.useTemplate.connect(self._use_template_named)
        ctx.statesChanged.connect(lambda _s: self.refresh())
        ctx.busyChanged.connect(lambda _b, _t: self._update_buttons())
        ctx.statusChanged.connect(lambda _s: self._update_buttons())
        self._fill_drives()
        self._distro_changed()
        self._storage_changed()
        self.refresh()

    # -- form ------------------------------------------------------------------------
    def _distro_changed(self) -> None:
        key = self.distro.currentData()
        custom = key == distros.CUSTOM_KEY
        self.custom_row.setVisible(custom)
        label = self.form.labelForField(self.custom_row)
        if label:
            label.setVisible(custom)
        d = distros.by_key(key)
        self.note.setText(d.note if d else "Any image from a registry that has a supported package manager.")
        if not self._name_touched and d:
            self.name.setText(self._unique_name(d.label))
        self._sync_desktop()

    # -- desktop / compositor / display server choices (greyed when they cannot work together) -----------
    @staticmethod
    def _set_item(combo: QComboBox, i: int, enabled: bool, reason: str) -> None:
        item = combo.model().item(i)
        item.setEnabled(enabled)
        combo.setItemData(i, "" if enabled else reason, Qt.ItemDataRole.ToolTipRole)

    def _distro_ids(self) -> tuple[str, str]:
        key = self.distro.currentData()
        if key == distros.CUSTOM_KEY:
            return "", self.custom_family.currentText()
        d = distros.by_key(key)
        return key, d.family if d else ""

    def _sync_desktop(self) -> None:
        if getattr(self, "_syncing", False) or not hasattr(self, "comp"):
            return
        self._syncing = True
        try:
            on = self.desktop.isChecked()
            for w in (self.de, self.server, self.comp):
                w.setEnabled(on)
            dkey, family = self._distro_ids()
            wanted_comp = self.comp.currentData()
            if self.de.currentData() != self._last_de:
                wanted_comp = ""  # a different desktop starts with its own compositor; a deliberate pick then sticks
            de, server, comp = desktops.normalize(self.de.currentData(), self.server.currentData(),
                                                  wanted_comp, dkey, family)
            self._last_de = de
            for i in range(self.de.count()):
                ok, why = desktops.de_state(self.de.itemData(i), dkey, family)
                self._set_item(self.de, i, ok, why)
            self.de.setCurrentIndex(self.de.findData(de))
            for i in range(self.server.count()):
                ok, why = desktops.server_state(de, self.server.itemData(i))
                self._set_item(self.server, i, ok, why)
            self.server.setCurrentIndex(self.server.findData(server))
            for i in range(self.comp.count()):
                ok, why = desktops.compositor_state(de, server, self.comp.itemData(i))
                self._set_item(self.comp, i, ok, why)
            self.comp.setCurrentIndex(self.comp.findData(comp))
            d = desktops.DESKTOPS[de]
            native = " (this desktop's own)" if desktops.is_native(de, comp) else ""
            self.desk_note.setText(
                (d.note + "  " if d.note else "") + f"Using {desktops.COMPOSITORS[comp].label}{native}. "
                "Greyed-out choices cannot work with the current selection - hover one to see why.")
        finally:
            self._syncing = False

    # -- storage ---------------------------------------------------------------------------------
    def _fill_drives(self, select: str | None = None) -> None:
        keep = select or self._storage_root()
        self.drive.blockSignals(True)
        self.drive.clear()
        home_root = paths.default_storage_root()
        hd = storage.drive_for_path(Path.home())
        home_label = f"Home folder  ({home_root})"
        if hd:
            home_label += f"   {storage.fmt_size(hd.free)} free"
        self.drive.addItem(home_label, str(home_root))
        for d in storage.list_drives():
            if d.mount == "/" or (hd and d.mount == hd.mount):
                continue
            self.drive.addItem(d.label, str(d.root_dir))
            i = self.drive.count() - 1
            self._set_item(self.drive, i, d.ok, d.reason)
        for root in self._custom_roots:
            self.drive.addItem(f"Folder  {root}", root)
        idx = self.drive.findData(keep) if keep else 0
        self.drive.setCurrentIndex(max(idx, 0))
        self.drive.blockSignals(False)
        self._storage_changed()

    def _storage_root(self) -> str:
        return (self.drive.currentData() or "") if hasattr(self, "drive") else ""

    def _browse_storage(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder to keep environments in", self._storage_root() or str(Path.home()))
        if not d:
            return
        ok, why = storage.validate_root(d)
        if not ok:
            QMessageBox.warning(self, "Cannot store environments there", why)
            return
        if self.drive.findData(d) < 0 and d not in self._custom_roots:
            self._custom_roots.append(d)
        self._fill_drives(select=d)

    def _storage_changed(self) -> None:
        if not hasattr(self, "drive_info"):
            return
        persistent = self.persist.isChecked()
        for w in (self.drive_row, self.ws_row):
            w.setVisible(persistent)
            lab = self.form.labelForField(w)
            if lab:
                lab.setVisible(persistent)
        if not persistent:
            self.drive_info.setText("One-time environment: nothing is kept. Bring files in on the Files tab and "
                                    "Export what you want to keep before you stop it - stopping discards everything.")
            return
        root = self._storage_root()
        ok, why = storage.validate_root(root) if root else (False, "No folder chosen")
        d = storage.drive_for_path(root) if root else None
        if not ok:
            self.drive_info.setText(f"Cannot use this location: {why}")
        else:
            free = f"{storage.fmt_size(d.free)} free on {d.mount} ({d.fstype})" if d else ""
            shaky = storage.unmounted_warning(root)
            self.drive_info.setStyleSheet(f"color:{theme.WARN if shaky else theme.MUTED};")
            if shaky:
                self.drive_info.setText(shaky)
                return
            self.drive_info.setText(
                f"Saved in {root}/<name>. {free}. System files are copied there once (about 1-6 GB, more with a big "
                "desktop), so a full system drive is not a problem.")

    def _unique_name(self, base: str) -> str:
        name, i = base, 2
        while self.ctx.store.get(name):
            name = f"{base} ({i})"
            i += 1
        return name

    def _browse_ws(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Workspace folder", self.workspace.text() or str(Path.home()))
        if d:
            self.workspace.setText(d)

    def _build_spec(self, quiet: bool = False) -> EnvSpec | None:
        """The environment described by the form, or None (after telling the user why, unless quiet)."""
        def bad(title: str, text: str) -> None:
            if not quiet:
                QMessageBox.warning(self, title, text)

        key = self.distro.currentData()
        if key == distros.CUSTOM_KEY:
            image = self.custom_image.text().strip()
            if not image:
                bad("Custom image", "Enter an image name.")
                return None
            d = distros.custom(image, self.custom_family.currentText())
        else:
            d = distros.by_key(key)
        assert d is not None
        name = self.name.text().strip()
        slug = slugify(name)
        if not name:
            bad("Name", "Give the environment a name.")
            return None
        if self.ctx.store.get(name) or self.ctx.store.slug_taken(slug):
            bad("Name in use", f"An environment called '{name}' already exists.")
            return None
        try:
            pkgs = toolkits.clean_packages(self.extra_pkgs.text())
        except ValueError as e:
            bad("Extra packages", str(e))
            return None
        persistent = self.persist.isChecked()
        root = self._storage_root() if persistent else ""
        if persistent:
            ok, why = storage.validate_root(root)
            if not ok:
                bad("Storage location", f"Cannot keep the environment there:\n{why}")
                return None
            ws = self.workspace.text().strip() or str(Path(root) / slug / "workspace")
        else:
            ws = ""
        de, server, comp = desktops.normalize(self.de.currentData(), self.server.currentData(),
                                              self.comp.currentData(), d.key, d.family)
        port = hostutil.find_free_port(6080, self.ctx.store.used_ports())
        return EnvSpec(
            name=name, slug=slug, distro_key=d.key, base_image=d.image, family=d.family, workspace=ws,
            desktop=self.desktop.isChecked(), de=de, display_server=server, compositor=comp,
            storage=PERSISTENT if persistent else EPHEMERAL, storage_root=root,
            toolchain=self.toolchain.isChecked(), packaging=self.packaging.isChecked(), fuse=self.fuse.isChecked(),
            resolution=self.res.currentText().strip() or "1440x900",
            cpus=self.resources.cpus(), memory_mb=self.resources.memory_mb(),
            resource_preset=self.resources.preset_key(), port=port,
            extras=[k for k, cb in self.toolkit_boxes.items() if cb.isChecked()], extra_packages=pkgs,
            template=getattr(self, "_from_template", ""),
        )

    def _compat(self, show_ok: bool = False, proceed_button: bool = False) -> bool:
        """Runs the compatibility checks. Returns True when creation may go ahead."""
        spec = self._build_spec()
        if spec is None:
            return False
        facts = compat.gather_facts(self.ctx.client, ports=(spec.port,))
        found = compat.check_all(spec, facts)
        blocked = has_errors(found)
        problems = [f for f in found if f.level != "ok"]
        if not problems or (all(f.level in ("info", "ok") for f in found) and not show_ok):
            return True
        dlg = FindingsDialog(
            "Compatibility check",
            "These need your attention before building." if blocked else
            "Nothing blocks this, but you should know:",
            found, self, continue_text="" if blocked or not proceed_button else "Create anyway")
        dlg.exec()
        return (not blocked) and dlg.proceed

    def _create(self) -> None:
        if not self.ctx.status.ok:
            QMessageBox.warning(self, "Docker not ready",
                                f"{self.ctx.status.describe()}.\n\nPress the orange Fix button to install or repair Docker.")
            return
        spec = self._build_spec()
        if spec is None:
            return
        found = compat.check_all(spec, compat.gather_facts(self.ctx.client, ports=(spec.port,)))
        if any(f.level in ("error", "warn") for f in found):
            blocked = has_errors(found)
            dlg = FindingsDialog("Compatibility check",
                                 "Fix these first, then try again." if blocked else
                                 "You can continue, but please read these first:", found, self,
                                 continue_text="" if blocked else "Create anyway", close_text="Cancel")
            dlg.exec()
            if blocked or not dlg.proceed:
                return
        name = spec.name
        steps = env_ops.create_steps(self.ctx.client, self.ctx.store, spec)

        def done(ok: bool, _s: str) -> None:
            if ok:
                self.ctx.set_current(spec.name)
                self._name_touched = False
                self._from_template = ""
                self._distro_changed()

        if self.ctx.start_job(f"Create environment '{name}'", steps, done):
            self.ctx.set_current(name)

    # -- templates ---------------------------------------------------------------------------
    _from_template = ""

    def load_template(self, t: EnvTemplate) -> None:
        """Fill the form from a template (the user can still tweak it before pressing Create)."""
        d = t.distro()
        if d is None:
            QMessageBox.warning(self, "Template", "; ".join(t.problems()))
            return
        i = self.distro.findData(d.key)
        if i < 0:
            self.distro.setCurrentIndex(self.distro.findData(distros.CUSTOM_KEY))
            self.custom_image.setText(t.custom_image)
            self.custom_family.setCurrentText(t.family)
        else:
            self.distro.setCurrentIndex(i)
        self._name_touched = False
        self.name.setText(self._unique_name(t.name))
        self._name_touched = True
        self.desktop.setChecked(t.desktop)
        self._last_de = t.de
        for combo, val in ((self.de, t.de), (self.server, t.display_server), (self.comp, t.compositor)):
            j = combo.findData(val)
            if j >= 0:
                combo.setCurrentIndex(j)
        self._sync_desktop()
        self.toolchain.setChecked(t.toolchain)
        self.packaging.setChecked(t.packaging)
        self.fuse.setChecked(t.fuse)
        self.res.setCurrentText(t.resolution)
        self.persist.setChecked(t.persistent)
        for k, cb in self.toolkit_boxes.items():
            cb.setChecked(k in t.extras)
        self.extra_pkgs.setText(t.extra_packages)
        self.resources.set_values(t.cpus, t.memory_mb, t.resource_preset)
        self._from_template = t.name
        self._storage_changed()

    def _use_template_named(self, name: str) -> None:
        t = self.ctx.templates.get(name)
        if t:
            self.load_template(t)

    def _save_template(self) -> None:
        spec = self._build_spec(quiet=True) or self._spec_ignoring_name()
        if spec is None:
            return
        name, ok = QInputDialog.getText(self, "Save as template", "Template name (e.g. Ubuntu-24.04-KDE-Qt6):",
                                        text=self.name.text().strip().replace(" ", "-"))
        if not ok or not name.strip():
            return
        desc, _ok = QInputDialog.getText(self, "Save as template", "Short description (optional):")
        t = EnvTemplate.from_spec(spec, name.strip(), desc.strip())
        try:
            try:
                self.ctx.templates.save(t)
            except FileExistsError:
                if QMessageBox.question(self, "Template exists", f"Replace the template '{t.name}'?"
                                        ) != QMessageBox.StandardButton.Yes:
                    return
                self.ctx.templates.save(t, overwrite=True)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Save as template", str(e))
            return
        self.ctx.templatesChanged.emit()
        QMessageBox.information(self, "Template saved", f"'{t.name}' is on the Templates tab.")

    def _spec_ignoring_name(self) -> EnvSpec | None:
        """A spec for saving as template even when the name is taken or empty."""
        saved = self.name.text()
        self.name.setText(f"_template_{len(self.ctx.store.all())}")
        try:
            return self._build_spec(quiet=True)
        finally:
            self.name.setText(saved)

    # -- clone / health ------------------------------------------------------------------------
    def _resources(self, env: EnvSpec) -> None:
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox

        from ...docker import resource_limits as rl
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Resources of '{env.name}'")
        lay = QVBoxLayout(dlg)
        panel = ResourcePanel(parent=dlg)
        panel.set_values(env.cpus, env.memory_mb, env.resource_preset)
        lay.addWidget(panel)
        note = QLabel("Applied immediately to a running environment (no restart needed). CPU is a share of "
                      "processing time, not reserved cores.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{theme.MUTED};")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        env.cpus, env.memory_mb, env.resource_preset = panel.cpus(), panel.memory_mb(), panel.preset_key()
        self.ctx.store.put(env)
        if self.ctx.state_of(env) not in ("missing", "unknown"):
            argv = self.ctx.client.argv(*rl.update_args(env.container, rl.Limits(env.cpus, env.memory_mb)))
            from ...runner import Step
            self.ctx.start_job(f"Apply resource limits to '{env.name}'", [Step("docker update", argv=argv)])
        self.ctx.envsChanged.emit()

    def _clone(self, env: EnvSpec) -> None:
        name, ok = QInputDialog.getText(self, "Clone environment", f"Name for the copy of '{env.name}':",
                                        text=self._unique_name(env.name + " copy"))
        if not ok or not name.strip():
            return
        root = None
        if env.persistent:
            box = QMessageBox(self)
            box.setWindowTitle("Clone environment")
            box.setText("Where should the copy's saved data go?")
            same = box.addButton("Same drive", QMessageBox.ButtonRole.AcceptRole)
            other = box.addButton("Choose a folder...", QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is other:
                root = QFileDialog.getExistingDirectory(self, "Folder for the clone", env.storage_root or str(Path.home()))
                if not root:
                    return
            elif box.clickedButton() is not same:
                return
        try:
            dst = env_ops.clone_spec(self.ctx.store, env, name, root)
        except ValueError as e:
            QMessageBox.warning(self, "Clone environment", str(e))
            return
        self.ctx.start_job(f"Clone '{env.name}' to '{dst.name}'",
                           env_ops.clone_steps(self.ctx.client, self.ctx.store, env, dst))

    def _health(self, env: EnvSpec) -> None:
        found = health.diagnose(health.DockerProbe(self.ctx.client, env.container), env)
        dlg = FindingsDialog(f"Health of '{env.name}'", "", found, self)
        dlg.exec()
        k = dlg.chosen_fix
        c, st = self.ctx.client, self.ctx.store
        if k == "start":
            self.ctx.start_job(f"Start '{env.name}'", env_ops.start_steps(c, env))
        elif k == "restart":
            self.ctx.start_job(f"Restart '{env.name}'", env_ops.restart_steps(c, env))
        elif k == "restart-desktop":
            self.ctx.start_job(f"Restart desktop of '{env.name}'", env_ops.start_desktop_steps(c, env, "restart"))
        elif k in ("rebuild", "fix"):
            self.ctx.start_job(f"Fix '{env.name}'", fixer.fix_steps(c, st, env))

    # -- table -------------------------------------------------------------------------
    def refresh(self) -> None:
        envs = self.ctx.store.all()
        self.table.blockSignals(True)
        self.table.setRowCount(len(envs))
        sel_row = -1
        for r, e in enumerate(envs):
            state = self.ctx.state_of(e)
            d = distros.by_key(e.distro_key)
            vals = [e.name, d.label if d else e.base_image, state,
                    desktops.describe(e.de, e.display_server, e.compositor) if e.desktop else "-",
                    self._storage_text(e)]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 2:
                    it.setForeground(QColor(STATE_COLORS.get(state, theme.MUTED)))
                self.table.setItem(r, c, it)
            if e.name == self.ctx.current_name:
                sel_row = r
        if sel_row >= 0:
            self.table.selectRow(sel_row)
        self.table.blockSignals(False)
        self._update_buttons()

    @staticmethod
    def _storage_text(e: EnvSpec) -> str:
        if e.persistent:
            dr = storage.drive_for_path(e.storage_root) if e.storage_root else None
            return f"Persistent - {dr.mount if dr else e.storage_root}"
        if e.ephemeral:
            return "One-time"
        return "Docker-managed"

    def _selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.ctx.set_current(self.table.item(rows[0].row(), 0).text())
        self._update_buttons()

    def _update_buttons(self) -> None:
        env = self.ctx.current_env()
        busy = self.ctx.busy
        docker_ok = self.ctx.status.ok
        state = self.ctx.state_of(env)
        have = env is not None
        running = state == "running"
        self.create_btn.setEnabled(not busy)
        en = {
            "start": have and docker_ok and not busy and not running,
            "stop": have and docker_ok and not busy and running,
            "restart": have and docker_ok and not busy and running and not env.ephemeral,
            "terminal": have and docker_ok and running,
            "folder": have and not env.ephemeral,
            "snapshot": have and docker_ok and not busy and state != "missing" and not env.persistent,
            "restore": have and docker_ok and not busy and not env.persistent,
            "rebuild": have and docker_ok and not busy,
            "resources": have and not busy,
            "clone": have and docker_ok and not busy and state != "missing" and not env.ephemeral,
            "health": have and docker_ok and not busy,
            "fix": have and not busy,
            "delete": have and docker_ok and not busy,
        }
        for k, b in self.buttons.items():
            b.setEnabled(bool(en[k]))
            b.setToolTip("")
        if have and env.persistent:
            self.buttons["snapshot"].setToolTip("Not needed: this environment's files already live on its drive")
            self.buttons["restore"].setToolTip("Not needed: this environment's files already live on its drive")
        if have and env.ephemeral:
            self.buttons["restart"].setToolTip("A one-time environment is discarded when it stops")
            self.buttons["folder"].setToolTip("A one-time environment has no folder on this computer")
        if not have:
            self.hint.setText("Pick a Linux, then press Create environment. ContainerQT Studio builds the image, starts it and "
                              "gives you a desktop, a terminal and a build window - no further setup.")
        elif not docker_ok:
            self.hint.setText("Docker is not available right now - press the orange Fix button.")
        elif env.persistent:
            self.hint.setText(f"Persistent - saved in {env.data_dir}. Projects folder: {env.workspace}  ->  "
                              "/workspace inside the environment.")
        elif env.ephemeral:
            self.hint.setText("One-time environment: nothing is kept after Stop. Use the Files tab to bring files in "
                              "and Export what you want to keep.")
        else:
            self.hint.setText(f"Workspace on this computer: {env.workspace}  ->  /workspace inside the environment.")

    # -- actions --------------------------------------------------------------------------
    def _action(self, key: str) -> None:
        env = self.ctx.current_env()
        if not env:
            return
        c, st = self.ctx.client, self.ctx.store
        if key == "start":
            self.ctx.start_job(f"Start '{env.name}'", env_ops.start_steps(c, env))
        elif key == "stop":
            if env.ephemeral and QMessageBox.question(
                self, "Stop one-time environment",
                f"'{env.name}' is a one-time environment.\n\nStopping it discards everything inside it. Export "
                "anything you want to keep first.\n\nStop and discard?",
            ) != QMessageBox.StandardButton.Yes:
                return
            self.ctx.start_job(f"Stop '{env.name}'", env_ops.stop_steps(c, env))
        elif key == "restart":
            self.ctx.start_job(f"Restart '{env.name}'", env_ops.restart_steps(c, env))
        elif key == "terminal":
            hostutil.launch_terminal(c.exec_argv(env.container, "bash", "-l", workdir="/workspace", interactive=True))
        elif key == "folder":
            if env.ephemeral:
                return
            Path(env.workspace).mkdir(parents=True, exist_ok=True)
            hostutil.open_path(env.workspace)
        elif key == "snapshot":
            self.ctx.start_job(f"Snapshot '{env.name}'", env_ops.snapshot_steps(c, env))
        elif key == "restore":
            self._restore(env)
        elif key == "rebuild":
            if env.persistent:
                text = (f"Rebuild '{env.name}'?\n\nIts system files on the drive (installed packages, /usr, /etc, /var, "
                        "/opt) are replaced by a fresh copy. Your home folder and projects are kept.")
            elif env.ephemeral:
                text = f"Rebuild '{env.name}'? The running session is discarded."
            else:
                text = (f"Rebuild '{env.name}' from scratch?\n\nSoftware installed inside the container (outside "
                        "/workspace) is lost; your workspace folder is untouched. Take a snapshot first to keep it.")
            if QMessageBox.question(self, "Rebuild environment", text) == QMessageBox.StandardButton.Yes:
                self.ctx.start_job(f"Rebuild '{env.name}'",
                                   env_ops.create_steps(c, st, env, rebuild=env.persistent))
        elif key == "resources":
            self._resources(env)
        elif key == "clone":
            self._clone(env)
        elif key == "health":
            self._health(env)
        elif key == "fix":
            self.ctx.start_job(f"Fix '{env.name}'", fixer.fix_steps(c, st, env))
        elif key == "delete":
            self._delete(env)

    def _delete(self, env: EnvSpec) -> None:
        c, st = self.ctx.client, self.ctx.store
        if env.persistent:
            box = QMessageBox(self)
            box.setWindowTitle("Delete environment")
            box.setText(f"Delete '{env.name}'?")
            box.setInformativeText(f"Its saved data is in {env.data_dir}.\n\nKeep the data to free only the "
                                   "container/image, or delete it too to free the drive space.")
            keep = box.addButton("Delete, keep saved data", QMessageBox.ButtonRole.AcceptRole)
            purge = box.addButton("Delete everything", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked not in (keep, purge):
                return
            self.ctx.start_job(f"Delete '{env.name}'", env_ops.delete_steps(c, st, env, purge_data=clicked is purge))
            return
        detail = ("Anything inside it is lost." if env.ephemeral
                  else f"The workspace folder {env.workspace} is kept.")
        if QMessageBox.question(self, "Delete environment",
                                f"Delete '{env.name}' (container and image)?\n\n{detail}",
                                ) == QMessageBox.StandardButton.Yes:
            self.ctx.start_job(f"Delete '{env.name}'", env_ops.delete_steps(c, st, env))

    def _restore(self, env: EnvSpec) -> None:
        tags = self.ctx.client.list_images(f"dockerqt/{env.slug}")
        if not tags:
            QMessageBox.information(self, "Restore", "No snapshots or images found for this environment.")
            return
        tag, ok = QInputDialog.getItem(self, "Restore snapshot", "Image to restore (replaces the container):",
                                       tags, 0, False)
        if ok and tag:
            self.ctx.start_job(f"Restore '{env.name}' from {tag}", env_ops.restore_steps(self.ctx.client,
                                                                                         self.ctx.store, env, tag))
