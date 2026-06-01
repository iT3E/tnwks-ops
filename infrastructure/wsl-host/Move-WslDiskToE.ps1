# Move-WslDiskToE.ps1
#
# Relocates the Ubuntu WSL distro's ext4.vhdx (distro root + /var/lib/docker =
# the homelab-wsl Talos containers + every local-path PVC) off the near-full C:
# drive onto E:, using `wsl --manage <distro> --move`.
#
# Why --move (not export/import): in-place relocation, no double-size tar (the
# export/import path would need ~376 GiB peak on E: vs ~387 GiB free - too
# tight), and it preserves the distro config (default user, wsl.conf) so there
# is no post-move user-restore step. Requires a recent WSL; verified on 2.7.3.
#
# IMPORTANT: this shuts WSL down, so it MUST run from the Windows side, detached
# from any WSL shell (the shell that launches it will die at --shutdown). It
# logs every step to $LogPath so progress is observable from a fresh WSL session
# via `tail -f`.
#
# Run from an ELEVATED PowerShell (Docker/WSL relocation needs admin):
#   powershell -NoProfile -ExecutionPolicy Bypass -File Move-WslDiskToE.ps1
#
# Idempotent-ish: if the distro is already under $DestDir it exits early.
#
# NOTE: this file must stay pure ASCII - Windows PowerShell 5.1 mis-decodes
# non-ASCII bytes (em-dashes, box-drawing) and fails to parse.

[CmdletBinding()]
param(
    [string]$Distro  = 'Ubuntu',
    # New home for the distro's vhdx on E:.
    [string]$DestDir = 'E:\WSL\Ubuntu',
    # Safety floor: refuse to start unless E: has at least this many GiB free.
    # --move needs roughly the vhdx size free transiently; 220 GiB gives margin
    # over the current ~188 GiB used.
    [int]$MinFreeGiBOnDest = 220,
    [string]$LogPath = 'E:\WSL\move-wsl-disk.log'
)

$ErrorActionPreference = 'Stop'

function Log {
    param([string]$Msg)
    $ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    $line = "[$ts] $Msg"
    Write-Host $line
    Add-Content -Path $LogPath -Value $line
}

function Require-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Move-WslDiskToE.ps1 must be run as Administrator.'
    }
}

function Get-DistroBasePath {
    param([string]$Name)
    $key = Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss |
        Where-Object { $_.GetValue('DistributionName') -eq $Name } |
        Select-Object -First 1
    if (-not $key) { throw "WSL distro '$Name' not found in registry." }
    return $key.GetValue('BasePath')
}

# --- Setup -------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path (Split-Path $LogPath), $DestDir | Out-Null
Log "=== Move-WslDiskToE start: distro=$Distro dest=$DestDir ==="

Require-Admin

# --- Pre-flight --------------------------------------------------------------
$basePath = Get-DistroBasePath -Name $Distro
Log "Current BasePath: $basePath"

if ($basePath -like "$DestDir*") {
    Log "Distro already located under $DestDir - nothing to do. Exiting."
    return
}

$destDrive = (Split-Path -Qualifier $DestDir)            # e.g. 'E:'
$free = (Get-PSDrive -Name $destDrive.TrimEnd(':')).Free
$freeGiB = [math]::Round($free / 1GB, 1)
Log "Destination drive $destDrive free: $freeGiB GiB (floor: $MinFreeGiBOnDest GiB)"
if ($freeGiB -lt $MinFreeGiBOnDest) {
    throw "ABORT: $destDrive has only $freeGiB GiB free, below the $MinFreeGiBOnDest GiB floor. Not starting the move."
}

# Capture default user so we can assert it survived (move should preserve it).
$defaultUid = (Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss |
    Where-Object { $_.GetValue('DistributionName') -eq $Distro } |
    Select-Object -First 1).GetValue('DefaultUid')
Log "Current DefaultUid: $defaultUid"

# --- Quiesce -----------------------------------------------------------------
Log "Shutting WSL down (cluster goes offline now)..."
& wsl.exe --shutdown
Start-Sleep -Seconds 8
Log "Shutdown issued."

# --- Move --------------------------------------------------------------------
Log "Running: wsl --manage $Distro --move `"$DestDir`"  (this is the long step)"
$moveOut = & wsl.exe --manage $Distro --move "$DestDir" 2>&1 | Out-String
Log "wsl --move output: $($moveOut.Trim())"

# wsl.exe returns non-zero on failure; surface it.
if ($LASTEXITCODE -ne 0) {
    throw "ABORT: 'wsl --manage $Distro --move' exited $LASTEXITCODE. Distro should be intact at its original location; re-run after resolving."
}

# --- Verify new location -----------------------------------------------------
$newBase = Get-DistroBasePath -Name $Distro
Log "New BasePath: $newBase"
if ($newBase -notlike "$DestDir*") {
    throw "ABORT: post-move BasePath '$newBase' is not under '$DestDir'. Investigate before proceeding."
}
Log "Disk relocation confirmed under $DestDir."

# --- Restart the cluster -----------------------------------------------------
# Booting the distro restarts systemd; the Talos node containers have
# restart_policy 'no' (per the bootstrap playbook), so start any that are down.
Log "Booting distro + restarting Talos node containers..."
$restart = @'
set -e
# wait for docker
for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 2; done
# start any talos-owned containers that aren't running
ids=$(docker ps -aq --filter label=talos.owned=true --filter status=exited)
[ -n "$ids" ] && docker start $ids || true
docker ps --filter label=talos.owned=true --format '{{.Names}} {{.Status}}'
'@
$restartOut = & wsl.exe -d $Distro -- bash -lc $restart 2>&1 | Out-String
Log "Container restart output:`n$($restartOut.Trim())"

# --- Verify cluster ----------------------------------------------------------
Log "Waiting for nodes to be Ready..."
$nodesOut = & wsl.exe -d $Distro -- bash -lc 'for i in $(seq 1 60); do kubectl get nodes --no-headers 2>/dev/null | grep -q " Ready " && break; sleep 5; done; kubectl get nodes 2>&1' | Out-String
Log "kubectl get nodes:`n$($nodesOut.Trim())"

$ksOut = & wsl.exe -d $Distro -- bash -lc 'kubectl get pods -n storage -o wide 2>&1; echo ---; kubectl get pods -n production -l app.kubernetes.io/name=sites 2>&1; echo ---; kubectl get pods -n tools 2>&1 | grep -E "runner|listener" || true' | Out-String
Log "Sites platform pods:`n$($ksOut.Trim())"

# --- Confirm C: reclaimed ----------------------------------------------------
$cFree = [math]::Round((Get-PSDrive -Name C).Free / 1GB, 1)
Log "C: free now: $cFree GiB"

Log "=== Move-WslDiskToE COMPLETE. Old vhdx removed from $basePath by --move. ==="
Log "If anything above looks wrong, the distro is at $newBase and bootable; investigate before relying on the cluster."
