"""Templates, build profiles, compatibility checks and health checks (no Docker, no GUI)."""
import json

import pytest

from containerqt import compat, health, toolkits
from containerqt.findings import ERROR, OK, WARN, has_errors
from containerqt.hardware import resource_advisor as ra
from containerqt.models import EnvSpec
from containerqt.profiles import BuildProfile, ProfileStore, parse_env_text
from containerqt.templates import BUILTIN, EnvTemplate, TemplateStore


# ------------------------------------------------------------------ templates
def test_builtins_are_valid_and_include_plasma_variants():
    names = [t.name for t in BUILTIN]
    assert all(not t.problems() for t in BUILTIN)
    assert any("Plasma 5" in n and "Qt5" in n for n in names)
    assert any("Plasma 6" in n and "Qt6" in n for n in names)
    assert len(names) == len(set(names))


def test_template_roundtrip_store(tmp_path):
    st = TemplateStore(tmp_path)
    base = BUILTIN[0]
    spec = base.to_spec("My env")
    t = EnvTemplate.from_spec(spec, "Ubuntu-24.04-KDE-Qt6", "known good")
    st.save(t)
    assert st.get("Ubuntu-24.04-KDE-Qt6").description == "known good"
    with pytest.raises(FileExistsError):
        st.save(t)
    st.save(t, overwrite=True)
    out = st.export_to(t, tmp_path.parent / (tmp_path.name + "-x.json"))
    st2 = TemplateStore(tmp_path / "other")
    assert st2.import_from(out).name == t.name
    assert st.delete(t.name) and not st.delete(t.name)


def test_damaged_and_foreign_files_ignored(tmp_path):
    st = TemplateStore(tmp_path)
    (tmp_path / "bad.json").write_text("{nope")
    (tmp_path / "other.json").write_text(json.dumps({"kind": "something-else"}))
    assert st.user() == []
    assert st.all()  # built-ins still there
    with pytest.raises(ValueError):
        st.import_from(tmp_path / "other.json")


def test_template_name_rules(tmp_path):
    t = EnvTemplate.from_spec(BUILTIN[0].to_spec("x"), "a/b")
    with pytest.raises(ValueError):
        TemplateStore(tmp_path).save(t)


def test_to_spec_applies_overrides():
    s = BUILTIN[0].to_spec("n", cpus=3, memory_mb=4096)
    assert (s.cpus, s.memory_mb, s.template) == (3, 4096, BUILTIN[0].name)


def test_toolkit_packages_validate():
    assert toolkits.clean_packages("git  cmake") == "git cmake"
    for bad in ("git; rm -rf /", "$(x)", "a b|c"):
        with pytest.raises(ValueError):
            toolkits.clean_packages(bad)


# ------------------------------------------------------------------ profiles
def test_profile_env_parsing():
    assert parse_env_text("A=1; B=x=y") == {"A": "1", "B": "x=y"}
    for bad in ("noequals", "APP_NAME=x", "1a=2"):
        with pytest.raises(ValueError):
            parse_env_text(bad)


def test_profile_store_roundtrip(tmp_path):
    st = ProfileStore(tmp_path)
    p = BuildProfile(name="Release", script="echo hi", extra_env={"X": "1"})
    st.save(p)
    assert st.get("Release").env_vars()["X"] == "1"
    assert st.delete("Release")


# ------------------------------------------------------------------ compat
def facts(**kw):
    f = compat.HostFacts(docker_path="/usr/bin/docker", docker_info={"ServerVersion": "27"},
                         docker_root_free=200 * compat.GB, home="/home/u",
                         resources=ra.HostResources.from_parts_simple(16, 8, 32768, 24000)
                         if hasattr(ra.HostResources, "from_parts_simple") else None)
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def spec(**kw):
    s = BUILTIN[0].to_spec("t")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def codes(items):
    return {x.code for x in items}


def test_missing_docker_is_error():
    assert "docker-missing" in codes(compat.check_host(facts(docker_path="")))


def test_snap_and_desktop_and_userns_warn():
    c = codes(compat.check_host(facts(docker_path="/snap/bin/docker")))
    assert "snap-docker" in c
    assert "docker-desktop" in codes(compat.check_host(facts(docker_info={"OperatingSystem": "Docker Desktop"})))
    assert "userns-remap" in codes(compat.check_host(facts(docker_info={"SecurityOptions": ["name=userns"]})))


def test_low_docker_disk():
    r = compat.check_host(facts(docker_root_free=1 * compat.GB))
    assert any(x.code == "docker-disk" and x.level == ERROR for x in r)


def test_fuse_missing_blocks():
    assert has_errors(compat.check_spec(spec(fuse=True), facts(has_dev_fuse=False)))


def test_port_busy_and_ok():
    f = facts(port_free={6080: False})
    assert "port-busy" in codes(compat.check_spec(spec(port=6080), f))


def test_persistent_without_folder_is_error_and_ephemeral_is_fine():
    assert "storage-fs" in codes(compat.check_spec(spec(), facts()))
    one_time = EnvTemplate.from_spec(spec(), "q")
    one_time.persistent = False
    assert not has_errors(compat.check_spec(one_time.to_spec("q"), facts()))


def test_check_all_dedupes_and_oks():
    r = compat.check_all(spec(), facts())
    assert r and len({x.code for x in r}) == len(r)


# ------------------------------------------------------------------ health
class FakeProbe:
    def __init__(self, state="running", failing=(), outputs=None):
        self.state, self.failing, self.outputs = state, failing, outputs or {}

    def container_state(self):
        return self.state

    def sh(self, script):
        for key in self.failing:
            if key in script:
                return 1, ""
        for key, val in self.outputs.items():
            if key in script:
                return 0, val
        return 0, ""


class Env:
    persistent = False
    toolchain = False
    desktop = True
    de = "xfce"
    display_server = "x11"


def test_health_missing_and_stopped():
    assert diagnose_codes(FakeProbe("missing")) == {"container-missing"}
    assert diagnose_codes(FakeProbe("exited")) == {"stopped"}


def diagnose_codes(p, env=None):
    return {f.code for f in health.diagnose(p, env or Env())}


def test_health_all_good():
    assert diagnose_codes(FakeProbe()) == {"running", "healthy"}


def test_health_detects_desktop_problems():
    c = diagnose_codes(FakeProbe(failing=("Xvnc",)))
    assert "no-vnc" in c
    c = diagnose_codes(FakeProbe(failing=("pgrep -x websockify",)))
    assert "novnc-stopped" in c or "no-novnc" in c


def test_health_disk_full():
    c = diagnose_codes(FakeProbe(outputs={"df -P": "97"}))
    assert "disk-full" in c


def test_health_drive_missing(tmp_path):
    class P(Env):
        persistent = True
        slug = "e"
        name = "e"
        storage_mount = ""
        data_dir = tmp_path / "nothere"
    assert diagnose_codes(FakeProbe(), P()) == {"drive-missing"}
