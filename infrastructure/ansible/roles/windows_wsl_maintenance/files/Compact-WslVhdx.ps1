# Compact-WslVhdx.ps1
# Shrinks E:\WSL\Ubuntu\ext4.vhdx back to what the distro actually uses.
#
# Why: the vhdx is dynamically expanding and never shrinks on its own. On
# 2026-10-30 it was 347 GB with ~240 GB in use after pruning 91 GiB of unused
# Talos node images, and E: sat at 93% (WindowsDiskCritical). Run `sudo fstrim
# -v /` inside WSL first so the freed blocks are unmapped; diskpart's compact
# then drops them from the file.
#
# What it does: pauses the WSL watchdog task, shuts WSL down (the Talos
# cluster, Frigate, Home Assistant and the OpenClaw gateway all stop), runs
# diskpart compact on the vhdx, then starts WSL again and re-enables and
# kicks the watchdog and the USB attach task so the gateway and the Z-Stick
# come back. Docker restarts the Talos node containers itself
# (restart policy unless-stopped).
#
# Runs as the desktop user with the elevated token (WSL distros are per-user,
# diskpart needs admin). Log: C:\ProgramData\tnwks-vhdx-compact\compact.log
# Managed by tnwks-ops (Ansible role windows_wsl_maintenance). The task
# tnwks-vhdx-compact only has the one-shot trigger of its first run
# (2026-10-31), so start it by hand (Start-ScheduledTask tnwks-vhdx-compact)
# when E: needs the space back. It stops WSL and the whole cluster.
#
# Anything that calls wsl.exe starts Ubuntu again, so those are paused for
# the run: the UDP relays (wsl.exe hostname -I every 60s; this is what
# restarted WSL ~45s into the first run on 2026-10-31 03:00), the USB attach
# and LAN bridge tasks (event-triggered on the vNIC going away), and the
# usbipd --auto-attach watchers, which outlive wsl --shutdown. WSL starts new
# watchers itself when it boots.

[CmdletBinding()]
param(
    [string]$Distro,
    [string]$Vhdx,
    [string]$LogDir
)
if (-not $Distro) { $Distro = 'Ubuntu' }
if (-not $Vhdx)   { $Vhdx   = 'E:\WSL\Ubuntu\ext4.vhdx' }
if (-not $LogDir) { $LogDir = 'C:\ProgramData\tnwks-vhdx-compact' }
# The drive the vhdx is on, for the free-space log lines (E: on DESKTOP-HKE05KI).
$Drive = (Split-Path -Qualifier $Vhdx).TrimEnd(':')

$ErrorActionPreference = 'Continue'
$LogFile = Join-Path $LogDir 'compact.log'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

function Write-Log([string]$Message) {
    $line = ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Get-SizeGB([string]$Path) { [math]::Round((Get-Item $Path).Length / 1GB, 1) }

Write-Log "start: vhdx $(Get-SizeGB $Vhdx) GB, ${Drive}: free $([math]::Round((Get-PSDrive $Drive).Free / 1GB, 1)) GB"

Disable-ScheduledTask -TaskName 'tnwks-wsl-watchdog' | Out-Null
Write-Log 'watchdog task disabled'

$Paused = @('tnwks-udp-relay', 'tnwks-udp-relay-syslog', 'tnwks-usb-attach', 'tnwks-lan-bridge-sync')

try {
    foreach ($t in $Paused) {
        Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
        Disable-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue | Out-Null
    }
    Write-Log "paused tasks: $($Paused -join ', ')"
    Get-CimInstance Win32_Process -Filter "Name='usbipd.exe'" |
        Where-Object { $_.CommandLine -match 'auto-attach' } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Log "stopped usbipd watcher $($_.ProcessId): $($_.CommandLine)"
        }

    & wsl.exe --shutdown
    $deadline = (Get-Date).AddMinutes(3)
    do {
        Start-Sleep -Seconds 5
        $running = (& wsl.exe --list --running --quiet) -replace "`0", '' | Where-Object { $_.Trim() }
    } while ($running -and (Get-Date) -lt $deadline)
    if ($running) { throw "WSL still running after shutdown: $running" }
    Start-Sleep -Seconds 10
    Write-Log 'wsl shut down'

    $dp = Join-Path $LogDir 'compact.diskpart.txt'
    @(
        "select vdisk file=`"$Vhdx`"",
        'attach vdisk readonly',
        'compact vdisk',
        'detach vdisk'
    ) | Set-Content -Path $dp -Encoding ASCII
    $out = & diskpart.exe /s $dp 2>&1
    $out | ForEach-Object { if ($_.Trim()) { Write-Log "diskpart: $_" } }
    Write-Log "compacted: vhdx $(Get-SizeGB $Vhdx) GB, ${Drive}: free $([math]::Round((Get-PSDrive $Drive).Free / 1GB, 1)) GB"
}
catch {
    Write-Log "ERROR: $_"
    # Make sure the vdisk isn't left attached, or WSL can't open it.
    $dp2 = Join-Path $LogDir 'detach.diskpart.txt'
    @("select vdisk file=`"$Vhdx`"", 'detach vdisk') | Set-Content -Path $dp2 -Encoding ASCII
    & diskpart.exe /s $dp2 2>&1 | Out-Null
}
finally {
    & wsl.exe -d $Distro -e true
    Write-Log 'wsl started'
    Enable-ScheduledTask -TaskName 'tnwks-wsl-watchdog' | Out-Null
    foreach ($t in $Paused) { Enable-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue | Out-Null }
    Start-Sleep -Seconds 30
    # usb-attach re-attaches the Z-Stick; lan-bridge-sync points the
    # portproxy at WSL's (possibly new) IP; the relays run until stopped.
    foreach ($t in @('tnwks-usb-attach', 'tnwks-lan-bridge-sync', 'tnwks-udp-relay', 'tnwks-udp-relay-syslog', 'tnwks-wsl-watchdog')) {
        Start-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    }
    Write-Log "re-enabled and started: watchdog, $($Paused -join ', ')"
}
