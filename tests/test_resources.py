"""Hardware detection parsers, the resource advisor and the Docker limit translation (no GUI, no Docker)."""
from pathlib import Path

import pytest

from containerqt.docker import resource_limits as rl
from containerqt.hardware import cpu, memory
from containerqt.hardware import resource_advisor as ra
from containerqt.hardware.resource_advisor import CAUTION, MB, RECOMMENDED, HostResources

GB = MB


def host(threads=16, cores=8, ram_gb=32, avail_gb=26.0, util=0.05, load5=0.5, swap_gb=8, pressure=0.0, **kw):
    return HostResources(cores, threads, int(ram_gb * GB), int(avail_gb * GB), util, load5, int(swap_gb * GB),
                         int(swap_gb * GB), pressure, **kw)


# ------------------------------------------------------------------ detection parsers
def fake_sysfs(root: Path, cores: int, smt: int, packages: int = 1):
    n = 0
    for pkg in range(packages):
        for core in range(cores):
            for _ in range(smt):
                d = root / f"cpu{n}" / "topology"
                d.mkdir(parents=True)
                (d / "core_id").write_text(f"{core}\n")
                (d / "physical_package_id").write_text(f"{pkg}\n")
                n += 1
    return n


@pytest.mark.parametrize("cores,smt,pk", [(8, 2, 1), (4, 1, 1), (32, 2, 2), (6, 2, 1)])
def test_physical_cores_from_sysfs(tmp_path, cores, smt, pk):
    n = fake_sysfs(tmp_path, cores, smt, pk)
    topo = cpu.read_sysfs_topology(None, tmp_path)
    assert len(topo) == n and cpu.count_physical(topo) == cores * pk        # core ids repeat across packages


def test_affinity_limits_what_is_counted(tmp_path):
    fake_sysfs(tmp_path, 8, 2)
    topo = cpu.read_sysfs_topology({0, 1, 2, 3}, tmp_path)
    assert len(topo) == 4 and cpu.count_physical(topo) == 2


CPUINFO = """processor\t: 0\nphysical id\t: 0\ncore id\t\t: 0\nmodel name\t: Test CPU @ 3GHz\n
processor\t: 1\nphysical id\t: 0\ncore id\t\t: 0\nmodel name\t: Test CPU @ 3GHz\n
processor\t: 2\nphysical id\t: 0\ncore id\t\t: 1\nmodel name\t: Test CPU @ 3GHz\n
processor\t: 3\nphysical id\t: 0\ncore id\t\t: 1\nmodel name\t: Test CPU @ 3GHz\n"""


def test_cpuinfo_fallback_and_model():
    assert cpu.count_physical(cpu.parse_cpuinfo_topology(CPUINFO)) == 2
    assert cpu.parse_model(CPUINFO) == "Test CPU @ 3GHz"
    assert cpu.parse_cpuinfo_topology("processor : 0\nBogoMIPS : 1") == {}       # ARM: no topology -> caller falls back


def test_cpu_info_describes_itself():
    assert cpu.CpuInfo(8, 16).describe() == "8 physical cores · 16 logical threads" and cpu.CpuInfo(8, 16).smt
    assert cpu.CpuInfo(1, 1).describe() == "1 physical core · 1 logical thread" and not cpu.CpuInfo(1, 1).smt


def test_live_detection_is_sane():
    info = cpu.read_cpu_info()
    assert 1 <= info.physical_cores <= info.logical_threads
    m = memory.read_memory()
    assert m.total_mb > 0 and 0 <= m.available_mb <= m.total_mb


def test_utilization_math_and_loadavg():
    a = cpu.parse_proc_stat("cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 2 3 4")
    b = cpu.parse_proc_stat("cpu  200 0 200 1000 0 0 0 0 0 0")
    assert cpu.utilization_between(a, b) == pytest.approx(0.5)
    assert cpu.utilization_between(a, a) is None
    assert cpu.parse_loadavg("1.5 2.5 3.5 1/200 999") == (1.5, 2.5, 3.5) and cpu.parse_loadavg("") is None


MEMINFO = "MemTotal: 33554432 kB\nMemFree: 1000000 kB\nMemAvailable: 24117248 kB\nSwapTotal: 8388608 kB\n" \
          "SwapFree: 8000000 kB\nCommitted_AS: 20000000 kB\n"


def test_meminfo_and_pressure():
    m = memory.from_text(MEMINFO, "some avg10=12.50 avg60=3.00 avg300=1.00 total=1\nfull avg10=0.0")
    assert (m.total_mb, m.available_mb, m.swap_total_mb) == (32768, 23552, 8192)
    assert m.pressure_some_avg10 == 12.5 and m.used_mb == 9216 and m.committed_mb == 19531
    assert memory.from_text("MemTotal: 1024 kB\nMemFree: 512 kB\nCached: 256 kB\n").available_mb == 0   # old kernels: coarse


# ------------------------------------------------------------------ CPU suggestions
def test_8_core_16_thread_tiers_match_the_brief():
    opts = ra.cpu_options(host())
    assert [(o.value, o.label) for o in opts] == [
        (2, "Lightweight tasks"), (4, "General development"), (6, "Moderate builds"),
        (8, "Larger builds"), (12, "Heavy compilation"), (16, "Maximum allocation")]
    assert [o.level for o in opts] == [RECOMMENDED] * 5 + [CAUTION]            # 16 leaves nothing for the host
    assert "no headroom" in opts[-1].note


@pytest.mark.parametrize("threads,expected", [(1, [1]), (2, [1, 2]), (4, [1, 2, 3, 4]), (8, [1, 2, 3, 4, 6, 8]),
                                              (12, [2, 3, 5, 6, 9, 12]), (64, [8, 16, 24, 32, 48, 64]),
                                              (128, [16, 32, 48, 64, 96, 128])])
def test_tiers_adapt_to_any_core_count(threads, expected):
    assert [v for v, _ in ra.cpu_tier_values(threads)] == expected


def test_every_cpu_value_remains_selectable_and_a_spike_changes_nothing():
    quiet, spike = host(util=0.02, load5=0.3), host(util=0.97, load5=0.3)
    assert ra.cpu_options(quiet) == ra.cpu_options(spike)
    assert ra.cpu_recommended_max(quiet) == ra.cpu_recommended_max(spike) == 14
    assert any("busy right now" in n for n in ra.advise(spike).notes)             # but it is mentioned, as advice
    assert not any("busy right now" in n for n in ra.advise(quiet).notes)
    assert ra.advise(spike).cpu_absolute_max == 16                                  # nothing is ever withheld


def test_sustained_load_nudges_the_recommendation_by_a_bounded_amount():
    base = ra.cpu_recommended_max(host(load5=0.5))
    busy = ra.cpu_recommended_max(host(load5=12.0))
    insane = ra.cpu_recommended_max(host(load5=500.0))
    assert busy < base and insane >= 16 - 2 - 4 and insane == ra.cpu_recommended_max(host(load5=16.0))  # <= 25% of 16
    assert ra.cpu_recommended_max(host(threads=1, cores=1, load5=9)) == 1


def test_reserve_scales_with_the_machine():
    assert [ra.cpu_reserve(t) for t in (1, 2, 4, 8, 16, 64)] == [0, 1, 1, 1, 2, 8]


# ------------------------------------------------------------------ memory suggestions
def test_32gb_machine_ladder_and_labels():
    opts = ra.mem_options(host(avail_gb=28))
    assert [o.value / GB for o in opts] == [1, 2, 3, 4, 6, 8, 12, 16, 24, 32]
    labels = {o.value // GB: o.label for o in opts}
    assert labels[2] == "Minimal environment" and labels[4] == "General development"
    assert labels[8] == "Desktop + development" and labels[12] == "Larger builds"
    assert labels[16] == "Heavy development" and labels[24] == "High-memory workloads"
    assert all(o.level == RECOMMENDED for o in opts if o.value <= 24 * GB)
    assert opts[-1].level == CAUTION and "host" in opts[-1].note                  # all 32 GB is allowed, but flagged


def test_available_memory_not_installed_memory_drives_the_recommendation():
    roomy, tight = host(avail_gb=28), host(avail_gb=6)
    assert ra.mem_recommended_max_mb(roomy) > ra.mem_recommended_max_mb(tight)
    assert ra.mem_recommended_max_mb(tight) <= 6 * GB
    # ...but both offer exactly the same choices: availability moves the line, it never removes an option
    assert [o.value for o in ra.mem_options(roomy)] == [o.value for o in ra.mem_options(tight)]
    assert ra.mem_recommended_max_mb(tight) % 512 == 0


def test_other_running_environments_reduce_the_recommendation():
    free, shared = host(avail_gb=28), host(avail_gb=28, other_envs_mem_mb=12 * GB, other_envs_cpus=6)
    assert ra.mem_recommended_max_mb(shared) <= ra.mem_recommended_max_mb(free) - 8 * GB
    assert any("Other running ContainerQT Studio" in n for n in ra.advise(shared).notes)


def test_small_machine_still_gets_usable_advice():
    h = host(threads=4, cores=2, ram_gb=4, avail_gb=2.5, swap_gb=0)
    a = ra.advise(h)
    assert 512 <= a.mem_recommended_max_mb <= 2 * GB and a.cpu_recommended_max == 3
    assert [o.value / GB for o in a.mem_options] == [1, 2, 3, 4]
    assert any("no swap" in n for n in a.notes)
    tiny = ra.advise(host(threads=1, cores=1, ram_gb=0.5, avail_gb=0.3))
    assert tiny.mem_options and tiny.cpu_options[0].value == 1


def test_pressure_is_advice_only():
    a = ra.advise(host(pressure=40))
    assert any("memory pressure" in n for n in a.notes)
    assert a.mem_options == ra.advise(host(pressure=0)).mem_options


# ------------------------------------------------------------------ presets
def test_presets_are_derived_from_the_hardware():
    p = ra.advise(host(avail_gb=30)).presets
    assert list(p) == ["lightweight", "balanced", "development", "heavy"]
    assert [(p[k].cpus, p[k].memory_mb // GB) for k in p] == [(2, 2), (4, 4), (6, 8), (12, 16)]
    q = ra.advise(host(threads=64, cores=32, ram_gb=128, avail_gb=110)).presets
    assert [q[k].cpus for k in q] == [8, 16, 24, 48]                           # same rule, bigger machine
    small = ra.advise(host(threads=4, cores=4, ram_gb=8, avail_gb=5)).presets
    assert small["heavy"].cpus <= 3 and small["heavy"].memory_mb <= ra.mem_recommended_max_mb(host(threads=4, cores=4, ram_gb=8, avail_gb=5))
    assert small["heavy"].note                                                  # says it was limited, and why


def test_desktop_minimum_raises_small_presets():
    p = ra.advise(host(), desktop_min_mb=ra.DESKTOP_MIN_MB["kde"]).presets
    assert p["lightweight"].memory_mb == 4 * GB
    assert ra.advise(host()).presets["lightweight"].memory_mb == 2 * GB


def test_assess_is_advisory_only():
    h = host(avail_gb=20)
    assert ra.assess(h, 4, 4 * GB) == []
    msgs = dict(ra.assess(h, 16, 30 * GB, desktop_min_mb=4 * GB))
    assert CAUTION in msgs                                                       # warnings, never errors
    texts = " ".join(t for _, t in ra.assess(h, 16, 30 * GB))
    assert "recommended maximum" in texts and "not a reservation" in texts
    assert "at least 4 GB" in " ".join(t for _, t in ra.assess(h, 2, 2 * GB, desktop_min_mb=4 * GB))
    assert "cap it at 16" in ra.assess(h, 40, 0)[0][1]


# ------------------------------------------------------------------ Docker translation
def test_run_args():
    assert rl.run_args(rl.Limits()) == []
    a = rl.run_args(rl.Limits(4, 8192))
    assert a == ["--cpus", "4", "--memory", "8192m", "--memory-reservation", "6144m"]
    assert rl.run_args(rl.Limits(1.5, 0)) == ["--cpus", "1.5"]


def test_clamp_never_asks_docker_for_the_impossible():
    assert rl.clamp(rl.Limits(40, 999999), 16, 32768) == rl.Limits(16.0, 32768)
    assert rl.clamp(rl.Limits(0, 1), 16, 32768).memory_mb == rl.MIN_MEMORY_MB
    assert rl.clamp(rl.Limits(0, 0), 16, 32768) == rl.Limits(0.0, 0)


def test_update_args_move_memory_and_swap_together():
    a = rl.update_args("c", rl.Limits(2, 4096))
    assert a[:3] == ["update", "--cpus", "2"] and "--memory-swap" in a and a[-1] == "c"
    off = rl.update_args("c", rl.Limits())
    assert off[off.index("--cpus") + 1] == "0" and off[off.index("--memory") + 1] == "0" and "-1" in off


def test_shm_scales_down_for_small_containers():
    assert rl.shm_size(rl.Limits()) == "1g" and rl.shm_size(rl.Limits(0, 8192)) == "1g"
    assert rl.shm_size(rl.Limits(0, 2048)) == "512m" and rl.shm_size(rl.Limits(0, 128)) == "64m"
    assert "4 CPU quota" in rl.describe(rl.Limits(4, 8192)) and "8.0 GB" in rl.describe(rl.Limits(4, 8192))
