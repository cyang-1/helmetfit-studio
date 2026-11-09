#!/bin/sh
set -eu

test "$(dpkg --print-architecture)" = amd64
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"

blender_release=https://download.blender.org/release/Blender4.5/blender-4.5.3-linux-x64.tar.xz
bambu_release=https://github.com/bambulab/BambuStudio/releases/download/v02.07.00.55/BambuStudio_ubuntu-24.04-v02.07.00.55-20260514170313.AppImage
curl --fail --show-error --silent --location --retry 4 --retry-all-errors \
  "${BLENDER_URL:-$blender_release}" -o blender.tar.xz
echo '975c58fcb244273838534bba771e64ad87739216b0f9b39a888531a49a72d845  blender.tar.xz' | sha256sum -c -
install -d /opt/blender
tar -xJf blender.tar.xz --strip-components=1 -C /opt/blender

curl --fail --show-error --silent --location --retry 4 --retry-all-errors \
  "${BAMBU_URL:-$bambu_release}" -o bambu.AppImage
echo 'ef3753b0bcb0e74f6234f6edd552033cbf77ec4d1a65810935fb50199705e6c6  bambu.AppImage' | sha256sum -c -
chmod 755 bambu.AppImage
./bambu.AppImage --appimage-extract >/dev/null
mv squashfs-root /opt/bambu
test -x /opt/bambu/AppRun
profile=$(find /opt/bambu -type f -path '*/profiles/BBL/machine/Bambu Lab P1S 0.4 nozzle.json')
test -f "$profile"
ln -s "$(dirname "$(dirname "$profile")")" /opt/bambu-bbl-profiles
