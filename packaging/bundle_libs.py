#!/usr/bin/env python3
"""Copy the system shared libraries that PyQt6's Qt libs/plugins need into the AppDir.

usage: bundle_libs.py <PyQt6 package dir> <destination lib dir>

Run it inside the glibc-2.34 builder image: everything copied is then built against glibc <= 2.34.
Libraries every desktop already has (glibc, GL/EGL, core X11, fontconfig, freetype, libstdc++ ...) are
deliberately left to the host, exactly as the AppImage "excludelist" recommends.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

EXCLUDE = re.compile(
    r"""^(
        ld-linux.*|libc|libm|libdl|libpthread|librt|libutil|libresolv|libnsl|libanl|libcrypt|
        libgcc_s|libstdc\+\+|
        libGL|libGLX|libGLdispatch|libEGL|libOpenGL|libGLESv2|libgbm|libdrm|libvulkan|
        libX11|libX11-xcb|libxcb|libXau|libXdmcp|libXext|libXrender|libXfixes|libXi|libSM|libICE|
        libfontconfig|libfreetype|libharfbuzz|libfribidi|libgraphite2|
        libglib-2\.0|libgobject-2\.0|libgio-2\.0|libgmodule-2\.0|libgthread-2\.0|
        libz|libexpat|libuuid|libselinux|libpcre2-8|libpcre|libdbus-1|libsystemd|libudev|libasound|
        libbrotli.*|libblkid|libmount|libcap|libcups|libavahi-.*|libgssapi_krb5|libkrb5.*|libk5crypto|libkeyutils|libcom_err|
        libwayland-.*|linux-vdso|libfuse.*
    )$""",
    re.X,
)


def is_elf(p: Path) -> bool:
    try:
        with p.open("rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def needed(elf: Path) -> list[tuple[str, str | None]]:
    out = subprocess.run(["ldd", str(elf)], capture_output=True, text=True).stdout
    res = []
    for line in out.splitlines():
        m = re.match(r"\s*(\S+)\s*=>\s*(\S+)?", line)
        if m:
            path = m.group(2)
            res.append((m.group(1), None if not path or path == "not" else path))
    return res


def main() -> int:
    pkg, dest = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    pkg_root = pkg.parent
    queue = [p for p in pkg.rglob("*") if p.is_file() and not p.is_symlink() and is_elf(p)]
    seen: set[str] = set()
    missing: set[str] = set()
    copied: list[str] = []
    while queue:
        elf = queue.pop()
        for soname, path in needed(elf):
            base = soname.split(".so")[0]
            if soname in seen or EXCLUDE.match(base):
                continue
            seen.add(soname)
            if path is None:
                missing.add(soname)
                continue
            real = Path(path).resolve()
            if pkg_root in real.parents or dest in real.parents:
                continue  # already shipped by the wheel / already bundled
            target = dest / soname
            shutil.copy2(real, target)
            copied.append(soname)
            queue.append(target)
    print(f"bundled {len(copied)} libraries into {dest}")
    for name in sorted(copied):
        print("  +", name)
    # "not found" for something the wheel itself ships (via $ORIGIN) is fine; anything else is worth a look.
    shipped = {p.name for p in pkg.rglob("*.so*")}
    real_missing = sorted(m for m in missing if m not in shipped and not m.startswith("libQt6"))
    if real_missing:
        print("WARNING: unresolved libraries (the host must provide them):", ", ".join(real_missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
