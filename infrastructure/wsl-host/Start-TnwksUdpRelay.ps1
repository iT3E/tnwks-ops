# Start-TnwksUdpRelay.ps1
#
# Long-running UDP relay: LAN -> this Windows host -> the WSL VM.
#
# netsh portproxy (used by Sync-TnwksLanBridge.ps1 for 80/443/1883) is
# TCP-only, so UDP services like NetFlow/IPFIX need their own forwarder.
# Chain for flow export (see docs/wsl-lan-exposure.md):
#
#   router udp/2055 -> <windows-ip>:2055 (this relay)
#     -> <wsl-ip>:2055 (socat: tnwks-lan-bridge@netflow)
#     -> MetalLB 10.5.0.204:2055 (monitoring/flow-collector)
#
# The WSL VM IP changes on every WSL restart, so it is re-resolved every
# $RefreshSeconds. Run by the 'tnwks-udp-relay' Scheduled Task as the desktop
# user: WSL distros are per-user, so SYSTEM's wsl.exe cannot see 'Ubuntu'.

[CmdletBinding()]
param(
    [int]$Port = 2055,
    [string]$WslDistro = 'Ubuntu',
    [int]$RefreshSeconds = 60,
    [string]$LogPath
)

$ErrorActionPreference = 'Continue'

if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $dir = $PSScriptRoot
    if ([string]::IsNullOrWhiteSpace($dir)) { $dir = 'C:\ProgramData\tnwks-udp-relay' }
    $LogPath = Join-Path $dir 'udp-relay.log'
}

function Write-Log([string]$msg) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    try {
        if ((Test-Path $LogPath) -and (Get-Item $LogPath).Length -gt 1MB) {
            Move-Item $LogPath "$LogPath.1" -Force
        }
        Add-Content -Path $LogPath -Value $line
    } catch { }
}

function Get-WslIpv4 {
    # Preferred: ask the distro. Fallback: the connectaddress of the existing
    # portproxy rules, which Sync-TnwksLanBridge.ps1 keeps current.
    try {
        $raw = & wsl.exe -d $WslDistro -- hostname -I 2>$null
        if ($LASTEXITCODE -eq 0 -and $raw) {
            $ip = (($raw | Out-String).Trim() -split '\s+')[0]
            if ($ip -match '^\d+\.\d+\.\d+\.\d+$') { return $ip }
        }
    } catch { }
    $m = netsh interface portproxy show v4tov4 | Select-String -Pattern '^\s*\S+\s+\d+\s+(\d+\.\d+\.\d+\.\d+)\s+\d+'
    if ($m) { return $m[0].Matches[0].Groups[1].Value }
    return $null
}

$listener = New-Object System.Net.Sockets.UdpClient($Port)
$listener.Client.ReceiveTimeout = 5000
$sender = New-Object System.Net.Sockets.UdpClient
$remote = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
$target = $null
$lastResolve = [datetime]::MinValue
$forwarded = 0
$lastReport = Get-Date

Write-Log "listening on udp/$Port, distro=$WslDistro"

while ($true) {
    if (((Get-Date) - $lastResolve).TotalSeconds -ge $RefreshSeconds -or -not $target) {
        $lastResolve = Get-Date
        $ip = Get-WslIpv4
        if ($ip) {
            if (-not $target -or $target.Address.ToString() -ne $ip) {
                $target = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Parse($ip), $Port)
                Write-Log "forwarding to ${ip}:$Port"
            }
        } elseif (-not $target) {
            Write-Log 'WSL IP unknown, dropping packets until it resolves'
        }
    }
    try {
        $bytes = $listener.Receive([ref]$remote)
    } catch [System.Net.Sockets.SocketException] {
        continue  # receive timeout: loop to re-check the target
    }
    if ($target) {
        try {
            [void]$sender.Send($bytes, $bytes.Length, $target)
            $forwarded++
        } catch {
            Write-Log "send failed: $($_.Exception.Message)"
        }
    }
    if (((Get-Date) - $lastReport).TotalMinutes -ge 60) {
        Write-Log "forwarded $forwarded packets in the last hour"
        $forwarded = 0
        $lastReport = Get-Date
    }
}
