"""CPU topology and load.

Physical cores come from the kernel's topology files (works on x86 and ARM), logical threads from the CPUs this
process may actually use. Load is read from /proc/stat (instantaneous, advisory) and /proc/loadavg (smoothed).
All parsers take text so they can be tested with made-up machines.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CpuInfo:
    physical_cores: int
    logical_threads: int
    model: str = ""

    @property
    def smt(self) -> bool:
        return self.logical_threads > self.physical_cores

    def describe(self) -> str:
        return f"{self.physical_cores} physical core{'s' if self.physical_cores != 1 else ''} · " \
               f"{self.logical_threads} logical thread{'s' if self.logical_threads != 1 else ''}"


@dataclass(frozen=True)
class CpuLoad:
    utilization: float | None  # 0..1 over a short sample; None when it could not be measured
    load1: float | None = None
    load5: float | None = None
    load15: float | None = None


def parse_model(cpuinfo: str) -> str:
    m = re.search(r"^(?:model name|Model|Hardware)\s*:\s*(.+)$", cpuinfo, re.M)
    return m.group(1).strip() if m else ""


def count_physical(topology: dict[int, tuple[str, str]]) -> int:
    """topology: {cpu number: (package id, core id)} -> number of distinct physical cores."""
    return len({v for v in topology.values()})


def parse_cpuinfo_topology(cpuinfo: str) -> dict[int, tuple[str, str]]:
    """Fallback for kernels without sysfs topology: pair up 'physical id' and 'core id' per processor block."""
    topo: dict[int, tuple[str, str]] = {}
    for block in re.split(r"\n\s*\n", cpuinfo.strip()):
        proc = re.search(r"^processor\s*:\s*(\d+)", block, re.M)
        core = re.search(r"^core id\s*:\s*(\d+)", block, re.M)
        if not proc or not core:
            continue
        pkg = re.search(r"^physical id\s*:\s*(\d+)", block, re.M)
        topo[int(proc.group(1))] = (pkg.group(1) if pkg else "0", core.group(1))
    return topo


def read_sysfs_topology(allowed: set[int] | None = None, root: Path = Path("/sys/devices/system/cpu")
                        ) -> dict[int, tuple[str, str]]:
    topo: dict[int, tuple[str, str]] = {}
    try:
        entries = list(root.glob("cpu[0-9]*"))
    except OSError:
        return topo
    for d in entries:
        n = int(d.name[3:])
        if allowed is not None and n not in allowed:
            continue
        try:
            core = (d / "topology" / "core_id").read_text().strip()
            pkg = (d / "topology" / "physical_package_id").read_text().strip()
        except OSError:
            continue
        topo[n] = (pkg, core)
    return topo


def usable_cpus() -> set[int]:
    try:
        return set(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return set(range(os.cpu_count() or 1))


def read_cpu_info() -> CpuInfo:
    cpus = usable_cpus()
    logical = max(1, len(cpus))
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(errors="replace")
    except OSError:
        cpuinfo = ""
    topo = read_sysfs_topology(cpus) or parse_cpuinfo_topology(cpuinfo)
    physical = count_physical(topo) if topo else logical
    return CpuInfo(physical_cores=max(1, min(physical, logical)), logical_threads=logical, model=parse_model(cpuinfo))


# ---------------------------------------------------------------------------- load
def parse_proc_stat(text: str) -> tuple[int, int] | None:
    """(busy jiffies, total jiffies) from the aggregate 'cpu' line."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            v = [int(x) for x in line.split()[1:]]
            if len(v) < 4:
                return None
            idle = v[3] + (v[4] if len(v) > 4 else 0)  # idle + iowait
            total = sum(v[:8]) if len(v) >= 8 else sum(v)
            return total - idle, total
    return None


def utilization_between(a: tuple[int, int], b: tuple[int, int]) -> float | None:
    busy, total = b[0] - a[0], b[1] - a[1]
    if total <= 0:
        return None
    return max(0.0, min(1.0, busy / total))


def parse_loadavg(text: str) -> tuple[float, float, float] | None:
    parts = text.split()
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (IndexError, ValueError):
        return None


def read_cpu_load(interval: float = 0.2) -> CpuLoad:
    util = None
    try:
        a = parse_proc_stat(Path("/proc/stat").read_text())
        time.sleep(interval)
        b = parse_proc_stat(Path("/proc/stat").read_text())
        if a and b:
            util = utilization_between(a, b)
    except OSError:
        pass
    la = None
    try:
        la = parse_loadavg(Path("/proc/loadavg").read_text())
    except OSError:
        pass
    return CpuLoad(util, *(la if la else (None, None, None)))
