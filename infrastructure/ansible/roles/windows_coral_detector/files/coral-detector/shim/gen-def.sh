#!/usr/bin/env bash
# gen-def.sh <real libusb-1.0.dll> > libusb-1.0.30.def
#
# Writes the shim's .def: every libusb_* export of the real DLL (undecorated
# names only) forwarded to libusbreal.dll, except libusb_set_option, which the
# shim implements. Re-run it, and rename the output, whenever the pinned libusb
# version changes.
set -euo pipefail
dll="${1:?usage: gen-def.sh <libusb-1.0.dll>}"
echo 'LIBRARY "libusb-1.0.dll"'
echo 'EXPORTS'
echo '  libusb_set_option'
x86_64-w64-mingw32-objdump -p "$dll" |
  sed -n '/\[Ordinal\/Name Pointer\] Table/,/^$/p' |
  awk '/\] libusb_/ { print $NF }' |
  grep -v '@' | grep -vx 'libusb_set_option' | sort -u |
  while read -r name; do printf '  %s = libusbreal.%s\n' "$name" "$name"; done
