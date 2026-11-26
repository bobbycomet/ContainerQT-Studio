#!/usr/bin/env bash
# Build ContainerQT-<version>-<arch>.AppImage into ./dist
#
#   packaging/build-appimage.sh            build inside an AlmaLinux 9 container (glibc 2.34)  [default]
#   packaging/build-appimage.sh --local    build on this machine (the result is only as portable as this
#                                          machine's glibc; the glibc check will tell you)
#
# Needs Docker (or Podman via DOCKER=podman) for the default mode and network access to GitHub + PyPI.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

if [[ "${1:-}" == "--local" ]]; then
    exec bash packaging/in-builder.sh
fi

DOCKER="${DOCKER:-docker}"
command -v "$DOCKER" >/dev/null || { echo "error: '$DOCKER' not found (use --local to build without a container)" >&2; exit 1; }

"$DOCKER" build -t containerqt-builder -f packaging/Dockerfile.builder packaging
mkdir -p dist
"$DOCKER" run --rm \
    -v "$PWD":/src \
    -e PBS_URL -e PYTHON_SERIES -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
    containerqt-builder
echo
ls -lh dist/*.AppImage
