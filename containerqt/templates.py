"""Environment templates: a saved, named, repeatable environment configuration.

A template captures *what to build* (Linux, desktop/compositor, toolkits, tools, resources, storage mode) - not the
contents of a particular container. Applying it to the Environments form (or creating from it) rebuilds the same
environment whenever it is needed. Built-in templates ship with the app; yours live in
~/.config/dockerqt/templates/<slug>.json and can be exported/imported as single files to share.

Resources are stored as a *preset* when you picked one (so they adapt to the machine the template is used on) and as
absolute values when you chose Custom; absolute values are clamped to the host and flagged when they do not fit.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import desktops, distros, paths, toolkits
from .models import EPHEMERAL, PERSISTENT, EnvSpec, slugify

SCHEMA = 1
KIND = "dockerqt-template"


@dataclass
class EnvTemplate:
    name: str
    description: str = ""
    distro_key: str = "ubuntu-2404"
    custom_image: str = ""          # only when distro_key == "custom"
    family: str = ""                # only when distro_key == "custom"
    desktop: bool = True
    de: str = "xfce"
    display_server: str = "x11"
    compositor: str = ""
    toolchain: bool = True
    packaging: bool = True
    fuse: bool = False
    resolution: str = "1440x900"
    resource_preset: str = "balanced"   # lightweight | balanced | development | heavy | custom
    cpus: float = 0.0                   # used when resource_preset == "custom"
    memory_mb: int = 0
    storage_limit_gb: int = 0
    persistent: bool = True
    extras: list = field(default_factory=list)
    extra_packages: str = ""
    build_profile: str = ""             # name of a build profile to preselect on the Build tab
    tags: list = field(default_factory=list)
    builtin: bool = False
    created: float = field(default_factory=time.time)

    @property
    def slug(self) -> str:
        return slugify(self.name)

    @classmethod
    def from_dict(cls, d: dict) -> "EnvTemplate":
        known = {f.name for f in fields(cls)}
        t = cls(**{k: v for k, v in d.items() if k in known})
        t.extras = toolkits.clean(t.extras)
        t.tags = [str(x) for x in (t.tags or [])][:12]
        return t

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("builtin", None)
        return d

    # -- relation to the rest of the app -----------------------------------------------------
    def distro(self) -> distros.Distro | None:
        if self.distro_key == distros.CUSTOM_KEY:
            return distros.custom(self.custom_image, self.family) if self.custom_image and self.family else None
        return distros.by_key(self.distro_key)

    def summary(self) -> str:
        d = self.distro()
        parts = [d.label if d else self.distro_key]
        if self.desktop:
            parts.append(desktops.describe(*desktops.normalize(self.de, self.display_server, self.compositor,
                                                               self.distro_key, d.family if d else "")))
            ps = desktops.plasma_series(self.distro_key) if self.de == "kde" else ""
            if ps:
                parts[-1] += f" (Plasma {ps})"
        else:
            parts.append("no desktop")
        if self.extras:
            parts.append(", ".join(toolkits.TOOLKITS[k].label for k in self.extras))
        parts.append("persistent" if self.persistent else "one-time")
        return " · ".join(parts)

    def problems(self) -> list[str]:
        """Why this template cannot be built as written (empty = fine). Used on import and before creating."""
        out = []
        d = self.distro()
        if d is None:
            out.append(f"Unknown Linux '{self.distro_key}'" if self.distro_key != distros.CUSTOM_KEY
                       else "A custom image needs an image name and package manager")
            return out
        if d.family not in distros.FAMILIES:
            out.append(f"Unsupported package manager '{d.family}'")
        if self.desktop:
            if self.de not in desktops.DESKTOPS:
                out.append(f"Unknown desktop '{self.de}'")
            else:
                ok, why = desktops.de_state(self.de, d.key, d.family)
                if not ok:
                    out.append(f"{desktops.DESKTOPS[self.de].label} is not available on {d.label}: {why}")
                else:
                    ok, why = desktops.compositor_state(self.de, self.display_server,
                                                        self.compositor or desktops.DESKTOPS[self.de].native)
                    if not ok:
                        out.append(why)
        try:
            toolkits.clean_packages(self.extra_packages)
        except ValueError as exc:
            out.append(str(exc))
        if self.resource_preset not in ("lightweight", "balanced", "development", "heavy", "custom"):
            out.append(f"Unknown resource preset '{self.resource_preset}'")
        return out

    @classmethod
    def from_spec(cls, spec: EnvSpec, name: str, description: str = "") -> "EnvTemplate":
        d = distros.by_key(spec.distro_key)
        return cls(
            name=name, description=description, distro_key=spec.distro_key,
            custom_image="" if d else spec.base_image, family="" if d else spec.family,
            desktop=spec.desktop, de=spec.de, display_server=spec.display_server, compositor=spec.compositor,
            toolchain=spec.toolchain, packaging=spec.packaging, fuse=spec.fuse, resolution=spec.resolution,
            resource_preset=spec.resource_preset or "custom", cpus=spec.cpus, memory_mb=spec.memory_mb,
            storage_limit_gb=spec.storage_limit_gb, persistent=spec.storage == PERSISTENT,
            extras=list(spec.extras), extra_packages=spec.extra_packages,
        )

    def to_spec(self, name: str, *, storage_root: str = "", workspace: str = "", port: int = 6080,
                cpus: float | None = None, memory_mb: int | None = None) -> EnvSpec:
        """Build an EnvSpec. Resources default to this template's (callers resolve presets for the local host)."""
        d = self.distro()
        if d is None:
            raise ValueError(self.problems()[0])
        de, server, comp = desktops.normalize(self.de, self.display_server, self.compositor, d.key, d.family)
        return EnvSpec(
            name=name, slug=slugify(name), distro_key=d.key, base_image=d.image, family=d.family, workspace=workspace,
            desktop=self.desktop, de=de, display_server=server, compositor=comp,
            storage=PERSISTENT if self.persistent else EPHEMERAL, storage_root=storage_root if self.persistent else "",
            toolchain=self.toolchain, packaging=self.packaging, fuse=self.fuse, resolution=self.resolution,
            cpus=self.cpus if cpus is None else cpus, memory_mb=self.memory_mb if memory_mb is None else memory_mb,
            resource_preset=self.resource_preset, storage_limit_gb=self.storage_limit_gb, port=port,
            extras=list(self.extras), extra_packages=self.extra_packages, template=self.name,
        )


# --------------------------------------------------------------------------------------------------- built-ins
def _b(name, description, **kw) -> EnvTemplate:
    return EnvTemplate(name=name, description=description, builtin=True, **kw)


BUILTIN: tuple[EnvTemplate, ...] = (
    _b("Ubuntu 24.04 · KDE Plasma 5 · Qt5", "Plasma 5.27 desktop with Qt 5 + KDE Frameworks 5 tools - the "
       "long-supported Plasma 5 baseline.", distro_key="ubuntu-2404", de="kde", extras=["qt5", "kf5"],
       resource_preset="development", tags=["kde", "plasma5", "qt5"]),
    _b("Ubuntu 24.04 · KDE Plasma 5 · Qt6", "Plasma 5.27 desktop with Qt 6 development libraries: build and test "
       "Qt 6 applications on a Plasma 5 desktop.", distro_key="ubuntu-2404", de="kde", extras=["qt6"],
       resource_preset="development", tags=["kde", "plasma5", "qt6"]),
    _b("Ubuntu 22.04 · KDE Plasma 5 · Qt5", "Older Plasma 5.24 and Qt 5.15: the oldest KDE most users still run.",
       distro_key="ubuntu-2204", de="kde", extras=["qt5", "kf5"], resource_preset="development",
       tags=["kde", "plasma5", "qt5"]),
    _b("Debian 13 · KDE Plasma 6 · Qt6", "Plasma 6 with Qt 6 and KDE Frameworks 6.", distro_key="debian-13",
       de="kde", extras=["qt6", "kf6"], resource_preset="development", tags=["kde", "plasma6", "qt6"]),
    _b("Fedora · KDE Plasma 6 · Qt6", "Current Fedora: the newest Plasma 6 / Qt 6 to test against.",
       distro_key="fedora-latest", de="kde", extras=["qt6", "kf6"], resource_preset="development",
       tags=["kde", "plasma6", "qt6"]),
    _b("Arch · KDE Plasma 6 · Qt6 (rolling)", "Rolling release: Plasma 6 and Qt 6 as they ship upstream.",
       distro_key="arch", de="kde", extras=["qt6", "kf6"], resource_preset="development",
       tags=["kde", "plasma6", "qt6", "rolling"]),
    _b("Ubuntu 22.04 · XFCE · AppImage builder", "Small XFCE desktop on an older glibc (2.35) - a wide-reach base "
       "for AppImages and .deb files.", distro_key="ubuntu-2204", de="xfce", resource_preset="balanced",
       tags=["appimage", "deb"]),
    _b("Ubuntu 22.04 · headless build box", "No desktop: just compilers and packaging tools (smallest and fastest).",
       distro_key="ubuntu-2204", desktop=False, resource_preset="balanced", tags=["headless", "build"]),
    _b("Rocky 9 · headless RPM builder", "RHEL 9 compatible (glibc 2.34) for .rpm packages and AppImages.",
       distro_key="rocky-9", desktop=False, resource_preset="balanced", tags=["headless", "rpm"]),
    _b("Ubuntu 24.04 · GNOME · GTK4", "GNOME session (X11) with GTK 3/4 development libraries.",
       distro_key="ubuntu-2404", de="gnome", extras=["gtk3", "gtk4"], resource_preset="development",
       tags=["gnome", "gtk"]),
    _b("One-time · Debian 12 quick test", "Disposable XFCE environment to try a package or script; nothing is kept.",
       distro_key="debian-12", de="xfce", persistent=False, resource_preset="lightweight", tags=["one-time"]),
)


# ------------------------------------------------------------------------------------------------- the store
def safe_name(name: str) -> str:
    n = re.sub(r"\s+", " ", (name or "").strip())
    if not n or len(n) > 80 or any(c in n for c in '/\\\0'):
        raise ValueError("A template name must be 1-80 characters and may not contain / or \\")
    return n


class TemplateStore:
    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder or (paths.config_dir() / "templates")

    def builtin(self) -> list[EnvTemplate]:
        return [_copy_builtin(t) for t in BUILTIN]

    def user(self) -> list[EnvTemplate]:
        out = []
        if self.folder.is_dir():
            for f in sorted(self.folder.glob("*.json")):
                try:
                    out.append(self._read(f))
                except (OSError, ValueError, TypeError, KeyError):
                    continue  # a damaged file must not hide the others
        return out

    def all(self) -> list[EnvTemplate]:
        user = self.user()
        taken = {t.name.lower() for t in user}
        return user + [t for t in self.builtin() if t.name.lower() not in taken]

    def get(self, name: str) -> EnvTemplate | None:
        return next((t for t in self.all() if t.name == name), None)

    def save(self, t: EnvTemplate, overwrite: bool = False) -> Path:
        t.name = safe_name(t.name)
        t.builtin = False
        if issues := t.problems():
            raise ValueError("; ".join(issues))
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{t.slug}.json"
        if path.exists() and not overwrite:
            raise FileExistsError(f"A template called '{t.name}' already exists")
        _atomic_write(path, _envelope(t))
        return path

    def delete(self, name: str) -> bool:
        t = next((x for x in self.user() if x.name == name), None)
        if not t:
            return False
        (self.folder / f"{t.slug}.json").unlink(missing_ok=True)
        return True

    # -- sharing ---------------------------------------------------------------------------------
    def export_to(self, t: EnvTemplate, dest: Path) -> Path:
        dest = Path(dest)
        if dest.is_dir():
            dest = dest / f"{t.slug}.dockerqt-template.json"
        _atomic_write(dest, _envelope(t))
        return dest

    def import_from(self, src: Path, overwrite: bool = False) -> EnvTemplate:
        t = self._read(Path(src))
        if issues := t.problems():
            raise ValueError("This template cannot be used here: " + "; ".join(issues))
        self.save(t, overwrite=overwrite)
        return t

    @staticmethod
    def _read(path: Path) -> EnvTemplate:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("kind") != KIND:
            raise ValueError("Not a ContainerQT Studio template file")
        if int(data.get("schema", 0)) > SCHEMA:
            raise ValueError("This template was made by a newer ContainerQT Studio")
        t = EnvTemplate.from_dict(data["template"])
        t.name = safe_name(t.name)
        t.builtin = False
        return t


def _copy_builtin(t: EnvTemplate) -> EnvTemplate:
    c = EnvTemplate.from_dict(asdict(t))
    c.builtin = True
    return c


def _envelope(t: EnvTemplate) -> dict:
    return {"kind": KIND, "schema": SCHEMA, "template": t.to_dict()}


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, path)
