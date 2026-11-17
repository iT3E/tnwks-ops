<#
.SYNOPSIS
The window of the move of a distro to another account, and its way back
(Design Doc 0004, section 9, step 5). It runs as SYSTEM, as a task that the
play registers. The rehearsal of the move (role windows_wsl_move_rehearsal)
runs this script with a config of its own.

.DESCRIPTION
Modes:
  forward  The window, steps 1 to 9. On a failed check it goes back by itself,
           from the state that it reads.
  wayback  The way back mode: it reads the version first, writes the marker of
           a started window, then the way back.
  boot     The boot task: the way back once, only when the last marker in the
           state file is that of a started window. It writes the marker of its
           run before its first action, and never restarts after a failure.

Each line of the log and each marker is flushed to disk before the next step
starts. The restart at the end is behind the switch "restart" of the config:
with it off, the script logs "would restart" and does not restart.

The script acts as an account only through the tasks of that account
(Invoke-TnwksWslAccountTask.ps1), because WSL refuses SYSTEM. It reads no
registration of an account: each task reads its own.

Exit 0: done, nothing to do, or a planned stop. Exit 3: a stop that changed
nothing. Exit 4: the window failed a check and went back. Exit 1: a failed
check that the way back did not end.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Config,
    [ValidateSet('forward', 'wayback', 'boot')][string]$Mode,
    # The run request that the play writes: mode, group_steps, stop_after,
    # rehearsal_group_test. The boot task has none: it takes the boot part of
    # the config.
    [string]$Request
)
$ErrorActionPreference = 'Stop'
$cfg = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
if ($Request) {
    $req = Get-Content -LiteralPath $Request -Raw | ConvertFrom-Json
    if (-not $Mode) { $Mode = $req.mode }
} else {
    $req = [pscustomobject]@{ mode = $Mode; group_steps = [bool]$cfg.boot.group_steps; stop_after = 0; rehearsal_group_test = $false }
}
if (-not $Mode) { throw 'No mode: give -Mode or a request with a mode.' }
$AdministratorsSid = 'S-1-5-32-544'

# One line, flushed to disk before the call returns (FileStream.Flush(true)).
function Write-TnwksLine([string]$Path, [string]$Text) {
    $bytes = (New-Object Text.UTF8Encoding $false).GetBytes($Text + "`r`n")
    $stream = New-Object IO.FileStream($Path, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } finally { $stream.Dispose() }
}

function Write-Log([string]$Text) {
    Write-TnwksLine $cfg.log_file ('{0} {1} {2}' -f [DateTime]::UtcNow.ToString('o'), $Mode, $Text)
}

# The markers that this run wrote: only a run that wrote the marker of a
# started window (or of a boot run) goes back or restarts after a failure.
$script:WroteMarkers = @()

function Add-Marker([string]$Name) {
    Write-TnwksLine $cfg.state_file ('{0} {1}' -f [DateTime]::UtcNow.ToString('o'), $Name)
    $script:WroteMarkers += $Name
    Write-Log "marker $Name"
}

# A whole file, flushed to disk, then renamed over the old one.
function Write-TnwksFile([string]$Path, [string]$Text) {
    $temp = "$Path.new"
    $bytes = (New-Object Text.UTF8Encoding $false).GetBytes($Text)
    $stream = New-Object IO.FileStream($temp, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } finally { $stream.Dispose() }
    Move-Item -LiteralPath $temp -Destination $Path -Force
}

# The tasks that step 2 disabled (they were enabled before the window): way
# back step 6 enables these only. Step 2 writes the list before the marker of
# a started window, and the end of a way back removes it.
$DisabledFile = "$($cfg.state_file).disabled"

function Get-LastMarker {
    if (-not (Test-Path -LiteralPath $cfg.state_file)) { return '' }
    $lines = @(Get-Content -LiteralPath $cfg.state_file | Where-Object { $_.Trim() })
    if ($lines.Count -eq 0) { return '' }
    ($lines[-1] -split ' ', 2)[1].Trim()
}

function Assert-Tnwks([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw [InvalidOperationException]::new("check failed: $Message") }
}

# The installed version of WSL: the file version of its service binary.
function Get-TnwksWslVersion {
    if (-not (Test-Path -LiteralPath $cfg.service_binary)) { return 'none' }
    [Diagnostics.FileVersionInfo]::GetVersionInfo($cfg.service_binary).FileVersion
}

function Get-TnwksSid([string]$Account) { (Get-LocalUser -Name $Account).SID.Value }

# The members of Administrators, as SIDs. Get-LocalGroupMember fails on a
# member that names no account; the WinNT provider then reads the same list by
# objectSid, as the group reads of the role do.
function Get-TnwksAdminSids {
    try { @(Get-LocalGroupMember -SID $AdministratorsSid -ErrorAction Stop | ForEach-Object { $_.SID.Value }) }
    catch {
        $name = (Get-LocalGroup -SID $AdministratorsSid).Name
        @(([ADSI]"WinNT://./$name,group").Invoke('Members') | ForEach-Object {
            $raw = [byte[]]$_.GetType().InvokeMember('objectSid', 'GetProperty', $null, $_, $null)
            (New-Object Security.Principal.SecurityIdentifier $raw, 0).Value })
    }
}

function Test-TnwksAdmin([string]$Sid) { @(Get-TnwksAdminSids) -contains $Sid }

# The WSL processes of one account, by the SID of their owner.
function Get-TnwksWslProcess([string]$Sid) {
    @(Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('wsl.exe', 'wslhost.exe', 'wslrelay.exe') } | Where-Object {
        $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid -ErrorAction SilentlyContinue
        $owner -and $owner.Sid -eq $Sid })
}

function Wait-TnwksNoWsl([string]$Sid, [string]$Account) {
    $deadline = (Get-Date).AddMinutes(2)
    do {
        $left = Get-TnwksWslProcess $Sid
        if ($left.Count -eq 0) { return }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    Assert-Tnwks $false "$($left.Count) WSL process(es) of $Account still run"
}

# Runs a task of an account, waits for its end, and reads its result file.
function Invoke-TnwksAccountTask([string]$Task, [string]$ResultFile) {
    $start = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $before = (Get-ScheduledTaskInfo -TaskName $Task).LastRunTime
    Start-ScheduledTask -TaskName $Task
    $deadline = (Get-Date).AddMinutes([int]$cfg.task_minutes)
    do {
        Start-Sleep -Seconds 2
        $info = Get-ScheduledTaskInfo -TaskName $Task
    } while (((Get-ScheduledTask -TaskName $Task).State -eq 'Running' -or $info.LastRunTime -eq $before) -and (Get-Date) -lt $deadline)
    $result = $null
    if (Test-Path -LiteralPath $ResultFile) {
        $read = Get-Content -LiteralPath $ResultFile -Raw | ConvertFrom-Json
        if ([int64]$read.time -ge $start) { $result = $read }
    }
    $code = [int64]$info.LastTaskResult
    Write-Log ('task {0} ended with {1}; result: {2}' -f $Task, $code, $(if ($result) { $result | ConvertTo-Json -Compress -Depth 4 } else { 'none' }))
    [pscustomobject]@{ code = $code; result = $result }
}

# Runs the stop task of an account. It passes only with a result of this run
# with a shutdown that exited 0: LastTaskResult alone can be the 0 of an
# earlier stop, when this start did not run.
function Invoke-TnwksStopTask([pscustomobject]$Who) {
    $stop = Invoke-TnwksAccountTask $Who.window_tasks.stop (Join-Path $Who.result_dir 'stop.json')
    Assert-Tnwks ($stop.code -eq 0 -and $stop.result -and $stop.result.shutdown_exit -eq 0) `
        "the stop task of $($Who.account) ended with $($stop.code), with $(if ($stop.result) { "shutdown exit $($stop.result.shutdown_exit)" } else { 'no result of this run' })"
}

# Whether a task is enabled: its flag, Settings.Enabled. Not its State: a task
# with a running instance reads Running, also when it is disabled (TASK_STATE
# reads Disabled only with no instance), and the start task of the present
# account runs when step 2 disables it.
function Test-TnwksTaskEnabled([string]$Task) { [bool](Get-ScheduledTask -TaskName $Task).Settings.Enabled }

function Disable-TnwksTask([string]$Task) {
    if (Test-TnwksTaskEnabled $Task) { Disable-ScheduledTask -TaskName $Task | Out-Null }
    Assert-Tnwks (-not (Test-TnwksTaskEnabled $Task)) "$Task does not read disabled"
    Write-Log "task $Task disabled"
}

function Enable-TnwksTask([string]$Task) {
    if (-not (Test-TnwksTaskEnabled $Task)) { Enable-ScheduledTask -TaskName $Task | Out-Null }
    Assert-Tnwks (Test-TnwksTaskEnabled $Task) "$Task does not read enabled"
    Write-Log "task $Task enabled"
}

# Starts the start tasks of an account and waits until each runs with a WSL
# process of that account.
function Start-TnwksStartTasks([string[]]$Tasks, [string]$Sid, [string]$Account) {
    foreach ($task in $Tasks) { Start-ScheduledTask -TaskName $task }
    $deadline = (Get-Date).AddMinutes(3)
    do {
        Start-Sleep -Seconds 3
        $running = @($Tasks | Where-Object { (Get-ScheduledTask -TaskName $_).State -eq 'Running' })
        $wsl = Get-TnwksWslProcess $Sid
    } while (($running.Count -lt $Tasks.Count -or $wsl.Count -eq 0) -and (Get-Date) -lt $deadline)
    Assert-Tnwks ($running.Count -eq $Tasks.Count -and $wsl.Count -gt 0) "the start tasks of $Account do not run"
    Write-Log "the start tasks of $Account run, with $($wsl.Count) WSL process(es)"
}

function Set-TnwksGroup([string]$Sid, [string]$Account, [bool]$In) {
    $now = Test-TnwksAdmin $Sid
    if ($In -and -not $now) { Add-LocalGroupMember -SID $AdministratorsSid -Member $Sid }
    if (-not $In -and $now) { Remove-LocalGroupMember -SID $AdministratorsSid -Member $Sid }
    Assert-Tnwks ((Test-TnwksAdmin $Sid) -eq $In) "the group of $Account does not read $(if ($In) { 'in' } else { 'out' })"
    if ($In -ne $now) { Write-Log "group: $Account $(if ($In) { 'joined' } else { 'left' }) Administrators" }
}

# --- Rights of a file, by SDDL. Get-Acl reads them and Set-Acl writes the
# owner and the access rules only.
function Get-TnwksSddl([string]$Path) { (Get-Acl -LiteralPath $Path).Sddl }

function Set-TnwksSddl([string]$Path, [string]$Sddl) {
    $acl = Get-Acl -LiteralPath $Path
    $acl.SetSecurityDescriptorSddlForm($Sddl, 'Owner, Access')
    Set-Acl -LiteralPath $Path -AclObject $acl
}

# The parts that a check compares: the owner, whether the rules are protected
# from inheritance, and the explicit rules (an inherited rule comes from the
# folder).
# An account in SDDL: Windows writes some SIDs as an alias (BA, SY, CO, and LA
# or LG for the local Administrator and Guest). Both forms read as the SID.
function ConvertTo-TnwksSddlSid([string]$Account) {
    if ($Account -match '^S-1-') { return $Account }
    try { (New-Object Security.Principal.SecurityIdentifier $Account).Value } catch { $Account }
}

function Get-TnwksSddlParts([string]$Sddl) {
    $owner = if ($Sddl -match 'O:([^:]+?)(?=[GDS]:|$)') { ConvertTo-TnwksSddlSid $Matches[1] } else { '' }
    $dacl = if ($Sddl -match 'D:([A-Z]*)((\([^)]*\))*)') { $Matches } else { @{ 1 = ''; 2 = '' } }
    $aces = @([regex]::Matches($dacl[2], '\(([^)]*)\)') | ForEach-Object { $_.Groups[1].Value } |
        Where-Object { ($_ -split ';')[1] -notmatch 'ID' } | ForEach-Object {
            $f = $_ -split ';'
            $f[-1] = ConvertTo-TnwksSddlSid $f[-1]
            $f -join ';' } | Sort-Object)
    [pscustomobject]@{ owner = $owner; protected = ($dacl[1] -match 'P'); aces = ($aces -join ' ') }
}

function Test-TnwksSddl([string]$Path, [string]$Expected) {
    $want = Get-TnwksSddlParts $Expected
    $have = Get-TnwksSddlParts (Get-TnwksSddl $Path)
    $want.owner -eq $have.owner -and $want.protected -eq $have.protected -and $want.aces -eq $have.aces
}

# A restore of a saved owner that is not SYSTEM or a group of its token needs
# SeRestorePrivilege enabled in the token.
function Enable-TnwksRestorePrivilege {
    try {
        if (-not ('TnwksPrivilege' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class TnwksPrivilege {
    [StructLayout(LayoutKind.Sequential, Pack = 1)]
    struct TokPriv1Luid { public int Count; public long Luid; public int Attr; }
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool AdjustTokenPrivileges(IntPtr h, bool disable, ref TokPriv1Luid n, int len, IntPtr prev, IntPtr rel);
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool OpenProcessToken(IntPtr p, int access, out IntPtr h);
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool LookupPrivilegeValue(string host, string name, out long luid);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    public static bool Enable(string name) {
        IntPtr h; long luid;
        if (!OpenProcessToken(GetCurrentProcess(), 0x28, out h)) return false;
        if (!LookupPrivilegeValue(null, name, out luid)) return false;
        var tp = new TokPriv1Luid { Count = 1, Luid = luid, Attr = 2 };
        return AdjustTokenPrivileges(h, false, ref tp, 0, IntPtr.Zero, IntPtr.Zero) && Marshal.GetLastWin32Error() == 0;
    }
}
'@
        }
        $ok = [TnwksPrivilege]::Enable('SeRestorePrivilege')
        Write-Log "SeRestorePrivilege enabled: $ok"
    } catch { Write-Log "SeRestorePrivilege: $($_.Exception.Message)" }
}

function Get-TnwksDiskSddl([string]$Sid) { 'O:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1301bf;;;{0})' -f $Sid }
function Get-TnwksMarkerSddl([string]$Sid) { 'O:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;{0})' -f $Sid }

# The marker file of the procedure keeps a folder through an unregister,
# which removes only an empty folder. Written only when it is absent.
function Add-TnwksFolderMarker([string]$Folder, [string]$Sid) {
    $marker = Join-Path $Folder $cfg.marker_name
    if (-not (Test-Path -LiteralPath $marker)) {
        [IO.File]::WriteAllText($marker, "tnwks-ops: the marker of the move window. No step removes it.`r`n")
        Set-TnwksSddl $marker (Get-TnwksMarkerSddl $Sid)
        Write-Log "marker file written in $Folder"
    }
    Assert-Tnwks (Test-Path -LiteralPath $marker) "no marker file in $Folder"
}

function Test-TnwksScope {
    $sep = [IO.Path]::DirectorySeparatorChar
    $root = [IO.Path]::GetFullPath($cfg.root).TrimEnd($sep)
    foreach ($path in @($cfg.present.folder, $cfg.new.folder, $cfg.state_file, $cfg.log_file, $cfg.acl_file, $cfg.version_file)) {
        $full = [IO.Path]::GetFullPath($path)
        if (-not $full.StartsWith($root + $sep, [StringComparison]::OrdinalIgnoreCase)) { return "the path $path is outside $root" }
    }
    foreach ($account in @($cfg.present.account, $cfg.new.account)) {
        if ($account -notmatch $cfg.account_pattern) { return "the account $account is outside the scope ($($cfg.account_pattern))" }
    }
    # Each task that the script starts, stops, disables or enables.
    if (-not $cfg.task_pattern) { return 'the config has no task_pattern' }
    $tasks = @($cfg.present.window_tasks.PSObject.Properties | ForEach-Object { $_.Value }) +
        @($cfg.new.window_tasks.PSObject.Properties | ForEach-Object { $_.Value }) +
        @($cfg.present.disable_tasks) + @($cfg.present.start_tasks) + @($cfg.new.start_tasks)
    foreach ($task in $tasks) {
        if ("$task" -notmatch $cfg.task_pattern) { return "the task $task is outside the scope ($($cfg.task_pattern))" }
    }
    # The check for a session of the owner (site 1) is not in this script.
    if ($cfg.session_check) { return 'the session check is on, and this script does not have it' }
    ''
}

# Whether a session of the owner is on, so that the way back must not restart
# (site 1). The script has no such check: Test-TnwksScope refuses a config
# with session_check on, so this reads no session.
function Test-TnwksOwnerSession { $false }

function Request-TnwksRestart([string]$Why) {
    if ($cfg.restart) {
        Write-Log "restart: $Why"
        Restart-Computer -Force
    } else {
        Write-Log "would restart ($Why); the switch restart is off"
    }
}

$present = $cfg.present
$new = $cfg.new
$oldPath = Join-Path $present.folder $cfg.disk_name
$newPath = Join-Path $new.folder $cfg.disk_name

function Read-TnwksState {
    $pSid = Get-TnwksSid $present.account
    $nSid = Get-TnwksSid $new.account
    $tasks = @{}
    foreach ($t in @($present.disable_tasks) + @($new.start_tasks)) {
        $task = Get-ScheduledTask -TaskName $t
        $tasks[$t] = '{0}, enabled {1}' -f $task.State, [bool]$task.Settings.Enabled
    }
    $state = [pscustomobject]@{
        old = [bool](Test-Path -LiteralPath $oldPath)
        new = [bool](Test-Path -LiteralPath $newPath)
        present_admin = (Test-TnwksAdmin $pSid)
        present_wsl = (Get-TnwksWslProcess $pSid).Count
        new_wsl = (Get-TnwksWslProcess $nSid).Count
        tasks = $tasks
        version = Get-TnwksWslVersion
        last_marker = Get-LastMarker
    }
    Write-Log "read: $($state | ConvertTo-Json -Compress -Depth 3)"
    $state
}

function Invoke-TnwksForward {
    $pSid = Get-TnwksSid $present.account
    $nSid = Get-TnwksSid $new.account
    # A window that started and did not end goes back first (mode wayback, or
    # the boot task), never forward again.
    $last = Get-LastMarker
    if ($last -in @('window-started', 'boot-run')) {
        Write-Log "stop: the last marker is '$last': a window started and did not end. Nothing is changed."
        return 'stopped'
    }
    if ($req.rehearsal_group_test) {
        # The rehearsal only: the present account is in Administrators, as
        # the real present account is. Window step 7 takes it out.
        Set-TnwksGroup $pSid $present.account $true
    }
    # (1) The version, before any change.
    $version = Get-TnwksWslVersion
    $savedVersion = ("$(Get-Content -LiteralPath $cfg.version_file -Raw)").Trim()
    if ($version -ne $savedVersion) {
        Write-Log "stop at step 1: WSL $version is not the saved $savedVersion. Nothing is changed."
        return 'stopped'
    }
    Write-Log "step 1: WSL $version, as saved. The session check is $(if ($cfg.session_check) { 'on' } else { 'off' })."
    # (2) The marker of a started window, then the tasks of the present account.
    # The tasks that this step disables, saved first, for way back step 6.
    $enabled = @($present.disable_tasks | Where-Object { Test-TnwksTaskEnabled $_ })
    Write-TnwksFile $DisabledFile ($enabled -join "`r`n")
    Add-Marker 'window-started'
    foreach ($t in @($present.disable_tasks)) { Disable-TnwksTask $t }
    Write-Log 'step 2 passed'
    # (3) End their instances (a disabled task can still have one running), and
    # stop the VM of the present account.
    foreach ($t in @($present.disable_tasks)) { Stop-ScheduledTask -TaskName $t }
    Invoke-TnwksStopTask $present
    Wait-TnwksNoWsl $pSid $present.account
    Write-Log 'step 3 passed'
    # (4) The markers, then the move, the owner and the rules.
    Add-TnwksFolderMarker $present.folder $pSid
    Assert-Tnwks (Test-Path -LiteralPath (Join-Path $new.folder $cfg.marker_name)) "no marker file in $($new.folder)"
    Assert-Tnwks ((Test-Path -LiteralPath $oldPath) -and -not (Test-Path -LiteralPath $newPath)) 'the disk file is not at the old path only'
    $rules = Get-TnwksSddl $oldPath
    Write-TnwksFile $cfg.acl_file $rules
    $size = (Get-Item -LiteralPath $oldPath).Length
    Write-Log "step 4: saved the rules of the disk file; size $size"
    Move-Item -LiteralPath $oldPath -Destination $newPath
    Set-TnwksSddl $newPath (Get-TnwksDiskSddl $nSid)
    Assert-Tnwks (-not (Test-Path -LiteralPath $oldPath)) 'the old path is not empty'
    Assert-Tnwks ((Get-Item -LiteralPath $newPath).Length -eq $size) 'the moved file has another size'
    Assert-Tnwks (Test-TnwksSddl $newPath (Get-TnwksDiskSddl $nSid)) 'the moved file has another owner or other rules'
    Write-Log 'step 4 passed'
    # (5) The unregister of the present account, with no file at its path.
    Assert-Tnwks (-not (Test-Path -LiteralPath $oldPath)) 'a file is at the path of the registration of the present account'
    $size = (Get-Item -LiteralPath $newPath).Length
    $unreg = Invoke-TnwksAccountTask $present.window_tasks.unregister (Join-Path $present.result_dir 'unregister.json')
    Assert-Tnwks ($unreg.code -eq 0 -and $unreg.result -and -not $unreg.result.registered) "the unregister of $($present.account) did not pass"
    Assert-Tnwks ((Test-Path -LiteralPath $newPath) -and (Get-Item -LiteralPath $newPath).Length -eq $size) 'the disk file changed in the unregister'
    Assert-Tnwks (Test-Path -LiteralPath (Join-Path $present.folder $cfg.marker_name)) 'the old folder or its marker is gone'
    Write-Log 'step 5 passed'
    if ([int]$req.stop_after -eq 5) {
        Write-Log 'stop after step 5 (the rehearsal): the marker of a started window stays'
        return 'stopped-after'
    }
    # (6) The import of the new account, then its stop.
    $import = Invoke-TnwksAccountTask $new.window_tasks.import (Join-Path $new.result_dir 'import.json')
    Assert-Tnwks ($import.code -eq 0 -and $import.result -and [string]::Equals("$($import.result.disk)", $newPath, [StringComparison]::OrdinalIgnoreCase)) "the import of $($new.account) did not pass"
    Invoke-TnwksStopTask $new
    Wait-TnwksNoWsl $nSid $new.account
    Write-Log 'step 6 passed'
    # (7) The present account leaves Administrators.
    if ($req.group_steps) { Set-TnwksGroup $pSid $present.account $false }
    Write-Log 'step 7 passed'
    # (8) The start tasks of the new account: an enable asks for no password.
    foreach ($t in @($new.start_tasks)) { Enable-TnwksTask $t }
    Write-Log 'step 8 passed'
    # (9) The marker of a done window, then the restart.
    Add-Marker 'window-done'
    Request-TnwksRestart 'the end of the window'
    'done'
}

# The way back: each step acts only where its forward step left a change.
function Invoke-TnwksWayBack {
    $pSid = Get-TnwksSid $present.account
    $nSid = Get-TnwksSid $new.account
    $state = Read-TnwksState
    if ($state.old -eq $state.new) {
        Write-Log "stop: the disk file is at $(if ($state.old) { 'both paths' } else { 'neither path' }). Nothing is changed."
        return 'stopped'
    }
    # Step 2 wrote the list of the tasks that it disabled before the marker
    # of a started window: with no list, nothing moves.
    Assert-Tnwks (Test-Path -LiteralPath $DisabledFile) 'no list of the tasks that step 2 disabled: nothing moves back'
    # (1) For step 7.
    if ($req.group_steps) { Set-TnwksGroup $pSid $present.account $true }
    Write-Log 'way back 1 passed'
    # (2) For steps 8 and 6.
    foreach ($t in @($new.start_tasks)) {
        Disable-TnwksTask $t
        Stop-ScheduledTask -TaskName $t
    }
    Invoke-TnwksStopTask $new
    Wait-TnwksNoWsl $nSid $new.account
    Write-Log 'way back 2 passed'
    # (3) For step 4.
    if (Test-Path -LiteralPath $newPath) {
        Assert-Tnwks ((Get-TnwksWslProcess $pSid).Count -eq 0 -and (Get-TnwksWslProcess $nSid).Count -eq 0) 'a WSL process runs before the move back'
        Assert-Tnwks (Test-Path -LiteralPath (Join-Path $present.folder $cfg.marker_name)) 'the old folder or its marker is gone: no move back'
        # Window step 4 saved the rules before the move: with no saved rules,
        # no move back.
        Assert-Tnwks (Test-Path -LiteralPath $cfg.acl_file) 'no saved rules of the disk file: no move back'
        $saved = ("$(Get-Content -LiteralPath $cfg.acl_file -Raw)").Trim()
        Assert-Tnwks ($saved -like 'O:*') 'the saved rules of the disk file are empty: no move back'
        $size = (Get-Item -LiteralPath $newPath).Length
        Move-Item -LiteralPath $newPath -Destination $oldPath
        Enable-TnwksRestorePrivilege
        Set-TnwksSddl $oldPath $saved
        Assert-Tnwks (Test-TnwksSddl $oldPath $saved) 'the file has not its saved owner and rules'
        Assert-Tnwks (-not (Test-Path -LiteralPath $newPath)) 'the new path is not empty'
        Assert-Tnwks ((Get-Item -LiteralPath $oldPath).Length -eq $size) 'the file moved back with another size'
    }
    Write-Log 'way back 3 passed'
    # (4) For step 6: the unregister of the new account. No distro is a pass.
    Assert-Tnwks (-not (Test-Path -LiteralPath $newPath)) 'a file is at the path of the registration of the new account'
    $size = (Get-Item -LiteralPath $oldPath).Length
    $unreg = Invoke-TnwksAccountTask $new.window_tasks.unregister (Join-Path $new.result_dir 'unregister.json')
    Assert-Tnwks ($unreg.code -eq 0 -and $unreg.result -and -not $unreg.result.registered) "the unregister of $($new.account) did not pass"
    Assert-Tnwks ((Get-Item -LiteralPath $oldPath).Length -eq $size) 'the disk file changed in the unregister'
    Assert-Tnwks (Test-Path -LiteralPath (Join-Path $new.folder $cfg.marker_name)) 'the new folder or its marker is gone'
    Write-Log 'way back 4 passed'
    # (5) For step 5: the import back of the present account.
    $import = Invoke-TnwksAccountTask $present.window_tasks.import (Join-Path $present.result_dir 'import.json')
    Assert-Tnwks ($import.code -eq 0 -and $import.result -and [string]::Equals("$($import.result.disk)", $oldPath, [StringComparison]::OrdinalIgnoreCase)) "the import back of $($present.account) did not pass"
    Write-Log 'way back 5 passed'
    # (6) For step 2: only the tasks that step 2 disabled.
    $enable = @(Get-Content -LiteralPath $DisabledFile | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    foreach ($t in @($present.disable_tasks)) {
        if ($enable -contains $t) { Enable-TnwksTask $t } else { Write-Log "task $t stays as it is: step 2 did not disable it" }
    }
    Write-Log 'way back 6 passed'
    Add-Marker 'wayback-done'
    # The list is spent: a later way back needs a new forward run.
    Remove-Item -LiteralPath $DisabledFile -Force
    # The end of the way back (the design): a restart. With no restart allowed
    # (a session of the owner on site 1, or the switch restart off), no
    # restart, and the start tasks of the present account start.
    $session = Test-TnwksOwnerSession
    if ($cfg.restart -and -not $session) {
        Request-TnwksRestart 'the end of the way back'
        return 'done'
    }
    if ($session) { Write-Log 'no restart: a session of the owner is on; the start tasks start' }
    else { Write-Log 'would restart (the end of the way back); the switch restart is off' }
    if ($req.rehearsal_group_test) {
        # The rehearsal only: A leaves Administrators before any task of A
        # starts, so no process of A holds a token from its time in the group.
        # The import back started A's VM with that token: A's stop task ends
        # it first, and no WSL process of A is left. A has no batch right of
        # its own here: the play starts its start task after the run.
        Invoke-TnwksStopTask $present
        Wait-TnwksNoWsl $pSid $present.account
        Write-Log "no WSL process of $($present.account) before it leaves the group"
        Set-TnwksGroup $pSid $present.account $false
        Write-Log "the start tasks of $($present.account) are left to the play (the group test)"
    } else {
        Start-TnwksStartTasks @($present.start_tasks) $pSid $present.account
    }
    'done'
}

$scope = Test-TnwksScope
if ($scope) {
    Write-Log "stop: $scope. Nothing is changed."
    exit 3
}
Write-Log "start, request: $($req | ConvertTo-Json -Compress)"
$outcome = 'failed'
try {
    switch ($Mode) {
        'forward' {
            try {
                $outcome = Invoke-TnwksForward
            } catch {
                # A failed check, or any error, after the marker of a started
                # window: the way back, from the state that it reads.
                Write-Log "fail: $($_.Exception.Message)"
                if ($script:WroteMarkers -contains 'window-started' -and (Get-LastMarker) -eq 'window-started') {
                    Write-Log 'the way back starts'
                    $outcome = Invoke-TnwksWayBack
                    if ($outcome -eq 'done') { $outcome = 'went-back' }
                } else {
                    $outcome = 'failed'
                }
            }
        }
        'wayback' {
            $version = Get-TnwksWslVersion
            $saved = ("$(Get-Content -LiteralPath $cfg.version_file -Raw)").Trim()
            if ($version -ne $saved) {
                Write-Log "stop: WSL $version is not the saved $saved. Nothing is changed."
                $outcome = 'stopped'
            } else {
                Add-Marker 'window-started'
                $outcome = Invoke-TnwksWayBack
            }
        }
        'boot' {
            $last = Get-LastMarker
            if ($last -ne 'window-started') {
                Write-Log "no action: the last marker is '$last'"
                $outcome = 'no-action'
            } else {
                Add-Marker 'boot-run'
                $version = Get-TnwksWslVersion
                $saved = ("$(Get-Content -LiteralPath $cfg.version_file -Raw)").Trim()
                if ($version -ne $saved) {
                    Write-Log "stop: WSL $version is not the saved $saved. Nothing is changed."
                    $outcome = 'stopped'
                } else {
                    $outcome = Invoke-TnwksWayBack
                }
            }
        }
    }
} catch {
    Write-Log "fail: $($_.Exception.Message)"
    $outcome = 'failed'
} finally {
    if ($req.rehearsal_group_test) {
        # The rehearsal only: the present account leaves Administrators at the
        # end of its run, whatever the result.
        try { Set-TnwksGroup (Get-TnwksSid $present.account) $present.account $false } catch { Write-Log "fail: the group of $($present.account): $($_.Exception.Message)" }
    }
}
Write-Log "result: $outcome"
if ($outcome -eq 'failed' -and $Mode -ne 'boot') {
    # A failed way back: the restart ends a VM that did not stop, and the boot
    # task makes one more run. The boot task itself never restarts after a fail.
    if ($script:WroteMarkers.Count -gt 0 -and (Get-LastMarker) -in @('window-started', 'boot-run')) { Request-TnwksRestart 'a failed check' }
}
switch ($outcome) {
    { $_ -in @('done', 'no-action', 'stopped-after') } { exit 0 }
    'went-back' { exit 4 }
    'stopped' { exit 3 }
    default { exit 1 }
}
