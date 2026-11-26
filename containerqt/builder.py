"""Generates the Dockerfile / build context and `docker run` arguments for an environment."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import desktops, distros, paths, storage, toolkits
from .docker import resource_limits
from .models import EnvSpec

# --------------------------------------------------------------------------
# Per-package-manager recipes
# --------------------------------------------------------------------------
FAMILY = {
    "apt": {
        "refresh": "apt-get update",
        "install": "apt-get install -y --no-install-recommends {pkgs}",
        "cleanup": "rm -rf /var/lib/apt/lists/*",
        "env": "DEBIAN_FRONTEND=noninteractive",
        "base": "sudo ca-certificates curl wget git gnupg locales xz-utils file unzip zip tar bash nano "
                "python3 python3-pip procps iproute2 less findutils passwd",
        "toolchain": "build-essential cmake ninja-build pkg-config meson autoconf automake libtool "
                     "python3-venv python3-dev",
        "packaging": "dpkg-dev debhelper devscripts equivs fakeroot lintian rpm squashfs-tools "
                     "desktop-file-utils patchelf zsync ruby-dev rpm2cpio cpio",
        "vnc": "tigervnc-standalone-server tigervnc-common dbus-x11 x11-xserver-utils xauth fonts-dejavu",
    },
    "dnf": {
        "refresh": "true",
        "install": "dnf install -y --allowerasing {pkgs}",
        "cleanup": "dnf clean all",
        "env": "",
        "base": "sudo ca-certificates curl wget git tar xz unzip zip file nano vim-minimal python3 python3-pip "
                "which findutils procps-ng shadow-utils util-linux glibc-langpack-en bash",
        "toolchain": "gcc gcc-c++ make cmake ninja-build pkgconf-pkg-config meson autoconf automake libtool "
                     "python3-devel",
        "packaging": "rpm-build rpmdevtools rpmlint squashfs-tools desktop-file-utils patchelf fakeroot dpkg "
                     "ruby-devel redhat-rpm-config",
        "vnc": "tigervnc-server dbus-x11 xauth dejavu-sans-fonts",
    },
    "pacman": {
        "refresh": "pacman -Syu --noconfirm",
        "install": "pacman -S --noconfirm --needed {pkgs}",
        "cleanup": "pacman -Scc --noconfirm",
        "env": "",
        "base": "sudo ca-certificates curl wget git tar xz unzip zip file nano vim python python-pip which "
                "findutils procps-ng iproute2 base-devel bash",
        "toolchain": "cmake ninja pkgconf meson",
        "packaging": "squashfs-tools desktop-file-utils patchelf fakeroot dpkg rpm-tools libarchive ruby",
        "vnc": "tigervnc dbus xorg-xauth ttf-dejavu",
    },
    "zypper": {
        "refresh": "zypper -n --gpg-auto-import-keys refresh",
        "install": "zypper -n install --no-recommends {pkgs}",
        "cleanup": "zypper -n clean --all",
        "env": "",
        "base": "sudo ca-certificates curl wget git tar xz unzip zip file nano vim python3 python3-pip which "
                "findutils procps shadow util-linux awk bash",
        "toolchain": "gcc gcc-c++ make cmake ninja pkgconf-pkg-config meson autoconf automake libtool "
                     "python3-devel",
        "packaging": "rpm-build rpmlint squashfs desktop-file-utils patchelf fakeroot dpkg ruby-devel",
        "vnc": "tigervnc dbus-1-x11 xauth dejavu-fonts",
    },
    "apk": {
        "refresh": "true",
        "install": "apk add --no-cache {pkgs}",
        "cleanup": "true",
        "env": "",
        "base": "sudo ca-certificates curl wget git tar xz unzip zip file nano vim python3 py3-pip bash shadow "
                "coreutils findutils procps iproute2 util-linux",
        "toolchain": "build-base cmake samurai pkgconf meson autoconf automake libtool python3-dev",
        "packaging": "alpine-sdk abuild squashfs-tools desktop-file-utils patchelf fakeroot ruby-dev",
        "vnc": "tigervnc dbus-x11 xauth font-dejavu",
    },
}

NOVNC_CLONE = (
    "(git clone --depth 1 --branch v1.5.0 https://github.com/novnc/noVNC.git /opt/novnc "
    "|| git clone --depth 1 https://github.com/novnc/noVNC.git /opt/novnc) && "
    "(git clone --depth 1 --branch v0.12.0 https://github.com/novnc/websockify.git /opt/novnc/utils/websockify "
    "|| git clone --depth 1 https://github.com/novnc/websockify.git /opt/novnc/utils/websockify) && "
    "ln -sf vnc.html /opt/novnc/index.html && rm -rf /opt/novnc/.git /opt/novnc/utils/websockify/.git"
)

# Create the unprivileged "dev" user with the host UID/GID so bind-mounted files stay yours.
USER_SETUP = (
    "set -e; "
    'old="$(getent passwd "$HOST_UID" | cut -d: -f1 || true)"; '
    '[ -z "$old" ] || userdel -r "$old" 2>/dev/null || userdel "$old" || true; '
    'oldg="$(getent group "$HOST_GID" | cut -d: -f1 || true)"; '
    '[ -z "$oldg" ] || groupdel "$oldg" 2>/dev/null || true; '
    'groupadd -g "$HOST_GID" dev; '
    'useradd -m -u "$HOST_UID" -g "$HOST_GID" -s /bin/bash dev; '
    "mkdir -p /etc/sudoers.d; "
    "echo 'dev ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-dev; chmod 0440 /etc/sudoers.d/90-dev"
)


def _fam(spec_family: str) -> dict:
    if spec_family not in FAMILY:
        raise ValueError(f"Unsupported package family: {spec_family}")
    return FAMILY[spec_family]


def install_required(family: str, pkgs: str) -> str:
    f = _fam(family)
    return f"{f['refresh']} && {f['install'].format(pkgs=pkgs)}"


def install_optional(family: str, pkgs: str) -> str:
    """Install each package separately; a missing one is reported but not fatal."""
    f = _fam(family)
    one = f["install"].format(pkgs='"$p"')
    return (
        f"{f['refresh']} || true; "
        f'for p in {pkgs}; do {one} || echo "[dockerqt] optional package $p unavailable"; done'
    )


def _run(cmd: str) -> str:
    """Render a multi-statement shell command as one Dockerfile RUN instruction."""
    return "RUN " + cmd.replace("\n", " ")


def desktop_lines(spec: EnvSpec) -> list[str]:
    """Dockerfile instructions that install the VNC server, the chosen desktop and compositor, and noVNC."""
    fam = _fam(spec.family)
    de, server, comp = desktops.normalize(spec.de, spec.display_server, spec.compositor)
    plan = desktops.plan(de, server, comp, spec.family)
    out = [f"# Desktop: {desktops.describe(de, server, comp)}"]
    out.append(_run(install_required(spec.family, fam["vnc"])))
    if plan.required:
        out.append(_run(install_required(spec.family, plan.required)))
    for extra in (plan.optional, plan.compositor_pkgs):
        if extra:
            out.append(_run(install_optional(spec.family, extra)))
    # Fail the build now, with a clear message, instead of producing an environment without a desktop.
    checks = " ".join('"' + " ".join(alts) + '"' for alts in plan.checks)
    out.append(_run(
        f'for alts in {checks}; do ok=0; for b in $alts; do command -v "$b" >/dev/null 2>&1 && ok=1; done; '
        f'[ "$ok" = 1 ] || {{ echo "[dockerqt] ERROR: none of [$alts] was installed - {desktops.DESKTOPS[de].label} '
        f'is not available for this Linux; pick another desktop or compositor." >&2; exit 1; }}; done'
    ))
    out.append(_run(NOVNC_CLONE))
    return out


def desktop_env(spec: EnvSpec) -> dict[str, str]:
    de, server, comp = desktops.normalize(spec.de, spec.display_server, spec.compositor)
    native = desktops.is_native(de, comp)
    return {"DQT_DE": de, "DQT_SERVER": server, "DQT_WM": "" if native else comp}


def render_dockerfile(spec: EnvSpec) -> str:
    fam = _fam(spec.family)
    d = distros.by_key(spec.distro_key)
    lines: list[str] = [
        "# Generated by ContainerQT - edits are overwritten on rebuild.",
        f"FROM {spec.base_image}",
        "LABEL io.dockerqt.managed=true",
        f"LABEL io.dockerqt.env={spec.slug}",
        "ARG HOST_UID=1000",
        "ARG HOST_GID=1000",
        "ENV LANG=C.UTF-8 APPIMAGE_EXTRACT_AND_RUN=1 DOCKERQT_ENV=" + spec.slug,
    ]
    if fam["env"]:
        lines.append(f"ENV {fam['env']}")
    if d and d.setup:
        lines.append(_run(d.setup))
    if spec.family == "pacman":
        # Newer pacman's Landlock sandbox fails inside many container runtimes.
        lines.append(_run(
            "if pacman -V | grep -q 'v7'; then "
            "grep -q '^DisableSandbox' /etc/pacman.conf || sed -i '/^\\[options\\]/a DisableSandbox' /etc/pacman.conf; fi"
        ))
    lines.append(_run(install_required(spec.family, fam["base"])))
    lines.append(_run(USER_SETUP))
    if spec.toolchain:
        lines.append(_run(install_required(spec.family, fam["toolchain"])))
    if spec.packaging:
        lines.append(_run(install_optional(spec.family, fam["packaging"])))
    if spec.desktop:
        lines += desktop_lines(spec)
    extra = " ".join(x for x in (toolkits.packages(spec.extras, spec.family),
                                 toolkits.clean_packages(spec.extra_packages)) if x)
    if extra:
        lines.append("# Toolkit packs and extra packages (best effort: a name this release lacks is reported, not fatal)")
        lines.append(_run(install_optional(spec.family, extra)))
    lines.append(_run(fam["cleanup"] + " || true"))
    lines += [
        "COPY entrypoint.sh /usr/local/bin/dockerqt-entrypoint",
        "COPY desktop.sh /usr/local/bin/dockerqt-desktop",
        _run(
            "chmod 0755 /usr/local/bin/dockerqt-entrypoint /usr/local/bin/dockerqt-desktop && "
            'mkdir -p /workspace && chown "$HOST_UID:$HOST_GID" /workspace'
        ),
        "WORKDIR /workspace",
        'ENTRYPOINT ["/usr/local/bin/dockerqt-entrypoint"]',
        "",
    ]
    return "\n".join(lines)


def host_ids() -> tuple[int, int]:
    uid, gid = os.getuid(), os.getgid()
    # A root-owned workspace makes no sense for the unprivileged dev user.
    return (uid or 1000, gid or 1000)


def sync_scripts(spec: EnvSpec) -> Path:
    """Copy the launcher scripts into the environment's context folder (also used as bind mounts)."""
    ctx = paths.build_context_dir(spec.slug)
    for name in ("entrypoint.sh", "desktop.sh"):
        dst = ctx / name
        shutil.copyfile(paths.resource_dir() / name, dst)
        dst.chmod(0o755)
    return ctx


def prepare_context(spec: EnvSpec) -> Path:
    """Write Dockerfile + helper scripts into the build context directory."""
    ctx = sync_scripts(spec)
    (ctx / "Dockerfile").write_text(render_dockerfile(spec), encoding="utf-8")
    if not spec.ephemeral and spec.workspace:
        ws = Path(spec.workspace)
        inside_data = spec.data_dir is not None and ws == spec.data_dir / "workspace"
        if not inside_data:  # inside the storage folder the root-side seed step creates it
            ws.mkdir(parents=True, exist_ok=True)
    return ctx


def build_args(spec: EnvSpec, ctx: Path) -> list[str]:
    uid, gid = host_ids()
    return [
        "build", "-t", spec.image_tag if spec.image_tag else spec.default_tag,
        "--build-arg", f"HOST_UID={uid}", "--build-arg", f"HOST_GID={gid}",
        "-f", str(ctx / "Dockerfile"), str(ctx),
    ]


def _selinux() -> bool:
    return os.path.isdir("/sys/fs/selinux") and os.path.exists("/sys/fs/selinux/enforce")


def mount_args(spec: EnvSpec) -> list[str]:
    """Bind mounts for the environment's storage mode."""
    z = ":z" if _selinux() else ""
    args: list[str] = []
    if spec.ephemeral:
        return args  # nothing on the host: everything lives (and dies) with the container
    args += ["-v", f"{spec.workspace}:/workspace{z}"]
    if spec.persistent:
        dirs = storage.saved_dirs(spec)
        if not dirs or spec.rootfs_dir is None:
            raise ValueError(f"'{spec.name}' has no saved data on its drive yet (storage was not prepared)")
        for d in dirs:
            args += ["-v", f"{spec.rootfs_dir / d}:/{d}{z}"]
        # keep the launcher scripts current even though /usr/local/bin now lives on the drive
        ctx = paths.build_context_dir(spec.slug)
        args += ["-v", f"{ctx / 'entrypoint.sh'}:/usr/local/bin/dockerqt-entrypoint:ro",
                 "-v", f"{ctx / 'desktop.sh'}:/usr/local/bin/dockerqt-desktop:ro"]
    return args


def limits_for(spec: EnvSpec) -> "resource_limits.Limits":
    """The environment's CPU/memory limits, clamped to what this machine can honour (Docker rejects more)."""
    from .hardware import cpu as hw_cpu, memory as hw_mem

    return resource_limits.clamp(resource_limits.Limits(spec.cpus, spec.memory_mb),
                                 hw_cpu.read_cpu_info().logical_threads, hw_mem.read_memory().total_mb or None)


def run_args(spec: EnvSpec, image: str | None = None) -> list[str]:
    limits = limits_for(spec)
    args = ["run", "-d", "--name", spec.container, "--hostname", spec.slug, "--shm-size", resource_limits.shm_size(limits)]
    if not spec.persistent:
        # `--init` bind-mounts docker-init at /sbin/docker-init. On usr-merged distros /sbin -> usr/sbin, which a
        # persistent environment replaces with the copy on its drive, hiding that mount (container fails to start).
        # The entrypoint (a POSIX shell as PID 1) reaps orphaned processes itself, so persistent ones go without.
        args.append("--init")
    if spec.ephemeral:
        args.append("--rm")  # one-time use: the container (and anything in it) is discarded on stop
    args += ["--label", "io.dockerqt.managed=true", "--label", f"io.dockerqt.env={spec.slug}",
             "--label", f"io.dockerqt.storage={spec.storage}"]
    args += mount_args(spec)
    args += ["-e", f"DOCKERQT_DESKTOP={'1' if spec.desktop else '0'}", "-e", f"VNC_RESOLUTION={spec.resolution}"]
    if spec.desktop:
        for k, v in desktop_env(spec).items():
            args += ["-e", f"{k}={v}"]
        args += ["-p", f"127.0.0.1:{spec.port}:6080"]
    args += resource_limits.run_args(limits)
    if spec.fuse:
        args += ["--device", "/dev/fuse", "--cap-add", "SYS_ADMIN", "--security-opt", "apparmor:unconfined"]
    args.append(image or spec.image_tag)
    return args


def seed_args(spec: EnvSpec, image: str | None = None, reset: bool = False) -> list[str]:
    """`docker run` that copies the image's directories onto the chosen drive (see resources/seed.sh)."""
    assert spec.storage_root
    uid, gid = host_ids()
    z = ":z" if _selinux() else ""
    script = (paths.resource_dir() / "seed.sh").read_text(encoding="utf-8")
    return ["run", "--rm", "--entrypoint", "/bin/sh", "-v", f"{spec.storage_root}:/store{z}",
            "-e", f"SLUG={spec.slug}", "-e", f"HOST_UID={uid}", "-e", f"HOST_GID={gid}",
            "-e", f"DQT_RESET={'reset' if reset else ''}",
            image or spec.image_tag, "-c", script]


def clone_args(src: EnvSpec, dst: EnvSpec) -> list[str]:
    """`docker run` that copies a persistent environment's saved data to the clone's folder (resources/clone.sh)."""
    assert src.storage_root and dst.storage_root
    uid, gid = host_ids()
    z = ":z" if _selinux() else ""
    script = (paths.resource_dir() / "clone.sh").read_text(encoding="utf-8")
    return ["run", "--rm", "--entrypoint", "/bin/sh", "-v", f"{src.storage_root}:/src-store:ro{z}",
            "-v", f"{dst.storage_root}:/store{z}", "-e", f"SRC_SLUG={src.slug}", "-e", f"SLUG={dst.slug}",
            "-e", f"HOST_UID={uid}", "-e", f"HOST_GID={gid}", src.image_tag, "-c", script]


def purge_args(spec: EnvSpec, image: str | None = None) -> list[str]:
    """`docker run` that deletes the saved (root-owned) data of an environment from its drive."""
    z = ":z" if _selinux() else ""
    script = (paths.resource_dir() / "purge.sh").read_text(encoding="utf-8")
    return ["run", "--rm", "--entrypoint", "/bin/sh", "-v", f"{spec.storage_root}:/store{z}",
            "-e", f"SLUG={spec.slug}", image or spec.image_tag, "-c", script]
