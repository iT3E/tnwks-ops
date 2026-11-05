# Install-TnwksWindowsSshd.ps1 -PublicKeyFile <path> | -PublicKey <key>[,<key>...]
#                              [-AdminUser <name>] [-Check]
#
# Bootstrap for managing this Windows host with Ansible (docs/wsl/windows-ansible.md):
# the built-in OpenSSH server, reachable only from WSL. The role windows_sshd
# runs this same file through win_powershell, so the nightly check reports
# drift from it.
#
#   - OpenSSH.Server capability installed, sshd Automatic (delayed) and running,
#     restarted by the service manager when it fails
#   - DefaultShell = Windows PowerShell (what Ansible's powershell shell expects)
#   - sshd_config: ListenAddress = the vEthernet (WSL) host IP, keys only,
#     AllowUsers = the admin user
#   - administrators_authorized_keys = the key(s) in -PublicKeyFile or
#     -PublicKey, ACL Administrators + SYSTEM only (sshd ignores the file
#     otherwise)
#   - firewall: tcp/22 from the WSL subnet only; the capability's own
#     OpenSSH-Server-In-TCP rule (any address) is removed
#
# Idempotent: only what differs from this file is changed. -Check reports
# drift, changes nothing and exits 2 when something differs (under Ansible:
# reports changed instead of exiting).
#
# Run elevated: `task wsl-host:sshd` (or `task wsl-host:sshd-check`).
#
# The vEthernet (WSL) adapter only exists while WSL runs, so after a host boot
# sshd can start before its ListenAddress exists and exit with "Cannot bind
# any address". The service's failure actions restart it every 30-60s until
# the address is there. Re-run this script if the WSL host IP ever changes.

[CmdletBinding()]
param(
    [string]$PublicKeyFile,
    [string[]]$PublicKey,
    [string]$AdminUser,
    [switch]$Check,
    # Default: the address of the vEthernet (WSL) adapter, e.g. 172.26.80.1.
    [string]$ListenAddress
)

$ErrorActionPreference = 'Stop'
# Invoked from the repo, the working directory is a \\wsl.localhost UNC path.
Set-Location $env:TEMP

$Capability = 'OpenSSH.Server~~~~0.0.1.0'
$SshDir = Join-Path $env:ProgramData 'ssh'
$SshdConfig = Join-Path $SshDir 'sshd_config'
$AdminKeys = Join-Path $SshDir 'administrators_authorized_keys'
$Shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$RuleName = 'tnwks-sshd (WSL)'
$DefaultRuleName = 'OpenSSH-Server-In-TCP'
$SidAdmins = 'S-1-5-32-544'
$SidSystem = 'S-1-5-18'

$script:drift = 0
function Say([string]$msg) { Write-Host $msg }
function Drift([string]$msg) {
    $script:drift++
    if ($Check) { Write-Host "DRIFT  $msg" } else { Write-Host "CHANGE $msg" }
}

# Native commands write to stderr; under 'Stop' Windows PowerShell 5.1 turns
# that into a terminating error, so run them with 'Continue' and check the code.
function Invoke-Native([string]$exe, [string[]]$argv) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $out = & $exe @argv 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $old }
    return @{ Code = $LASTEXITCODE; Output = ($out -join "`n") }
}

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Install-TnwksWindowsSshd.ps1 must be run as Administrator.'
}
if (-not $AdminUser) { $AdminUser = $env:USERNAME }
$AdminUser = $AdminUser.ToLower()
# Under Ansible (win_powershell) there is an $Ansible object, and `exit` would
# end the module instead of reporting.
$UnderAnsible = [bool](Get-Variable -Name Ansible -ErrorAction SilentlyContinue)
if ($PublicKeyFile) { $PublicKey = @(((Get-Content -Raw $PublicKeyFile) -replace "`r", '').Trim()) }
if (-not $PublicKey) { throw '-PublicKeyFile <path> or -PublicKey <key> (the Ansible public keys) is required' }
foreach ($k in $PublicKey) {
    if ($k -notmatch '^ssh-ed25519 \S+') { throw "not an ssh-ed25519 public key: $k" }
}

function Get-WslAddress {
    $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceAlias -like 'vEthernet (WSL*' } | Select-Object -First 1
    if (-not $ip) { throw 'no vEthernet (WSL) adapter (is WSL running?); pass -ListenAddress and -WslSubnet' }
    return $ip
}
$wslIp = Get-WslAddress
if (-not $ListenAddress) { $ListenAddress = $wslIp.IPAddress }
$bytes = ([Net.IPAddress]$wslIp.IPAddress).GetAddressBytes()
[Array]::Reverse($bytes)
$mask = [uint32]([math]::Pow(2, 32) - [math]::Pow(2, 32 - $wslIp.PrefixLength))
$net = [BitConverter]::GetBytes([BitConverter]::ToUInt32($bytes, 0) -band $mask)
[Array]::Reverse($net)
$subnet = '{0}/{1}' -f ([Net.IPAddress]$net).IPAddressToString, $wslIp.PrefixLength
$maskBytes = [BitConverter]::GetBytes($mask)
[Array]::Reverse($maskBytes)
$subnetDotted = '{0}/{1}' -f ([Net.IPAddress]$net).IPAddressToString, ([Net.IPAddress]$maskBytes).IPAddressToString

# --- capability --------------------------------------------------------------
$cap = Get-WindowsCapability -Online -Name $Capability
if ($cap.State -ne 'Installed') {
    Drift "capability $Capability ($($cap.State) -> Installed)"
    if (-not $Check) {
        Add-WindowsCapability -Online -Name $Capability | Out-Null
    }
}
$sshdExe = Join-Path $env:SystemRoot 'System32\OpenSSH\sshd.exe'

# --- DefaultShell --------------------------------------------------------------
$shellNow = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -ErrorAction SilentlyContinue).DefaultShell
if ($shellNow -ne $Shell) {
    Drift "HKLM:\SOFTWARE\OpenSSH DefaultShell '$shellNow' -> $Shell"
    if (-not $Check) {
        New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
        New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value $Shell -PropertyType String -Force | Out-Null
    }
}

# --- sshd_config ---------------------------------------------------------------
# The header below names this script's old path (infrastructure/wsl-host/). It
# is part of the file content, so changing it means rewriting sshd_config and
# restarting sshd; leave it until there is another reason to restart.
$configWanted = @"
# Managed by tnwks-ops infrastructure/wsl-host/Install-TnwksWindowsSshd.ps1.
# Edits here are reported as drift and overwritten by the next run.
Port 22
AddressFamily inet
ListenAddress $ListenAddress
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
AllowUsers $AdminUser
AllowAgentForwarding no
AllowTcpForwarding no
GatewayPorts no
PermitTunnel no
AuthorizedKeysFile .ssh/authorized_keys
SyslogFacility LOCAL0
LogLevel INFO
Subsystem sftp sftp-server.exe

Match Group administrators
       AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys
"@ -replace "`r", ''
$configWanted += "`n"
$configNow = if (Test-Path $SshdConfig) { (Get-Content -Raw $SshdConfig) -replace "`r", '' } else { '' }
$configChanged = $false
if ($configNow -ne $configWanted) {
    Drift "$SshdConfig (ListenAddress $ListenAddress, keys only, AllowUsers $AdminUser)"
    if (-not $Check) {
        New-Item -ItemType Directory -Force -Path $SshDir | Out-Null
        $tmp = Join-Path $SshDir 'sshd_config.tnwks-new'
        [IO.File]::WriteAllText($tmp, $configWanted)
        # sshd -t needs host keys, which the service creates on its first start.
        if ((Test-Path $sshdExe) -and (Get-ChildItem $SshDir -Filter 'ssh_host_*_key' -ErrorAction SilentlyContinue)) {
            $t = Invoke-Native $sshdExe @('-t', '-f', $tmp)
            if ($t.Code -ne 0) { Remove-Item $tmp -Force; throw "sshd -t rejected the new config: $($t.Output)" }
        }
        Move-Item -Force $tmp $SshdConfig
        $configChanged = $true
    }
}

# --- administrators_authorized_keys ------------------------------------------------
$keysWanted = ($PublicKey -join "`n") + "`n"
$keysNow = if (Test-Path $AdminKeys) { (Get-Content -Raw $AdminKeys) -replace "`r", '' } else { '' }
if ($keysNow -ne $keysWanted) {
    Drift "$AdminKeys ($($PublicKey.Count) key(s): $(($PublicKey | ForEach-Object { ($_ -split ' ')[2] }) -join ', '))"
    if (-not $Check) { [IO.File]::WriteAllText($AdminKeys, $keysWanted) }
}
if (Test-Path $AdminKeys) {
    $acl = Get-Acl $AdminKeys
    $sids = @(@($acl.Access | ForEach-Object {
                $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value + ':' + $_.FileSystemRights + ':' + $_.AccessControlType
            }) | Sort-Object)
    $aclOk = $acl.AreAccessRulesProtected -and $sids.Count -eq 2 -and
        ($sids -join ',') -eq "${SidSystem}:FullControl:Allow,${SidAdmins}:FullControl:Allow"
    if (-not $aclOk) {
        Drift "$AdminKeys ACL ($($sids -join ', ')) -> Administrators + SYSTEM only"
        if (-not $Check) {
            $r = Invoke-Native 'icacls.exe' @($AdminKeys, '/inheritance:r', '/grant', "*${SidAdmins}:F", '/grant', "*${SidSystem}:F")
            if ($r.Code -ne 0) { throw "icacls failed: $($r.Output)" }
            # /grant adds; drop anything else that was granted explicitly.
            $acl = Get-Acl $AdminKeys
            foreach ($ace in @($acl.Access)) {
                $sid = $ace.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
                if ($sid -notin @($SidAdmins, $SidSystem)) { [void]$acl.RemoveAccessRuleAll($ace) }
            }
            Set-Acl -Path $AdminKeys -AclObject $acl
        }
    }
} elseif ($Check) {
    Drift "$AdminKeys ACL -> Administrators + SYSTEM only"
}

# --- service -------------------------------------------------------------------
$svc = Get-CimInstance Win32_Service -Filter "Name='sshd'" -ErrorAction SilentlyContinue
if (-not $svc) {
    if ($Check) { Drift 'service sshd -> Automatic (delayed), running, restart on failure' }
} else {
    $delayed = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\sshd' -Name DelayedAutostart -ErrorAction SilentlyContinue).DelayedAutostart
    if ($svc.StartMode -ne 'Auto' -or $delayed -ne 1) {
        Drift "service sshd start mode $($svc.StartMode) (delayed=$delayed) -> Automatic (delayed)"
        if (-not $Check) {
            $r = Invoke-Native 'sc.exe' @('config', 'sshd', 'start=', 'delayed-auto')
            if ($r.Code -ne 0) { throw "sc config failed: $($r.Output)" }
        }
    }
    # Restart after 30s, 60s, then every 60s; the failure count resets after a day.
    $q = (Invoke-Native 'sc.exe' @('qfailure', 'sshd')).Output
    $flag = (Invoke-Native 'sc.exe' @('qfailureflag', 'sshd')).Output
    $failOk = $q -match 'RESET_PERIOD \(in seconds\)\s+:\s+86400' -and
        ((@([regex]::Matches($q, 'RESTART -- Delay = (\d+)') | ForEach-Object { $_.Groups[1].Value })) -join ',') -eq '30000,60000,60000' -and
        $flag -match 'FAILURE_ACTIONS_ON_NONCRASH_FAILURES:\s+TRUE'
    if (-not $failOk) {
        Drift 'service sshd failure actions -> restart 30s/60s/60s, also on non-crash failures'
        if (-not $Check) {
            $r = Invoke-Native 'sc.exe' @('failure', 'sshd', 'reset=', '86400', 'actions=', 'restart/30000/restart/60000/restart/60000')
            if ($r.Code -ne 0) { throw "sc failure failed: $($r.Output)" }
            $r = Invoke-Native 'sc.exe' @('failureflag', 'sshd', '1')
            if ($r.Code -ne 0) { throw "sc failureflag failed: $($r.Output)" }
        }
    }
}

# --- firewall ------------------------------------------------------------------
$default = Get-NetFirewallRule -Name $DefaultRuleName -ErrorAction SilentlyContinue
if ($default) {
    Drift "firewall rule $DefaultRuleName (tcp/22 from any) -> removed"
    if (-not $Check) { $default | Remove-NetFirewallRule }
}
$rule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $rule) {
    Drift "firewall '$RuleName': inbound tcp/22 from $subnet"
    if (-not $Check) {
        New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow -Protocol TCP `
            -LocalPort 22 -RemoteAddress $subnet -Profile Any -Program $sshdExe | Out-Null
    }
} else {
    $port = $rule | Get-NetFirewallPortFilter
    $addr = ($rule | Get-NetFirewallAddressFilter).RemoteAddress
    $app = ($rule | Get-NetFirewallApplicationFilter).Program
    $ok = $port.Protocol -eq 'TCP' -and [string]$port.LocalPort -eq '22' -and $rule.Enabled -eq 'True' -and
        $rule.Action -eq 'Allow' -and @($addr).Count -eq 1 -and ([string]$addr) -eq $subnetDotted -and $app -eq $sshdExe
    if (-not $ok) {
        Drift "firewall '$RuleName': tcp/$($port.LocalPort) from $addr ($app) -> tcp/22 from $subnet ($sshdExe)"
        if (-not $Check) {
            $rule | Set-NetFirewallRule -Enabled True -Direction Inbound -Action Allow -Protocol TCP `
                -LocalPort 22 -RemoteAddress $subnet -Profile Any -Program $sshdExe
        }
    }
}

# --- running -------------------------------------------------------------------
if (-not $Check) {
    $svc = Get-Service sshd
    if ($configChanged -and $svc.Status -eq 'Running') {
        Say 'CHANGE restart sshd (new sshd_config)'
        Restart-Service sshd
    } elseif ($svc.Status -ne 'Running') {
        Say 'CHANGE start sshd'
        Start-Service sshd
    }
    Start-Sleep -Seconds 2
} elseif ((Get-Service sshd -ErrorAction SilentlyContinue).Status -ne 'Running') {
    Drift 'service sshd not running'
}

# --- verify --------------------------------------------------------------------
$listen = @(Get-NetTCPConnection -State Listen -LocalPort 22 -ErrorAction SilentlyContinue |
        Where-Object { (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Name -eq 'sshd' } |
        ForEach-Object { $_.LocalAddress })
if ($listen.Count) {
    if ((@($listen | Sort-Object -Unique) -join ',') -eq $ListenAddress) { Say "ok     sshd listens on ${ListenAddress}:22 only" }
    else { Say "WARN   sshd listens on $($listen -join ', ') (want $ListenAddress only)" }
} elseif (-not $Check -or $script:drift -eq 0) {
    Say 'WARN   sshd is not listening on port 22'
}

if ($UnderAnsible) { $Ansible.Changed = $script:drift -gt 0 }
if ($Check) {
    if ($script:drift) {
        Say "$($script:drift) item(s) differ from Install-TnwksWindowsSshd.ps1"
        if (-not $UnderAnsible) { exit 2 }
    } else {
        Say 'No drift.'
    }
}
