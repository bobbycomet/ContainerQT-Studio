"""Offline tests: no Docker daemon required."""
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HOME = tempfile.mkdtemp(prefix="dqt-home-")
os.environ["HOME"] = HOME
os.environ["XDG_DATA_HOME"] = HOME + "/data"
os.environ["XDG_CONFIG_HOME"] = HOME + "/config"

from containerqt import buildscripts, builder, distros, env_ops, paths, privilege, logger  # noqa: E402
from containerqt.client import DockerClient  # noqa: E402
from containerqt.models import EPHEMERAL, EnvSpec, EnvStore, slugify  # noqa: E402
from containerqt.runner import Runner, Step  # noqa: E402

logger.setup()


def spec_for(d, **kw):
    return EnvSpec(name=d.label, slug=slugify(d.label), distro_key=d.key, base_image=d.image,
                   family=d.family, workspace=HOME + "/ws", **kw)


@pytest.mark.parametrize("d", distros.CATALOG, ids=lambda d: d.key)
@pytest.mark.parametrize("desktop", [True, False])
def test_dockerfile_renders(d, desktop):
    text = builder.render_dockerfile(spec_for(d, desktop=desktop))
    assert text.startswith("# Generated")
    assert f"FROM {d.image}" in text
    assert ("novnc" in text.lower()) == desktop
    assert text.count("\nRUN ") >= 4
    assert 'ENTRYPOINT ["/usr/local/bin/dockerqt-entrypoint"]' in text
    # RUN lines must be single physical lines
    for ln in text.splitlines():
        assert not ln.endswith("\\")


def test_run_args():
    d = distros.by_key("ubuntu-2204")
    s = spec_for(d, fuse=True, cpus=2, memory_mb=4096, port=6123, storage=EPHEMERAL)
    a = builder.run_args(s)
    assert "127.0.0.1:6123:6080" in a
    assert "--cpus" in a and "--memory" in a and "/dev/fuse" in a
    assert "--rm" in a and "-v" not in a          # one-time: discarded on stop, nothing mounted from the host
    assert a[-1] == s.image_tag


def test_user_setup_shell_syntax():
    for shell in ("sh", "bash"):
        subprocess.run([shell, "-n", "-c", builder.USER_SETUP], check=True)
    for fam in builder.FAMILY:
        for pk in ("base", "toolchain", "packaging", "vnc"):
            subprocess.run(["sh", "-n", "-c", builder.install_required(fam, builder.FAMILY[fam][pk])], check=True)
            subprocess.run(["sh", "-n", "-c", builder.install_optional(fam, builder.FAMILY[fam][pk])], check=True)
    subprocess.run(["sh", "-n", "-c", builder.NOVNC_CLONE], check=True)


@pytest.mark.parametrize("t", buildscripts.all_templates(), ids=lambda t: t.id)
def test_templates_are_valid_bash(t):
    subprocess.run(["bash", "-n"], input=t.script, text=True, check=True)


def test_template_filtering():
    apt = {t.id for t in buildscripts.templates_for("apt")}
    assert "deb-dpkg-deb" in apt and "arch-makepkg" not in apt
    assert "alpine-abuild" in {t.id for t in buildscripts.templates_for("apk")}
    assert "appimage-cmake" not in {t.id for t in buildscripts.templates_for("apk")}


@pytest.mark.parametrize("name", ["entrypoint.sh", "desktop.sh", "dockerqt-helper", "seed.sh", "purge.sh"])
def test_resource_scripts_posix_syntax(name):
    for sh in ("sh", "dash"):
        subprocess.run([sh, "-n", str(paths.resource_dir() / name)], check=True)


def test_helper_installer_script_and_refuses_unknown():
    script = privilege._installer_script()
    subprocess.run(["sh", "-n"], input=script, text=True, check=True)
    assert "/usr/libexec/dockerqt/dockerqt-helper" in script
    # run the helper as non-root: must refuse
    p = subprocess.run(["sh", str(paths.resource_dir() / "dockerqt-helper"), "status"], capture_output=True, text=True)
    if os.geteuid() != 0:
        assert p.returncode != 0 and "root" in p.stderr
    p = subprocess.run(["sh", str(paths.resource_dir() / "dockerqt-helper"), "rm -rf /"], capture_output=True, text=True)
    assert p.returncode != 0
    with pytest.raises(ValueError):
        privilege.run_helper("rm -rf /", lambda s: None)


def test_policy_is_wellformed_xml():
    import xml.dom.minidom as md
    md.parse(str(paths.resource_dir() / "io.github.bobbycomet.dockerqt.policy"))


def test_store_roundtrip():
    st = EnvStore(Path(HOME) / "envs.json")
    d = distros.by_key("arch")
    s = spec_for(d)
    st.put(s)
    st2 = EnvStore(Path(HOME) / "envs.json")
    assert st2.get(s.name).container == "dockerqt-arch-linux"
    assert st2.used_ports() == {6080}
    st2.remove(s.name)
    assert EnvStore(Path(HOME) / "envs.json").all() == []


def test_runner_streams_fails_and_cancels():
    lines = []
    r = Runner(lines.append)
    res = r.run([Step("ok", argv=["sh", "-c", "echo hi; echo err >&2"]), Step("py", fn=lambda out: (out("x"), True)[1])])
    assert res.ok and "hi" in lines and "err" in lines and "x" in lines
    res = Runner(lines.append).run([Step("bad", argv=["sh", "-c", "echo boom; exit 3"]),
                                    Step("never", argv=["true"])])
    assert not res.ok and res.failed_step == "bad" and "boom" in res.tail
    res = Runner(lines.append).run([Step("bad-ok", argv=["false"], ignore_fail=True), Step("next", argv=["true"])])
    assert res.ok
    res = Runner(lines.append).run([Step("missing", argv=["definitely-not-a-binary"])])
    assert not res.ok


def test_host_env_restores_apprun_vars(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/app/lib")
    monkeypatch.setenv("DOCKERQT_ORIG_LD_LIBRARY_PATH", "/host/lib")
    monkeypatch.setenv("PYTHONHOME", "/app/py")
    monkeypatch.setenv("DOCKERQT_ORIG_PYTHONHOME", "__unset__")
    e = paths.host_env()
    assert e["LD_LIBRARY_PATH"] == "/host/lib" and "PYTHONHOME" not in e


def test_client_argv_sg_wrapping():
    c = DockerClient()
    c.binary = "/usr/bin/docker"
    assert c.argv("ps") == ["/usr/bin/docker", "ps"]
    c.use_sg = True
    import shutil
    if shutil.which("sg"):
        assert c.argv("run", "a b")[:3] == ["sg", "docker", "-c"]
    argv = c.exec_argv("c1", "bash", "-l", workdir="/w", env={"A": "1"}, interactive=True)
    assert "-it" in " ".join(argv) and "-w" in " ".join(argv)


def test_import_steps(tmp_path):
    d = distros.by_key("ubuntu-2204")
    s = spec_for(d)
    s.workspace = str(tmp_path / "ws")
    src = tmp_path / "proj"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "a.txt").write_text("hi")
    f = tmp_path / "single.txt"
    f.write_text("x")
    steps = env_ops.import_steps(s, [str(src), str(f)], "in/here")
    res = Runner(lambda _l: None).run(steps)
    assert res.ok
    assert (tmp_path / "ws/in/here/proj/sub/a.txt").read_text() == "hi"
    assert (tmp_path / "ws/in/here/single.txt").exists()
