"""Thin wrapper around the `docker` CLI: status detection, command building, quick queries."""
from __future__ import annotations

import getpass
import grp
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass

from . import logger, paths

log = logger.get()

OK = "ok"
MISSING = "missing"  # docker CLI not installed
DAEMON_DOWN = "daemon_down"
PERMISSION = "permission"  # socket exists but user may not use it
UNKNOWN = "unknown"


@dataclass
class DockerStatus:
    kind: str
    version: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == OK

    def describe(self) -> str:
        return {
            OK: f"Docker {self.version}",
            MISSING: "Docker is not installed",
            DAEMON_DOWN: "Docker daemon is not running",
            PERMISSION: "No permission to use Docker",
            UNKNOWN: "Docker status unknown",
        }.get(self.kind, self.kind)


def _in_docker_group_db() -> bool:
    """True if /etc/group lists the user in `docker` (even if this session does not have it yet)."""
    try:
        g = grp.getgrnam("docker")
    except KeyError:
        return False
    user = getpass.getuser()
    return user in g.gr_mem or g.gr_gid == os.getgid()


def _session_has_docker_group() -> bool:
    try:
        gid = grp.getgrnam("docker").gr_gid
    except KeyError:
        return False
    return gid in os.getgroups()


class DockerClient:
    def __init__(self) -> None:
        self.use_sg = False  # run docker through `sg docker -c` (group added after login)
        self.binary = shutil.which("docker")

    # -- command construction ------------------------------------------------
    def argv(self, *args: str) -> list[str]:
        base = [self.binary or "docker", *args]
        if self.use_sg and shutil.which("sg"):
            return ["sg", "docker", "-c", shlex.join(base)]
        return base

    def run(self, *args: str, timeout: float = 30, input: str | None = None) -> tuple[int, str, str]:
        """Run a short docker command and capture output. Never raises."""
        argv = self.argv(*args)
        try:
            p = subprocess.run(
                argv, input=input, capture_output=True, text=True, timeout=timeout,
                env=paths.host_env(), errors="replace",
            )
            return p.returncode, p.stdout.strip(), p.stderr.strip()
        except FileNotFoundError:
            return 127, "", "docker executable not found"
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout:.0f}s: {' '.join(args)}"

    # -- status --------------------------------------------------------------
    def status(self) -> DockerStatus:
        self.binary = shutil.which("docker")
        if not self.binary:
            return DockerStatus(MISSING, detail="`docker` was not found in PATH")
        rc, out, err = self.run("version", "--format", "{{.Server.Version}}", timeout=10)
        if rc == 0 and out:
            return DockerStatus(OK, version=out)
        low = err.lower()
        if "permission denied" in low:
            if not self.use_sg and _in_docker_group_db() and not _session_has_docker_group() and shutil.which("sg"):
                self.use_sg = True
                rc2, out2, err2 = self.run("version", "--format", "{{.Server.Version}}", timeout=10)
                if rc2 == 0 and out2:
                    log.info("Using `sg docker` because the docker group was added after you logged in")
                    return DockerStatus(OK, version=out2)
                self.use_sg = False
            return DockerStatus(PERMISSION, detail=err)
        if (
            "cannot connect" in low or "is the docker daemon running" in low
            or "no such file or directory" in low or "connection refused" in low
            or "failed to connect" in low
        ):
            return DockerStatus(DAEMON_DOWN, detail=err)
        return DockerStatus(UNKNOWN, detail=err or f"exit code {rc}")

    def info(self) -> dict:
        """`docker info` as a dict ({} when unavailable)."""
        import json

        rc, out, _ = self.run("info", "--format", "{{json .}}", timeout=20)
        if rc != 0 or not out:
            return {}
        try:
            data = json.loads(out)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    # -- quick queries --------------------------------------------------------
    def container_state(self, container: str) -> str:
        """running | exited | paused | created | restarting | dead | missing"""
        rc, out, _ = self.run("inspect", "-f", "{{.State.Status}}", container, timeout=10)
        return out if rc == 0 and out else "missing"

    def image_exists(self, tag: str) -> bool:
        rc, _, _ = self.run("image", "inspect", tag, timeout=10)
        return rc == 0

    def list_images(self, repo: str) -> list[str]:
        rc, out, _ = self.run("images", repo, "--format", "{{.Repository}}:{{.Tag}}", timeout=15)
        if rc != 0:
            return []
        return [ln for ln in out.splitlines() if ln and "<none>" not in ln]

    def exec_argv(self, container: str, *cmd: str, workdir: str | None = None,
                  env: dict[str, str] | None = None, interactive: bool = False, user: str = "dev") -> list[str]:
        args = ["exec"]
        if interactive:
            args.append("-it")
        args += ["-u", user]
        if workdir:
            args += ["-w", workdir]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        args.append(container)
        args += list(cmd)
        return self.argv(*args)

    def list_dir(self, container: str, path: str) -> tuple[bool, list[tuple[str, bool]] | str]:
        """List `path` inside the container -> (ok, [(name, is_dir)] | error)."""
        rc, out, err = self.run("exec", "-u", "dev", "-w", path, container, "ls", "-1Ap", timeout=15)
        if rc != 0:
            return False, err or "could not list directory"
        items = []
        for ln in out.splitlines():
            if not ln:
                continue
            items.append((ln.rstrip("/"), ln.endswith("/")))
        return True, items
