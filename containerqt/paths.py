"""Filesystem locations and host-environment helpers."""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "ContainerQT Studio"
APP_ID = "io.github.bobbycomet.dockerqt"

_UNSET = "__unset__"
# Variables the AppImage launcher (AppRun) overrides. Child processes that are
# *host* programs (docker, pkexec, xdg-open, terminals) must not see them.
_APPRUN_VARS = (
    "LD_LIBRARY_PATH",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONNOUSERSITE",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QTWEBENGINE_DISABLE_SANDBOX",
    "QTWEBENGINE_CHROMIUM_FLAGS",
)


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or (Path.home() / default))


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    return _ensure(_xdg("XDG_DATA_HOME", ".local/share") / "dockerqt")


def config_dir() -> Path:
    return _ensure(_xdg("XDG_CONFIG_HOME", ".config") / "dockerqt")


def log_dir() -> Path:
    return _ensure(data_dir() / "logs")


def log_file() -> Path:
    return log_dir() / "dockerqt.log"


def build_context_dir(slug: str) -> Path:
    return _ensure(data_dir() / "build" / slug)


def user_scripts_dir() -> Path:
    return _ensure(config_dir() / "scripts")


def envs_file() -> Path:
    return config_dir() / "environments.json"


def default_workspace(slug: str) -> Path:
    return Path.home() / "DockerQT" / slug


def default_storage_root() -> Path:
    """Where persistent environments live unless the user picks another drive."""
    return Path.home() / "DockerQT"


def resource_dir() -> Path:
    return Path(__file__).resolve().parent / "resources"


def in_appimage() -> bool:
    return bool(os.environ.get("APPIMAGE") or os.environ.get("APPDIR"))


def host_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for launching programs that live on the *host*.

    Undoes the library/Python overrides made by the AppImage's AppRun so that
    docker, pkexec, xdg-open, terminal emulators etc. load host libraries.
    """
    env = dict(os.environ)
    for var in _APPRUN_VARS:
        saved = env.pop(f"DOCKERQT_ORIG_{var}", None)
        if saved is None:
            continue
        if saved == _UNSET:
            env.pop(var, None)
        else:
            env[var] = saved
    if extra:
        env.update(extra)
    return env
