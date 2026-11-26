# Build environment for the ContainerQT AppImage.
#
# AlmaLinux 9 ships glibc 2.34, so everything bundled into the AppImage from this image only
# needs glibc <= 2.34 and the result runs on any distro with glibc 2.34 or newer
# (Ubuntu 22.04+, Debian 12+, Fedora 35+, RHEL/Alma/Rocky 9+, Arch, openSUSE 15.4+ ...).
FROM almalinux:9

RUN dnf -y install --allowerasing \
        curl tar gzip xz findutils file which binutils desktop-file-utils \
        xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm \
        libxkbcommon libxkbcommon-x11 \
        nss nspr libXcomposite libXdamage libXrandr libXtst libXi libXcursor libXfixes \
        libxshmfence alsa-lib \
    && dnf clean all

# Libraries every desktop host provides and the AppImage deliberately does NOT bundle (GL, fonts, core X11,
# Wayland, glib...). They are installed here only so the build's smoke test can import Qt/WebEngine.
# strict=0: a package name that doesn't exist on this release must not fail the whole image.
RUN dnf -y install --allowerasing --setopt=strict=0 \
        mesa-libGL mesa-libEGL mesa-libgbm libglvnd-glx libglvnd-egl libglvnd-opengl libglvnd-gles libdrm \
        fontconfig freetype harfbuzz libX11 libX11-xcb libxcb libXext libXrender libXfixes libSM libICE \
        libXau libXdmcp glib2 dbus-libs libxkbfile pcre2 zlib libpng libjpeg-turbo \
        libwayland-client libwayland-cursor libwayland-egl libwayland-server \
    && dnf clean all

WORKDIR /src
ENTRYPOINT ["/bin/bash", "/src/packaging/in-builder.sh"]
