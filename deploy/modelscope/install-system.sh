#!/bin/sh
set -eu

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates curl xz-utils python3 tini xvfb xauth fonts-dejavu-core \
    libfuse2t64 libgl1 libgl1-mesa-dri libegl1 libopengl0 libglu1-mesa \
    libxi6 libxxf86vm1 libxfixes3 libxkbcommon-x11-0 libxcursor1 libxrandr2 \
    libxinerama1 libsm6 libxrender1 libgtk-3-0t64 libwebkit2gtk-4.1-0 \
    libgstreamer-plugins-base1.0-0 libsecret-1-0 libcurl4t64 libdbus-1-3
rm -rf /var/lib/apt/lists/*
