#!/bin/sh
# dockerqt-desktop start|stop|restart|status
#
# Starts the environment's desktop and exposes it through noVNC (websockify) on :6080.
# Runs as the unprivileged "dev" user. Selected with:
#   DQT_DE      xfce kde gnome cinnamon mate lxqt i3 openbox sway labwc
#   DQT_WM      window manager / compositor to use instead of the desktop's own ("" = native)
#   DQT_SERVER  x11 (Xvnc + session)  |  wayland (headless wlroots compositor + wayvnc)
#   VNC_RESOLUTION  e.g. 1440x900

RES="${VNC_RESOLUTION:-1440x900}"
DE="${DQT_DE:-xfce}"
WM="${DQT_WM:-}"
SERVER="${DQT_SERVER:-x11}"
RUN=/tmp/dockerqt-desktop
NOVNC=/opt/novnc
mkdir -p "$RUN"

have() { command -v "$1" >/dev/null 2>&1; }
alive() { [ -f "$RUN/$1.pid" ] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }

# command that starts a window manager (for replacing a desktop's own)
wm_cmd() {
    case "$1" in
        xfwm4) echo xfwm4 ;;
        kwin) echo kwin_x11 ;;
        mutter) echo "mutter --x11 --replace" ;;
        muffin) echo "muffin --replace" ;;
        marco) echo "marco --replace" ;;
        openbox) echo openbox ;;
        i3) echo i3 ;;
        *) echo "$1" ;;
    esac
}
wm_bin() { wm_cmd "$1" | cut -d' ' -f1; }

stop_all() {
    for n in novnc session wayvnc compositor xvnc; do
        if [ -f "$RUN/$n.pid" ]; then
            kill "$(cat "$RUN/$n.pid")" 2>/dev/null
            rm -f "$RUN/$n.pid"
        fi
    done
    for p in xfce4-session xfwm4 xfdesktop xfce4-panel plasmashell kwin_x11 kwin_wayland startplasma-x11 \
             gnome-session gnome-shell mutter cinnamon cinnamon-session muffin mate-session marco mate-panel \
             startlxqt lxqt-session openbox i3 sway labwc wayvnc Xvnc Xtigervnc websockify; do
        pkill -u "$(id -u)" -x "$p" 2>/dev/null
    done
    rm -f /tmp/.X1-lock /tmp/.X11-unix/X1
}

wait_for() { # wait_for <seconds*5> <test command...>
    n="$1"; shift
    while [ "$n" -gt 0 ]; do
        "$@" && return 0
        sleep 0.2
        n=$((n - 1))
    done
    return 1
}

# Run "$1" (shell snippet) then exec "$2" inside one D-Bus session
in_session() {
    pre="$1"; cmd="$2"
    script="$pre
exec $cmd"
    if have dbus-run-session; then
        nohup dbus-run-session -- sh -c "$script" >"$RUN/session.log" 2>&1 &
    elif have dbus-launch; then
        nohup dbus-launch --exit-with-session sh -c "$script" >"$RUN/session.log" 2>&1 &
    else
        nohup sh -c "$script" >"$RUN/session.log" 2>&1 &
    fi
    echo $! >"$RUN/session.pid"
}

first_of() { for b in "$@"; do if have "$b"; then echo "$b"; return 0; fi; done; return 1; }

# ---------------------------------------------------------------------------- X11 sessions
start_x11_session() {
    pre=""
    export DISPLAY=:1
    case "$DE" in
        xfce)
            cmd="$(first_of startxfce4 xfce4-session)"
            if [ -n "$WM" ] && [ "$WM" != xfwm4 ]; then
                pre="xfconf-query -c xfce4-session -p /sessions/Failsafe/Client0_Command -n -a -t string -s '$(wm_cmd "$WM")' || true"
            fi ;;
        kde)
            cmd="$(first_of startplasma-x11 startkde)"
            export XDG_CURRENT_DESKTOP=KDE KDE_FULL_SESSION=true KWIN_COMPOSE=Q LIBGL_ALWAYS_SOFTWARE=1
            if [ -n "$WM" ] && [ "$WM" != kwin ]; then
                KDEWM="$(command -v "$(wm_bin "$WM")")"; export KDEWM
            fi ;;
        gnome)
            cmd="$(first_of gnome-session)"
            export XDG_CURRENT_DESKTOP=GNOME XDG_SESSION_TYPE=x11 LIBGL_ALWAYS_SOFTWARE=1 GNOME_SHELL_SESSION_MODE=gnome ;;
        cinnamon)
            cmd="$(first_of cinnamon-session-cinnamon2d cinnamon-session)"
            export XDG_CURRENT_DESKTOP=X-Cinnamon LIBGL_ALWAYS_SOFTWARE=1 ;;
        mate)
            cmd="$(first_of mate-session)"
            if [ -n "$WM" ] && [ "$WM" != marco ]; then
                pre="gsettings set org.mate.session.required-components windowmanager '$(wm_bin "$WM")' || true"
            fi ;;
        lxqt)
            cmd="$(first_of startlxqt)"
            if [ -n "$WM" ] && [ "$WM" != openbox ]; then
                f="$HOME/.config/lxqt/session.conf"
                mkdir -p "$HOME/.config/lxqt"
                if grep -q '^window_manager=' "$f" 2>/dev/null; then
                    sed -i "s|^window_manager=.*|window_manager=$(wm_bin "$WM")|" "$f"
                elif grep -q '^\[General\]' "$f" 2>/dev/null; then
                    sed -i "/^\[General\]/a window_manager=$(wm_bin "$WM")" "$f"
                else
                    printf '[General]\nwindow_manager=%s\n' "$(wm_bin "$WM")" >>"$f"
                fi
            fi ;;
        i3)
            cmd="$(first_of i3)"
            if [ ! -f "$HOME/.config/i3/config" ] && [ -f /etc/i3/config ]; then
                mkdir -p "$HOME/.config/i3" && cp /etc/i3/config "$HOME/.config/i3/config"
            fi ;;
        openbox)
            cmd="$(first_of openbox-session openbox)"
            if [ ! -f "$HOME/.config/openbox/autostart" ] && have tint2; then
                mkdir -p "$HOME/.config/openbox" && echo "tint2 &" >"$HOME/.config/openbox/autostart"
            fi ;;
        *) echo "Unknown desktop '$DE'" >&2; return 1 ;;
    esac
    if [ -z "$cmd" ]; then
        echo "The '$DE' session program is not installed in this environment" >&2
        return 1
    fi
    in_session "$pre" "$cmd"
}

start_x11() {
    XVNC="$(command -v Xvnc || command -v Xtigervnc)"
    if [ -z "$XVNC" ]; then
        echo "Xvnc (TigerVNC) is not installed in this environment" >&2
        return 1
    fi
    nohup "$XVNC" :1 -geometry "$RES" -depth 24 -SecurityTypes None -rfbport 5901 -AlwaysShared \
        >"$RUN/xvnc.log" 2>&1 &
    echo $! >"$RUN/xvnc.pid"
    if ! wait_for 50 test -S /tmp/.X11-unix/X1; then
        echo "Xvnc did not start; see $RUN/xvnc.log" >&2
        return 1
    fi
    start_x11_session
}

# ---------------------------------------------------------------------------- Wayland sessions
start_wayland() {
    case "$DE" in sway) comp=sway ;; labwc) comp=labwc ;; *) echo "'$DE' has no Wayland session here" >&2; return 1 ;; esac
    have "$comp" || { echo "$comp is not installed in this environment" >&2; return 1; }
    have wayvnc || { echo "wayvnc is not installed in this environment" >&2; return 1; }

    unset DISPLAY WAYLAND_DISPLAY
    export XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP="$comp" WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 \
           WLR_RENDERER=pixman WLR_NO_HARDWARE_CURSORS=1 LIBSEAT_BACKEND=noop XDG_RUNTIME_DIR

    if [ "$comp" = sway ]; then
        # Alt as the Mod key (Super is usually taken by the browser/host); never overwrite a user's config
        if [ ! -f "$HOME/.config/sway/config" ] && [ -f /etc/sway/config ]; then
            mkdir -p "$HOME/.config/sway"
            sed 's/^set \$mod Mod4/set $mod Mod1/' /etc/sway/config >"$HOME/.config/sway/config"
        fi
    fi
    nohup dbus-run-session -- "$comp" >"$RUN/session.log" 2>&1 &
    echo $! >"$RUN/compositor.pid"
    if ! wait_for 100 sh -c 'ls "$XDG_RUNTIME_DIR" 2>/dev/null | grep -q "^wayland-[0-9]*$"'; then
        echo "$comp did not start; see $RUN/session.log" >&2
        return 1
    fi
    WAYLAND_DISPLAY="$(ls "$XDG_RUNTIME_DIR" | grep -m1 '^wayland-[0-9]*$')"
    export WAYLAND_DISPLAY
    # headless output size
    if [ "$comp" = sway ] && have swaymsg; then
        swaymsg -s "$(ls "$XDG_RUNTIME_DIR"/sway-ipc.*.sock 2>/dev/null | head -n1)" output HEADLESS-1 resolution "$RES" >/dev/null 2>&1
    elif have wlr-randr; then
        wlr-randr --output HEADLESS-1 --custom-mode "$RES" >/dev/null 2>&1
    fi
    nohup wayvnc 127.0.0.1 5901 >"$RUN/wayvnc.log" 2>&1 &
    echo $! >"$RUN/wayvnc.pid"
}

start_all() {
    if alive novnc && { alive xvnc || alive wayvnc; }; then
        echo "desktop already running"
        return 0
    fi
    stop_all

    XDG_RUNTIME_DIR="/tmp/runtime-$(id -u)"
    export XDG_RUNTIME_DIR
    mkdir -p "$XDG_RUNTIME_DIR"
    chmod 700 "$XDG_RUNTIME_DIR"

    if [ "$SERVER" = wayland ]; then
        start_wayland || return 1
    else
        start_x11 || return 1
    fi

    if [ ! -d "$NOVNC" ]; then
        echo "noVNC missing in $NOVNC" >&2
        return 1
    fi
    PYTHONPATH="$NOVNC/utils/websockify" nohup python3 -m websockify --web "$NOVNC" 6080 localhost:5901 \
        >"$RUN/novnc.log" 2>&1 &
    echo $! >"$RUN/novnc.pid"
    echo "desktop started on :6080 ($DE, ${WM:-native} window manager, $SERVER, ${RES})"
}

case "$1" in
    start) start_all ;;
    stop) stop_all; echo "desktop stopped" ;;
    restart) stop_all; start_all ;;
    status)
        if alive novnc && { alive xvnc || alive wayvnc; }; then echo running; else echo stopped; exit 1; fi
        ;;
    *) echo "usage: $0 start|stop|restart|status" >&2; exit 2 ;;
esac
