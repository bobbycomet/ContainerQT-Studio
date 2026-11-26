"""Build-script templates for the Build window, filtered by the environment's package family.

Every script runs inside the container as the `dev` user and receives:
  APP_NAME     application name
  APP_VERSION  version string
  SRC_DIR      project directory inside the container (default /workspace)
  OUT_DIR      where finished packages should be written (default /workspace/dist)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import paths

HEADER = r'''#!/usr/bin/env bash
# ContainerQT Studio build script - edit freely. Environment provided by ContainerQT Studio:
#   APP_NAME  APP_VERSION  SRC_DIR  OUT_DIR
set -euo pipefail
: "${APP_NAME:?}" "${APP_VERSION:?}" "${SRC_DIR:?}" "${OUT_DIR:?}"
ARCH="${ARCH:-$(uname -m)}"
TOOLS="$HOME/.cache/dockerqt-tools"
mkdir -p "$TOOLS" "$OUT_DIR"
cd "$SRC_DIR"

fetch() {  # fetch <url> <dest> : download once, mark executable
  [ -s "$2" ] || { echo "Downloading $1"; curl -fL --retry 3 -o "$2.part" "$1" && mv "$2.part" "$2"; }
  chmod +x "$2"
}
'''


@dataclass(frozen=True)
class Template:
    id: str
    title: str
    description: str
    families: tuple[str, ...]  # package families this makes sense on
    body: str

    @property
    def script(self) -> str:
        return HEADER + "\n" + self.body.strip("\n") + "\n"


ALL = ("apt", "dnf", "pacman", "zypper", "apk")
GLIBC = ("apt", "dnf", "pacman", "zypper")

_T: list[Template] = []


def _add(id: str, title: str, desc: str, families: tuple[str, ...], body: str) -> None:
    _T.append(Template(id, title, desc, families, body))


_add("appimage-appdir", "AppImage - from a prepared AppDir",
     "Wraps SRC_DIR/AppDir (AppRun, .desktop, icon) into an .AppImage with appimagetool.", GLIBC, r'''
APPDIR="${APPDIR:-$SRC_DIR/AppDir}"
[ -d "$APPDIR" ] || { echo "No AppDir found at $APPDIR (set APPDIR=... or create it)"; exit 1; }
fetch "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage" "$TOOLS/appimagetool"
export APPIMAGE_EXTRACT_AND_RUN=1
ARCH="$ARCH" "$TOOLS/appimagetool" "$APPDIR" "$OUT_DIR/${APP_NAME}-${APP_VERSION}-${ARCH}.AppImage"
echo "Built: $OUT_DIR/${APP_NAME}-${APP_VERSION}-${ARCH}.AppImage"
''')

_add("appimage-cmake", "AppImage - CMake project + linuxdeploy",
     "Builds with CMake/Ninja, installs into an AppDir and bundles libraries with linuxdeploy.", GLIBC, r'''
BUILD="$SRC_DIR/build-appimage"
APPDIR="$SRC_DIR/AppDir-build"
rm -rf "$APPDIR"
cmake -S "$SRC_DIR" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr
cmake --build "$BUILD" --parallel
DESTDIR="$APPDIR" cmake --install "$BUILD"
DESKTOP="$(find "$APPDIR/usr/share/applications" -name '*.desktop' | head -n1)"
[ -n "$DESKTOP" ] || { echo "Project installs no .desktop file; add one to its install rules"; exit 1; }
fetch "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-$ARCH.AppImage" "$TOOLS/linuxdeploy"
export APPIMAGE_EXTRACT_AND_RUN=1 LINUXDEPLOY_OUTPUT_VERSION="$APP_VERSION"
( cd "$OUT_DIR" && "$TOOLS/linuxdeploy" --appdir "$APPDIR" --desktop-file "$DESKTOP" --output appimage )
ls -1 "$OUT_DIR"/*.AppImage
''')

_add("appimage-python", "AppImage - Python app (uses host python3)",
     "Assembles an AppDir from a Python project and its pip requirements. Needs python3 on the target.", GLIBC, r'''
ENTRY="${ENTRY:-main.py}"          # script inside SRC_DIR to launch
ICON="${ICON:-}"                   # optional path to a .png icon
APPDIR="$SRC_DIR/AppDir-python"
rm -rf "$APPDIR"; mkdir -p "$APPDIR/usr/lib/$APP_NAME" "$APPDIR/usr/share/applications"
cp -r "$SRC_DIR"/. "$APPDIR/usr/lib/$APP_NAME/" 2>/dev/null || true
rm -rf "$APPDIR/usr/lib/$APP_NAME"/AppDir* "$APPDIR/usr/lib/$APP_NAME/.git"
if [ -f "$SRC_DIR/requirements.txt" ]; then
  python3 -m pip install --no-cache-dir --target "$APPDIR/usr/lib/$APP_NAME/_deps" -r "$SRC_DIR/requirements.txt"
fi
cat > "$APPDIR/AppRun" <<EOF
#!/bin/sh
HERE="\$(dirname "\$(readlink -f "\$0")")"
export PYTHONPATH="\$HERE/usr/lib/$APP_NAME/_deps:\$HERE/usr/lib/$APP_NAME:\${PYTHONPATH:-}"
exec python3 "\$HERE/usr/lib/$APP_NAME/$ENTRY" "\$@"
EOF
chmod +x "$APPDIR/AppRun"
cat > "$APPDIR/$APP_NAME.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Exec=AppRun
Icon=$APP_NAME
Categories=Utility;
EOF
if [ -n "$ICON" ] && [ -f "$ICON" ]; then cp "$ICON" "$APPDIR/$APP_NAME.png"; else
  python3 - "$APPDIR/$APP_NAME.png" <<'PY'
import struct, sys, zlib
w = h = 64
raw = b"".join(b"\x00" + bytes([40, 90, 200] * w) for _ in range(h))
def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
open(sys.argv[1], "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
PY
fi
fetch "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage" "$TOOLS/appimagetool"
export APPIMAGE_EXTRACT_AND_RUN=1
ARCH="$ARCH" "$TOOLS/appimagetool" "$APPDIR" "$OUT_DIR/${APP_NAME}-${APP_VERSION}-${ARCH}.AppImage"
''')

_add("deb-dpkg-deb", "Debian package - dpkg-deb from a staging tree",
     "Builds a .deb from SRC_DIR/staging (a directory laid out like the target filesystem).", ("apt",), r'''
STAGE="${STAGE:-$SRC_DIR/staging}"
MAINTAINER="${MAINTAINER:-$(whoami) <$(whoami)@localhost>}"
DESC="${DESC:-$APP_NAME packaged with ContainerQT Studio}"
DEPENDS="${DEPENDS:-}"
[ -d "$STAGE" ] || { echo "Create $STAGE with the files laid out as they should install (e.g. staging/usr/bin/app)"; exit 1; }
PKG="$(echo "$APP_NAME" | tr '[:upper:]_ ' '[:lower:]--')"
WORK="$(mktemp -d)"; cp -a "$STAGE"/. "$WORK"/
mkdir -p "$WORK/DEBIAN"
DEB_ARCH="$(dpkg --print-architecture)"
cat > "$WORK/DEBIAN/control" <<EOF
Package: $PKG
Version: $APP_VERSION
Section: utils
Priority: optional
Architecture: $DEB_ARCH
Maintainer: $MAINTAINER
${DEPENDS:+Depends: $DEPENDS}
Description: $DESC
EOF
fakeroot dpkg-deb --build "$WORK" "$OUT_DIR/${PKG}_${APP_VERSION}_${DEB_ARCH}.deb"
lintian "$OUT_DIR"/*.deb || true
ls -1 "$OUT_DIR"/*.deb
''')

_add("deb-debhelper", "Debian package - project with debian/ directory",
     "Installs build-dependencies and runs dpkg-buildpackage on a project that has debian/.", ("apt",), r'''
[ -d debian ] || { echo "SRC_DIR has no debian/ directory"; exit 1; }
sudo apt-get update
sudo mk-build-deps -ir -t "apt-get -y --no-install-recommends" debian/control
dpkg-buildpackage -us -uc -b
mv -v ../*.deb ../*.buildinfo ../*.changes "$OUT_DIR"/ 2>/dev/null || true
ls -1 "$OUT_DIR"/*.deb
''')

_add("rpm-rpmbuild", "RPM package - rpmbuild from a .spec",
     "Installs BuildRequires and builds binary RPMs from SRC_DIR/*.spec.", ("dnf", "zypper"), r'''
SPEC="$(ls "$SRC_DIR"/*.spec 2>/dev/null | head -n1)"
[ -n "$SPEC" ] || { echo "No .spec file in $SRC_DIR"; exit 1; }
TOP="$HOME/rpmbuild"; mkdir -p "$TOP"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
cp -f "$SRC_DIR"/* "$TOP/SOURCES/" 2>/dev/null || true
if command -v dnf >/dev/null; then sudo dnf builddep -y "$SPEC"; else sudo zypper -n si -d "$(basename "$SPEC" .spec)" || true; fi
rpmbuild -bb --define "_topdir $TOP" --define "version $APP_VERSION" "$SPEC"
find "$TOP/RPMS" -name '*.rpm' -exec cp -v {} "$OUT_DIR"/ \;
''')

_add("arch-makepkg", "Arch package - makepkg from a PKGBUILD",
     "Runs makepkg in SRC_DIR (PKGBUILD required) and collects the .pkg.tar.zst.", ("pacman",), r'''
[ -f PKGBUILD ] || { echo "No PKGBUILD in $SRC_DIR"; exit 1; }
makepkg -sf --noconfirm --needed
mv -v ./*.pkg.tar.* "$OUT_DIR"/
ls -1 "$OUT_DIR"
''')

_add("alpine-abuild", "Alpine package - abuild from an APKBUILD",
     "Generates a signing key if needed and builds an .apk with abuild.", ("apk",), r'''
[ -f APKBUILD ] || { echo "No APKBUILD in $SRC_DIR"; exit 1; }
sudo addgroup "$(whoami)" abuild 2>/dev/null || true
[ -d "$HOME/.abuild" ] || abuild-keygen -a -n
abuild -r
find "$HOME/packages" -name '*.apk' -exec cp -v {} "$OUT_DIR"/ \;
''')

_add("fpm-any", "fpm - convert a directory to deb / rpm / pacman / apk / tar",
     "Uses fpm (installed on demand via gem) to package SRC_DIR/staging into any supported format.", ALL, r'''
TARGET="${TARGET:-$(command -v dpkg >/dev/null && echo deb || (command -v rpmbuild >/dev/null && echo rpm || echo tar))}"
STAGE="${STAGE:-$SRC_DIR/staging}"
[ -d "$STAGE" ] || { echo "Create $STAGE laid out like the target filesystem"; exit 1; }
if ! command -v fpm >/dev/null; then
  gem install --no-document --user-install fpm
  export PATH="$(ruby -e 'print Gem.user_dir')/bin:$PATH"
fi
fpm -s dir -t "$TARGET" -n "$APP_NAME" -v "$APP_VERSION" -p "$OUT_DIR" -C "$STAGE" .
ls -1 "$OUT_DIR"
''')

_add("pyinstaller", "Single binary - PyInstaller",
     "Freezes a Python script into one executable inside a throw-away virtualenv.", GLIBC, r'''
ENTRY="${ENTRY:-main.py}"
python3 -m venv "$SRC_DIR/.venv-build"
. "$SRC_DIR/.venv-build/bin/activate"
pip install --upgrade pip pyinstaller
[ -f requirements.txt ] && pip install -r requirements.txt
pyinstaller --noconfirm --onefile --name "$APP_NAME" --distpath "$OUT_DIR" "$ENTRY"
ls -lh "$OUT_DIR/$APP_NAME"
''')

_add("cmake-install", "Compile - CMake/Ninja release + tarball",
     "Configures, builds and installs into a staging dir, then writes a .tar.gz.", ALL, r'''
BUILD="$SRC_DIR/build-release"; STAGE="$(mktemp -d)"
cmake -S "$SRC_DIR" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr
cmake --build "$BUILD" --parallel
DESTDIR="$STAGE" cmake --install "$BUILD"
tar -C "$STAGE" -czf "$OUT_DIR/${APP_NAME}-${APP_VERSION}-${ARCH}.tar.gz" .
ls -lh "$OUT_DIR"
''')

_add("tarball", "Archive - tar.gz of the project",
     "Simplest possible package: a versioned tarball of SRC_DIR (build output excluded).", ALL, r'''
tar --exclude='./dist' --exclude='./.git' --exclude='*.AppImage' \
    -czf "$OUT_DIR/${APP_NAME}-${APP_VERSION}.tar.gz" -C "$SRC_DIR" .
ls -lh "$OUT_DIR"
''')

_add("blank", "Blank script",
     "An empty script with the ContainerQT Studio environment variables ready.", ALL, r'''
echo "Building $APP_NAME $APP_VERSION from $SRC_DIR into $OUT_DIR"
# your commands here
''')


def templates_for(family: str) -> list[Template]:
    return [t for t in _T if family in t.families]


def get(template_id: str) -> Template | None:
    return next((t for t in _T if t.id == template_id), None)


def all_templates() -> list[Template]:
    return list(_T)


# -- user scripts -------------------------------------------------------------------
def list_user_scripts() -> list[Path]:
    return sorted(paths.user_scripts_dir().glob("*.sh"))


def save_user_script(name: str, text: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_ ." else "_" for c in name).strip() or "script"
    p = paths.user_scripts_dir() / (safe if safe.endswith(".sh") else safe + ".sh")
    p.write_text(text, encoding="utf-8")
    return p
