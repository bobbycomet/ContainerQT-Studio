"""Build profiles: a reusable build configuration (script + app name/version + folders + extra variables).

Unlike an environment template (what to *build the environment from*), a profile says *how to build a project*, and
can be applied to any environment whose package family the script supports. The script text is stored inside the
profile, so a profile keeps working (reproducibly) even if the built-in template it started from changes later.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import buildscripts, paths
from .templates import _atomic_write, safe_name

KIND, SCHEMA = "dockerqt-build-profile", 1
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
RESERVED = {"APP_NAME", "APP_VERSION", "SRC_DIR", "OUT_DIR"}


@dataclass
class BuildProfile:
    name: str
    description: str = ""
    script: str = ""                 # the script text (always stored)
    template_id: str = ""            # built-in template it came from, if any (decides which families it fits)
    app_name: str = "App"
    version: str = "0.0.1"
    src_dir: str = "/workspace"
    out_dir: str = "/workspace/dist"
    extra_env: dict = field(default_factory=dict)
    env_template: str = ""           # name of the environment template this profile is meant for (optional hint)
    created: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, d: dict) -> "BuildProfile":
        known = {f.name for f in fields(cls)}
        p = cls(**{k: v for k, v in d.items() if k in known})
        p.extra_env = clean_env(p.extra_env)
        return p

    def applies_to(self, family: str) -> tuple[bool, str]:
        if not self.template_id:
            return True, ""
        t = buildscripts.get(self.template_id)
        if t is None:
            return True, ""
        if self.template_id in {x.id for x in buildscripts.templates_for(family)}:
            return True, ""
        return False, f"'{t.title}' is not meant for {family}-based Linux"

    def env_vars(self) -> dict[str, str]:
        e = dict(self.extra_env)
        e.update({"APP_NAME": self.app_name, "APP_VERSION": self.version, "SRC_DIR": self.src_dir,
                  "OUT_DIR": self.out_dir})
        return e


def clean_env(d) -> dict[str, str]:
    """Extra variables for the build script: valid names, single-line values, none of the reserved ones."""
    out: dict[str, str] = {}
    for k, v in (d or {}).items():
        k, v = str(k).strip(), str(v)
        if not _KEY.fullmatch(k):
            raise ValueError(f"'{k}' is not a valid variable name")
        if k in RESERVED:
            raise ValueError(f"{k} is set by ContainerQT Studio (use the fields above instead)")
        if "\n" in v or "\0" in v:
            raise ValueError(f"The value of {k} must be a single line")
        out[k] = v
    return out


def parse_env_text(text: str) -> dict[str, str]:
    """'KEY=value' pairs separated by commas/newlines -> dict (values may contain '=')."""
    pairs = {}
    for part in re.split(r"[\n;]+", text or ""):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"'{part}' should look like NAME=value")
        k, v = part.split("=", 1)
        pairs[k.strip()] = v.strip()
    return clean_env(pairs)


def format_env_text(env: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in env.items())


class ProfileStore:
    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder or (paths.config_dir() / "profiles")

    def all(self) -> list[BuildProfile]:
        out = []
        if self.folder.is_dir():
            for f in sorted(self.folder.glob("*.json")):
                try:
                    out.append(self._read(f))
                except (OSError, ValueError, TypeError, KeyError):
                    continue
        return out

    def get(self, name: str) -> BuildProfile | None:
        return next((p for p in self.all() if p.name == name), None)

    def save(self, p: BuildProfile, overwrite: bool = True) -> Path:
        p.name = safe_name(p.name)
        if not p.script.strip():
            raise ValueError("A profile needs a build script")
        p.extra_env = clean_env(p.extra_env)
        path = self.folder / f"{re.sub(r'[^a-z0-9]+', '-', p.name.lower()).strip('-') or 'profile'}.json"
        if path.exists() and not overwrite:
            raise FileExistsError(f"A profile called '{p.name}' already exists")
        _atomic_write(path, {"kind": KIND, "schema": SCHEMA, "profile": asdict(p)})
        return path

    def delete(self, name: str) -> bool:
        for f in self.folder.glob("*.json") if self.folder.is_dir() else []:
            try:
                if self._read(f).name == name:
                    f.unlink()
                    return True
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return False

    def export_to(self, p: BuildProfile, dest: Path) -> Path:
        dest = Path(dest)
        if dest.is_dir():
            dest = dest / f"{re.sub(r'[^a-z0-9]+', '-', p.name.lower()).strip('-')}.dockerqt-profile.json"
        _atomic_write(dest, {"kind": KIND, "schema": SCHEMA, "profile": asdict(p)})
        return dest

    def import_from(self, src: Path) -> BuildProfile:
        p = self._read(Path(src))
        self.save(p)
        return p

    @staticmethod
    def _read(path: Path) -> BuildProfile:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("kind") != KIND:
            raise ValueError("Not a ContainerQT Studio build profile")
        if int(data.get("schema", 0)) > SCHEMA:
            raise ValueError("This profile was made by a newer ContainerQT Studio")
        p = BuildProfile.from_dict(data["profile"])
        p.name = safe_name(p.name)
        return p
