# Sync-TnwksLanBridge.ps1
#
# Keeps Windows netsh portproxy rules in sync with the current WSL VM IP so
# that LAN devices can reach the homelab-wsl cluster's internal services
# (Ingress 80/443, mosquitto 1883, UniFi inform 8080) through this Windows
# host. UDP services (NetFlow 2055, syslog 6001) use Start-TnwksUdpRelay.ps1
# instead. See docs/wsl/lan-exposure.md.
#
# The ports come from ports.json next to this script, which Ansible writes
# (role windows_lan_bridge, `lan_bridge_tcp_ports`). Ansible also owns the
# firewall rules tnwks-lan-bridge-<port> and this script's Scheduled Task
# (tnwks-lan-bridge-sync). This script only does what has to happen at run
# time: point each portproxy entry at the WSL VM's address, which changes on
# every WSL restart, and drop entries for retired ports.
#
# Run elevated. Idempotent: an entry that already points at the right address
# is left alone, so a run doesn't drop open connections.

[CmdletBinding()]
param(
    [string]$WslDistro = 'Ubuntu',
    # No $PSScriptRoot default: it is empty during param binding under
    # `powershell.exe -File`, so the path is resolved below instead.
    [string]$ConfigPath
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $here = $PSScriptRoot
    if ([string]::IsNullOrWhiteSpace($here) -and $MyInvocation.MyCommand.Path) {
        $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    }
    if ([string]::IsNullOrWhiteSpace($here)) { $here = 'C:\ProgramData\tnwks-lan-bridge' }
    $ConfigPath = Join-Path $here 'ports.json'
}

function Require-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Sync-TnwksLanBridge.ps1 must be run as Administrator.'
    }
}

function Get-WslIpv4 {
    param([string]$Distro)
    # `wsl -d <distro> hostname -I` returns space-separated IPv4s; first
    # one is the eth0 address inside the WSL VM.
    $raw = & wsl.exe -d $Distro -- hostname -I 2>$null
    if (-not $raw) { throw "Could not query IP for WSL distro '$Distro'." }
    $ip = ($raw.Trim() -split '\s+')[0]
    if ($ip -notmatch '^\d+\.\d+\.\d+\.\d+$') {
        throw "Got non-IPv4 from WSL hostname -I: '$ip'"
    }
    return $ip
}

# Current v4tov4 entries as listen "address:port" -> "connectaddress:connectport".
function Get-PortProxy {
    $map = @{}
    foreach ($line in (netsh interface portproxy show v4tov4)) {
        if ($line -match '^\s*(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(\S+)\s+(\d+)\s*$') {
            $map["$($Matches[1]):$($Matches[2])"] = "$($Matches[3]):$($Matches[4])"
        }
    }
    return $map
}

function Sync-PortProxy {
    param(
        [hashtable]$Current,
        [string]$ListenAddress,
        [int]   $Port,
        [string]$WslIp,
        [int]   $ConnectPort
    )
    $want = "${WslIp}:${ConnectPort}"
    $have = $Current["${ListenAddress}:${Port}"]
    if ($have -eq $want) {
        Write-Host "portproxy ${ListenAddress}:${Port} -> $want (unchanged)"
        return
    }
    if ($have) {
        # netsh has no in-place update.
        & netsh interface portproxy delete v4tov4 listenaddress=$ListenAddress listenport=$Port | Out-Null
    }
    & netsh interface portproxy add v4tov4 `
        listenaddress=$ListenAddress listenport=$Port `
        connectaddress=$WslIp connectport=$ConnectPort | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "netsh portproxy add ${ListenAddress}:${Port} failed ($LASTEXITCODE)" }
    Write-Host "portproxy ${ListenAddress}:${Port} -> $want (was: $(if ($have) { $have } else { 'none' }))"
}

function Remove-RetiredPort {
    param([hashtable]$Current, [string]$ListenAddress, [int]$Port)
    if ($Current.ContainsKey("${ListenAddress}:${Port}")) {
        & netsh interface portproxy delete v4tov4 listenaddress=$ListenAddress listenport=$Port | Out-Null
        Write-Host "portproxy ${ListenAddress}:${Port} removed (retired)"
    }
}

Write-Host "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') Sync-TnwksLanBridge"
Require-Admin

if (-not (Test-Path $ConfigPath)) { throw "No port list at $ConfigPath (written by Ansible, role windows_lan_bridge)." }
$config = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
$listen = $config.ListenAddress

$wslIp = Get-WslIpv4 -Distro $WslDistro
Write-Host "WSL ($WslDistro) IPv4: $wslIp"

$current = Get-PortProxy
foreach ($p in @($config.TcpPorts)) {
    Sync-PortProxy -Current $current -ListenAddress $listen -Port $p.Port -WslIp $wslIp -ConnectPort $p.WslPort
}
foreach ($port in @($config.RetiredTcpPorts)) {
    Remove-RetiredPort -Current $current -ListenAddress $listen -Port $port
}

Write-Host ''
Write-Host 'Final portproxy table:'
& netsh interface portproxy show v4tov4
