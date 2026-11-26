#!/usr/bin/env python3
"""Shrink an installed PyQt6 + PyQt6-WebEngine tree to what ContainerQT uses.

usage: prune_qt.py <site-packages>/PyQt6

Keeps the PyQt modules ContainerQT imports, their Qt libraries (found by walking ELF NEEDED entries, so
nothing a kept binary links against is ever removed), the plugin families a desktop app needs, and the
QtWebEngine resources + the en-US locale. Everything else (Quick3D, Designer, QML, Multimedia,
translations, ...) is deleted. Run a real import test afterwards - in-builder.sh does.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

KEEP_MODULES = {
    "QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtDBus", "QtOpenGL", "QtOpenGLWidgets", "QtPrintSupport",
    "QtSvg", "QtSvgWidgets", "QtWebChannel", "QtWebEngineCore", "QtWebEngineWidgets", "sip",
}
KEEP_PLUGIN_DIRS = {
    "platforms", "platformthemes", "platforminputcontexts", "xcbglintegrations",
    "imageformats", "iconengines", "tls", "networkinformation", "generic", "styles",
}  # not: printsupport (drags in CUPS/Kerberos/GnuTLS) 
KEEP_PLUGIN_PREFIX = ("wayland-",)
NEEDED = re.compile(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]")


def needed(p: Path) -> list[str]:
    out = subprocess.run(["readelf", "-d", str(p)], capture_output=True, text=True).stdout
    return NEEDED.findall(out)


def rm(p: Path) -> int:
    if not p.exists() and not p.is_symlink():
        return 0
    size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
    shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
    return size


def main() -> int:
    pkg = Path(sys.argv[1]).resolve()
    qt = pkg / "Qt6"
    lib = qt / "lib"
    freed = 0

    # 1. PyQt modules we don't import
    for so in list(pkg.glob("*.abi3.so")) + list(pkg.glob("*.so")):
        name = so.name.split(".")[0]
        if name not in KEEP_MODULES:
            freed += rm(so)
    for pyi in list(pkg.glob("*.pyi")):
        if pyi.stem not in KEEP_MODULES and pyi.stem != "__init__":
            freed += rm(pyi)
    for d in ("bindings", "uic", "lupdate"):
        freed += rm(pkg / d)

    # 2. plugins
    plugins = qt / "plugins"
    if plugins.is_dir():
        for d in plugins.iterdir():
            if d.name not in KEEP_PLUGIN_DIRS and not d.name.startswith(KEEP_PLUGIN_PREFIX):
                freed += rm(d)

    # the GTK3 theme plugin would drag the whole GTK stack in; the portal/xdg themes are enough
    freed += rm(plugins / "platformthemes" / "libqgtk3.so")

    # plugins that need Qt libraries the wheels don't ship (embedded-device display, PDF images)
    for pat in ("platforms/libqeglfs*", "platforms/libqlinuxfb*", "platforms/libqkms*", "platforms/libqvnc*",
                "imageformats/libqpdf*"):
        for f in plugins.glob(pat):
            freed += rm(f)

    # 3. qml / translations (keep only WebEngine's en-US locale)
    for d in ("qml", "qsci", "metatypes", "modules", "libexec/../mkspecs"):
        freed += rm(qt / d)
    tr = qt / "translations"
    if tr.is_dir():
        for f in tr.iterdir():
            if f.name == "qtwebengine_locales":
                for pak in f.iterdir():
                    if pak.name not in ("en-US.pak",):
                        freed += rm(pak)
            else:
                freed += rm(f)

    # 4. Qt libs: keep the dependency closure of everything we kept
    roots = [p for p in pkg.glob("*.so")] + [p for p in plugins.rglob("*.so")] if plugins.is_dir() else []
    roots += [p for p in qt.rglob("QtWebEngineProcess")]
    roots += [p for p in pkg.glob("*.abi3.so")]
    present = {p.name: p for p in lib.iterdir() if p.is_file() or p.is_symlink()} if lib.is_dir() else {}
    keep: set[str] = set()
    stack = list(dict.fromkeys(roots))
    seen_files: set[Path] = set()
    while stack:
        f = stack.pop()
        if f in seen_files:
            continue
        seen_files.add(f)
        for n in needed(f):
            if n in present and n not in keep:
                keep.add(n)
                stack.append(present[n].resolve())
    for name, p in present.items():
        if name not in keep:
            freed += rm(p)
    print(f"pruned {freed / 1048576:.0f} MiB; kept {len(keep)} Qt/runtime libraries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
