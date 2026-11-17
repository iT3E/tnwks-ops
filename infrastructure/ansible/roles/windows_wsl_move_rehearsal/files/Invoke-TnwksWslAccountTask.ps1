<#
.SYNOPSIS
The steps that an account runs for itself in the move of a distro (Design Doc
0004, section 9, step 3 and the window). Each S4U task of the account runs one
step. The window procedure (Invoke-TnwksMoveWindow.ps1, as SYSTEM) acts as an
account only through these tasks, because WSL refuses SYSTEM.

.DESCRIPTION
Each step reads its config (a JSON file that the play writes) and writes one
JSON result, <log>\<step>.json, with a new file and one rename. Exit 0: the
step passed, or had nothing to do. Exit 2: the step refused and changed
nothing. Exit 1: the step failed.

  probe          whoami /groups: the integrity level and the groups of the
                 token. The first run of a task of the account makes its
                 profile.
  import-rootfs  wsl --import of the distro from the pinned root file system
                 into the folder of the account, and the files of the
                 rehearsal in the distro. When the account has the distro at
                 that path already, no import: the files are written again.
                 Refused, with no change, when it lists it at another path.
  import         wsl --import-in-place of the disk file in the folder of the
                 account. Nothing when the account lists the distro at that
                 path; refused, with no change, when it lists it at another
                 path. Sets OOBEComplete before, and after it writes the values
                 of the registration of the config and the default distro.
  stop           wsl --terminate of the distro, then wsl --shutdown: the VM of
                 this account only (WSL keeps one session for each account).
  unregister     wsl --unregister of the distro. Refused, with no change,
                 while a file is at the path of its registration. No distro is
                 a pass.
  check          Reads, from the distro and the registry of the account.
  wslconf        As root in the distro: /etc/wsl.conf names the default user.
  filetest       In the folder of the account: make, replace and delete a file
                 of its own, then try to remove the marker of the procedure.

Every name and path must fit the scope of the config: a distro name that does
not match its pattern, or a path outside its root, stops the step before any
action.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Config,
    [Parameter(Mandatory)][ValidateSet('probe', 'import-rootfs', 'import', 'stop', 'unregister', 'check', 'wslconf', 'filetest')][string]$Step,
    # import: a folder other than the folder of the account (a test that the
    # import refuses a second path). It must still be in the scope.
    [string]$Folder
)
$ErrorActionPreference = 'Stop'
# wsl.exe writes UTF-16 to a pipe unless this is set.
$env:WSL_UTF8 = '1'
try { [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false } catch { }
$system = Join-Path $env:SystemRoot 'System32'
$lxssKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'

$cfg = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json

function Write-Result([hashtable]$Data) {
    $Data.step = $Step
    $Data.time = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $name = if ($Folder) { "$Step-elsewhere" } else { $Step }
    $path = Join-Path $cfg.log_dir "$name.json"
    $temp = "$path.new"
    [IO.File]::WriteAllText($temp, ($Data | ConvertTo-Json -Compress -Depth 5), (New-Object Text.UTF8Encoding $false))
    Move-Item -LiteralPath $temp -Destination $path -Force
}

# The scope: a distro name that matches the pattern of the config, and a path
# under its root. The live distro and its folder never fit it.
function Test-TnwksScope([string]$Distro, [string]$Path) {
    if ($Distro -notmatch $cfg.name_pattern) { return "the distro $Distro is outside the scope ($($cfg.name_pattern))" }
    foreach ($live in @($cfg.refused_names)) { if ($Distro -eq $live) { return "the distro $Distro is a refused name" } }
    if ($Path) {
        $sep = [IO.Path]::DirectorySeparatorChar
        $full = [IO.Path]::GetFullPath($Path).TrimEnd($sep)
        $root = [IO.Path]::GetFullPath($cfg.root).TrimEnd($sep)
        if (-not $full.StartsWith($root + $sep, [StringComparison]::OrdinalIgnoreCase)) { return "the path $Path is outside $root" }
    }
    ''
}

function Invoke-Wsl([string[]]$Arguments) {
    # Windows PowerShell 5.1 makes each line that a program writes to stderr an
    # error record, which stops the script under Stop. wsl.exe writes its
    # warnings there, so they are kept as text in the output.
    $ErrorActionPreference = 'Continue'
    $out = (& (Join-Path $system 'wsl.exe') @Arguments 2>&1 | ForEach-Object { "$_" } | Out-String).Trim()
    @{ exit = $LASTEXITCODE; output = $out }
}

# Runs a shell script in the distro. The text never passes as an argument of
# its own: Windows PowerShell 5.1 does not escape a double quote inside an
# argument to a native program, so wsl.exe would split the text at each
# quote, and sh would then drop each backslash. The script goes as base64
# (letters, digits, +, / and =) in one fixed command with no quote and no
# backslash. sh decodes it to a new private file (mktemp, mode 0600) and runs
# that file with -e; the file removes itself first. A command line holds at
# most 32767 characters, and base64 of a script that writes a file as base64
# is about 1.8 times the size of the file: a longer command does not run.
function Invoke-WslScript([string]$Script, [switch]$Root) {
    $text = 'rm -f "$0"' + "`n" + ($Script -replace "`r`n", "`n")
    if (-not $text.EndsWith("`n")) { $text += "`n" }
    $b64 = [Convert]::ToBase64String((New-Object Text.UTF8Encoding $false).GetBytes($text))
    $command = 'f=$(mktemp) && echo {0} | base64 -d > $f && exec /bin/sh -e $f' -f $b64
    $user = if ($Root) { @('-u', 'root') } else { @() }
    $arguments = @('-d', $distro) + $user + @('-e', '/bin/sh', '-c', $command)
    $length = (Join-Path $system 'wsl.exe').Length + (($arguments | ForEach-Object { $_.Length + 3 }) | Measure-Object -Sum).Sum
    if ($length -gt 32000) { return @{ exit = 1; output = "the script is too long for one command line ($length characters)" } }
    Invoke-Wsl $arguments
}

# The shell line that writes a file in the distro with exactly this text, and
# a newline at its end.
function Get-WslWriteLine([string]$Path, [string]$Text) {
    $body = $Text -replace "`r`n", "`n"
    if (-not $body.EndsWith("`n")) { $body += "`n" }
    'echo {0} | base64 -d > {1}' -f [Convert]::ToBase64String((New-Object Text.UTF8Encoding $false).GetBytes($body)), $Path
}

# The values of a read in the distro, one key=value line each, by key: a line
# that a value runs into does not move the others. A key that comes twice (a
# value with a newline in it that names a key) reads as no value, and is
# listed in repeated.
function ConvertFrom-WslValues([string]$Output) {
    $values = @{}
    $repeated = @()
    foreach ($line in @($Output -split "`r?`n")) {
        if ($line -notmatch '^([a-z_0-9]+)=(.*)$') { continue }
        $key = $Matches[1]
        if ($values.ContainsKey($key)) {
            if ($repeated -notcontains $key) { $repeated += $key }
            $values[$key] = $null
        } else {
            $values[$key] = $Matches[2].Trim()
        }
    }
    [pscustomobject]@{ values = $values; repeated = $repeated }
}

# The registration of the distro in the registry of this account, or $null.
function Get-TnwksRegistration([string]$Distro) {
    if (-not (Test-Path -LiteralPath $lxssKey)) { return $null }
    foreach ($key in @(Get-ChildItem -LiteralPath $lxssKey)) {
        $p = Get-ItemProperty -LiteralPath $key.PSPath
        if ($p.DistributionName -eq $Distro) {
            $base = "$($p.BasePath)" -replace '^\\\\\?\\', ''
            $vhd = if ($p.VhdFileName) { "$($p.VhdFileName)" } else { 'ext4.vhdx' }
            return [pscustomobject]@{ guid = $key.PSChildName; base = $base; disk = (Join-Path $base $vhd); values = $p }
        }
    }
    $null
}

# Whether a file is at a path, read so that it fails closed: only "not found"
# reads as no file. When the account may not read the attributes, Test-Path
# throws under the Stop preference and reads $false with its errors silenced,
# and WSL deletes the file at the path of a registration.
function Test-TnwksFile([string]$Path) {
    try {
        [void][IO.File]::GetAttributes($Path)
        return $true
    } catch {
        $e = $_.Exception
        while ($e -is [Management.Automation.MethodInvocationException] -and $e.InnerException) { $e = $e.InnerException }
        return -not ($e -is [IO.FileNotFoundException] -or $e -is [IO.DirectoryNotFoundException])
    }
}

function Exit-Tnwks([int]$Code, [hashtable]$Data) {
    Write-Result $Data
    exit $Code
}

$distro = $cfg.distro
# Not a variable named folder: PowerShell names are not case sensitive, so it
# would change the parameter Folder.
$diskDir = if ($Folder) { $Folder } else { $cfg.folder }
$target = Join-Path $diskDir $cfg.disk_name
$scope = Test-TnwksScope $distro $target
if ($scope) { Exit-Tnwks 2 @{ refused = $scope } }

switch ($Step) {
    'probe' {
        $rows = @(& (Join-Path $system 'whoami.exe') /groups /fo csv | ConvertFrom-Csv)
        $label = @($rows | Where-Object { $_.SID -like 'S-1-16-*' } | ForEach-Object { $_.SID })
        $admins = @($rows | Where-Object { $_.SID -eq 'S-1-5-32-544' })
        $user = @(& (Join-Path $system 'whoami.exe') /user /fo csv | ConvertFrom-Csv)[0].SID
        Exit-Tnwks 0 @{
            # S-1-16-8192 is Medium Mandatory Level.
            medium = ($label.Count -eq 1 -and $label[0] -eq 'S-1-16-8192')
            integrity_sids = $label -join ','
            administrators = ($admins.Count -gt 0)
            user = $user
            hive = [bool](Test-Path -LiteralPath "Registry::HKEY_USERS\$user")
            profile = $env:USERPROFILE
        }
    }
    'import-rootfs' {
        $reg = Get-TnwksRegistration $distro
        $existed = [bool]$reg
        if ($reg -and -not [string]::Equals($reg.disk, $target, [StringComparison]::OrdinalIgnoreCase)) {
            Exit-Tnwks 2 @{ refused = "the account lists $distro at $($reg.disk), not at $target"; disk = $reg.disk }
        }
        if (-not $reg) {
            $hash = (Get-FileHash -LiteralPath $cfg.rootfs -Algorithm SHA256).Hash
            if ($hash -ne $cfg.rootfs_sha256) { Exit-Tnwks 1 @{ failed = "the root file system has the SHA-256 $hash, not the pinned one" } }
            if (-not (Test-Path -LiteralPath $lxssKey)) { New-Item -Path $lxssKey -Force | Out-Null }
            Set-ItemProperty -LiteralPath $lxssKey -Name OOBEComplete -Value 1 -Type DWord
            $run = Invoke-Wsl @('--import', $distro, $diskDir, $cfg.rootfs, '--version', '2')
            if ($run.exit -ne 0) { Exit-Tnwks 1 @{ failed = "wsl --import exited $($run.exit)"; output = $run.output } }
        }
        # The files of the rehearsal: a token that shows the data after the
        # move, a user of UID 1000, and a shortcut with an icon (as the distro
        # of site 2 has), so that the import in place writes the icon. The
        # check step reads each one back. A second run writes them again.
        $script = @(
            ('id -u {0} >/dev/null 2>&1 || adduser -D -u 1000 {0}' -f $cfg.linux_user),
            (Get-WslWriteLine '/etc/tnwks-move-token' $cfg.data_token),
            'mkdir -p /usr/share/tnwks-move',
            ('echo {0} | base64 -d > /usr/share/tnwks-move/icon.ico' -f $cfg.icon_base64),
            (Get-WslWriteLine '/etc/wsl-distribution.conf' $cfg.distribution_conf)
        ) -join "`n"
        $in = Invoke-WslScript $script -Root
        $stop = Invoke-Wsl @('--terminate', $distro)
        Exit-Tnwks ([int]($in.exit -ne 0)) @{ imported = (-not $existed); existed = $existed; prepare_exit = $in.exit; prepare = $in.output; terminate_exit = $stop.exit }
    }
    'import' {
        $reg = Get-TnwksRegistration $distro
        if ($reg) {
            if ([string]::Equals($reg.disk, $target, [StringComparison]::OrdinalIgnoreCase)) { Exit-Tnwks 0 @{ existed = $true; disk = $reg.disk } }
            Exit-Tnwks 2 @{ refused = "the account lists $distro at $($reg.disk), not at $target"; disk = $reg.disk }
        }
        if (-not (Test-TnwksFile $target)) { Exit-Tnwks 1 @{ failed = "no disk file at $target" } }
        # No first-run program of WSL for this account.
        if (-not (Test-Path -LiteralPath $lxssKey)) { New-Item -Path $lxssKey -Force | Out-Null }
        Set-ItemProperty -LiteralPath $lxssKey -Name OOBEComplete -Value 1 -Type DWord
        $run = Invoke-Wsl @('--import-in-place', $distro, $target)
        $reg = Get-TnwksRegistration $distro
        if ($run.exit -ne 0 -or -not $reg) { Exit-Tnwks 1 @{ failed = "wsl --import-in-place exited $($run.exit)"; output = $run.output } }
        # The default user that the import gave, before the values are written.
        $uid = Invoke-Wsl @('-d', $distro, '-e', '/usr/bin/id', '-u')
        foreach ($p in @($cfg.registration_values.PSObject.Properties)) {
            Set-ItemProperty -LiteralPath $reg.values.PSPath -Name $p.Name -Value ([int]$p.Value) -Type DWord
        }
        Set-ItemProperty -LiteralPath $lxssKey -Name DefaultDistribution -Value $reg.guid
        Exit-Tnwks 0 @{ imported = $true; disk = $reg.disk; guid = $reg.guid; uid_after_import = $uid.output; uid_exit = $uid.exit }
    }
    'stop' {
        $terminate = if (Get-TnwksRegistration $distro) { Invoke-Wsl @('--terminate', $distro) } else { @{ exit = 0; output = 'no distro' } }
        $shutdown = Invoke-Wsl @('--shutdown')
        Exit-Tnwks ([int]($shutdown.exit -ne 0)) @{ terminate_exit = $terminate.exit; shutdown_exit = $shutdown.exit }
    }
    'unregister' {
        $reg = Get-TnwksRegistration $distro
        if (-not $reg) { Exit-Tnwks 0 @{ registered = $false; note = 'no distro' } }
        $scope = Test-TnwksScope $distro $reg.disk
        if ($scope) { Exit-Tnwks 2 @{ refused = $scope } }
        # WSL deletes the disk file at the path of the registration: never
        # while a file is there.
        if (Test-TnwksFile $reg.disk) {
            Exit-Tnwks 2 @{ refused = "a file is at $($reg.disk), the path of the registration, or the account can not read it"; registered = $true }
        }
        $run = Invoke-Wsl @('--unregister', $distro)
        $after = Get-TnwksRegistration $distro
        Exit-Tnwks ([int]($run.exit -ne 0 -or [bool]$after)) @{ unregister_exit = $run.exit; output = $run.output; registered = [bool]$after }
    }
    'check' {
        $reg = Get-TnwksRegistration $distro
        $data = @{ registered = [bool]$reg }
        $root = Get-ItemProperty -LiteralPath $lxssKey -ErrorAction SilentlyContinue
        $data.oobe_complete = $root.OOBEComplete
        if ($reg) {
            $data.disk = $reg.disk
            $data.default_distribution = ($root.DefaultDistribution -eq $reg.guid)
            $data.default_uid = $reg.values.DefaultUid
            # As the default user. A file that is not there reads as an empty
            # value; a file with no newline at its end changes no other value.
            $read = Invoke-WslScript (@(
                'printf ''uid=%s\n'' "$(id -u)"',
                ('printf ''user_uid=%s\n'' "$(id -u {0} 2>/dev/null)"' -f $cfg.linux_user),
                'printf ''token=%s\n'' "$(cat /etc/tnwks-move-token 2>/dev/null)"',
                'printf ''mem_total_kib=%s\n'' "$(awk ''/^MemTotal:/ {print $2}'' /proc/meminfo)"',
                'printf ''distribution_conf_sha256=%s\n'' "$(sha256sum 2>/dev/null < /etc/wsl-distribution.conf | cut -d '' '' -f 1)"',
                'printf ''wsl_conf_sha256=%s\n'' "$(sha256sum 2>/dev/null < /etc/wsl.conf | cut -d '' '' -f 1)"',
                'printf ''icon_sha256=%s\n'' "$(sha256sum 2>/dev/null < /usr/share/tnwks-move/icon.ico | cut -d '' '' -f 1)"'
            ) -join "`n")
            $data.read_exit = $read.exit
            $parsed = ConvertFrom-WslValues $read.output
            $values = $parsed.values
            # A count and one text: Windows PowerShell 5.1 may write an array
            # as {"value": [...], "Count": n}, and the play only counts.
            $data.repeated_count = @($parsed.repeated).Count
            $data.repeated_keys = @($parsed.repeated) -join ','
            foreach ($key in @('uid', 'user_uid', 'token', 'distribution_conf_sha256', 'wsl_conf_sha256', 'icon_sha256')) { $data[$key] = $values[$key] }
            $data.mem_total_kib = if ($values.mem_total_kib -match '^[0-9]+$') { [int64]$values.mem_total_kib } else { $null }
        }
        Exit-Tnwks 0 $data
    }
    'wslconf' {
        $run = Invoke-WslScript (Get-WslWriteLine '/etc/wsl.conf' $cfg.wsl_conf) -Root
        Exit-Tnwks ([int]($run.exit -ne 0)) @{ exit = $run.exit; output = $run.output }
    }
    'filetest' {
        # The right on the folder: make files only; CREATOR OWNER lets the
        # account replace and delete a file that it made, and no other file.
        $own = Join-Path $diskDir 'tnwks-move-filetest.tmp'
        $marker = Join-Path $diskDir $cfg.marker_name
        $result = @{}
        try { [IO.File]::WriteAllText($own, 'one'); $result.make = $true } catch { $result.make = $false; $result.make_error = $_.Exception.Message }
        try { [IO.File]::WriteAllText($own, 'two'); $result.replace = $true } catch { $result.replace = $false }
        try { Remove-Item -LiteralPath $own -Force; $result.delete_own = $true } catch { $result.delete_own = $false }
        try { Remove-Item -LiteralPath $marker -Force; $result.delete_marker = $true } catch { $result.delete_marker = $false }
        $result.marker_there = [bool](Test-Path -LiteralPath $marker)
        $pass = $result.make -and $result.replace -and $result.delete_own -and -not $result.delete_marker -and $result.marker_there
        Exit-Tnwks ([int](-not $pass)) $result
    }
}
exit 0
