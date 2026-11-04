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
    $settings = New-Object Xml.XmlWriterSettings
    $settings.Indent = $true
    $settings.OmitXmlDeclaration = $true
    $sb = New-Object Text.StringBuilder
    $w = [Xml.XmlWriter]::Create($sb, $settings)
    $doc.Save($w)
    $w.Close()
    return $sb.ToString() + "`n"
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
        try {
            $task = Register-ScheduledTask -TaskPath $path -TaskName $name -Xml $module.Params.xml -Force
        } catch {
            $module.FailJson("Register-ScheduledTask $path$name failed: $($_.Exception.Message)", $_)
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
