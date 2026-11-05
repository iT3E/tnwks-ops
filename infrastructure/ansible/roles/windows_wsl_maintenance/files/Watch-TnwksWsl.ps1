# Watch-TnwksWsl.ps1
# Keeps the WSL "Ubuntu" distro (and therefore the OpenClaw gateway) alive.
#
# Why this exists: on 2026-07-25 06:04 a Store/Windows Update in-place upgrade
# of the WSL app (-> 2.7.11.0) tore down the WSL VM, flipped the WSL service
# start type to disabled, and RestartManager explicitly refused to restart
# wsl.exe ("Application SID does not match Conductor SID"). Windows never
# rebooted, so nothing on the Windows side ever brought WSL back. WSL stayed
# down ~38 hours until a human ran `wsl` by hand.
#
# Runs every 5 minutes (and at boot) as the desktop user (S4U, elevated): WSL
# distros are per-user, so SYSTEM's wsl.exe can't see Ubuntu. Idempotent and
# quiet: if everything is already healthy it logs one line and exits 0.
# Managed by tnwks-ops: the Ansible role windows_wsl_maintenance copies it to
# C:\ProgramData\tnwks-watchdog and owns its task tnwks-wsl-watchdog.
#
# Checks, in order:
#   1. WSL service (WSLService) is running and not disabled
#   2. Ubuntu appears in `wsl --list --running`
#   3. systemd inside Ubuntu reports the openclaw-gateway user unit active
#   4. The Aeotec Z-Stick is attached to WSL (usbip). The Coral TPU isn't on
#      usbip since 2026-11-03: a Windows service serves it to Frigate
#      (docs/wsl/coral-detector.md).
#
# A host without the gateway or the Z-Stick (WSL-02) runs it with -SkipGateway
# and -SkipUsb (task arguments, wsl_watchdog_args in host_vars): checks 3 and
# 4 and the worker-2 restart are skipped, so it only keeps WSL and the distro
# up.

[CmdletBinding()]
param(
    [string]$Distro  = 'Ubuntu',
    [string]$LogDir  = 'C:\ProgramData\tnwks-watchdog',
    [int]$MaxLogKB   = 1024,
    [switch]$SkipGateway,
    [switch]$SkipUsb,
    # A scheduled task holding a wsl.exe client open (tnwks-wsl-keepalive);
    # started again when it isn't running, e.g. after a wsl --shutdown.
    [string]$KeepAliveTask
)

$ErrorActionPreference = 'Continue'
$script:LogFile = Join-Path $LogDir 'watchdog.log'
$script:Actions = @()

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $line = ('{0}  [{1,-5}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message)
    try { Add-Content -Path $script:LogFile -Value $line -Encoding UTF8 } catch { }
    # MUST NOT use Write-Output: this function is called from inside functions
    # that return booleans. Emitting to the success stream pollutes those return
    # values into arrays (always truthy), which silently defeats every
    # `if (-not (Do-Thing))` guard in this script. Use the host/info stream only.
    [Console]::Error.WriteLine($line)
}

function Rotate-Log {
    if (-not (Test-Path $script:LogFile)) { return }
    $sizeKB = (Get-Item $script:LogFile).Length / 1KB
    if ($sizeKB -lt $MaxLogKB) { return }
    $archive = Join-Path $LogDir 'watchdog.log.1'
    Move-Item -Path $script:LogFile -Destination $archive -Force -ErrorAction SilentlyContinue
}

function Test-WslService {
    $svc = Get-Service -Name 'WSLService' -ErrorAction SilentlyContinue
    if (-not $svc) { Write-Log 'WSLService not found on this host.' 'WARN'; return $false }

    if ($svc.StartType -eq 'Disabled') {
        Write-Log 'WSLService StartType=Disabled (Store upgrade side effect); setting to Manual.' 'WARN'
        Set-Service -Name 'WSLService' -StartupType Manual -ErrorAction SilentlyContinue
        $script:Actions += 'wsl-service-starttype-restored'
    }

    if ($svc.Status -ne 'Running') {
        Write-Log ("WSLService status={0}; starting." -f $svc.Status) 'WARN'
        Start-Service -Name 'WSLService' -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        $script:Actions += 'wsl-service-started'
    }
    return $true
}

function Test-DistroRunning {
    # `wsl --list --running` emits UTF-16; force the console to decode it correctly.
    $prev = [Console]::OutputEncoding
    try { [Console]::OutputEncoding = [System.Text.Encoding]::Unicode } catch { }
    $running = & wsl.exe --list --running 2>&1 | Out-String
    try { [Console]::OutputEncoding = $prev } catch { }

    # Strip NULs in case encoding coercion did not take.
    $running = ($running -replace "`0", '')
    return ($running -match [regex]::Escape($Distro))
}

function Start-Distro {
    Write-Log ("distro '{0}' not running; starting it." -f $Distro) 'WARN'
    # Any command boots the VM. `exec true` is the cheapest and needs no shell.
    & wsl.exe -d $Distro -u root -e /bin/true 2>&1 | Out-Null
    $script:Actions += 'distro-started'

    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 2
        if (Test-DistroRunning) { Write-Log ("distro '{0}' is up." -f $Distro); return $true }
    }
    Write-Log ("distro '{0}' did NOT come up after 30s." -f $Distro) 'ERROR'
    return $false
}

function Test-Gateway {
    # The gateway is a *user* systemd unit owned by it3e (uid 1000) with
    # linger enabled, so it must be probed with that user's XDG_RUNTIME_DIR.
    $probe = 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active openclaw-gateway'
    $state = & wsl.exe -d $Distro -u root -e /bin/sh -c "runuser -u it3e -- env $probe" 2>&1 | Out-String
    $state = ($state -replace "`0", '').Trim()
    return ($state -eq 'active')
}

function Restart-Gateway {
    Write-Log 'openclaw-gateway not active; starting it.' 'WARN'
    $cmd = 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user start openclaw-gateway'
    & wsl.exe -d $Distro -u root -e /bin/sh -c "runuser -u it3e -- env $cmd" 2>&1 | Out-Null
    $script:Actions += 'gateway-started'
    Start-Sleep -Seconds 5
    if (Test-Gateway) { Write-Log 'openclaw-gateway is active.'; return $true }
    Write-Log 'openclaw-gateway still not active after start attempt.' 'ERROR'
    return $false
}

function Sync-UsbDevices {
    # Z-Stick for zwave-js-ui.
    $devices = @(
        @{ Name = 'Aeotec Z-Stick'; Id = '0658:0200' }
    )
    $state = & usbipd.exe state 2>&1 | Out-String
    foreach ($d in $devices) {
        # Attach is cheap and self-describing: it refuses if already attached.
        $out = & usbipd.exe attach --wsl $Distro --hardware-id $d.Id 2>&1 | Out-String
        $out = $out.Trim()
        if ($LASTEXITCODE -eq 0) {
            Write-Log ("attached {0} [{1}]." -f $d.Name, $d.Id) 'WARN'
            $script:Actions += ('usb-attached:' + $d.Id)
        }
        elseif ($out -match 'already attached') {
            # normal steady state; stay quiet
        }
        else {
            Write-Log ("could not attach {0} [{1}]: {2}" -f $d.Name, $d.Id, $out) 'WARN'
        }
    }
}

# ---------------- main ----------------
Rotate-Log

if (-not (Test-WslService)) {
    Write-Log 'aborting: WSL service unavailable.' 'ERROR'
    exit 1
}

$distroWasUp = Test-DistroRunning
if (-not $distroWasUp) {
    if (-not (Start-Distro)) { Write-Log 'aborting: distro failed to start.' 'ERROR'; exit 1 }
}

if ($KeepAliveTask) {
    $ka = Get-ScheduledTask -TaskName $KeepAliveTask -ErrorAction SilentlyContinue
    if (-not $ka) {
        Write-Log ("keepalive task '{0}' not found." -f $KeepAliveTask) 'WARN'
    }
    elseif ("$($ka.State)" -ne 'Running') {
        Write-Log ("keepalive task '{0}' is {1}; starting it." -f $KeepAliveTask, $ka.State) 'WARN'
        Start-ScheduledTask -TaskName $KeepAliveTask -ErrorAction SilentlyContinue
        $script:Actions += 'keepalive-started'
    }
}

if (-not $SkipUsb) {
    Sync-UsbDevices
}

if (-not $SkipGateway -and -not (Test-Gateway)) {
    Restart-Gateway | Out-Null
}

# If we had to cold-start the distro, restart worker-2 so the Z-Stick's serial
# node is visible inside the Talos node (it was added for the same problem
# with the Coral, see the 2026-07-23 Frigate incident).
if (-not $distroWasUp -and -not $SkipUsb) {
    Write-Log 'distro was cold-started; restarting worker-2 for USB bind mount.' 'WARN'
    & wsl.exe -d $Distro -u root -e /bin/sh -c 'docker restart homelab-wsl-worker-2' 2>&1 | Out-Null
    $script:Actions += 'worker2-restarted'
}

if ($script:Actions.Count -eq 0) {
    $healthy = @('wsl up')
    if ($KeepAliveTask) { $healthy += 'keepalive running' }
    if (-not $SkipGateway) { $healthy += 'gateway active' }
    if (-not $SkipUsb) { $healthy += 'Z-Stick attached' }
    Write-Log ('healthy ({0}).' -f ($healthy -join ', '))
} else {
    Write-Log ('recovery actions: ' + ($script:Actions -join ', ')) 'WARN'
}
exit 0
