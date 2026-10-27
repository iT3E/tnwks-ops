# Register-TnwksLanBridgeTask.ps1
#
# One-shot setup: installs Sync-TnwksLanBridge.ps1 to C:\ProgramData\tnwks-lan-bridge
# and registers a Scheduled Task that re-syncs the portproxy rules whenever the
# WSL VM IP can change: at user logon, and when the Hyper-V vmswitch reconnects
# a VM NIC. Run elevated. Re-running is safe: it replaces the task and script,
# so re-run it after editing Sync-TnwksLanBridge.ps1.
#
# 2026-10-26 fixes (the task had never worked as intended):
#   - The vmswitch trigger listened on Microsoft-Windows-Hyper-V-VmSwitch-Operational,
#     which never receives EventID 232. VmSwitch 232 is written to the System
#     log, so that trigger never fired. It now subscribes to System.
#   - It ran as SYSTEM, whose wsl.exe cannot see the per-user `Ubuntu` distro,
#     so Get-WslIpv4 failed (last result 0xFFFD0000). It now runs as the desktop
#     user (S4U, elevated), like tnwks-udp-relay and tnwks-usb-attach.
#   - The script ran from the \\wsl$ share, which the task can't rely on before
#     WSL is up. It is copied to C:\ProgramData like the UDP relay.
#   - No $PSScriptRoot parameter default (empty during binding under -File).

[CmdletBinding()]
param(
    # No $PSScriptRoot default: it is empty during param binding under
    # `powershell.exe -File`, so the path is resolved below instead.
    [string]$SourceScript,
    [string]$InstallDir = 'C:\ProgramData\tnwks-lan-bridge',
    [string]$TaskName   = 'tnwks-lan-bridge-sync',
    # WSL distros are per-user, so the sync runs as the desktop user (S4U:
    # no stored password, works without an interactive logon).
    [string]$RunAsUser  = "$env:USERDOMAIN\$env:USERNAME"
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($SourceScript)) {
    $here = $PSScriptRoot
    if ([string]::IsNullOrWhiteSpace($here) -and $MyInvocation.MyCommand.Path) {
        $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    }
    $SourceScript = Join-Path $here 'Sync-TnwksLanBridge.ps1'
}

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$p  = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Register-TnwksLanBridgeTask.ps1 must be run as Administrator.'
}

if (-not (Test-Path $SourceScript)) { throw "Sync script not found at $SourceScript" }
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$script = Join-Path $InstallDir 'Sync-TnwksLanBridge.ps1'
Copy-Item $SourceScript $script -Force
$log = Join-Path $InstallDir 'sync.log'

# The sync script only writes to the console, so the task wraps it to keep a log.
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"& '$script' *>> '$log'; exit `$LASTEXITCODE`""

# Trigger 1: every user logon (catches reboot + WSL cold start).
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn

# Trigger 2: Hyper-V VmSwitch EventID 232 (VM NIC connected), which fires when
# WSL boots and when it rebuilds its vNIC after a host network change. It is
# written to the System log; the VmSwitch-Operational log never gets it.
$cim = New-CimInstance -CimClass (
    Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
) -ClientOnly -Property @{
    Enabled      = $true
    Subscription = @"
<QueryList>
  <Query Id="0" Path="System">
    <Select Path="System">
      *[System[Provider[@Name='Microsoft-Windows-Hyper-V-VmSwitch'] and (EventID=232)]]
    </Select>
  </Query>
</QueryList>
"@
}
# The NIC event fires before WSL answers `wsl.exe hostname -I`.
$cim.Delay = 'PT15S'

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

$principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType S4U -RunLevel Highest

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $action `
    -Trigger   @($logonTrigger, $cim) `
    -Settings  $settings `
    -Principal $principal | Out-Null

Write-Host "Scheduled Task '$TaskName' registered (runs as $RunAsUser, elevated)."
Write-Host "  triggers : at logon, on Hyper-V VmSwitch 232 (System log, +15s)"
Write-Host "  script   : $script"
Write-Host "  log      : $log"
Write-Host "Run it now with: Start-ScheduledTask -TaskName $TaskName"
