<#
.SYNOPSIS
Starts one task of the rehearsal of the move as SYSTEM, waits for its end, and
gives its last result, its result file when the run wrote one, and whether it
ran at all. It never throws on the result of the task: the play decides.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Task,
    [string]$Result,
    [int]$Minutes = 20
)
$ErrorActionPreference = 'Stop'
$start = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$before = (Get-ScheduledTaskInfo -TaskName $Task).LastRunTime
# A task that can not log on may be refused here, or may end with an error code.
$refused = ''
try { Start-ScheduledTask -TaskName $Task } catch { $refused = $_.Exception.Message }
$deadline = (Get-Date).AddMinutes($Minutes)
do {
    Start-Sleep -Seconds 3
    $info = Get-ScheduledTaskInfo -TaskName $Task
} while (-not $refused -and ((Get-ScheduledTask -TaskName $Task).State -eq 'Running' -or $info.LastRunTime -eq $before) -and (Get-Date) -lt $deadline)
$read = $null
if ($Result -and (Test-Path -LiteralPath $Result)) {
    $doc = Get-Content -LiteralPath $Result -Raw | ConvertFrom-Json
    if ([int64]$doc.time -ge $start) { $read = $doc }
}
[pscustomobject]@{
    task = $Task
    ran = ($info.LastRunTime -ne $before)
    code = [int64]$info.LastTaskResult
    hex = '0x{0:X8}' -f $info.LastTaskResult
    refused = $refused
    result = $read
}
