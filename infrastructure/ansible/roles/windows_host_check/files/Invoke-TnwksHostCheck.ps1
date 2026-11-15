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
time at most 300 seconds ahead, and the detail series of a distro only. A
document that does not fit, or no document, is the failed query relay: no
result and no time, so the distro reads as late.

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
}

$script:Source = 'host-check'

# The severity of each check, in the label severity of its result. The
# alert for a new finding (SecurityFindingNew) reads high and critical only.
# Design Doc 0004, section 4.1.4, alerts for Defender off, the firewall off
# and updates pending for more than 14 days, so those are high. Old
# signatures are not in that list: they show on the board and do not alert.
$script:Severity = [ordered]@{
    'defender-enabled'    = 'high'
    'defender-signatures' = 'medium'
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
# The detail series of a distro, each with the names of its labels.
$script:RelayDetails = @{
    homelab_security_host_package_lists_timestamp_seconds = @()
    homelab_security_host_updates_pending                 = @()
    homelab_security_host_updates_held                    = @()
    homelab_security_host_reboot_pending                  = @()
    homelab_security_host_write_test_result               = @('uid')
}

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
    # install them, and the search of updates-pending does not list them.
    Search-TnwksUpdates "IsInstalled=0 and IsHidden=1 and Type='Software'"
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

function Measure-TnwksHostCheck {
    <#
    .SYNOPSIS
    Runs each query, and gives the result of each check, the series of the
    host check itself, and the names of the queries that failed.
    #>
    param(
        [datetime]$Now,
        [int]$SignatureMaxAgeDays,
        [int]$UpdatesMaxAgeDays
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
    $families = [ordered]@{}
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
    if ($Object -isnot [System.Management.Automation.PSCustomObject]) { throw 'not an object' }
    @($Object.PSObject.Properties | ForEach-Object { $_.Name })
}

function Read-TnwksDistroDocument {
    <#
    .SYNOPSIS
    Reads the document of the distro, and gives its measure and its time. A
    document that does not fit throws, with the reason.
    #>
    param([string]$Path, [string]$System, [datetime]$Now)
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) { throw 'the document is a directory' }
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'the document is a link' }
    # Read at most one byte past the limit, so a file that grows after the
    # check of its length is still refused.
    $buffer = New-Object byte[] ($script:RelayMaxBytes + 1)
    $count = 0
    $stream = [IO.File]::Open($item.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
    try {
        while ($count -lt $buffer.Length) {
            $read = $stream.Read($buffer, $count, $buffer.Length - $count)
            if ($read -le 0) { break }
            $count += $read
        }
    } finally {
        $stream.Dispose()
    }
    if ($count -gt $script:RelayMaxBytes) { throw "the document is larger than $($script:RelayMaxBytes) bytes" }
    # UTF-8 that is not valid throws.
    $document = (New-Object Text.UTF8Encoding($false, $true)).GetString($buffer, 0, $count) | ConvertFrom-Json
    $names = Get-TnwksNames $document
    foreach ($name in $names) {
        if (@('version', 'system', 'time', 'results', 'failed', 'details') -notcontains $name) { throw "the document has a field $name" }
    }
    if (-not (Test-TnwksWhole $document.version) -or $document.version -ne 1) { throw 'the document is not version 1' }
    if ($document.system -isnot [string] -or $document.system -cne $System) { throw "the document names another system than $System" }
    if (-not (Test-TnwksNumber $document.time) -or $document.time -le 0) { throw 'the document has no time' }
    $limit = (ConvertTo-TnwksUnixTime $Now) + $script:RelayMaxAheadSeconds
    if ([double]$document.time -gt $limit) { throw 'the time of the document is more than 300 seconds ahead' }

    $failed = New-Object System.Collections.Generic.List[string]
    $queries = @($document.failed)
    if ($queries.Count -gt 10) { throw 'the document names more than 10 failed queries' }
    foreach ($query in $queries) {
        if ($query -isnot [string] -or $query -cnotmatch '^[a-z][a-z-]{0,39}$') { throw 'a failed query has no name' }
        $failed.Add($query)
    }

    $results = [ordered]@{}
    $checks = Get-TnwksNames $document.results
    foreach ($check in $checks) {
        if ($script:RelayChecks -cnotcontains $check) { throw "the document has a check $check that is not of a distro" }
        $value = $document.results.$check
        if (-not (Test-TnwksWhole $value) -or $value -lt 0) { throw "the result of $check is not a whole number of 0 or more" }
        $results[$check] = [long]$value
    }
    if ($failed.Count -eq 0 -and $results.Count -ne $script:RelayChecks.Count) { throw 'the document has no failed query and not each result' }
    if ($failed.Count -gt 0 -and $results.Count -gt 0) { throw 'the document has a failed query and a result' }

    $details = New-Object System.Collections.Generic.List[object]
    $rows = @($document.details)
    if ($rows.Count -gt 20) { throw 'the document has more than 20 details' }
    foreach ($row in $rows) {
        $fields = Get-TnwksNames $row
        if (($fields | Sort-Object) -join ',' -ne 'labels,name,value') { throw 'a detail is not a name, labels and a value' }
        if ($row.name -isnot [string] -or -not $script:RelayDetails.ContainsKey($row.name)) { throw 'a detail is not a series of a distro' }
        $want = @($script:RelayDetails[$row.name])
        $have = @(Get-TnwksNames $row.labels)
        if ((($have | Sort-Object) -join ',') -cne (($want | Sort-Object) -join ',')) { throw "a detail $($row.name) has other labels" }
        $labels = [ordered]@{}
        foreach ($label in $want) {
            $text = $row.labels.$label
            if ($text -isnot [string] -or $text -cnotmatch '^[0-9]{1,10}$') { throw "the label $label of $($row.name) is not a number" }
            $labels[$label] = $text
        }
        if (-not (Test-TnwksNumber $row.value)) { throw "the value of $($row.name) is not a number" }
        $details.Add(@($row.name, $labels, $row.value))
    }
    $time = [DateTimeOffset]::FromUnixTimeSeconds([long][math]::Floor([double]$document.time)).UtcDateTime
    [pscustomobject]@{
        Measure = [pscustomobject]@{ Results = $results; Details = $details; Failed = $failed }
        Time    = $time
    }
}

function Invoke-TnwksRelay {
    param([string]$System, [string]$InboxDir, [string]$InboxFile, [string]$TextfileDir, [string]$FileName, [datetime]$Now)
    if (-not $System) { throw 'no -System: the name of the distro in security/registry/systems.yaml' }
    try {
        $read = Read-TnwksDistroDocument -Path (Join-Path $InboxDir $InboxFile) -System $System -Now $Now
        $measure = $read.Measure
        $time = $read.Time
    } catch {
        # No result and no time: the distro reads as late. The reason names the
        # rule that the document broke, and no value of the document.
        Write-Output ('relay of {0}: the document does not fit: {1}' -f $System, $_.Exception.Message)
        $failed = New-Object System.Collections.Generic.List[string]
        $failed.Add('relay')
        $measure = [pscustomobject]@{ Results = [ordered]@{}; Details = (New-Object System.Collections.Generic.List[object]); Failed = $failed }
        $time = $Now
    }
    Write-TnwksTextfile $TextfileDir $FileName (Format-TnwksHostCheck -System $System -Measure $measure -Now $time)
    if ($measure.Failed.Count -gt 0) {
        Write-Output ('relay of {0}: no result, these queries failed: {1}' -f $System, ($measure.Failed -join ', '))
        exit 1
    }
    Write-Output ('relay of {0}: {1} checks' -f $System, $measure.Results.Count)
}

function Invoke-TnwksHostCheck {
    param([string]$System, [string]$TextfileDir, [string]$FileName, [int]$SignatureMaxAgeDays, [int]$UpdatesMaxAgeDays)
    if (-not $System) { throw 'no -System: the name of this host in security/registry/systems.yaml' }
    $measure = Measure-TnwksHostCheck -Now (Get-Date) -SignatureMaxAgeDays $SignatureMaxAgeDays -UpdatesMaxAgeDays $UpdatesMaxAgeDays
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
            -FileName $DistroFileName -Now (Get-Date)
    } else {
        Invoke-TnwksHostCheck -System $System -TextfileDir $TextfileDir -FileName $FileName `
            -SignatureMaxAgeDays $SignatureMaxAgeDays -UpdatesMaxAgeDays $UpdatesMaxAgeDays
    }
}
