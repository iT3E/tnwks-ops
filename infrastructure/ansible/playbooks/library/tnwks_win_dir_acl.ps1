#!powershell

# Copyright: tnwks-ops contributors
# A directory with a fixed, protected set of rules: the folder of a role
# under C:\ProgramData, which SYSTEM or an elevated task reads, and a
# directory that a task with no elevation writes to and that SYSTEM or an
# elevated task also reads (docs/wsl/windows-ansible.md, "Start WSL with no
# elevation").
#
# The directory inherits no rule, the Administrators own it, SYSTEM and the
# Administrators have full control, and Users may read it. With `writer`, that
# account has two rules: on the directory itself it may list it and make
# files (no delete, no subdirectory), so it can not remove the directory or
# put a link or a junction in its place; on the files it may change, replace
# and delete them (a new file, then a rename over the old one).
#
# With `items: reset`, each file and folder below, but for the folders of
# `keep` and what is in them, gets the Administrators as its owner and only
# the rules that it inherits from the directory, unless it has them already
# (an owner of SYSTEM stays). A folder of a role holds the scripts and the
# configuration that SYSTEM or an elevated task reads, so no item there may
# keep an owner or a rule of its own: an owner can change the rules of the
# item. The folders of `keep` have their own call of this module, with a
# writer.
#
# The module stops, and changes nothing, when the path is a link or a
# junction: Get-Acl and Set-Acl follow one, so the rules would go to its
# target. A link or a junction below, outside `keep`, stops it in the same
# way, before it changes any item. It makes the directory when it is
# missing.

#AnsibleRequires -CSharpUtil Ansible.Basic

# --- BEGIN logic (security/tests/test_wsl_start_unelevated.py runs this part with fakes)
function Get-TnwksDirAclWant([string]$WriterSid) {
    # Who, which rights, to which children, how far.
    $all = 'ContainerInherit, ObjectInherit'
    $want = @(
        @('S-1-5-18', 'FullControl', $all, 'None'),
        @('S-1-5-32-544', 'FullControl', $all, 'None'),
        @('S-1-5-32-545', 'ReadAndExecute, Synchronize', $all, 'None')
    )
    if ($WriterSid) {
        $want += , @($WriterSid, 'ReadAndExecute, CreateFiles, Synchronize', 'None', 'None')
        $want += , @($WriterSid, 'Modify, Synchronize', 'ObjectInherit', 'InheritOnly')
    }
    , $want
}

function Get-TnwksDirAclKey($Rules) {
    @($Rules | ForEach-Object {
        '{0}|{1}|{2}|{3}|{4}' -f $_.IdentityReference.Value, [int]$_.FileSystemRights,
            [int]$_.InheritanceFlags, [int]$_.PropagationFlags, $_.AccessControlType
    } | Sort-Object) -join ';'
}

function Invoke-TnwksDirAcl {
    <#
    .SYNOPSIS
    Makes the directory and sets its rules; gives whether it changed anything.
    With -Check, it changes nothing and gives whether it would.
    #>
    param([string]$Path, [string]$Writer, [bool]$Check)
    $sid = [System.Security.Principal.SecurityIdentifier]
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        if ($Check) { return $true }
        New-Item -ItemType Directory -Path $Path | Out-Null
        $item = Get-Item -LiteralPath $Path -Force
    }
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "$Path is not a plain directory (a link or a junction?). Nothing is changed."
    }
    $writerSid = ''
    if ($Writer) { $writerSid = (New-Object System.Security.Principal.NTAccount $Writer).Translate($sid).Value }
    $want = Get-TnwksDirAclWant $writerSid
    $wantKey = @($want | ForEach-Object {
        '{0}|{1}|{2}|{3}|Allow' -f $_[0], [int][System.Security.AccessControl.FileSystemRights]$_[1],
            [int][System.Security.AccessControl.InheritanceFlags]$_[2], [int][System.Security.AccessControl.PropagationFlags]$_[3]
    } | Sort-Object) -join ';'
    $have = Get-Acl -LiteralPath $Path
    $same = $have.AreAccessRulesProtected -and
        $have.GetOwner($sid).Value -eq 'S-1-5-32-544' -and
        (Get-TnwksDirAclKey $have.GetAccessRules($true, $true, $sid)) -eq $wantKey
    if ($same) { return $false }
    if (-not $Check) {
        $acl = New-Object System.Security.AccessControl.DirectorySecurity
        $acl.SetOwner((New-Object $sid 'S-1-5-32-544'))
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($rule in $want) {
            $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
                (New-Object $sid $rule[0]), [System.Security.AccessControl.FileSystemRights]$rule[1],
                [System.Security.AccessControl.InheritanceFlags]$rule[2],
                [System.Security.AccessControl.PropagationFlags]$rule[3],
                [System.Security.AccessControl.AccessControlType]::Allow)))
        }
        Set-Acl -LiteralPath $Path -AclObject $acl
    }
    $true
}

function Get-TnwksDirItems {
    <#
    .SYNOPSIS
    Each file and folder below the directory, but for the folders of Keep and
    what is in them. Stops at a link or a junction.
    #>
    param([string]$Path, [string[]]$Keep)
    $skip = @($Keep | Where-Object { $_ } | ForEach-Object { $_.TrimEnd('\') })
    $out = [System.Collections.Generic.List[object]]::new()
    $todo = [System.Collections.Generic.Queue[string]]::new()
    $todo.Enqueue($Path)
    while ($todo.Count -gt 0) {
        foreach ($child in @(Get-ChildItem -LiteralPath $todo.Dequeue() -Force)) {
            if ($skip -contains $child.FullName.TrimEnd('\')) { continue }
            if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "$($child.FullName) is a link or a junction. No item is changed."
            }
            $out.Add($child)
            if ($child.PSIsContainer) { $todo.Enqueue($child.FullName) }
        }
    }
    , $out
}

function Invoke-TnwksDirItems {
    <#
    .SYNOPSIS
    Gives each item below the directory the owner Administrators and only its
    inherited rules; gives the number of items it changed (or would, with -Check).
    #>
    param([string]$Path, [string[]]$Keep, [bool]$Check)
    $sid = [System.Security.Principal.SecurityIdentifier]
    $owners = @('S-1-5-32-544', 'S-1-5-18')
    $items = Get-TnwksDirItems -Path $Path -Keep $Keep
    $reset = @($items | Where-Object {
        $acl = Get-Acl -LiteralPath $_.FullName
        $acl.AreAccessRulesProtected -or $owners -notcontains $acl.GetOwner($sid).Value -or
            @($acl.GetAccessRules($true, $false, $sid)).Count -gt 0
    })
    if (-not $Check) {
        foreach ($item in $reset) {
            $kind = 'System.Security.AccessControl.FileSecurity'
            if ($item.PSIsContainer) { $kind = 'System.Security.AccessControl.DirectorySecurity' }
            $acl = New-Object $kind
            $acl.SetOwner((New-Object $sid 'S-1-5-32-544'))
            $acl.SetAccessRuleProtection($false, $false)
            Set-Acl -LiteralPath $item.FullName -AclObject $acl
        }
    }
    $reset.Count
}
# --- END logic

$spec = @{
    options = @{
        path = @{ type = 'path'; required = $true }
        # The account that writes files here with no elevation; none: read only.
        writer = @{ type = 'str'; default = '' }
        # reset: each item below takes the owner and the rules of the directory.
        items = @{ type = 'str'; default = 'keep'; choices = @('keep', 'reset') }
        # Folders below that the reset leaves as they are, with what is in them.
        keep = @{ type = 'list'; elements = 'path'; default = @() }
    }
    supports_check_mode = $true
}

$module = [Ansible.Basic.AnsibleModule]::Create($args, $spec)
try {
    $changed = Invoke-TnwksDirAcl -Path $module.Params.path -Writer $module.Params.writer -Check $module.CheckMode
    $module.Result.items_reset = 0
    # A check run does not make a missing directory, so there is nothing below it to read.
    if ($module.Params.items -eq 'reset' -and (Test-Path -LiteralPath $module.Params.path)) {
        $module.Result.items_reset = Invoke-TnwksDirItems -Path $module.Params.path -Keep $module.Params.keep -Check $module.CheckMode
    }
    $module.Result.changed = $changed -or $module.Result.items_reset -gt 0
} catch {
    $module.FailJson($_.Exception.Message, $_)
}
$module.ExitJson()
