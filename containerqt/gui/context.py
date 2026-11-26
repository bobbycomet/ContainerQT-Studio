"""Shared application state, background job thread and status poller."""
from __future__ import annotations

import threading
from typing import Callable

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from .. import logger
from ..client import DockerClient, DockerStatus
from ..models import EnvSpec, EnvStore
from ..profiles import ProfileStore
from ..templates import TemplateStore
from ..runner import Result, Runner, Step

log = logger.get()


class LogBridge(QObject):
    """Moves log records from any thread onto the GUI thread."""

    record = pyqtSignal(int, str)

    def __init__(self) -> None:
        super().__init__()
        logger.add_callback(lambda lvl, text: self.record.emit(lvl, text))


class JobThread(QThread):
    output = pyqtSignal(str)
    stepChanged = pyqtSignal(int, int, str)
    finishedJob = pyqtSignal(bool, str)

    def __init__(self, title: str, steps: list[Step], parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.steps = steps
        self.runner = Runner(self.output.emit, lambda i, n, t: self.stepChanged.emit(i, n, t))
        self.result: Result | None = None

    def cancel(self) -> None:
        self.runner.cancel()

    def run(self) -> None:
        log.info("Job started: %s", self.title)
        try:
            self.result = self.runner.run(self.steps)
        except Exception as exc:  # noqa: BLE001
            log.exception("Job crashed: %s", self.title)
            self.result = Result(False, f"Internal error: {exc}")
        if self.result.ok:
            log.info("Job finished: %s", self.title)
        else:
            log.error("Job did not complete: %s - %s", self.title, self.result.summary)
        self.finishedJob.emit(self.result.ok, self.result.summary)


class StatusPoller(QThread):
    statusReady = pyqtSignal(object, dict)  # DockerStatus, {env name: state}

    def __init__(self, client: DockerClient, store: EnvStore, parent=None) -> None:
        super().__init__(parent)
        self.client, self.store = client, store
        self._stop = threading.Event()
        self._poke = threading.Event()

    def poke(self) -> None:
        self._poke.set()

    def stop(self) -> None:
        self._stop.set()
        self._poke.set()

    def run(self) -> None:
        last_kind = None
        while not self._stop.is_set():
            st = self.client.status()
            states: dict[str, str] = {}
            if st.ok:
                for env in self.store.all():
                    states[env.name] = self.client.container_state(env.container)
            if st.kind != last_kind:
                log.info("Docker status: %s", st.describe())
                if not st.ok and st.detail:
                    log.warning("Docker detail: %s", st.detail)
                last_kind = st.kind
            self.statusReady.emit(st, states)
            self._poke.wait(4)
            self._poke.clear()


class AppContext(QObject):
    envsChanged = pyqtSignal()
    currentChanged = pyqtSignal()
    statusChanged = pyqtSignal(object)
    statesChanged = pyqtSignal(dict)
    busyChanged = pyqtSignal(bool, str)
    jobOutput = pyqtSignal(str)
    jobStep = pyqtSignal(int, int, str)
    jobFinished = pyqtSignal(bool, str)
    templatesChanged = pyqtSignal()
    useTemplate = pyqtSignal(str)
    profilesChanged = pyqtSignal()
    requestTab = pyqtSignal(str)  # ask the main window to show a tab by key
    requestExport = pyqtSignal(str)  # prefill export path

    def __init__(self) -> None:
        super().__init__()
        self.client = DockerClient()
        self.store = EnvStore()
        self.templates = TemplateStore()
        self.profiles = ProfileStore()
        self.status = DockerStatus("unknown")
        self.states: dict[str, str] = {}
        self.current_name: str | None = None
        self._job: JobThread | None = None
        envs = self.store.all()
        if envs:
            self.current_name = envs[0].name
        self.poller = StatusPoller(self.client, self.store, self)
        self.poller.statusReady.connect(self._on_poll)

    # -- polling -------------------------------------------------------------------
    def start(self) -> None:
        self.poller.start()

    def shutdown(self) -> None:
        self.poller.stop()
        self.poller.wait(3000)
        if self._job and self._job.isRunning():
            self._job.cancel()
            self._job.wait(3000)

    def refresh_now(self) -> None:
        self.poller.poke()

    def _on_poll(self, status: DockerStatus, states: dict) -> None:
        if status.kind != self.status.kind or status.version != self.status.version:
            self.status = status
            self.statusChanged.emit(status)
        if states != self.states:
            self.states = states
            self.statesChanged.emit(states)

    # -- environments --------------------------------------------------------------
    def current_env(self) -> EnvSpec | None:
        return self.store.get(self.current_name)

    def set_current(self, name: str | None) -> None:
        if name != self.current_name:
            self.current_name = name
            self.currentChanged.emit()

    def env_changed(self) -> None:
        """Call after the store changed (add/remove/edit)."""
        if self.current_name and not self.store.get(self.current_name):
            envs = self.store.all()
            self.current_name = envs[0].name if envs else None
            self.currentChanged.emit()
        self.envsChanged.emit()
        self.refresh_now()

    def state_of(self, env: EnvSpec | None) -> str:
        if not env:
            return "none"
        return self.states.get(env.name, "unknown")

    def is_running(self, env: EnvSpec | None) -> bool:
        return self.state_of(env) == "running"

    # -- jobs ---------------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._job is not None and self._job.isRunning()

    def start_job(self, title: str, steps: list[Step],
                  on_done: Callable[[bool, str], None] | None = None) -> bool:
        if self.busy:
            log.warning("Busy: refusing to start '%s' while another job runs", title)
            return False
        job = JobThread(title, steps, self)
        job.output.connect(self.jobOutput)
        job.stepChanged.connect(self.jobStep)

        def done(ok: bool, summary: str) -> None:
            self._job = None
            self.busyChanged.emit(False, "")
            self.jobFinished.emit(ok, summary)
            self.env_changed()
            if on_done:
                on_done(ok, summary)

        job.finishedJob.connect(done)
        job.finished.connect(job.deleteLater)
        self._job = job
        self.busyChanged.emit(True, title)
        self.jobOutput.emit(f"##### {title} #####")
        job.start()
        return True

    def cancel_job(self) -> None:
        if self._job:
            log.warning("Cancelling job: %s", self._job.title)
            self._job.cancel()
