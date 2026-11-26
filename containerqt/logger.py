"""Central logging: rotating log file + callbacks for the GUI log page.

Levels used:
  DEBUG   raw command output (kept in the file so failures can be diagnosed)
  INFO    steps / actions
  WARNING recoverable problems
  ERROR   failures
"""
from __future__ import annotations

import logging
import logging.handlers
import threading
from typing import Callable

from . import paths

_LOGGER_NAME = "dockerqt"
_lock = threading.Lock()
_callbacks: list[Callable[[int, str], None]] = []
_configured = False


class _CallbackHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
        try:
            text = self.format(record)
        except Exception:
            return
        for cb in list(_callbacks):
            try:
                cb(record.levelno, text)
            except Exception:
                pass


def setup(debug: bool = False) -> logging.Logger:
    global _configured
    log = logging.getLogger(_LOGGER_NAME)
    with _lock:
        if _configured:
            return log
        log.setLevel(logging.DEBUG)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        try:
            fh = logging.handlers.RotatingFileHandler(
                paths.log_file(), maxBytes=2_000_000, backupCount=5, encoding="utf-8"
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
            log.addHandler(fh)
        except OSError:
            pass
        cb = _CallbackHandler()
        cb.setLevel(logging.DEBUG)
        cb.setFormatter(fmt)
        log.addHandler(cb)
        if debug:
            sh = logging.StreamHandler()
            sh.setFormatter(fmt)
            log.addHandler(sh)
        _configured = True
    return log


def get() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def add_callback(cb: Callable[[int, str], None]) -> None:
    _callbacks.append(cb)


def read_tail(max_lines: int = 400) -> list[str]:
    """Return the last lines of the persistent log file (for the log page)."""
    try:
        with open(paths.log_file(), "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    return [ln.rstrip("\n") for ln in lines[-max_lines:]]
