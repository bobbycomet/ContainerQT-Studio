"""Persistent vs one-time environments, drive detection, and the safety checks around saved data."""
import subprocess
from pathlib import Path

import pytest

from containerqt import builder, env_ops, paths, storage
from containerqt.client import DockerClient
from containerqt.models import CLASSIC, EPHEMERAL, PERSISTENT, EnvSpec, EnvStore

MOUNTINFO = r"""
22 1 8:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw
30 22 8:17 / /mnt/data rw,relatime - xfs /dev/sdb1 rw
31 22 8:33 / /media/bobby/USB\040DISK rw,relatime - exfat /dev/sdc1 rw
32 22 8:49 / /mnt/win rw,relatime - ntfs3 /dev/sdd1 rw
33 22 0:40 / /tmp rw - tmpfs tmpfs rw
34 22 7:1 / /snap/core/1 ro - squashfs /dev/loop1 ro
35 22 0:50 / /var/lib/docker/overlay2/x rw - overlay overlay rw
36 22 0:51 / /mnt/nas rw - nfs4 server:/share rw
"""


def spec(storage_mode=PERSISTENT, root="", **kw):
    return EnvSpec(name="Dev", slug="dev", distro_key="ubuntu-2204", base_image="ubuntu:22.04", family="apt",
                   workspace=kw.pop("workspace", ""), storage=storage_mode, storage_root=root, **kw)


# ------------------------------------------------------------------ drives
def test_mountinfo_parsing_and_unescape():
    mounts = {m for m, _s, _t in storage.read_mounts(MOUNTINFO)}
    assert {"/", "/mnt/data", "/media/bobby/USB DISK", "/tmp"} <= mounts


def test_classify_filesystems():
    for fs in ("ext4", "xfs", "btrfs"):
        assert storage.classify(fs)[0]
    for fs in ("vfat", "exfat", "ntfs3", "cifs", "tmpfs", "somethingnew"):
        ok, why = storage.classify(fs)
        assert not ok and why


def test_list_drives_filters_and_flags(monkeypatch):
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda p: type("U", (), dict(total=10 * 2**40, used=0, free=5 * 2**40))())
    drives = {d.mount: d for d in storage.list_drives(MOUNTINFO)}
    assert drives["/mnt/data"].ok and drives["/"].ok
    assert not drives["/media/bobby/USB DISK"].ok and "Unix permissions" in drives["/media/bobby/USB DISK"].reason
    assert not drives["/mnt/win"].ok and not drives["/mnt/nas"].ok
    for hidden in ("/tmp", "/snap/core/1", "/var/lib/docker/overlay2/x"):
        assert hidden not in drives
    assert list(drives)[0] == "/"
    assert drives["/mnt/data"].root_dir == Path("/mnt/data/DockerQT")


def test_drive_for_path_picks_longest_mount(monkeypatch):
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda p: type("U", (), dict(total=100, used=0, free=50))())
    monkeypatch.setattr(storage.os.path, "realpath", lambda p: p)
    assert storage.drive_for_path("/mnt/data/DockerQT/x", MOUNTINFO).mount == "/mnt/data"
    assert storage.drive_for_path("/home/me", MOUNTINFO).mount == "/"
    assert storage.drive_for_path("/media/bobby/USB DISK/a", MOUNTINFO).fstype == "exfat"


def test_validate_root_rejects_bad_filesystems(monkeypatch, tmp_path):
    bad = storage.Drive("/mnt/win", "/dev/sdd1", "ntfs3", 1, 1, False, "NTFS cannot store Unix permissions reliably")
    monkeypatch.setattr(storage, "drive_for_path", lambda p, mi=None: bad)
    ok, why = storage.validate_root(tmp_path / "new" / "deeper")
    assert not ok and "NTFS" in why
    good = storage.Drive("/", "/dev/sda2", "ext4", 100 * 2**30, 50 * 2**30, True)
    monkeypatch.setattr(storage, "drive_for_path", lambda p, mi=None: good)
    assert storage.validate_root(tmp_path / "x") == (True, "")


# ------------------------------------------------------------------ storage modes
def test_modes_and_paths(tmp_path):
    p = spec(root=str(tmp_path))
    assert p.persistent and not p.ephemeral
    assert p.data_dir == tmp_path / "dev" and p.rootfs_dir == tmp_path / "dev" / "rootfs"
    e = spec(EPHEMERAL)
    assert e.ephemeral and e.data_dir is None and e.rootfs_dir is None


def test_legacy_environments_load_as_classic(tmp_path):
    f = tmp_path / "environments.json"
    f.write_text('{"environments":[{"name":"Old","slug":"old","distro_key":"ubuntu-2204","base_image":"ubuntu:22.04",'
                 '"family":"apt","workspace":"/w","desktop":true}]}')
    old = EnvStore(f).get("Old")
    assert old.storage == CLASSIC and not old.persistent and not old.ephemeral
    assert builder.mount_args(old) == ["-v", "/w:/workspace"] or builder.mount_args(old)[1].endswith(":/workspace:z")


def test_store_roundtrip_keeps_desktop_and_storage_choices(tmp_path):
    st = EnvStore(tmp_path / "e.json")
    st.put(spec(root="/mnt/data/DockerQT", de="kde", display_server="x11", compositor="kwin"))
    again = EnvStore(tmp_path / "e.json").get("Dev")
    assert (again.de, again.display_server, again.compositor, again.storage_root) == \
        ("kde", "x11", "kwin", "/mnt/data/DockerQT")


# ------------------------------------------------------------------ one-time environments
def test_one_time_container_args():
    a = builder.run_args(spec(EPHEMERAL, port=6100))
    assert "--rm" in a and "-v" not in a
    assert "io.dockerqt.storage=ephemeral" in a


def test_one_time_start_always_creates_a_fresh_container():
    c = DockerClient()
    s = spec(EPHEMERAL)
    titles = [st.title for st in env_ops.start_steps(c, s)]
    assert "Start container" in titles and "Remove previous container" in titles
    assert all("saved data" not in t for t in titles)


def test_one_time_import_goes_through_docker_cp_not_a_host_folder(tmp_path):
    c = DockerClient()
    src = tmp_path / "proj"
    src.mkdir()
    steps = env_ops.import_steps(spec(EPHEMERAL), [str(src)], "work", c)
    argvs = [" ".join(st.argv) for st in steps if st.argv]
    assert any(" cp " in a and "dockerqt-dev:/workspace/work/" in a for a in argvs)
    with pytest.raises(ValueError):
        env_ops.import_steps(spec(EPHEMERAL), [str(src)], "")      # needs the client


def test_one_time_build_script_is_copied_into_the_container():
    steps = env_ops.put_script_steps(DockerClient(), spec(EPHEMERAL), "echo hi\n", "/tmp/dockerqt/build-1.sh")
    argvs = [" ".join(st.argv) for st in steps if st.argv]
    assert any("mkdir -p /tmp/dockerqt" in a for a in argvs) and any("chmod 0755 /tmp/dockerqt/build-1.sh" in a for a in argvs)


# ------------------------------------------------------------------ persistent environments
def prepared(tmp_path, dirs="etc usr var opt home root srv"):
    s = spec(root=str(tmp_path))
    (s.rootfs_dir).mkdir(parents=True)
    (s.rootfs_dir / ".dockerqt-dirs").write_text(dirs.replace(" ", "\n") + "\n")
    (s.data_dir / storage.MARKER).write_text("dev\n")
    s.workspace = str(s.data_dir / "workspace")
    return s


def test_persistent_mounts_cover_system_and_user_dirs(tmp_path):
    s = prepared(tmp_path)
    a = builder.mount_args(s)
    pairs = [a[i + 1] for i, x in enumerate(a) if x == "-v"]
    for d in ("etc", "usr", "var", "opt", "home", "root", "srv"):
        assert any(p.startswith(f"{s.rootfs_dir / d}:/{d}") for p in pairs), d
    assert any(p.split(":")[1] == "/workspace" for p in pairs)
    assert any(p.endswith("/usr/local/bin/dockerqt-entrypoint:ro") for p in pairs)
    assert not any(p.split(":")[1] in ("/bin", "/lib") for p in pairs)       # symlinked dirs are never mounted


def test_persistent_containers_do_not_use_docker_init(tmp_path):
    # docker-init is mounted at /sbin/docker-init, which a saved /usr hides on usr-merged distros
    assert "--init" not in builder.run_args(prepared(tmp_path))
    assert "--init" in builder.run_args(spec(EPHEMERAL))


def test_persistent_without_seed_refuses_to_start(tmp_path):
    s = spec(root=str(tmp_path))
    with pytest.raises(ValueError):
        builder.mount_args(s)


def test_preflight_protects_against_an_unmounted_drive(tmp_path):
    s = prepared(tmp_path)
    assert storage.preflight(s) == (True, "")
    (s.data_dir / storage.MARKER).unlink()                    # what an unmounted drive looks like
    ok, why = storage.preflight(s)
    assert not ok and "mounted" in why
    assert storage.preflight(spec(EPHEMERAL)) == (True, "")


def test_start_steps_for_persistent_check_the_drive_first(tmp_path):
    steps = env_ops.start_steps(DockerClient(), prepared(tmp_path))
    assert steps[0].title == "Check the environment's saved data"


def test_saved_dirs_ignores_junk(tmp_path):
    s = prepared(tmp_path, dirs="etc usr ../evil a/b")
    assert storage.saved_dirs(s) == ["etc", "usr"]


def test_create_steps_persistent_vs_one_time(tmp_path):
    c, st = DockerClient(), EnvStore(tmp_path / "e.json")
    p = [x.title for x in env_ops.create_steps(c, st, spec(root=str(tmp_path)))]
    assert p[0] == "Check the storage drive" and any("Save the environment on the drive" in t for t in p)
    e = [x.title for x in env_ops.create_steps(c, st, spec(EPHEMERAL))]
    assert not any("drive" in t.lower() for t in e)


def test_delete_can_keep_or_purge_saved_data(tmp_path):
    c, st = DockerClient(), EnvStore(tmp_path / "e.json")
    s = prepared(tmp_path)
    keep = [x.title for x in env_ops.delete_steps(c, st, s, purge_data=False)]
    assert not any("Delete the saved data" in t for t in keep)
    # purge: the image does not exist here, so it explains the manual step instead of failing silently
    purge = [x.title for x in env_ops.delete_steps(c, st, s, purge_data=True)]
    assert any("Delete the saved data" in t for t in purge)


# ------------------------------------------------------------------ the root-side scripts (run in a container)
def run_purge(tmp_path, slug):
    script = (paths.resource_dir() / "purge.sh").read_text().replace("/store", str(tmp_path))
    return subprocess.run(["sh", "-c", script], env={"SLUG": slug, "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)


def test_purge_only_removes_dockerqt_folders(tmp_path):
    mine, other = tmp_path / "dev", tmp_path / "photos"
    for d in (mine, other):
        d.mkdir()
        (d / "file").write_text("x")
    (mine / ".dockerqt-env").write_text("dev\n")
    assert run_purge(tmp_path, "photos").returncode == 0 and other.exists()      # no marker -> left alone
    assert run_purge(tmp_path, "dev").returncode == 0 and not mine.exists()
    for bad in ("..", ".", "a/b", ""):
        assert run_purge(tmp_path, bad).returncode != 0


def test_seed_script_only_touches_the_chosen_folder(tmp_path):
    """Run seed.sh against a fake root: it must create <slug>/rootfs + marker, skip symlinked dirs, stay in /store."""
    fake = tmp_path / "root"
    for d in ("etc", "usr", "home"):
        (fake / d).mkdir(parents=True)
        (fake / d / "f").write_text(d)
    (fake / "bin").symlink_to("usr")
    store = tmp_path / "store"
    store.mkdir()
    env = {"SLUG": "dev", "HOST_UID": "0", "HOST_GID": "0", "DQT_STORE": str(store), "DQT_SRC": str(fake),
           "PATH": "/usr/bin:/bin"}
    script = paths.resource_dir() / "seed.sh"
    r = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (store / "dev" / ".dockerqt-env").read_text().strip() == "dev"
    assert (store / "dev" / "rootfs" / "usr" / "f").read_text() == "usr"
    dirs = (store / "dev" / "rootfs" / ".dockerqt-dirs").read_text().split()
    assert "bin" not in dirs and {"etc", "usr", "home"} <= set(dirs)
    assert (store / "dev" / "workspace").is_dir()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["root", "store"]       # nothing written elsewhere

    # a second run keeps what is saved (a user's installed packages) ...
    (store / "dev" / "rootfs" / "usr" / "installed-later").write_text("pkg")
    assert subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True).returncode == 0
    assert (store / "dev" / "rootfs" / "usr" / "installed-later").exists()
    # ... and a rebuild replaces system dirs but never the home folder
    (store / "dev" / "rootfs" / "home" / "mine").write_text("keep me")
    env2 = dict(env, DQT_RESET="reset")
    assert subprocess.run(["sh", str(script)], env=env2, capture_output=True, text=True).returncode == 0
    assert not (store / "dev" / "rootfs" / "usr" / "installed-later").exists()
    assert (store / "dev" / "rootfs" / "home" / "mine").read_text() == "keep me"


def test_rebuild_resets_the_saved_system_dirs(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(builder, "seed_args", lambda s, image=None, reset=False: seen.append(reset) or (_ for _ in ()).throw(ValueError("stop")))
    for rebuild in (False, True):
        steps = env_ops.create_steps(DockerClient(), EnvStore(tmp_path / "e.json"), spec(root=str(tmp_path)), rebuild=rebuild)
        next(s for s in steps if "Save the environment" in s.title).fn(lambda _m: None)
    assert seen == [False, True]
