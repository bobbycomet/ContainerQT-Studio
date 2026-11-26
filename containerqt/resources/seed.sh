#!/bin/sh
# ContainerQT: copy an environment image's directories onto the drive the user chose ("persistent" mode).
#
# Runs as root INSIDE a throw-away container created from the environment image, with the chosen storage
# folder mounted at /store. Environment:
#   SLUG, HOST_UID, HOST_GID   the environment's slug and the host user's ids
#   DQT_RESET                  "reset" = wipe the system directories first (Rebuild); user data is kept
#
# Layout it creates:  /store/<slug>/{workspace,rootfs/<dir>...}  plus marker files.
# System dirs (replaced on Rebuild):  etc usr var opt bin sbin lib lib32 lib64 libx32 (those that are real dirs)
# User dirs (never wiped):            home root srv
set -e
: "${SLUG:?}" "${HOST_UID:?}" "${HOST_GID:?}"
STORE="${DQT_STORE:-/store}"   # the drive folder (mounted here by ContainerQT)
SRC="${DQT_SRC:-}"             # root of the system to copy ("" = this container's /); overridden by tests only
E="$STORE/$SLUG"
R="$E/rootfs"
mkdir -p "$R" "$E/workspace"

dirs=""
for d in etc usr var opt home root srv bin sbin lib lib32 lib64 libx32; do
    if [ -d "$SRC/$d" ] && [ ! -L "$SRC/$d" ]; then dirs="$dirs $d"; fi
done

if [ "${DQT_RESET:-}" = "reset" ]; then
    for d in etc usr var opt bin sbin lib lib32 lib64 libx32; do
        rm -rf "${R:?}/$d" "$R/.seeded.$d"
    done
fi

for d in $dirs; do
    if [ -e "$R/.seeded.$d" ]; then
        echo "[seed] /$d already saved"
        continue
    fi
    echo "[seed] copying /$d ..."
    rm -rf "${R:?}/$d"
    mkdir -p "$R/$d"
    cp -a "$SRC/$d/." "$R/$d/"
    touch "$R/.seeded.$d"
done

echo "$dirs" | tr -s ' ' '\n' | sed '/^$/d' > "$R/.dockerqt-dirs"
printf '%s\n' "$SLUG" > "$E/.dockerqt-env"
# Let the host user traverse/read the folders and own the workspace; the system dirs stay root-owned.
chmod 0755 "$E" "$R"
chown "$HOST_UID:$HOST_GID" "$E" "$E/workspace" 2>/dev/null || true
chmod 0644 "$E/.dockerqt-env" "$R/.dockerqt-dirs"
# If Docker had to create the storage folder itself (root-owned, holding nothing but this environment), hand it to
# the user so they can browse it. A folder that already existed, or holds anything else, is never touched.
if [ "$(stat -c %u "$STORE" 2>/dev/null)" = "0" ] && [ -z "$(ls -A "$STORE" | grep -v -x -F "$SLUG")" ]; then
    chown "$HOST_UID:$HOST_GID" "$STORE" 2>/dev/null || true
fi
echo "[seed] done: $(du -sh "$R" 2>/dev/null | cut -f1) saved on the drive"
