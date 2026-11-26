"""Templates, build profiles, compatibility and health checks (no Docker, no GUI)."""
import json

import pytest

from containerqt import compat, health, profiles, templates, toolkits
from containerqt.findings import ERROR, OK, WARN
from containerqt.hardware import resource_advisor as ra
from containerqt.models import EnvSpec


def tpl(**kw):
    base = dict(name="T", distro_key="ubuntu-2404", desktop=True, de="kde", display_server="x11")
    base.update(kw)
    return templates.EnvTemplate(**base)


# ---------------------------------------------------------------- templates
def test_builtins_have_no_problems_and_plasma_variants():
    names = [t.name for t in templates.BUILTIN]
    assert all(not t.problems() for t in templates.BUILTIN)
    assert any("Plasma 5" in n and "Qt5" in n for n in names)
    assert any("Plasma 6" in n and "Qt6" in n for n in names)


def test_store_roundtrip_overwrite_delete(tmp_path):
    s = templates.TemplateStore(tmp_path)
    t = tpl(name="Ubuntu-24.04-KDE-Qt6", extras=["qt6"])
    s.save(t)
    assert s.get("Ubuntu-24.04-KDE-Qt6").extras == ["qt6"]
    with pytest.raises(FileExistsError):
        s.save(tpl(name="Ubuntu-24.04-KDE-Qt6"))
    s.save(tpl(name="Ubuntu-24.04-KDE-Qt6", extras=["qt5"]), overwrite=True)
    assert s.get("Ubuntu-24.04-KDE-Qt6").extras == ["qt5"]
    assert s.delete("Ubuntu-24.04-KDE-Qt6") and not s.delete("Ubuntu-24.04-KDE-Qt6")


def test_damaged_file_ignored_and_export_import(tmp_path):
    s = templates.TemplateStore(tmp_path / "a")
    s.save(tpl(name="One"))
    (s.folder / "bad.json").write_text("{nope")
    assert [t.name for t in s.user()] == ["One"]
    out = s.export_to(s.get("One"), tmp_path / "x.json")
    s2 = templates.TemplateStore(tmp_path / "b")
    assert s2.import_from(out).name == "One"
    (tmp_path / "y.json").write_text(json.dumps({"kind": "other"}))
    with pytest.raises(ValueError):
        s2.import_from(tmp_path / "y.json")


def test_name_validation_and_to_spec():
    for bad in ("", "a/b", "x" * 81):
        with pytest.raises(ValueError):
            templates.safe_name(bad)
    spec = tpl(persistent=False).to_spec("env1")
    assert spec.template == "T" and spec.de == "kde"
    assert tpl(distro_key="nope").problems()


def test_clean_packages_rejects_metacharacters():
    assert toolkits.clean_packages("git  vim") 
    for bad in ("vim; rm -rf /", "$(x)", "a|b"):
        with pytest.raises(ValueError):
            toolkits.clean_packages(bad)


# ---------------------------------------------------------------- profiles
def test_env_parsing():
    assert profiles.parse_env_text("A=1; B=x=y") == {"A": "1", "B": "x=y"}
    for bad in ("1A=1", "APP_NAME=x", "novalue"):
        with pytest.raises(ValueError):
            profiles.parse_env_text(bad)


def test_profile_store_and_vars(tmp_path):
    s = profiles.ProfileStore(tmp_path)
    p = profiles.BuildProfile(name="P", script="echo hi", extra_env={"K": "v"})
    s.save(p)
    got = s.get("P")
    assert got.env_vars()["K"] == "v" and got.env_vars()["APP_NAME"] == "App"
    assert got.applies_to("apt")[0]
    assert s.delete("P")


# ---------------------------------------------------------------- compat
def facts(**kw):
    r = ra.HostResources(8, 16, 32 * 1024, 26 * 1024, 0.05, 0.5, 8 * 1024, 8 * 1024, 0.0)
    base = dict(docker_path="/usr/bin/docker", docker_info={"SecurityOptions": ["name=seccomp"]},
                docker_root_free=100 * compat.GB, home="/home/u", resources=r)
    base.update(kw)
    return compat.HostFacts(**base)


def codes(items):
    return {f.code for f in items}


def test_host_checks():
    assert codes(compat.check_host(facts())) == {"host-ok"}
    assert "docker-missing" in codes(compat.check_host(facts(docker_path="")))
    assert "snap-docker" in codes(compat.check_host(facts(docker_path="/snap/bin/docker")))
    assert "userns-remap" in codes(compat.check_host(facts(docker_info={"SecurityOptions": ["name=userns"]})))
    low = compat.check_host(facts(docker_root_free=1 * compat.GB))
    assert any(f.code == "docker-disk" and f.level == ERROR for f in low)


def test_spec_checks():
    s = tpl().to_spec("e", storage_root="", persistent=False) if False else tpl(persistent=False).to_spec("e")
    assert not any(f.level == ERROR for f in compat.check_spec(s, facts()))
    s.fuse = True
    assert "no-fuse" in codes(compat.check_spec(s, facts(has_dev_fuse=False)))
    s.port = 6080
    assert "port-busy" in codes(compat.check_spec(s, facts(port_free={6080: False})))
    assert "plasma-series" in codes(compat.check_spec(s, facts()))
    p = tpl(persistent=True).to_spec("p", storage_root="")
    assert "storage-fs" in codes(compat.check_spec(p, facts()))


def test_check_all_ok():
    s = tpl(de="xfce", persistent=False).to_spec("e")
    assert compat.check_all(s, facts())[0].level in (OK, WARN, "info")


# ---------------------------------------------------------------- health
class FakeProbe:
    def __init__(self, state="running", fail=()):
        self.state, self.fail = state, fail

    def container_state(self):
        return self.state

    def sh(self, script):
        return (1 if any(f in script for f in self.fail) else 0), ""


class Env:
    persistent = False
    toolchain = True
    desktop = True
    de = "xfce"
    display_server = "x11"


def test_health_states():
    assert health.diagnose(FakeProbe("missing"), Env())[0].code == "container-missing"
    assert health.diagnose(FakeProbe("exited"), Env())[0].fix == "start"
    assert health.diagnose(FakeProbe(), Env())[-1].code == "healthy" or any(
        f.level == OK for f in health.diagnose(FakeProbe(), Env()))
    broken = health.diagnose(FakeProbe(fail=("websockify",)), Env())
    assert any(f.level in (WARN, ERROR) for f in broken)
