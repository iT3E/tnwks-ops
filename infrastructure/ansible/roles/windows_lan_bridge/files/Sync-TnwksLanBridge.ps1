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
#
# With wsl_start_unelevated (Design Doc 0004, section 5.9), no elevated task
# starts a process in the distro, so the script runs in two parts:
#   -WriteIpFile <path>  the task tnwks-lan-bridge-ip, with no elevation: asks
#                        the distro for the VM address and writes it to the
#                        file. It needs no admin right and changes no rule.
#   -IpFile <path>       the task tnwks-lan-bridge-sync, as SYSTEM: reads the
#                        address from the file and runs no wsl.exe. It takes
#                        only an address in the subnet of the vEthernet (WSL)
#                        adapter, so the file can point a rule at the WSL VM
#                        only, and it writes to its log only when it changes a
#                        rule.

[CmdletBinding()]
param(
    [string]$WslDistro = 'Ubuntu',
    # No $PSScriptRoot default: it is empty during param binding under
    # `powershell.exe -File`, so the path is resolved below instead.
    [string]$ConfigPath,
    [string]$WriteIpFile,
    [string]$IpFile,
    # Define the functions only, for the tests.
    [switch]$Library
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

function Write-WslIpFile {
    param([string]$Path, [string]$Ip)
    # A new file next to the old one, then one rename: the reader never sees
    # half a file.
    $text = (@{ address = $Ip; time = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() } | ConvertTo-Json -Compress)
    $temp = "$Path.new"
    [IO.File]::WriteAllText($temp, $text, (New-Object Text.UTF8Encoding $false))
    Move-Item -LiteralPath $temp -Destination $Path -Force
}

function ConvertTo-UInt32Address([string]$Address) {
    $bytes = ([Net.IPAddress]::Parse($Address)).GetAddressBytes()
    [Array]::Reverse($bytes)
    [BitConverter]::ToUInt32($bytes, 0)
}

function Get-WslSubnet {
    # The address and prefix of the host on the vEthernet (WSL) adapter, as
    # playbooks/windows-host.yml reads them.
    $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceAlias -like 'vEthernet (WSL*' } | Select-Object -First 1
    if (-not $ip) { throw 'No vEthernet (WSL) adapter.' }
    [pscustomobject]@{ Address = [string]$ip.IPAddress; PrefixLength = [int]$ip.PrefixLength }
}

function Read-WslIpFile {
    param([string]$Path, $Subnet)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "$Path is not a plain file." }
    if ($item.Length -gt 1024) { throw "$Path is larger than 1 KiB." }
    $ip = [string](Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json).address
    if ($ip -notmatch '^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$') { throw "$Path has no IPv4 address." }
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($ip, [ref]$parsed)) { throw "$Path has no IPv4 address." }
    $mask = [uint32]([math]::Pow(2, 32) - [math]::Pow(2, 32 - $Subnet.PrefixLength))
    $hostAddress = ConvertTo-UInt32Address $Subnet.Address
    $address = ConvertTo-UInt32Address $ip
    if ((($address -band $mask) -ne ($hostAddress -band $mask)) -or $address -eq $hostAddress) {
        throw "$ip is not an address of the WSL VM in the subnet of the vEthernet (WSL) adapter."
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

if ($Library) { return }

if ($WriteIpFile) {
    # No admin right and no rule: the address only.
    $wslIp = Get-WslIpv4 -Distro $WslDistro
    Write-WslIpFile -Path $WriteIpFile -Ip $wslIp
    exit 0
}

if ($IpFile) {
    Require-Admin
    if (-not (Test-Path $ConfigPath)) { throw "No port list at $ConfigPath (written by Ansible, role windows_lan_bridge)." }
    $config = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
    $listen = $config.ListenAddress
    $wslIp = Read-WslIpFile -Path $IpFile -Subnet (Get-WslSubnet)
    $current = Get-PortProxy
    $same = @($config.TcpPorts | Where-Object { $current["${listen}:$($_.Port)"] -ne "${wslIp}:$($_.WslPort)" }).Count -eq 0 -and
        @($config.RetiredTcpPorts | Where-Object { $current.ContainsKey("${listen}:$_") }).Count -eq 0
    # Each 5 minutes: quiet when each rule is right.
    if ($same) { exit 0 }
    Write-Host "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') Sync-TnwksLanBridge (address from $IpFile)"
} else {
    Write-Host "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') Sync-TnwksLanBridge"
    Require-Admin

    if (-not (Test-Path $ConfigPath)) { throw "No port list at $ConfigPath (written by Ansible, role windows_lan_bridge)." }
    $config = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
    $listen = $config.ListenAddress

    $wslIp = Get-WslIpv4 -Distro $WslDistro
}
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
