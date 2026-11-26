#!/bin/sh
# ContainerQT: copy one persistent environment's saved data to a new environment folder (cloning).
# Runs as root in a throw-away container. Mounts: source store at /src-store (read-only), target store at /store.
#   SRC_SLUG, SLUG   source and new environment slugs;  HOST_UID/HOST_GID  the host user's ids
set -e
: "${SRC_SLUG:?}" "${SLUG:?}" "${HOST_UID:?}" "${HOST_GID:?}"
for s in "$SRC_SLUG" "$SLUG"; do
    case "$s" in */*|.|..|"") echo "refusing odd slug '$s'" >&2; exit 1 ;; esac
done
FROM="${DQT_SRC_STORE:-/src-store}/$SRC_SLUG"
TO="${DQT_STORE:-/store}/$SLUG"
[ -f "$FROM/.dockerqt-env" ] || { echo "[clone] $FROM is not a ContainerQT Studio environment (is the drive mounted?)" >&2; exit 1; }
[ ! -e "$TO" ] || { echo "[clone] $TO already exists; refusing to overwrite" >&2; exit 1; }
echo "[clone] copying $SRC_SLUG -> $SLUG (this can take a few minutes) ..."
cp -a "$FROM" "$TO"
printf '%s\n' "$SLUG" > "$TO/.dockerqt-env"
chown "$HOST_UID:$HOST_GID" "$TO" "$TO/workspace" 2>/dev/null || true
echo "[clone] done"
