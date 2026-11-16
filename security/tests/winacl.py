"""Fakes for the ACL scripts of the Windows roles: the commands and the types that only
Windows has, so a test can run such a script in the pwsh of CI.

Get-Item gives a directory (a link with $script:IsLink, nothing with $script:Exists off),
New-Item makes it, Get-Acl gives $script:Have (New-TestAcl, New-TestRule), and Set-Acl and
each command add to $script:Calls. New-Object fakes NTAccount (it translates to
S-1-5-21-7-7-7-1001), SecurityIdentifier, FileSystemAccessRule and DirectorySecurity.
Get-TestKeys gives the rules of an ACL as sorted text.
"""

ACL_FAKES = r"""
$script:Calls = New-Object System.Collections.Generic.List[object]
$Ansible = [pscustomobject]@{ Changed = $null }
$script:IsLink = $false
$script:Exists = $true
function Get-Item {
    param([string]$LiteralPath, [switch]$Force, $ErrorAction)
    $script:Calls.Add('Get-Item')
    if (-not $script:Exists) { return $null }
    $attributes = [IO.FileAttributes]::Directory
    if ($script:IsLink) { $attributes = $attributes -bor [IO.FileAttributes]::ReparsePoint }
    [pscustomobject]@{ PSIsContainer = $true; Attributes = $attributes }
}
function New-TestRule($Sid, $Rights, $Inheritance, $Propagation, $Inherited = $false) {
    [pscustomobject]@{
        IdentityReference = [pscustomobject]@{ Value = [string]$Sid }
        FileSystemRights  = [System.Security.AccessControl.FileSystemRights]$Rights
        InheritanceFlags  = [System.Security.AccessControl.InheritanceFlags]$Inheritance
        PropagationFlags  = [System.Security.AccessControl.PropagationFlags]$Propagation
        AccessControlType = 'Allow'
        IsInherited       = $Inherited
    }
}
function New-Object {
    param([Parameter(Position = 0)]$TypeName, [Parameter(Position = 1)]$ArgumentList)
    switch -Wildcard ("$TypeName") {
        '*.NTAccount' {
            $account = [pscustomobject]@{ Name = $ArgumentList }
            $account | Add-Member ScriptMethod Translate { param($Type) [pscustomobject]@{ Value = 'S-1-5-21-7-7-7-1001' } }
            return $account
        }
        '*.SecurityIdentifier' { return [pscustomobject]@{ Value = [string]$ArgumentList } }
        '*.FileSystemAccessRule' {
            $a = @($ArgumentList)
            return New-TestRule $a[0].Value $a[1] $a[2] $a[3]
        }
        '*.DirectorySecurity' {
            $acl = [pscustomobject]@{ Owner = $null; Protected = $null; Rules = [System.Collections.Generic.List[object]]::new() }
            $acl | Add-Member ScriptMethod SetOwner { param($Sid) $this.Owner = $Sid.Value }
            $acl | Add-Member ScriptMethod SetAccessRuleProtection { param($On, $Keep) $this.Protected = $On }
            $acl | Add-Member ScriptMethod AddAccessRule { param($Rule) $this.Rules.Add($Rule) }
            return $acl
        }
        default { throw "no fake for $TypeName" }
    }
}
function New-TestAcl([bool]$Protected, [string]$Owner, $Rules) {
    $acl = [pscustomobject]@{ AreAccessRulesProtected = $Protected; Owner = $Owner; Rules = @($Rules) }
    $acl | Add-Member ScriptMethod GetOwner { param($Type) [pscustomobject]@{ Value = $this.Owner } }
    $acl | Add-Member ScriptMethod GetAccessRules {
        param($Explicit, $Inherited, $Type)
        @($this.Rules | Where-Object { ($Explicit -and -not $_.IsInherited) -or ($Inherited -and $_.IsInherited) })
    }
    $acl
}
function New-Item {
    param($ItemType, [string]$Path)
    $script:Calls.Add('New-Item')
    $script:Exists = $true
}
function Get-Acl { param([string]$LiteralPath) $script:Calls.Add('Get-Acl'); $script:Have }
function Set-Acl { param([string]$LiteralPath, $AclObject) $script:Calls.Add(@{ Path = $LiteralPath; Acl = $AclObject }) }
function Get-TestKeys($Rules) {
    @($Rules | ForEach-Object { '{0}|{1}|{2}|{3}' -f $_.IdentityReference.Value, $_.FileSystemRights, $_.InheritanceFlags, $_.PropagationFlags } | Sort-Object)
}
"""
