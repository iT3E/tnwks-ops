# Register-TnwksUsbAttachTask.ps1
#
# One-shot setup: registers a Scheduled Task that re-attaches the homelab USB
# dongles (Z-Stick, Coral) into the WSL2 VM via usbipd-win whenever the attach
# can be lost — at user logon (catches reboot + WSL cold start) and when the
# Hyper-V vmswitch logs the WSL VM coming up. Run elevated, once.
#
# Companion to Register-TnwksLanBridgeTask.ps1 — same trigger strategy, but a
# separate task because the concerns are unrelated (USB passthrough vs LAN
# portproxy) and each runs its own sync script.

[CmdletBinding()]
param(
    [string]$ScriptPath = (Join-Path $PSScriptRoot 'Sync-TnwksUsbAttach.ps1'),
    [string]$TaskName   = 'tnwks-usb-attach'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $ScriptPath)) {
    throw "Sync script not found at $ScriptPath"
}

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$p  = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Register-TnwksUsbAttachTask.ps1 must be run as Administrator.'
}

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

# Trigger 1: every user logon (catches reboot + WSL cold start).
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn

# Trigger 2: Hyper-V vmswitch logs event 232 when a new VM NIC connects —
# close enough to "WSL just (re)started" to re-attach after a mid-session
# `wsl --shutdown`. usbipd needs the WSL VM up to attach into it, so reacting
# to the vmswitch event lands the attach right after the VM is reachable.
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

# Small delay on the vmswitch trigger: the VM NIC event fires before WSL is
# fully reachable for `usbipd attach`. 15s is comfortably past that.
$cim.Delay = 'PT15S'

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

# Run as SYSTEM so the attach doesn't need an interactive admin session.
# usbipd attach --wsl targets the default WSL distro regardless of the
# invoking user's session.
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
