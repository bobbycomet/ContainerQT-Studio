#!/bin/sh
# ContainerQT container entrypoint (runs as root as PID 1's child; docker --init is PID 1).
trap 'exit 0' TERM INT

if [ "${DOCKERQT_DESKTOP:-0}" = "1" ]; then
    su -l -s /bin/sh dev -c "VNC_RESOLUTION='${VNC_RESOLUTION:-1440x900}' DQT_DE='${DQT_DE:-xfce}' DQT_WM='${DQT_WM:-}' DQT_SERVER='${DQT_SERVER:-x11}' /usr/local/bin/dockerqt-desktop start" \
        >/tmp/dockerqt-entrypoint.log 2>&1 || echo "[dockerqt] desktop failed to start, see /tmp/dockerqt-entrypoint.log"
fi

tail -f /dev/null &
wait $!
