# Register-TnwksLanBridgeTask.ps1
#
# One-shot setup: registers a Scheduled Task that re-syncs portproxy rules
# whenever the WSL VM IP can change (at user logon, and when the Hyper-V
# vmswitch logs the VM coming up). Run elevated, once.

[CmdletBinding()]
param(
    [string]$ScriptPath = (Join-Path $PSScriptRoot 'Sync-TnwksLanBridge.ps1'),
    [string]$TaskName   = 'tnwks-lan-bridge-sync'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $ScriptPath)) {
    throw "Sync script not found at $ScriptPath"
}

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$p  = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Register-TnwksLanBridgeTask.ps1 must be run as Administrator.'
}

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

# Trigger 1: every user logon (catches reboot + WSL cold start).
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn

# Trigger 2: Hyper-V vmswitch logs an event when the WSL VM port comes up.
# Event ID 232 in Microsoft-Windows-Hyper-V-VmSwitch/Operational fires when
# a new VM NIC connects — close enough to "WSL just (re)started" to catch
# IP changes that happen mid-session (e.g. wsl --shutdown then a fresh boot).
$cim = New-CimInstance -CimClass (
    Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
) -ClientOnly -Property @{
    Enabled      = $true
    Subscription = @"
<QueryList>
  <Query Id="0" Path="Microsoft-Windows-Hyper-V-VmSwitch-Operational">
    <Select Path="Microsoft-Windows-Hyper-V-VmSwitch-Operational">
      *[System[Provider[@Name='Microsoft-Windows-Hyper-V-VmSwitch'] and (EventID=232)]]
    </Select>
  </Query>
</QueryList>
"@
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

# Run as SYSTEM so portproxy + firewall edits don't need an interactive
# admin session.
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $action `
    -Trigger   @($logonTrigger, $cim) `
    -Settings  $settings `
    -Principal $principal | Out-Null

Write-Host "Scheduled Task '$TaskName' registered."
Write-Host "Run it now with: Start-ScheduledTask -TaskName $TaskName"
