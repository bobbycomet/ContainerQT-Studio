"""Optional development toolkit packs (Qt 5/6, KDE Frameworks 5/6) a template or environment can add.

Installed best-effort, one package at a time, so a name a given release lacks is reported but never fails the build.
Which Plasma series a desktop gets is decided by the Linux release (see desktops.PLASMA_SERIES); these packs only add the
*development* libraries, so e.g. Ubuntu 24.04 (Plasma 5.27) can still be used to build and test Qt 6 applications.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

FAMILIES = ("apt", "dnf", "pacman", "zypper", "apk")


@dataclass(frozen=True)
class Toolkit:
    key: str
    label: str
    pkgs: dict  # family -> packages
    note: str = ""


TOOLKITS: dict[str, Toolkit] = {t.key: t for t in (
    Toolkit("qt5", "Qt 5 development", {
        "apt": "qtbase5-dev qtdeclarative5-dev qttools5-dev qttools5-dev-tools libqt5svg5-dev qtmultimedia5-dev",
        "dnf": "qt5-qtbase-devel qt5-qtdeclarative-devel qt5-qttools-devel qt5-qtsvg-devel qt5-qtmultimedia-devel",
        "pacman": "qt5-base qt5-declarative qt5-tools qt5-svg qt5-multimedia",
        "zypper": "libqt5-qtbase-devel libqt5-qtdeclarative-devel libqt5-qttools-devel libqt5-qtsvg-devel",
        "apk": "qt5-qtbase-dev qt5-qtdeclarative-dev qt5-qttools-dev qt5-qtsvg-dev qt5-qtmultimedia-dev"}),
    Toolkit("qt6", "Qt 6 development", {
        "apt": "qt6-base-dev qt6-declarative-dev qt6-tools-dev qt6-tools-dev-tools qt6-svg-dev qt6-multimedia-dev "
               "libgl1-mesa-dev",
        "dnf": "qt6-qtbase-devel qt6-qtdeclarative-devel qt6-qttools-devel qt6-qtsvg-devel qt6-qtmultimedia-devel",
        "pacman": "qt6-base qt6-declarative qt6-tools qt6-svg qt6-multimedia",
        "zypper": "qt6-base-devel qt6-declarative-devel qt6-tools-devel qt6-svg-devel qt6-multimedia-devel",
        "apk": "qt6-qtbase-dev qt6-qtdeclarative-dev qt6-qttools-dev qt6-qtsvg-dev qt6-qtmultimedia-dev"}),
    Toolkit("kf5", "KDE Frameworks 5 development", {
        "apt": "extra-cmake-modules libkf5coreaddons-dev libkf5i18n-dev libkf5config-dev libkf5kio-dev "
               "libkf5widgetsaddons-dev libkf5xmlgui-dev",
        "dnf": "extra-cmake-modules kf5-kcoreaddons-devel kf5-ki18n-devel kf5-kconfig-devel kf5-kio-devel "
               "kf5-kwidgetsaddons-devel kf5-kxmlgui-devel",
        "pacman": "extra-cmake-modules",
        "zypper": "extra-cmake-modules kcoreaddons-devel ki18n-devel kconfig-devel kio-devel kwidgetsaddons-devel",
        "apk": "extra-cmake-modules"}),
    Toolkit("kf6", "KDE Frameworks 6 development", {
        "apt": "extra-cmake-modules libkf6coreaddons-dev libkf6i18n-dev libkf6config-dev libkf6kio-dev "
               "libkf6widgetsaddons-dev libkf6xmlgui-dev",
        "dnf": "extra-cmake-modules kf6-kcoreaddons-devel kf6-ki18n-devel kf6-kconfig-devel kf6-kio-devel "
               "kf6-kwidgetsaddons-devel kf6-kxmlgui-devel",
        "pacman": "extra-cmake-modules kcoreaddons ki18n kconfig kio kwidgetsaddons kxmlgui",
        "zypper": "extra-cmake-modules kf6-kcoreaddons-devel kf6-ki18n-devel kf6-kconfig-devel kf6-kio-devel",
        "apk": "extra-cmake-modules kcoreaddons-dev ki18n-dev kconfig-dev kio-dev kwidgetsaddons-dev"}),
    Toolkit("gtk3", "GTK 3 development", {
        "apt": "libgtk-3-dev", "dnf": "gtk3-devel", "pacman": "gtk3", "zypper": "gtk3-devel", "apk": "gtk+3.0-dev"}),
    Toolkit("gtk4", "GTK 4 development", {
        "apt": "libgtk-4-dev libadwaita-1-dev", "dnf": "gtk4-devel libadwaita-devel", "pacman": "gtk4 libadwaita",
        "zypper": "gtk4-devel libadwaita-devel", "apk": "gtk4.0-dev libadwaita-dev"}),
)}
ORDER = ("qt5", "qt6", "kf5", "kf6", "gtk3", "gtk4")
_PKG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:@-]*")


def clean(extras) -> list[str]:
    """Known toolkit keys only, in canonical order, no duplicates."""
    want = set(extras or ())
    return [k for k in ORDER if k in want]


def packages(extras, family: str) -> str:
    seen: dict[str, None] = {}
    for k in clean(extras):
        for p in TOOLKITS[k].pkgs.get(family, "").split():
            seen.setdefault(p)
    return " ".join(seen)


def clean_packages(text: str) -> str:
    """User-typed extra package names -> a safe, space-separated list (they end up in a shell command).

    Raises ValueError naming the first token that is not a plain package name.
    """
    out = []
    for tok in re.split(r"[\s,]+", (text or "").strip()):
        if not tok:
            continue
        if not _PKG.fullmatch(tok):
            raise ValueError(f"'{tok}' is not a valid package name (letters, digits and . _ + : @ - only)")
        out.append(tok)
    return " ".join(dict.fromkeys(out))
