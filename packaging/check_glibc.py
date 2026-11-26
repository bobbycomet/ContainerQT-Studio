#!/usr/bin/env python3
"""Fail if any ELF file in a tree needs a newer glibc than --max.

usage: check_glibc.py <dir> [--max 2.34]

Reads the GLIBC_x.y symbol versions each binary references (objdump -T), so it measures what the
dynamic loader will demand at run time, not what the build machine happens to have.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

VER = re.compile(rb"\bGLIBC_(\d+(?:\.\d+)+)\b")


def parse(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def is_elf(p: Path) -> bool:
    try:
        with p.open("rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def required(p: Path) -> tuple[int, ...]:
    out = subprocess.run(["objdump", "-T", str(p)], capture_output=True).stdout
    vs = [parse(m.decode()) for m in VER.findall(out)]
    return max(vs, default=())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--max", default="2.34")
    ns = ap.parse_args()
    limit = parse(ns.max)
    worst: tuple[int, ...] = ()
    bad: list[tuple[tuple[int, ...], Path]] = []
    n = 0
    for p in ns.root.rglob("*"):
        if p.is_file() and not p.is_symlink() and is_elf(p):
            n += 1
            need = required(p)
            worst = max(worst, need)
            if need > limit:
                bad.append((need, p))
    if n == 0:
        print(f"FAIL: no ELF files found under {ns.root} - nothing was checked")
        return 2
    shown = ".".join(map(str, worst)) if worst else "none"
    print(f"checked {n} ELF files; highest glibc requirement: {shown} (limit {ns.max})")
    for need, p in sorted(bad, reverse=True):
        print(f"  TOO NEW  GLIBC_{'.'.join(map(str, need))}  {p.relative_to(ns.root)}")
    if bad:
        print("FAIL: build in the AlmaLinux 9 builder (packaging/build-appimage.sh), not on a newer distro.")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
