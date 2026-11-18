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
           Its group steps and group test come from the boot part of the
           config. In the rehearsal, a boot request (boot_request_file) sets
           them for one boot run: the run removes it at its end, once the
           present account is out of Administrators.

Each line of the log and each marker is flushed to disk before the next step
starts. The restart at the end is behind the switch "restart" of the config:
with it off, the script logs "would restart" and does not restart.

Each run, in every mode, first turns on the privileges of SYSTEM
(SeBackupPrivilege, SeRestorePrivilege, SeTakeOwnershipPrivilege) and stops
if one is not on, before it reads the state. The move of the disk file and
each read and write of its rules work by these privileges, not by the rules
of the file or the right of SYSTEM on its folders: the account that owns
the file may remove SYSTEM and the Administrators from its rules.

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
    # the config, and the boot request of the rehearsal when there is one.
    [string]$Request
)
$ErrorActionPreference = 'Stop'
$cfg = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
if ($Request) {
    $req = Get-Content -LiteralPath $Request -Raw | ConvertFrom-Json
    if (-not $Mode) { $Mode = $req.mode }
} else {
    $req = [pscustomobject]@{ mode = $Mode; group_steps = [bool]$cfg.boot.group_steps; stop_after = 0
        rehearsal_group_test = [bool]$cfg.boot.rehearsal_group_test }
}
# The rehearsal only: the boot request sets the group steps and the group test
# of one boot run, each a boolean. A request that is not read stops the run
# with nothing changed (exit 3, after the scope check). The run removes the
# request at its end, only once the present account is out of Administrators,
# so a run that a power loss ends leaves it for the next boot run, which then
# takes the account out first.
$BootRequestRead = $false
$BootRequestError = ''
if (-not $Request -and $Mode -eq 'boot' -and $cfg.boot_request_file -and (Test-Path -LiteralPath $cfg.boot_request_file)) {
    try {
        $bootRequest = Get-Content -LiteralPath $cfg.boot_request_file -Raw | ConvertFrom-Json
        if ($bootRequest.group_steps -isnot [bool] -or $bootRequest.rehearsal_group_test -isnot [bool]) {
            throw 'group_steps and rehearsal_group_test are not two booleans'
        }
        # The join of the group steps and the leave of the group test come together.
        if ($bootRequest.group_steps -ne $bootRequest.rehearsal_group_test) {
            throw 'group_steps and rehearsal_group_test are not equal'
        }
        $req.group_steps = $bootRequest.group_steps
        $req.rehearsal_group_test = $bootRequest.rehearsal_group_test
        $BootRequestRead = $true
    } catch { $BootRequestError = "the boot request $($cfg.boot_request_file) is not read: $($_.Exception.Message)" }
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
    "$(($lines[-1] -split ' ', 2)[1])".Trim()
}

function Assert-Tnwks([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw [InvalidOperationException]::new("check failed: $Message") }
}

# A text in a fail line: one line, with each control character and each line
# or paragraph separator of Unicode (U+0085, U+2028, U+2029) a space, and at
# most $Limit characters. An output of wsl.exe or an error can hold line
# breaks and be long; the log keeps one line for each moment, and the checks
# of the log read each line by its start.
function Format-TnwksText([string]$Text, [int]$Limit = 300) {
    $one = ([regex]::Replace("$Text", '[\x00-\x1F\x7F\u0085\u2028\u2029]+', ' ')).Trim()
    if ($one.Length -gt $Limit) { $one = $one.Substring(0, $Limit) + ' [cut]' }
    $one
}

# A fail line: "fail: " and its text, bounded.
function Write-TnwksFail([string]$Text) { Write-Log "fail: $(Format-TnwksText $Text 1000)" }

# What a run of a task of an account gave, for the fail line of its step: the
# exit code of the task, and the error text of its command in the result of
# this run (failed, output), or that there is no result of this run.
function Format-TnwksResultText($Result) {
    $text = ''
    if ($Result.failed) { $text += ", failed: $(Format-TnwksText $Result.failed)" }
    if ($Result.output) { $text += ", output: $(Format-TnwksText $Result.output)" }
    $text
}
function Format-TnwksTaskRun([pscustomobject]$Run, [switch]$Stop) {
    if (-not $Run.ran) { return "did not run to its end in this run (last result $($Run.code))" }
    if ($Stop) {
        return "ended with $($Run.code), with $(if ($Run.result) { "shutdown exit $($Run.result.shutdown_exit)$(Format-TnwksResultText $Run.result)" } else { 'no result of this run' })"
    }
    if (-not $Run.result) { return "exit code $($Run.code), no result of this run" }
    "exit code $($Run.code)$(Format-TnwksResultText $Run.result)"
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
# What comes from Task Scheduler, read by SYSTEM: whether the task ran in this
# run (a new last run time, not Running or Queued, a last result that is not
# 0x41301) and its last result, the exit code of the process of the account.
# What comes from the account: the result file, in a folder that the account
# can write, with its time. So the procedure takes a result only from a run
# that ended in this run, and only with a time from the start of this run to
# the moment of the read; with no such run or result, a step does not pass.
# A step that reads a result of an account requires that result of this run
# and the exit code of its task. Each stop also requires, as SYSTEM, no WSL
# process of the account (by the SID of their owner); window step 5 and way
# back step 4 also require the size of the disk file and the marker of the
# folder as before.
function Invoke-TnwksAccountTask([string]$Task, [string]$ResultFile) {
    $start = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $before = (Get-ScheduledTaskInfo -TaskName $Task).LastRunTime
    Start-ScheduledTask -TaskName $Task | Out-Null
    $deadline = (Get-Date).AddMinutes([int]$cfg.task_minutes)
    # A run has ended when the task is not Running or Queued, its last run
    # time is new, and its result is not 0x41301 (the task is running): a
    # task that never ran can read a new time before its run ends.
    do {
        Start-Sleep -Seconds 2
        $info = Get-ScheduledTaskInfo -TaskName $Task
        $ended = "$((Get-ScheduledTask -TaskName $Task).State)" -notin @('Running', 'Queued') -and
            $info.LastRunTime -ne $before -and [int64]$info.LastTaskResult -ne 0x41301
    } while (-not $ended -and (Get-Date) -lt $deadline)
    $result = $null
    if ($ended -and (Test-Path -LiteralPath $ResultFile)) {
        $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        try {
            $read = Get-Content -LiteralPath $ResultFile -Raw | ConvertFrom-Json
            $time = [int64]$read.time
            if ($time -ge $start -and $time -le $now) { $result = $read }
        } catch { Write-Log "task ${Task}: its result file is not read: $(Format-TnwksText $_.Exception.Message)" }
    }
    $code = [int64]$info.LastTaskResult
    # The result as JSON on one line, bounded: an output of wsl.exe can be long.
    Write-Log ('task {0} {1} {2}; result: {3}' -f $Task, $(if ($ended) { 'ended with' } else { 'did not run to its end in this run; last result' }), $code,
        $(if ($result) { Format-TnwksText ($result | ConvertTo-Json -Compress -Depth 4) 2000 } else { 'none' }))
    [pscustomobject]@{ ran = $ended; code = $code; result = $result }
}

# Runs the stop task of an account. It passes only with a result of this run
# with a shutdown that exited 0: LastTaskResult alone can be the 0 of an
# earlier stop, when this start did not run.
function Invoke-TnwksStopTask([pscustomobject]$Who) {
    $stop = Invoke-TnwksAccountTask $Who.window_tasks.stop (Join-Path $Who.result_dir 'stop.json')
    Assert-Tnwks ($stop.ran -and $stop.code -eq 0 -and $stop.result -and $stop.result.shutdown_exit -eq 0) `
        "the stop task of $($Who.account) $(Format-TnwksTaskRun $stop -Stop)"
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

# The rehearsal only: the end of a run of the group test, whatever its result.
# The present account leaves Administrators. When the import back ran in this
# run, its VM may run with a token from the group: the stop task of the
# account ends it first, and the run waits for no WSL process of the account.
# The stop runs once in a run: when the stop of the way back before the leave
# already ran and failed, it does not run again (the time limit of the task
# holds one stop). A stop that fails is logged, and the leave comes all the
# same: no run ends with the account in the group.
$script:ImportBackRan = $false
$script:StopBeforeLeaveRan = $false
function Exit-TnwksGroupTest {
    $sid = Get-TnwksSid $present.account
    if (-not (Test-TnwksAdmin $sid)) { return }
    if ($script:ImportBackRan -and $script:StopBeforeLeaveRan) {
        Write-Log "the stop of $($present.account) before it leaves the group ran once; it leaves all the same"
    } elseif ($script:ImportBackRan) {
        try {
            Invoke-TnwksStopTask $present
            Wait-TnwksNoWsl $sid $present.account
            Write-Log "no WSL process of $($present.account) before it leaves the group"
        } catch { Write-TnwksFail "the stop of $($present.account) before it leaves the group: $($_.Exception.Message)" }
    }
    Set-TnwksGroup $sid $present.account $false
}

# --- Rights of a file, by SDDL. The account that owns its disk file may
# change its rules, also remove SYSTEM and the Administrators, so SYSTEM
# reads and writes them with its privileges, not with the rules of the file:
# each handle opens with backup semantics (CreateFile: with the flag, the
# privileges override the checks of the file), with SeBackupPrivilege,
# SeRestorePrivilege and SeTakeOwnershipPrivilege enabled. The read asks
# for READ_CONTROL only, and either privilege grants it: SeBackupPrivilege
# directly, SeRestorePrivilege through FILE_GENERIC_WRITE, which holds
# STANDARD_RIGHTS_WRITE, that is READ_CONTROL (Microsoft: Privilege
# Constants; File Security and Access Rights; Standard Access Rights). A write
# takes ownership for the Administrators first (the owner's group may then
# write the rules), then sets the owner of the SDDL and its rules in one
# write by name (SeRestorePrivilege allows any owner), with the explicit
# rules and the protection of the SDDL; the inherited rules of an
# SDDL with no protection come from the folder of the file, so Windows
# computes them in that write (a CREATOR OWNER rule names the owner of the
# SDDL), and a check compares them after. A move is
# a rename by these privileges (way back step 3 says why). What a folder
# gives a file comes from Windows' own inheritance function
# (CreatePrivateObjectSecurityEx), as at a write.
$TnwksSecuritySource = @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class TnwksSecurity {
    [StructLayout(LayoutKind.Sequential, Pack = 1)]
    struct TokPriv1Luid { public int Count; public long Luid; public int Attr; }
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool AdjustTokenPrivileges(IntPtr h, bool disable, ref TokPriv1Luid n, int len, IntPtr prev, IntPtr rel);
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool OpenProcessToken(IntPtr p, int access, out IntPtr h);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)] static extern bool LookupPrivilegeValue(string host, string name, out long luid);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr LocalFree(IntPtr p);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern SafeFileHandle CreateFile(string name, int access, int share, IntPtr sa, int disposition, int flags, IntPtr template);
    [DllImport("advapi32.dll")]
    static extern int GetSecurityInfo(SafeFileHandle h, int type, int info, out IntPtr owner, out IntPtr group, out IntPtr dacl, out IntPtr sacl, out IntPtr sd);
    [DllImport("advapi32.dll")]
    static extern int SetSecurityInfo(SafeFileHandle h, int type, int info, IntPtr owner, IntPtr group, IntPtr dacl, IntPtr sacl);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode)]
    static extern int SetNamedSecurityInfo(string name, int type, int info, IntPtr owner, IntPtr group, IntPtr dacl, IntPtr sacl);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool ConvertSecurityDescriptorToStringSecurityDescriptor(IntPtr sd, int revision, int info, out IntPtr text, out int length);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(string text, int revision, out IntPtr sd, out int size);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool ConvertStringSidToSid(string text, out IntPtr sid);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool GetSecurityDescriptorOwner(IntPtr sd, out IntPtr owner, out bool defaulted);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool GetSecurityDescriptorDacl(IntPtr sd, out bool present, out IntPtr dacl, out bool defaulted);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool GetSecurityDescriptorControl(IntPtr sd, out short control, out int revision);
    [StructLayout(LayoutKind.Sequential)]
    struct GenericMapping { public int Read; public int Write; public int Execute; public int All; }
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool CreatePrivateObjectSecurityEx(IntPtr parent, IntPtr creator, out IntPtr created, IntPtr objectType, bool isContainer, int flags, IntPtr token, ref GenericMapping mapping);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool DestroyPrivateObjectSecurity(ref IntPtr sd);
    const int OWNER = 0x1, GROUP = 0x2, DACL = 0x4, PROTECTED_DACL = unchecked((int)0x80000000), UNPROTECTED_DACL = 0x20000000;
    const int READ_CONTROL = 0x20000, WRITE_DAC = 0x40000, WRITE_OWNER = 0x80000;

    static bool EnableOne(string name) {
        IntPtr h; long luid;
        if (!OpenProcessToken(GetCurrentProcess(), 0x28, out h)) return false;
        try {
            if (!LookupPrivilegeValue(null, name, out luid)) return false;
            TokPriv1Luid tp = new TokPriv1Luid();
            tp.Count = 1; tp.Luid = luid; tp.Attr = 2;
            return AdjustTokenPrivileges(h, false, ref tp, 0, IntPtr.Zero, IntPtr.Zero) && Marshal.GetLastWin32Error() == 0;
        } finally { CloseHandle(h); }
    }
    public static string Enable() {
        string[] names = { "SeBackupPrivilege", "SeRestorePrivilege", "SeTakeOwnershipPrivilege" };
        string[] parts = new string[names.Length];
        for (int i = 0; i < names.Length; i++) parts[i] = names[i] + " " + EnableOne(names[i]);
        return String.Join(", ", parts);
    }
    static SafeFileHandle Open(string path, int access) {
        // OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS; every share mode.
        SafeFileHandle h = CreateFile(path, access, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero);
        if (h.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error(), "open " + path);
        return h;
    }
    public static string ReadSddl(string path) {
        using (SafeFileHandle h = Open(path, READ_CONTROL)) {
            IntPtr owner, group, dacl, sacl, sd, text; int length;
            int r = GetSecurityInfo(h, 1, OWNER | DACL, out owner, out group, out dacl, out sacl, out sd);
            if (r != 0) throw new Win32Exception(r, "read the rules of " + path);
            try {
                if (!ConvertSecurityDescriptorToStringSecurityDescriptor(sd, 1, OWNER | DACL, out text, out length))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                try { return Marshal.PtrToStringUni(text); } finally { LocalFree(text); }
            } finally { LocalFree(sd); }
        }
    }
    // The attributes of the entry at a path itself (FILE_ATTRIBUTE_*), or -1
    // when there is none. GetFileAttributes gives the attributes of a
    // symbolic link, not of its target, so a link reads as a reparse point
    // and a broken link as an entry (Microsoft: GetFileAttributesW).
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern uint GetFileAttributesW(string name);
    public static int ReadEntryAttributes(string path) {
        uint a = GetFileAttributesW(path);
        if (a != 0xFFFFFFFF) return (int)a;
        int e = Marshal.GetLastWin32Error();
        // ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND: no entry.
        if (e == 2 || e == 3) return -1;
        throw new Win32Exception(e, "read the attributes of " + path);
    }
    // The group of a file (G:, as SDDL without the prefix), or '' with none.
    public static string ReadGroup(string path) {
        using (SafeFileHandle h = Open(path, READ_CONTROL)) {
            IntPtr owner, group, dacl, sacl, sd, text; int length;
            int r = GetSecurityInfo(h, 1, GROUP, out owner, out group, out dacl, out sacl, out sd);
            if (r != 0) throw new Win32Exception(r, "read the group of " + path);
            try {
                if (group == IntPtr.Zero) return "";
                if (!ConvertSecurityDescriptorToStringSecurityDescriptor(sd, 1, GROUP, out text, out length))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                try { string g = Marshal.PtrToStringUni(text); return g.StartsWith("G:") ? g.Substring(2) : ""; } finally { LocalFree(text); }
            } finally { LocalFree(sd); }
        }
    }
    // The rules that a folder gives a new file in it, by Windows' own
    // inheritance function: the folder's security descriptor as the parent,
    // the owner and the group of the file as the creator, with an empty DACL
    // (so the result holds the inherited rules only; CREATOR OWNER and
    // CREATOR GROUP rules of the folder name them), the generic mapping of
    // files, and an object that is not a container. SEF_DACL_AUTO_INHERIT
    // (0x1), SEF_AVOID_PRIVILEGE_CHECK (0x8), SEF_AVOID_OWNER_CHECK (0x10).
    // The DACL of the result, as SDDL.
    public static string FileGives(string folderSddl, string owner, string group) {
        IntPtr parent, creator, created, text, token; int size, length;
        if (!ConvertStringSecurityDescriptorToSecurityDescriptor(folderSddl, 1, out parent, out size))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "the rules of the folder " + folderSddl);
        try {
            if (!ConvertStringSecurityDescriptorToSecurityDescriptor("O:" + owner + "G:" + group + "D:", 1, out creator, out size))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "the owner " + owner + " and the group " + group);
            try {
                // TOKEN_QUERY: the token of this process, which the function
                // reads for no default here (the creator has an owner, a group
                // and a DACL).
                if (!OpenProcessToken(GetCurrentProcess(), 0x8, out token)) throw new Win32Exception(Marshal.GetLastWin32Error(), "the token");
                try {
                    GenericMapping map = new GenericMapping();
                    map.Read = 0x120089; map.Write = 0x120116; map.Execute = 0x1200A0; map.All = 0x1F01FF;
                    if (!CreatePrivateObjectSecurityEx(parent, creator, out created, IntPtr.Zero, false, 0x1 | 0x8 | 0x10, token, ref map))
                        throw new Win32Exception(Marshal.GetLastWin32Error(), "what the folder gives");
                    try {
                        if (!ConvertSecurityDescriptorToStringSecurityDescriptor(created, 1, DACL, out text, out length))
                            throw new Win32Exception(Marshal.GetLastWin32Error());
                        try { return Marshal.PtrToStringUni(text); } finally { LocalFree(text); }
                    } finally { DestroyPrivateObjectSecurity(ref created); }
                } finally { CloseHandle(token); }
            } finally { LocalFree(creator); }
        } finally { LocalFree(parent); }
    }
    public static void SetOwner(string path, string owner) {
        IntPtr sid;
        if (!ConvertStringSidToSid(owner, out sid)) throw new Win32Exception(Marshal.GetLastWin32Error(), "the owner " + owner);
        try {
            using (SafeFileHandle h = Open(path, WRITE_OWNER)) {
                int r = SetSecurityInfo(h, 1, OWNER, sid, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
                if (r != 0) throw new Win32Exception(r, "set the owner of " + path);
            }
        } finally { LocalFree(sid); }
    }
    // The owner and the rules of an SDDL in one write by name. With no
    // protection, Windows computes the inherited rules from the folder in
    // this write, so a CREATOR OWNER rule of the folder names the owner of
    // the SDDL. WRITE_DAC comes from the take of ownership by the
    // Administrators, WRITE_OWNER from SeTakeOwnershipPrivilege, and any
    // owner is allowed with SeRestorePrivilege.
    public static void SetRules(string path, string sddl) {
        IntPtr sd, owner, dacl; int size; bool present, defaulted, ownerDefaulted; short control; int revision;
        if (!ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, 1, out sd, out size)) throw new Win32Exception(Marshal.GetLastWin32Error(), "the rules " + sddl);
        try {
            if (!GetSecurityDescriptorOwner(sd, out owner, out ownerDefaulted) || owner == IntPtr.Zero) throw new InvalidOperationException("no owner in " + sddl);
            if (!GetSecurityDescriptorDacl(sd, out present, out dacl, out defaulted) || !present) throw new InvalidOperationException("no rules in " + sddl);
            if (!GetSecurityDescriptorControl(sd, out control, out revision)) throw new Win32Exception(Marshal.GetLastWin32Error());
            // SE_DACL_PROTECTED: the rules take no rule from the folder.
            int info = OWNER | DACL | (((control & 0x1000) != 0) ? PROTECTED_DACL : UNPROTECTED_DACL);
            int r = SetNamedSecurityInfo(path, 1, info, owner, IntPtr.Zero, dacl, IntPtr.Zero);
            if (r != 0) throw new Win32Exception(r, "set the owner and the rules of " + path);
        } finally { LocalFree(sd); }
    }
}
'@

# Once per run: the type, then each privilege on. A privilege that is not on
# stops here. Each run calls it at its start, in every mode, before the
# state read and before any move (see the end of the script); the read and
# the write of the rules call it too, at no cost.
function Initialize-TnwksSecurity {
    if (-not ('TnwksSecurity' -as [type])) { Add-Type -TypeDefinition $TnwksSecuritySource }
    if (-not $script:TnwksPrivileges) {
        $enabled = [TnwksSecurity]::Enable()
        Write-Log "privileges: $enabled"
        Assert-Tnwks ($enabled -eq 'SeBackupPrivilege True, SeRestorePrivilege True, SeTakeOwnershipPrivilege True') "a privilege of SYSTEM is not on: $enabled"
        $script:TnwksPrivileges = $enabled
    }
}

function Get-TnwksSddl([string]$Path) {
    Initialize-TnwksSecurity
    [TnwksSecurity]::ReadSddl($Path)
}

function Set-TnwksSddl([string]$Path, [string]$Sddl) {
    Initialize-TnwksSecurity
    # The owner, then a group (G:, which is not written), then the rules.
    if ($Sddl -notmatch '^O:([^:]+?)(?=[GD]:)') { throw "no owner in the rules $Sddl" }
    $owner = $Matches[1]
    $dacl = if ($Sddl -match '(D:[^:]*)$') { $Matches[1] } else { throw "no rules in $Sddl" }
    # The explicit rules only: Windows gives the inherited ones. No rule of a
    # VM or of its capability either: WSL adds those at each start, and right
    # after a write the file has none.
    $explicit = $dacl -replace '\([^;()]*;[A-Z]*ID[A-Z]*;[^)]*\)', ''
    $wslSids = 'S-1-5-83-1-[0-9]+-[0-9]+-[0-9]+-[0-9]+' + $(if ($TnwksVmCapabilitySid) { '|' + [regex]::Escape($TnwksVmCapabilitySid) } else { '' })
    $explicit = $explicit -replace ('\(A;;(FA|0x1[fF]01[fF]{2});;;(' + $wslSids + ')\)'), ''
    [TnwksSecurity]::SetOwner($Path, 'BA')
    [TnwksSecurity]::SetRules($Path, "O:$owner$explicit")
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

# The rights of a rule as one number: SDDL writes a mask as hex or as names of
# two letters (FA, or CC and DC and so on), whichever Windows picks.
$TnwksRightNames = @{
    GA = 0x10000000; GR = 2147483648; GW = 0x40000000; GX = 0x20000000
    FA = 0x1F01FF; FR = 0x120089; FW = 0x120116; FX = 0x1200A0
    CC = 0x1; DC = 0x2; LC = 0x4; SW = 0x8; RP = 0x10; WP = 0x20; DT = 0x40; LO = 0x80; CR = 0x100
    SD = 0x10000; RC = 0x20000; WD = 0x40000; WO = 0x80000
}
function ConvertTo-TnwksSddlMask([string]$Rights) {
    if ($Rights -match '^0x[0-9a-fA-F]+$') { return '0x{0:x}' -f [Convert]::ToInt64($Rights.Substring(2), 16) }
    if ($Rights.Length -eq 0 -or $Rights.Length % 2) { return $Rights }
    [int64]$mask = 0
    for ($i = 0; $i -lt $Rights.Length; $i += 2) {
        $name = $Rights.Substring($i, 2).ToUpperInvariant()
        if (-not $TnwksRightNames.ContainsKey($name)) { return $Rights }
        $mask = $mask -bor [int64]$TnwksRightNames[$name]
    }
    '0x{0:x}' -f $mask
}

# WSL grants the VM of each start access to the disk file, as an explicit rule
# of a virtual machine SID, S-1-5-83-1- and four numbers (MS-DTYP, well-known
# SIDs: NT VIRTUAL MACHINE\Remote Virtual Machine; any other S-1-5-83 SID is
# not WSL's), and while the VM runs the file may
# also have an explicit rule for the capability SID that Windows derives from
# the capability name vmWorkerProcess (DeriveCapabilitySidsFromName: SHA-256
# of the name in upper case, as UTF-16). The config holds that one SID
# (vm_capability_sid); any other capability SID is a rule like any other. A
# clean stop removes both. Each counts as WSL's only as an allow rule with
# full control and no flag; in any other form it is a rule like any other.
# A check compares every other explicit rule and each inherited rule (as its
# folder gives it), allows these two kinds only (their counts are "vm" and
# "capability"), and allows no deny rule (their count is "deny").
function Get-TnwksSddlParts([string]$Sddl) {
    $owner = if ($Sddl -match 'O:([^:]+?)(?=[GDS]:|$)') { ConvertTo-TnwksSddlSid $Matches[1] } else { '' }
    $dacl = if ($Sddl -match 'D:([A-Z]*)((\([^)]*\))*)') { $Matches } else { @{ 1 = ''; 2 = '' } }
    $aces = @(); $inherited = @(); $vm = 0; $capability = 0; $deny = 0
    foreach ($ace in @([regex]::Matches($dacl[2], '\(([^)]*)\)') | ForEach-Object { $_.Groups[1].Value })) {
        $f = $ace -split ';'
        if ($f.Count -lt 6) { continue }
        $f[2] = ConvertTo-TnwksSddlMask $f[2]
        $f[-1] = ConvertTo-TnwksSddlSid $f[-1]
        if ($f[0] -ne 'A') { $deny++ }
        $wsl = $f[0] -eq 'A' -and $f[1] -eq '' -and $f[2] -eq '0x1f01ff'
        if ($f[1] -match 'ID') { $inherited += ($f -join ';') }
        elseif ($wsl -and $f[-1] -match '^S-1-5-83-1-[0-9]+-[0-9]+-[0-9]+-[0-9]+$') { $vm++ }
        elseif ($wsl -and $TnwksVmCapabilitySid -and $f[-1] -eq $TnwksVmCapabilitySid) { $capability++ }
        else { $aces += ($f -join ';') }
    }
    [pscustomobject]@{ owner = $owner; protected = ($dacl[1] -match 'P'); aces = (@($aces | Sort-Object) -join ' ')
        inherited = (@($inherited | Sort-Object) -join ' '); vm = $vm; capability = $capability; deny = $deny }
}

# What a folder gives a file in it, owned by $Owner, of the group $Group (the
# owner when none is given): the inherited part of the DACL that Windows' own
# inheritance function computes from the folder's rules
# (TnwksSecurity.FileGives), in the form of Get-TnwksSddlParts.
function Get-TnwksFolderGives([string]$FolderSddl, [string]$Owner, [string]$Group = '') {
    (Get-TnwksSddlParts ([TnwksSecurity]::FileGives($FolderSddl, $Owner, $(if ($Group) { $Group } else { $Owner })))).inherited
}

# The inherited rules that the folder of a file gives it, with its owner and
# its group: none for a file with protection.
function Get-TnwksFileGives([string]$Path, [pscustomobject]$Parts) {
    if ($Parts.protected) { return '' }
    $folder = Get-TnwksSddl (Split-Path -Parent $Path)
    Get-TnwksFolderGives $folder $Parts.owner ([TnwksSecurity]::ReadGroup($Path))
}

# The owner, the protection and the explicit rules as in the SDDL expected,
# and no deny rule. The inherited rules as in that SDDL; with -Saved (against
# a saved set: way back step 3, its restore-only condition, the state read
# and the play's reads) they are not compared: the way back writes the
# explicit rules of the saved set only, Windows gives the inherited ones from
# the folder, and a difference from the saved ones is a note. Whether the
# inherited rules are those that the folder gives is a fact of its own, for
# the guard of window step 1. The rules of WSL: any number of a VM, and at
# most one of its capability; -Exact (right after a write, which replaces
# every rule): none.
function Test-TnwksSddl([string]$Path, [string]$Expected, [switch]$Exact, [switch]$Saved) {
    $want = Get-TnwksSddlParts $Expected
    $have = Get-TnwksSddlParts (Get-TnwksSddl $Path)
    $wsl = if ($Exact) { $have.vm -eq 0 -and $have.capability -eq 0 } else { $have.capability -le 1 }
    $want.owner -eq $have.owner -and $want.protected -eq $have.protected -and $want.aces -eq $have.aces -and
        ($Saved -or $have.inherited -eq $want.inherited) -and $wsl -and $have.deny -eq 0
}

# A note in the log when the inherited rules of a file differ from those of a
# saved set: not a failed check, as the folder gives them.
function Write-TnwksInheritedNote([string]$Path, [string]$Saved, [string]$Step) {
    $want = (Get-TnwksSddlParts $Saved).inherited
    $have = (Get-TnwksSddlParts (Get-TnwksSddl $Path)).inherited
    if ($want -ne $have) {
        Write-Log ("{0}: note: the inherited rules differ from the saved set, as the folder gives them: saved '{1}', now '{2}'" -f
            $Step, (ConvertTo-TnwksMaskedText $want), (ConvertTo-TnwksMaskedText $have))
    }
}

# The owner and the rules of a file for the log, with the SID of each account
# masked: <present>, <new>, S-1-5-83-1-<vm> for a virtual machine, <sid>
# for any other.
function ConvertTo-TnwksMaskedText([string]$Text) {
    $sids = @{ (Get-TnwksSid $present.account) = '<present>'; (Get-TnwksSid $new.account) = '<new>' }
    foreach ($sid in $sids.Keys) { $Text = $Text -replace ([regex]::Escape($sid) + '(?![0-9-])'), $sids[$sid] }
    $Text -replace 'S-1-5-83-1(-[0-9]+){4}(?![0-9-])', 'S-1-5-83-1-<vm>' -replace 'S-1-5-21(-[0-9]+)+', '<sid>'
}

function Get-TnwksMaskedSddl([string]$Path) {
    try {
        if (-not (Test-Path -LiteralPath $Path)) { return 'no file' }
        ConvertTo-TnwksMaskedText (Get-TnwksSddl $Path)
    } catch { "not read: $($_.Exception.Message)" }
}

# The rule of the disk file in the folder of the new account (the config,
# disk_rule): its owner, and the access mask of the new account. SYSTEM and
# the Administrators have full control; no inheritance.
function Get-TnwksDiskSddl([string]$Sid) {
    $owner = if ($cfg.disk_rule.owner -eq 'new-account') { $Sid } else { 'BA' }
    'O:{0}D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;{1};;;{2})' -f $owner, $cfg.disk_rule.new_mask, $Sid
}
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

# --- The icon of the distro -------------------------------------------------
# An import in place writes the icon of the distro, shortcut.ico, in the
# folder of the disk file, as the account of the import, with a create that
# replaces a file that is there. The create needs the right to write the
# file, and fails on a file with the attribute hidden or system; a file with
# the attribute read-only can not be written (Microsoft: CreateFile). An
# unregister deletes the icon as its account. So a read before each import
# checks that the entry there is a plain file that the account of the import
# can write. The read takes the entry itself and follows no link: an entry
# with the attribute directory, reparse point or encrypted is refused, as
# are read-only, hidden and system, and its rules are read only for a plain
# file. In the rules, it counts the allow rules of the SIDs given only: the
# account, and the Administrators when the account imports as a member. Any
# deny rule is refused, and any object or conditional rule. Each import
# also requires its own result of this run: an import that fails for any
# other cause fails its check (window step 6 then goes back, and way back
# step 5 stops). The read changes nothing: no step deletes an icon.
$TnwksIconName = 'shortcut.ico'
$TnwksWrite = [int64]0x120116   # FILE_GENERIC_WRITE
$TnwksDelete = [int64]0x10000   # DELETE
# The rights of the generic bits on a file (Microsoft: File Security and Access Rights).
$TnwksFileGeneric = @{ [int64]0x10000000 = [int64]0x1F01FF; [int64]0x40000000 = [int64]0x120116
    [int64]0x20000000 = [int64]0x1200A0; [int64]2147483648 = [int64]0x120089 }

# What keeps the account of an import from replacing the icon in a folder
# ('' for no icon, or an icon that it can replace): each problem, in words.
function Get-TnwksIconProblem([string]$Folder, [string[]]$Sids, [int64]$Need) {
    $path = Join-Path $Folder $TnwksIconName
    $read = [TnwksSecurity]::ReadEntryAttributes($path)
    if ($read -eq -1) { return '' }
    $problems = @()
    $attributes = [IO.FileAttributes]$read
    $names = [ordered]@{ ReadOnly = 'read-only'; Hidden = 'hidden'; System = 'system'; Directory = 'directory'
        ReparsePoint = 'reparse point'; Encrypted = 'encrypted' }
    foreach ($a in $names.Keys) {
        if ($attributes.HasFlag([IO.FileAttributes]$a)) { $problems += "the attribute $($names[$a])" }
    }
    # A directory or a link: its rules are not read.
    if ($attributes.HasFlag([IO.FileAttributes]::Directory) -or $attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
        return "the icon $path has $($problems -join ', ')"
    }
    $sddl = Get-TnwksSddl $path
    $dacl = if ($sddl -match 'D:[A-Z]*((\([^)]*\))*)') { $Matches[1] } else { '' }
    [int64]$mask = 0; $deny = 0
    # An object rule (OA, OD) or a conditional rule (XA, XD): the read does not
    # take its form.
    if ($dacl -match '\((O|X)[A-Z]*;' -or $sddl -match '\((O|X)[A-Z]*;') { $problems += 'an object or conditional rule' }
    foreach ($ace in @([regex]::Matches($dacl, '\(([^)]*)\)') | ForEach-Object { $_.Groups[1].Value })) {
        $f = $ace -split ';'
        if ($f.Count -lt 6) { continue }
        # An inherit-only rule does not apply to the file itself.
        if ($f[1] -match 'IO') { continue }
        if ($f[0] -ne 'A') { if ($f[0] -match '^(D|OD|XD)$') { $deny++ }; continue }
        # SDDL writes the Administrators as BA.
        $sid = ConvertTo-TnwksSddlSid $f[5]
        if ($sid -eq 'BA') { $sid = 'S-1-5-32-544' }
        if ($sid -notin $Sids) { continue }
        $m = ConvertTo-TnwksSddlMask $f[2]
        if ($m -notmatch '^0x') { $problems += "a rule with rights that the read does not know ($($f[2]))"; continue }
        [int64]$bits = [Convert]::ToInt64($m.Substring(2), 16)
        foreach ($g in $TnwksFileGeneric.Keys) { if ($bits -band $g) { $bits = ($bits -band (-bnot $g)) -bor $TnwksFileGeneric[$g] } }
        $mask = $mask -bor $bits
    }
    if ($deny) { $problems += "$deny deny rule(s)" }
    if (($mask -band $Need) -ne $Need) { $problems += ('no rule that gives the right to {0} it' -f $(if ($Need -band $TnwksDelete) { 'write and delete' } else { 'write' })) }
    if (-not $problems.Count) { return '' }
    "the icon $path has $($problems -join ', '); the rules: $(ConvertTo-TnwksMaskedText $sddl)"
}

# The SIDs whose rules count for the import of the present account: the
# account, and the Administrators when it imports as a member.
function Get-TnwksImportSids([string]$Sid, [bool]$Member) {
    @($Sid) + @(if ($Member) { 'S-1-5-32-544' })
}

function Test-TnwksScope {
    $sep = [IO.Path]::DirectorySeparatorChar
    $root = [IO.Path]::GetFullPath($cfg.root).TrimEnd($sep)
    $paths = @($cfg.present.folder, $cfg.new.folder, $cfg.state_file, $cfg.log_file, $cfg.acl_file, $cfg.version_file)
    if ($cfg.boot_request_file) { $paths += $cfg.boot_request_file }
    foreach ($path in $paths) {
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
    if ("$($cfg.disk_rule.owner)" -notin @('administrators', 'new-account')) { return "the owner of the moved file is '$($cfg.disk_rule.owner)': administrators or new-account" }
    if ("$($cfg.disk_rule.new_mask)" -notmatch '^(0x[0-9a-fA-F]{1,8}|F[ARWX])$') { return "the mask of the new account is '$($cfg.disk_rule.new_mask)': a hex mask or FA, FR, FW, FX" }
    # The check for a session of the owner (site 1) is not in this script.
    if ($cfg.session_check) { return 'the session check is on, and this script does not have it' }
    # The group test takes the present account out of Administrators at the end
    # of its run: a restart in the run would keep it there.
    if ($req.rehearsal_group_test -and $cfg.restart) { return 'the group test runs only with the switch restart off' }
    # The rehearsal only: way back step 1 joins the present account to
    # Administrators with the group steps, and only the group test takes it out
    # at the end of the run. With a config of the rehearsal the two come
    # together, in each mode, from a request or from the boot part of the
    # config: a join with no leave would keep a temporary account in the group
    # across a restart. The real procedure runs the group steps with no group
    # test (the real present account stays in Administrators), so this holds
    # only for a config of the rehearsal. The config has no key that names
    # the rehearsal; boot_request_file stands for one, as only the config of
    # the rehearsal has a boot request.
    if ($cfg.boot_request_file -and [bool]$req.group_steps -ne [bool]$req.rehearsal_group_test) {
        return 'group_steps and rehearsal_group_test are not equal: the rehearsal has no join without its leave'
    }
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
$TnwksVmCapabilitySid = "$($cfg.vm_capability_sid)"
$newPath = Join-Path $new.folder $cfg.disk_name

function Test-TnwksOldAsSaved {
    try {
        if (-not ((Test-Path -LiteralPath $oldPath) -and (Test-Path -LiteralPath $cfg.acl_file))) { return $false }
        Test-TnwksSddl $oldPath ("$(Get-Content -LiteralPath $cfg.acl_file -Raw)").Trim() -Saved
    } catch { "not read: $($_.Exception.Message)" }
}

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
        # The rules at the old path, against the saved set of step 4.
        old_as_saved = Test-TnwksOldAsSaved
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
    # (1) Before any change: the disk file has the inherited rules that its
    # folder gives (none with protection). Way back step 3 puts back the
    # explicit rules of the saved set, and Windows gives the inherited ones
    # from the folder, so a file that lost them would not come back as saved.
    # Nor has it a rule that the check of the way back refuses: a deny rule
    # (explicit or inherited), more than one rule of the capability of the VM,
    # or an explicit rule of a VM SID or a capability SID that is not WSL's
    # (Get-TnwksSddlParts): the way back would fail its check on every run.
    if (Test-Path -LiteralPath $oldPath) {
        $diskSddl = Get-TnwksSddl $oldPath
        $diskParts = Get-TnwksSddlParts $diskSddl
        $gives = Get-TnwksFileGives $oldPath $diskParts
        $refused = @()
        if ($diskParts.deny -gt 0) { $refused += "$($diskParts.deny) deny rule(s)" }
        if ($diskParts.capability -gt 1) { $refused += "$($diskParts.capability) rules of the capability of the VM" }
        $odd = @("$($diskParts.aces)" -split ' ' | Where-Object { $_ -match ';(S-1-5-83-|S-1-15-3-)[0-9-]+$' })
        if ($odd.Count) { $refused += "$($odd.Count) rule(s) of a VM SID or a capability SID that are not WSL's" }
        if ($diskParts.inherited -ne $gives) {
            Write-Log ("stop at step 1: the inherited rules of the disk file are not those that its folder gives. Nothing is changed. The file: '{0}'; the folder gives: '{1}'" -f
                (ConvertTo-TnwksMaskedText $diskParts.inherited), (ConvertTo-TnwksMaskedText $gives))
        }
        if ($refused.Count) {
            Write-Log ("stop at step 1: the disk file has {0}, which the check of the way back refuses. Nothing is changed. The rules: '{1}'" -f
                ($refused -join ', '), (ConvertTo-TnwksMaskedText $diskSddl))
        }
        if ($refused.Count -or $diskParts.inherited -ne $gives) { return 'stopped' }
        Write-Log 'step 1: the disk file has the inherited rules that its folder gives, and no rule that the way back refuses'
    }
    # (1) The icon in each folder, before any change. The old folder: for an
    # import back by the present account, with the Administrators when it is
    # a member now (write and delete: the unregister of step 5 deletes it).
    # The group test of the rehearsal puts its account into the group just
    # after this read, so there it counts as a member. The folder of the new
    # account: for its import at step 6.
    $member = (Test-TnwksAdmin $pSid) -or [bool]$req.rehearsal_group_test
    $icons = @(
        (Get-TnwksIconProblem $present.folder (Get-TnwksImportSids $pSid $member) ($TnwksWrite -bor $TnwksDelete)),
        (Get-TnwksIconProblem $new.folder @($nSid) ($TnwksWrite -bor $TnwksDelete)) | Where-Object { $_ })
    if ($icons.Count) {
        # The text of a read holds the rules of the file: one line, bounded.
        foreach ($i in $icons) { Write-Log "stop at step 1: $(Format-TnwksText $i 1000). An import there would fail. Nothing is changed." }
        return 'stopped'
    }
    Write-Log 'step 1: no icon in either folder that the import there can not replace'
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
    # The saved rules of an earlier window are spent: way back step 3 restores
    # only the rules that step 4 of this window saves.
    if (Test-Path -LiteralPath $cfg.acl_file) {
        Remove-Item -LiteralPath $cfg.acl_file -Force
        Write-Log 'step 2: removed the saved rules of an earlier window'
    }
    Add-Marker 'window-started'
    foreach ($t in @($present.disable_tasks)) { Disable-TnwksTask $t }
    Write-Log 'step 2 passed'
    # (3) End their instances (a disabled task can still have one running), and
    # stop the VM of the present account.
    foreach ($t in @($present.disable_tasks)) { Stop-ScheduledTask -TaskName $t }
    Invoke-TnwksStopTask $present
    Wait-TnwksNoWsl $pSid $present.account
    Write-Log 'step 3 passed'
    # (4) The markers, then the move, the owner and the rules. The move is a
    # rename by the privileges that the run turned on at its start.
    Add-TnwksFolderMarker $present.folder $pSid
    Assert-Tnwks (Test-Path -LiteralPath (Join-Path $new.folder $cfg.marker_name)) "no marker file in $($new.folder)"
    Assert-Tnwks ((Test-Path -LiteralPath $oldPath) -and -not (Test-Path -LiteralPath $newPath)) 'the disk file is not at the old path only'
    $rules = Get-TnwksSddl $oldPath
    Write-TnwksFile $cfg.acl_file $rules
    $size = (Get-Item -LiteralPath $oldPath).Length
    Write-Log "step 4: saved the rules of the disk file; size $size; the rules: $(Get-TnwksMaskedSddl $oldPath)"
    Move-Item -LiteralPath $oldPath -Destination $newPath
    Set-TnwksSddl $newPath (Get-TnwksDiskSddl $nSid)
    Assert-Tnwks (-not (Test-Path -LiteralPath $oldPath)) 'the old path is not empty'
    Assert-Tnwks ((Get-Item -LiteralPath $newPath).Length -eq $size) 'the moved file has another size'
    Assert-Tnwks (Test-TnwksSddl $newPath (Get-TnwksDiskSddl $nSid) -Exact) 'the moved file has another owner or other rules'
    Write-Log 'step 4 passed'
    # (5) The unregister of the present account, with no file at its path.
    Assert-Tnwks (-not (Test-Path -LiteralPath $oldPath)) 'a file is at the path of the registration of the present account'
    $size = (Get-Item -LiteralPath $newPath).Length
    $unreg = Invoke-TnwksAccountTask $present.window_tasks.unregister (Join-Path $present.result_dir 'unregister.json')
    Assert-Tnwks ($unreg.ran -and $unreg.code -eq 0 -and $unreg.result -and -not $unreg.result.registered) "the unregister of $($present.account) did not pass ($(Format-TnwksTaskRun $unreg))"
    Assert-Tnwks ((Test-Path -LiteralPath $newPath) -and (Get-Item -LiteralPath $newPath).Length -eq $size) 'the disk file changed in the unregister'
    Assert-Tnwks (Test-Path -LiteralPath (Join-Path $present.folder $cfg.marker_name)) 'the old folder or its marker is gone'
    # The unregister deletes the icon of the distro as its account.
    Assert-Tnwks ([TnwksSecurity]::ReadEntryAttributes((Join-Path $present.folder $TnwksIconName)) -eq -1) "an icon is left in $($present.folder) after the unregister"
    Write-Log 'step 5 passed'
    if ([int]$req.stop_after -eq 5) {
        Write-Log 'stop after step 5 (the rehearsal): the marker of a started window stays'
        return 'stopped-after'
    }
    # (6) The import of the new account, then its stop. First the read of
    # its icon of step 1 again.
    $icon = Get-TnwksIconProblem $new.folder @($nSid) ($TnwksWrite -bor $TnwksDelete)
    Assert-Tnwks (-not $icon) "before the import of $($new.account): $icon"
    $import = Invoke-TnwksAccountTask $new.window_tasks.import (Join-Path $new.result_dir 'import.json')
    # What the import and its first start of the distro left on the file (the
    # rule of a VM), whether the import passed or not.
    Write-Log "step 6: the rules of the disk file after the import: $(Get-TnwksMaskedSddl $newPath)"
    Assert-Tnwks ($import.ran -and $import.code -eq 0 -and $import.result -and [string]::Equals("$($import.result.disk)", $newPath, [StringComparison]::OrdinalIgnoreCase)) "the import of $($new.account) did not pass ($(Format-TnwksTaskRun $import))"
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
    # (3) For step 4. First the move back: a rename keeps the rules. SYSTEM
    # moves the file by the privileges that the run turned on at its start,
    # not by its right on the folders: the file may grant SYSTEM nothing.
    # MoveFileEx names delete on the file or delete child on the folder, but
    # the rename also opens the file, and on such a file that open is refused
    # unless the privileges are on (Microsoft: MoveFileEx; Privilege
    # Constants). Then, at the old path, the
    # saved owner and rules: their inherited rules come from the old folder,
    # so they can be put back there only. A run that a power loss ends between
    # the two finds the file at the old path with other rules, and restores
    # only; with the saved rules there, nothing.
    $saved = if (Test-Path -LiteralPath $cfg.acl_file) { ("$(Get-Content -LiteralPath $cfg.acl_file -Raw)").Trim() } else { '' }
    if (Test-Path -LiteralPath $newPath) {
        Assert-Tnwks ((Get-TnwksWslProcess $pSid).Count -eq 0 -and (Get-TnwksWslProcess $nSid).Count -eq 0) 'a WSL process runs before the move back'
        Assert-Tnwks (Test-Path -LiteralPath (Join-Path $present.folder $cfg.marker_name)) 'the old folder or its marker is gone: no move back'
        # Window step 4 saved the rules before the move: with no saved rules,
        # no move back.
        Assert-Tnwks (Test-Path -LiteralPath $cfg.acl_file) 'no saved rules of the disk file: no move back'
        Assert-Tnwks ($saved -like 'O:*') 'the saved rules of the disk file are empty: no move back'
        $size = (Get-Item -LiteralPath $newPath).Length
        Move-Item -LiteralPath $newPath -Destination $oldPath
        Assert-Tnwks (-not (Test-Path -LiteralPath $newPath)) 'the new path is not empty'
        Assert-Tnwks ((Get-Item -LiteralPath $oldPath).Length -eq $size) 'the file moved back with another size'
        Write-Log 'way back 3: moved the file back'
    }
    # With no saved rules, step 4 did not start in this window: nothing to
    # restore.
    # The restore writes the saved owner, protection and explicit rules; the
    # check compares those exactly. The inherited rules are not part of "as
    # saved": Windows gives them from the old folder, and a difference from
    # the saved inherited rules is a note.
    if ($saved -like 'O:*' -and (Test-Path -LiteralPath $oldPath)) {
        if (Test-TnwksSddl $oldPath $saved -Saved) {
            Write-Log 'way back 3: the file has its saved owner and rules'
        } else {
            Assert-Tnwks ((Get-TnwksWslProcess $pSid).Count -eq 0 -and (Get-TnwksWslProcess $nSid).Count -eq 0) 'a WSL process runs before the restore of the rules'
            Set-TnwksSddl $oldPath $saved
            Assert-Tnwks (Test-TnwksSddl $oldPath $saved -Exact -Saved) 'the file has not its saved owner and rules'
            Write-Log "way back 3: restored the saved owner and rules: $(Get-TnwksMaskedSddl $oldPath)"
        }
        Write-TnwksInheritedNote $oldPath $saved 'way back 3'
    }
    Write-Log 'way back 3 passed'
    # (4) For step 6: the unregister of the new account. No distro is a pass.
    Assert-Tnwks (-not (Test-Path -LiteralPath $newPath)) 'a file is at the path of the registration of the new account'
    $size = (Get-Item -LiteralPath $oldPath).Length
    $unreg = Invoke-TnwksAccountTask $new.window_tasks.unregister (Join-Path $new.result_dir 'unregister.json')
    Assert-Tnwks ($unreg.ran -and $unreg.code -eq 0 -and $unreg.result -and -not $unreg.result.registered) "the unregister of $($new.account) did not pass ($(Format-TnwksTaskRun $unreg))"
    Assert-Tnwks ((Get-Item -LiteralPath $oldPath).Length -eq $size) 'the disk file changed in the unregister'
    Assert-Tnwks (Test-Path -LiteralPath (Join-Path $new.folder $cfg.marker_name)) 'the new folder or its marker is gone'
    Write-Log 'way back 4 passed'
    # (5) For step 5: the import back of the present account. First the icon
    # in the old folder: one that the import back can not replace stops the
    # way back here, before the import. Nothing deletes it: the way out is a
    # reviewed change of the play.
    $icon = Get-TnwksIconProblem $present.folder (Get-TnwksImportSids $pSid (Test-TnwksAdmin $pSid)) $TnwksWrite
    Assert-Tnwks (-not $icon) "before the import back of $($present.account): $icon. The way back stops here, and no step deletes the icon"
    # From here a VM of the present account may run, started in this run.
    $script:ImportBackRan = $true
    $import = Invoke-TnwksAccountTask $present.window_tasks.import (Join-Path $present.result_dir 'import.json')
    Assert-Tnwks ($import.ran -and $import.code -eq 0 -and $import.result -and [string]::Equals("$($import.result.disk)", $oldPath, [StringComparison]::OrdinalIgnoreCase)) "the import back of $($present.account) did not pass ($(Format-TnwksTaskRun $import))"
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
        $script:StopBeforeLeaveRan = $true
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
if ($BootRequestError) {
    Write-Log "stop: $BootRequestError. Nothing is changed."
    exit 3
}
Write-Log "start, request: $($req | ConvertTo-Json -Compress)"
$outcome = 'failed'
try {
    # The privileges first, in every mode: the state read, the move and the
    # rules of the disk file come after. A privilege that is not on fails
    # the run here, with no marker and nothing changed.
    Initialize-TnwksSecurity
    # The rehearsal only: in the group test of a boot run, the present account
    # is in Administrators only inside the run. A run that a power loss ended
    # can leave it there: this run takes it out first, before it reads the
    # marker (no VM of the account runs right after a boot).
    if ($Mode -eq 'boot' -and $req.rehearsal_group_test -and (Test-TnwksAdmin (Get-TnwksSid $present.account))) {
        Write-Log "$($present.account) is in Administrators at the start of a boot run: an earlier run did not end"
        Set-TnwksGroup (Get-TnwksSid $present.account) $present.account $false
    }
    switch ($Mode) {
        'forward' {
            try {
                $outcome = Invoke-TnwksForward
            } catch {
                # A failed check, or any error, after the marker of a started
                # window: the way back, from the state that it reads.
                Write-TnwksFail $_.Exception.Message
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
    Write-TnwksFail $_.Exception.Message
    $outcome = 'failed'
} finally {
    if ($req.rehearsal_group_test) {
        # The rehearsal only: the present account leaves Administrators at the
        # end of its run, whatever the result.
        try { Exit-TnwksGroupTest } catch { Write-TnwksFail "the group of $($present.account): $($_.Exception.Message)" }
    }
    # The boot request is spent, once the present account is out of the group.
    if ($BootRequestRead) {
        try {
            if (Test-TnwksAdmin (Get-TnwksSid $present.account)) {
                Write-Log "the boot request stays: $($present.account) is still in Administrators"
            } else {
                Remove-Item -LiteralPath $cfg.boot_request_file -Force
                Write-Log 'the boot request is spent: removed'
            }
        } catch { Write-TnwksFail "the boot request: $($_.Exception.Message)" }
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
