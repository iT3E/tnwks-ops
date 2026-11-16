<#
.SYNOPSIS
One step of the rehearsal of the account change (role windows_wsl_rehearsal),
as the temporary account, from its own S4U task with no elevation.

.DESCRIPTION
Each step writes one JSON document, <LogDir>\<step>.json, with a new file and
one rename, and exits 0 when the step ran. Whether a check passes is for
playbooks/windows-rehearsal-check.yml to say, from the document.

  probe   whoami /groups: the integrity level and the groups of the token of
          this task. Also whether the registry hive of the account is loaded
          (WSL keeps the list of distros of an account there), its profile
          folder, and the distros that WSL lists for it. The first run of a
          task of the account also makes its profile.
  import  wsl --import of the distro, from the root file system, after a check
          of its SHA-256, into %LOCALAPPDATA%\<distro>. Nothing when the account
          has the distro already.
  write   As root in the distro, one try to make a file in the textfile
          directory of the host (write-test.sh), which removes a file that it
          made.
  mount   wsl --mount --vhd <disk> --bare, then wsl --unmount <disk>.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('probe', 'import', 'write', 'mount')][string]$Step,
    [Parameter(Mandatory)][string]$LogDir,
    [string]$Distro,
    [string]$RootFs,
    [string]$Sha256,
    [string]$WriteTestDir,
    [string]$Disk
)
$ErrorActionPreference = 'Stop'
# wsl.exe writes UTF-16 to a pipe unless this is set.
$env:WSL_UTF8 = '1'
try { [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false } catch { }
$system = Join-Path $env:SystemRoot 'System32'

function Write-Result([hashtable]$Data) {
    $Data.step = $Step
    $Data.time = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $path = Join-Path $LogDir "$Step.json"
    $temp = "$path.new"
    [IO.File]::WriteAllText($temp, ($Data | ConvertTo-Json -Compress -Depth 4), (New-Object Text.UTF8Encoding $false))
    Move-Item -LiteralPath $temp -Destination $path -Force
}

function Invoke-Wsl([string[]]$Arguments) {
    # Windows PowerShell 5.1 makes each line that a program writes to stderr an
    # error record, which stops the script under Stop. wsl.exe writes its
    # warnings there, so they are kept as text in the output.
    $ErrorActionPreference = 'Continue'
    $out = (& (Join-Path $system 'wsl.exe') @Arguments 2>&1 | ForEach-Object { "$_" } | Out-String).Trim()
    @{ exit = $LASTEXITCODE; output = $out }
}

function Get-Distros {
    $list = Invoke-Wsl @('--list', '--quiet')
    @($list.output -split "`r?`n" | ForEach-Object { $_.Trim([char]0, ' ') } | Where-Object { $_ })
}

switch ($Step) {
    'probe' {
        $rows = @(& (Join-Path $system 'whoami.exe') /groups /fo csv | ConvertFrom-Csv)
        $label = @($rows | Where-Object { $_.SID -like 'S-1-16-*' } | ForEach-Object { $_.SID })
        $admins = @($rows | Where-Object { $_.SID -eq 'S-1-5-32-544' } | ForEach-Object { $_.Attributes })
        $user = @(& (Join-Path $system 'whoami.exe') /user /fo csv | ConvertFrom-Csv)[0].SID
        Write-Result @{
            integrity_sids = $label
            # S-1-16-8192 is Medium Mandatory Level.
            medium = ($label.Count -eq 1 -and $label[0] -eq 'S-1-16-8192')
            administrators = $admins
            user = $user
            hive = [bool](Test-Path -LiteralPath "Registry::HKEY_USERS\$user")
            profile = $env:USERPROFILE
            distros = @(Get-Distros)
        }
    }
    'import' {
        $before = Get-Distros
        if ($before -contains $Distro) {
            Write-Result @{ exit = 0; existed = $true; distros = $before }
            break
        }
        $hash = (Get-FileHash -LiteralPath $RootFs -Algorithm SHA256).Hash
        if ($hash -ne $Sha256) {
            Write-Result @{ exit = -1; existed = $false; output = "the root file system has the SHA-256 $hash, not the pinned one" }
            break
        }
        $target = Join-Path $env:LOCALAPPDATA $Distro
        $run = Invoke-Wsl @('--import', $Distro, $target, $RootFs, '--version', '2')
        Write-Result @{ exit = $run.exit; existed = $false; output = $run.output; distros = @(Get-Distros) }
    }
    'write' {
        # C:\Program Files\x -> /mnt/c/Program Files/x, the automount of drvfs.
        $linux = '/mnt/' + $WriteTestDir.Substring(0, 1).ToLowerInvariant() + ($WriteTestDir.Substring(2) -replace '\\', '/')
        $script = '/mnt/' + $PSScriptRoot.Substring(0, 1).ToLowerInvariant() + ($PSScriptRoot.Substring(2) -replace '\\', '/') + '/write-test.sh'
        $run = Invoke-Wsl @('-d', $Distro, '-u', 'root', '-e', '/bin/sh', $script, $linux)
        Write-Result @{ exit = $run.exit; output = $run.output; directory = $linux }
    }
    'mount' {
        $mount = Invoke-Wsl @('--mount', '--vhd', $Disk, '--bare')
        $unmount = Invoke-Wsl @('--unmount', $Disk)
        Write-Result @{ mount_exit = $mount.exit; mount_output = $mount.output; unmount_exit = $unmount.exit; unmount_output = $unmount.output }
    }
}
exit 0
