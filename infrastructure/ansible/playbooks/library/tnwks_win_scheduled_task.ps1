#!powershell

# Copyright: tnwks-ops contributors
# A Windows Scheduled Task as a whole task XML document. Used by the Windows
# host roles (docs/wsl/windows-ansible.md) instead of
# community.windows.win_scheduled_task, so that a role states the exact task
# Task Scheduler stores (triggers, delays, settings, principal) and check mode
# compares it field for field, with a readable XML diff.

#AnsibleRequires -CSharpUtil Ansible.Basic

$spec = @{
    options = @{
        name = @{ type = 'str'; required = $true }
        # Folder, e.g. '\' or '\Prometheus\'.
        path = @{ type = 'str'; default = '\' }
        # The task definition (Export-ScheduledTask format). Required for
        # state=present.
        xml = @{ type = 'str' }
        state = @{ type = 'str'; default = 'present'; choices = @('present', 'absent') }
        # true: start the task when it isn't running (long-running tasks).
        running = @{ type = 'bool'; default = $false }
        # The account of an S4U task whose principal is not the account of this
        # session. Only for an account whose password nobody needs: the module
        # RESETS it. Microsoft's rule (Security Contexts for Tasks, "Security for
        # Registering Tasks",
        # https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks)
        # asks an administrator, SYSTEM included, for the password of a different
        # account to register an S4U task for it. So when the task must be
        # registered (not in check mode, not when it compares equal), the module
        # makes a fresh random password on the host, sets it on the account,
        # registers through the Schedule.Service object with TASK_LOGON_S4U, and
        # drops it. The password is not an argument, and no result, diff,
        # warning, failure text or file holds it. With S4U the service stores no
        # password. Not set (each other task): Register-ScheduledTask -Xml.
        # The module refuses, and changes nothing, when the account is a member
        # of Administrators, when its Description is not s4u_description, when
        # it is the account of this process, or when it has an interactive,
        # network or remote logon session open (the account of the Ansible
        # session, or a person signed in).
        s4u_user = @{ type = 'str' }
        # The Description that the account of s4u_user must have. Required with it.
        s4u_description = @{ type = 'str' }
    }
    required_if = @(, @('state', 'present', @(, 'xml')))
    required_by = @{ s4u_user = @(, 's4u_description') }
    supports_check_mode = $true
}

$module = [Ansible.Basic.AnsibleModule]::Create($args, $spec)
$name = $module.Params.name
$path = $module.Params.path
if (-not $path.StartsWith('\')) { $path = '\' + $path }
if (-not $path.EndsWith('\')) { $path = $path + '\' }
$check = $module.CheckMode

function ConvertTo-Sid([string]$account) {
    if ([string]::IsNullOrWhiteSpace($account) -or $account -match '^S-1-') { return $account }
    try {
        return (New-Object Security.Principal.NTAccount($account)).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        return $account
    }
}

# Canonical form: no registration metadata (date, author, URI, version),
# accounts as SIDs, indented. Two definitions that Task Scheduler treats the
# same compare equal.
function Get-CanonicalXml([string]$text) {
    # The declaration's encoding (UTF-16 in exports) means nothing for an
    # in-memory string and makes LoadXml fail on some inputs.
    $text = $text -replace '^\s*<\?xml[^>]*\?>', ''
    $doc = New-Object Xml.XmlDocument
    $doc.PreserveWhitespace = $false
    $doc.LoadXml($text)
    $ns = New-Object Xml.XmlNamespaceManager($doc.NameTable)
    $ns.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
    foreach ($c in @($doc.SelectNodes('//comment()'))) { [void]$c.ParentNode.RemoveChild($c) }
    $doc.DocumentElement.RemoveAttribute('version')
    foreach ($x in @('Date', 'Author', 'URI', 'Version', 'Source', 'SecurityDescriptor')) {
        $n = $doc.SelectSingleNode("/t:Task/t:RegistrationInfo/t:$x", $ns)
        if ($n) { [void]$n.ParentNode.RemoveChild($n) }
    }
    $ri = $doc.SelectSingleNode('/t:Task/t:RegistrationInfo', $ns)
    if ($ri -and -not $ri.HasChildNodes) { [void]$ri.ParentNode.RemoveChild($ri) }
    foreach ($n in $doc.SelectNodes('//t:UserId', $ns)) { $n.InnerText = ConvertTo-Sid $n.InnerText }
    # LeastPrivilege is the default run level, and Export-ScheduledTask leaves
    # the element out for it. A template that names it is the same task.
    foreach ($n in @($doc.SelectNodes('//t:Principal/t:RunLevel', $ns))) {
        if ($n.InnerText.Trim() -eq 'LeastPrivilege') { [void]$n.ParentNode.RemoveChild($n) }
    }
    $settings = New-Object Xml.XmlWriterSettings
    $settings.Indent = $true
    $settings.OmitXmlDeclaration = $true
    $sb = New-Object Text.StringBuilder
    $w = [Xml.XmlWriter]::Create($sb, $settings)
    $doc.Save($w)
    $w.Close()
    return $sb.ToString() + "`n"
}

# Each exception of the chain, outer first: its type, its HResult and its
# message. A CimException of the provider of ScheduledTasks also gives its
# status and the fields of its error data that hold a code or a message.
function Format-TnwksError($Exception) {
    $parts = @()
    $e = $Exception
    while ($e) {
        $part = '{0} 0x{1:X8}: {2}' -f $e.GetType().FullName, $e.HResult, "$($e.Message)".Trim()
        if ($e.PSObject.Properties['StatusCode']) { $part += " (status $($e.StatusCode))" }
        if ($e.PSObject.Properties['ErrorData'] -and $e.ErrorData) {
            $fields = @($e.ErrorData.CimInstanceProperties | Where-Object { $_.Name -match 'Code|Message' -and $null -ne $_.Value } |
                ForEach-Object { "$($_.Name)=$($_.Value)" })
            if ($fields) { $part += " [$($fields -join '; ')]" }
        }
        $parts += $part
        $e = $e.InnerException
    }
    $parts -join ' <- '
}

# A random password with an upper and a lower case letter, a digit and a sign,
# 68 characters: within the limit of 127 of a local account.
function New-TnwksPassword {
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    'Aa1!' + ([Convert]::ToBase64String($bytes) -replace '[+/=]', 'x')
}

# The reads behind the refusals, each on its own so a test can replace it.
function Get-TnwksAccountSid([string]$User) {
    ([Security.Principal.NTAccount]::new($User)).Translate([Security.Principal.SecurityIdentifier]).Value
}
function Get-TnwksAccountDescription([string]$Sid) {
    "$((Get-LocalUser -SID $Sid).Description)"
}
# The SIDs of the direct members of Administrators, by their objectSid: a
# member that does not resolve to a name is still read.
function Get-TnwksAdministratorSids {
    $name = ([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')).Translate([Security.Principal.NTAccount]).Value.Split('\')[-1]
    $group = [ADSI]"WinNT://$env:COMPUTERNAME/$name,group"
    @($group.Invoke('Members') | ForEach-Object {
        $bytes = $_.GetType().InvokeMember('objectSid', 'GetProperty', $null, $_, $null)
        ([Security.Principal.SecurityIdentifier]::new([byte[]]$bytes, 0)).Value
    })
}
function Get-TnwksCallerSid {
    [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
}
# The logon types of the sessions of the account that are open now.
function Get-TnwksLogonTypes([string]$Sid) {
    $name = ([Security.Principal.SecurityIdentifier]::new($Sid)).Translate([Security.Principal.NTAccount]).Value.Split('\')
    @(Get-CimInstance Win32_LoggedOnUser | Where-Object { $_.Antecedent.Name -eq $name[-1] -and $_.Antecedent.Domain -eq $name[0] } |
        ForEach-Object { (Get-CimInstance Win32_LogonSession -Filter "LogonId = '$($_.Dependent.LogonId)'").LogonType })
}

# Empty when the module may reset the password of the account, else why not.
function Get-TnwksS4uRefusal([string]$User, [string]$Description) {
    try { $sid = Get-TnwksAccountSid $User } catch { return "$User is not an account of this host" }
    if ((Get-TnwksAdministratorSids) -contains $sid) { return "$User is a member of Administrators" }
    $found = Get-TnwksAccountDescription $sid
    if ($found -ne $Description) { return "the Description of $User is '$found', not '$Description'" }
    if ($sid -eq (Get-TnwksCallerSid)) { return "$User is the account of this process" }
    # 2 interactive, 3 network, 10 remote interactive, 11 cached interactive.
    $open = @(Get-TnwksLogonTypes $sid | Where-Object { $_ -in 2, 3, 10, 11 })
    if ($open) { return "$User has a logon session open (type $($open -join ', ')): the account of a session or of a person" }
    ''
}

# An S4U task for another account: the refusals first, then a fresh password
# on the account, then ITaskFolder.RegisterTask with the user, that password and
# TASK_LOGON_S4U (2), as TASK_CREATE_OR_UPDATE (6). A folder that is not there
# is made under the root. A failure throws its text with the password taken out.
function Register-TnwksS4uTask($Service, [string]$Path, [string]$Name, [string]$Xml, [string]$User, [string]$Description) {
    $refusal = Get-TnwksS4uRefusal -User $User -Description $Description
    if ($refusal) { throw [InvalidOperationException]::new("refused, nothing is changed: $refusal. s4u_user resets the password of the account.") }
    $fresh = New-TnwksPassword
    try {
        # No cmdlet takes the plain value: module logging would record it.
        $secure = [Net.NetworkCredential]::new('', $fresh).SecurePassword
        Set-LocalUser -Name $User -Password $secure
        $Service.Connect()
        $folderPath = if ($Path.Trim('\')) { '\' + $Path.Trim('\') } else { '\' }
        try {
            $folder = $Service.GetFolder($folderPath)
        } catch {
            $folder = $Service.GetFolder('\').CreateFolder($Path.Trim('\'))
        }
        [void]$folder.RegisterTask($Name, $Xml, 6, $User, $fresh, 2)
    } catch {
        throw [InvalidOperationException]::new((Format-TnwksError $_.Exception).Replace($fresh, '<password>'))
    } finally {
        $fresh = $null
    }
}

$task = Get-ScheduledTask -TaskPath $path -TaskName $name -ErrorAction SilentlyContinue
$before = ''
if ($task) {
    $before = Get-CanonicalXml (Export-ScheduledTask -TaskPath $path -TaskName $name)
}

if ($module.Params.state -eq 'absent') {
    if ($task) {
        $module.Result.changed = $true
        if (-not $check) {
            Stop-ScheduledTask -InputObject $task -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -InputObject $task -Confirm:$false
        }
    }
    $module.Diff.before = $before
    $module.Diff.after = ''
    $module.ExitJson()
}

$after = Get-CanonicalXml $module.Params.xml
$module.Diff.before = $before
$module.Diff.after = $after

if ($before -ne $after) {
    $module.Result.changed = $true
    if (-not $check) {
        $user = $module.Params.s4u_user
        $call = if ($user) { "RegisterTask $path$name for $user by S4U, with a new password" } else { "Register-ScheduledTask $path$name" }
        if ($user) {
            # The text only: the error record could carry the password.
            try {
                Register-TnwksS4uTask -Service (New-Object -ComObject Schedule.Service) -Path $path -Name $name -Xml $module.Params.xml `
                    -User $user -Description $module.Params.s4u_description
            } catch {
                $module.FailJson("$call failed: $($_.Exception.Message)")
            }
            $task = Get-ScheduledTask -TaskPath $path -TaskName $name
        } else {
            try {
                $task = Register-ScheduledTask -TaskPath $path -TaskName $name -Xml $module.Params.xml -Force
            } catch {
                $module.FailJson("$call failed: $(Format-TnwksError $_.Exception)", $_)
            }
        }
    }
}

if ($module.Params.running) {
    $state = if ($task) { "$((Get-ScheduledTask -TaskPath $path -TaskName $name -ErrorAction SilentlyContinue).State)" } else { '' }
    if ($state -ne 'Running') {
        $module.Result.changed = $true
        $module.Result.started = $true
        if (-not $check) { Start-ScheduledTask -TaskPath $path -TaskName $name }
    }
}

$current = Get-ScheduledTask -TaskPath $path -TaskName $name -ErrorAction SilentlyContinue
$module.Result.task_state = if ($current) { "$($current.State)" } else { '' }
$module.ExitJson()
