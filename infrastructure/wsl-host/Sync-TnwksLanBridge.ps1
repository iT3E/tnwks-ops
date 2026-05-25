# Sync-TnwksLanBridge.ps1
#
# Keeps Windows netsh portproxy rules in sync with the current WSL VM IP so
# that LAN devices can reach the homelab-wsl cluster's internal services
# (Ingress 80/443, k8s-gateway DNS 53/UDP, mosquitto 1883) through this
# Windows host.
#
# Run elevated. Idempotent — safe to invoke from a Scheduled Task on logon
# or whenever the WSL VM IP changes.

[CmdletBinding()]
param(
    [string]$WslDistro = 'Ubuntu',
    # Ports forwarded LAN -> Windows -> WSL eth0 -> socat -> MetalLB VIP.
    # 53/UDP is excluded here because netsh portproxy is TCP-only; UDP DNS
    # is handled separately (see docs/wsl-lan-exposure.md — port 53 needs a
    # different forwarder, e.g. PortProxy with WinDivert or running k8s-gateway
    # DNS on a different LAN entry path).
    [int[]]$TcpPorts  = @(80, 443, 1883),
    [string]$ListenAddress = '0.0.0.0'
)

$ErrorActionPreference = 'Stop'

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

function Sync-PortProxy {
    param(
        [string]$ListenAddress,
        [int]   $Port,
        [string]$WslIp
    )
    $existing = netsh interface portproxy show v4tov4 |
        Select-String -Pattern "^\s*$([regex]::Escape($ListenAddress))\s+$Port\s+"
    if ($existing) {
        # Reset to the new target — netsh has no in-place "update".
        & netsh interface portproxy delete v4tov4 listenaddress=$ListenAddress listenport=$Port | Out-Null
    }
    & netsh interface portproxy add v4tov4 `
        listenaddress=$ListenAddress listenport=$Port `
        connectaddress=$WslIp connectport=$Port | Out-Null
    Write-Host "portproxy ${ListenAddress}:${Port} -> ${WslIp}:${Port}"
}

function Sync-FirewallRule {
    param([int]$Port)
    $name = "tnwks-lan-bridge-$Port"
    $rule = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    if (-not $rule) {
        New-NetFirewallRule -DisplayName $name -Direction Inbound `
            -Action Allow -Protocol TCP -LocalPort $Port `
            -Profile Any | Out-Null
        Write-Host "firewall: created inbound allow tcp/$Port"
    }
}

Require-Admin

$wslIp = Get-WslIpv4 -Distro $WslDistro
Write-Host "WSL ($WslDistro) IPv4: $wslIp"

foreach ($port in $TcpPorts) {
    Sync-PortProxy -ListenAddress $ListenAddress -Port $port -WslIp $wslIp
    Sync-FirewallRule -Port $port
}

Write-Host ''
Write-Host 'Final portproxy table:'
& netsh interface portproxy show v4tov4
