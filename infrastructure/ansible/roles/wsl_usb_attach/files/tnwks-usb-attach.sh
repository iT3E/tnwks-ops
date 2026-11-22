#!/bin/bash
# tnwks-usb-attach: keep a homelab USB dongle attached into this WSL2 VM.
#
# A systemd instance of this script runs per dongle (tnwks-usb-attach@<VID:PID>)
# and keeps it attached, so a start of WSL, a restart of the host and a
# replug recover with no Windows event and no person.
#
# It attaches with the Linux usbip client that usbipd-win installs for WSL
# (WSL/usbip next to usbipd.exe), straight against the usbipd service of the
# host (TCP 3240). It never runs usbipd.exe: `usbipd attach --wsl` first asks
# the Windows service control manager whether the usbipd service runs, and
# the service control manager and the service grant that to interactive and
# service logons, SYSTEM and the Administrators only. A batch logon of a
# standard account (the account that owns the distro when the tasks run with
# no elevation, wsl_start_unelevated) holds none of them. The usbip client
# needs no Windows right: the service exports each device that is bound, and
# the play binds each device (role windows_usb_attach). This is what
# `usbipd attach --wsl --auto-attach` itself runs in the distro (usbipd-win
# 4.3.0, Usbipd/Wsl.cs and WSL/auto-attach.sh), with the bus id found again
# at each try, as the bus id changes at a replug.
#
# Each round (every TNWKS_USB_INTERVAL seconds, 5 by default):
#   1. The device is attached when a USB device with the VID:PID sits on the
#      vhci_hcd bus of this kernel: nothing to do.
#   2. Otherwise: load vhci_hcd, find the address of the host (127.0.0.1 in
#      mirrored networking mode, else the default gateway, which is the host
#      in NAT mode), find the bus id of the VID:PID in the devices that the
#      host exports (`usbip list -r`), and attach it (`usbip attach`).
# A failure (no route yet, the host not reachable yet, the device absent or
# attached to another client) is logged once and tried again next round.
#
# The worker-2 container binds the whole /dev of the distro at /hostdev, so
# the device node reaches zwave-js-ui with no restart of the container
# (docs/wsl/usb-passthrough.md).
#
# Usage: tnwks-usb-attach [--once] <VID:PID>
#   --once: one round, then exit 0 when the device is attached and 1 when not
#   (the Windows backup task runs this through wsl.exe).

set -euo pipefail
shopt -s nullglob

# The usbip client of usbipd-win, a static Linux build, by absolute path
# (interop appendWindowsPath is off in /etc/wsl.conf).
USBIP="${USBIP_CLIENT:-/mnt/c/Program Files/usbipd-win/WSL/usbip}"
USB_DEVICES="${TNWKS_USB_DEVICES:-/sys/bus/usb/devices}"
INTERVAL="${TNWKS_USB_INTERVAL:-5}"

HWID=""
HOST=""
BUSID=""
LAST_ERROR=""
REPORTED=""

one_line() {
  local text="${1//$'\n'/ }"
  echo "${text%"${text##*[![:space:]]}"}"
}

report() {
  if [[ "$1" != "$REPORTED" ]]; then
    echo "$1"
    REPORTED="$1"
  fi
}

# The device sits on the vhci_hcd bus: a usbip import of this kernel.
is_attached() {
  local dev vid pid
  for dev in "$USB_DEVICES"/*; do
    [[ -r "$dev/idVendor" && -r "$dev/idProduct" ]] || continue
    read -r vid <"$dev/idVendor" || continue
    read -r pid <"$dev/idProduct" || continue
    [[ "${vid,,}:${pid,,}" == "$HWID" ]] || continue
    [[ "$(readlink -f "$dev")" == */vhci_hcd* ]] && return 0
  done
  return 1
}

host_address() {
  local mode="" route
  if command -v wslinfo >/dev/null 2>&1; then
    mode="$(wslinfo --networking-mode 2>/dev/null || true)"
  fi
  if [[ "${mode//[[:space:]]/}" == "mirrored" ]]; then
    HOST="127.0.0.1"
    return 0
  fi
  route="$(ip -4 route show default 2>/dev/null || true)"
  if [[ "$route" =~ ^default\ via\ ([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+) ]]; then
    HOST="${BASH_REMATCH[1]}"
    return 0
  fi
  LAST_ERROR="no default route to the host yet"
  return 1
}

# The bus id of the VID:PID among the devices that the host exports. usbip
# prints each as "<busid>: <vendor> : <product> (<vid>:<pid>)".
find_busid() {
  local out line
  if ! out="$("$USBIP" list -r "$HOST" 2>&1)"; then
    LAST_ERROR="the host $HOST does not answer yet: $(one_line "$out")"
    return 1
  fi
  while IFS= read -r line; do
    if [[ "$line" =~ ^[[:space:]]*([0-9]+-[0-9.]+):\ .*\(([0-9A-Fa-f]{4}:[0-9A-Fa-f]{4})\)[[:space:]]*$ ]] \
      && [[ "${BASH_REMATCH[2],,}" == "$HWID" ]]; then
      BUSID="${BASH_REMATCH[1]}"
      return 0
    fi
  done <<<"$out"
  LAST_ERROR="the host $HOST exports no device $HWID (absent, or not bound)"
  return 1
}

try_attach() {
  local out i
  if ! out="$(modprobe vhci_hcd 2>&1)"; then
    LAST_ERROR="modprobe vhci_hcd: $(one_line "$out")"
    return 1
  fi
  host_address || return 1
  find_busid || return 1
  if ! out="$("$USBIP" attach -r "$HOST" -b "$BUSID" 2>&1)"; then
    LAST_ERROR="usbip attach of $BUSID from $HOST: $(one_line "$out")"
    return 1
  fi
  # The kernel enumerates the device a moment after the import.
  for ((i = 0; i < 10; i++)); do
    is_attached && return 0
    sleep 1
  done
  LAST_ERROR="usbip attached $BUSID from $HOST, and no $HWID is on the vhci_hcd bus"
  return 1
}

round() {
  if is_attached; then
    report "$HWID is attached"
    return 0
  fi
  if try_attach; then
    echo "$HWID is attached (bus id $BUSID from $HOST)"
    REPORTED="$HWID is attached"
    return 0
  fi
  report "$HWID is not attached: $LAST_ERROR"
  return 1
}

main() {
  local once=0
  if [[ "${1:-}" == "--once" ]]; then
    once=1
    shift
  fi
  HWID="${1:-}"
  HWID="${HWID,,}"
  if [[ ! "$HWID" =~ ^[0-9a-f]{4}:[0-9a-f]{4}$ ]]; then
    echo "usage: $0 [--once] <VID:PID>" >&2
    return 2
  fi
  if [[ ! -x "$USBIP" ]]; then
    echo "the usbip client of usbipd-win is not at '$USBIP': is usbipd-win installed on the Windows host?" >&2
    return 1
  fi
  if ((once)); then
    round
    return
  fi
  while :; do
    round || true
    sleep "$INTERVAL"
  done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
