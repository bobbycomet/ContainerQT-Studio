"""Desktop / compositor / display-server matrix: what is greyed out, and that every valid choice is buildable."""
import itertools
import re
import subprocess

import pytest

from containerqt import builder, desktops, distros, paths
from containerqt.desktops import COMPOSITORS, DESKTOPS, DE_ORDER, SERVERS, WAYLAND, X11
from containerqt.models import EnvSpec


def all_valid():
    """Every (de, server, compositor) combination the matrix allows."""
    for de, server in itertools.product(DE_ORDER, SERVERS):
        for comp in COMPOSITORS:
            if desktops.compositor_state(de, server, comp)[0]:
                yield de, server, comp


# ------------------------------------------------------------------ the rules the user asked for
@pytest.mark.parametrize("de,server,comp,ok", [
    ("xfce", X11, "kwin", True), ("xfce", X11, "mutter", True), ("xfce", X11, "marco", True),
    ("kde", X11, "kwin", True), ("kde", X11, "muffin", False), ("kde", X11, "mutter", False),
    ("cinnamon", X11, "muffin", True), ("cinnamon", X11, "kwin", False), ("cinnamon", X11, "openbox", False),
    ("mate", X11, "marco", True), ("mate", X11, "muffin", True),
    ("gnome", X11, "mutter", True), ("gnome", X11, "kwin", False),
    ("sway", WAYLAND, "sway", True), ("sway", WAYLAND, "labwc", False), ("sway", X11, "sway", False),
    ("labwc", WAYLAND, "labwc", True), ("labwc", WAYLAND, "sway", False),
    ("xfce", WAYLAND, "xfwm4", False),      # an X11-only window manager can't run on Wayland
    ("kde", WAYLAND, "kwin", False),        # needs a DRM seat a container does not have
    ("gnome", WAYLAND, "mutter", False),
])
def test_compositor_matrix(de, server, comp, ok):
    enabled, why = desktops.compositor_state(de, server, comp)
    assert enabled is ok
    assert bool(why) is (not ok), "a disabled choice must say why (it becomes the tooltip)"


def test_server_availability_and_reasons():
    assert desktops.server_state("xfce", X11)[0] and not desktops.server_state("xfce", WAYLAND)[0]
    assert desktops.server_state("sway", WAYLAND)[0] and not desktops.server_state("sway", X11)[0]
    ok, why = desktops.server_state("kde", WAYLAND)
    assert not ok and "seat" in why
    assert "X11-only" in desktops.server_state("i3", WAYLAND)[1] or "Sway" in desktops.server_state("i3", WAYLAND)[1]


def test_distro_and_family_blocks():
    assert not desktops.de_state("kde", "rocky-9", "dnf")[0]
    assert "RHEL" in desktops.de_state("kde", "alma-9", "dnf")[1]
    assert desktops.de_state("xfce", "rocky-9", "dnf")[0]
    assert not desktops.de_state("labwc", "ubuntu-2204", "apt")[0]
    assert desktops.de_state("labwc", "ubuntu-2404", "apt")[0]
    assert not desktops.de_state("gnome", "alpine", "apk")[0]
    assert desktops.de_state("kde", "", "apt")[0]            # custom image: attempted, build reports clearly


# ------------------------------------------------------------------ data sanity
def test_data_is_self_consistent():
    for key, d in DESKTOPS.items():
        assert d.native in COMPOSITORS, key
        for server, comps in d.compat.items():
            for c in comps:
                assert server in COMPOSITORS[c].servers, f"{key}: {c} cannot run on {server}"
        for fam in desktops.FAMILIES:
            assert fam in d.required and fam in d.optional, (key, fam)
        assert d.session_bins
    for key, c in COMPOSITORS.items():
        for server in c.servers:
            assert server in c.binary, (key, server)
        for fam in desktops.FAMILIES:
            assert c.pkgs.get(fam), (key, fam)
    assert set(DE_ORDER) == set(DESKTOPS)


@pytest.mark.parametrize("d", distros.CATALOG, ids=lambda d: d.key)
def test_normalize_always_returns_something_valid(d):
    for de, server, comp in itertools.product(list(DE_ORDER) + ["bogus"], list(SERVERS) + ["bogus"],
                                              list(COMPOSITORS) + ["", "bogus"]):
        n_de, n_server, n_comp = desktops.normalize(de, server, comp, d.key, d.family)
        assert desktops.de_state(n_de, d.key, d.family)[0]
        assert desktops.server_state(n_de, n_server)[0]
        assert desktops.compositor_state(n_de, n_server, n_comp)[0]
        assert desktops.normalize(n_de, n_server, n_comp, d.key, d.family) == (n_de, n_server, n_comp)


def test_every_desktop_has_a_native_choice():
    for de in DE_ORDER:
        assert any(desktops.server_state(de, s)[0] for s in SERVERS)
        n_de, n_server, n_comp = desktops.normalize(de, X11, "", "", "apt")
        if desktops.server_state(de, n_server)[0]:
            assert n_comp == DESKTOPS[de].native or n_comp in desktops.compositors_for(de, n_server)


# ------------------------------------------------------------------ everything valid is buildable
@pytest.mark.parametrize("family", desktops.FAMILIES)
def test_plan_installs_something_and_checks_something(family):
    for de, server, comp in all_valid():
        p = desktops.plan(de, server, comp, family)
        assert p.required or p.compositor_pkgs, (de, server, comp, family)
        assert p.checks and all(alts for alts in p.checks)


@pytest.mark.parametrize("d", [distros.by_key(k) for k in ("ubuntu-2204", "fedora-latest", "arch", "tumbleweed", "alpine")],
                         ids=lambda d: d.key)
def test_dockerfile_for_every_valid_choice_has_valid_shell(d):
    n = 0
    for de, server, comp in all_valid():
        if not desktops.de_state(de, d.key, d.family)[0]:
            continue
        spec = EnvSpec(name="t", slug="t", distro_key=d.key, base_image=d.image, family=d.family, workspace="/w",
                       de=de, display_server=server, compositor=comp)
        text = builder.render_dockerfile(spec)
        assert "novnc" in text.lower()
        for ln in text.splitlines():
            if ln.startswith("RUN "):
                subprocess.run(["sh", "-n", "-c", ln[4:]], check=True)
        n += 1
    assert n > 5


def test_run_env_carries_the_choice():
    spec = EnvSpec(name="t", slug="t", distro_key="ubuntu-2404", base_image="ubuntu:24.04", family="apt",
                   workspace="/w", de="xfce", display_server="x11", compositor="kwin")
    assert builder.desktop_env(spec) == {"DQT_DE": "xfce", "DQT_SERVER": "x11", "DQT_WM": "kwin"}
    spec.compositor = "xfwm4"   # the desktop's own -> no override
    assert builder.desktop_env(spec)["DQT_WM"] == ""
    spec.de, spec.display_server, spec.compositor = "sway", "wayland", "sway"
    assert builder.desktop_env(spec) == {"DQT_DE": "sway", "DQT_SERVER": "wayland", "DQT_WM": ""}


# ------------------------------------------------------------------ desktop.sh agrees with the data
def _script() -> str:
    return (paths.resource_dir() / "desktop.sh").read_text(encoding="utf-8")


def test_desktop_script_handles_every_desktop():
    s = _script()
    for key, d in DESKTOPS.items():
        if X11 in d.compat:
            assert re.search(rf"^\s+{key}\)", s, re.M), f"desktop.sh has no X11 branch for {key}"
    assert "start_wayland" in s and "sway" in s and "labwc" in s


@pytest.mark.parametrize("key", [k for k, c in COMPOSITORS.items() if c.x11_cmd])
def test_wm_cmd_matches_data(key):
    s = _script()
    func = re.search(r"^wm_cmd\(\) \{.*?^\}", s, re.M | re.S).group(0)
    out = subprocess.run(["sh", "-c", func + f'\nwm_cmd {key}'], capture_output=True, text=True, check=True).stdout.strip()
    assert out == COMPOSITORS[key].x11_cmd
