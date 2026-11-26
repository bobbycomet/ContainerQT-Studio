"""Catalog of Linux environments ContainerQT can create.

`family` selects the package manager used to build the environment:
apt (Debian/Ubuntu), dnf (Fedora/RHEL), pacman (Arch), zypper (openSUSE), apk (Alpine).
"""
from __future__ import annotations

from dataclasses import dataclass

CUSTOM_KEY = "custom"
FAMILIES = ("apt", "dnf", "pacman", "zypper", "apk")


@dataclass(frozen=True)
class Distro:
    key: str
    label: str
    image: str
    family: str
    note: str = ""
    setup: str = ""  # extra shell run first (e.g. enable EPEL)


_EL_SETUP = (
    "dnf install -y --allowerasing dnf-plugins-core epel-release; "
    "dnf config-manager --set-enabled crb || dnf config-manager --set-enabled powertools || true"
)

CATALOG: tuple[Distro, ...] = (
    Distro("ubuntu-2204", "Ubuntu 22.04 LTS", "ubuntu:22.04", "apt",
           "Recommended baseline for AppImages (glibc 2.35)"),
    Distro("ubuntu-2404", "Ubuntu 24.04 LTS", "ubuntu:24.04", "apt"),
    Distro("ubuntu-latest", "Ubuntu (latest LTS)", "ubuntu:latest", "apt"),
    Distro("ubuntu-2004", "Ubuntu 20.04 LTS", "ubuntu:20.04", "apt", "Oldest glibc (2.31) - widest AppImage reach"),
    Distro("debian-12", "Debian 12 (Bookworm)", "debian:12", "apt"),
    Distro("debian-13", "Debian 13 (Trixie)", "debian:13", "apt"),
    Distro("debian-11", "Debian 11 (Bullseye)", "debian:11", "apt"),
    Distro("kali", "Kali Linux (rolling)", "kalilinux/kali-rolling", "apt"),
    Distro("fedora-latest", "Fedora (latest)", "fedora:latest", "dnf"),
    Distro("fedora-42", "Fedora 42", "fedora:42", "dnf"),
    Distro("rocky-9", "Rocky Linux 9", "rockylinux:9", "dnf", "RHEL 9 compatible (glibc 2.34)", _EL_SETUP),
    Distro("alma-9", "AlmaLinux 9", "almalinux:9", "dnf", "RHEL 9 compatible (glibc 2.34)", _EL_SETUP),
    Distro("amazon-2023", "Amazon Linux 2023", "amazonlinux:2023", "dnf"),
    Distro("arch", "Arch Linux", "archlinux:latest", "pacman", "Rolling release"),
    Distro("tumbleweed", "openSUSE Tumbleweed", "opensuse/tumbleweed:latest", "zypper", "Rolling release"),
    Distro("leap", "openSUSE Leap", "opensuse/leap:latest", "zypper"),
    Distro("alpine", "Alpine Linux", "alpine:latest", "apk", "musl libc - AppImages built here are not glibc-portable"),
)


def by_key(key: str) -> Distro | None:
    for d in CATALOG:
        if d.key == key:
            return d
    return None


def custom(image: str, family: str) -> Distro:
    return Distro(CUSTOM_KEY, f"Custom ({image})", image, family)
