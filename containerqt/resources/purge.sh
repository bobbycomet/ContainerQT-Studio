#!/bin/sh
# ContainerQT: delete an environment's saved data (root-owned files the host user cannot remove itself).
# Runs as root in a throw-away container with the storage folder at /store; SLUG selects the environment.
set -e
: "${SLUG:?}"
case "$SLUG" in */*|.|..|"") echo "refusing odd slug '$SLUG'" >&2; exit 1 ;; esac
[ -f "/store/$SLUG/.dockerqt-env" ] || { echo "[purge] /store/$SLUG is not a ContainerQT Studio environment folder; leaving it alone"; exit 0; }
rm -rf "/store/$SLUG"
echo "[purge] removed the saved data of $SLUG"
