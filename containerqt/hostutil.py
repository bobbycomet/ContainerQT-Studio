"""Helpers that interact with the host desktop (browser, file manager, terminal)."""
from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

from . import logger, paths

log = logger.get()

# (binary, args-before-command)
_TERMINALS = (
    ("x-terminal-emulator", ["-e"]),
    ("gnome-terminal", ["--"]),
    ("kgx", ["--"]),
    ("konsole", ["-e"]),
    ("xfce4-terminal", ["-x"]),
    ("mate-terminal", ["-x"]),
    ("tilix", ["-e"]),
    ("kitty", []),
    ("alacritty", ["-e"]),
    ("foot", []),
    ("xterm", ["-e"]),
)


def _spawn(argv: list[str]) -> bool:
    try:
        subprocess.Popen(
            argv, env=paths.host_env(), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        return True
    except OSError as exc:
        log.warning("Could not launch %s: %s", argv[0], exc)
        return False


def open_url(url: str) -> bool:
    for opener in ("xdg-open", "gio", "sensible-browser"):
        exe = shutil.which(opener)
        if exe:
            argv = [exe, "open", url] if opener == "gio" else [exe, url]
            if _spawn(argv):
                return True
    return False


def open_path(path: str | Path) -> bool:
    return open_url(str(path))


def find_terminal() -> tuple[str, list[str]] | None:
    for name, args in _TERMINALS:
        exe = shutil.which(name)
        if exe:
            return exe, args
    return None


def launch_terminal(command: list[str]) -> bool:
    found = find_terminal()
    if not found:
        log.error("No terminal emulator found on the host (tried x-terminal-emulator, gnome-terminal, konsole, ...)")
        return False
    exe, pre = found
    log.info("Opening terminal: %s", " ".join(command))
    return _spawn([exe, *pre, *command])


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def find_free_port(start: int = 6080, avoid: set[int] | None = None) -> int:
    avoid = avoid or set()
    for port in range(start, start + 400):
        if port not in avoid and port_is_free(port):
            return port
    raise RuntimeError("No free local port found for the desktop view")


def http_ok(url: str, timeout: float = 2.0) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - localhost only
            return 200 <= r.status < 400
    except Exception:
        return False
