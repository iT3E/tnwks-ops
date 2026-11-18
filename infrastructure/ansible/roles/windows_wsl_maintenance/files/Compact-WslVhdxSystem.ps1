# Compact-WslVhdxSystem.ps1
# The compaction of the disk file of the WSL distro once the move of the
# distro is done (Design Doc 0004, section 5.10): the disk step runs as
# SYSTEM, and the stop of WSL is a task of the account that owns the distro.
#
# Managed by tnwks-ops (Ansible role windows_wsl_maintenance). The task
# tnwks-vhdx-compact-system runs it on demand
# (Start-ScheduledTask tnwks-vhdx-compact-system). It stops WSL and the
# cluster.
#
# The order:
#   1. The read of the owner and the rules of the disk file, the check
#      wsl-disk-rules of the host check (Measure-TnwksWslDisk). A fail stops
#      the run before it pauses a task: nothing is changed.
#   2. Pause the tasks that start WSL (-Paused): stop and disable each one
#      that is enabled.
#   3. Start the stop task of the account (-StopTask, wsl --shutdown and
#      nothing else), wait for its end, then until the Host Compute Service
#      lists no compute system.
#   4. The read again. A fail starts the tasks again and stops the run.
#   5. diskpart: attach the file read-only, compact, detach.
#   6. Enable the paused tasks again and start each one.
#
# It enables no privilege, sets no owner and no rules, and opens the disk
# file only through diskpart, after the read passed. The read is the host
# check's own: the script dot-sources the copy of Invoke-TnwksHostCheck.ps1
# in the folder of the host check, only when its SHA-256 is -HostCheckSha256.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Vhdx,
    [Parameter(Mandatory)][string]$Account,
    [Parameter(Mandatory)][string]$CapabilitySid,
    [Parameter(Mandatory)][string]$HostCheck,
    [Parameter(Mandatory)][string]$HostCheckSha256,
    [Parameter(Mandatory)][string]$StopTask,
    [string[]]$Paused = @(),
    [string]$LogDir = 'C:\ProgramData\tnwks-vhdx-compact'
)
$ErrorActionPreference = 'Stop'
$LogFile = Join-Path $LogDir 'compact-system.log'
# powershell.exe -File passes a list as one string: a,b,c.
$Paused = @($Paused | ForEach-Object { "$_" -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

function Write-Log([string]$Message) {
    $line = ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

# The compute systems that run on the host, as window step 7 reads them
# (roles/windows_wsl_move_rehearsal/files/Invoke-TnwksMoveWindow.ps1): the
# same code, word for word (security/tests/test_wsl_move_compaction.py).
# --- BEGIN compute systems (word for word from Invoke-TnwksMoveWindow.ps1)
$TnwksHcsSource = @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class TnwksHcs {
    [DllImport("computecore.dll", ExactSpelling = true)]
    static extern IntPtr HcsCreateOperation(IntPtr context, IntPtr callback);
    [DllImport("computecore.dll", ExactSpelling = true, CharSet = CharSet.Unicode)]
    static extern int HcsEnumerateComputeSystems(string query, IntPtr operation);
    [DllImport("computecore.dll", ExactSpelling = true)]
    static extern int HcsWaitForOperationResult(IntPtr operation, uint timeoutMs, out IntPtr resultDocument);
    [DllImport("computecore.dll", ExactSpelling = true)]
    static extern void HcsCloseOperation(IntPtr operation);
    [DllImport("kernel32.dll")]
    static extern IntPtr LocalFree(IntPtr h);
    public static string Enumerate() {
        IntPtr op = HcsCreateOperation(IntPtr.Zero, IntPtr.Zero);
        if (op == IntPtr.Zero) throw new InvalidOperationException("HcsCreateOperation gave no operation");
        try {
            int hr = HcsEnumerateComputeSystems(null, op);
            if (hr != 0) throw new Win32Exception(hr, "HcsEnumerateComputeSystems: 0x" + hr.ToString("x8"));
            IntPtr doc;
            hr = HcsWaitForOperationResult(op, 60000, out doc);
            try {
                string text = doc == IntPtr.Zero ? null : Marshal.PtrToStringUni(doc);
                if (hr != 0) throw new Win32Exception(hr, "HcsWaitForOperationResult: 0x" + hr.ToString("x8") + " " + text);
                return text;
            } finally { if (doc != IntPtr.Zero) LocalFree(doc); }
        } finally { HcsCloseOperation(op); }
    }
}
'@

function Get-TnwksComputeSystem {
    if (-not ('TnwksHcs' -as [type])) { Add-Type -TypeDefinition $TnwksHcsSource }
    $text = [TnwksHcs]::Enumerate()
    if ("$text".Trim() -notmatch '^\[') { throw 'the Host Compute Service gave no list of its compute systems' }
    # Into a variable first: Windows PowerShell 5.1 writes a JSON array to
    # the pipeline as one object; from a variable, the pipeline takes each.
    $systems = ConvertFrom-Json $text
    @($systems | Where-Object { $null -ne $_ })
}
# --- END compute systems

function Test-TnwksDiskRead([string]$When) {
    $broken = @(Measure-TnwksWslDisk -Path $Vhdx -Account $Account -CapabilitySid $CapabilitySid)
    if ($broken.Count) {
        Write-Log "the read of the disk file $When failed: $($broken -join ', ')"
        return $false
    }
    Write-Log "the read of the disk file $When passed"
    $true
}

function Wait-TnwksTaskEnd([string]$Task, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        Start-Sleep -Seconds 2
        $state = (Get-ScheduledTask -TaskName $Task).State
    } while ($state -eq 'Running' -and (Get-Date) -lt $deadline)
    if ($state -eq 'Running') { throw "$Task still runs after $Seconds seconds" }
    $result = (Get-ScheduledTaskInfo -TaskName $Task).LastTaskResult
    if ($result -ne 0) { throw "$Task ended with $result" }
}

function Wait-TnwksNoComputeSystem([int]$Reads) {
    for ($read = 1; $read -le $Reads; $read++) {
        $systems = @(Get-TnwksComputeSystem)
        if ($systems.Count -eq 0) { return }
        Start-Sleep -Seconds 2
    }
    throw "$($systems.Count) compute system(s) still run after $Reads reads"
}

function Invoke-TnwksDiskpart([string[]]$Lines, [string]$Name) {
    $file = Join-Path $LogDir $Name
    $Lines | Set-Content -LiteralPath $file -Encoding ASCII
    $out = & diskpart.exe /s $file 2>&1
    $out | ForEach-Object { if ("$_".Trim()) { Write-Log "diskpart: $_" } }
    if ($LASTEXITCODE -ne 0) { throw "diskpart ended with $LASTEXITCODE" }
}

function Invoke-TnwksCompaction {
    if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    Write-Log "start: $Vhdx"
    $hash = (Get-FileHash -LiteralPath $HostCheck -Algorithm SHA256).Hash
    if ($hash -ne $HostCheckSha256) {
        Write-Log "stop: the host check script is not the one of the play. Nothing is changed."
        return 'stopped'
    }
    . $HostCheck -Library
    # (1) Before any pause.
    if (-not (Test-TnwksDiskRead 'before the pause')) {
        Write-Log 'stop: nothing is changed'
        return 'stopped'
    }
    $pausedNow = New-Object System.Collections.Generic.List[string]
    $outcome = 'stopped'
    try {
        # (2)
        foreach ($t in $Paused) {
            $task = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
            if (-not $task -or -not $task.Settings.Enabled) { continue }
            Disable-ScheduledTask -TaskName $t | Out-Null
            Stop-ScheduledTask -TaskName $t
            $pausedNow.Add($t)
        }
        Write-Log "paused: $($pausedNow -join ', ')"
        # (3)
        Start-ScheduledTask -TaskName $StopTask
        Wait-TnwksTaskEnd $StopTask 180
        Wait-TnwksNoComputeSystem 30
        Write-Log 'no compute system runs'
        # (4) Before diskpart.
        if (-not (Test-TnwksDiskRead 'before diskpart')) { return 'stopped' }
        # (5)
        try {
            Invoke-TnwksDiskpart @("select vdisk file=`"$Vhdx`"", 'attach vdisk readonly', 'compact vdisk', 'detach vdisk') 'compact.diskpart.txt'
            $outcome = 'done'
        } catch {
            Write-Log "ERROR: $_"
            # The file stays attached otherwise, and WSL can not open it.
            try { Invoke-TnwksDiskpart @("select vdisk file=`"$Vhdx`"", 'detach vdisk') 'detach.diskpart.txt' } catch { Write-Log "ERROR: $_" }
        }
    } catch {
        Write-Log "ERROR: $_"
    } finally {
        # (6)
        foreach ($t in $pausedNow) { Enable-ScheduledTask -TaskName $t | Out-Null }
        foreach ($t in $pausedNow) { Start-ScheduledTask -TaskName $t }
        Write-Log "enabled again: $($pausedNow -join ', '); $outcome"
    }
    $outcome
}

if (-not $env:TNWKS_COMPACT_LIBRARY) { Invoke-TnwksCompaction }
