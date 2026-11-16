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
        # session. Set: the module registers the task through the Schedule.Service
        # object with this user, no password and TASK_LOGON_S4U. Empty (each other
        # task): Register-ScheduledTask -Xml.
        s4u_user = @{ type = 'str'; default = '' }
    }
    required_if = @(, @('state', 'present', @(, 'xml')))
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

# An S4U task for another account: ITaskFolder.RegisterTask with the user, no
# password and TASK_LOGON_S4U (2), as TASK_CREATE_OR_UPDATE (6). A folder that
# is not there is made under the root.
function Register-TnwksS4uTask($Service, [string]$Path, [string]$Name, [string]$Xml, [string]$User) {
    $Service.Connect()
    try {
        $folder = $Service.GetFolder($Path)
    } catch {
        $folder = $Service.GetFolder('\').CreateFolder($Path.Trim('\'))
    }
    [void]$folder.RegisterTask($Name, $Xml, 6, $User, $null, 2)
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
        $call = if ($user) { "RegisterTask $path$name for $user by S4U" } else { "Register-ScheduledTask $path$name" }
        try {
            if ($user) {
                Register-TnwksS4uTask -Service (New-Object -ComObject Schedule.Service) -Path $path -Name $name -Xml $module.Params.xml -User $user
                $task = Get-ScheduledTask -TaskPath $path -TaskName $name
            } else {
                $task = Register-ScheduledTask -TaskPath $path -TaskName $name -Xml $module.Params.xml -Force
            }
        } catch {
            $module.FailJson("$call failed: $(Format-TnwksError $_.Exception)", $_)
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
