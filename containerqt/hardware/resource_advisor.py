"""Turns detected hardware into allocation suggestions. No GUI, no Docker: plain data in, plain data out.

Principles (from the product brief):
* ContainerQT explains what is reasonable; it never decides what a developer may use. Every value from 1 thread /
  512 MB up to the machine's total stays selectable. Anything above the *recommended maximum* is marked
  "caution" with the reason, not removed.
* The recommended maximum rests on static safety margins (a reserve for the host OS). Momentary CPU
  utilisation is advisory only: a spike never shrinks the list. Only the smoothed 5-minute load average and
  memory the kernel reports as available nudge the recommendation, by a bounded amount.
* Docker CPU limits are a CPU *quota* (time share), not reserved cores - the texts say so.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .cpu import CpuInfo, CpuLoad
from .memory import MemInfo

MB = 1024
CAUTION, RECOMMENDED = "caution", "recommended"

# (fraction of the machine's threads, label). Dedupe keeps the first label for a given value.
CPU_TIERS = ((1 / 8, "Lightweight tasks"), (1 / 4, "General development"), (3 / 8, "Moderate builds"),
             (1 / 2, "Larger builds"), (3 / 4, "Heavy compilation"), (1.0, "Maximum allocation"))
MEM_LADDER_GB = (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024)
PRESETS = ("lightweight", "balanced", "development", "heavy", "custom")
PRESET_LABEL = {"lightweight": "Lightweight", "balanced": "Balanced", "development": "Development",
                "heavy": "Heavy builds", "custom": "Custom"}
# rough minimum RAM (MB) for a comfortable desktop session
DESKTOP_MIN_MB = {"xfce": 2048, "kde": 4096, "gnome": 4096, "cinnamon": 3072, "mate": 2048, "lxqt": 1536,
                  "i3": 1024, "openbox": 1024, "sway": 1024, "labwc": 1024}


def _round(x: float) -> int:
    return int(x + 0.5)


def mem_label(gb: float) -> str:
    if gb <= 1:
        return "Shell only"
    if gb <= 2:
        return "Minimal environment"
    if gb <= 4:
        return "General development"
    if gb <= 6:
        return "Development + light desktop"
    if gb <= 8:
        return "Desktop + development"
    if gb <= 12:
        return "Larger builds"
    if gb <= 16:
        return "Heavy development"
    return "High-memory workloads"


@dataclass(frozen=True)
class HostResources:
    physical_cores: int
    logical_threads: int
    mem_total_mb: int
    mem_available_mb: int
    cpu_utilization: float | None = None      # instantaneous, advisory
    load5: float | None = None                # smoothed (5 min), used for a bounded nudge
    swap_total_mb: int = 0
    swap_free_mb: int = 0
    mem_pressure: float | None = None         # PSI some avg10 (%), advisory
    other_envs_cpus: float = 0.0              # CPU quota promised to other running ContainerQT environments
    other_envs_mem_mb: int = 0                # memory limits of other running ContainerQT environments
    cpu_model: str = ""

    @classmethod
    def from_parts(cls, cpu: CpuInfo, load: CpuLoad, mem: MemInfo, other_cpus: float = 0.0,
                   other_mem_mb: int = 0) -> "HostResources":
        return cls(cpu.physical_cores, cpu.logical_threads, mem.total_mb, mem.available_mb, load.utilization,
                   load.load5, mem.swap_total_mb, mem.swap_free_mb, mem.pressure_some_avg10, other_cpus,
                   other_mem_mb, cpu.model)


@dataclass(frozen=True)
class Option:
    value: float
    label: str
    level: str = RECOMMENDED  # recommended | caution
    note: str = ""


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    cpus: int
    memory_mb: int
    note: str = ""


@dataclass
class Advice:
    host: HostResources
    cpu_options: list[Option]
    mem_options: list[Option]
    cpu_recommended_max: int
    cpu_absolute_max: int
    mem_recommended_max_mb: int
    mem_absolute_max_mb: int
    reserve_threads: int
    reserve_mb: int
    presets: dict[str, Preset]
    notes: list[str] = field(default_factory=list)  # advisory messages about the host's current state
    cpu_free_threads: float | None = None


# --------------------------------------------------------------------------------------------- CPU
def cpu_reserve(threads: int) -> int:
    """Static margin kept for the host OS: ~1 in 8 threads, at least 1 (when there is more than 1)."""
    return 0 if threads <= 1 else max(1, _round(threads / 8))


def cpu_recommended_max(host: HostResources) -> int:
    t = host.logical_threads
    rec = t - cpu_reserve(t)
    # Bounded nudge from *sustained* load only (5-minute average): at most a quarter of the machine.
    if host.load5 is not None and t > 1:
        sustained = max(0.0, min(float(t), host.load5) - 0.25 * t)
        rec -= min(int(0.25 * t), int(sustained * 0.5))
    return max(1, rec)


def cpu_tier_values(threads: int) -> list[tuple[int, str]]:
    seen: dict[int, str] = {}
    for frac, label in CPU_TIERS:
        v = max(1, min(threads, _round(threads * frac)))
        seen.setdefault(v, label)
    return sorted(seen.items())


def cpu_options(host: HostResources, rec_max: int | None = None) -> list[Option]:
    t = host.logical_threads
    rec_max = cpu_recommended_max(host) if rec_max is None else rec_max
    out = []
    for v, label in cpu_tier_values(t):
        if v <= rec_max:
            out.append(Option(v, label))
        else:
            left = t - v
            out.append(Option(v, label, CAUTION, f"Leaves {left} thread{'s' if left != 1 else ''} for the host"
                              if left > 0 else "Leaves no headroom for the host while the build runs flat out"))
    return out


# ------------------------------------------------------------------------------------------ memory
def mem_reserve_mb(total_mb: int) -> int:
    return min(8 * MB, max(2 * MB, _round(total_mb * 0.15)))


def mem_recommended_max_mb(host: HostResources) -> int:
    total = host.mem_total_mb
    by_installed = total - mem_reserve_mb(total) - host.other_envs_mem_mb
    margin = max(1 * MB, _round(total * 0.05))
    by_available = host.mem_available_mb - margin
    rec = min(by_installed, by_available) if host.mem_available_mb else by_installed
    rec = (rec // 512) * 512
    return int(max(512, min(rec, total)))


def mem_options(host: HostResources, rec_max_mb: int | None = None) -> list[Option]:
    total = host.mem_total_mb
    rec_max_mb = mem_recommended_max_mb(host) if rec_max_mb is None else rec_max_mb
    out = []
    for gb in MEM_LADDER_GB:
        mb = gb * MB
        if mb > total:
            break
        label = mem_label(gb)
        if mb <= rec_max_mb:
            out.append(Option(mb, label))
        else:
            left = (total - mb) / MB
            out.append(Option(mb, label, CAUTION, f"Leaves {left:.1f} GB of installed RAM for the host"))
    if not out:  # tiny machines
        out.append(Option(max(512, total), "Whole machine", CAUTION, "Very little RAM"))
    return out


# --------------------------------------------------------------------------------------------- notes
def host_notes(host: HostResources) -> list[str]:
    notes: list[str] = []
    t = host.logical_threads
    if host.cpu_utilization is not None and host.cpu_utilization >= 0.6:
        notes.append(f"The CPU is busy right now (~{host.cpu_utilization * 100:.0f}%). This is only a snapshot; "
                     "the options above are not reduced for it.")
    if host.load5 is not None and host.load5 > 0.5 * t:
        notes.append(f"Sustained load is high (5-minute load {host.load5:.1f} on {t} threads), so the recommended "
                     "maximum is a little lower than usual.")
    if host.mem_pressure is not None and host.mem_pressure >= 10:
        notes.append(f"The host is under memory pressure right now ({host.mem_pressure:.0f}% stall time); "
                     "close other programs before starting a large environment.")
    if host.swap_total_mb == 0:
        notes.append("This machine has no swap: a process that exceeds the memory limit is killed rather than "
                     "slowed down.")
    if host.other_envs_mem_mb or host.other_envs_cpus:
        notes.append(f"Other running ContainerQT Studio environments already have up to {host.other_envs_cpus:g} CPUs and "
                     f"{host.other_envs_mem_mb / MB:.1f} GB promised; the memory recommendation accounts for it.")
    return notes


# -------------------------------------------------------------------------------------------- presets
def make_presets(host: HostResources, cpu_rec: int, mem_rec_mb: int, desktop_min_mb: int = 0) -> dict[str, Preset]:
    """Presets derived from the hardware (fractions of threads, RAM tiers), never fixed numbers."""
    t = host.logical_threads
    spec = {  # key: (thread fraction, wanted GB)
        "lightweight": (1 / 8, 2), "balanced": (1 / 4, 4), "development": (3 / 8, 8), "heavy": (3 / 4, 16),
    }
    out: dict[str, Preset] = {}
    for key, (frac, gb) in spec.items():
        c = max(1, _round(t * frac))
        m = max(gb * MB, desktop_min_mb)
        notes = []
        if c > cpu_rec:
            c = cpu_rec
            notes.append("threads limited to the recommended maximum")
        if m > mem_rec_mb:
            m = max(512, mem_rec_mb)
            notes.append("memory limited to the recommended maximum")
        out[key] = Preset(key, PRESET_LABEL[key], int(c), int(m), "; ".join(notes))
    return out


def advise(host: HostResources, desktop_min_mb: int = 0) -> Advice:
    cpu_rec, mem_rec = cpu_recommended_max(host), mem_recommended_max_mb(host)
    free = None
    if host.cpu_utilization is not None:
        free = max(0.0, host.logical_threads * (1 - host.cpu_utilization))
    return Advice(
        host=host, cpu_options=cpu_options(host, cpu_rec), mem_options=mem_options(host, mem_rec),
        cpu_recommended_max=cpu_rec, cpu_absolute_max=host.logical_threads, mem_recommended_max_mb=mem_rec,
        mem_absolute_max_mb=host.mem_total_mb, reserve_threads=cpu_reserve(host.logical_threads),
        reserve_mb=mem_reserve_mb(host.mem_total_mb), presets=make_presets(host, cpu_rec, mem_rec, desktop_min_mb),
        notes=host_notes(host), cpu_free_threads=free,
    )


def assess(host: HostResources, cpus: float, memory_mb: int, desktop_min_mb: int = 0) -> list[tuple[str, str]]:
    """Advisory messages for a chosen allocation: [(level, text)], level in info|caution. Never an error."""
    cpu_rec, mem_rec = cpu_recommended_max(host), mem_recommended_max_mb(host)
    out: list[tuple[str, str]] = []
    if cpus and cpus > host.logical_threads:
        out.append((CAUTION, f"{cpus:g} CPUs is more than this machine's {host.logical_threads} threads; Docker will "
                             f"cap it at {host.logical_threads}."))
    elif cpus and cpus > cpu_rec:
        out.append((CAUTION, f"{cpus:g} threads is above the recommended maximum of {cpu_rec}. It works - a quota "
                             "is not a reservation - but a flat-out build can make the rest of the desktop sluggish."))
    if memory_mb and memory_mb > host.mem_total_mb:
        out.append((CAUTION, f"{memory_mb / MB:.1f} GB is more than the {host.mem_total_mb / MB:.1f} GB installed."))
    elif memory_mb and memory_mb > mem_rec:
        out.append((CAUTION, f"{memory_mb / MB:.1f} GB is above the recommended maximum of {mem_rec / MB:.1f} GB "
                             f"(about {host.mem_available_mb / MB:.1f} GB is available now). The host may start "
                             "swapping or the kernel may kill processes."))
    if memory_mb and desktop_min_mb and memory_mb < desktop_min_mb:
        out.append((CAUTION, f"A desktop session usually wants at least {desktop_min_mb / MB:.0f} GB; "
                             f"{memory_mb / MB:.1f} GB may feel slow or run out of memory."))
    return out
