# Register-TnwksUdpRelayTask.ps1
#
# One-shot setup: installs Start-TnwksUdpRelay.ps1 to C:\ProgramData\tnwks-udp-relay,
# opens inbound udp/2055 from the LAN, and registers a Scheduled Task that keeps
# the relay running (at startup and logon, restarted if it ever exits).
# Run elevated, once. Re-running is safe: it replaces the task and script.

[CmdletBinding()]
param(
    [string]$SourceScript = (Join-Path $PSScriptRoot 'Start-TnwksUdpRelay.ps1'),
    [string]$InstallDir   = 'C:\ProgramData\tnwks-udp-relay',
    [string]$TaskName     = 'tnwks-udp-relay',
    [int]$Port            = 2055,
    [string]$RemoteAddress = '10.0.0.0/8,172.16.0.0/12,192.168.0.0/16',
    # WSL distros are per-user, so the relay runs as the desktop user (S4U:
    # no stored password, works without an interactive logon).
    [string]$RunAsUser    = "$env:USERDOMAIN\$env:USERNAME"
)

$ErrorActionPreference = 'Stop'

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$p  = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Register-TnwksUdpRelayTask.ps1 must be run as Administrator.'
}
if (-not (Test-Path $SourceScript)) { throw "Relay script not found at $SourceScript" }

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$script = Join-Path $InstallDir 'Start-TnwksUdpRelay.ps1'
Copy-Item $SourceScript $script -Force

$fwName = "tnwks-udp-relay-$Port"
Get-NetFirewallRule -DisplayName $fwName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $fwName -Direction Inbound -Action Allow `
    -Protocol UDP -LocalPort $Port -RemoteAddress ($RemoteAddress -split ',') `
    -Profile Any | Out-Null

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Port $Port"
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn)
)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType S4U -RunLevel Highest

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Scheduled Task '$TaskName' registered and started (runs as $RunAsUser)."
Write-Host "firewall: inbound udp/$Port allowed from $RemoteAddress"
Write-Host "Log: $(Join-Path $InstallDir 'udp-relay.log')"
