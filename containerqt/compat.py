"""Host / container compatibility checks: catch missing dependencies and unsupported configurations *before* a long build.

`check_host()` and `check_spec()` are pure functions over a `HostFacts` snapshot (so they are unit-testable);
`gather_facts()` collects the snapshot from the real machine.

Errors block creating an environment; warnings explain a risk and let the user continue (ContainerQT advises, the user decides).
"""
from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import desktops, distros, storage, toolkits
from .findings import ERROR, INFO, OK, WARN, Finding, sort_findings
from .hardware import resource_advisor as ra
from .models import EnvSpec

GB = 1024 ** 3
# rough image growth (GB) beyond the base system, used for the disk-space estimate
DE_GB = {"xfce": 1.5, "kde": 5.0, "gnome": 4.0, "cinnamon": 3.0, "mate": 2.5, "lxqt": 1.5, "i3": 0.6,
         "openbox": 0.5, "sway": 0.8, "labwc": 0.8}


@dataclass
class HostFacts:
    arch: str = "x86_64"
    docker_path: str = ""
    docker_info: dict = field(default_factory=dict)
    docker_root_free: int | None = None         # bytes free where Docker keeps images/layers
    has_dev_fuse: bool = True
    home: str = ""
    webengine: bool = True
    selinux: bool = False
    cgroup_v2: bool = True
    resources: ra.HostResources | None = None
    port_free: dict = field(default_factory=dict)   # port -> bool, filled for ports asked about

    # -- derived from `docker info` ---------------------------------------------------------------
    @property
    def security_options(self) -> list[str]:
        return [str(x) for x in self.docker_info.get("SecurityOptions") or []]

    @property
    def rootless(self) -> bool:
        return any("rootless" in o for o in self.security_options)

    @property
    def userns(self) -> bool:
        return any("userns" in o for o in self.security_options)

    @property
    def docker_desktop(self) -> bool:
        return "docker desktop" in str(self.docker_info.get("OperatingSystem", "")).lower()

    @property
    def podman(self) -> bool:
        blob = (str(self.docker_info.get("ServerVersion", "")) + str(self.docker_info.get("Name", ""))
                + str(self.docker_info.get("OperatingSystem", ""))).lower()
        return "podman" in blob or "podman" in self.docker_path.lower()

    @property
    def snap_docker(self) -> bool:
        return self.docker_path.startswith("/snap/") or "/snap/bin/" in self.docker_path

    @property
    def docker_warnings(self) -> list[str]:
        return [str(w) for w in self.docker_info.get("Warnings") or []]


def gather_facts(client=None, ports: tuple[int, ...] = ()) -> HostFacts:
    from . import hostutil
    from .hardware import cpu as hw_cpu, memory as hw_mem

    f = HostFacts(arch=platform.machine() or "x86_64", docker_path=shutil.which("docker") or "",
                  has_dev_fuse=os.path.exists("/dev/fuse"), home=str(Path.home()),
                  selinux=os.path.exists("/sys/fs/selinux/enforce"),
                  cgroup_v2=os.path.exists("/sys/fs/cgroup/cgroup.controllers"))
    try:
        from PyQt6 import QtWebEngineWidgets  # noqa: F401
        f.webengine = True
    except Exception:  # noqa: BLE001
        f.webengine = False
    if client is not None:
        f.docker_info = client.info()
    root = f.docker_info.get("DockerRootDir") or "/var/lib/docker"
    probe = Path(root)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        f.docker_root_free = shutil.disk_usage(probe).free
    except OSError:
        f.docker_root_free = None
    f.resources = ra.HostResources.from_parts(hw_cpu.read_cpu_info(), hw_cpu.read_cpu_load(0.1), hw_mem.read_memory())
    for p in ports:
        f.port_free[p] = hostutil.port_is_free(p)
    return f


# ------------------------------------------------------------------------------------------------ host
def check_host(f: HostFacts) -> list[Finding]:
    out: list[Finding] = []
    if not f.docker_path:
        out.append(Finding(ERROR, "docker-missing", "Docker is not installed",
                           "ContainerQT Studio needs Docker Engine.", "fix", "Install Docker"))
        return out
    if not f.docker_info:
        out.append(Finding(WARN, "docker-info", "Could not read Docker's configuration",
                           "The daemon may be stopped or you may lack permission.", "fix", "Fix Docker"))
    if f.podman:
        out.append(Finding(WARN, "podman", "This looks like Podman, not Docker",
                           "ContainerQT Studio is built and tested for Docker Engine; Podman's docker shim can differ "
                           "(volumes, --init, networking)."))
    if f.arch not in ("x86_64", "aarch64"):
        out.append(Finding(WARN, "arch", f"Unusual CPU architecture ({f.arch})",
                           "Most distro images and desktop packages are only published for x86_64 and aarch64."))
    elif f.arch == "aarch64":
        out.append(Finding(INFO, "arch-arm", "ARM host (aarch64)",
                           "Environments run natively for ARM. Build scripts that download x86_64 tools "
                           "(linuxdeploy, appimagetool) fetch the aarch64 builds, and some packages are x86_64-only."))
    if f.snap_docker:
        out.append(Finding(WARN, "snap-docker", "Docker was installed as a snap",
                           "Snap Docker can only see your home folder, so persistent environments on another drive "
                           "(/mnt, /media) will fail to mount. Use Docker from your distribution or docker.com."))
    if f.docker_desktop:
        out.append(Finding(WARN, "docker-desktop", "Docker Desktop detected",
                           "It runs Docker in a VM: other drives must be added under Settings > Resources > File "
                           "sharing, bind mounts are slower, and the container desktop is reached through the VM."))
    if f.rootless:
        out.append(Finding(INFO, "rootless", "Rootless Docker",
                           "Saved environment files are owned by your own user, which is fine; ports below 1024 and "
                           "some mounts (FUSE) are restricted."))
    if f.userns:
        out.append(Finding(WARN, "userns-remap", "Docker user-namespace remapping is on",
                           "Persistent environments store files owned by a remapped user id, so they can look "
                           "unreadable in your file manager and mounts of your own folders may be denied."))
    for w in f.docker_warnings:
        wl = w.lower()
        if "memory limit" in wl or "swap limit" in wl or "cpu cfs" in wl or "cpuset" in wl:
            out.append(Finding(WARN, "cgroup-limits", "This kernel cannot enforce some resource limits",
                               w.replace("WARNING: ", "") + " - CPU/memory limits you set may be ignored."))
    if f.selinux:
        out.append(Finding(INFO, "selinux", "SELinux is present", "ContainerQT Studio labels its mounts (:z) automatically."))
    if not f.webengine:
        out.append(Finding(INFO, "no-webengine", "The embedded desktop view is unavailable",
                           "PyQt6-WebEngine is not installed, so the desktop opens in your browser instead "
                           "(the AppImage includes it)."))
    if f.docker_root_free is not None and f.docker_root_free < 5 * GB:
        out.append(Finding(ERROR if f.docker_root_free < 2 * GB else WARN, "docker-disk",
                           f"Only {storage.fmt_size(f.docker_root_free)} free where Docker stores images",
                           "Building an environment needs several GB. Free space, or move Docker's data-root to a "
                           "larger drive."))
    if not out:
        out.append(Finding(OK, "host-ok", "Docker and this computer look ready"))
    return out


# -------------------------------------------------------------------------------------- an environment
def estimate_gb(spec: EnvSpec) -> float:
    g = 1.0 + (1.2 if spec.toolchain else 0) + (0.6 if spec.packaging else 0)
    if spec.desktop:
        g += DE_GB.get(spec.de, 2.0) + 0.5
    g += 0.6 * len(toolkits.clean(spec.extras))
    return g


def check_spec(spec: EnvSpec, f: HostFacts) -> list[Finding]:
    out: list[Finding] = []
    d = distros.by_key(spec.distro_key)
    label = d.label if d else spec.base_image
    if spec.desktop:
        ok, why = desktops.de_state(spec.de, spec.distro_key, spec.family)
        if not ok:
            out.append(Finding(ERROR, "de-unavailable", f"{desktops.DESKTOPS[spec.de].label} is not available on {label}", why))
        ok, why = desktops.server_state(spec.de, spec.display_server)
        if not ok:
            out.append(Finding(ERROR, "server-unavailable", "That display server cannot run this desktop", why))
        else:
            ok, why = desktops.compositor_state(spec.de, spec.display_server,
                                                spec.compositor or desktops.DESKTOPS[spec.de].native)
            if not ok:
                out.append(Finding(ERROR, "compositor-unavailable", "That compositor cannot drive this desktop", why))
        if spec.de == "kde" and (s := desktops.plasma_series(spec.distro_key)):
            out.append(Finding(INFO, "plasma-series", f"KDE Plasma {s}.x",
                               f"{label} ships the Plasma {s} series. Pick another release to get the other series."))
    if spec.family == "apk" and spec.name:
        out.append(Finding(INFO, "musl", "Alpine uses musl libc",
                           "You can build and run anything inside it, but binaries linked against musl are not "
                           "interchangeable with glibc ones: an AppImage meant for glibc-based hosts should be built "
                           "in a glibc environment (Ubuntu/Debian/Rocky)."))
    if spec.fuse and not f.has_dev_fuse:
        out.append(Finding(ERROR, "no-fuse", "/dev/fuse is missing on this computer",
                           "FUSE mounts were requested; load the fuse module (sudo modprobe fuse) or untick the option."))
    if spec.fuse and f.rootless:
        out.append(Finding(WARN, "fuse-rootless", "FUSE with rootless Docker", "It may not work; AppImages run fine "
                           "without FUSE (ContainerQT Studio sets APPIMAGE_EXTRACT_AND_RUN)."))
    if spec.persistent:
        ok, why = storage.validate_root(spec.storage_root) if spec.storage_root else (False, "No storage folder chosen")
        if not ok:
            out.append(Finding(ERROR, "storage-fs", "This drive cannot hold a persistent environment", why))
        elif storage.looks_unmounted(spec.storage_root):
            out.append(Finding(ERROR, "drive-unmounted", "That drive does not look mounted",
                               f"{spec.storage_root} is under /mnt or /media but is stored on your system drive. "
                               "Mount the drive first, otherwise the environment would silently fill the system drive "
                               "and later start empty when the drive is attached."))
        elif f.snap_docker and spec.storage_root and not str(spec.storage_root).startswith(f.home or "\0"):
            out.append(Finding(ERROR, "snap-storage", "Snap Docker cannot reach that folder",
                               "Choose a folder inside your home directory or install Docker without snap."))
        if f.docker_desktop and spec.storage_root and not str(spec.storage_root).startswith(f.home or "\0"):
            out.append(Finding(WARN, "dd-sharing", "Share this drive with Docker Desktop first",
                               f"Add {spec.storage_root} under Docker Desktop > Settings > Resources > File sharing."))
        if spec.storage_root and (w := storage.unmounted_warning(spec.storage_root)):
            out.append(Finding(ERROR, "drive-not-mounted", "That drive is not mounted", w))
        if spec.storage_root:
            drv = storage.drive_for_path(spec.storage_root)
            need = estimate_gb(spec) * GB
            if drv is not None:
                if drv.free < need:
                    out.append(Finding(ERROR, "drive-space", f"Not enough free space on {drv.mount}",
                                       f"About {need / GB:.0f} GB is needed, {storage.fmt_size(drv.free)} is free."))
                elif drv.free < 2 * need:
                    out.append(Finding(WARN, "drive-space-tight", f"Little spare room on {drv.mount}",
                                       f"The system files need about {need / GB:.0f} GB and your own installs and "
                                       f"projects will add to it; {storage.fmt_size(drv.free)} is free."))
    if f.docker_root_free is not None and f.docker_root_free < estimate_gb(spec) * GB:
        out.append(Finding(ERROR, "docker-space", "Not enough room for the image",
                           f"The image needs about {estimate_gb(spec):.0f} GB where Docker keeps its data "
                           f"({storage.fmt_size(f.docker_root_free)} free)."))
    if f.port_free.get(spec.port) is False and spec.desktop:
        out.append(Finding(ERROR, "port-busy", f"Port {spec.port} is already in use",
                           "The desktop view needs a free local port; ContainerQT Studio normally picks one automatically."))
    if f.resources is not None:
        for level, text in ra.assess(f.resources, spec.cpus, spec.memory_mb,
                                     ra.DESKTOP_MIN_MB.get(spec.de, 0) if spec.desktop else 0):
            out.append(Finding(WARN, "resources", "Resource allocation", text))
    if spec.storage_limit_gb and spec.persistent and spec.storage_limit_gb < estimate_gb(spec):
        out.append(Finding(WARN, "limit-small", "The storage budget is below the system size",
                           f"The system files alone need about {estimate_gb(spec):.0f} GB; the {spec.storage_limit_gb} GB "
                           "budget will show as exceeded straight away."))
    return sort_findings(out)


def check_all(spec: EnvSpec, f: HostFacts) -> list[Finding]:
    seen, merged = set(), []
    for x in check_host(f) + check_spec(spec, f):
        if x.code not in seen and x.code != "host-ok":
            seen.add(x.code)
            merged.append(x)
    return sort_findings(merged) or [Finding(OK, "all-ok", "No problems found")]
