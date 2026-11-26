"""Environment model and its JSON store."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import paths

PERSISTENT, EPHEMERAL, CLASSIC = "persistent", "ephemeral", "classic"
STORAGE_MODES = (PERSISTENT, EPHEMERAL, CLASSIC)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "env"


@dataclass
class EnvSpec:
    name: str
    slug: str
    distro_key: str
    base_image: str
    family: str  # apt | dnf | pacman | zypper | apk
    workspace: str  # host folder bind-mounted at /workspace
    desktop: bool = True
    de: str = "xfce"  # desktop environment key (see desktops.py)
    display_server: str = "x11"  # x11 | wayland
    compositor: str = ""  # "" = the desktop's own
    storage: str = PERSISTENT  # persistent | ephemeral (one-time use) | classic (pre-1.1 environments)
    storage_root: str = ""  # persistent: folder (on the chosen drive) that holds <slug>/
    toolchain: bool = True
    packaging: bool = True
    fuse: bool = False
    resolution: str = "1440x900"
    cpus: float = 0.0  # CPU quota in CPUs (not reserved cores); 0 = unlimited
    memory_mb: int = 0  # hard memory limit; 0 = unlimited
    resource_preset: str = "custom"  # lightweight | balanced | development | heavy | custom (informational)
    storage_limit_gb: int = 0  # advisory (soft) budget for the saved data; 0 = none
    storage_mount: str = ""  # persistent: mount point of the drive at creation time (guards against it being absent)
    extras: list = field(default_factory=list)  # toolkit packs: qt5 qt6 kf5 kf6 gtk3 gtk4 (see toolkits.py)
    extra_packages: str = ""  # additional packages installed best-effort at build time
    template: str = ""  # name of the template this environment was created from, if any
    port: int = 6080  # host port -> noVNC (127.0.0.1 only)
    image_tag: str = ""  # image the container is created from
    created: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.image_tag:
            self.image_tag = self.default_tag
        self.extras = list(self.extras or [])

    @property
    def default_tag(self) -> str:
        return f"dockerqt/{self.slug}:latest"

    # -- storage ----------------------------------------------------------------------------
    @property
    def persistent(self) -> bool:
        return self.storage == PERSISTENT

    @property
    def ephemeral(self) -> bool:
        return self.storage == EPHEMERAL

    @property
    def data_dir(self) -> Path | None:
        """Folder on the chosen drive holding this environment's saved data (persistent only)."""
        return Path(self.storage_root) / self.slug if self.persistent and self.storage_root else None

    @property
    def rootfs_dir(self) -> Path | None:
        return self.data_dir / "rootfs" if self.data_dir else None

    @property
    def container(self) -> str:
        return f"dockerqt-{self.slug}"

    @property
    def novnc_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/vnc.html"

    @classmethod
    def from_dict(cls, d: dict) -> "EnvSpec":
        known = {f.name for f in fields(cls)}
        d = dict(d)
        if "storage" not in d:  # saved by ContainerQT 1.0: container kept in Docker's own storage, workspace on host
            d["storage"] = CLASSIC
        if d.get("storage") not in STORAGE_MODES:
            d["storage"] = CLASSIC
        return cls(**{k: v for k, v in d.items() if k in known})


class EnvStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.envs_file()
        self._envs: dict[str, EnvSpec] = {}
        self.load()

    def load(self) -> None:
        self._envs = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in data.get("environments", []):
            try:
                spec = EnvSpec.from_dict(item)
            except TypeError:
                continue
            self._envs[spec.name] = spec

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "environments": [asdict(e) for e in self._envs.values()]}
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, self.path)

    def all(self) -> list[EnvSpec]:
        return sorted(self._envs.values(), key=lambda e: e.name.lower())

    def get(self, name: str | None) -> EnvSpec | None:
        return self._envs.get(name) if name else None

    def put(self, spec: EnvSpec) -> None:
        self._envs[spec.name] = spec
        self.save()

    def remove(self, name: str) -> None:
        self._envs.pop(name, None)
        self.save()

    def used_ports(self, exclude: str | None = None) -> set[int]:
        return {e.port for e in self._envs.values() if e.name != exclude}

    def slug_taken(self, slug: str) -> bool:
        return any(e.slug == slug for e in self._envs.values())
