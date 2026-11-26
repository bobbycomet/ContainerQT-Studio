"""Environment health checks: detect a broken desktop session or missing components.

`diagnose(probe, env)` is pure logic over a `Probe` (anything with `container_state()` and `sh(script)`),
so it is testable without Docker. `DockerProbe` is the real implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from . import desktops, storage
from .findings import ERROR, OK, WARN, Finding, sort_findings


class Probe(Protocol):
    def container_state(self) -> str: ...      # running | exited | missing | ...
    def sh(self, script: str) -> tuple[int, str]: ...


@dataclass
class DockerProbe:
    client: object
    name: str

    def container_state(self) -> str:
        return self.client.container_state(self.name) or "missing"

    def sh(self, script: str) -> tuple[int, str]:
        rc, out, err = self.client.run("exec", self.name, "sh", "-c", script, timeout=25)
        return rc, (out or err or "").strip()


def _has(probe: Probe, cmd: str) -> bool:
    return probe.sh(f"command -v {cmd} >/dev/null 2>&1")[0] == 0


def _running(probe: Probe, proc: str) -> bool:
    return probe.sh(f"pgrep -x {proc} >/dev/null 2>&1 || pgrep -f {proc} >/dev/null 2>&1")[0] == 0


def diagnose(probe: Probe, env) -> list[Finding]:
    out: list[Finding] = []
    # persistent storage first: a missing drive is the nastiest mistake
    if getattr(env, "persistent", False):
        ok, why = storage.preflight(env)
        if not ok:
            out.append(Finding(ERROR, "drive-missing", "The storage drive is not available", why))
            return out
    state = probe.container_state()
    if state == "missing":
        return [Finding(ERROR, "container-missing", "The container no longer exists",
                        "Your files are safe if this environment is persistent.", "fix", "Recreate it")]
    if state != "running":
        return [Finding(WARN, "stopped", f"The environment is {state}", "", "start", "Start it")]
    out.append(Finding(OK, "running", "The container is running"))
    if probe.sh("true")[0] != 0:
        out.append(Finding(ERROR, "no-exec", "Cannot run commands inside the container", "", "restart", "Restart"))
        return sort_findings(out)
    if probe.sh("getent hosts localhost >/dev/null 2>&1 || grep -q localhost /etc/hosts")[0] != 0:
        out.append(Finding(WARN, "hosts", "/etc/hosts is missing localhost", "", "fix", "Repair"))
    if probe.sh("[ -s /etc/resolv.conf ]")[0] != 0:
        out.append(Finding(WARN, "dns", "No DNS configuration", "Package installs will fail.", "restart", "Restart"))
    if getattr(env, "toolchain", False) and not _has(probe, "gcc") and not _has(probe, "cc"):
        out.append(Finding(WARN, "no-compiler", "No C compiler found", "The toolchain did not install fully.",
                           "rebuild", "Rebuild"))
    if getattr(env, "desktop", False):
        de = desktops.DESKTOPS.get(env.de)
        if probe.sh("[ -x /usr/bin/Xvnc ] || command -v Xvnc >/dev/null 2>&1 || command -v x11vnc >/dev/null 2>&1 "
                    "|| command -v wayvnc >/dev/null 2>&1")[0] != 0:
            out.append(Finding(ERROR, "no-vnc", "The desktop display server is not installed",
                               "Xvnc/x11vnc/wayvnc is missing.", "rebuild", "Rebuild"))
        if not _has(probe, "websockify") and probe.sh("ls /usr/share/novnc >/dev/null 2>&1")[0] != 0:
            out.append(Finding(ERROR, "no-novnc", "noVNC is missing", "The desktop cannot be shown.", "rebuild", "Rebuild"))
        elif not _running(probe, "websockify"):
            out.append(Finding(WARN, "novnc-stopped", "The desktop web bridge is not running", "",
                               "restart-desktop", "Restart desktop"))
        bins = list(de.session_bins) if de else []
        exe = bins[0] if bins else ""
        if bins and not any(_has(probe, b) for b in bins):
            out.append(Finding(ERROR, "no-session", f"{de.label} is not installed ({' / '.join(bins)} not found)", "",
                               "rebuild", "Rebuild"))
        elif exe and not any(_running(probe, b) for b in bins):
            out.append(Finding(WARN, "session-stopped", f"The {de.label} session is not running", "",
                               "restart-desktop", "Restart desktop"))
        if probe.sh("ls /tmp/.X11-unix/X* >/dev/null 2>&1")[0] != 0 and getattr(env, "display_server", "x11") == "x11":
            out.append(Finding(WARN, "no-x", "No X display socket", "The X server is not running.",
                               "restart-desktop", "Restart desktop"))
    rc, df = probe.sh("df -P / | awk 'NR==2{print $5}' | tr -d '%'")
    if rc == 0 and df.isdigit() and int(df) >= 95:
        out.append(Finding(WARN, "disk-full", f"The container disk is {df}% full", "Builds and installs may fail.", "", ""))
    if len(out) == 1:
        out.append(Finding(OK, "healthy", "No problems found"))
    return sort_findings(out)
