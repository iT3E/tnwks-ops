#requires -Version 5.1
<#
Sync-TnwksUsbAttach.ps1

Re-attaches the Aeotec Z-Stick into the WSL2 VM via usbipd-win and bounces the
Talos worker container so the new device actually reaches the zwave-js-ui pod.
Managed by tnwks-ops: the Ansible role windows_usb_attach copies it to
C:\ProgramData\tnwks-usb-attach and owns its Scheduled Task tnwks-usb-attach
(docs/wsl/usb-passthrough.md).

`usbipd bind` is persistent across reboots but `usbipd attach --wsl` is
per-session: after a Windows reboot or `wsl --shutdown` the dongle drops out of
WSL and zwave-js-ui fails to mount /dev/ttyACM0. The WSL unit
tnwks-usb-attach@0658:0200 re-attaches it from inside WSL; this task is the
Windows-side backup, and the part that restarts worker-2 after a fresh attach.
It runs at logon, every 15 minutes and 15s after a WSL vNIC reset (Hyper-V
VmSwitch EventID 232, System log). Idempotent; safe to run repeatedly.

The Coral TPU no longer goes through usbip: since 2026-11-03 it is owned by a
Windows WinUSB driver and served by the tnwks-coral-detector-<id> services
(docs/wsl/coral-detector.md). Its handling here (the DFU -> runtime firmware
flip below) is off unless both -CoralDfuVidPid and -CoralRuntimeVidPid are
given, which is only for the usbip fallback in coral-detector.md.

Run elevated (usbipd bind/attach need Administrator).

Devices are matched by VID:PID from the USB InstanceId, NOT busid, because the
busid changes across reboots and replugs.

--- 2026-08-17 rewrite ---------------------------------------------------------
Three bugs made the previous version fail every single run (LastTaskResult 1):

1. STDERR WAS FATAL. The script set $ErrorActionPreference = 'Stop' and then
   piped native output with `2>&1`. usbipd writes *informational* lines to
   stderr, e.g. "usbipd: info: Device with busid '1-6' was already shared."
   Under 'Stop' that becomes a terminating NativeCommandError. The Z-Stick is
   first in the device list, so the script died on device #1 and NEVER REACHED
   THE CORAL. Native calls now go through Invoke-Native, which forces
   'Continue' locally and inspects the real exit code instead.

2. NO LOG. Failures left no trace, so "result 1" was undiagnosable. Everything
   now appends to sync.log (rotated at 512 KB).

3. FLIP WAS UNHANDLED, and the worker was never restarted. See below.

--- Coral firmware flip --------------------------------------------------------
The Coral enumerates at a DFU/bootloader VID (1a6e:089a). The first time
frigate opens it, libedgetpu uploads firmware; the device resets and
re-enumerates at the runtime VID (18d1:9302), which on a usbip link pops it back
onto the Windows host as a NEW, UNATTACHED device.

Observed across the 2026-07-23 and 2026-08-01 incidents:
  * The pod's flash attempt is what primes the device.
  * A detach + settle AFTER a flash attempt is what actually completes the flip.
  * worker-2's /dev/bus/usb is a bind mount snapshot taken at container start,
    so a device attached later never propagates in. The container must be
    restarted while the runtime VID is already attached, otherwise the pod sees
    a device node but `lsusb` inside the pod shows nothing.

This script now walks that state machine: attach at whichever VID is present,
wait for the flip, force it with detach + settle if it stalls, then restart the
worker container once the runtime VID is attached. A cooldown in sync-state.json
keeps the periodic trigger from restart-looping the node.

--- Task principal -------------------------------------------------------------
This must NOT run as SYSTEM. WSL distributions are registered per user, so
SYSTEM's `wsl.exe` sees no distro and both `usbipd attach --wsl` and the
`docker restart` fail. Register-TnwksUsbAttachTask.ps1 runs it as the desktop
user with RunLevel Highest.
#>
[CmdletBinding()]
param(
    [string]$ZStickVidPid       = '0658:0200',   # Aeotec Z-Stick Gen5 (Sigma Designs CDC ACM)
    # Coral over usbip (fallback only, see the header): 1a6e:089a (DFU
    # bootloader) and 18d1:9302 (runtime). Empty = leave the Coral alone.
    [string]$CoralDfuVidPid     = '',
    [string]$CoralRuntimeVidPid = '',
    [string]$WorkerContainer    = 'homelab-wsl-worker-2',
    [string]$WslDistro          = 'Ubuntu',
    [int]   $FlipTimeoutSec     = 90,
    [int]   $RestartCooldownMin = 15,
    # NOTE: do NOT default these with $PSScriptRoot. Under `powershell.exe -File`
    # (which is how the Scheduled Task invokes this) $PSScriptRoot is empty at
    # param-binding time, so Join-Path throws before the script body runs. That
    # produced LastTaskResult=1 with no log entry at all, because the failure
    # happened before the try/finally that calls Save-Log. Resolved below instead.
    [string]$LogPath,
    [string]$StatePath,
    [switch]$DryRun,
    [switch]$NoWorkerRestart
)

$ErrorActionPreference = 'Stop'

# Resolve the script directory defensively: $PSScriptRoot is reliable in the body
# but empty in param defaults under -File; $MyInvocation covers odd invocations;
# the literal is the last resort so logging never silently disappears.
$scriptDir = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($scriptDir) -and $MyInvocation.MyCommand.Path) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if ([string]::IsNullOrWhiteSpace($scriptDir)) {
    $scriptDir = 'C:\ProgramData\tnwks-usb-attach'
}
if ([string]::IsNullOrWhiteSpace($LogPath))   { $LogPath   = Join-Path $scriptDir 'sync.log' }
if ([string]::IsNullOrWhiteSpace($StatePath)) { $StatePath = Join-Path $scriptDir 'sync-state.json' }

$script:LogBuffer = New-Object System.Collections.Generic.List[string]

function Write-Log {
    param(
        [Parameter(Mandatory)][string]$Message,
        [ValidateSet('INFO', 'OK', 'WARN', 'ERROR')][string]$Level = 'INFO'
    )
    $line = '{0} [{1,-5}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    $script:LogBuffer.Add($line) | Out-Null
    Write-Host $line
}

function Save-Log {
    if ($script:LogBuffer.Count -eq 0) { return }
    try {
        $dir = Split-Path -Parent $LogPath
        if ($dir -and -not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        if ((Test-Path $LogPath) -and ((Get-Item $LogPath).Length -gt 512KB)) {
            Move-Item -Path $LogPath -Destination "$LogPath.1" -Force
        }
        Add-Content -Path $LogPath -Value $script:LogBuffer -Encoding UTF8
    } catch {
        Write-Host "could not write log to ${LogPath}: $($_.Exception.Message)"
    }
}

# Native commands are invoked here so that informational stderr never turns into
# a terminating error. This is the fix for the bug that killed every prior run.
function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    try {
        $raw = & $FilePath @Arguments 2>&1
        $code = $LASTEXITCODE
        $text = (@($raw) | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
    } catch {
        $code = -1
        $text = $_.Exception.Message
    } finally {
        $ErrorActionPreference = $previous
    }
    [pscustomobject]@{ ExitCode = $code; Output = $text }
}

function Invoke-Usbipd {
    param([string[]]$Arguments = @())
    Invoke-Native -FilePath 'usbipd' -Arguments $Arguments
}

function Test-Admin {
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-UsbipdDevices {
    # `usbipd state` emits JSON: { "Devices": [ { BusId, InstanceId,
    # ClientIPAddress, ... } ] }. ClientIPAddress is non-empty when the device is
    # attached to a client (the WSL VM). InstanceId carries VID_/PID_.
    $result = Invoke-Usbipd @('state')
    if ($result.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($result.Output)) {
        Write-Log "usbipd state failed (exit $($result.ExitCode)): $($result.Output)" 'ERROR'
        return @()
    }
    try {
        $state = $result.Output | ConvertFrom-Json
    } catch {
        Write-Log "could not parse usbipd state JSON: $($_.Exception.Message)" 'ERROR'
        return @()
    }
    foreach ($device in $state.Devices) {
        $vidPid = $null
        if ($device.InstanceId -match 'VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})') {
            $vidPid = ('{0}:{1}' -f $Matches[1], $Matches[2]).ToLower()
        }
        [pscustomobject]@{
            BusId     = $device.BusId
            VidPid    = $vidPid
            Attached  = -not [string]::IsNullOrWhiteSpace($device.ClientIPAddress)
            Connected = -not [string]::IsNullOrWhiteSpace($device.BusId)
        }
    }
}

function Find-Device {
    param([Parameter(Mandatory)][string]$VidPid)
    Get-UsbipdDevices |
        Where-Object { $_.Connected -and $_.VidPid -eq $VidPid.ToLower() } |
        Select-Object -First 1
}

# Returns Present/Changed. Changed=$true means we newly attached it, which is the
# signal that the worker container's bind-mount snapshot is now stale.
function Set-DeviceAttached {
    param(
        [Parameter(Mandatory)][string]$VidPid,
        [Parameter(Mandatory)][string]$Label
    )
    $device = Find-Device -VidPid $VidPid
    if (-not $device) {
        Write-Log "$Label ($VidPid): not connected" 'INFO'
        return [pscustomobject]@{ Present = $false; Changed = $false }
    }
    if ($device.Attached) {
        Write-Log "$Label ($VidPid busid $($device.BusId)): already attached" 'OK'
        return [pscustomobject]@{ Present = $true; Changed = $false }
    }
    if ($DryRun) {
        Write-Log "$Label ($VidPid busid $($device.BusId)): would bind + attach [dry run]" 'INFO'
        return [pscustomobject]@{ Present = $true; Changed = $true }
    }

    # bind --force is persistent and safe to repeat; it just ensures the device is
    # shared before we attach it into the WSL VM.
    $bind = Invoke-Usbipd @('bind', '--force', '--busid', $device.BusId)
    if ($bind.ExitCode -ne 0) {
        Write-Log "$Label bind returned exit $($bind.ExitCode): $($bind.Output)" 'WARN'
    }
    $attach = Invoke-Usbipd @('attach', '--wsl', '--busid', $device.BusId)
    if ($attach.ExitCode -ne 0) {
        Write-Log "$Label attach FAILED (exit $($attach.ExitCode)): $($attach.Output)" 'ERROR'
        if ($attach.Output -match 'no distribution|not installed|WSL') {
            Write-Log 'attach could not reach WSL - if this ran as SYSTEM, re-register the task to run as the desktop user' 'ERROR'
        }
        return [pscustomobject]@{ Present = $true; Changed = $false }
    }
    Write-Log "$Label ($VidPid busid $($device.BusId)): attached" 'OK'
    return [pscustomobject]@{ Present = $true; Changed = $true }
}

# Walks the DFU -> runtime flip. Returns $true when the worker container needs a
# restart to pick the device up.
function Resolve-CoralAttachment {
    if (Find-Device -VidPid $CoralRuntimeVidPid) {
        Write-Log 'Coral is at runtime VID' 'INFO'
        return (Set-DeviceAttached -VidPid $CoralRuntimeVidPid -Label 'Coral (runtime)').Changed
    }

    if (-not (Find-Device -VidPid $CoralDfuVidPid)) {
        Write-Log 'Coral absent at both VIDs - check that it is physically plugged in' 'WARN'
        return $false
    }

    Write-Log 'Coral is at DFU VID; firmware flip to runtime VID required' 'INFO'
    $dfu = Set-DeviceAttached -VidPid $CoralDfuVidPid -Label 'Coral (DFU)'

    if ($DryRun) {
        Write-Log 'would poll for DFU -> runtime flip and force it with detach + settle [dry run]' 'INFO'
        return $dfu.Changed
    }

    # frigate opening the device uploads firmware, which resets it to the runtime
    # VID. Give that a chance to happen on its own first.
    $deadline = (Get-Date).AddSeconds($FlipTimeoutSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        if (Find-Device -VidPid $CoralRuntimeVidPid) {
            Write-Log 'Coral flipped to runtime VID on its own' 'OK'
            break
        }
    }

    if (-not (Find-Device -VidPid $CoralRuntimeVidPid)) {
        # A detach after a flash attempt is what actually completes the reset.
        Write-Log "no flip within ${FlipTimeoutSec}s; forcing it with detach + settle" 'INFO'
        $current = Find-Device -VidPid $CoralDfuVidPid
        if ($current) {
            $detach = Invoke-Usbipd @('detach', '--busid', $current.BusId)
            if ($detach.ExitCode -ne 0) {
                Write-Log "detach returned exit $($detach.ExitCode): $($detach.Output)" 'WARN'
            }
            Start-Sleep -Seconds 8
        }
    }

    if (Find-Device -VidPid $CoralRuntimeVidPid) {
        Set-DeviceAttached -VidPid $CoralRuntimeVidPid -Label 'Coral (runtime)' | Out-Null
        # The flip itself invalidates the worker's snapshot, so restart regardless
        # of whether the attach above was a no-op.
        return $true
    }

    Write-Log 'Coral still at DFU VID after forced detach; re-attaching so frigate can retry the flash' 'WARN'
    $retry = Set-DeviceAttached -VidPid $CoralDfuVidPid -Label 'Coral (DFU)'
    return $retry.Changed
}

function Get-SyncState {
    if (-not (Test-Path $StatePath)) { return $null }
    try { return (Get-Content -Path $StatePath -Raw | ConvertFrom-Json) } catch { return $null }
}

function Set-SyncState {
    param([Parameter(Mandatory)]$State)
    if ($DryRun) { return }
    try {
        $State | ConvertTo-Json | Set-Content -Path $StatePath -Encoding UTF8
    } catch {
        Write-Log "could not persist state to ${StatePath}: $($_.Exception.Message)" 'WARN'
    }
}

function Restart-WorkerContainer {
    $state = Get-SyncState
    if ($state -and $state.LastWorkerRestart) {
        try {
            $elapsed = ((Get-Date) - [datetime]$state.LastWorkerRestart).TotalMinutes
            if ($elapsed -lt $RestartCooldownMin) {
                Write-Log ('skipping {0} restart - last was {1:N1} min ago, cooldown is {2} min' -f $WorkerContainer, $elapsed, $RestartCooldownMin) 'WARN'
                return
            }
        } catch {
            Write-Log 'could not read last restart timestamp; proceeding' 'WARN'
        }
    }

    if ($DryRun) {
        Write-Log "would restart $WorkerContainer [dry run]" 'INFO'
        return
    }

    Write-Log "restarting $WorkerContainer so its /dev/bus/usb snapshot includes the device" 'INFO'
    $restart = Invoke-Native -FilePath 'wsl.exe' -Arguments @('-d', $WslDistro, '--exec', 'docker', 'restart', $WorkerContainer)
    if ($restart.ExitCode -ne 0) {
        Write-Log "restart of $WorkerContainer FAILED (exit $($restart.ExitCode)): $($restart.Output)" 'ERROR'
        Write-Log 'if this ran as SYSTEM, wsl.exe cannot see per-user distros - re-register the task to run as the desktop user' 'ERROR'
        return
    }
    Write-Log "$WorkerContainer restarted" 'OK'
    Set-SyncState ([pscustomobject]@{ LastWorkerRestart = (Get-Date).ToString('o') })
}

# Main body lives in a function so early `return` cannot skip the final `exit`.
# At script scope a bare `return` ends the script, which would have swallowed the
# nonzero exit code and reported success to Task Scheduler.
function Invoke-Sync {
    Write-Log '=== tnwks-usb-attach sync starting ==='
    Write-Log ('identity={0} elevated={1} dryRun={2} distro={3}' -f `
        [Security.Principal.WindowsIdentity]::GetCurrent().Name, (Test-Admin), [bool]$DryRun, $WslDistro)

    if (-not (Test-Admin)) {
        Write-Log 'not running as Administrator - usbipd bind/attach will fail. Aborting.' 'ERROR'
        return 1
    }

    $probe = Invoke-Usbipd @('--version')
    if ($probe.ExitCode -ne 0) {
        Write-Log "usbipd not usable (exit $($probe.ExitCode)): $($probe.Output)" 'ERROR'
        return 1
    }
    Write-Log "usbipd version $($probe.Output.Trim())"

    $needsRestart = $false

    # Z-Stick: plain attach. zwave-js-ui needs /dev/ttyACM0, which is also a
    # snapshot in the worker's bind mount, so a fresh attach means a restart too.
    $zstick = Set-DeviceAttached -VidPid $ZStickVidPid -Label 'Z-Stick'
    if ($zstick.Changed) { $needsRestart = $true }

    if ($CoralDfuVidPid -and $CoralRuntimeVidPid) {
        if (Resolve-CoralAttachment) { $needsRestart = $true }
    } else {
        Write-Log 'Coral: not handled here (Windows-native detector service)' 'INFO'
    }

    if ($needsRestart -and $NoWorkerRestart) {
        Write-Log 'device attachment changed but worker restart suppressed (-NoWorkerRestart)' 'INFO'
    } elseif ($needsRestart) {
        Restart-WorkerContainer
    } else {
        Write-Log 'no attachment changes; worker restart not needed' 'OK'
    }

    $final = Invoke-Usbipd @('list')
    Write-Log 'final usbipd state:'
    foreach ($line in ($final.Output -split "`r?`n")) {
        if ($line.Trim()) { Write-Log "  $line" }
    }
    Write-Log '=== tnwks-usb-attach sync done ==='
    return 0
}

$exitCode = 1
try {
    $exitCode = Invoke-Sync
} catch {
    Write-Log "unhandled error: $($_.Exception.Message)" 'ERROR'
    $exitCode = 1
} finally {
    Save-Log
}

exit $exitCode
