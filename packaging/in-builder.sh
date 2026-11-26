#!/usr/bin/env bash
# Does the actual AppImage build. Runs inside the builder container (or locally via --local).
#
# AppDir layout:
#   AppRun                      launcher (see packaging/AppRun)
#   usr/python/                 relocatable CPython (python-build-standalone) with PyQt6 + ContainerQT installed
#   usr/lib/                    system libraries Qt needs that hosts may lack (xcb-cursor, nss, ...)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
ROOT="$PWD"

ARCH="${ARCH:-$(uname -m)}"
PYTHON_SERIES="${PYTHON_SERIES:-3.12}"
VERSION="$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' containerqt/__init__.py)"
APPID="io.github.bobbycomet.dockerqt"
APPDIR="$ROOT/build/AppDir"
CACHE="$ROOT/build/cache"
OUT="$ROOT/dist/ContainerQT-${VERSION}-${ARCH}.AppImage"

say() { printf '\n==> %s\n' "$*"; }

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr" "$CACHE" "$ROOT/dist"

# --- 1. relocatable Python ----------------------------------------------------------------------
say "Fetching python-build-standalone (CPython ${PYTHON_SERIES}, ${ARCH})"
PBS_SHA=""
if [[ -z "${PBS_URL:-}" ]]; then
    # latest-release.json names the newest release; its SHA256SUMS lists every asset (the GitHub API
    # percent-encodes the '+' in asset names and paginates, so it is not used here).
    PBS_PREFIX="$(curl -fsSL https://raw.githubusercontent.com/astral-sh/python-build-standalone/latest-release/latest-release.json \
        | sed -n 's/.*"asset_url_prefix": *"\([^"]*\)".*/\1/p')" || true
    [[ -n "$PBS_PREFIX" ]] || { echo "error: could not read python-build-standalone latest-release.json (network?)" >&2; exit 1; }
    SUMS="$(curl -fsSL "$PBS_PREFIX/SHA256SUMS")" || { echo "error: could not download $PBS_PREFIX/SHA256SUMS" >&2; exit 1; }
    LINE="$(grep -E "cpython-${PYTHON_SERIES//./\\.}\.[0-9]+\+[0-9]+-${ARCH}-unknown-linux-gnu-install_only\.tar\.gz\$" <<<"$SUMS" | sort -k2 -V | tail -n1)" || true
    [[ -n "$LINE" ]] || { echo "error: no CPython ${PYTHON_SERIES} ${ARCH} build in $PBS_PREFIX; set PBS_URL or PYTHON_SERIES" >&2; exit 1; }
    PBS_SHA="${LINE%% *}"
    PBS_NAME="${LINE##* }"
    PBS_URL="$PBS_PREFIX/${PBS_NAME//+/%2B}"
fi
echo "    $PBS_URL"
PBS_TGZ="$CACHE/$(basename "${PBS_URL//%2B/+}")"
[[ -f "$PBS_TGZ" ]] || curl -fL --retry 3 -o "$PBS_TGZ" "$PBS_URL"
if [[ -n "$PBS_SHA" ]]; then
    echo "$PBS_SHA  $PBS_TGZ" | sha256sum -c - || { rm -f "$PBS_TGZ"; echo "error: checksum mismatch" >&2; exit 1; }
fi
tar -xzf "$PBS_TGZ" -C "$APPDIR/usr"        # creates usr/python
PY="$APPDIR/usr/python/bin/python3"
"$PY" --version

# --- 2. install ContainerQT + PyQt6 ------------------------------------------------------------------
say "Installing ContainerQT and PyQt6 (+WebEngine for the embedded desktop)"
"$PY" -m pip install --no-cache-dir --upgrade pip >/dev/null
"$PY" -m pip install --no-cache-dir --only-binary=:all: "PyQt6>=6.5" "PyQt6-WebEngine>=6.5"
"$PY" -m pip install --no-cache-dir --no-deps "$ROOT"
rm -rf "$ROOT/build/lib" "$ROOT"/*.egg-info

# --- 3. trim -----------------------------------------------------------------------------------
say "Trimming"
SP="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
LIBPY="$(dirname "$SP")"
rm -rf "$LIBPY"/test "$LIBPY"/idlelib "$LIBPY"/tkinter "$LIBPY"/turtledemo "$LIBPY"/lib2to3 \
       "$APPDIR/usr/python/share" "$APPDIR/usr/python/include"
"$PY" -m pip uninstall -y pip setuptools wheel >/dev/null 2>&1 || true
find "$APPDIR" -name '__pycache__' -type d -prune -exec rm -rf {} +
# Drop every Qt module/plugin/translation ContainerQT doesn't use (keeps WebEngine + en-US locale)
"$PY" "$ROOT/packaging/prune_qt.py" "$SP/PyQt6"

# --- 4. bundle system libs Qt/QtWebEngine need ----------------------------------------------------
say "Bundling shared libraries"
mkdir -p "$APPDIR/usr/lib"
"$PY" "$ROOT/packaging/bundle_libs.py" "$SP/PyQt6" "$APPDIR/usr/lib"

# --- 5. launcher, desktop entry, icon, metadata ---------------------------------------------------
say "Writing AppDir metadata"
install -m 0755 packaging/AppRun "$APPDIR/AppRun"
install -m 0644 "packaging/$APPID.desktop" "$APPDIR/$APPID.desktop"
install -m 0644 containerqt/resources/dockerqt.png "$APPDIR/$APPID.png"
ln -sf "$APPID.png" "$APPDIR/.DirIcon"
install -Dm 0644 "packaging/$APPID.metainfo.xml" "$APPDIR/usr/share/metainfo/$APPID.appdata.xml"
install -Dm 0644 containerqt/resources/dockerqt.png "$APPDIR/usr/share/icons/hicolor/512x512/apps/$APPID.png"
install -Dm 0644 containerqt/resources/dockerqt-256.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/$APPID.png"
command -v desktop-file-validate >/dev/null && desktop-file-validate "$APPDIR/$APPID.desktop" || true

# the installed package must carry every launcher/helper script and the icon
for f in entrypoint.sh desktop.sh seed.sh purge.sh dockerqt-helper dockerqt.png \
         io.github.bobbycomet.dockerqt.policy; do
    [[ -f "$SP/containerqt/resources/$f" ]] || { echo "error: containerqt/resources/$f missing from the installed package" >&2; exit 1; }
done

# --- 5b. smoke test: the launcher, the app and the embedded-desktop engine must all start --------------
say "Smoke test"
"$APPDIR/AppRun" --version
# List every shared library that cannot be found, all at once, before trying to import anything.
MISSING="$(find "$SP/PyQt6" "$APPDIR/usr/lib" -type f \( -name '*.so*' -o -name QtWebEngineProcess \) -print0 \
    | LD_LIBRARY_PATH="$APPDIR/usr/lib" xargs -0 -n1 ldd 2>/dev/null | sed -n 's/^\s*\(\S*\) => not found.*/\1/p' | sort -u)"
if [[ -n "$MISSING" ]]; then
    echo "Libraries not found in the builder:"; sed 's/^/    /' <<<"$MISSING"
    echo "(host-provided ones belong in packaging/Dockerfile.builder; Qt-internal ones are normal and are ignored)"
    REAL="$(grep -v '^libQt6\|^libicu\|^libav\|^libsw\|^libvpx\|^libwebp\|^libopus\|^libminizip\|^libevent\|^libre2\|^libsnappy\|^libjsoncpp\|^libxslt\|^libxml2\|^libprotobuf\|^libdav1d' <<<"$MISSING" || true)"
    [[ -z "$REAL" ]] || { echo "error: required system libraries are missing from the builder image" >&2; exit 1; }
fi
LD_LIBRARY_PATH="$APPDIR/usr/lib" QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 "$PY" -s - <<'PYEOF'
import sys
from PyQt6 import QtWebEngineWidgets          # must precede QApplication
from PyQt6.QtCore import QT_VERSION_STR, QCoreApplication, Qt
from PyQt6.QtWidgets import QApplication
QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(["dockerqt-smoke"])
view = QtWebEngineWidgets.QWebEngineView()
import containerqt.gui.main_window  # noqa: F401
print("Qt", QT_VERSION_STR, "- QtWebEngine and ContainerQT import OK")
PYEOF
for lib in libxcb-cursor.so.0 libnss3.so; do
    [[ -e "$APPDIR/usr/lib/$lib" ]] || echo "WARNING: $lib was not bundled (needed on hosts that lack it)"
done

# --- 6. the glibc gate ----------------------------------------------------------------------------
say "Checking glibc requirements (max allowed: 2.34)"
"$PY" "$ROOT/packaging/check_glibc.py" "$APPDIR" --max 2.34

# --- 7. appimagetool ------------------------------------------------------------------------------
say "Building the AppImage"
TOOL="$CACHE/appimagetool-$ARCH.AppImage"
[[ -x "$TOOL" ]] || { curl -fL --retry 3 -o "$TOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage" && chmod +x "$TOOL"; }
# --appimage-extract-and-run: containers usually have no FUSE
ARCH="$ARCH" "$TOOL" --appimage-extract-and-run --no-appstream "$APPDIR" "$OUT"
chmod +x "$OUT"

if [[ -n "${HOST_UID:-}" ]]; then chown -R "$HOST_UID:${HOST_GID:-$HOST_UID}" "$ROOT/dist" "$ROOT/build" || true; fi
say "Done: $OUT"
