<#
.SYNOPSIS
The host check of the security platform on a Windows host: the state of
Defender, the updates and the firewall, as a .prom file for the textfile
collector of windows_exporter.

.DESCRIPTION
Managed by tnwks-ops (role windows_host_check). The scheduled task
tnwks-host-check runs this script as SYSTEM every 6 hours. It reads, and it
changes no setting of the host.

It writes the two series of the security registry (security/README.md, "The
series") for the source host-check, and a few series of its own that say
what it read. Each check gives the number of items that fail it, and 0 is a
pass:

  defender-enabled     the Defender service, the antivirus and real-time
                       protection: one for each that is off
  defender-signatures  1 when the newest signatures are older than
                       -SignatureMaxAgeDays
  firewall-enabled     the Domain, Private and Public profiles of the active
                       store: one for each that is off
  updates-pending      each software update that Windows Update offers and
                       that is not installed, released more than
                       -UpdatesMaxAgeDays ago
  updates-held         each hidden software update that is not installed,
                       released more than -UpdatesMaxAgeDays ago (Design Doc
                       0004, section 5.7): a hidden update never shows in
                       updates-pending

When one query fails, the file has no result and no time of the last result,
so the rules of the registry read the source as late for this host
(SecurityResultLate), and never as a pass. The file then says how many
queries failed. The file goes to its place in one step, so windows_exporter
never reads half a file.

With -Relay, the script runs in its second mode, the relay of the WSL
distro of the host (Design Doc 0004, section 5.9). The scheduled task
tnwks-host-check-relay runs it as SYSTEM each hour. The check of the distro
writes a JSON document into the inbox -InboxDir, which only SYSTEM, the
Administrators and the account of the distro may change, and the distro may
not write the textfile directory. The relay reads the document and writes
the .prom file of the distro (-DistroFileName), with -System as the name of
the distro and the time of the distro. It takes the document only when it
fits: at most 64 KiB, not a link, the name -System, the checks
updates-pending and updates-held only, each a whole number of 0 or more, a
time at most 300 seconds ahead, and the detail series of a distro only,
each name and labels once. The inbox and the document must be what their
names say: the relay opens the document once, and refuses it when the open
file is not the one at its path (a link or a junction on the way), or when
the file has more than one name (a hard link). A
document that does not fit, or no document, is the failed query relay: no
result and no time, so the distro reads as late. The relay logs each run to
-LogPath, in the directory of this script, which only SYSTEM and the
Administrators may change. A reason names the rule that the document broke,
never a value of the document.

Tests: security/tests/test_host_check_windows.py dot-sources this file with
-Library and gives each query a fake answer.
#>
[CmdletBinding()]
param(
    # The name of this host in security/registry/systems.yaml.
    [string]$System = '',
    [string]$TextfileDir = 'C:\Program Files\windows_exporter\textfile_inputs',
    [string]$FileName = 'tnwks_security_host_check.prom',
    [int]$SignatureMaxAgeDays = 7,
    [int]$UpdatesMaxAgeDays = 14,
    # The relay of the document of the WSL distro; -System is then the name of
    # the distro.
    [switch]$Relay,
    [string]$InboxDir = 'C:\ProgramData\tnwks-host-check-distro',
    [string]$InboxFile = 'tnwks_security_host_check_distro.json',
    [string]$DistroFileName = 'tnwks_security_host_check_distro.prom',
    # The log of the relay; none when empty.
    [string]$LogPath = '',
    # Define the functions only, for the tests.
    [switch]$Library
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'

# The text of each series, also of the series that the relay writes for the
# WSL distro: one text for each series, as the exporter serves no series with
# two texts.
$script:Help = [ordered]@{
    homelab_security_check_result                         = 'The number of items that fail one check of the security registry. 0 is a pass.'
    homelab_security_last_result_timestamp_seconds        = 'The Unix time of the newest result of a source of the security registry.'
    homelab_security_host_check_failed_queries            = 'The number of queries of the host check that failed in its last run. Above 0, the run gives no result.'
    homelab_security_host_defender_enabled                = 'Whether one protection of Defender is on: 1 on, 0 off.'
    homelab_security_host_defender_signature_timestamp_seconds = 'The Unix time of the newest signatures of Defender.'
    homelab_security_host_defender_last_scan_timestamp_seconds = 'The Unix time at which the newest quick or full scan of Defender ended.'
    homelab_security_host_firewall_enabled                = 'Whether one profile of the firewall is on in the active store: 1 on, 0 off.'
    homelab_security_host_updates_pending                 = 'The number of updates that the host can install and has not installed.'
    homelab_security_host_updates_held                    = 'The number of updates that the host holds back or hides, and has not installed.'
    homelab_security_host_updates_last_install_timestamp_seconds = 'The Unix time of the newest update that the host installed.'
    homelab_security_host_reboot_pending                  = 'Whether the host waits for a restart to finish an update: 1 yes, 0 no.'
    homelab_security_host_package_lists_timestamp_seconds = 'The Unix time of the oldest package list of the updates and the security fixes of the host, from the Date of its Release file.'
    homelab_security_host_write_test_result               = 'The write test of a WSL distro to the textfile directory of its host, by the uid that tried: 0 refused by Windows, 1 the write succeeded, 2 refused by Linux before Windows, 3 not done.'
    homelab_security_host_write_test_errno                = 'The errno of the write test of a WSL distro, by the uid that tried, or 0 when the try had none.'
    homelab_security_host_write_test_files                = 'The number of files of the write test of a WSL distro in the textfile directory of the host. A try removes its own file, so this is 0.'
}

$script:Source = 'host-check'

# The severity of each check, in the label severity of its result. The
# alert for a new finding (SecurityFindingNew) reads high and critical only.
# Design Doc 0004, section 4.1.4, alerts for Defender off, the firewall off
# and updates pending for more than 14 days, so those are high. Section 5.8
# makes old signatures high too, so a fail reaches the owner in one cycle.
$script:Severity = [ordered]@{
    'defender-enabled'    = 'high'
    'defender-signatures' = 'high'
    'firewall-enabled'    = 'high'
    'updates-pending'     = 'high'
    'updates-held'        = 'high'
}
$script:Profiles = @('Domain', 'Private', 'Public')

# What the relay takes from the document of a distro.
$script:RelayMaxBytes = 65536
# The allowance for a clock that runs ahead, as the generated rules use.
$script:RelayMaxAheadSeconds = 300
$script:RelayChecks = @('updates-pending', 'updates-held')
$script:RelayFields = @('version', 'system', 'time', 'results', 'failed', 'details')
# The detail series of a distro, each with the names of its labels. Ordinal:
# a name in other letter case is not a series of a distro.
$script:RelayDetails = New-Object 'System.Collections.Generic.Dictionary[string,string[]]' ([StringComparer]::Ordinal)
$script:RelayDetails['homelab_security_host_package_lists_timestamp_seconds'] = [string[]]@()
$script:RelayDetails['homelab_security_host_updates_pending'] = [string[]]@()
$script:RelayDetails['homelab_security_host_updates_held'] = [string[]]@()
$script:RelayDetails['homelab_security_host_reboot_pending'] = [string[]]@()
$script:RelayDetails['homelab_security_host_write_test_result'] = [string[]]@('uid')
$script:RelayDetails['homelab_security_host_write_test_errno'] = [string[]]@('uid')
# The log of the relay rolls over at this size, to one old file.
$script:RelayLogMaxBytes = 1048576

# --- The queries. Each one reads, and the tests replace it. -----------------

function Get-TnwksDefenderStatus {
    Get-MpComputerStatus
}

function Get-TnwksFirewallProfiles {
    # The active store: the settings in force, from the local policy and
    # from a group policy.
    Get-NetFirewallProfile -PolicyStore ActiveStore
}

function Get-TnwksUpdateSession {
    New-Object -ComObject Microsoft.Update.Session
}

function Search-TnwksUpdates([string]$Criteria) {
    # The search asks Windows Update itself, so a stale local cache can not
    # give an empty list.
    $searcher = (Get-TnwksUpdateSession).CreateUpdateSearcher()
    $searcher.Online = $true
    $found = $searcher.Search($Criteria)
    # 2 is orcSucceeded. Any other code is a search that did not finish.
    if ($found.ResultCode -ne 2) { throw "the search of Windows Update ended with code $($found.ResultCode)" }
    # One release time for each update, on the output.
    foreach ($update in $found.Updates) { [datetime]$update.LastDeploymentChangeTime }
}

function Get-TnwksPendingUpdates {
    # Software updates that Windows Update offers and that are not installed.
    # Not hidden, and not optional (BrowseOnly), as Windows would install
    # them.
    Search-TnwksUpdates "IsInstalled=0 and IsHidden=0 and BrowseOnly=0 and Type='Software'"
}

function Get-TnwksHeldUpdates {
    # Software updates that are hidden and not installed: Windows does not
    # install them, and the search of updates-pending does not list them. Not
    # optional (BrowseOnly), as in that search: the two count the same kind of
    # update, and differ only in IsHidden.
    Search-TnwksUpdates "IsInstalled=0 and IsHidden=1 and BrowseOnly=0 and Type='Software'"
}

function Get-TnwksLastInstall {
    # The newest update that the host installed with success, from the
    # history of Windows Update. The signatures of Defender are left out:
    # they come each day and say nothing of the updates of Windows.
    $searcher = (Get-TnwksUpdateSession).CreateUpdateSearcher()
    $count = [math]::Min($searcher.GetTotalHistoryCount(), 1000)
    if ($count -le 0) { return $null }
    $newest = $null
    foreach ($entry in $searcher.QueryHistory(0, $count)) {
        # Operation 1 is an installation, and result code 2 a success.
        if ($entry.Operation -ne 1 -or $entry.ResultCode -ne 2) { continue }
        try { $categories = @($entry.Categories | ForEach-Object { $_.Name }) } catch { $categories = @() }
        if ($categories -contains 'Definition Updates') { continue }
        $date = [datetime]$entry.Date
        if ($null -eq $newest -or $date -gt $newest) { $newest = $date }
    }
    $newest
}

function Get-TnwksRebootPending {
    [bool](New-Object -ComObject Microsoft.Update.SystemInfo).RebootRequired
}

# --- The checks --------------------------------------------------------------

function ConvertTo-TnwksUnixTime([datetime]$Moment) {
    [DateTimeOffset]::new($Moment.ToUniversalTime()).ToUnixTimeSeconds()
}

function Get-TnwksWriteTestFiles([string]$Directory) {
    # The files that the write test of a distro makes: role wsl_host_check,
    # write_test. -Force also lists a hidden file.
    @(Get-ChildItem -LiteralPath $Directory -Force -File -Filter '.tnwks-write-test-*.tmp' -ErrorAction Stop).Count
}

function Measure-TnwksHostCheck {
    <#
    .SYNOPSIS
    Runs each query, and gives the result of each check, the series of the
    host check itself, and the names of the queries that failed.
    #>
    param(
        [datetime]$Now,
        [int]$SignatureMaxAgeDays,
        [int]$UpdatesMaxAgeDays,
        # The textfile directory, for the count of files of the write test.
        [string]$TextfileDir
    )
    $results = [ordered]@{}
    $details = New-Object System.Collections.Generic.List[object]
    $failed = New-Object System.Collections.Generic.List[string]

    try {
        $status = Get-TnwksDefenderStatus
        $on = [ordered]@{
            service   = ($status.AMServiceEnabled -eq $true)
            antivirus = ($status.AntivirusEnabled -eq $true)
            real_time = ($status.RealTimeProtectionEnabled -eq $true)
        }
        $signatures = $status.AntivirusSignatureLastUpdated
        if ($null -eq $signatures) { throw 'Defender gives no time for its signatures' }
        $signatures = [datetime]$signatures
        $scans = @(@($status.QuickScanEndTime, $status.FullScanEndTime) | Where-Object { $null -ne $_ } | ForEach-Object { [datetime]$_ })
        $results['defender-enabled'] = @($on.Values | Where-Object { -not $_ }).Count
        $results['defender-signatures'] = [int](($Now - $signatures).TotalDays -gt $SignatureMaxAgeDays)
        foreach ($name in $on.Keys) {
            $details.Add(@('homelab_security_host_defender_enabled', [ordered]@{ protection = $name }, [int]$on[$name]))
        }
        $details.Add(@('homelab_security_host_defender_signature_timestamp_seconds', [ordered]@{}, (ConvertTo-TnwksUnixTime $signatures)))
        if ($scans.Count -gt 0) {
            $newestScan = @($scans | Sort-Object -Descending)[0]
            $details.Add(@('homelab_security_host_defender_last_scan_timestamp_seconds', [ordered]@{}, (ConvertTo-TnwksUnixTime $newestScan)))
        }
    } catch {
        $failed.Add('defender')
    }

    try {
        $byName = @{}
        foreach ($item in @(Get-TnwksFirewallProfiles)) { $byName[[string]$item.Name] = ([string]$item.Enabled -eq 'True') }
        foreach ($name in $script:Profiles) {
            if (-not $byName.ContainsKey($name)) { throw "the active store has no profile $name" }
        }
        $results['firewall-enabled'] = @($script:Profiles | Where-Object { -not $byName[$_] }).Count
        foreach ($name in $script:Profiles) {
            $details.Add(@('homelab_security_host_firewall_enabled', [ordered]@{ profile = $name.ToLowerInvariant() }, [int]$byName[$name]))
        }
    } catch {
        $failed.Add('firewall')
    }

    try {
        $released = @(Get-TnwksPendingUpdates)
        $results['updates-pending'] = @($released | Where-Object { ($Now - $_).TotalDays -gt $UpdatesMaxAgeDays }).Count
        $details.Add(@('homelab_security_host_updates_pending', [ordered]@{}, $released.Count))
    } catch {
        $failed.Add('updates')
    }

    try {
        $hidden = @(Get-TnwksHeldUpdates)
        $results['updates-held'] = @($hidden | Where-Object { ($Now - $_).TotalDays -gt $UpdatesMaxAgeDays }).Count
        $details.Add(@('homelab_security_host_updates_held', [ordered]@{}, $hidden.Count))
    } catch {
        $failed.Add('updates-held')
    }

    # Facts of the updates that no check reads: a fault here leaves out the
    # series of the fact, and costs no result.
    try {
        $last = Get-TnwksLastInstall
        if ($null -ne $last) {
            $details.Add(@('homelab_security_host_updates_last_install_timestamp_seconds', [ordered]@{}, (ConvertTo-TnwksUnixTime $last)))
        }
    } catch {
        Write-Verbose 'the history of Windows Update can not be read'
    }
    try {
        $details.Add(@('homelab_security_host_reboot_pending', [ordered]@{}, [int](Get-TnwksRebootPending)))
    } catch {
        Write-Verbose 'Windows Update does not say whether a restart is pending'
    }
    if ($TextfileDir) {
        try {
            $details.Add(@('homelab_security_host_write_test_files', [ordered]@{}, (Get-TnwksWriteTestFiles $TextfileDir)))
        } catch {
            Write-Verbose 'the textfile directory can not be listed'
        }
    }

    [pscustomobject]@{ Results = $results; Details = $details; Failed = $failed }
}

# --- The file ----------------------------------------------------------------

function Format-TnwksLabelValue([string]$Value) {
    # The escapes of the text format of Prometheus.
    $Value.Replace('\', '\\').Replace('"', '\"').Replace("`n", '\n')
}

function Format-TnwksSeries([string]$Name, $Labels, $Value) {
    $pairs = foreach ($key in $Labels.Keys) { '{0}="{1}"' -f $key, (Format-TnwksLabelValue ([string]$Labels[$key])) }
    '{0}{{{1}}} {2}' -f $Name, ($pairs -join ','), ([string]$Value)
}

function Format-TnwksHostCheck {
    <#
    .SYNOPSIS
    The text of the .prom file: each family once, with its HELP and TYPE.
    #>
    param([string]$System, $Measure, [datetime]$Now)
    # Ordinal: each family once, by its exact name.
    $families = New-Object System.Collections.Specialized.OrderedDictionary ([StringComparer]::Ordinal)
    function Add-Line([string]$Name, [string]$Line) {
        if (-not $families.Contains($Name)) { $families[$Name] = New-Object System.Collections.Generic.List[string] }
        $families[$Name].Add($Line)
    }
    if ($Measure.Failed.Count -eq 0) {
        foreach ($check in $Measure.Results.Keys) {
            Add-Line 'homelab_security_check_result' (Format-TnwksSeries 'homelab_security_check_result' ([ordered]@{ source = $script:Source; check = $check; system = $System; severity = $script:Severity[$check] }) $Measure.Results[$check])
        }
        Add-Line 'homelab_security_last_result_timestamp_seconds' (Format-TnwksSeries 'homelab_security_last_result_timestamp_seconds' ([ordered]@{ source = $script:Source; system = $System }) (ConvertTo-TnwksUnixTime $Now))
    }
    Add-Line 'homelab_security_host_check_failed_queries' (Format-TnwksSeries 'homelab_security_host_check_failed_queries' ([ordered]@{ system = $System }) $Measure.Failed.Count)
    foreach ($detail in $Measure.Details) {
        $labels = [ordered]@{ system = $System }
        foreach ($key in $detail[1].Keys) { $labels[$key] = $detail[1][$key] }
        Add-Line $detail[0] (Format-TnwksSeries $detail[0] $labels $detail[2])
    }
    $text = New-Object System.Text.StringBuilder
    foreach ($name in $families.Keys) {
        [void]$text.Append("# HELP $name $($script:Help[$name])`n")
        [void]$text.Append("# TYPE $name gauge`n")
        foreach ($line in $families[$name]) { [void]$text.Append("$line`n") }
    }
    $text.ToString()
}

function Write-TnwksTextfile([string]$Directory, [string]$Name, [string]$Text) {
    <#
    .SYNOPSIS
    Writes the file next to its place, then puts it in place in one step.
    The exporter reads only names that end in .prom, so it never reads the
    file before it is whole. UTF-8 with no byte order mark, and LF.
    #>
    $final = Join-Path $Directory $Name
    $partial = "$final.partial"
    [IO.File]::WriteAllText($partial, $Text, (New-Object Text.UTF8Encoding $false))
    if (Test-Path -LiteralPath $final) {
        # PowerShell passes $null to a .NET string as an empty string, which
        # Replace refuses. [NullString]::Value is a real null: no backup file.
        [IO.File]::Replace($partial, $final, [NullString]::Value)
    } else {
        [IO.File]::Move($partial, $final)
    }
}

# --- The relay of the WSL distro --------------------------------------------

function Test-TnwksWhole($Value) {
    ($Value -is [int]) -or ($Value -is [long])
}

function Test-TnwksNumber($Value) {
    if ($Value -is [bool]) { return $false }
    if (Test-TnwksWhole $Value) { return $true }
    (($Value -is [double]) -or ($Value -is [decimal])) -and -not [double]::IsNaN([double]$Value) -and -not [double]::IsInfinity([double]$Value)
}

function Get-TnwksNames($Object) {
    if ($Object -isnot [System.Management.Automation.PSCustomObject]) { Stop-TnwksRelay 'a part of the document is not an object' }
    @($Object.PSObject.Properties | ForEach-Object { $_.Name })
}

function Stop-TnwksRelay([string]$Rule) {
    # The reason of a refusal: the rule that the document broke, never a value
    # of the document.
    throw (New-Object System.IO.InvalidDataException $Rule)
}

$script:FinalPathCode = @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
public static class TnwksFinalPath {
    [StructLayout(LayoutKind.Sequential)]
    struct FileInformation {
        public uint Attributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME Creation, LastAccess, LastWrite;
        public uint VolumeSerialNumber, SizeHigh, SizeLow, NumberOfLinks, IndexHigh, IndexLow;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern uint GetFinalPathNameByHandleW(SafeFileHandle file, StringBuilder path, uint length, uint flags);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInformation information);
    public static uint Links(SafeFileHandle file) {
        FileInformation information;
        if (!GetFileInformationByHandle(file, out information)) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
        return information.NumberOfLinks;
    }
    public static string Of(SafeFileHandle file) {
        StringBuilder path = new StringBuilder(1024);
        uint length = GetFinalPathNameByHandleW(file, path, (uint)path.Capacity, 0);
        if (length == 0 || length >= path.Capacity) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
        return path.ToString();
    }
}
'@

function Get-TnwksOpenPath([IO.FileStream]$Stream) {
    <#
    .SYNOPSIS
    The path of the file that a stream has open, after each link and junction.
    #>
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        if (-not ('TnwksFinalPath' -as [type])) { Add-Type -TypeDefinition $script:FinalPathCode }
        $path = [TnwksFinalPath]::Of($Stream.SafeFileHandle)
        if ($path.StartsWith('\\?\UNC\')) { return '\\' + $path.Substring(8) }
        if ($path.StartsWith('\\?\')) { return $path.Substring(4) }
        return $path
    }
    # Not Windows (the tests in CI): the link of the open file descriptor.
    [IO.File]::ResolveLinkTarget("/proc/self/fd/$($Stream.SafeFileHandle.DangerousGetHandle())", $false).FullName
}

function Get-TnwksOpenLinks([IO.FileStream]$Stream) {
    <#
    .SYNOPSIS
    How many names the file that a stream has open has: more than one is a hard
    link, which the check of the final path does not see.
    #>
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        if (-not ('TnwksFinalPath' -as [type])) { Add-Type -TypeDefinition $script:FinalPathCode }
        return [int][TnwksFinalPath]::Links($Stream.SafeFileHandle)
    }
    # Not Windows (the tests in CI): the link count of the open file descriptor.
    [int](& stat -L -c '%h' "/proc/$PID/fd/$($Stream.SafeFileHandle.DangerousGetHandle())")
}

function Open-TnwksPlainFile([string]$Path) {
    <#
    .SYNOPSIS
    Opens a file to read, once, and refuses it when the open file is not the
    one at its path: a link or a junction at the file or at a directory above
    it, or a file with a second name (a hard link). No FileShare.Delete: while
    the stream is open, the file at the path can not be renamed, replaced or
    deleted.
    #>
    $full = [IO.Path]::GetFullPath($Path)
    $stream = [IO.File]::Open($full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
    try {
        if (-not [string]::Equals((Get-TnwksOpenPath $stream), $full, [StringComparison]::OrdinalIgnoreCase)) {
            Stop-TnwksRelay 'the document or its directory is a link'
        }
        if ((Get-TnwksOpenLinks $stream) -gt 1) { Stop-TnwksRelay 'the document has more than one name (a hard link)' }
    } catch {
        $stream.Dispose()
        throw
    }
    $stream
}

function Read-TnwksDistroText([string]$InboxDir, [string]$InboxFile) {
    <#
    .SYNOPSIS
    The text of the document of the distro: at most -RelayMaxBytes, UTF-8.
    #>
    $inbox = Get-Item -LiteralPath $InboxDir -Force
    if (-not $inbox.PSIsContainer -or ($inbox.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Stop-TnwksRelay 'the inbox is not a plain directory'
    }
    $item = Get-Item -LiteralPath (Join-Path $InboxDir $InboxFile) -Force
    if ($item.PSIsContainer) { Stop-TnwksRelay 'the document is a directory' }
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { Stop-TnwksRelay 'the document is a link' }
    # Read at most one byte past the limit, so a file that grows after the
    # check of its length is still refused.
    $buffer = New-Object byte[] ($script:RelayMaxBytes + 1)
    $count = 0
    $stream = Open-TnwksPlainFile $item.FullName
    try {
        while ($count -lt $buffer.Length) {
            $read = $stream.Read($buffer, $count, $buffer.Length - $count)
            if ($read -le 0) { break }
            $count += $read
        }
    } finally {
        $stream.Dispose()
    }
    if ($count -gt $script:RelayMaxBytes) { Stop-TnwksRelay "the document is larger than $($script:RelayMaxBytes) bytes" }
    try {
        # UTF-8 that is not valid throws.
        (New-Object Text.UTF8Encoding($false, $true)).GetString($buffer, 0, $count)
    } catch {
        Stop-TnwksRelay 'the document is not UTF-8'
    }
}

function Test-TnwksDistroDocument {
    <#
    .SYNOPSIS
    Checks the document of the distro, as ConvertFrom-Json gives it, and gives
    its measure and its time. A document that does not fit throws, with the
    rule that it broke.
    .DESCRIPTION
    Windows PowerShell 5.1 runs it on the host and PowerShell 7 in the tests.
    Their ConvertFrom-Json differ: 5.1 gives a whole number as Int32 or Int64
    and a fraction as Decimal; 7 gives Int64 (BigInteger past it) and Double,
    and makes a date of a string in the form of a date. Each check here takes
    the types of both, and a date in place of a string is refused.
    #>
    param($Document, [string]$System, [datetime]$Now)
    $names = Get-TnwksNames $Document
    foreach ($name in $names) {
        if ($script:RelayFields -cnotcontains $name) { Stop-TnwksRelay 'the document has a field that is not of a document' }
    }
    foreach ($name in $script:RelayFields) {
        if ($names -cnotcontains $name) { Stop-TnwksRelay 'the document misses a field' }
    }
    if (-not (Test-TnwksWhole $Document.version) -or $Document.version -ne 1) { Stop-TnwksRelay 'the document is not version 1' }
    if ($Document.system -isnot [string] -or $Document.system -cne $System) { Stop-TnwksRelay 'the document names another system' }
    if (-not (Test-TnwksNumber $Document.time) -or $Document.time -le 0) { Stop-TnwksRelay 'the document has no time' }
    $limit = (ConvertTo-TnwksUnixTime $Now) + $script:RelayMaxAheadSeconds
    if ([double]$Document.time -gt $limit) { Stop-TnwksRelay 'the time of the document is more than 300 seconds ahead' }

    $failed = New-Object System.Collections.Generic.List[string]
    $queries = @($Document.failed)
    if ($queries.Count -gt 10) { Stop-TnwksRelay 'the document names more than 10 failed queries' }
    foreach ($query in $queries) {
        if ($query -isnot [string] -or $query -cnotmatch '\A[a-z][a-z-]{0,39}\z') { Stop-TnwksRelay 'a failed query has no name' }
        $failed.Add($query)
    }

    $results = [ordered]@{}
    foreach ($check in (Get-TnwksNames $Document.results)) {
        if ($script:RelayChecks -cnotcontains $check) { Stop-TnwksRelay 'the document has a check that is not of a distro' }
        $value = $Document.results.$check
        if (-not (Test-TnwksWhole $value) -or $value -lt 0) { Stop-TnwksRelay 'a result is not a whole number of 0 or more' }
        $results[$check] = [long]$value
    }
    if ($failed.Count -eq 0 -and $results.Count -ne $script:RelayChecks.Count) { Stop-TnwksRelay 'the document has no failed query and not each result' }
    if ($failed.Count -gt 0 -and $results.Count -gt 0) { Stop-TnwksRelay 'the document has a failed query and a result' }

    $details = New-Object System.Collections.Generic.List[object]
    # Each name and labels once: windows_exporter serves no series of a file
    # with a repeated series, and none at all for a repeat across its files.
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    $rows = @($Document.details)
    if ($rows.Count -gt 20) { Stop-TnwksRelay 'the document has more than 20 details' }
    foreach ($row in $rows) {
        $fields = Get-TnwksNames $row
        if ((($fields | Sort-Object) -join ',') -cne 'labels,name,value') { Stop-TnwksRelay 'a detail is not a name, labels and a value' }
        if ($row.name -isnot [string] -or -not $script:RelayDetails.ContainsKey($row.name)) { Stop-TnwksRelay 'a detail is not a series of a distro' }
        $want = $script:RelayDetails[$row.name]
        $have = @(Get-TnwksNames $row.labels)
        if ((($have | Sort-Object) -join ',') -cne (($want | Sort-Object) -join ',')) { Stop-TnwksRelay 'a detail has other labels than its series' }
        $labels = [ordered]@{}
        $key = $row.name
        foreach ($label in $want) {
            $text = $row.labels.$label
            if ($text -isnot [string] -or $text -cnotmatch '\A[0-9]{1,10}\z') { Stop-TnwksRelay 'a label of a detail is not a number' }
            $labels[$label] = $text
            $key += "|$label=$text"
        }
        if (-not $seen.Add($key)) { Stop-TnwksRelay 'a detail repeats a series' }
        if (-not (Test-TnwksNumber $row.value)) { Stop-TnwksRelay 'the value of a detail is not a number' }
        $details.Add(@($row.name, $labels, $row.value))
    }
    $time = [DateTimeOffset]::FromUnixTimeSeconds([long][math]::Floor([double]$Document.time)).UtcDateTime
    [pscustomobject]@{
        Measure = [pscustomobject]@{ Results = $results; Details = $details; Failed = $failed }
        Time    = $time
    }
}

function Read-TnwksDistroDocument {
    <#
    .SYNOPSIS
    Reads the document of the distro, and gives its measure and its time.
    #>
    param([string]$InboxDir, [string]$InboxFile, [string]$System, [datetime]$Now)
    $text = Read-TnwksDistroText -InboxDir $InboxDir -InboxFile $InboxFile
    try {
        $document = $text | ConvertFrom-Json
    } catch {
        # The error of ConvertFrom-Json can quote the text.
        Stop-TnwksRelay 'the document is not JSON'
    }
    Test-TnwksDistroDocument -Document $document -System $System -Now $Now
}

function Write-TnwksRelayLog([string]$Path, [string]$Line) {
    if (-not $Path) { return }
    if ((Test-Path -LiteralPath $Path) -and (Get-Item -LiteralPath $Path).Length -gt $script:RelayLogMaxBytes) {
        Move-Item -LiteralPath $Path -Destination "$Path.1" -Force
    }
    Add-Content -LiteralPath $Path -Value ('{0:yyyy-MM-ddTHH:mm:ssZ} {1}' -f (Get-Date).ToUniversalTime(), $Line) -Encoding ASCII
}

function Invoke-TnwksRelay {
    param([string]$System, [string]$InboxDir, [string]$InboxFile, [string]$TextfileDir, [string]$FileName, [datetime]$Now,
        [string]$LogPath = '')
    if (-not $System) { throw 'no -System: the name of the distro in security/registry/systems.yaml' }
    $lines = New-Object System.Collections.Generic.List[string]
    try {
        $read = Read-TnwksDistroDocument -InboxDir $InboxDir -InboxFile $InboxFile -System $System -Now $Now
        $measure = $read.Measure
        $time = $read.Time
    } catch {
        # No result and no time: the distro reads as late. The reason is a rule
        # of the relay, or only the kind of the error: an error of PowerShell
        # or .NET can quote what it read.
        if ($_.Exception -is [System.IO.InvalidDataException]) {
            $why = $_.Exception.Message
        } elseif ($_.Exception -is [System.Management.Automation.ItemNotFoundException]) {
            $why = 'no document'
        } else {
            $why = 'the document could not be read ({0})' -f $_.Exception.GetType().FullName
        }
        $lines.Add(('relay of {0}: the document does not fit: {1}' -f $System, $why))
        $failed = New-Object System.Collections.Generic.List[string]
        $failed.Add('relay')
        $measure = [pscustomobject]@{ Results = [ordered]@{}; Details = (New-Object System.Collections.Generic.List[object]); Failed = $failed }
        $time = $Now
    }
    Write-TnwksTextfile $TextfileDir $FileName (Format-TnwksHostCheck -System $System -Measure $measure -Now $time)
    if ($measure.Failed.Count -gt 0) {
        $lines.Add(('relay of {0}: no result, these queries failed: {1}' -f $System, ($measure.Failed -join ', ')))
    } else {
        $lines.Add(('relay of {0}: {1} checks' -f $System, $measure.Results.Count))
    }
    foreach ($line in $lines) {
        Write-Output $line
        Write-TnwksRelayLog $LogPath $line
    }
    if ($measure.Failed.Count -gt 0) { exit 1 }
}

function Invoke-TnwksHostCheck {
    param([string]$System, [string]$TextfileDir, [string]$FileName, [int]$SignatureMaxAgeDays, [int]$UpdatesMaxAgeDays)
    if (-not $System) { throw 'no -System: the name of this host in security/registry/systems.yaml' }
    $measure = Measure-TnwksHostCheck -Now (Get-Date) -SignatureMaxAgeDays $SignatureMaxAgeDays -UpdatesMaxAgeDays $UpdatesMaxAgeDays `
        -TextfileDir $TextfileDir
    $now = Get-Date
    Write-TnwksTextfile $TextfileDir $FileName (Format-TnwksHostCheck -System $System -Measure $measure -Now $now)
    # The names of the queries only: no value that the host gave.
    if ($measure.Failed.Count -gt 0) {
        Write-Output ('host check of {0}: no result, these queries failed: {1}' -f $System, ($measure.Failed -join ', '))
        exit 1
    }
    Write-Output ('host check of {0}: {1} checks' -f $System, $measure.Results.Count)
}

if (-not $Library) {
    if ($Relay) {
        Invoke-TnwksRelay -System $System -InboxDir $InboxDir -InboxFile $InboxFile -TextfileDir $TextfileDir `
            -FileName $DistroFileName -Now (Get-Date) -LogPath $LogPath
    } else {
        Invoke-TnwksHostCheck -System $System -TextfileDir $TextfileDir -FileName $FileName `
            -SignatureMaxAgeDays $SignatureMaxAgeDays -UpdatesMaxAgeDays $UpdatesMaxAgeDays
    }
}
