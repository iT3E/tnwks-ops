# Sync-TnwksUsbAttach.ps1
#
# Re-attaches the homelab USB dongles (Aeotec Z-Stick, Google Coral TPU) into
# the WSL2 VM via usbipd-win. `usbipd bind` is persistent across reboots but
# `usbipd attach --wsl` is per-session: after a Windows reboot or
# `wsl --shutdown` the dongles drop out of WSL and the frigate / zwave-js-ui
# pods CrashLoop (no Coral) or fail to mount (no /dev/ttyACM0). A Scheduled
# Task runs this at logon and whenever the WSL VM reconnects so the attach is
# restored automatically. See docs/usb-passthrough.md.
#
# Run elevated. Idempotent — devices already attached are left alone.
#
# Devices are matched by VID:PID (from the USB InstanceId), NOT busid, because
# the busid can change across reboots/replug.

[CmdletBinding()]
param(
    # VID:PID of each dongle to keep attached. The Coral lists two: 18d1:9302
    # (runtime, preferred) and 1a6e:089a (DFU bootloader, before firmware
    # loads). We attach whichever is present — see the Coral note below.
    [string[]]$TargetVidPids = @(
        '0658:0200',   # Aeotec Z-Stick Gen5 (Sigma Designs CDC ACM)
        '18d1:9302',   # Google Coral TPU — runtime VID (preferred)
        '1a6e:089a'    # Google Coral TPU — DFU bootloader VID
    )
)

$ErrorActionPreference = 'Stop'

function Require-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Sync-TnwksUsbAttach.ps1 must be run as Administrator.'
    }
}

function Get-UsbipdDevices {
    # `usbipd state` emits JSON: { "Devices": [ { BusId, InstanceId,
    # ClientIPAddress, ... } ] }. ClientIPAddress is non-empty when the device
    # is attached to a client (the WSL VM). InstanceId carries VID_/PID_.
    $raw = & usbipd state 2>$null
    if (-not $raw) { throw 'usbipd state returned nothing — is usbipd-win installed?' }
    $state = $raw | ConvertFrom-Json
    foreach ($d in $state.Devices) {
        $vidpid = $null
        if ($d.InstanceId -match 'VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})') {
            $vidpid = ('{0}:{1}' -f $Matches[1], $Matches[2]).ToLower()
        }
        [pscustomobject]@{
            BusId      = $d.BusId
            VidPid     = $vidpid
            Attached   = -not [string]::IsNullOrWhiteSpace($d.ClientIPAddress)
            Connected  = -not [string]::IsNullOrWhiteSpace($d.BusId)
        }
    }
}

Require-Admin

$devices = @(Get-UsbipdDevices | Where-Object { $_.Connected -and $_.VidPid })

foreach ($vidpid in $TargetVidPids) {
    $match = $devices | Where-Object { $_.VidPid -eq $vidpid.ToLower() } | Select-Object -First 1
    if (-not $match) {
        Write-Host "skip $vidpid - not connected"
        continue
    }
    if ($match.Attached) {
        Write-Host "ok   $vidpid (busid $($match.BusId)) - already attached"
        continue
    }
    # bind --force is persistent and safe to repeat; ensures the device is
    # shared before we attach. Then attach into the WSL VM.
    & usbipd bind --force --busid $match.BusId 2>&1 | Out-Null
    & usbipd attach --wsl --busid $match.BusId 2>&1 | Out-Null
    Write-Host "attached $vidpid (busid $($match.BusId))"
}

# Coral gotcha — automation cannot fully resolve the firmware flip.
# When the Coral is attached at the bootloader VID (1a6e:089a) and the frigate
# pod first opens it, libedgetpu uploads firmware; the device resets and
# re-enumerates at the runtime VID (18d1:9302), which on a usbip link pops the
# device back onto the Windows host as a *new, unattached* device. This task
# re-attaches it on the next trigger (WSL reconnect / logon), but if frigate is
# CrashLooping faster than the trigger fires, attach the runtime VID by hand:
#   usbipd detach --busid <busid>
#   usbipd list                       # wait for 18d1:9302
#   usbipd bind  --busid <busid> --force
#   usbipd attach --wsl --busid <busid>
# See docs/usb-passthrough.md "Coral gotcha".

Write-Host ''
Write-Host 'Final usbipd state:'
& usbipd list
