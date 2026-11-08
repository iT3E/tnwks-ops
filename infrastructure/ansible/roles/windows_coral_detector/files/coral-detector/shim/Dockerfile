# The shim's build toolchain, pinned so every controller (CI runner, the WSL
# workstation) builds the same bytes whatever its own OS ships. build.sh
# builds this image and compiles inside it.
#
# The base is pinned by digest (Renovate moves it) and apt by an Ubuntu
# snapshot date, so the mingw-w64 packages can't drift underneath. This
# toolchain produces coral_detector_shim_sha256 in the role defaults. Moving
# to another toolchain (a new snapshot or Ubuntu release) changes the DLL:
# re-pin that hash in the same PR and verify the detector after the apply.
FROM docker.io/library/ubuntu:26.04@sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c

# ca-certificates comes from the live archive (snapshot.ubuntu.com is
# https-only); it doesn't take part in the build.
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && apt-get update --snapshot 20261101T000000Z \
  && apt-get install -y --no-install-recommends --snapshot 20261101T000000Z \
    gcc-mingw-w64-x86-64-win32=13.2.0-6ubuntu1+26.1 \
    binutils-mingw-w64-x86-64=2.41.90.20240122-1ubuntu1+11.4 \
    mingw-w64-x86-64-dev=11.0.1-3build1 \
  && rm -rf /var/lib/apt/lists/*
