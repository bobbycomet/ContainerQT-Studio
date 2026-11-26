"""Environment resources: presets, CPU and memory sliders with host-aware advice.

All numbers come from `hardware.resource_advisor`; this widget only displays them and reports the user's choice.
The advice refreshes in the background but NEVER moves the user's sliders or removes choices - allocations above the
recommendation stay selectable and are merely flagged.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout,
                             QWidget)
from PyQt6.QtCore import Qt

from . import theme
from ..hardware import resource_advisor as ra
from ..hardware.resource_advisor import MB


def read_host() -> ra.HostResources:
    from ..hardware import cpu, memory

    return ra.HostResources.from_parts(cpu.read_cpu_info(), cpu.read_cpu_load(0.1), memory.read_memory())


class ResourcePanel(QGroupBox):
    changed = pyqtSignal()

    def __init__(self, host_provider: Callable[[], ra.HostResources] = read_host, auto_refresh: bool = True,
                 parent=None) -> None:
        super().__init__("Environment resources", parent)
        self._provider = host_provider
        self._desktop_min = 0
        self._busy = False
        self.advice: ra.Advice | None = None
        self._mem_steps: list[int] = []

        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)

        self.preset = QComboBox()
        self.preset.activated.connect(self._preset_chosen)
        form.addRow("Preset", self.preset)

        self.cpu_limit = QCheckBox("Limit CPU")
        self.cpu_limit.setChecked(True)
        self.cpu_slider = QSlider(Qt.Orientation.Horizontal)
        self.cpu_slider.setSingleStep(1)
        self.cpu_label = QLabel("")
        cpu_row = QWidget()
        h = QHBoxLayout(cpu_row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.cpu_slider, 1)
        h.addWidget(self.cpu_label)
        form.addRow(self.cpu_limit, cpu_row)
        self.cpu_info = self._muted()
        form.addRow("", self.cpu_info)

        self.mem_limit = QCheckBox("Limit memory")
        self.mem_limit.setChecked(True)
        self.mem_slider = QSlider(Qt.Orientation.Horizontal)
        self.mem_label = QLabel("")
        mem_row = QWidget()
        h = QHBoxLayout(mem_row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.mem_slider, 1)
        h.addWidget(self.mem_label)
        form.addRow(self.mem_limit, mem_row)
        self.mem_info = self._muted()
        form.addRow("", self.mem_info)

        self.reserve = self._muted()
        form.addRow("Host reserve", self.reserve)
        self.advisory = QLabel("")
        self.advisory.setWordWrap(True)
        form.addRow("", self.advisory)
        self.note = self._muted()
        self.note.setText("CPU limits are a quota, not a reservation: allocating 8 CPUs does not set aside 8 cores, "
                          "the environment just cannot use more than 8 threads' worth at once. ContainerQT Studio advises; "
                          "you decide.")
        lay.addWidget(self.note)

        for w in (self.cpu_limit, self.mem_limit):
            w.toggled.connect(self._edited)
        self.cpu_slider.valueChanged.connect(self._edited)
        self.mem_slider.valueChanged.connect(self._edited)
        self.refresh_host()
        self.apply_preset("development")
        if auto_refresh:
            self._timer = QTimer(self)
            self._timer.setInterval(5000)
            self._timer.timeout.connect(self.refresh_host)
            self._timer.start()

    @staticmethod
    def _muted() -> QLabel:
        lb = QLabel("")
        lb.setWordWrap(True)
        lb.setStyleSheet(f"color:{theme.MUTED};")
        return lb

    # -- host data --------------------------------------------------------------------------
    def set_desktop_min(self, mb: int) -> None:
        self._desktop_min = mb
        self._update_text()

    def refresh_host(self) -> None:
        """Re-read the machine. Keeps the user's chosen values; only the advice and the slider ranges change."""
        try:
            host = self._provider()
        except Exception:  # noqa: BLE001
            return
        cpus, mem = self.cpus(), self.memory_mb()
        self.advice = ra.advise(host, self._desktop_min)
        a = self.advice
        self._mem_steps = [int(o.value) for o in a.mem_options]
        if a.mem_absolute_max_mb > self._mem_steps[-1]:
            self._mem_steps.append(a.mem_absolute_max_mb)
        self._busy = True
        try:
            self.cpu_slider.setRange(1, max(1, a.cpu_absolute_max))
            self.mem_slider.setRange(0, len(self._mem_steps) - 1)
            self.preset.blockSignals(True)
            cur = self.preset.currentData()
            self.preset.clear()
            for key in ra.PRESETS:
                if key == "custom":
                    self.preset.addItem("Custom", key)
                else:
                    p = a.presets[key]
                    self.preset.addItem(f"{p.label}  -  {p.cpus} CPU, {mem_text(p.memory_mb)}", key)
            self.preset.setCurrentIndex(max(0, self.preset.findData(cur or "custom")))
            self.preset.blockSignals(False)
            self._set_cpu(cpus)
            self._set_mem(mem)
        finally:
            self._busy = False
        self._update_text()

    # -- values ------------------------------------------------------------------------------------
    def cpus(self) -> float:
        return float(self.cpu_slider.value()) if self.cpu_limit.isChecked() else 0.0

    def memory_mb(self) -> int:
        if not self.mem_limit.isChecked() or not self._mem_steps:
            return 0
        return self._mem_steps[self.mem_slider.value()]

    def preset_key(self) -> str:
        return self.preset.currentData() or "custom"

    def set_values(self, cpus: float, memory_mb: int, preset: str = "custom") -> None:
        self._busy = True
        try:
            self.cpu_limit.setChecked(cpus > 0)
            self.mem_limit.setChecked(memory_mb > 0)
            if cpus > 0:
                self._set_cpu(cpus)
            if memory_mb > 0:
                self._set_mem(memory_mb)
            self.preset.setCurrentIndex(max(0, self.preset.findData(preset if preset in ra.PRESETS else "custom")))
        finally:
            self._busy = False
        self._update_text()
        self.changed.emit()

    def apply_preset(self, key: str) -> None:
        if key == "custom" or not self.advice or key not in self.advice.presets:
            return
        p = self.advice.presets[key]
        self.set_values(p.cpus, p.memory_mb, key)

    def _preset_chosen(self, _i: int) -> None:
        self.apply_preset(self.preset_key())

    def _set_cpu(self, cpus: float) -> None:
        if cpus > 0:
            self.cpu_slider.setValue(int(min(max(1, round(cpus)), self.cpu_slider.maximum())))

    def _set_mem(self, mb: int) -> None:
        if mb > 0 and self._mem_steps:
            i = min(range(len(self._mem_steps)), key=lambda k: abs(self._mem_steps[k] - mb))
            self.mem_slider.setValue(i)

    def _edited(self) -> None:
        if self._busy:
            return
        self.preset.setCurrentIndex(self.preset.findData("custom"))
        self._update_text()
        self.changed.emit()

    # -- text ---------------------------------------------------------------------------------------
    def _update_text(self) -> None:
        a = self.advice
        if not a:
            return
        h = a.host
        self.cpu_slider.setEnabled(self.cpu_limit.isChecked())
        self.mem_slider.setEnabled(self.mem_limit.isChecked())
        cp = self.cpus()
        self.cpu_label.setText((f"{cp:g} thread" + ("" if cp == 1 else "s")) if cp else "no limit")
        tier = next((o for o in a.cpu_options if o.value == cp), None)
        phys = f"{h.physical_cores} physical cores  -  {h.logical_threads} logical threads detected"
        rec = f"Recommended up to {a.cpu_recommended_max}, absolute maximum {a.cpu_absolute_max}."
        self.cpu_info.setText(f"{phys}.  {rec}" + (f"  {tier.label}." if tier else ""))
        mb = self.memory_mb()
        self.mem_label.setText(mem_text(mb) if mb else "no limit")
        mt = next((o for o in a.mem_options if o.value == mb), None)
        self.mem_info.setText(
            f"{mem_text(h.mem_total_mb)} installed  -  {mem_text(h.mem_available_mb)} available now.  "
            f"Recommended up to {mem_text(a.mem_recommended_max_mb)}, absolute maximum {mem_text(a.mem_absolute_max_mb)}."
            + (f"  {mt.label}." if mt else ""))
        self.reserve.setText(f"automatically calculated: {a.reserve_threads} thread{"" if a.reserve_threads == 1 else "s"} and {mem_text(a.reserve_mb)} "
                             "stay free for the host")
        msgs = [t for _lv, t in ra.assess(h, cp, mb, self._desktop_min)] + list(a.notes)
        if not cp or not mb:
            msgs.insert(0, "Without a limit the environment can use as much as the machine has.")
        self.advisory.setText("<br>".join(msgs))
        self.advisory.setStyleSheet(f"color:{theme.WARN};" if msgs else "")
        self.advisory.setVisible(bool(msgs))


def mem_text(mb: float) -> str:
    gb = mb / MB
    if gb < 1:
        return f"{int(mb)} MB"
    return f"{gb:.1f}".rstrip("0").rstrip(".") + " GB"
