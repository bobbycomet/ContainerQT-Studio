"""Cloning: spec derivation and the clone script against real directories (no Docker)."""
import os
import subprocess
from pathlib import Path

import pytest

from containerqt import env_ops, paths
from containerqt.models import EnvSpec, EnvStore


def make(tmp_path, **kw):
    base = dict(name="Src", slug="src", distro_key="ubuntu-2404", base_image="ubuntu:24.04", family="apt",
                workspace="", storage_root=str(tmp_path / "drive"), port=6080)
    base.update(kw)
    return EnvSpec(**base)


def test_clone_spec_persistent(tmp_path):
    store = EnvStore(tmp_path / "e.json")
    src = make(tmp_path, extras=["qt6"], cpus=4)
    store.put(src)
    d = env_ops.clone_spec(store, src, "Copy One")
    assert d.slug == "copy-one" and d.extras == ["qt6"] and d.cpus == 4
    assert d.port != src.port and d.image_tag != src.image_tag
    assert d.workspace.endswith("copy-one/workspace")
    with pytest.raises(ValueError):
        env_ops.clone_spec(store, src, "Src")
    with pytest.raises(ValueError):
        env_ops.clone_spec(store, src, "  ")


def test_clone_spec_ephemeral(tmp_path):
    store = EnvStore(tmp_path / "e.json")
    src = make(tmp_path, storage="ephemeral", storage_root="")
    d = env_ops.clone_spec(store, src, "Eph2")
    assert d.ephemeral and d.workspace == ""


def run_clone(tmp_path, **env):
    script = paths.resource_dir() / "clone.sh"
    e = dict(os.environ, DQT_SRC_STORE=str(tmp_path / "a"), DQT_STORE=str(tmp_path / "b"),
             HOST_UID=str(os.getuid()), HOST_GID=str(os.getgid()), **env)
    return subprocess.run(["sh", str(script)], env=e, capture_output=True, text=True)


def test_clone_script(tmp_path):
    src = tmp_path / "a" / "src"
    (src / "workspace").mkdir(parents=True)
    (src / "rootfs" / "home").mkdir(parents=True)
    (src / ".dockerqt-env").write_text("src\n")
    (src / "rootfs" / "home" / "f.txt").write_text("data")
    (tmp_path / "b").mkdir()
    r = run_clone(tmp_path, SRC_SLUG="src", SLUG="dup")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "b/dup/rootfs/home/f.txt").read_text() == "data"
    assert (tmp_path / "b/dup/.dockerqt-env").read_text().strip() == "dup"
    assert run_clone(tmp_path, SRC_SLUG="src", SLUG="dup").returncode != 0        # no overwrite
    assert run_clone(tmp_path, SRC_SLUG="../x", SLUG="y").returncode != 0         # odd slug
    assert run_clone(tmp_path, SRC_SLUG="missing", SLUG="z").returncode != 0      # source absent (unmounted drive)
