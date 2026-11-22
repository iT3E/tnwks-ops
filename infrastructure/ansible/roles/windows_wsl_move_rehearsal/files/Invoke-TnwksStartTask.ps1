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
try { Start-ScheduledTask -TaskName $Task | Out-Null } catch { $refused = $_.Exception.Message }
$deadline = (Get-Date).AddMinutes($Minutes)
# The run has ended when the task is not Running or Queued, its last run time
# is new, and its result is not 0x41301 (the task is running): a task that
# never ran can read a new time before its run ends.
do {
    Start-Sleep -Seconds 3
    $info = Get-ScheduledTaskInfo -TaskName $Task
    $ended = "$((Get-ScheduledTask -TaskName $Task).State)" -notin @('Running', 'Queued') -and
        $info.LastRunTime -ne $before -and [int64]$info.LastTaskResult -ne 0x41301
} while (-not $refused -and -not $ended -and (Get-Date) -lt $deadline)
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
