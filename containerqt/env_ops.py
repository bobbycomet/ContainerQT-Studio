"""Step factories for environment lifecycle operations (create, start, stop, snapshot, export...)."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import builder, hostutil, logger, paths, storage
from .client import DockerClient
from .models import EnvSpec, EnvStore
from .runner import Out, Step

log = logger.get()


def _docker_step(client: DockerClient, title: str, make_argv, ignore_fail: bool = False) -> Step:
    """A `docker ...` step whose arguments are computed when it runs (mounts may not exist before then)."""
    def go(out: Out) -> bool:
        from .runner import Runner  # local import to avoid cycles at module load

        try:
            argv = client.argv(*make_argv())
        except ValueError as exc:
            out(str(exc))
            return False
        return Runner(out).run([Step(title, argv=argv, ignore_fail=ignore_fail)]).ok

    return Step(title, fn=go, ignore_fail=ignore_fail)


def create_steps(client: DockerClient, store: EnvStore, spec: EnvSpec, rebuild: bool = False) -> list[Step]:
    """One-click: register -> write Dockerfile -> build image -> (persistent: save to drive) -> container -> wait.

    rebuild=True on a persistent environment replaces its system directories on the drive (installed packages are
    lost) but keeps the home folder, /srv and the workspace.
    """
    ctx_holder: dict[str, Path] = {}

    def check_storage(out: Out) -> bool:
        ok, why = storage.validate_root(spec.storage_root)
        if not ok:
            out(f"Cannot store the environment there: {why}")
            return False
        if storage.looks_unmounted(spec.storage_root):
            out("That folder is on your system drive although its path looks like a removable drive - the drive is "
                "probably not mounted. Mount it first.")
            return False
        d = storage.drive_for_path(spec.storage_root)
        spec.storage_mount = d.mount if d else ""
        if d:
            out(f"Saving '{spec.name}' on {d.mount} ({d.fstype}, {storage.fmt_size(d.free)} free) in {spec.data_dir}")
            if d.free < storage.MIN_FREE_GB * 1024 ** 3:
                out(f"Warning: less than {storage.MIN_FREE_GB} GB free there; a desktop environment can need 5-10 GB.")
        try:  # best effort: a folder the user owns; otherwise Docker creates it and seed.sh hands it over
            Path(spec.storage_root).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return True

    def prepare(out: Out) -> bool:
        spec.image_tag = spec.default_tag
        if spec.persistent and spec.data_dir and not spec.workspace:
            spec.workspace = str(spec.data_dir / "workspace")
        store.put(spec)
        ctx_holder["ctx"] = builder.prepare_context(spec)
        out(f"Dockerfile written to {ctx_holder['ctx'] / 'Dockerfile'}")
        if spec.ephemeral:
            out("One-time environment: nothing is saved on this computer; export what you want to keep.")
        else:
            out(f"Workspace folder: {spec.workspace}")
        return True

    steps: list[Step] = []
    if spec.persistent:
        steps.append(Step("Check the storage drive", fn=check_storage))
    steps.append(Step("Prepare build context", fn=prepare))

    def build(out: Out) -> bool:  # run docker build with the real context path
        from .runner import Runner

        r = Runner(out)
        res = r.run([Step("docker build", argv=client.argv(*builder.build_args(spec, ctx_holder["ctx"])))])
        return res.ok

    steps.append(Step(f"Build image ({spec.base_image})", fn=build))
    if spec.persistent:
        steps.append(_docker_step(client, "Save the environment on the drive (can take a few minutes)",
                                  lambda: builder.seed_args(spec, reset=rebuild)))
    steps += recreate_container_steps(client, spec)
    steps.append(Step("Wait for the environment to be ready", fn=lambda out: wait_ready(client, spec, out)))
    return steps


def recreate_container_steps(client: DockerClient, spec: EnvSpec, image: str | None = None) -> list[Step]:
    return [
        Step("Remove previous container", argv=client.argv("rm", "-f", spec.container), ignore_fail=True),
        _docker_step(client, "Start container", lambda: builder.run_args(spec, image)),
    ]


def wait_ready(client: DockerClient, spec: EnvSpec, out: Out, timeout: int = 90) -> bool:
    deadline = time.time() + 30
    while time.time() < deadline:
        rc, _, _ = client.run("exec", "-u", "dev", spec.container, "true", timeout=10)
        if rc == 0:
            break
        time.sleep(1)
    else:
        out("Container did not become ready")
        return False
    out("Container is running")
    if spec.desktop:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if hostutil.http_ok(spec.novnc_url):
                out(f"Desktop is up at {spec.novnc_url}")
                return True
            time.sleep(2)
        out("Desktop view not responding yet (the Fix button can restart it)")
    return True


def preflight_step(spec: EnvSpec) -> Step:
    def check(out: Out) -> bool:
        ok, why = storage.preflight(spec)
        if not ok:
            out(why)
        else:
            builder.sync_scripts(spec)  # keep the launcher scripts current (they are bind-mounted)
        return ok

    return Step("Check the environment's saved data", fn=check)


def start_steps(client: DockerClient, spec: EnvSpec) -> list[Step]:
    steps: list[Step] = []
    if spec.persistent:
        steps.append(preflight_step(spec))
    if spec.ephemeral:
        # A one-time container is gone once stopped: "start" gives a fresh one from the image.
        steps += recreate_container_steps(client, spec)
    else:
        steps.append(Step("Start container", argv=client.argv("start", spec.container)))
    steps.append(Step("Wait for the environment to be ready", fn=lambda out: wait_ready(client, spec, out)))
    return steps


def stop_steps(client: DockerClient, spec: EnvSpec) -> list[Step]:
    return [Step("Stop container", argv=client.argv("stop", "-t", "5", spec.container))]


def restart_steps(client: DockerClient, spec: EnvSpec) -> list[Step]:
    return [
        Step("Restart container", argv=client.argv("restart", "-t", "5", spec.container)),
        Step("Wait for the environment to be ready", fn=lambda out: wait_ready(client, spec, out)),
    ]


def delete_steps(client: DockerClient, store: EnvStore, spec: EnvSpec, purge_data: bool = False) -> list[Step]:
    def unregister(out: Out) -> bool:
        store.remove(spec.name)
        if spec.persistent and not purge_data:
            out(f"Removed '{spec.name}' from ContainerQT Studio. Its saved data is still in {spec.data_dir}")
        elif spec.ephemeral:
            out(f"Removed '{spec.name}' from ContainerQT Studio")
        else:
            out(f"Removed '{spec.name}' from ContainerQT Studio (workspace folder {spec.workspace} was kept)")
        return True

    steps = [Step("Remove container", argv=client.argv("rm", "-f", spec.container), ignore_fail=True)]
    if spec.persistent and purge_data and spec.storage_root:
        if client.image_exists(spec.image_tag):
            steps.append(_docker_step(client, "Delete the saved data from the drive",
                                      lambda: builder.purge_args(spec)))
        else:
            steps.append(Step("Delete the saved data from the drive", fn=lambda out: (out(
                "The image is gone, so the root-owned saved data cannot be removed automatically; delete "
                f"{spec.data_dir} manually (sudo rm -rf)."), True)[1]))
    steps += [
        Step("Remove image", argv=client.argv("rmi", "-f", spec.default_tag), ignore_fail=True),
        Step("Unregister environment", fn=unregister),
    ]
    return steps


def snapshot_steps(client: DockerClient, spec: EnvSpec) -> list[Step]:
    tag = f"dockerqt/{spec.slug}:snap-{time.strftime('%Y%m%d-%H%M%S')}"
    return [Step(f"Snapshot container to {tag}",
                 argv=client.argv("commit", "-a", "ContainerQT", "-m", "ContainerQT snapshot", spec.container, tag))]


def restore_steps(client: DockerClient, store: EnvStore, spec: EnvSpec, tag: str) -> list[Step]:
    def remember(out: Out) -> bool:
        spec.image_tag = tag
        store.put(spec)
        out(f"Environment now uses image {tag}")
        return True

    return [Step("Select snapshot", fn=remember), *recreate_container_steps(client, spec, tag),
            Step("Wait for the environment to be ready", fn=lambda out: wait_ready(client, spec, out))]


def start_desktop_steps(client: DockerClient, spec: EnvSpec, action: str = "start") -> list[Step]:
    return [
        Step(f"{action.capitalize()} desktop",
             argv=client.exec_argv(spec.container, "/usr/local/bin/dockerqt-desktop", action,
                                   env={"VNC_RESOLUTION": spec.resolution})),
        Step("Wait for the desktop view", fn=lambda out: _wait_desktop(spec, out)),
    ]


def _wait_desktop(spec: EnvSpec, out: Out) -> bool:
    deadline = time.time() + 60
    while time.time() < deadline:
        if hostutil.http_ok(spec.novnc_url):
            out("Desktop is ready")
            return True
        time.sleep(1.5)
    out("Desktop did not respond in time")
    return False


def import_steps(spec: EnvSpec, sources: list[str], subdir: str, client: DockerClient | None = None) -> list[Step]:
    sub = subdir.strip("/")
    if spec.ephemeral:
        if client is None:
            raise ValueError("importing into a one-time environment needs the Docker client")
        dest = "/workspace" + (f"/{sub}" if sub else "")
        steps = [Step("Create the destination folder", argv=client.argv("exec", "-u", "dev", spec.container,
                                                                         "mkdir", "-p", dest))]
        for src in sources:
            steps.append(Step(f"Copy {Path(src).name}", argv=client.argv("cp", src, f"{spec.container}:{dest}/")))
        steps.append(Step("Fix ownership", argv=client.argv("exec", "-u", "root", spec.container,
                                                            "chown", "-R", "dev:dev", dest)))
        return steps

    dest_root = Path(spec.workspace) / sub if sub else Path(spec.workspace)

    def copy(out: Out) -> bool:
        dest_root.mkdir(parents=True, exist_ok=True)
        for s in sources:
            src = Path(s)
            target = dest_root / src.name
            if not src.exists():
                out(f"missing: {src}")
                return False
            out(f"Copying {src} -> {target}")
            if src.is_dir():
                shutil.copytree(src, target, dirs_exist_ok=True)
            else:
                shutil.copy2(src, target)
        out(f"Imported {len(sources)} item(s) into /workspace/{sub}".rstrip("/"))
        return True

    return [Step("Copy files into the workspace", fn=copy)]


def put_script_steps(client: DockerClient, spec: EnvSpec, text: str, container_path: str) -> list[Step]:
    """Copy a script into a running container (one-time environments have no shared host folder)."""
    d = str(Path(container_path).parent)
    holder: dict[str, Path] = {}

    def stage(out: Out) -> bool:
        tmp = paths.data_dir() / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        f = tmp / Path(container_path).name
        f.write_text(text, encoding="utf-8")
        holder["f"] = f
        return True

    def copy(out: Out) -> bool:
        from .runner import Runner

        argv = client.argv("cp", str(holder["f"]), f"{spec.container}:{container_path}")
        ok = Runner(out).run([Step("docker cp", argv=argv)]).ok
        holder["f"].unlink(missing_ok=True)
        return ok

    return [
        Step("Prepare the build script", fn=stage),
        Step("Create the script folder", argv=client.argv("exec", "-u", "root", spec.container, "mkdir", "-p", d)),
        Step("Copy the build script into the environment", fn=copy),
        Step("Make the script runnable", argv=client.argv("exec", "-u", "root", spec.container, "chmod", "0755",
                                                           container_path)),
    ]


def export_steps(client: DockerClient, spec: EnvSpec, container_path: str, host_dir: str,
                 contents_only: bool) -> list[Step]:
    src = container_path.rstrip("/") + ("/." if contents_only else "")

    def mk(out: Out) -> bool:
        Path(host_dir).mkdir(parents=True, exist_ok=True)
        return True

    return [
        Step("Create destination folder", fn=mk),
        Step(f"Copy {container_path} to the host",
             argv=client.argv("cp", f"{spec.container}:{src}", host_dir)),
    ]


def clone_spec(store: EnvStore, src: EnvSpec, name: str, storage_root: str | None = None) -> EnvSpec:
    """The registered-to-be spec of a clone: same configuration, new name, free port, own image tag/workspace."""
    from dataclasses import replace

    from .models import slugify

    name = name.strip()
    slug = slugify(name)
    if not name or not slug:
        raise ValueError("Give the clone a name")
    if store.get(name) or store.slug_taken(slug):
        raise ValueError(f"An environment called '{name}' already exists")
    dst = replace(src, name=name, slug=slug, image_tag="", extras=list(src.extras),
                  port=hostutil.find_free_port(src.port + 1, avoid=store.used_ports()))
    dst.image_tag = dst.default_tag
    if src.persistent:
        dst.storage_root = storage_root or src.storage_root
        dst.workspace = str(dst.data_dir / "workspace") if dst.data_dir else ""
        if dst.data_dir and dst.data_dir.exists():
            raise ValueError(f"{dst.data_dir} already exists")
    else:
        dst.workspace = ""
    return dst


def clone_steps(client: DockerClient, store: EnvStore, src: EnvSpec, dst: EnvSpec) -> list[Step]:
    """Duplicate a configured environment: same image (re-tagged), copy of the saved data when persistent."""
    def check(out: Out) -> bool:
        if not client.image_exists(src.image_tag):
            out(f"The image {src.image_tag} no longer exists; use Fix on '{src.name}' first.")
            return False
        if src.persistent:
            ok, why = storage.preflight(src)
            if not ok:
                out(why)
                return False
            ok, why = storage.validate_root(dst.storage_root)
            if not ok:
                out(f"Cannot store the clone there: {why}")
                return False
        return True

    def register(out: Out) -> bool:
        store.put(dst)
        out(f"Registered '{dst.name}'")
        return True

    steps = [Step("Check the source environment", fn=check),
             Step(f"Copy image to {dst.image_tag}", argv=client.argv("tag", src.image_tag, dst.image_tag))]
    if src.persistent:
        steps.append(_docker_step(client, "Copy the saved data (can take a few minutes)",
                                  lambda: builder.clone_args(src, dst)))
    steps.append(Step("Register the clone", fn=register))
    steps += recreate_container_steps(client, dst)
    steps.append(Step("Wait for the environment to be ready", fn=lambda out: wait_ready(client, dst, out)))
    return steps
