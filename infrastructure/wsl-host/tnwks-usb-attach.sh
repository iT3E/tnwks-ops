#!/bin/bash
# tnwks-usb-attach — keep a homelab USB dongle attached into this WSL2 VM.
#
# Inverts the old Windows-driven model (a Scheduled Task on logon/vmswitch
# events that pushed the attach into WSL). The Windows side now does exactly
# one thing — start WSL — and WSL owns the rest: a systemd instance of this
# script runs per dongle and keeps it attached, so a WSL-only restart (dockerd
# bounce, `wsl --shutdown`, VM OOM) self-heals without any Windows event.
#
# Why this works without elevation: `usbipd bind` (the admin-only, persistent
# step) is already done — both dongles show Persisted=True. Only `usbipd
# attach` recurs, and attach into WSL needs no admin. usbipd.exe is reached
# over WSL/Windows interop by absolute path (appendWindowsPath is off here).
#
# Chain:
#   systemd (WantedBy=multi-user.target, Restart=always)
#     → modprobe vhci_hcd            # usbip client module; unloaded after some
#     → usbipd.exe attach --auto-attach --hardware-id <VID:PID>
#         (blocks; re-attaches on detach/replug — covers the Coral firmware
#          flip where the device re-enumerates at its runtime VID)
#
# Devices are matched by VID:PID, not busid — the busid changes across
# replug/reboot. See docs/usb-passthrough.md.

set -euo pipefail

# Absolute path: interop PATH injection (appendWindowsPath) is disabled in
# /etc/wsl.conf, so a bare `usbipd.exe` does not resolve.
USBIPD="${USBIPD_EXE:-/mnt/c/Program Files/usbipd-win/usbipd.exe}"

hwid="${1:-}"
if [[ -z "$hwid" ]]; then
  echo "usage: $0 <VID:PID>" >&2
  exit 2
fi

if [[ ! -x "$USBIPD" ]]; then
  echo "usbipd.exe not found at '$USBIPD' — is usbipd-win installed on the Windows host?" >&2
  exit 1
fi

# The usbip client module is what `attach` binds the remote device onto. It is
# not always autoloaded after a WSL kernel start, and a missing vhci_hcd is the
# exact failure that wedged USB after the last WSL restart.
modprobe vhci_hcd

# --auto-attach blocks and re-attaches on detach/unplug. systemd's Restart=always
# covers the case where usbipd.exe itself exits (e.g. device not yet connected),
# retrying until the dongle is present.
exec "$USBIPD" attach --wsl --auto-attach --hardware-id "$hwid"
