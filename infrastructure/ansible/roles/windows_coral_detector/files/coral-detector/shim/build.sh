#!/usr/bin/env bash
# build.sh <def file> <output dll>
#
# Builds the libusb shim with mingw-w64 inside the pinned toolchain image
# (Dockerfile next to this script), so the controller needs only Docker. The
# build is reproducible (no PE timestamp), so the same source and toolchain
# give the same bytes and the Ansible copy only changes when the source does.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
def="${1:?usage: build.sh <def> <out.dll>}"
out="${2:?usage: build.sh <def> <out.dll>}"

if [ -n "${CORAL_SHIM_IN_BUILDER:-}" ]; then
  exec x86_64-w64-mingw32-gcc -O2 -s -shared -Wl,--no-insert-timestamp -Wl,--build-id=none -Wl,--disable-auto-image-base \
    -o "$out" "$here/libusb-usbdk-shim.c" "$def"
fi

# Tagged by the Dockerfile's hash: a toolchain change builds a new image, an
# unchanged one reuses the local image.
image="tnwks-coral-shim-builder:$(sha256sum "$here/Dockerfile" | cut -c1-12)"
if ! docker image inspect "$image" >/dev/null 2>&1; then
  docker build --quiet --tag "$image" "$here" >/dev/null
fi

def_dir="$(cd "$(dirname "$def")" && pwd)"
out_dir="$(mkdir -p "$(dirname "$out")" && cd "$(dirname "$out")" && pwd)"
docker run --rm --network none --user "$(id -u):$(id -g)" \
  --env CORAL_SHIM_IN_BUILDER=1 \
  --volume "$here:/shim:ro" --volume "$def_dir:/def:ro" --volume "$out_dir:/out" \
  "$image" /shim/build.sh "/def/$(basename "$def")" "/out/$(basename "$out")"
