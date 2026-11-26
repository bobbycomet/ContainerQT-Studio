"""RAM, swap and memory pressure (from /proc/meminfo and, where the kernel has it, /proc/pressure/memory)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MemInfo:
    total_mb: int
    available_mb: int  # the kernel's estimate: free + reclaimable cache (what can be used without swapping)
    swap_total_mb: int = 0
    swap_free_mb: int = 0
    committed_mb: int | None = None  # Committed_AS: memory promised to processes (may exceed physical RAM)
    pressure_some_avg10: float | None = None  # % of the last 10 s some task stalled waiting for memory

    @property
    def used_mb(self) -> int:
        return max(0, self.total_mb - self.available_mb)


def parse_meminfo(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        m = re.match(r"(\w+(?:\(\w+\))?):\s+(\d+)\s*(kB)?", line)
        if m:
            out[m.group(1)] = int(m.group(2))  # kB
    return out


def parse_pressure(text: str) -> float | None:
    m = re.search(r"^some .*?avg10=([\d.]+)", text, re.M)
    return float(m.group(1)) if m else None


def from_text(meminfo: str, pressure: str = "") -> MemInfo:
    k = parse_meminfo(meminfo)
    total = k.get("MemTotal", 0)
    avail = k.get("MemAvailable", k.get("MemFree", 0) + k.get("Cached", 0) + k.get("Buffers", 0))
    return MemInfo(
        total_mb=total // 1024, available_mb=min(avail, total) // 1024,
        swap_total_mb=k.get("SwapTotal", 0) // 1024, swap_free_mb=k.get("SwapFree", 0) // 1024,
        committed_mb=(k["Committed_AS"] // 1024) if "Committed_AS" in k else None,
        pressure_some_avg10=parse_pressure(pressure) if pressure else None,
    )


def read_memory() -> MemInfo:
    try:
        text = Path("/proc/meminfo").read_text()
    except OSError:
        return MemInfo(0, 0)
    try:
        psi = Path("/proc/pressure/memory").read_text()
    except OSError:
        psi = ""
    return from_text(text, psi)
