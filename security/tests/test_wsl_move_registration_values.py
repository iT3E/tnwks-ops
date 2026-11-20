"""The values of the registration of today in the configs of the move, and the repair of a
registration that an import with those values as text made (role windows_wsl_move).

The import task of each account writes each value of `registration_values` as a DWORD into
its registration. A read of the kept file that returns the values as an object puts them
three levels deep in the output of win_powershell, which writes its output to the depth of its
option `depth` (2) and gives each object below that depth as its PowerShell text. The configs
then hold the text "@{DefaultUid=...; Flags=...}", and the import writes one DWORD named
Length. Tests that build the configs by hand, as mappings, and pass no read through the output
of win_powershell do not see that. These tests run the script of the read, write its output as
win_powershell does, and render the configs with Ansible from it.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import ansible_render
import pytest
import yaml
from test_wsl_start_unelevated import needs_pwsh, pwsh

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLE = ANSIBLE / "roles/windows_wsl_move"
# The depth of the output of win_powershell when the task sets none (its option depth).
MODULE_DEPTH = 2
SAVED = {"dwords": {"DefaultUid": 1000, "Flags": 15}, "kernel_command_line": "quiet",
         "default_environment": ["HOSTTYPE=x86_64", "LANG=C.UTF-8"], "default": True, "nat": {}}


def tasks(name: str) -> list[dict]:
    out = []
    for task in yaml.safe_load((ROLE / "tasks" / name).read_text()):
        out.extend(task.get("block", [task]))
    return out


def named(name: str, title: str) -> dict:
    return next(task for task in tasks(name) if task.get("name") == title)


def module_output(script: str, registration: Path, wslconfig: Path) -> list:
    """The output of the task as win_powershell gives it: each object of the script, written to
    the depth of the module."""
    encoded = base64.b64encode(script.encode()).decode()
    result = pwsh(f"""$Ansible = [pscustomobject]@{{ Changed = $null }}
$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))
$out = @(& ([scriptblock]::Create($text)) -Registration '{registration}' -Wslconfig '{wslconfig}')
[Console]::Out.Write((ConvertTo-Json -InputObject $out -Depth {MODULE_DEPTH} -Compress))""")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def kept_files(tmp_path: Path) -> tuple[Path, Path]:
    registration = tmp_path / "registration.json"
    # As step 3 writes it (read_today.yml): ConvertTo-Json of the read, UTF-8 with no BOM.
    registration.write_text(json.dumps(SAVED, indent=4))
    wslconfig = tmp_path / "present.wslconfig"
    wslconfig.write_text("[wsl2]\nmemory=8GB\n")
    return registration, wslconfig


READ = named("read_kept.yml", "Read the kept reads of today")["ansible.windows.win_powershell"]["script"]
FACT = named("read_kept.yml", "The reads of today")["ansible.builtin.set_fact"]
CHECK = named("read_kept.yml", "The values of the registration of today are a mapping of whole numbers")


@needs_pwsh
def test_an_object_three_levels_deep_reaches_the_play_as_its_text(tmp_path):
    """Why a read that returns the kept file as an object gives text: the module writes the
    dwords, at depth 3 of its output, as their PowerShell text."""
    registration, wslconfig = kept_files(tmp_path)
    old = ("param([string]$Registration, [string]$Wslconfig)\n"
           "@{ registration = (Get-Content -LiteralPath $Registration -Raw | ConvertFrom-Json); "
           "wslconfig = [IO.File]::ReadAllText($Wslconfig) }")
    out = module_output(old, registration, wslconfig)
    assert out[-1]["registration"]["dwords"] == "@{DefaultUid=1000; Flags=15}"
    assert len(out[-1]["registration"]["dwords"]) == 28


@needs_pwsh  # In the job of the pwsh tests, which runs ansible-playbook too.
@ansible_render.no_ansible
def test_each_account_config_takes_the_saved_values_as_a_mapping(tmp_path):
    """The read of the role, its output as win_powershell writes it, and the configs as Ansible
    renders them from it, in the form whose SHA-256 the role compares (to_nice_json)."""
    registration, wslconfig = kept_files(tmp_path)
    out = module_output(READ, registration, wslconfig)
    site2 = ansible_render.load(
        "roles/windows_wsl_move/defaults/main.yml", "roles/windows_wsl_move/vars/main.yml",
        "inventory/group_vars/windows_hosts.yml", "inventory/host_vars/WSL-02/main.yml")
    standins = {"wsl_move_present_account": "standin1", "wsl_move_new_account": "standin-wsl",
                "wsl_move_present_folder": "C:\\Users\\standin1\\wsl", "wsl_move_rehearsed_wsl_version": "3.0.5.0",
                "ansible_user": "standin-admin"}
    hosts = {}
    for name, read in (("WSL-02", out), ("WSL-02-text", [{"registration": json.dumps(
            {**SAVED, "dwords": "@{DefaultUid=1000; Flags=15}"}), "wslconfig": ""}]), ("WSL-02-none", [None])):
        hosts[name] = {**site2, **standins, "wsl_move_today_read": {"output": read}, **FACT}
    expressions = {
        "new": "wsl_move_configs['config-new'] | to_nice_json",
        "present": "wsl_move_configs['config-present'] | to_nice_json",
        "check": " and ".join(f"({rule})" for rule in CHECK["ansible.builtin.assert"]["that"]),
    }
    rendered = ansible_render.render(tmp_path, {**hosts, "WSL-02-ubuntu": {"wsl_distro_ssh_host": "192.0.2.10", "ansible_host": "192.0.2.10", "ansible_port": 22, "ansible_ssh_private_key_file": "/dev/null"}},
                                     list(hosts), expressions)
    for key in ("new", "present"):
        config = json.loads(rendered["WSL-02"][key])
        assert config["registration_values"] == {"DefaultUid": 1000, "Flags": 15}, key
    assert rendered["WSL-02"]["check"] is True
    # Values that are text stop the play before any config is written.
    assert rendered["WSL-02-text"]["check"] is False
    # Nothing kept yet (a check run on a new host): no values, and no stop.
    assert json.loads(rendered["WSL-02-none"]["new"])["registration_values"] == {}
    assert rendered["WSL-02-none"]["check"] is True


def test_the_check_of_the_values_comes_before_the_configs_and_the_repair():
    main = [task.get("name") for task in tasks("main.yml")]
    kept = [task.get("name") for task in tasks("read_kept.yml")]
    assert kept.index("The reads of today") < kept.index(CHECK["name"])
    assert main.index("What step 3 reads of today") < main.index(REPAIR_NAME) < main.index(
        "The scripts, the configs and the tasks of the procedure")
    assert "from_json" in FACT["wsl_move_registration"]
    assert "ConvertFrom-Json" not in READ


REPAIR_NAME = "The registration of the new account holds the values of step 3, and no value Length"
REPAIR = named("main.yml", REPAIR_NAME)
SID = "S-1-5-21-1-2-3-1002"

# A registry of one hive, with the methods of RegistryKey that the script calls.
REGISTRY_FAKES = r"""
$global:Calls = New-Object System.Collections.Generic.List[string]
function New-TestKey([string]$Name, [hashtable]$Values) {
    $key = [pscustomobject]@{ PSPath = "Registry::HKEY_USERS\SID\Lxss\$Name"; Values = $Values }
    $key | Add-Member ScriptMethod GetValue { param($n) $this.Values[$n] }
    $key | Add-Member ScriptMethod GetValueKind { param($n) if ($this.Values[$n] -is [string]) { 'String' } else { 'DWord' } }
    $key | Add-Member ScriptMethod GetValueNames { @($this.Values.Keys) }
    $key
}
function Test-Path { param([string]$LiteralPath) $global:Loaded }
function Get-ChildItem { param([string]$LiteralPath) $global:Keys }
function Set-ItemProperty { param([string]$LiteralPath, $Name, $Value, $Type) $global:Calls.Add("set $Name=$Value $Type") }
function Remove-ItemProperty { param([string]$LiteralPath, $Name) $global:Calls.Add("remove $Name") }
$Ansible = [pscustomobject]@{ Changed = $null }
$global:Loaded = $true
"""


def run_repair(keys: str, what_if: bool = False, loaded: bool = True) -> dict:
    script = REPAIR["ansible.windows.win_powershell"]["script"]
    encoded = base64.b64encode(script.encode()).decode()
    values = json.dumps(SAVED["dwords"])
    result = pwsh(REGISTRY_FAKES + f"""
$global:Loaded = ${str(loaded).lower()}
$global:Keys = @({keys})
$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))
$thrown = $null
try {{
    & ([scriptblock]::Create($text)) -Sid '{SID}' -Distro 'Ubuntu' -Values '{values}' -WhatIf:${str(what_if).lower()} 6>$null | Out-Null
}} catch {{ $thrown = $_.Exception.Message }}
[Console]::Out.Write((ConvertTo-Json -Compress @{{ changed = $Ansible.Changed; thrown = $thrown; calls = @($global:Calls) }}))
""")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


FAULTY = "(New-TestKey 'a' @{ DistributionName = 'Ubuntu'; DefaultUid = 0; Flags = 15; Length = 28 })"
REPAIRED = "(New-TestKey 'a' @{ DistributionName = 'Ubuntu'; DefaultUid = 1000; Flags = 15 })"
OTHER = "(New-TestKey 'b' @{ DistributionName = 'docker-desktop'; DefaultUid = 0; Flags = 15 })"


@needs_pwsh
def test_the_repair_writes_the_saved_values_and_removes_the_value_length():
    out = run_repair(f"{FAULTY}, {OTHER}")
    assert out["thrown"] is None and out["changed"] is True
    assert out["calls"] == ["set DefaultUid=1000 DWord", "remove Length"]


@needs_pwsh
def test_a_registration_with_the_saved_values_is_no_change():
    out = run_repair(f"{REPAIRED}, {OTHER}")
    assert out == {"changed": False, "thrown": None, "calls": []}


@needs_pwsh
def test_a_check_run_of_the_repair_changes_nothing():
    out = run_repair(FAULTY, what_if=True)
    assert out["changed"] is True and out["calls"] == [] and out["thrown"] is None


@needs_pwsh
@pytest.mark.parametrize(("keys", "loaded", "text"), [
    (FAULTY, False, "not loaded"),
    (OTHER, True, "lists 0 distros"),
    (f"{FAULTY}, {REPAIRED}", True, "lists 2 distros"),
])
def test_the_repair_stops_before_any_change_when_it_can_not_tell_the_registration(keys, loaded, text):
    out = run_repair(keys, loaded=loaded)
    assert out["thrown"] and text in out["thrown"] and out["calls"] == []


def test_the_repair_runs_as_system_after_a_window_that_ended_done():
    task = REPAIR["ansible.windows.win_powershell"]
    assert task["parameters"] == {"Sid": "{{ wsl_move_sids.new }}", "Distro": "{{ wsl_move_distro }}",
                                  "Values": "{{ wsl_move_registration_values | to_json }}"}
    assert REPAIR["vars"]["ansible_become_user"] == "SYSTEM"
    assert REPAIR["when"] == ["(wsl_move_marker_read.output | last | default('')) == 'window-done'",
                              "'new' in wsl_move_sids"]
    assert "SupportsShouldProcess" in task["script"] and REPAIR["no_log"] is True
