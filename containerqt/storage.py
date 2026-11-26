"""Drives and folders where persistent environments can live.

A persistent environment keeps its system directories (/usr /var /etc /opt ...), its home folder and its
workspace as plain folders on a drive of the user's choice, bind-mounted into the container. That only works on
filesystems that store Unix owners, permissions and symlinks, so FAT/exFAT/NTFS, network shares and RAM disks
are listed but disabled, with the reason.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import logger, paths

log = logger.get()

GOOD_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "zfs", "jfs", "reiserfs", "bcachefs"}
BAD_REASON = {
    "vfat": "FAT cannot store Unix permissions or symlinks",
    "exfat": "exFAT cannot store Unix permissions or symlinks",
    "ntfs": "NTFS cannot store Unix permissions reliably",
    "ntfs3": "NTFS cannot store Unix permissions reliably",
    "fuseblk": "This FUSE filesystem (usually NTFS/exFAT) cannot store Unix permissions",
    "nfs": "Network shares often block root-owned files",
    "nfs4": "Network shares often block root-owned files",
    "cifs": "Network shares cannot store Unix permissions",
    "tmpfs": "RAM disk: contents vanish on reboot",
}
SKIP_PREFIXES = ("/proc", "/sys", "/dev", "/run/user", "/run/lock", "/boot", "/snap", "/var/lib/docker",
                 "/var/lib/snapd", "/var/snap", "/var/lib/containers", "/usr", "/etc", "/opt")
MARKER = ".dockerqt-env"
MIN_FREE_GB = 15


@dataclass
class Drive:
    mount: str
    source: str
    fstype: str
    total: int
    free: int
    ok: bool
    reason: str = ""

    @property
    def label(self) -> str:
        return f"{self.mount}   {self.fstype}   {fmt_size(self.free)} free of {fmt_size(self.total)}"

    @property
    def root_dir(self) -> Path:
        """Folder for ContainerQT's environments on this drive."""
        if self.mount == "/":
            return paths.default_storage_root()
        return Path(self.mount) / "DockerQT"


def fmt_size(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{v:.0f} {unit}" if unit in ("B", "KB") else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} TB"


def _unescape(s: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def read_mounts(mountinfo: str | None = None) -> list[tuple[str, str, str]]:
    """[(mount point, source, fstype)] from /proc/self/mountinfo."""
    if mountinfo is None:
        try:
            mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
    out = []
    for line in mountinfo.splitlines():
        parts = line.split(" ")
        if "-" not in parts:
            continue
        i = parts.index("-")
        if len(parts) < i + 3 or len(parts) < 5:
            continue
        out.append((_unescape(parts[4]), _unescape(parts[i + 2]), parts[i + 1]))
    return out


def classify(fstype: str) -> tuple[bool, str]:
    if fstype in GOOD_FS:
        return True, ""
    return False, BAD_REASON.get(fstype, f"Filesystem '{fstype}' is not known to keep Unix permissions")


def list_drives(mountinfo: str | None = None) -> list[Drive]:
    drives: dict[str, Drive] = {}
    for mount, source, fstype in read_mounts(mountinfo):
        if fstype in ("squashfs", "iso9660", "udf", "erofs", "overlay", "aufs"):
            continue  # read-only images / union mounts are never somewhere to store an environment
        real_dev = source.startswith("/dev/") and not source.startswith(("/dev/loop", "/dev/ram", "/dev/zram"))
        network = fstype in ("nfs", "nfs4", "cifs")
        if not (real_dev or network or fstype in ("fuseblk", "exfat", "ntfs", "ntfs3", "vfat")):
            continue
        if mount != "/" and mount.startswith(SKIP_PREFIXES):
            continue
        try:
            du = shutil.disk_usage(mount)
        except OSError:
            continue
        ok, reason = classify(fstype)
        drives[mount] = Drive(mount, source, fstype, du.total, du.free, ok, reason)
    return sorted(drives.values(), key=lambda d: (d.mount != "/", d.mount))


def drive_for_path(path: str | Path, mountinfo: str | None = None) -> Drive | None:
    """The mounted filesystem that contains `path` (longest matching mount point)."""
    p = os.path.realpath(str(path))
    best: tuple[int, str, str, str] | None = None
    for mount, source, fstype in read_mounts(mountinfo):
        if p == mount or p.startswith(mount.rstrip("/") + "/") or mount == "/":
            if best is None or len(mount) > best[0]:
                best = (len(mount), mount, source, fstype)
    if not best:
        return None
    _, mount, source, fstype = best
    try:
        du = shutil.disk_usage(mount)
    except OSError:
        return None
    ok, reason = classify(fstype)
    return Drive(mount, source, fstype, du.total, du.free, ok, reason)


def validate_root(root: str | Path) -> tuple[bool, str]:
    """Can a persistent environment live under `root`? (message explains when not)."""
    root = Path(root)
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    d = drive_for_path(probe)
    if d is None:
        return False, f"Cannot tell which drive holds {root}"
    if not d.ok:
        return False, f"{d.mount} ({d.fstype}): {d.reason}"
    if d.free < MIN_FREE_GB * 1024 ** 3:
        log.warning("Only %s free on %s", fmt_size(d.free), d.mount)
    return True, ""


def env_marker(spec_data_dir: Path) -> Path:
    return spec_data_dir / MARKER


REMOVABLE_PREFIXES = ("/mnt/", "/media/", "/run/media/")


def looks_unmounted(root: str | Path, mountinfo: str | None = None) -> bool:
    """True when `root` is under /mnt, /media or /run/media but sits on the system drive: the drive you meant is
    almost certainly not mounted, and saving there would silently fill the system drive instead."""
    p = os.path.realpath(str(root)) + "/"
    if not p.startswith(REMOVABLE_PREFIXES):
        return False
    d = drive_for_path(root, mountinfo)
    return d is not None and d.mount == "/"


def preflight(spec) -> tuple[bool, str]:
    """Before starting a persistent environment: is its saved data really there?

    Guards against a drive that is not mounted - Docker would otherwise create fresh, empty folders on the
    system drive and the environment would silently start without its data.
    """
    if not spec.persistent:
        return True, ""
    data = spec.data_dir
    if data is None:
        return False, "This environment has no storage folder set."
    mnt = getattr(spec, "storage_mount", "")
    if mnt and mnt != "/" and not os.path.ismount(mnt):
        return False, (f"The drive for '{spec.name}' ({mnt}) is not mounted.\n"
                       "Connect/mount it and try again. ContainerQT Studio will not start the environment without it - "
                       "that would create an empty copy on your system drive.")
    m = env_marker(data)
    try:
        if m.read_text(encoding="utf-8").strip() == spec.slug:
            return True, ""
    except OSError:
        pass
    return False, (f"Saved data for '{spec.name}' was not found at {data}.\n"
                   "Is the drive mounted? Connect/mount it and try again (use Rebuild only if you really want "
                   "a fresh environment).")


def saved_dirs(spec) -> list[str]:
    """System/user directories that were seeded onto the drive (written by seed.sh)."""
    root = spec.rootfs_dir
    if root is None:
        return []
    try:
        names = (root / ".dockerqt-dirs").read_text(encoding="utf-8").split()
    except OSError:
        return []
    return [n for n in names if re.fullmatch(r"[a-z0-9]+", n)]


def unmounted_warning(root: str | Path, mountinfo: str | None = None) -> str:
    """A warning when `root` looks like it belongs on an external drive but is actually on the system drive.

    Typical mistake: typing /mnt/data while the drive is not mounted - the folder would then be created on the root
    filesystem and quietly fill it. Returns "" when all is well.
    """
    p = str(root)
    if not (p.startswith("/mnt/") or p.startswith("/media/") or p.startswith("/run/media/")):
        return ""
    d = drive_for_path(p, mountinfo)
    if d is not None and d.mount not in ("/", ""):
        return ""
    return (f"{p} is under /mnt or /media but no drive is mounted there, so it would be created on your system "
            "drive. Mount the drive first (or pick it from the list), otherwise the environment fills the wrong disk.")
