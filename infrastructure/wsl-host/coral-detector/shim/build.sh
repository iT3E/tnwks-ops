#!/usr/bin/env bash
# build.sh <def file> <output dll>
#
# Builds the libusb shim with mingw-w64 (apt: gcc-mingw-w64-x86-64). The build
# is reproducible (no PE timestamp), so the same source gives the same bytes and
# the Ansible copy only changes when the source does.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
def="${1:?usage: build.sh <def> <out.dll>}"
out="${2:?usage: build.sh <def> <out.dll>}"
x86_64-w64-mingw32-gcc -O2 -s -shared -Wl,--no-insert-timestamp -Wl,--build-id=none -Wl,--disable-auto-image-base \
  -o "$out" "$here/libusb-usbdk-shim.c" "$def"
