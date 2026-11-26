"""pkexec / polkit integration.

First use installs the root-owned helper + polkit policy (one generic pkexec prompt, script piped over
stdin so nothing user-writable is ever executed as root). After that every privileged action goes
through `pkexec /usr/libexec/dockerqt/dockerqt-helper <action>` with the ContainerQT polkit action.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable

from . import logger, paths

log = logger.get()
Out = Callable[[str], None]

HELPER_PATH = "/usr/libexec/dockerqt/dockerqt-helper"
POLICY_PATH = "/usr/share/polkit-1/actions/io.github.bobbycomet.dockerqt.policy"
VALID_ACTIONS = (
    "install-docker", "start-docker", "restart-docker", "add-user", "fix-socket", "status", "uninstall",
)


def pkexec_available() -> bool:
    return shutil.which("pkexec") is not None


def helper_installed() -> bool:
    return os.access(HELPER_PATH, os.X_OK) and os.path.exists(POLICY_PATH)


def _installer_script() -> str:
    helper = (paths.resource_dir() / "dockerqt-helper").read_text(encoding="utf-8")
    policy = (paths.resource_dir() / "io.github.bobbycomet.dockerqt.policy").read_text(encoding="utf-8")
    for marker in ("DQT_HELPER_EOF", "DQT_POLICY_EOF"):
        if marker in helper or marker in policy:
            raise RuntimeError("heredoc marker collision in helper resources")
    return (
        "set -e\n"
        "umask 022\n"
        "mkdir -p /usr/libexec/dockerqt /usr/share/polkit-1/actions\n"
        f"cat > {HELPER_PATH} <<'DQT_HELPER_EOF'\n{helper}\nDQT_HELPER_EOF\n"
        f"chown root:root {HELPER_PATH} && chmod 0755 {HELPER_PATH}\n"
        f"cat > {POLICY_PATH} <<'DQT_POLICY_EOF'\n{policy}\nDQT_POLICY_EOF\n"
        f"chown root:root {POLICY_PATH} && chmod 0644 {POLICY_PATH}\n"
        "echo '[installer] ContainerQT Studio helper installed'\n"
    )


def _stream(argv: list[str], out: Out, stdin: str | None = None) -> int:
    log.info("$ %s", " ".join(argv))
    try:
        p = subprocess.Popen(
            argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            errors="replace", env=paths.host_env(),
        )
    except OSError as exc:
        out(f"could not run {argv[0]}: {exc}")
        return 127
    if stdin is not None and p.stdin:
        try:
            p.stdin.write(stdin)
            p.stdin.close()
        except OSError:
            pass
    assert p.stdout is not None
    for line in p.stdout:
        out(line.rstrip("\r\n"))
    return p.wait()


def install_helper(out: Out) -> bool:
    if not pkexec_available():
        out("pkexec (polkit) is not installed. Install the `polkit` package, or run ContainerQT Studio's helper steps manually.")
        return False
    out("Installing the ContainerQT Studio privileged helper (one-time admin prompt)...")
    rc = _stream(["pkexec", "/bin/sh", "-s"], out, stdin=_installer_script())
    if rc == 126:
        out("Authentication was cancelled or denied.")
    elif rc == 127:
        out("No polkit authentication agent is running in this session.")
    ok = rc == 0 and helper_installed()
    if not ok:
        log.error("Helper installation failed (exit %s)", rc)
    return ok


def run_helper(action: str, out: Out) -> bool:
    """Run one whitelisted privileged action. Installs the helper first if needed."""
    if action not in VALID_ACTIONS:
        raise ValueError(f"unknown helper action: {action}")
    if not helper_installed() and not install_helper(out):
        return False
    argv = [HELPER_PATH, action] if os.geteuid() == 0 else ["pkexec", HELPER_PATH, action]
    log.info("Privileged action: %s", action)
    rc = _stream(argv, out)
    if rc == 126:
        out("Authentication was cancelled or denied.")
    if rc != 0:
        log.error("Helper action '%s' failed (exit %s)", action, rc)
    return rc == 0


def remove_helper(out: Out) -> bool:
    if not helper_installed():
        out("Helper is not installed.")
        return True
    return run_helper("uninstall", out)
