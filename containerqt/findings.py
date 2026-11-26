"""A small shared result type for compatibility and health checks."""
from __future__ import annotations

from dataclasses import dataclass

ERROR, WARN, INFO, OK = "error", "warn", "info", "ok"
_RANK = {ERROR: 0, WARN: 1, INFO: 2, OK: 3}


@dataclass(frozen=True)
class Finding:
    level: str          # error | warn | info | ok
    code: str           # stable id, e.g. "snap-docker-storage"
    title: str
    detail: str = ""
    fix: str = ""       # action key the GUI can offer: start | restart | restart-desktop | rebuild | fix | ""
    fix_label: str = ""

    @property
    def rank(self) -> int:
        return _RANK.get(self.level, 9)


def sort_findings(items: list[Finding]) -> list[Finding]:
    return sorted(items, key=lambda f: f.rank)


def worst(items: list[Finding]) -> str:
    return min((f.level for f in items), key=lambda lv: _RANK.get(lv, 9), default=OK)


def has_errors(items: list[Finding]) -> bool:
    return any(f.level == ERROR for f in items)
