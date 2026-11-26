"""Sequential step runner (no Qt) used for create/build/export/fix jobs.

A Step is either an external command (argv) or a Python callable fn(out) -> bool.
Output lines are sent to `out` and also written to the log file at DEBUG level.
"""
from __future__ import annotations

import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from . import logger, paths

log = logger.get()
Out = Callable[[str], None]


@dataclass
class Step:
    title: str
    argv: list[str] | None = None
    fn: Callable[[Out], bool] | None = None
    stdin: str | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None
    ignore_fail: bool = False


@dataclass
class Result:
    ok: bool
    summary: str
    failed_step: str = ""
    tail: list[str] = field(default_factory=list)


class Runner:
    def __init__(self, out: Out, on_step: Callable[[int, int, str], None] | None = None) -> None:
        self._out = out
        self._on_step = on_step
        self._cancel = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._tail: deque[str] = deque(maxlen=20)

    # -- control ---------------------------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # -- output ------------------------------------------------------------------
    def emit(self, line: str) -> None:
        self._tail.append(line)
        log.debug(line)
        self._out(line)

    # -- execution ---------------------------------------------------------------
    def _run_cmd(self, step: Step) -> int:
        assert step.argv
        self.emit("$ " + " ".join(step.argv))
        try:
            self._proc = subprocess.Popen(
                step.argv,
                stdin=subprocess.PIPE if step.stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, errors="replace",
                cwd=step.cwd, env=paths.host_env(step.env),
            )
        except FileNotFoundError:
            self.emit(f"command not found: {step.argv[0]}")
            return 127
        except OSError as exc:
            self.emit(f"could not start {step.argv[0]}: {exc}")
            return 126
        proc = self._proc
        if step.stdin is not None and proc.stdin:
            try:
                proc.stdin.write(step.stdin)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        assert proc.stdout is not None
        for raw in proc.stdout:
            self.emit(raw.rstrip("\r\n"))
        return proc.wait()

    def run(self, steps: list[Step]) -> Result:
        total = len(steps)
        for i, step in enumerate(steps, 1):
            if self.cancelled:
                return Result(False, "Cancelled")
            if self._on_step:
                self._on_step(i, total, step.title)
            log.info("[%d/%d] %s", i, total, step.title)
            self._out(f"=== [{i}/{total}] {step.title} ===")
            self._tail.clear()
            try:
                if step.fn is not None:
                    ok = bool(step.fn(self.emit))
                    rc = 0 if ok else 1
                else:
                    rc = self._run_cmd(step)
            except Exception as exc:  # noqa: BLE001 - surface any step error in the log
                log.exception("Step crashed: %s", step.title)
                self.emit(f"internal error: {exc}")
                rc = 1
            if self.cancelled:
                log.warning("Cancelled during: %s", step.title)
                return Result(False, "Cancelled", step.title)
            if rc != 0:
                tail = list(self._tail)
                if step.ignore_fail:
                    log.warning("Step failed but continuing (exit %s): %s", rc, step.title)
                    continue
                log.error("FAILED (exit %s): %s", rc, step.title)
                for ln in tail[-12:]:
                    log.error("    %s", ln)
                return Result(False, f"Failed: {step.title}", step.title, tail)
        return Result(True, "Done")
