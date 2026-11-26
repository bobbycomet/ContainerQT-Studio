"""Desktop environments, compositors / window managers and display servers ContainerQT can set up.

Everything here is data: the GUI greys out whatever `compositor_state()` / `de_state()` / `server_state()`
say cannot work, and `builder.py` installs the package sets listed below.

Display servers
  x11      Xvnc (TigerVNC) runs the X display; the session runs on it.
  wayland  A wlroots compositor (Sway, labwc) runs headless and is shown through wayvnc. KDE and GNOME
           Wayland sessions need a DRM/logind seat, which an unprivileged container does not have,
           so they are listed but disabled (flip WAYLAND_BLOCKED below if you get them working).

Everything is shown to the user through noVNC, whichever server is used.
"""
from __future__ import annotations

from dataclasses import dataclass, field

X11, WAYLAND = "x11", "wayland"
SERVERS = (X11, WAYLAND)
SERVER_LABEL = {X11: "X11 (Xvnc)", WAYLAND: "Wayland (headless + wayvnc)"}
NONE = "none"
FAMILIES = ("apt", "dnf", "pacman", "zypper", "apk")


# --------------------------------------------------------------------------------------------------
# Compositors / window managers
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Compositor:
    key: str
    label: str
    servers: frozenset
    binary: dict  # server -> executable that must exist after installation
    pkgs: dict  # family -> packages (installed one by one; a missing one is not fatal)
    x11_cmd: str = ""  # command used when this is the window manager of an X11 desktop session
    note: str = ""


def _c(key, label, servers, binary, pkgs, x11_cmd="", note=""):
    return Compositor(key, label, frozenset(servers), binary, pkgs, x11_cmd, note)


_WL_EXTRA = {
    "apt": "libgl1-mesa-dri libegl1 xwayland wlr-randr",
    "dnf": "mesa-dri-drivers mesa-libEGL xorg-x11-server-Xwayland wlr-randr",
    "pacman": "mesa xorg-xwayland wlr-randr",
    "zypper": "Mesa-dri Mesa-libEGL1 xwayland wlr-randr",
    "apk": "mesa-dri-gallium mesa-egl xwayland wlr-randr",
}


def _wl(comp: dict) -> dict:
    """Packages for a headless wlroots compositor: it, a terminal, wayvnc, software GL and Xwayland."""
    return {f: f"{comp[f]} foot wayvnc {_WL_EXTRA[f]}" for f in FAMILIES}


COMPOSITORS: dict[str, Compositor] = {c.key: c for c in (
    _c("xfwm4", "xfwm4 (XFCE)", [X11], {X11: "xfwm4"},
       {f: "xfwm4" for f in FAMILIES}, "xfwm4"),
    _c("kwin", "KWin (KDE)", [X11, WAYLAND], {X11: "kwin_x11", WAYLAND: "kwin_wayland"},
       {"apt": "kwin-x11", "dnf": "kwin kwin-x11", "pacman": "kwin kwin-x11", "zypper": "kwin6 kwin6-x11",
        "apk": "kwin"}, "kwin_x11", "KWin as an X11 window manager; Wayland needs a real GPU seat"),
    _c("mutter", "Mutter (GNOME)", [X11, WAYLAND], {X11: "mutter", WAYLAND: "mutter"},
       {f: "mutter" for f in FAMILIES}, "mutter --x11 --replace", "GNOME's compositor"),
    _c("muffin", "Muffin (Cinnamon)", [X11], {X11: "muffin"},
       {f: "muffin" for f in FAMILIES}, "muffin --replace"),
    _c("marco", "Marco (MATE)", [X11], {X11: "marco"},
       {f: "marco" for f in FAMILIES}, "marco --replace"),
    _c("openbox", "Openbox", [X11], {X11: "openbox"},
       {f: "openbox" for f in FAMILIES}, "openbox"),
    _c("i3", "i3 (tiling)", [X11], {X11: "i3"},
       {"apt": "i3-wm", "dnf": "i3", "pacman": "i3-wm", "zypper": "i3", "apk": "i3wm"}, "i3"),
    _c("sway", "Sway (Wayland, tiling)", [WAYLAND], {WAYLAND: "sway"},
       _wl({f: "sway" for f in FAMILIES}), "", "i3-compatible Wayland compositor"),
    _c("labwc", "labwc (Wayland, Openbox-style)", [WAYLAND], {WAYLAND: "labwc"},
       _wl({f: "labwc" for f in FAMILIES}), ""),
)}


# --------------------------------------------------------------------------------------------------
# Desktop environments / sessions
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class DesktopEnv:
    key: str
    label: str
    native: str  # compositor key that ships with this DE
    compat: dict  # server -> compositors that can drive this DE on that server
    required: dict  # family -> packages that must install
    optional: dict  # family -> packages installed if available
    session_bins: tuple  # at least one of these must exist after installation
    experimental: bool = False
    note: str = ""
    wayland_reason: str = ""  # why there is no Wayland option, when there is none


DESKTOPS: dict[str, DesktopEnv] = {d.key: d for d in (
    DesktopEnv(
        "xfce", "XFCE", "xfwm4",
        {X11: ("xfwm4", "openbox", "marco", "mutter", "kwin", "muffin")},
        {"apt": "xfce4 xfce4-terminal thunar", "dnf": "xfce4-session xfwm4 xfdesktop xfce4-panel xfce4-terminal "
                                                      "xfce4-settings Thunar",
         "pacman": "xfce4 xfce4-terminal", "zypper": "xfce4-session xfce4-panel xfdesktop xfwm4 xfce4-terminal "
                                                     "xfce4-settings thunar",
         "apk": "xfce4 xfce4-terminal thunar"},
        {f: "" for f in FAMILIES}, ("startxfce4", "xfce4-session"),
        note="Light and reliable; the default.",
        wayland_reason="XFCE's Wayland support is not ready yet",
    ),
    DesktopEnv(
        "kde", "KDE Plasma", "kwin",
        {X11: ("kwin", "openbox", "xfwm4", "marco")},
        {"apt": "plasma-desktop konsole dolphin", "dnf": "plasma-desktop konsole dolphin",
         "pacman": "plasma-desktop konsole dolphin", "zypper": "patterns-kde-kde_plasma konsole dolphin",
         "apk": "plasma-desktop konsole dolphin"},
        {"apt": "kwin-x11 plasma-workspace", "dnf": "kwin-x11 plasma-workspace-x11",
         "pacman": "plasma-x11-session kwin-x11", "zypper": "", "apk": "plasma-workspace"},
        ("startplasma-x11", "startkde"),
        note="Heavy (several GB). Runs on software rendering.",
        wayland_reason="Plasma's Wayland session needs a DRM/logind seat, which a container does not have",
    ),
    DesktopEnv(
        "gnome", "GNOME", "mutter",
        {X11: ("mutter",)},
        {"apt": "gnome-session gnome-shell gnome-terminal nautilus mutter",
         "dnf": "gnome-shell gnome-session gnome-terminal nautilus mutter",
         "pacman": "gnome-shell gnome-session gnome-terminal nautilus mutter",
         "zypper": "gnome-shell gnome-session gnome-terminal nautilus mutter",
         "apk": "gnome-shell gnome-session gnome-terminal nautilus mutter"},
        {"apt": "gnome-session-flashback", "dnf": "gnome-session-xsession", "pacman": "", "zypper": "", "apk": ""},
        ("gnome-session",), experimental=True,
        note="Experimental: GNOME 49+ dropped its X11 session, so only older releases work.",
        wayland_reason="GNOME's Wayland session needs a DRM/logind seat, which a container does not have",
    ),
    DesktopEnv(
        "cinnamon", "Cinnamon", "muffin",
        {X11: ("muffin",)},
        {"apt": "cinnamon cinnamon-session nemo gnome-terminal", "dnf": "cinnamon nemo gnome-terminal",
         "pacman": "cinnamon gnome-terminal", "zypper": "cinnamon nemo gnome-terminal",
         "apk": "cinnamon gnome-terminal"},
        {f: "" for f in FAMILIES}, ("cinnamon-session-cinnamon2d", "cinnamon-session"),
        experimental=True, note="Runs in Cinnamon's software-rendered (2D) mode.",
        wayland_reason="Cinnamon's Wayland session is not usable yet",
    ),
    DesktopEnv(
        "mate", "MATE", "marco",
        {X11: ("marco", "xfwm4", "openbox", "mutter", "muffin")},
        {"apt": "mate-desktop-environment-core mate-terminal caja",
         "dnf": "mate-desktop mate-session-manager marco mate-panel caja mate-terminal mate-settings-daemon "
                "mate-control-center",
         "pacman": "mate mate-terminal",
         "zypper": "mate-session-manager marco mate-panel caja mate-terminal mate-control-center "
                   "mate-settings-daemon",
         "apk": "mate-desktop mate-session-manager marco mate-panel caja mate-terminal"},
        {f: "" for f in FAMILIES}, ("mate-session",),
        wayland_reason="MATE has no Wayland session",
    ),
    DesktopEnv(
        "lxqt", "LXQt", "openbox",
        {X11: ("openbox", "xfwm4", "kwin", "marco", "mutter", "muffin")},
        {"apt": "lxqt-core openbox qterminal pcmanfm-qt",
         "dnf": "lxqt-session lxqt-panel pcmanfm-qt qterminal openbox lxqt-config",
         "pacman": "lxqt openbox", "zypper": "lxqt-session lxqt-panel pcmanfm-qt qterminal openbox lxqt-config",
         "apk": "lxqt-desktop openbox"},
        {f: "" for f in FAMILIES}, ("startlxqt",),
        wayland_reason="LXQt's Wayland session is not packaged widely enough",
    ),
    DesktopEnv(
        "i3", "i3 (X11 tiling)", "i3", {X11: ("i3",)},
        {"apt": "i3-wm i3status dmenu xterm", "dnf": "i3 i3status dmenu xterm",
         "pacman": "i3-wm i3status dmenu xterm", "zypper": "i3 i3status dmenu xterm",
         "apk": "i3wm i3status dmenu xterm"},
        {f: "" for f in FAMILIES}, ("i3",),
        note="Keyboard-driven tiling. Super key = Mod; use Alt if your browser eats it.",
        wayland_reason="i3 is X11-only (use Sway for the Wayland equivalent)",
    ),
    DesktopEnv(
        "openbox", "Openbox (minimal X11)", "openbox", {X11: ("openbox",)},
        {f: "openbox xterm" for f in FAMILIES},
        {"apt": "tint2 pcmanfm", "dnf": "tint2 pcmanfm", "pacman": "tint2 pcmanfm", "zypper": "tint2 pcmanfm",
         "apk": "tint2 pcmanfm"},
        ("openbox-session", "openbox"), note="Tiny: a window manager, a panel and a terminal.",
        wayland_reason="Openbox is X11-only (use labwc for the Wayland equivalent)",
    ),
    DesktopEnv(
        "sway", "Sway (Wayland tiling)", "sway", {WAYLAND: ("sway",)},
        {f: "" for f in FAMILIES}, {f: "" for f in FAMILIES}, ("sway",),
        note="Alt = Mod key. Runs headless and is streamed with wayvnc.",
        wayland_reason="",
    ),
    DesktopEnv(
        "labwc", "labwc (Wayland, Openbox-style)", "labwc", {WAYLAND: ("labwc",)},
        {f: "" for f in FAMILIES}, {f: "" for f in FAMILIES}, ("labwc",),
        note="Floating-window Wayland compositor. Runs headless and is streamed with wayvnc.",
    ),
)}

DE_ORDER = ("xfce", "kde", "gnome", "cinnamon", "mate", "lxqt", "i3", "openbox", "sway", "labwc")


# Wayland sessions that exist upstream but cannot run in a plain container. Flip an entry to allow it.
WAYLAND_BLOCKED = {
    ("kde", "kwin"): DESKTOPS["kde"].wayland_reason,
    ("gnome", "mutter"): DESKTOPS["gnome"].wayland_reason,
}

# --------------------------------------------------------------------------------------------------
# Per-distro availability (what we know is not packaged); everything else is attempted.
# --------------------------------------------------------------------------------------------------
_EL9 = "Not packaged for RHEL 9-compatible distributions in the default repositories"
DISTRO_BLOCKS: dict[str, dict[str, str]] = {
    "rocky-9": {"kde": _EL9, "sway": _EL9, "labwc": _EL9},
    "alma-9": {"kde": _EL9, "sway": _EL9, "labwc": _EL9},
    "amazon-2023": {k: "Amazon Linux 2023 only packages MATE" for k in DE_ORDER if k != "mate"},
    "ubuntu-2004": {"labwc": "labwc is not in Ubuntu 20.04"},
    "ubuntu-2204": {"labwc": "labwc is not in Ubuntu 22.04"},
    "debian-11": {"labwc": "labwc is not in Debian 11"},
    "leap": {"labwc": "labwc is not in openSUSE Leap"},
}
FAMILY_BLOCKS: dict[str, dict[str, str]] = {
    "apk": {"gnome": "GNOME needs systemd/logind, which Alpine does not use"},
}


# --------------------------------------------------------------------------------------------------
# Which KDE Plasma series a Linux release ships. The series is a property of the release, not a switch:
# to test Plasma 5 and Plasma 6 you pick releases that carry them (see templates.BUILTIN). Series only, not point
# releases, because those move; "latest" images are listed as the series they carry today.
# --------------------------------------------------------------------------------------------------
PLASMA_SERIES: dict[str, str] = {
    "ubuntu-2004": "5", "ubuntu-2204": "5", "ubuntu-2404": "5", "ubuntu-latest": "6",
    "debian-11": "5", "debian-12": "5", "debian-13": "6", "kali": "6",
    "fedora-latest": "6", "fedora-42": "6", "arch": "6", "tumbleweed": "6", "leap": "6", "alpine": "6",
}


def plasma_series(distro_key: str) -> str:
    """'5', '6', or '' when not known (custom images)."""
    return PLASMA_SERIES.get(distro_key, "")


def plasma_note(distro_key: str) -> str:
    s = plasma_series(distro_key)
    return f"KDE Plasma {s}.x (this Linux release ships the Plasma {s} series)" if s else ""


# --------------------------------------------------------------------------------------------------
# Availability queries used by the GUI (each returns (enabled, reason-when-disabled))
# --------------------------------------------------------------------------------------------------
def de_state(de: str, distro_key: str = "", family: str = "") -> tuple[bool, str]:
    if de not in DESKTOPS:
        return False, "Unknown desktop"
    if reason := DISTRO_BLOCKS.get(distro_key, {}).get(de):
        return False, reason
    if reason := FAMILY_BLOCKS.get(family, {}).get(de):
        return False, reason
    return True, ""


def compositors_for(de: str, server: str) -> tuple[str, ...]:
    d = DESKTOPS.get(de)
    if not d:
        return ()
    return tuple(k for k in d.compat.get(server, ()) if (de, k) not in WAYLAND_BLOCKED or server != WAYLAND)


def server_state(de: str, server: str) -> tuple[bool, str]:
    d = DESKTOPS.get(de)
    if not d:
        return False, "Unknown desktop"
    if compositors_for(de, server):
        return True, ""
    if server == WAYLAND:
        return False, d.wayland_reason or f"{d.label} has no Wayland session"
    return False, f"{d.label} is Wayland-only"


def compositor_state(de: str, server: str, comp: str) -> tuple[bool, str]:
    c = COMPOSITORS.get(comp)
    d = DESKTOPS.get(de)
    if not c or not d:
        return False, "Unknown compositor"
    if server not in c.servers:
        other = ", ".join(SERVER_LABEL[s] for s in sorted(c.servers))
        return False, f"{c.label} only works on {other}"
    if server == WAYLAND and (de, comp) in WAYLAND_BLOCKED:
        return False, WAYLAND_BLOCKED[(de, comp)]
    if comp in compositors_for(de, server):
        return True, ""
    if comp == d.native or comp in d.compat.get(server, ()):
        return False, "Not available on this display server"
    return False, f"{c.label} cannot drive {d.label} on {SERVER_LABEL[server]}"


def normalize(de: str, server: str, comp: str, distro_key: str = "", family: str = "") -> tuple[str, str, str]:
    """Return a valid (de, server, compositor) close to the request."""
    if de not in DESKTOPS or not de_state(de, distro_key, family)[0]:
        de = next((k for k in DE_ORDER if de_state(k, distro_key, family)[0]), "xfce")
    if not server_state(de, server)[0]:
        server = next((s for s in SERVERS if server_state(de, s)[0]), X11)
    allowed = compositors_for(de, server)
    if comp not in allowed:
        native = DESKTOPS[de].native
        comp = native if native in allowed else (allowed[0] if allowed else "")
    return de, server, comp


def is_native(de: str, comp: str) -> bool:
    d = DESKTOPS.get(de)
    return bool(d and (not comp or comp == d.native))


def describe(de: str, server: str, comp: str) -> str:
    d = DESKTOPS.get(de)
    if not d:
        return "-"
    c = COMPOSITORS.get(comp or d.native)
    return f"{d.label} - {c.label.split(' (')[0] if c else '?'} - {server.upper() if server == X11 else 'Wayland'}"


# --------------------------------------------------------------------------------------------------
# What the Dockerfile needs
# --------------------------------------------------------------------------------------------------
@dataclass
class DesktopPlan:
    required: str = ""
    optional: str = ""
    compositor_pkgs: str = ""
    checks: list = field(default_factory=list)  # each: list of alternative executables, one must exist


def plan(de: str, server: str, comp: str, family: str) -> DesktopPlan:
    d = DESKTOPS[de]
    comp = comp or d.native
    c = COMPOSITORS[comp]
    p = DesktopPlan()
    p.required = d.required.get(family, "")
    p.optional = d.optional.get(family, "")
    # The compositor's own packages: always for Sway/labwc (they are the "desktop"), and for any
    # compositor that is not the DE's native one.
    if d.required.get(family, "") == "" or comp != d.native:
        p.compositor_pkgs = c.pkgs.get(family, "")
    p.checks.append(list(d.session_bins))
    p.checks.append([c.binary.get(server) or c.binary[next(iter(c.binary))]])
    return p
