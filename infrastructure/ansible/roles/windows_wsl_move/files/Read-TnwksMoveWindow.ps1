<#
.SYNOPSIS
The reads of the window play (playbooks/windows-move-window.yml): the state
of the window, the guards of its start, the readiness reads before it, and
the check play of step 6 (Design Doc 0004, section 9, steps 5 and 6). It also
starts the window, once, in the mode start. It runs as SYSTEM, from the play
(win_powershell), and is never copied to the host.

.DESCRIPTION
Modes:
  status     The state file, the request, the window task and the boot task,
             and the tail of the window log. It never fails on a file that is
             not there: it says "absent".
  guards     Each guard of a start of the window, for the way of the config
             (forward or wayback), as lines of pass or fail.
  readiness  The reads before the window, as lines of pass or fail.
  check      The check play of step 6, as lines of pass or fail.
  start      The guards again, then the request and one start of the window
             task. It does not wait: the path of the lead ends when WSL stops.

It changes nothing, apart from the mode start, which writes the request file
and starts the window task. The lines of pass or fail hold no value from the
host. The status masks each account, SID and folder of the move.

It reads with the parts of the window procedure itself: the copy of
Invoke-TnwksMoveWindow.ps1 on the host, after its SHA-256 (the guard of the
copies), gives the reads of the processes, of the Administrators and the
hardened open (Import-TnwksWindowPart). The read of the disk file is the host
check's own (Measure-TnwksWslDisk), from its copy, after its SHA-256.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][ValidateSet('status', 'guards', 'readiness', 'check', 'start')][string]$Mode,
    # The config of the play, as JSON (vars/main.yml, wsl_move_window_read).
    [Parameter(Mandatory)][string]$Config
)
$ErrorActionPreference = 'Stop'
$c = $Config | ConvertFrom-Json
# win_powershell gives $Ansible: only the start is a change.
if (Get-Variable -Name Ansible -ErrorAction SilentlyContinue) { $Ansible.Changed = ($Mode -eq 'start') }

# A Windows path joined by hand: the reads run on Windows only, and the tests
# on any system.
function Join-TnwksPath([string]$Folder, [string]$Name) { "$($Folder.TrimEnd('\'))\$Name" }
$DiskOld = Join-TnwksPath $c.present.folder $c.disk_name
$DiskNew = Join-TnwksPath $c.new.folder $c.disk_name

# --- Parts of the window procedure ------------------------------------------

# The text of named functions and variables of the top level of a script,
# from its syntax tree: the caller dot-sources it. Nothing else of the script
# runs.
function Import-TnwksWindowPart([string]$Path, [string[]]$Functions, [string[]]$Variables) {
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw "the window script does not parse: $($errors.Count) error(s)" }
    $parts = @{}
    foreach ($s in $ast.EndBlock.Statements) {
        if ($s -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $s.Name -in $Functions) { $parts[$s.Name] = $s.Extent.Text }
        if ($s -is [System.Management.Automation.Language.AssignmentStatementAst] -and
            $s.Left -is [System.Management.Automation.Language.VariableExpressionAst] -and
            $s.Left.VariablePath.UserPath -in $Variables) { $parts['$' + $s.Left.VariablePath.UserPath] = $s.Extent.Text }
    }
    foreach ($name in @($Functions) + @($Variables | ForEach-Object { '$' + $_ })) {
        if (-not $parts.ContainsKey($name)) { throw "the window script has no $name" }
    }
    ($parts.Values -join "`n")
}

$TnwksWindowFunctions = @('Get-TnwksSid', 'Get-TnwksAdminSids', 'Test-TnwksAdmin', 'Get-TnwksWslInstallFolder',
    'Test-TnwksWslImage', 'Get-TnwksWslProcess', 'Get-TnwksSessionUsers', 'Test-TnwksAccountSession')
$TnwksWindowVariables = @('AdministratorsSid', 'TnwksWslPackageNames', 'TnwksWslSystemNames', 'TnwksSecuritySource',
    'TnwksSessionSource')

# Once, and only from a copy that has the SHA-256 of the role's.
function Initialize-TnwksWindowPart {
    if ($script:TnwksWindowPartsLoaded) { return }
    if (-not (Test-TnwksCopy $c.window_script)) { throw 'the copy of the window script is not the role''s' }
    $script:TnwksWindowPartText = Import-TnwksWindowPart $c.window_script $TnwksWindowFunctions $TnwksWindowVariables
    $script:TnwksWindowPartsLoaded = $true
}

# --- Reads of Windows ---------------------------------------------------------

$TnwksWindowReadSource = @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
// The groups of the token of a process, each as "<SID> <attributes>" (TokenGroups).
public static class TnwksMoveToken {
    [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr OpenProcess(int access, bool inherit, int pid);
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool OpenProcessToken(IntPtr process, int access, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool GetTokenInformation(IntPtr token, int cls, IntPtr buffer, int length, out int needed);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    public static string[] Groups(int pid) {
        IntPtr process = OpenProcess(0x1000, false, pid);
        if (process == IntPtr.Zero) throw new Win32Exception();
        IntPtr token;
        try { if (!OpenProcessToken(process, 0x8, out token)) throw new Win32Exception(); }
        finally { CloseHandle(process); }
        try {
            int needed;
            GetTokenInformation(token, 2, IntPtr.Zero, 0, out needed);
            IntPtr buffer = Marshal.AllocHGlobal(needed);
            try {
                if (!GetTokenInformation(token, 2, buffer, needed, out needed)) throw new Win32Exception();
                int count = Marshal.ReadInt32(buffer);
                List<string> groups = new List<string>();
                for (int i = 0; i < count; i++) {
                    IntPtr entry = new IntPtr(buffer.ToInt64() + IntPtr.Size + i * 2 * IntPtr.Size);
                    int attributes = Marshal.ReadInt32(entry, IntPtr.Size);
                    groups.Add(new SecurityIdentifier(Marshal.ReadIntPtr(entry)).Value + " " + attributes.ToString("x"));
                }
                return groups.ToArray();
            } finally { Marshal.FreeHGlobal(buffer); }
        } finally { CloseHandle(token); }
    }
}
// The target of a drive letter: \Device\... for a volume, \??\... for a subst drive.
public static class TnwksDosDevice {
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern uint QueryDosDeviceW(string name, StringBuilder target, int max);
    public static string Target(string drive) {
        StringBuilder target = new StringBuilder(1024);
        if (QueryDosDeviceW(drive, target, target.Capacity) == 0) throw new Win32Exception(Marshal.GetLastWin32Error());
        return target.ToString();
    }
}
'@

function Initialize-TnwksWindowRead {
    if (-not ('TnwksDosDevice' -as [type])) { Add-Type -TypeDefinition $TnwksWindowReadSource }
}

function Get-TnwksBootTime { (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime() }

function Get-TnwksRegisteredTask([string]$Name) { Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue }

# Whether a copy on the host has the SHA-256 of the role's (the config's hashes).
function Test-TnwksCopy([string]$Path) {
    $want = "$($c.hashes.$Path)"
    if (-not $want -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $want.ToUpperInvariant()
}

function Get-TnwksLastMarker {
    if (-not (Test-Path -LiteralPath $c.state_file -PathType Leaf)) { return $null }
    $lines = @(Get-Content -LiteralPath $c.state_file | Where-Object { $_.Trim() })
    if ($lines.Count -eq 0) { return '' }
    "$(($lines[-1] -split ' ', 2)[1])".Trim()
}

function Test-TnwksRestartPending {
    (Test-Path -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') -or
    (Test-Path -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending')
}

# The read of the procedure itself (Test-TnwksAccountSession), from the copy of
# the window script on the host, after its SHA-256: the same read that window
# step 1 and the way back make with session_check on.
function Test-TnwksPresentSession {
    Initialize-TnwksWindowPart
    . ([scriptblock]::Create($script:TnwksWindowPartText))
    Test-TnwksAccountSession $c.present.account
}

function Get-TnwksInstalledWslVersion {
    if (-not (Test-Path -LiteralPath $c.service_binary -PathType Leaf)) { return '' }
    "$([Diagnostics.FileVersionInfo]::GetVersionInfo($c.service_binary).FileVersion)".Trim()
}

# Whether a task runs WSL: a command or an argument that names wsl.exe,
# wslg.exe or bash.exe, a command that is wsl, or a script file in its
# arguments that runs one of them.
$TnwksWslWord = '(?i)\b(wsl|wslg|bash)\.exe\b'
function Get-TnwksActionScript([string]$Path) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) { [IO.File]::ReadAllText($Path) }
}
function Test-TnwksTaskRunsWsl($Task) {
    foreach ($a in @($Task.Actions | Where-Object { $_ })) {
        $command = "$($a.Execute)".Trim().Trim('"')
        $arguments = "$($a.Arguments)"
        if ("$command $arguments" -match $TnwksWslWord) { return $true }
        if ($command -match '(?i)(^|\\)(wsl|wslg|bash)$') { return $true }
        foreach ($m in [regex]::Matches($arguments, '(?i)[A-Za-z]:\\[^"]+?\.(ps1|psm1|cmd|bat|vbs|js)\b')) {
            if ("$(Get-TnwksActionScript $m.Value)" -match "$TnwksWslWord|(?im)(^|[\s&(;])wsl\s+-") { return $true }
        }
    }
    $false
}

# Whether a task runs as an account: its principal names it, with or without
# the computer, or by its SID.
function Test-TnwksTaskOf($Task, [string]$Account, [string]$Sid) {
    $user = "$($Task.Principal.UserId)"
    ($Sid -and $user -eq $Sid) -or [string]::Equals(($user -split '\\')[-1], $Account, [StringComparison]::OrdinalIgnoreCase)
}

# The enabled tasks of the present account that run WSL, other than those of
# the config: its WSL tasks and its three window tasks.
function Get-TnwksOtherWslTask([string]$Sid) {
    $known = @($c.present.tasks) + @($c.present.window_tasks)
    @(Get-ScheduledTask | Where-Object {
        $_.Settings.Enabled -and $_.TaskName -notin $known -and (Test-TnwksTaskOf $_ $c.present.account $Sid) -and (Test-TnwksTaskRunsWsl $_)
    })
}

# The registration of the distro in the loaded hive of an account, as SYSTEM:
# $null when the hive is not loaded. No hive is loaded here.
function Get-TnwksHiveRegistration([string]$Sid) {
    if (-not (Test-Path -LiteralPath "Registry::HKEY_USERS\$Sid")) { return $null }
    $lxss = "Registry::HKEY_USERS\$Sid\Software\Microsoft\Windows\CurrentVersion\Lxss"
    if (-not (Test-Path -LiteralPath $lxss)) { return [pscustomobject]@{ root = $null; found = @() } }
    [pscustomobject]@{
        root = Get-ItemProperty -LiteralPath $lxss
        found = @(Get-ChildItem -LiteralPath $lxss | ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath } |
            Where-Object { $_.DistributionName -eq $c.distro })
    }
}

function Get-TnwksRegistrationDisk($Registration) {
    $base = "$($Registration.BasePath)" -replace '^\\\\\?\\', ''
    Join-TnwksPath $base $(if ($Registration.VhdFileName) { "$($Registration.VhdFileName)" } else { $c.disk_name })
}

# The read of the host check (wsl-disk-rules), from its copy on the host after
# its SHA-256: the rules that the file breaks, or $null when the copy is not
# the role's.
function Get-TnwksHostCheckRead([string]$Path, [string]$Account) {
    if (-not (Test-Path -LiteralPath $c.host_check -PathType Leaf)) { return $null }
    if ((Get-FileHash -LiteralPath $c.host_check -Algorithm SHA256).Hash -ne "$($c.host_check_sha256)".ToUpperInvariant()) { return $null }
    . $c.host_check -Library
    , @(Measure-TnwksWslDisk -Path $Path -Account $Account -CapabilitySid $c.capability_sid)
}

# The batch logons of an account in the Security log (event 4624, logon type
# 4), newest first: whether each had an elevated token (%%1842 is Yes).
function Get-TnwksBatchLogon([string]$Sid) {
    $xpath = "*[System[EventID=4624] and EventData[Data[@Name='LogonType']='4'] and EventData[Data[@Name='TargetUserSid']='$Sid']]"
    @(Get-WinEvent -LogName Security -FilterXPath $xpath -MaxEvents ([int]$c.batch_logons) -ErrorAction SilentlyContinue | ForEach-Object {
        $data = @(([xml]$_.ToXml()).Event.EventData.Data | Where-Object { $_.Name -eq 'ElevatedToken' })
        ($data.Count -eq 1 -and "$($data[0].'#text')".Trim() -eq '%%1842')
    })
}

# --- Lines --------------------------------------------------------------------

# One line: its name, and whether it passed. A read that throws is a fail.
function New-TnwksLine([string]$Name, [scriptblock]$Read) {
    $pass = try { [bool](& $Read) } catch { $false }
    [pscustomobject]@{ name = $Name; pass = $pass }
}

function Get-TnwksGuard {
    $way = "$($c.way)"
    $last = Get-TnwksLastMarker
    $allowed = if ($way -eq 'wayback') { @('window-done', 'window-started', 'boot-run') } else { @('', 'wayback-done') }
    $copies = @($c.hashes.PSObject.Properties | ForEach-Object { $_.Name })
    $session = try { Test-TnwksPresentSession } catch { $null }
    @(
        (New-TnwksLine 'the copies of the scripts and the configs on the host are the role''s' {
            $copies.Count -gt 0 -and @($copies | Where-Object { -not (Test-TnwksCopy $_) }).Count -eq 0 })
        (New-TnwksLine "the last marker of the state file lets a run of the way $way start" { $null -ne $last -and $last -in $allowed })
        (New-TnwksLine 'the log of the window is there' { Test-Path -LiteralPath $c.log_file -PathType Leaf })
        (New-TnwksLine 'the window task is there and enabled, and does not run' {
            $t = Get-TnwksRegisteredTask $c.window_task
            $t -and $t.Settings.Enabled -and "$($t.State)" -notin @('Running', 'Queued') })
        (New-TnwksLine 'the boot task of the way back is there and enabled' {
            $t = Get-TnwksRegisteredTask $c.boot_task
            $t -and $t.Settings.Enabled })
        (New-TnwksLine 'the window tasks of both accounts are there and enabled' {
            $names = @($c.present.window_tasks) + @($c.new.window_tasks)
            @($names | Where-Object { $t = Get-TnwksRegisteredTask $_; -not ($t -and $t.Settings.Enabled) }).Count -eq 0 })
        (New-TnwksLine 'each WSL task of the present account in the config is there' {
            @($c.present.tasks | Where-Object { -not (Get-TnwksRegisteredTask $_) }).Count -eq 0 })
        (New-TnwksLine $(if ($way -eq 'wayback') { 'each start task of the new account is there' } else { 'each start task of the new account is there and disabled' }) {
            @($c.new.start_tasks).Count -gt 0 -and @($c.new.start_tasks | Where-Object {
                $t = Get-TnwksRegisteredTask $_
                -not $t -or ($way -ne 'wayback' -and $t.Settings.Enabled) }).Count -eq 0 })
        (New-TnwksLine 'the installed WSL is the version of the rehearsal and the version that step 3 saved' {
            $installed = Get-TnwksInstalledWslVersion
            $saved = if (Test-Path -LiteralPath $c.version_file -PathType Leaf) { "$(Get-Content -LiteralPath $c.version_file -Raw)".Trim() } else { '' }
            $installed -and $installed -eq "$($c.rehearsed_version)".Trim() -and $installed -eq $saved })
        (New-TnwksLine 'no restart is pending for updates' { -not (Test-TnwksRestartPending) })
        $(if ($session -and $c.force_session) {
            [pscustomobject]@{ name = 'a session of the present account is on the host, and the run goes on by wsl_move_window_force_session'; pass = $true }
        } else {
            [pscustomobject]@{ name = 'no session of the present account is on the host'; pass = ($session -eq $false) }
        })
    )
}

# The line when the parts of the window script, or the SIDs, are not read.
$TnwksPartLine = 'the copy of the window script on the host is the role''s, its parts load, and both accounts are there'

function Get-TnwksReadiness {
    try {
        Initialize-TnwksWindowPart
        . ([scriptblock]::Create($script:TnwksWindowPartText))
        $pSid = Get-TnwksSid $c.present.account
        $null = Get-TnwksSid $c.new.account
    } catch { return , @([pscustomobject]@{ name = $TnwksPartLine; pass = $false }) }
    $registration = Get-TnwksHiveRegistration $pSid
    @(
        (New-TnwksLine 'no other enabled task of the present account runs WSL' { @(Get-TnwksOtherWslTask $pSid).Count -eq 0 })
        (New-TnwksLine 'the present account registers the distro by its name, once, at the folder of the config' {
            $registration -and $registration.found.Count -eq 1 -and
            [string]::Equals((Get-TnwksRegistrationDisk $registration.found[0]), $DiskOld, [StringComparison]::OrdinalIgnoreCase) })
        (New-TnwksLine 'the folder of today and the folder of the new disk file are on one drive, which is not a subst drive' {
            Initialize-TnwksWindowRead
            $drive = $c.present.folder.Substring(0, 2)
            [string]::Equals($drive, $c.new.folder.Substring(0, 2), [StringComparison]::OrdinalIgnoreCase) -and
            [TnwksDosDevice]::Target($drive).StartsWith('\Device\', [StringComparison]::OrdinalIgnoreCase) })
        (New-TnwksLine 'the distro of the present account runs' { @(Get-TnwksWslProcess $pSid).Count -gt 0 })
        (New-TnwksLine 'the hardened open of the procedure reads the disk file and both folders as SYSTEM' {
            if (-not ('TnwksSecurity' -as [type])) { Add-Type -TypeDefinition $TnwksSecuritySource }
            if ([TnwksSecurity]::Enable() -ne 'SeBackupPrivilege True, SeRestorePrivilege True, SeTakeOwnershipPrivilege True') { return $false }
            foreach ($path in @($DiskOld, $c.present.folder, $c.new.folder)) { [void][TnwksSecurity]::ReadSddl($path) }
            [void][TnwksSecurity]::ReadGroup($DiskOld)
            $true })
        (New-TnwksLine 'the read of the host check finds the disk file of today, reads it, and it is a plain file' {
            $broken = Get-TnwksHostCheckRead $DiskOld $c.present.account
            $null -ne $broken -and @($broken | Where-Object { $_ -in @('missing', 'unreadable', 'not-a-plain-file', 'account') }).Count -eq 0 })
        (New-TnwksLine 'each batch logon of the present account in the Security log had an elevated token, and it is in Administrators' {
            $logons = @(Get-TnwksBatchLogon $pSid)
            (Test-TnwksAdmin $pSid) -and $logons.Count -gt 0 -and @($logons | Where-Object { -not $_ }).Count -eq 0 })
    )
}

# Each WSL process of an account reads Medium Mandatory Level (the label of
# its token, SE_GROUP_INTEGRITY 0x20) and no Administrators group that is
# enabled (SE_GROUP_ENABLED 0x4; a filtered token has it for deny only), and
# at least one runs.
function Get-TnwksCheckToken([string]$Sid) {
    Initialize-TnwksWindowRead
    $processes = @(Get-TnwksWslProcess $Sid)
    $processes.Count -gt 0 -and @($processes | Where-Object {
        $groups = @([TnwksMoveToken]::Groups([int]$_.ProcessId))
        $admin = @($groups | Where-Object { $_ -match '^S-1-5-32-544 ([0-9a-f]+)$' -and ([Convert]::ToInt32($Matches[1], 16) -band 0x4) })
        $level = @($groups | Where-Object { $_ -match '^(S-1-16-[0-9]+) ([0-9a-f]+)$' -and ([Convert]::ToInt32($Matches[2], 16) -band 0x20) } |
            ForEach-Object { ($_ -split ' ')[0] })
        $admin.Count -gt 0 -or $level.Count -ne 1 -or $level[0] -ne 'S-1-16-8192' }).Count -eq 0
}

# The check play of step 6, after the restart of the window.
function Get-TnwksCheck {
    try {
        Initialize-TnwksWindowPart
        . ([scriptblock]::Create($script:TnwksWindowPartText))
        $pSid = Get-TnwksSid $c.present.account
        $nSid = Get-TnwksSid $c.new.account
    } catch { return , @([pscustomobject]@{ name = $TnwksPartLine; pass = $false }) }
    $boot = Get-TnwksBootTime
    $markers = if (Test-Path -LiteralPath $c.state_file -PathType Leaf) { @(Get-Content -LiteralPath $c.state_file | Where-Object { $_.Trim() }) } else { @() }
    $log = if (Test-Path -LiteralPath $c.log_file -PathType Leaf) { @(Get-Content -LiteralPath $c.log_file) } else { @() }
    # The texts of the last run in the mode forward ("<time> <mode> <text>").
    $start = -1
    for ($i = $log.Count - 1; $i -ge 0; $i--) { if ($log[$i] -match '^\S+ forward start, request: ') { $start = $i; break } }
    $run = if ($start -ge 0) { @($log[$start..($log.Count - 1)] | Where-Object { $_ -match '^\S+ forward ' } | ForEach-Object { ($_ -split ' ', 3)[2] }) } else { @() }
    $saved = if (Test-Path -LiteralPath $c.registration_file -PathType Leaf) { Get-Content -LiteralPath $c.registration_file -Raw | ConvertFrom-Json } else { $null }
    $registration = Get-TnwksHiveRegistration $nSid
    @(
        (New-TnwksLine 'the last marker is a done window, written before the boot' {
            $markers.Count -gt 0 -and "$(($markers[-1] -split ' ', 2)[1])".Trim() -eq 'window-done' -and
            ([datetime]::Parse(($markers[-1] -split ' ', 2)[0], $null, 'RoundtripKind').ToUniversalTime() -lt $boot) })
        (New-TnwksLine 'the last forward run passed steps 5, 7 and 8 and ended done; the present account listed no distro at step 5' {
            $run -contains 'step 5 passed' -and $run -contains 'step 7 passed' -and $run -contains 'step 8 passed' -and $run -contains 'result: done' })
        (New-TnwksLine 'each WSL process of the new account reads Medium Mandatory Level and no Administrators group, and one runs' {
            Get-TnwksCheckToken $nSid })
        (New-TnwksLine 'no WSL process of the present account runs' { @(Get-TnwksWslProcess $pSid).Count -eq 0 })
        (New-TnwksLine 'each WSL task of the present account in the config is disabled' {
            @($c.present.tasks | Where-Object { $t = Get-TnwksRegisteredTask $_; -not $t -or $t.Settings.Enabled }).Count -eq 0 })
        (New-TnwksLine 'no other enabled task of the present account runs WSL' { @(Get-TnwksOtherWslTask $pSid).Count -eq 0 })
        (New-TnwksLine 'each start task of the new account is enabled, and ran after the boot with result 0 or still runs' {
            @($c.new.start_tasks).Count -gt 0 -and @($c.new.start_tasks | Where-Object {
                $t = Get-TnwksRegisteredTask $_
                $i = if ($t) { Get-ScheduledTaskInfo -TaskName $_ }
                -not ($t -and $t.Settings.Enabled -and $i.LastRunTime -and $i.LastRunTime.ToUniversalTime() -gt $boot -and
                    ([int64]$i.LastTaskResult -eq 0 -or "$($t.State)" -eq 'Running')) }).Count -eq 0 })
        (New-TnwksLine 'the new account registers the distro once, at the new disk file, as its default distro when it was the default of today' {
            $registration -and $registration.found.Count -eq 1 -and
            [string]::Equals((Get-TnwksRegistrationDisk $registration.found[0]), $DiskNew, [StringComparison]::OrdinalIgnoreCase) -and
            (-not $saved.default -or "$($registration.root.DefaultDistribution)" -eq $registration.found[0].PSChildName) })
        # The user of a launch with no user, by the rule of WSL (its init,
        # src/linux/init/init.cpp): the user that [user] default of
        # /etc/wsl.conf names, when that user exists; else the user of the
        # DefaultUid of the registration; else root. An import in place
        # registers DefaultUid 0, root (LxssUserSession.cpp,
        # ImportDistributionInplace). This script reads the registration only.
        # The read in the distro gives the first part (wsl_conf_uid): the uid
        # of the user that wsl.conf names, or 'none' when it names no user that
        # exists. With no such read, the line fails.
        (New-TnwksLine 'a launch with no user takes the user of today, not root: the user that /etc/wsl.conf names (the read in the distro), else the default uid of the registration' {
            $reg = $registration.found[0]
            $conf = "$($c.wsl_conf_uid)".Trim()
            $uid = if ($conf -match '^[0-9]+$') { [int64]$conf }
                elseif ($conf -eq 'none' -and $null -ne $reg.DefaultUid) { [int64]$reg.DefaultUid }
                else { $null }
            $null -ne $uid -and $uid -ne 0 -and $uid -eq [int64]$saved.dwords.DefaultUid })
        (New-TnwksLine 'the flags, the default environment and the kernel command line of the registration are as step 3 read them' {
            $reg = $registration.found[0]
            $flags = @($saved.dwords.PSObject.Properties | Where-Object { $_.Name -ne 'DefaultUid' } |
                Where-Object { [int64]$reg.($_.Name) -ne [int64]$_.Value })
            $null -ne $saved -and $flags.Count -eq 0 -and "$($reg.KernelCommandLine)" -ceq "$($saved.kernel_command_line)" -and
            (@($reg.DefaultEnvironment | ForEach-Object { "$_" }) -join "`n") -ceq (@($saved.default_environment | ForEach-Object { "$_" }) -join "`n") })
        (New-TnwksLine 'the port proxy points to the address that the new VM wrote after the boot' {
            $file = Get-Item -LiteralPath $c.bridge_address_file
            $address = "$((Get-Content -LiteralPath $c.bridge_address_file -Raw | ConvertFrom-Json).address)".Trim()
            $targets = @(& netsh.exe interface portproxy show v4tov4 | ForEach-Object { "$_".Trim() } |
                Where-Object { $_ -match '^\S+\s+[0-9]+\s+(\S+)\s+[0-9]+$' } | ForEach-Object { ($_ -split '\s+')[2] })
            $file.LastWriteTimeUtc -gt $boot -and $address -and $targets.Count -gt 0 -and @($targets | Where-Object { $_ -ne $address }).Count -eq 0 })
        (New-TnwksLine 'the .wslconfig of the new account holds each setting of the .wslconfig of today' {
            $profile = (Get-CimInstance Win32_UserProfile -Filter "SID='$nSid'").LocalPath
            $have = @([IO.File]::ReadAllLines((Join-TnwksPath $profile '.wslconfig')) | ForEach-Object { $_.Trim() })
            $want = @([IO.File]::ReadAllLines($c.wslconfig_file) | ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -notmatch '^[#;]' })
            $want.Count -gt 1 -and @($want | Where-Object { $_ -notin $have }).Count -eq 0 })
        (New-TnwksLine 'the read of the host check of the moved disk file passes: its owner is the new account, and its rules are the rule' {
            $broken = Get-TnwksHostCheckRead $DiskNew $c.new.account
            $null -ne $broken -and $broken.Count -eq 0 })
        (New-TnwksLine 'the old path of the disk file is empty' { -not (Test-Path -LiteralPath $DiskOld) })
        (New-TnwksLine 'neither account is in Administrators' { -not (Test-TnwksAdmin $pSid) -and -not (Test-TnwksAdmin $nSid) })
    )
}

# --- Status ---------------------------------------------------------------------

# Each name, SID and folder of the move, and any other SID of an account,
# masked.
function Get-TnwksMask {
    $pairs = New-Object System.Collections.Generic.List[object]
    foreach ($key in 'present', 'new') {
        try { $pairs.Add(@((Get-LocalUser -Name $c.$key.account).SID.Value, "<$key-sid>")) } catch { }
    }
    $pairs.Add(@("$($c.present.folder)", '<present-folder>'))
    $pairs.Add(@("$($c.new.folder)", '<new-folder>'))
    foreach ($key in 'present', 'new') { $pairs.Add(@("$($c.$key.account)", "<$key>")) }
    , $pairs.ToArray()
}

function ConvertTo-TnwksMasked([string]$Text, $Mask) {
    foreach ($pair in $Mask) {
        if ($pair[0]) { $Text = [regex]::Replace($Text, [regex]::Escape($pair[0]), $pair[1], 'IgnoreCase') }
    }
    $Text -replace 'S-1-5-21(-[0-9]+)+', '<sid>'
}

# One line of at most $Limit characters, as the window's fail lines.
function Format-TnwksLine([string]$Text, [int]$Limit) {
    $one = ([regex]::Replace("$Text", '[\x00-\x1F\x7F\u0085  ]+', ' ')).Trim()
    if ($one.Length -gt $Limit) { $one = $one.Substring(0, $Limit) + ' [cut]' }
    $one
}

function Get-TnwksTaskStatus([string]$Name) {
    $t = Get-TnwksRegisteredTask $Name
    if (-not $t) { return 'absent' }
    $i = Get-ScheduledTaskInfo -TaskName $Name
    $last = if ($i.LastRunTime -and $i.LastRunTime.Year -gt 1999) { $i.LastRunTime.ToUniversalTime().ToString('o') } else { 'never' }
    '{0}, enabled {1}, last run {2}, last result 0x{3:x}' -f $t.State, [bool]$t.Settings.Enabled, $last, [int64]$i.LastTaskResult
}

function Get-TnwksStatus {
    $mask = Get-TnwksMask
    $limit = [int]$c.line_limit
    $state = if (Test-Path -LiteralPath $c.state_file -PathType Leaf) { @(Get-Content -LiteralPath $c.state_file | Where-Object { $_.Trim() }) } else { $null }
    $log = if (Test-Path -LiteralPath $c.log_file -PathType Leaf) { @(Get-Content -LiteralPath $c.log_file) } else { $null }
    $request = if (Test-Path -LiteralPath $c.request_file -PathType Leaf) {
        try { ConvertTo-TnwksMasked (Get-Content -LiteralPath $c.request_file -Raw | ConvertFrom-Json | ConvertTo-Json -Compress) $mask } catch { 'not read' }
    } else { 'absent' }
    $boot = try { (Get-TnwksBootTime).ToString('o') } catch { 'not read' }
    [ordered]@{
        boot = $boot
        state_file = if ($null -eq $state) { 'absent' } else { "$($state.Count) marker(s)" }
        last_marker = if ($null -eq $state) { 'absent' } elseif ($state.Count) { "$(($state[-1] -split ' ', 2)[1])".Trim() } else { 'none' }
        markers = @($state | Where-Object { $_ } | ForEach-Object { Format-TnwksLine (ConvertTo-TnwksMasked $_ $mask) $limit })
        disabled_list = if (Test-Path -LiteralPath "$($c.state_file).disabled" -PathType Leaf) { 'present' } else { 'absent' }
        request = $request
        window_task = Get-TnwksTaskStatus $c.window_task
        boot_task = Get-TnwksTaskStatus $c.boot_task
        log = if ($null -eq $log) { 'absent' } else { "$($log.Count) line(s)" }
        log_tail = @($log | Where-Object { $_ } | Select-Object -Last ([int]$c.log_lines) | ForEach-Object { Format-TnwksLine (ConvertTo-TnwksMasked $_ $mask) $limit })
        # The stops, fails, results and restarts of the whole log, the last 20.
        log_ends = @($log | Where-Object { $_ -match '^\S+ \S+ (stop|fail: |result: |restart: |would restart)' } | Select-Object -Last 20 |
            ForEach-Object { Format-TnwksLine (ConvertTo-TnwksMasked $_ $mask) $limit })
    }
}

# --- Start ----------------------------------------------------------------------

function Start-TnwksWindow {
    $failed = @(Get-TnwksGuard | Where-Object { -not $_.pass } | ForEach-Object { $_.name })
    if ($failed.Count) { throw "A guard of the start fails: $($failed -join '; '). Nothing is changed." }
    $request = [ordered]@{ mode = "$($c.way)"; group_steps = $true; stop_after = 0; rehearsal_group_test = $false } | ConvertTo-Json -Compress
    $before = (Get-ScheduledTaskInfo -TaskName $c.window_task).LastRunTime
    [IO.File]::WriteAllText($c.request_file, $request, (New-Object Text.UTF8Encoding $false))
    Start-ScheduledTask -TaskName $c.window_task
    $state = "$((Get-ScheduledTask -TaskName $c.window_task).State)"
    $after = (Get-ScheduledTaskInfo -TaskName $c.window_task).LastRunTime
    [pscustomobject]@{ started = ($state -in @('Running', 'Queued') -or $after -ne $before); state = $state; request = $request }
}

if (-not $env:TNWKS_MOVE_WINDOW_LIBRARY) {
    switch ($Mode) {
        'status' { [pscustomobject](Get-TnwksStatus) }
        'guards' { , @(Get-TnwksGuard) }
        'readiness' { , @(Get-TnwksReadiness) }
        'check' { , @(Get-TnwksCheck) }
        'start' {
            if ($PSCmdlet.ShouldProcess($c.window_task, 'write the request and start the window')) { Start-TnwksWindow }
        }
    }
}
