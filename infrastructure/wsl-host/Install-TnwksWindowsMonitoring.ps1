# Install-TnwksWindowsMonitoring.ps1 [-Check]
#
# The Windows host's monitoring agents, which Prometheus on the WSL cluster
# scrapes at ${WINDOWS_HOST_IP} (kube-prometheus-stack additionalScrapeConfigs):
#
#   windows_exporter   :9182  service, collectors from config.yaml
#   smartctl_exporter  :9633  SYSTEM scheduled task \Prometheus\smartctl_exporter,
#                             reading drives through smartmontools' smartctl
#
# Both listen only to the WSL NAT subnet (the vEthernet (WSL) network), which
# is where the cluster's scrapes come from; nothing else on the LAN reaches
# them. Every download is pinned and SHA-256 checked. Idempotent: only what
# differs from this file is changed. -Check reports drift and changes nothing.
#
# Run elevated: `task wsl-host:monitoring` (or `task wsl-host:monitoring-check`).

[CmdletBinding()]
param(
    [switch]$Check,
    # Default: derived from the vEthernet (WSL) adapter, e.g. 172.26.80.0/20.
    [string]$WslSubnet
)

$ErrorActionPreference = 'Stop'
# Run from a local directory: invoked from the repo, the working directory is a
# \\wsl.localhost UNC path, which cmd.exe and some installers reject on stderr.
Set-Location $env:TEMP

$WindowsExporter = @{
    Version    = '0.31.7'
    Url        = 'https://github.com/prometheus-community/windows_exporter/releases/download/v0.31.7/windows_exporter-0.31.7-amd64.msi'
    Sha256     = 'd29114fb7ae6af2865a9f1c4be7fdccc2b9fbbb4366a811b620bc85c983d0007'
    Dir        = 'C:\Program Files\windows_exporter'
    Port       = 9182
    # diskdrive feeds the WindowsDiskDriveStatusBad alert (disk-health.yaml).
    Collectors = 'cpu,logical_disk,memory,net,os,physical_disk,service,system,diskdrive'
}
$Smartmontools = @{
    Version = '7.5'
    Url     = 'https://github.com/smartmontools/smartmontools/releases/download/RELEASE_7_5/smartmontools-7.5.win32-setup.exe'
    Sha256  = '896337fcc253220614cf8cdbd5cf2321c5aa326a37a04160a672a281e6104c70'
    Smartctl = 'C:\Program Files\smartmontools\bin\smartctl.exe'
}
$SmartctlExporter = @{
    Version  = '0.14.0'
    Url      = 'https://github.com/prometheus-community/smartctl_exporter/releases/download/v0.14.0/smartctl_exporter-0.14.0.windows-amd64.zip'
    Sha256   = 'dc4345fdc877e9e70afc510df293b1ec59f11e0d7a847a3482290aa7f4b0340a'
    Dir      = 'C:\Program Files\smartctl_exporter'
    Port     = 9633
    TaskPath = '\Prometheus\'
    TaskName = 'smartctl_exporter'
}
# Drives are re-read every 120s (the cluster scrapes every 60s); the device
# list is rescanned every 10m, so a hot-plugged drive appears without a restart.
$SmartctlArgs = "--smartctl.path=`"$($Smartmontools.Smartctl)`" --smartctl.interval=120s --smartctl.rescan=10m --web.listen-address=:$($SmartctlExporter.Port) --log.level=warn"

$script:drift = 0
function Say([string]$msg) { Write-Host $msg }
function Drift([string]$msg) {
    $script:drift++
    if ($Check) { Write-Host "DRIFT  $msg" } else { Write-Host "CHANGE $msg" }
}

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Install-TnwksWindowsMonitoring.ps1 must be run as Administrator.'
}

function Get-Verified([hashtable]$pkg) {
    $file = Join-Path $env:TEMP (Split-Path $pkg.Url -Leaf)
    Invoke-WebRequest -UseBasicParsing -Uri $pkg.Url -OutFile $file
    $hash = (Get-FileHash -Algorithm SHA256 $file).Hash.ToLower()
    if ($hash -ne $pkg.Sha256) {
        Remove-Item $file -Force
        throw "checksum mismatch for $($pkg.Url): got $hash"
    }
    return $file
}

function Get-WslSubnet {
    if ($WslSubnet) { return $WslSubnet }
    $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceAlias -like 'vEthernet (WSL*' } | Select-Object -First 1
    if (-not $ip) { throw 'no vEthernet (WSL) adapter; pass -WslSubnet' }
    $bytes = ([Net.IPAddress]$ip.IPAddress).GetAddressBytes()
    [Array]::Reverse($bytes)
    $mask = [uint32]([math]::Pow(2, 32) - [math]::Pow(2, 32 - $ip.PrefixLength))
    $net = [BitConverter]::GetBytes([BitConverter]::ToUInt32($bytes, 0) -band $mask)
    [Array]::Reverse($net)
    return '{0}/{1}' -f ([Net.IPAddress]$net).IPAddressToString, $ip.PrefixLength
}

# --- windows_exporter --------------------------------------------------------
$weConfig = Join-Path $WindowsExporter.Dir 'config.yaml'
$weWanted = "collectors:`n  enabled: $($WindowsExporter.Collectors)`n"
$weInstalled = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -eq 'windows_exporter' } | Select-Object -First 1
$weConfigNow = if (Test-Path $weConfig) { (Get-Content -Raw $weConfig) -replace "`r", '' } else { '' }
if ($weConfigNow -ne $weWanted) {
    Drift "windows_exporter config.yaml (collectors: $($WindowsExporter.Collectors))"
    if (-not $Check) {
        New-Item -ItemType Directory -Force -Path $WindowsExporter.Dir | Out-Null
        [IO.File]::WriteAllText($weConfig, $weWanted)
    }
}
if (-not $weInstalled -or $weInstalled.DisplayVersion -ne $WindowsExporter.Version) {
    Drift "windows_exporter $($weInstalled.DisplayVersion) -> $($WindowsExporter.Version)"
    if (-not $Check) {
        $msi = Get-Verified $WindowsExporter
        $p = Start-Process msiexec.exe -Wait -PassThru -ArgumentList @(
            '/i', "`"$msi`"", '/qn', '/norestart', "CONFIG_FILE=`"$weConfig`"")
        if ($p.ExitCode -ne 0) { throw "windows_exporter install failed ($($p.ExitCode))" }
    }
} elseif ($weConfigNow -ne $weWanted -and -not $Check) {
    Restart-Service windows_exporter
}
$svc = Get-CimInstance Win32_Service -Filter "Name='windows_exporter'" -ErrorAction SilentlyContinue
if ($svc -and $svc.PathName -notmatch [regex]::Escape("--config.file=`"$weConfig`"")) {
    Drift "windows_exporter service does not use $weConfig ($($svc.PathName))"
}
if ($svc -and $svc.StartMode -ne 'Auto') {
    Drift 'windows_exporter service start mode -> Automatic'
    if (-not $Check) { Set-Service windows_exporter -StartupType Automatic }
}
if ($svc -and $svc.State -ne 'Running' -and -not $Check) { Start-Service windows_exporter }

# --- smartmontools -----------------------------------------------------------
$smartctlVersion = if (Test-Path $Smartmontools.Smartctl) {
    ((& $Smartmontools.Smartctl --version | Select-Object -First 1) -split ' ')[1]
} else { '' }
if ($smartctlVersion -ne $Smartmontools.Version) {
    Drift "smartmontools '$smartctlVersion' -> $($Smartmontools.Version)"
    if (-not $Check) {
        $exe = Get-Verified $Smartmontools
        $p = Start-Process $exe -Wait -PassThru -ArgumentList '/S'
        if ($p.ExitCode -ne 0) { throw "smartmontools install failed ($($p.ExitCode))" }
    }
}

# --- smartctl_exporter -------------------------------------------------------
$seExe = Join-Path $SmartctlExporter.Dir 'smartctl_exporter.exe'
# Through cmd: the exporter prints its version on stderr, and Windows
# PowerShell 5.1 turns redirected native stderr into a terminating error under
# ErrorActionPreference=Stop.
$seVersion = if (Test-Path $seExe) {
    if ((cmd.exe /c "`"$seExe`" --version 2>&1" | Out-String) -match 'version (\S+)') { $Matches[1] } else { '?' }
} else { '' }
$task = Get-ScheduledTask -TaskPath $SmartctlExporter.TaskPath -TaskName $SmartctlExporter.TaskName -ErrorAction SilentlyContinue
if ($seVersion -ne $SmartctlExporter.Version) {
    Drift "smartctl_exporter '$seVersion' -> $($SmartctlExporter.Version)"
    if (-not $Check) {
        $zip = Get-Verified $SmartctlExporter
        $tmp = Join-Path $env:TEMP 'smartctl_exporter-extract'
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        Expand-Archive -Path $zip -DestinationPath $tmp
        if ($task) { Stop-ScheduledTask -InputObject $task -ErrorAction SilentlyContinue }
        Get-Process smartctl_exporter -ErrorAction SilentlyContinue | Stop-Process -Force
        New-Item -ItemType Directory -Force -Path $SmartctlExporter.Dir | Out-Null
        Get-ChildItem $tmp -Recurse -File | Copy-Item -Destination $SmartctlExporter.Dir -Force
        Remove-Item -Recurse -Force $tmp
    }
}

$action = $task.Actions | Select-Object -First 1
$taskOk = $task -and $action.Execute -eq $seExe -and $action.Arguments -eq $SmartctlArgs -and
    $task.Principal.UserId -in @('SYSTEM', 'NT AUTHORITY\SYSTEM')
if (-not $taskOk) {
    if ($task) { Say "       current task: $($action.Execute) $($action.Arguments) as $($task.Principal.UserId)" }
    Drift "task $($SmartctlExporter.TaskPath)$($SmartctlExporter.TaskName): $seExe $SmartctlArgs as SYSTEM"
    if (-not $Check) {
        if ($task) {
            Stop-ScheduledTask -InputObject $task -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -InputObject $task -Confirm:$false
        }
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskPath $SmartctlExporter.TaskPath -TaskName $SmartctlExporter.TaskName `
            -Action (New-ScheduledTaskAction -Execute $seExe -Argument $SmartctlArgs) `
            -Trigger (New-ScheduledTaskTrigger -AtStartup) -Settings $settings `
            -Principal (New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest) | Out-Null
        $task = Get-ScheduledTask -TaskPath $SmartctlExporter.TaskPath -TaskName $SmartctlExporter.TaskName
    }
}
if ($task -and $task.State -ne 'Running' -and -not $Check) { Start-ScheduledTask -InputObject $task }

# --- firewall: both exporters, WSL subnet only -------------------------------
$subnet = Get-WslSubnet
# Windows reports a rule's subnet with a dotted mask (172.26.80.0/255.255.240.0).
$prefix = [int]($subnet -split '/')[1]
$maskBytes = [BitConverter]::GetBytes([uint32]([math]::Pow(2, 32) - [math]::Pow(2, 32 - $prefix)))
[Array]::Reverse($maskBytes)
$subnetDotted = '{0}/{1}' -f ($subnet -split '/')[0], ([Net.IPAddress]$maskBytes).IPAddressToString
foreach ($fw in @(
        @{ Name = 'windows_exporter (Prometheus)'; Port = $WindowsExporter.Port },
        @{ Name = 'smartctl_exporter (Prometheus)'; Port = $SmartctlExporter.Port })) {
    $rule = Get-NetFirewallRule -DisplayName $fw.Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $rule) {
        Drift "firewall '$($fw.Name)': inbound tcp/$($fw.Port) from $subnet"
        if (-not $Check) {
            New-NetFirewallRule -DisplayName $fw.Name -Direction Inbound -Action Allow -Protocol TCP `
                -LocalPort $fw.Port -RemoteAddress $subnet -Profile Any | Out-Null
        }
        continue
    }
    $port = $rule | Get-NetFirewallPortFilter
    $addr = ($rule | Get-NetFirewallAddressFilter).RemoteAddress
    $ok = $port.Protocol -eq 'TCP' -and [string]$port.LocalPort -eq [string]$fw.Port -and $rule.Enabled -eq 'True' -and
        @($addr).Count -eq 1 -and ([string]$addr) -eq $subnetDotted
    if (-not $ok) {
        Drift "firewall '$($fw.Name)': tcp/$($port.LocalPort) from $addr -> tcp/$($fw.Port) from $subnet"
        if (-not $Check) {
            $rule | Set-NetFirewallRule -Enabled True -Direction Inbound -Action Allow -Protocol TCP `
                -LocalPort $fw.Port -RemoteAddress $subnet -Profile Any
        }
    }
}

# --- verify ------------------------------------------------------------------
if (-not $Check) { Start-Sleep -Seconds 5 }
foreach ($probe in @(
        @{ Name = 'windows_exporter'; Url = "http://localhost:$($WindowsExporter.Port)/metrics"; Expect = 'windows_diskdrive_status' },
        @{ Name = 'smartctl_exporter'; Url = "http://localhost:$($SmartctlExporter.Port)/metrics"; Expect = 'smartctl_device{' })) {
    try {
        $body = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 30 -Uri $probe.Url).Content
        if ($body.Contains($probe.Expect)) { Say "ok     $($probe.Name) serves $($probe.Expect)" }
        else { Say "WARN   $($probe.Name) answers but has no $($probe.Expect)" }
    } catch { Say "WARN   $($probe.Name) not answering on $($probe.Url): $($_.Exception.Message)" }
}

if ($Check) {
    if ($script:drift) { Say "$($script:drift) item(s) differ from Install-TnwksWindowsMonitoring.ps1"; exit 2 }
    Say 'No drift.'
}
