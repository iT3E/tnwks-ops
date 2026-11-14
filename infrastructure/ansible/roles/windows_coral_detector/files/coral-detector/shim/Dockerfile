# The shim's build toolchain, pinned so every controller (CI runner, the WSL
# workstation) builds the same bytes whatever its own OS ships. build.sh
# builds this image and compiles inside it.
#
# The base is pinned by digest (Renovate moves it) and apt by an Ubuntu
# snapshot date, so the mingw-w64 packages can't drift underneath. This
# toolchain produces coral_detector_shim_sha256 in the role defaults. Moving
# to another toolchain (a new snapshot or Ubuntu release) changes the DLL:
# re-pin that hash in the same PR and verify the detector after the apply.
FROM docker.io/library/ubuntu:26.04@sha256:065aa8626841f16057b78b027b464cd59c4fac36c204c72dcbf48d66f5fa8a96

# ca-certificates comes from the live archive (snapshot.ubuntu.com is
# https-only); it doesn't take part in the build.
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && apt-get update --snapshot 20261101T000000Z \
  && apt-get install -y --no-install-recommends --snapshot 20261101T000000Z \
    gcc-mingw-w64-x86-64-win32=13.2.0-6ubuntu1+26.1 \
    binutils-mingw-w64-x86-64=2.45.90.20260125-1ubuntu1+13.3 \
    mingw-w64-x86-64-dev=13.0.0-2ubuntu1 \
  && rm -rf /var/lib/apt/lists/*
