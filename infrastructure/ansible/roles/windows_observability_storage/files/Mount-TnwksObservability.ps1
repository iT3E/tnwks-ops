# One C:-backed native ext4 VHDX; normal startup never initializes a disk.
[CmdletBinding()]
param(
    [string]$Vhdx = 'C:\WSL\observability.vhdx',
    [string]$StateDir = 'C:\ProgramData\tnwks-observability',
    [string]$Distro = 'Ubuntu',
    [ValidateSet(20)][int]$SizeGiB = 20,
    [switch]$Initialize
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ([IO.Path]::GetFullPath($Vhdx) -ne 'C:\WSL\observability.vhdx' -or $StateDir -ne 'C:\ProgramData\tnwks-observability') {
    throw 'Only the dedicated C monitoring volume and ownership directory are managed.'
}
$stateFile = Join-Path $StateDir 'volume.json'
$helper = '/mnt/c/ProgramData/tnwks-observability/observability-volume.py'

function Invoke-VolumeHelper([string]$Action, [string[]]$Extra = @()) {
    $helperArgs = @('-d', $Distro, '-u', 'root', '--', 'python3', $helper, $Action)
    if ($Action -ne 'probe') { $helperArgs += @('--uuid', $script:state.uuid, '--size-gib', "$SizeGiB") }
    $output = & wsl.exe @helperArgs @Extra 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Monitoring volume helper ($Action) failed; no fallback is allowed." }
    # Windows PowerShell 5.1 has no String.Replace(char, string); -replace works in both.
    return ((($output -join "`n") -replace "`0", '').Trim() | ConvertFrom-Json)
}

if (-not (Test-Path -LiteralPath $stateFile)) {
    if (-not $Initialize) { throw 'Monitoring disk is not initialized.' }
    if (Test-Path -LiteralPath $Vhdx) { throw 'Existing VHDX has no ownership record; refusing to use or format it.' }
    if ((Get-PSDrive C).Free -lt (($SizeGiB + 50) * 1GB)) { throw 'Insufficient C: space for full volume growth and 50GiB reserve.' }
    $state = [pscustomobject]@{ path = $Vhdx; size_gib = $SizeGiB; uuid = [guid]::NewGuid().ToString(); phase = 'pending' }
    # Record the intended random UUID before creating or formatting anything.
    $state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
} else {
    $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
}
if ($state.path -ne $Vhdx -or $state.size_gib -ne $SizeGiB) { throw 'Monitoring disk ownership record does not match the requested configuration.' }
if (-not (Test-Path -LiteralPath $Vhdx)) {
    if (-not $Initialize -or $state.phase -ne 'pending') { throw 'Managed VHDX is missing; startup never recreates it.' }
    $diskpartFile = Join-Path $StateDir 'create-volume.diskpart'
    @("create vdisk file=`"$Vhdx`" maximum=$($SizeGiB * 1024) type=expandable", 'exit') |
        Set-Content -LiteralPath $diskpartFile -Encoding ASCII
    try {
        $null = & diskpart.exe /s $diskpartFile
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Vhdx)) { throw 'VHDX creation failed.' }
    } finally { Remove-Item -LiteralPath $diskpartFile -Force -ErrorAction SilentlyContinue }
}
if ($state.phase -eq 'pending') {
    if (-not $Initialize) { throw 'Initialization is incomplete; startup will not format.' }
    # A retry may find this exact, still-unassigned VHDX attached. It cannot
    # belong to a Talos worker until initialization has completed below.
    $null = & wsl.exe --unmount $Vhdx 2>&1
    $before = Invoke-VolumeHelper 'probe'
    $null = & wsl.exe --mount --vhd $Vhdx --bare 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'Cannot attach the new monitoring VHDX.' }
    $after = Invoke-VolumeHelper 'probe'
    $new = @($after.disks | Where-Object { $_.name -notin @($before.disks.name) })
    if ($new.Count -ne 1 -or [long]$new[0].size -ne ($SizeGiB * 1GB)) { throw 'New monitoring disk is ambiguous or has the wrong size; refusing to format.' }
    $null = Invoke-VolumeHelper 'initialize' -Extra @('--device', $new[0].name)
    $state.phase = 'initialized'
    $state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
} else {
    if ($state.phase -ne 'initialized') { throw 'Unknown monitoring disk state.' }
    $status = Invoke-VolumeHelper 'check'
    if (-not $status.available) {
        $null = & wsl.exe --mount --vhd $Vhdx --bare 2>&1
        if ($LASTEXITCODE -ne 0) { throw 'Cannot attach the existing monitoring VHDX.' }
    }
    if (-not $status.ready) { $null = Invoke-VolumeHelper 'mount' }
}
Write-Output 'Monitoring volume mounted with its expected native ext4 UUID.'
