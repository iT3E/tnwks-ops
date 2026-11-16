#!powershell

# Copyright: tnwks-ops contributors
# A directory with a fixed, protected set of rules, for a directory that a
# task with no elevation writes to and that SYSTEM or an elevated task also
# reads (docs/wsl/windows-ansible.md, "Start WSL with no elevation").
#
# The directory inherits no rule, the Administrators own it, SYSTEM and the
# Administrators have full control, and Users may read it. With `writer`, that
# account has two rules: on the directory itself it may list it and make
# files (no delete, no subdirectory), so it can not remove the directory or
# put a link or a junction in its place; on the files it may change, replace
# and delete them (a new file, then a rename over the old one).
#
# The module stops, and changes nothing, when the path is a link or a
# junction: Get-Acl and Set-Acl follow one, so the rules would go to its
# target. It makes the directory when it is missing.

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
# --- END logic

$spec = @{
    options = @{
        path = @{ type = 'path'; required = $true }
        # The account that writes files here with no elevation; none: read only.
        writer = @{ type = 'str'; default = '' }
    }
    supports_check_mode = $true
}

$module = [Ansible.Basic.AnsibleModule]::Create($args, $spec)
try {
    $module.Result.changed = Invoke-TnwksDirAcl -Path $module.Params.path -Writer $module.Params.writer -Check $module.CheckMode
} catch {
    $module.FailJson($_.Exception.Message, $_)
}
$module.ExitJson()
