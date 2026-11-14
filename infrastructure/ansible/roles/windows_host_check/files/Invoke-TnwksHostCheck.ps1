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

When one query fails, the file has no result and no time of the last result,
so the rules of the registry read the source as late for this host
(SecurityResultLate), and never as a pass. The file then says how many
queries failed. The file goes to its place in one step, so windows_exporter
never reads half a file.

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
    # Define the functions only, for the tests.
    [switch]$Library
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'

# The text of each series. The check of each WSL distro (role wsl_host_check)
# writes some of the same series to the same exporter, with the same text: a
# series with two texts would stop the exporter from serving it.
$script:Help = [ordered]@{
    homelab_security_check_result                         = 'The number of items that fail one check of the security registry. 0 is a pass.'
    homelab_security_last_result_timestamp_seconds        = 'The Unix time of the newest result of a source of the security registry.'
    homelab_security_host_check_failed_queries            = 'The number of queries of the host check that failed in its last run. Above 0, the run gives no result.'
    homelab_security_host_defender_enabled                = 'Whether one protection of Defender is on: 1 on, 0 off.'
    homelab_security_host_defender_signature_timestamp_seconds = 'The Unix time of the newest signatures of Defender.'
    homelab_security_host_defender_last_scan_timestamp_seconds = 'The Unix time at which the newest quick or full scan of Defender ended.'
    homelab_security_host_firewall_enabled                = 'Whether one profile of the firewall is on in the active store: 1 on, 0 off.'
    homelab_security_host_updates_pending                 = 'The number of updates that the host can install and has not installed.'
    homelab_security_host_updates_last_install_timestamp_seconds = 'The Unix time of the newest update that the host installed.'
    homelab_security_host_reboot_pending                  = 'Whether the host waits for a restart to finish an update: 1 yes, 0 no.'
}

$script:Source = 'host-check'
$script:Profiles = @('Domain', 'Private', 'Public')

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

function Get-TnwksPendingUpdates {
    # Software updates that Windows Update offers and that are not installed.
    # Not hidden, and not optional (BrowseOnly), as Windows would install
    # them. The search asks Windows Update itself, so a stale local cache
    # can not give an empty list.
    $searcher = (Get-TnwksUpdateSession).CreateUpdateSearcher()
    $searcher.Online = $true
    $found = $searcher.Search("IsInstalled=0 and IsHidden=0 and BrowseOnly=0 and Type='Software'")
    # 2 is orcSucceeded. Any other code is a search that did not finish.
    if ($found.ResultCode -ne 2) { throw "the search of Windows Update ended with code $($found.ResultCode)" }
    # One release time for each update, on the output.
    foreach ($update in $found.Updates) { [datetime]$update.LastDeploymentChangeTime }
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
            Add-Line 'homelab_security_check_result' (Format-TnwksSeries 'homelab_security_check_result' ([ordered]@{ source = $script:Source; check = $check; system = $System }) $Measure.Results[$check])
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
        [IO.File]::Replace($partial, $final, $null)
    } else {
        [IO.File]::Move($partial, $final)
    }
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
    Invoke-TnwksHostCheck -System $System -TextfileDir $TextfileDir -FileName $FileName `
        -SignatureMaxAgeDays $SignatureMaxAgeDays -UpdatesMaxAgeDays $UpdatesMaxAgeDays
}
