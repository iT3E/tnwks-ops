# Read-only host capacity evidence. Never attaches, formats or moves a disk.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('wsl', 'wsl-02')]
    [string]$Site,
    [ValidateRange(20, 500)]
    [int]$WindowsReserveGiB = 50
)
$ErrorActionPreference = 'Stop'
$additionalGiB = if ($Site -eq 'wsl') { 64 } else { 368 }
$drives = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {
    [pscustomobject]@{
        drive = $_.DeviceID
        capacity_gib = [math]::Round($_.Size / 1GB, 2)
        physical_free_gib = [math]::Round($_.FreeSpace / 1GB, 2)
        free_percent = [math]::Round(100 * $_.FreeSpace / $_.Size, 2)
    }
})
$osInfo = Get-CimInstance Win32_OperatingSystem
$wslImages = @(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' -ErrorAction SilentlyContinue | ForEach-Object {
    $entry = Get-ItemProperty $_.PSPath
    $root = [Environment]::ExpandEnvironmentVariables($entry.BasePath)
    if ($root.StartsWith('\\?\')) { $root = $root.Substring(4) }
    $file = Join-Path $root 'ext4.vhdx'
    if (Test-Path -LiteralPath $file) {
        # Emit the backing drive and allocated file length, not user-profile paths.
        [pscustomobject]@{
            backing_drive = [IO.Path]::GetPathRoot($file).TrimEnd('\')
            vhdx_file_length_gib = [math]::Round((Get-Item -LiteralPath $file).Length / 1GB, 2)
        }
    }
})
$systemDrive = $drives | Where-Object drive -eq 'C:'
if (-not $systemDrive) { throw 'Cannot establish physical free space on C:.' }
$fits = $systemDrive.physical_free_gib -ge ($additionalGiB + $WindowsReserveGiB)
[pscustomobject]@{
    site = $Site
    measured_at = (Get-Date).ToUniversalTime().ToString('o')
    drives = $drives
    memory_available_gib = [math]::Round($osInfo.FreePhysicalMemory / 1MB, 2)
    memory_total_gib = [math]::Round($osInfo.TotalVisibleMemorySize / 1MB, 2)
    wsl_images = $wslImages
    additional_nominal_capacity_gib = $additionalGiB
    windows_reserve_gib = $WindowsReserveGiB
    conservative_capacity_gate = $fits
    note = 'Nominal PVC/VHDX capacity is not immediate allocation or an enforced local-path quota. Linux virtual free space does not establish Windows physical headroom. Recheck existing PVC occupancy to avoid double counting on WSL-02.'
} | ConvertTo-Json -Depth 6
if (-not $fits) { exit 2 }
