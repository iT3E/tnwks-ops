#!/usr/bin/env python3
"""Test the Windows check-mode guard using a fully mocked filesystem boundary."""

import argparse
import base64
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--powershell", default=shutil.which("pwsh"))
    args = parser.parse_args()
    assert args.powershell, "Supply a PowerShell executable for the mock test"
    tasks = yaml.safe_load((ROOT / "roles/windows_observability_storage/tasks/main.yml").read_text())
    source = next(t["ansible.windows.win_powershell"]["script"] for t in tasks if "ansible.windows.win_powershell" in t)
    script = r"""
$ProgressPreference='SilentlyContinue'
function Test-Path { param($LiteralPath) return $true }
function Get-Item { param($LiteralPath) return [pscustomobject]@{Length=[long]4194304} }
function Get-PSDrive { param($Name) return [pscustomobject]@{Free=1000GB} }
function Join-Path { param($Path,$ChildPath) return 'Invoke-ForbiddenMutation' }
function Invoke-ForbiddenMutation { throw 'Mutation boundary reached in check mode' }
$Ansible=[pscustomobject]@{CheckMode=$true;Changed=$false}
$code=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__SOURCE__'))
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($code,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parse error'}
$commands=$ast.FindAll({param($node) $node -is [Management.Automation.Language.CommandAst]},$true)
foreach($command in $commands){
    $name=$command.GetCommandName()
    if($name -and $name -notin @('Test-Path','Get-Item','Get-PSDrive','Join-Path')){throw 'Unexpected command outside the mock boundary'}
    if(-not $name -and -not $command.Extent.Text.StartsWith("& (Join-Path `$StateDir 'Mount-TnwksObservability.ps1')")){throw 'Unexpected dynamic invocation'}
}
$methods=$ast.FindAll({param($node) $node -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true)
foreach($method in $methods){
    if($method.Expression.Extent.Text -ne '[math]' -or $method.Member.Extent.Text -ne 'Max'){throw 'Unexpected method outside the mock boundary'}
}
& ([ScriptBlock]::Create($code)) -Vhdx 'fixture-volume' -StateDir 'fixture-state' -Distro 'fixture-distro'
'PASS: fully mocked check-mode branch returned without invoking the forbidden mutation mock'
""".replace("__SOURCE__", base64.b64encode(source.encode()).decode())
    result = subprocess.run([args.powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand",
                             base64.b64encode(script.encode("utf-16le")).decode()],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    print(result.stdout.strip())


if __name__ == "__main__":
    main()
