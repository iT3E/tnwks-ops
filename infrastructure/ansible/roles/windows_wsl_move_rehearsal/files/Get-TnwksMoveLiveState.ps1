<#
.SYNOPSIS
A read of the live parts that the rehearsal of the move must not touch: the
VM processes, the port proxy, the NAT networks, and the registrations of the
distros of the account of the host's person (from its loaded hive, read only),
as a count and a SHA-256, so the output names no account or path. It changes
nothing.
#>
[CmdletBinding()]
param([Parameter(Mandatory)][string]$PresentUser)
$ErrorActionPreference = 'Stop'
$vm = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'vmmem*' } | ForEach-Object {
    [pscustomobject]@{ id = [int]$_.ProcessId; name = $_.Name; created = $(if ($_.CreationDate) { $_.CreationDate.ToUniversalTime().ToString('o') } else { '' }) } })
$proxy = (& { $ErrorActionPreference = 'Continue'; netsh.exe interface portproxy show all 2>&1 | ForEach-Object { "$_".Trim() } } |
    Where-Object { $_ -match '^\d' } | Sort-Object) -join ' | '
$nat = @(Get-NetNat -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Name) $($_.InternalIPInterfaceAddressPrefix)" } | Sort-Object) -join ' | '
$registrations = 'not read: the hive is not loaded'
$user = Get-LocalUser -Name $PresentUser -ErrorAction SilentlyContinue
if ($user) {
    $lxss = "Registry::HKEY_USERS\$($user.SID.Value)\Software\Microsoft\Windows\CurrentVersion\Lxss"
    if (Test-Path -LiteralPath $lxss) {
        $rows = @(Get-ChildItem -LiteralPath $lxss | ForEach-Object {
            $p = Get-ItemProperty -LiteralPath $_.PSPath
            if ($p.DistributionName) { "$($_.PSChildName) $($p.DistributionName) $($p.BasePath) $($p.VhdFileName) $($p.DefaultUid) $($p.Flags)" } } | Sort-Object)
        $sha = [Security.Cryptography.SHA256]::Create()
        $hash = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($rows -join "`n")) | ForEach-Object { $_.ToString('x2') })
        $registrations = "$($rows.Count) registration(s), sha256 $hash"
    }
}
[pscustomobject]@{ vm = $vm; portproxy = $proxy; nat = $nat; registrations = $registrations }
