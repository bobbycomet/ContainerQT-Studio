"""Translates the allocation a user picked into Docker arguments. Knows nothing about hardware detection.

`--cpus N` is a CFS *quota* (N CPU-seconds per second, spread over all cores): it caps how much CPU time the
container may use and reserves nothing. `--memory` is a hard cap (a container above it has processes killed);
`--memory-reservation` is a soft limit Docker enforces first when the host is short of memory.
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_MEMORY_MB = 6          # Docker refuses less
SOFT_RESERVATION = 0.75    # soft limit = 75% of the hard limit


@dataclass(frozen=True)
class Limits:
    cpus: float = 0.0       # 0 = unlimited
    memory_mb: int = 0      # 0 = unlimited

    @property
    def limited(self) -> bool:
        return bool(self.cpus or self.memory_mb)


def clamp(limits: Limits, host_threads: int | None = None, host_mem_mb: int | None = None) -> Limits:
    """Docker rejects a CPU count above the host's; clamp rather than fail at start."""
    cpus = limits.cpus
    if cpus and host_threads:
        cpus = min(cpus, float(host_threads))
    mem = limits.memory_mb
    if mem:
        mem = max(MIN_MEMORY_MB, mem)
        if host_mem_mb:
            mem = min(mem, host_mem_mb)
    return Limits(round(cpus, 2) if cpus else 0.0, int(mem))


def run_args(limits: Limits) -> list[str]:
    a: list[str] = []
    if limits.cpus and limits.cpus > 0:
        a += ["--cpus", f"{limits.cpus:g}"]
    if limits.memory_mb and limits.memory_mb > 0:
        a += ["--memory", f"{limits.memory_mb}m",
              "--memory-reservation", f"{max(MIN_MEMORY_MB, int(limits.memory_mb * SOFT_RESERVATION))}m"]
    return a


def update_args(container: str, limits: Limits) -> list[str]:
    """`docker update` arguments to change a (running or stopped) container's limits in place.

    Memory and memory-swap must move together when lowering the limit, so swap is pinned to the memory limit.
    0 means "remove the limit" to `docker update`.
    """
    mem = limits.memory_mb
    a = ["update", "--cpus", f"{limits.cpus:g}" if limits.cpus else "0"]
    if mem:
        a += ["--memory", f"{mem}m", "--memory-swap", f"{mem}m",
              "--memory-reservation", f"{max(MIN_MEMORY_MB, int(mem * SOFT_RESERVATION))}m"]
    else:
        a += ["--memory", "0", "--memory-swap", "-1", "--memory-reservation", "0"]
    return a + [container]


def shm_size(limits: Limits) -> str:
    """/dev/shm (Chromium, Qt apps need plenty): 1 GB, less when the whole container is capped lower."""
    if limits.memory_mb and limits.memory_mb < 4096:
        return f"{max(64, limits.memory_mb // 4)}m"
    return "1g"


def describe(limits: Limits) -> str:
    cpu = f"{limits.cpus:g} CPU quota" if limits.cpus else "all CPUs"
    mem = f"{limits.memory_mb / 1024:.1f} GB" if limits.memory_mb else "no memory limit"
    return f"{cpu}, {mem}"
