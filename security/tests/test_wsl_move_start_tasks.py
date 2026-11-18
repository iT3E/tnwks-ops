"""The start tasks of the account that owns the distro (Design Doc 0004, section 5.12; plan task 10.10).

Each WSL role registers a copy of each of its start tasks for that account, under a name of
its own. The phase of a host (wsl_move_phase) decides: off (every host today) is as before;
move registers each copy disabled and keeps the enabled state of each start task of either
account as the window set it; done keeps each copy enabled and leaves the tasks of the
present account alone. Both accounts may write to the folders of the start tasks while the
move runs; once it is done, only the account that owns the distro.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import yaml
from test_wsl_start_unelevated import needs_pwsh, pwsh
from winacl import ACL_FAKES

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLES = ANSIBLE / "roles"
GROUP = yaml.safe_load((ANSIBLE / "inventory/group_vars/windows_hosts.yml").read_text())
TASK_MODULE = ANSIBLE / "playbooks/library/tnwks_win_scheduled_task.ps1"
DIR_MODULE = ANSIBLE / "playbooks/library/tnwks_win_dir_acl.ps1"
MOVE = "{{ wsl_move_phase == 'move' }}"
SYSTEM_BECOME = {"ansible_become": True, "ansible_become_method": "ansible.builtin.runas", "ansible_become_user": "SYSTEM"}


def tasks(role: str) -> list[dict]:
    return yaml.safe_load((ROLES / role / "tasks/main.yml").read_text())


def named(role: str, name: str) -> dict:
    return next(task for task in tasks(role) if task.get("name") == name)


# --- The phase -------------------------------------------------------------------------

def test_the_phase_is_off_on_every_host_until_wsl_move_is_on():
    assert GROUP["wsl_move_done"] is False
    assert GROUP["wsl_move_task_suffix"] == "-account"
    phase = " ".join(GROUP["wsl_move_phase"].split())
    assert phase == ("{{ 'off' if not (wsl_move | default(false) | bool) "
                     "else ('done' if wsl_move_done | bool else 'move') }}")
    assert len(GROUP["wsl_move_new_description"]) <= 48
    # No host sets wsl_move or wsl_move_done in a public file: both are off everywhere.
    for path in (ANSIBLE / "inventory/host_vars").glob("*/main.yml"):
        data = yaml.safe_load(path.read_text()) or {}
        assert not {"wsl_move", "wsl_move_done", "wsl_move_phase"} & set(data), path


# --- windows_wsl_maintenance -----------------------------------------------------------

MAINTENANCE = "windows_wsl_maintenance"
DEFAULTS = yaml.safe_load((ROLES / MAINTENANCE / "defaults/main.yml").read_text())


def test_the_present_start_tasks_keep_their_state_while_the_move_runs_and_are_left_once_done():
    for name in ("Watchdog task (every 5 min and at boot)", "Keepalive task (one wsl.exe client from boot)",
                 "vhdx compaction task (on demand)"):
        task = named(MAINTENANCE, name)
        module = task["tnwks_win_scheduled_task"]
        assert module["keep_task_enabled_state"] == MOVE, name
        assert "s4u_user" not in module and "vars" not in task, name
        assert "wsl_move_phase != 'done'" in task["when"], name
    assert named(MAINTENANCE, "Keepalive task (one wsl.exe client from boot)")["when"] == (
        "wsl_keepalive_enabled | bool and wsl_move_phase != 'done'")


def test_the_copies_are_tasks_of_the_account_that_owns_the_distro_registered_as_system():
    task = named(MAINTENANCE, "Watchdog and keepalive tasks of the account that owns the distro")
    module = task["tnwks_win_scheduled_task"]
    assert task["when"] == "wsl_move_phase != 'off'"
    assert module["s4u_user"] == "{{ wsl_move_new_account }}"
    assert module["s4u_description"] == "{{ wsl_move_new_description }}"
    assert module["keep_task_enabled_state"] == MOVE
    assert module["running"] == "{{ item.running }}"
    # While the move runs, a copy is registered disabled: only the window enables it.
    assert "regex_replace('<Settings>', '<Settings><Enabled>false</Enabled>' if wsl_move_phase == 'move'" in module["xml"]
    assert "count=1" in module["xml"]
    become = {key: value for key, value in task["vars"].items() if key.startswith("ansible_become")}
    assert become == SYSTEM_BECOME
    assert task["vars"]["wsl_maintenance_user"] == "{{ wsl_move_new_account }}"
    assert task["vars"]["wsl_watchdog_task_args"] == "{{ wsl_watchdog_args_account }}"
    assert DEFAULTS["wsl_watchdog_task_args"] == "{{ wsl_watchdog_args }}"
    watchdog = (ROLES / MAINTENANCE / "templates/wsl-watchdog.xml.j2").read_text()
    assert "{{ wsl_watchdog_task_args | e }}" in watchdog and "wsl_watchdog_args" not in watchdog
    assert task["no_log"] is True
    loop = " ".join(task["loop"].split())
    assert "'name': wsl_watchdog_task_account, 'template': 'wsl-watchdog.xml.j2', 'running': false" in loop
    assert "'name': wsl_keepalive_task_account, 'template': 'wsl-keepalive.xml.j2', 'running': true" in loop
    assert "if wsl_keepalive_enabled | bool else []" in loop
    assert DEFAULTS["wsl_watchdog_task_account"] == "{{ wsl_watchdog_task }}{{ wsl_move_task_suffix }}"
    assert DEFAULTS["wsl_keepalive_task_account"] == "{{ wsl_keepalive_task }}{{ wsl_move_task_suffix }}"


def test_the_move_needs_the_start_with_no_elevation():
    task = named(MAINTENANCE, "The move of the distro needs the start with no elevation")
    assert task["ansible.builtin.assert"]["that"] == "wsl_move_phase == 'off' or wsl_start_unelevated | bool"
    names = [t.get("name") for t in tasks(MAINTENANCE)]
    assert names.index("The move of the distro needs the start with no elevation") < names.index(
        "Watchdog task (every 5 min and at boot)")


def test_the_copy_of_the_watchdog_names_the_copy_of_the_keepalive():
    args = " ".join(DEFAULTS["wsl_watchdog_args_account"].split())
    pattern = re.search(r"regex_replace\('(.*?)' ~ \(wsl_keepalive_task \| regex_escape\) ~ '(.*?)',", args)
    # The regex as Ansible reads it from the YAML text: each backslash as it is.
    regex = pattern[1] + re.escape("tnwks-wsl-keepalive") + pattern[2]
    replace = lambda text: re.sub(regex, r"\g<1>" + "tnwks-wsl-keepalive-account", text)
    host = yaml.safe_load((ANSIBLE / "inventory/host_vars/WSL-02/main.yml").read_text())
    assert replace(host["wsl_watchdog_args"]) == "-SkipGateway -SkipUsb -KeepAliveTask tnwks-wsl-keepalive-account"
    assert replace("-KeepAliveTask tnwks-wsl-keepalive -SkipUsb") == "-KeepAliveTask tnwks-wsl-keepalive-account -SkipUsb"
    assert replace("-KeepAliveTask tnwks-wsl-keepalive2") == "-KeepAliveTask tnwks-wsl-keepalive2"
    assert replace("") == ""
    assert "'\\g<1>' ~ wsl_keepalive_task_account" in args


def test_the_wsl_service_task_starts_the_copy_of_the_keepalive_once_the_move_is_done():
    assert DEFAULTS["wsl_service_keepalive_task"] == (
        "{{ wsl_keepalive_task_account if wsl_move_phase == 'done' else wsl_keepalive_task }}")
    service = (ROLES / MAINTENANCE / "templates/wsl-service.xml.j2").read_text()
    assert '-KeepAliveTask "{{ wsl_service_keepalive_task | e }}"' in service
    assert "wsl_keepalive_task |" not in service


def test_both_accounts_write_the_log_of_the_watchdog_while_the_move_runs():
    writers = " ".join(DEFAULTS["wsl_maintenance_writers"].split())
    assert writers == ("{{ [wsl_maintenance_user] if wsl_move_phase == 'off' else ([wsl_maintenance_user, "
                       "wsl_move_new_account] if wsl_move_phase == 'move' else [wsl_move_new_account]) }}")
    logs = named(MAINTENANCE, "Log directories of the watchdog and the WSL service task")
    assert logs["loop"][0] == {"path": "{{ wsl_watchdog_log_dir }}", "writer": "{{ wsl_maintenance_writers }}"}
    assert logs["loop"][1] == {"path": "{{ wsl_service_log_dir }}", "writer": []}


# --- tnwks_win_scheduled_task: running and a disabled task -----------------------------

TASK = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Settings>{enabled}<Hidden>false</Hidden></Settings>
  <Triggers><BootTrigger><Enabled>true</Enabled></BootTrigger></Triggers>
</Task>"""


def run_running(state: str, keep: bool, exists: bool = True, enabled: str = "") -> dict:
    module = TASK_MODULE.read_text()
    function = module[module.index("function Test-TnwksEnabled"):module.index("# Each exception of the chain")]
    block = module[module.index("if ($module.Params.running) {"):module.index("$current = Get-ScheduledTask")]
    xml = base64.b64encode(TASK.format(enabled=enabled).encode()).decode()
    body = function + f"""
$script:Started = 0
function Get-ScheduledTask {{ param($TaskPath, $TaskName, $ErrorActionPreference) [pscustomobject]@{{ State = '{state}' }} }}
function Start-ScheduledTask {{ param($TaskPath, $TaskName) $script:Started++ }}
$module = [pscustomobject]@{{ Params = @{{ running = $true }}; Result = @{{ changed = $false }} }}
$check = $false
$path = '\\'; $name = 'tnwks-x'
$keep = ${str(keep).lower()}
$task = {'[pscustomobject]@{ State = "x" }' if exists else '$null'}
$xml = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{xml}'))
{block}
[Console]::Out.Write((ConvertTo-Json -Compress @{{ started = $script:Started; changed = $module.Result.changed }}))
"""
    run = pwsh(body)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.splitlines()[-1])


@needs_pwsh
def test_running_does_not_start_a_task_that_is_disabled_when_the_state_is_the_tasks_own():
    # As before: with no option, a task that does not run is started.
    assert run_running("Ready", keep=False) == {"started": 1, "changed": True}
    assert run_running("Disabled", keep=False) == {"started": 1, "changed": True}
    assert run_running("Running", keep=True) == {"started": 0, "changed": False}
    # With the option, a disabled task stays as it is.
    assert run_running("Ready", keep=True) == {"started": 1, "changed": True}
    assert run_running("Disabled", keep=True) == {"started": 0, "changed": False}
    # A new task in a check run: the state of its xml (a trigger's Enabled does not count).
    assert run_running("", keep=True, exists=False, enabled="<Enabled>false</Enabled>")["started"] == 0
    assert run_running("", keep=True, exists=False, enabled="<Enabled>true</Enabled>")["started"] == 1
    assert run_running("", keep=True, exists=False)["started"] == 1


# --- tnwks_win_dir_acl: a list of writers ----------------------------------------------

DIR_RULES = sorted([
    "S-1-5-18|FullControl|ContainerInherit, ObjectInherit|None",
    "S-1-5-32-544|FullControl|ContainerInherit, ObjectInherit|None",
    "S-1-5-32-545|ReadAndExecute, Synchronize|ContainerInherit, ObjectInherit|None",
])


def writer_rules(sid: str) -> list[str]:
    return [f"{sid}|WriteData, ReadAndExecute, Synchronize|None|None", f"{sid}|Modify, Synchronize|ObjectInherit|InheritOnly"]


def run_writers(writers: str) -> dict:
    text = DIR_MODULE.read_text()
    logic = text[text.index("# --- BEGIN logic"):text.index("# --- END logic")]
    encoded = base64.b64encode(logic.encode()).decode()
    body = ACL_FAKES + f"""
. ([scriptblock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))))
$script:Have = New-TestAcl $true 'S-1-5-32-544' @()
$script:Exists = $true
$script:IsLink = $false
$changed = Invoke-TnwksDirAcl -Path 'C:\\ProgramData\\x\\log' -Writer {writers} -Check $false
$sets = @($script:Calls | Where-Object {{ $_ -is [hashtable] }} | ForEach-Object {{ @{{ rules = @(Get-TestKeys $_.Acl.Rules) }} }})
[Console]::Out.Write((ConvertTo-Json -Depth 5 -Compress @{{ changed = $changed; sets = $sets }}))
"""
    return json.loads(pwsh(body).stdout.splitlines()[-1])


@needs_pwsh
def test_the_folder_module_gives_each_writer_of_a_list_the_rules_of_a_writer():
    # The fakes translate kid1 to S-1-5-21-7-7-7-1002 and each other name to S-1-5-21-7-7-7-1001.
    one = run_writers("'adult1'")["sets"][0]["rules"]
    assert sorted(one) == sorted(DIR_RULES + writer_rules("S-1-5-21-7-7-7-1001"))
    two = run_writers("@('adult1', 'kid1')")["sets"][0]["rules"]
    assert sorted(two) == sorted(DIR_RULES + writer_rules("S-1-5-21-7-7-7-1001") + writer_rules("S-1-5-21-7-7-7-1002"))
    # An empty name and a name twice give no rule more; no writer gives the rules of a folder that is read only.
    assert sorted(run_writers("@('adult1', '', 'adult1')")["sets"][0]["rules"]) == sorted(one)
    assert sorted(run_writers("@()")["sets"][0]["rules"]) == DIR_RULES
    assert sorted(run_writers("''")["sets"][0]["rules"]) == DIR_RULES


def test_the_folder_module_takes_one_writer_or_a_list():
    text = DIR_MODULE.read_text()
    assert "writer = @{ type = 'list'; elements = 'str'; default = @() }" in text
    assert "param([string]$Path, [string[]]$Writer, [bool]$Check, [string]$Readers = 'users')" in text
    assert "foreach ($writer in @($WriterSid | Where-Object { $_ } | Select-Object -Unique)) {" in text
