"""The 'Fix' button: diagnose Docker and every ContainerQT environment, repairing what it can."""
from __future__ import annotations

import time
from pathlib import Path

from . import builder, client as dc, hostutil, logger, privilege, storage
from .client import DockerClient
from .models import EnvSpec, EnvStore
from .runner import Out, Runner, Step

log = logger.get()


class Fixer:
    def __init__(self, client: DockerClient, store: EnvStore, out: Out, only: EnvSpec | None = None) -> None:
        self.client = client
        self.store = store
        self.out = out
        self.only = only
        self.problems = 0
        self.fixed = 0

    def _say(self, msg: str) -> None:
        log.info(msg)
        self.out(msg)

    # -- docker level ---------------------------------------------------------------
    def _wait_status(self, seconds: int = 40) -> dc.DockerStatus:
        end = time.time() + seconds
        st = self.client.status()
        while not st.ok and time.time() < end:
            time.sleep(2)
            st = self.client.status()
        return st

    def fix_docker(self) -> bool:
        st = self.client.status()
        self._say(f"Docker check: {st.describe()}")
        if st.ok:
            return True
        self.problems += 1

        if st.kind == dc.MISSING:
            self._say("Installing Docker (admin password required)...")
            if not privilege.run_helper("install-docker", self.out):
                return False
            st = self._wait_status()
        elif st.kind == dc.DAEMON_DOWN:
            self._say("Starting the Docker daemon (admin password required)...")
            if not privilege.run_helper("start-docker", self.out):
                self._say("Start failed; trying a restart")
                if not privilege.run_helper("restart-docker", self.out):
                    return False
            st = self._wait_status()
        elif st.kind == dc.PERMISSION:
            self._say("Granting this user access to Docker (admin password required)...")
            if not (privilege.run_helper("add-user", self.out) and privilege.run_helper("fix-socket", self.out)):
                return False
            st = self._wait_status(10)
        else:
            self._say(f"Unrecognised Docker problem: {st.detail}")
            return False

        if st.kind == dc.PERMISSION:
            self._say("Docker is installed but this login session cannot use it yet. "
                      "Log out and back in (or reboot) once, then press Fix again.")
            return False
        if st.ok:
            self.fixed += 1
            self._say(f"Docker repaired: {st.describe()}")
            return True
        self._say(f"Docker is still not working: {st.describe()} {st.detail}")
        return False

    # -- per environment ----------------------------------------------------------------
    def _stream(self, args: list[str], title: str) -> bool:
        res = Runner(self.out).run([Step(title, argv=self.client.argv(*args))])
        return res.ok

    def _run_container(self, spec: EnvSpec, image: str | None = None) -> bool:
        """docker run, retrying on a new port if the desktop port is taken."""
        for attempt in range(2):
            try:
                argv = builder.run_args(spec, image)
            except ValueError as exc:
                self.out(str(exc))
                return False
            rc, out, err = self.client.run(*argv, timeout=120)
            if rc == 0:
                return True
            msg = (err or out).lower()
            self.out(err or out)
            if spec.desktop and ("port is already allocated" in msg or "address already in use" in msg) and attempt == 0:
                new = hostutil.find_free_port(spec.port + 1, self.store.used_ports(spec.name))
                self._say(f"Port {spec.port} is busy; moving the desktop view to port {new}")
                self.client.run("rm", "-f", spec.container)
                spec.port = new
                self.store.put(spec)
                continue
            if "is already in use by container" in msg:
                self.client.run("rm", "-f", spec.container)
                continue
            return False
        return False

    def fix_env(self, spec: EnvSpec) -> bool:
        self._say(f"--- Checking environment '{spec.name}' ---")
        healthy = True
        if spec.persistent:
            ok, why = storage.preflight(spec)
            if not ok:
                self.problems += 1
                self._say(why)
                return False
            builder.sync_scripts(spec)
        if not spec.ephemeral and spec.workspace:
            Path(spec.workspace).mkdir(parents=True, exist_ok=True)

        if not self.client.image_exists(spec.image_tag):
            healthy = False
            self.problems += 1
            self._say(f"Image {spec.image_tag} is missing; rebuilding it (this can take a while)...")
            ctx = builder.prepare_context(spec)
            if spec.image_tag != spec.default_tag:
                spec.image_tag = spec.default_tag
                self.store.put(spec)
            if not self._stream(builder.build_args(spec, ctx), "docker build"):
                self._say("Image rebuild failed - see the log above")
                return False
            self.client.run("rm", "-f", spec.container)

        state = self.client.container_state(spec.container)
        self._say(f"Container state: {state}")
        if spec.ephemeral and state == "missing":
            self._say("This is a one-time environment and it is not running - that is normal. Press Start for a "
                      "fresh one.")
            return True
        if state == "missing":
            healthy = False
            self.problems += 1
            self._say("Container is missing; creating it again from the image")
            if not self._run_container(spec):
                return False
        elif state in ("exited", "created"):
            healthy = False
            self.problems += 1
            self._say("Container is stopped; starting it")
            rc, out, err = self.client.run("start", spec.container, timeout=60)
            if rc != 0:
                self.out(err or out)
                self._say("Start failed; recreating the container")
                self.client.run("rm", "-f", spec.container)
                if not self._run_container(spec):
                    return False
        elif state == "paused":
            healthy = False
            self.problems += 1
            self.client.run("unpause", spec.container)
        elif state in ("dead", "restarting", "removing"):
            healthy = False
            self.problems += 1
            self._say(f"Container is '{state}'; recreating it")
            self.client.run("rm", "-f", spec.container)
            if not self._run_container(spec):
                return False

        rc, _, err = self.client.run("exec", "-u", "dev", spec.container, "true", timeout=20)
        if rc != 0:
            self.problems += 1
            healthy = False
            self._say(f"Cannot run commands in the container: {err}")
            return False

        if spec.desktop and not hostutil.http_ok(spec.novnc_url):
            healthy = False
            self.problems += 1
            self._say("Desktop view is not responding; restarting the desktop session")
            self.client.run("exec", "-d", "-u", "dev", "-e", f"VNC_RESOLUTION={spec.resolution}",
                            spec.container, "/usr/local/bin/dockerqt-desktop", "restart", timeout=30)
            for _ in range(30):
                if hostutil.http_ok(spec.novnc_url):
                    break
                time.sleep(2)
            else:
                self._say("Desktop still not responding. Check /tmp/dockerqt-desktop/*.log inside the container.")
                return False

        if healthy:
            self._say(f"'{spec.name}' looks healthy")
        else:
            self.fixed += 1
            self._say(f"'{spec.name}' repaired")
        return True

    # -- entry --------------------------------------------------------------------------
    def run(self) -> bool:
        self._say("Starting diagnosis...")
        if not self.fix_docker():
            self._say("Fix stopped: Docker itself is not usable. See messages above and the Log tab.")
            return False
        envs = [self.only] if self.only else self.store.all()
        if not envs:
            self._say("No ContainerQT Studio environments to check yet.")
        ok = True
        for spec in envs:
            ok = self.fix_env(spec) and ok
        if self.problems == 0:
            self._say("Everything looks healthy - nothing needed fixing.")
        else:
            self._say(f"Finished: {self.fixed} problem(s) repaired, {self.problems} found.")
        return ok


def fix_steps(client: DockerClient, store: EnvStore, only: EnvSpec | None = None) -> list[Step]:
    def go(out: Out) -> bool:
        return Fixer(client, store, out, only).run()

    return [Step("Diagnose and repair", fn=go)]
