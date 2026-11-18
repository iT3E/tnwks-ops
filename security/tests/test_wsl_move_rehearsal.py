"""The rehearsal of the move of a distro to another account (Design Doc 0004, section 9, step 0).

Role windows_wsl_move_rehearsal: two temporary accounts, A in the place of the present
account and B in the place of the new one, a small distro, and the scripts of the window:
the procedure (Invoke-TnwksMoveWindow.ps1, as SYSTEM) and the steps that each account
runs for itself (Invoke-TnwksWslAccountTask.ps1). These tests read the role, its
templates and its wiring, and run the scripts in PowerShell with fakes for wsl.exe, the
registry, the scheduled tasks, the groups, the processes and the rules of files. What
only the host can show (WSL, the rules of NTFS, S4U, a restart) is the rehearsal's own
work, phase by phase (docs/wsl/windows-ansible.md).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from pathlib import Path

import pytest
import yaml
from test_wsl_rehearsal import SYSTEM_BECOME, all_tasks, becomes
from test_wsl_start_unelevated import TASK_MODULE, needs_pwsh, pwsh

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLES = ANSIBLE / "roles"
ROLE = ROLES / "windows_wsl_move_rehearsal"
INVENTORY = ANSIBLE / "inventory"
DEFAULTS = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
WINDOW = ROLE / "files/Invoke-TnwksMoveWindow.ps1"
ACCOUNT = ROLE / "files/Invoke-TnwksWslAccountTask.ps1"
A_SID = "S-1-5-21-7-7-7-2001"
B_SID = "S-1-5-21-7-7-7-2002"
PERSON_SID = "S-1-5-21-7-7-7-1001"


def tasks_of(name: str) -> list[dict]:
    return all_tasks(yaml.safe_load((ROLE / "tasks" / name).read_text()))


# --- The role, its wiring and its templates ------------------------------------------------

def test_the_rehearsal_of_the_move_runs_on_the_site_2_host_only_and_last():
    group = yaml.safe_load((INVENTORY / "group_vars/windows_hosts.yml").read_text())
    assert group["wsl_move_rehearsal"] is False
    on = {path.parent.name for path in INVENTORY.glob("host_vars/*/main.yml")
          if (yaml.safe_load(path.read_text()) or {}).get("wsl_move_rehearsal")}
    # The rehearsal ended: the switch is off on every host until the next one. A
    # rehearsal runs on the site 2 host only. The pull request of the next
    # rehearsal, which turns the switch on for the site 2 host, removes the
    # second assert.
    assert on <= {"WSL-02"}
    assert on == set()
    # The switch is set only in the group vars of the Windows hosts and in the host vars
    # of the site 2 host: not inline in hosts.yml, not in another vars file. A value in a
    # SOPS file is out of reach of this test (it reads no SOPS file).
    # The key plain or quoted, in block or flow form.
    setting = re.compile(r"(?<![\w-])([\"']?)wsl_move_rehearsal\1\s*:")
    for text in ("wsl_move_rehearsal: true", '"wsl_move_rehearsal": true', "{'wsl_move_rehearsal' : true}",
                 "x: {wsl_move_rehearsal: true}"):
        assert setting.search(text), text
    for text in ("wsl_move_rehearsal_phase: none", '"wsl_move_rehearsal_phase": none', "my_wsl_move_rehearsal: true",
                 "when: wsl_move_rehearsal | bool"):
        assert not setting.search(text), text
    where = sorted(str(path.relative_to(INVENTORY)) for path in INVENTORY.rglob("*")
                   if path.is_file() and ".sops." not in path.name and setting.search(path.read_text(errors="replace")))
    assert where == ["group_vars/windows_hosts.yml", "host_vars/WSL-02/main.yml"], where
    play = yaml.safe_load((ANSIBLE / "playbooks/windows-host.yml").read_text())
    hosts = next(p for p in play if p["hosts"] == "windows_hosts")
    assert hosts["roles"][-1] == {"role": "windows_wsl_move_rehearsal", "when": "wsl_move_rehearsal | bool",
                                  "tags": ["wsl-move-rehearsal"]}
    # A run with no phase only checks the guards.
    assert DEFAULTS["wsl_move_rehearsal_phase"] == "none"
    main = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    assert main[-1]["when"] == "wsl_move_rehearsal_phase != 'none'"
    assert main[-1]["ansible.builtin.include_tasks"] == "{{ wsl_move_rehearsal_phase }}.yml"
    for phase in DEFAULTS["wsl_move_rehearsal_phases"][1:]:
        assert (ROLE / f"tasks/{phase}.yml").exists(), phase


def test_no_name_or_path_of_the_rehearsal_is_one_of_the_live_distro():
    """By design: each name has the prefix of the rehearsal, each path is under its own
    folder, and the scripts refuse the name of the live distro whatever the pattern."""
    prefix = DEFAULTS["wsl_move_rehearsal_prefix"]
    live = {yaml.safe_load((ROLES / role / "defaults/main.yml").read_text())[name]
            for role, name in (("windows_observability_storage", "observability_storage_distro"),
                               ("windows_wsl_maintenance", "wsl_keepalive_distro"))}
    assert live == {"Ubuntu"} and live <= set(DEFAULTS["wsl_move_rehearsal_refused_names"])
    assert DEFAULTS["wsl_move_rehearsal_distro"].startswith(prefix)
    assert DEFAULTS["wsl_move_rehearsal_distro"] not in live
    assert DEFAULTS["wsl_move_rehearsal_dir"] == f"C:\\ProgramData\\{prefix}"
    for key, account in DEFAULTS["wsl_move_rehearsal_accounts"].items():
        assert account["name"] == f"{prefix}-{key}" and len(account["name"]) <= 20
        # New-LocalUser of Windows PowerShell 5.1 takes 48 characters at most.
        assert len(account["description"]) <= 48, account["description"]
        for field in ("folder", "log_dir"):
            assert account[field].startswith("{{ wsl_move_rehearsal_dir }}\\"), (key, field)
    for name, value in DEFAULTS.items():
        if isinstance(value, str) and "\\" in value and name not in (
                "wsl_move_rehearsal_dir", "wsl_move_rehearsal_service_binary", "wsl_move_rehearsal_userlist_key"):
            assert value.startswith(("{{ wsl_move_rehearsal_dir }}\\", "{{ wsl_move_rehearsal_state_dir }}\\")), name
    site2 = yaml.safe_load((INVENTORY / "host_vars/WSL-02/main.yml").read_text())
    assert prefix not in site2["vhdx_compact_vhdx"].lower()
    guard = yaml.safe_load((ROLE / "tasks/main.yml").read_text())[1]["ansible.builtin.assert"]["that"]
    text = " ".join(guard)
    for needle in ("wsl_move_rehearsal_refused_names", "windows_desktop_user", "vhdx_compact_vhdx", "regex_escape"):
        assert needle in text, needle
    config = yaml.safe_load((ROLE / "vars/main.yml").read_text())
    assert config["wsl_move_rehearsal_account_config_common"]["refused_names"] == "{{ wsl_move_rehearsal_refused_names }}"
    assert config["wsl_move_rehearsal_window_config"]["account_pattern"] == "^{{ wsl_move_rehearsal_prefix }}-"


def test_the_accounts_tasks_run_by_s4u_with_no_elevation_and_no_trigger():
    text = (ROLE / "templates/account-task.xml.j2").read_text()
    assert "<LogonType>S4U</LogonType>" in text and "<RunLevel>LeastPrivilege</RunLevel>" in text
    assert "<Triggers />" in text and "Trigger>" not in text
    # Only the start task of B is registered disabled; the window tasks stay enabled.
    enabled = re.findall(r"\{% if ([^%]*) %\}\s*(?:<!--.*?-->\s*)?<Enabled>false</Enabled>", text, re.DOTALL)
    assert enabled == ["task.step == 'start' and task.key == 'b'"]
    assert text.count("<Enabled>") == 1
    assert "-d {{ wsl_move_rehearsal_distro | e }} -u root -e /bin/sleep infinity" in text
    system = (ROLE / "templates/system-task.xml.j2").read_text()
    assert "<UserId>S-1-5-18</UserId>" in system
    assert re.search(r"\{% if item == 'boot' %\}\s*<Triggers>\s*<BootTrigger>", system)
    assert "{% if item == 'boot' %} -Mode boot{% else %} -Request" in system
    tasks = DEFAULTS["wsl_move_rehearsal_tasks"]
    for key in ("a", "b"):
        assert {"stop", "unregister", "import", "start", "probe", "check"} <= set(tasks[key]), key


def test_only_the_rehearsal_sets_the_option_that_keeps_the_enabled_state():
    """keep_task_enabled_state is off by default; no other role sets it, so the tasks of
    the other roles compare and register as before."""
    module = TASK_MODULE.read_text()
    assert "keep_task_enabled_state = @{ type = 'bool'; default = $false }" in module
    # The WSL roles keep the state of their start tasks while the move of the distro runs only
    # (test_wsl_move_start_tasks.py).
    start_roles = ("windows_wsl_maintenance", "windows_lan_bridge", "windows_udp_relay")
    others = [path for path in ROLES.glob("*/tasks/*.yml") if path.parent.parent.name not in (ROLE.name, *start_roles)]
    assert others and not any("keep_task_enabled_state" in path.read_text() for path in others)
    for role in start_roles:
        text = (ROLES / role / "tasks/main.yml").read_text()
        values = re.findall(r"keep_task_enabled_state: (.*)", text)
        assert all(value == "\"{{ wsl_move_phase == 'move' }}\"" for value in values), role
    for path in ANSIBLE.glob("playbooks/*.yml"):
        assert "keep_task_enabled_state" not in path.read_text(), path.name
    setup = next(t for t in tasks_of("host_files.yml") if t.get("name") == "Tasks of the accounts")
    assert setup["tnwks_win_scheduled_task"]["keep_task_enabled_state"] == "{{ task.step == 'start' }}"
    again = next(t for t in tasks_of("window.yml") if t.get("name") == "An apply after the window does not change the start tasks")
    assert again["tnwks_win_scheduled_task"]["keep_task_enabled_state"] is True
    assert again["failed_when"] == "wsl_move_rehearsal_keep.changed or wsl_move_rehearsal_keep.failed | default(false)"


def test_each_task_that_acts_on_a_scheduled_task_runs_as_system():
    acting, becoming = [], []
    for path in sorted((ROLE / "tasks").glob("*.yml")) + [ANSIBLE / "playbooks/windows-move-rehearsal-check.yml"]:
        for task in all_tasks(yaml.safe_load(path.read_text())):
            script = str((task.get("ansible.windows.win_powershell") or {}).get("script", ""))
            params = (task.get("ansible.windows.win_powershell") or {}).get("parameters") or {}
            module = task.get("tnwks_win_scheduled_task") or {}
            # The tasks of SYSTEM register as in the other roles; each task of an account goes through SYSTEM.
            if "system-task.xml.j2" in str(module.get("xml")):
                continue
            if module or "Runner" in params or re.search(
                    r"(Start|Stop|Enable|Disable|Unregister|Register)-ScheduledTask", script):
                acting.append((path.name, task["name"]))
            if becomes(task):
                becoming.append((path.name, task["name"]))
                become = {k: v for k, v in task["vars"].items() if k.startswith("ansible_become")}
                assert become == SYSTEM_BECOME, task["name"]
    assert acting and set(acting) <= set(becoming)


def test_the_baseline_of_the_live_parts_is_read_before_the_first_account():
    names = [t["name"] for t in yaml.safe_load((ROLE / "tasks/setup.yml").read_text())]
    order = ["The root file system is in the controller cache",
             "The state file holds no marker", "Folder of the rehearsal", "Folders of the procedure", "The live parts at the start",
             "The baseline of the live parts is kept, and it read the registrations", "The accounts of the rehearsal"]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    gate = next(t for t in tasks_of("setup.yml") if t.get("name") == order[5])
    assert "(wsl_move_rehearsal_baseline.output | last).registrations is not match('not read')" in gate["ansible.builtin.assert"]["that"]
    # The cached root file system is read on the controller first, before any
    # change of the host, and the message names the limit that downloads it.
    assert names[0] == order[0]
    cached = next(t for t in tasks_of("setup.yml") if t.get("name") == order[0])["ansible.builtin.assert"]
    assert cached["that"] == "(wsl_move_rehearsal_cache ~ '/' ~ (wsl_move_rehearsal_rootfs.url | basename)) is file"
    assert "--limit 'localhost,{{ inventory_hostname }}'" in cached["fail_msg"]
    controller = (ROLE / "tasks/controller.yml").read_text()
    assert 'checksum: "sha256:{{ wsl_move_rehearsal_rootfs.sha256 }}"' in controller
    # The phase command of the docs runs the controller play too.
    docs = (ROOT / "docs/wsl/windows-ansible.md").read_text()
    assert "playbooks/windows-host.yml --limit 'localhost,WSL-02' --tags wsl-move-rehearsal" in docs
    assert "windows-host.yml --limit WSL-02" not in docs
    marker = next(t for t in tasks_of("setup.yml") if t.get("name") == "The state file holds no marker")
    assert marker["ansible.builtin.assert"]["that"] == "(wsl_move_rehearsal_setup_markers.output | last | int) == 0"
    # Each read of the rules is the procedure's (Get-TnwksSddlParts converts an alias to
    # its SID), from the copy of the procedure on the host.
    for name in ("read_disk.yml", "older_disk.yml"):
        assert "{{ wsl_move_rehearsal_procedure_lib }}" in (ROLE / "tasks" / name).read_text(), name
    # No cmdlet of the role takes a plain password.
    for path in ROLE.rglob("*"):
        if path.is_file():
            assert "-AsPlainText" not in path.read_text(), path.name


def test_each_run_of_the_procedure_names_the_last_marker_that_its_phase_expects():
    # The window runs after setup, or again after a window that went back.
    # The way back runs after a done window, and also ends a started window that did not end.
    expected = {"window.yml": ["", "wayback-done"], "wayback.yml": ["window-done", "window-started", "boot-run"],
                "interrupt.yml": ["wayback-done"]}
    for phase, markers in expected.items():
        runs = [t for t in tasks_of(phase) if t.get("ansible.builtin.include_tasks") == "run_procedure.yml"]
        assert [t["vars"]["wsl_move_rehearsal_last_markers"] for t in runs] == [markers], phase
    run = (ROLE / "tasks/run_procedure.yml").read_text()
    assert "if ($last -notin @($LastMarkers)) {" in run
    restart = (ROLE / "tasks/restart.yml").read_text()
    assert "in ['window-started', 'wayback-done']" in restart


def test_the_window_reads_the_state_that_it_starts_from_before_any_task_starts():
    tasks = tasks_of("window.yml")
    names = [t.get("name", "") for t in tasks]
    first_run = next(i for i, t in enumerate(tasks) if t.get("ansible.builtin.include_tasks") in ("run_task.yml", "run_procedure.yml"))
    assert names.index("Read the state before the window") < first_run
    assert next(i for i, n in enumerate(names) if n.startswith("The window starts after setup or after a way back")) < first_run


def test_the_play_waits_past_the_limit_of_the_window_task_and_the_worst_case_is_under_it():
    limit = re.search(r"<ExecutionTimeLimit>PT(\d+)H</ExecutionTimeLimit>", (ROLE / "templates/system-task.xml.j2").read_text())
    limit_minutes = int(limit[1]) * 60
    wait = int(re.search(r"\$deadline = \(Get-Date\)\.AddMinutes\((\d+)\)", (ROLE / "tasks/run_procedure.yml").read_text())[1])
    assert wait > limit_minutes
    minutes = yaml.safe_load((ROLE / "vars/main.yml").read_text())["wsl_move_rehearsal_window_config"]["task_minutes"]
    # Four tasks of an account in the window and four waits for no WSL process
    # (2 minutes each); in the way back three tasks and the start tasks (3 minutes),
    # or in the group test four tasks and one wait (the stop of A before it leaves
    # the group). The fifth wait is that of the stop at the end of a run of the
    # group test: it runs only when the stop before the leave did not run, so a
    # run has at most one stop of A.
    window = (ROLE / "files/Invoke-TnwksMoveWindow.ps1").read_text()
    assert window.count("Wait-TnwksNoWsl $") == 5
    assert "if ($script:ImportBackRan -and $script:StopBeforeLeaveRan) {" in window
    worst = 4 * minutes + 4 * 2 + max(3 * minutes + 3, 4 * minutes + 2)
    assert worst <= limit_minutes - 30


def test_after_the_way_back_of_the_group_test_the_play_starts_a_and_reads_its_tokens():
    names = [t.get("name") for t in tasks_of("wayback.yml")]
    order = ["The window procedure, way back mode", "A may log on as a batch job again, with no group",
             "No VM of the rehearsal before the start task of A",
             "The start task of A, started by the play", "The tokens of the WSL processes of A",
             "No WSL process of A holds a token from its time in Administrators"]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    poll = next(t for t in tasks_of("wayback.yml") if t.get("name") == "No VM of the rehearsal before the start task of A")
    script = poll["ansible.windows.win_powershell"]["script"]
    # The set of the VM processes by process ID and start time, as the baseline
    # keeps them, not only their count.
    assert "$_.ProcessId" in script and "CreationDate.ToUniversalTime().ToString('o')" in script
    assert '"$($_.id) $($_.created)"' in script and poll["failed_when"] == "not (wsl_move_rehearsal_vm_count.output | last).same"
    live = (ROLE / "files/Get-TnwksMoveLiveState.ps1").read_text()
    assert "id = [int]$_.ProcessId" in live and "$_.CreationDate.ToUniversalTime().ToString('o')" in live
    tokens = (ROLE / "tasks/tokens.yml").read_text()
    assert "(attributes & 0x4) != 0" in tokens and "-contains 'S-1-5-32-544'" in tokens
    boot = (ROLE / "tasks/after-boot.yml").read_text()
    assert "LastBootUpTime" in boot


def test_a_check_run_starts_no_task_and_waits_for_nothing():
    """Each step that runs a task, the procedure, a move or a restart is in a block for a
    real run only. The registrations go through the module, which changes nothing in a
    check run, and the removal's script stops before its actions under WhatIf."""
    acting = ("run_task.yml", "run_procedure.yml", "b_cycle.yml", "procedure_stops.yml", "move_side.yml", "read_window.yml")
    for phase in DEFAULTS["wsl_move_rehearsal_phases"][1:]:
        for entry in yaml.safe_load((ROLE / f"tasks/{phase}.yml").read_text()):
            include = entry.get("ansible.builtin.include_tasks", "")
            assert include not in acting, (phase, entry["name"])
            assert "ansible.windows.win_reboot" not in entry, (phase, entry["name"])
            if "block" in entry and any(t.get("ansible.builtin.include_tasks") in acting or "ansible.windows.win_reboot" in t
                                        for t in all_tasks(entry["block"])):
                assert "not ansible_check_mode" in str(entry.get("when")), (phase, entry["name"])
    remove = (ROLE / "tasks/remove-before-restart.yml").read_text()
    assert "if ($WhatIfPreference) { $out.note = 'a check run stops no distro and removes no file'; return $out }" in remove
    play = yaml.safe_load((ANSIBLE / "playbooks/windows-move-rehearsal-check.yml").read_text())
    assert play[0]["check_mode"] is True
    check = (ROLE / "tasks/check.yml").read_text()
    assert "ScheduledTask" not in check.replace("Get-ScheduledTask", "")


def test_a_restart_needs_the_owners_go_and_only_the_phase_restart_has_one():
    restart = yaml.safe_load((ROLE / "tasks/restart.yml").read_text())
    assert restart[0]["ansible.builtin.assert"]["that"] == "ansible_check_mode or (wsl_move_rehearsal_restart_go | bool)"
    assert DEFAULTS["wsl_move_rehearsal_restart_go"] is False
    reboots = [path.name for path in (ROLE / "tasks").glob("*.yml") if "win_reboot" in path.read_text()]
    assert reboots == ["restart.yml"]
    assert not any("Restart-Computer" in path.read_text() for path in (ROLE / "tasks").glob("*.yml"))
    # The procedure and the boot task restart only with the switch of their config on.
    assert DEFAULTS["wsl_move_rehearsal_restart"] is False
    config = yaml.safe_load((ROLE / "vars/main.yml").read_text())["wsl_move_rehearsal_window_config"]
    assert config["restart"] == "{{ wsl_move_rehearsal_restart | bool }}"
    assert config["boot"] == {"group_steps": False, "rehearsal_group_test": False}
    # The read of window step 7 that no compute system runs: off in the rehearsal only.
    assert config["vm_check"] is False
    text = WINDOW.read_text()
    assert text.count("Restart-Computer") == 1
    assert re.search(r"if \(\$cfg\.restart\) \{\s*Write-Log \"restart: \$Why\"\s*Restart-Computer -Force", text)


def test_the_procedure_reads_the_enabled_flag_of_a_task_not_its_state():
    """A disabled task with a running instance reads Running (TASK_STATE_DISABLED only
    with no instance), so a disable or enable is checked on Settings.Enabled."""
    text = WINDOW.read_text()
    assert "function Test-TnwksTaskEnabled([string]$Task) { [bool](Get-ScheduledTask -TaskName $Task).Settings.Enabled }" in text
    for name in ("Disable-TnwksTask", "Enable-TnwksTask"):
        body = re.search(rf"function {name}\(.*?\n\}}", text, re.DOTALL)[0]
        assert "Test-TnwksTaskEnabled" in body and ".State" not in body, name


def test_the_rules_compare_an_alias_and_a_sid_as_one_account():
    text = WINDOW.read_text()
    parts = re.search(r"function Get-TnwksSddlParts\(.*?\n\}", text, re.DOTALL)[0]
    assert "ConvertTo-TnwksSddlSid $Matches[1]" in parts and "$f[-1] = ConvertTo-TnwksSddlSid $f[-1]" in parts
    assert "(New-Object Security.Principal.SecurityIdentifier $Account).Value" in text


def test_each_line_of_the_log_and_each_marker_is_flushed_before_the_next_step():
    text = WINDOW.read_text()
    assert "$stream.Flush($true)" in text
    assert re.search(r"function Write-Log[^}]*Write-TnwksLine \$cfg\.log_file", text)
    assert re.search(r"function Add-Marker[^}]*Write-TnwksLine \$cfg\.state_file", text)
    # No other write to the log or the state file.
    assert text.count("Write-TnwksLine $cfg.") == 2
    assert not re.search(r"(WriteAllText|AppendAllText|Set-Content|Add-Content|Out-File)[^\n]*\$cfg\.(log_file|state_file)", text)


def test_the_removal_takes_the_accounts_out_of_every_group_first():
    # remove-before-restart first ends a run of the procedure, which could put A
    # back into Administrators.
    before = yaml.safe_load((ROLE / "tasks/remove-before-restart.yml").read_text())
    assert [t["ansible.builtin.include_tasks"] for t in before[:2]] == ["stop_procedure.yml", "groups_out.yml"]
    assert yaml.safe_load((ROLE / "tasks/remove.yml").read_text())[0]["ansible.builtin.include_tasks"] == "groups_out.yml"
    stop = (ROLE / "tasks/stop_procedure.yml").read_text()
    assert "{{ wsl_move_rehearsal_window_task }}" in stop and "Stop-ScheduledTask -TaskName $t" in stop
    # remove: the gate of the loaded profiles comes before the tasks go.
    names = [t["name"] for t in yaml.safe_load((ROLE / "tasks/remove.yml").read_text())]
    assert names.index("No profile of the accounts") < names.index("No task of the rehearsal")
    out = (ROLE / "tasks/groups_out.yml").read_text()
    assert "if ($g.SID.Value -eq 'S-1-5-32-545') { continue }" in out
    remove = (ROLE / "tasks/remove.yml").read_text()
    # The phase remove runs no task of an account, so it loads no profile.
    assert "run_task.yml" not in remove and "Runner" not in remove


# --- The scripts of the accounts, in PowerShell -------------------------------------------

ACCOUNT_FAKES = r"""
$global:Reg = @{}
$global:RootValues = @{}
$global:Sets = New-Object System.Collections.Generic.List[string]
function Test-Path {
    [CmdletBinding()] param([string]$LiteralPath, [string]$Path)
    $p = if ($LiteralPath) { $LiteralPath } else { $Path }
    if ($p -like 'HKCU:*') { return $true }
    Microsoft.PowerShell.Management\Test-Path -LiteralPath $p
}
function Get-ChildItem {
    [CmdletBinding()] param([string]$LiteralPath)
    foreach ($g in @($global:Reg.Keys)) { [pscustomobject]@{ PSChildName = $g; PSPath = "REG:$g" } }
}
function Get-ItemProperty {
    [CmdletBinding()] param([string]$LiteralPath, [string]$Name)
    if ($LiteralPath -like 'REG:*') {
        $o = [pscustomobject]$global:Reg[$LiteralPath.Substring(4)]
        $o | Add-Member -NotePropertyName PSPath -NotePropertyValue $LiteralPath
        return $o
    }
    [pscustomobject]$global:RootValues
}
function Set-ItemProperty { [CmdletBinding()] param([string]$LiteralPath, [string]$Name, $Value, [string]$Type) $global:Sets.Add("$Name=$Value") }
function New-Item { [CmdletBinding()] param([string]$Path, [switch]$Force) }
"""


# The fake wsl.exe. It records each call twice: as one line, and as its argv (fields
# split by 0x1f, calls by 0x1e). A call with -c must be the fixed command of
# Invoke-WslScript; the fake decodes its script and runs it in a real sh, with the
# paths of the distro under $TNWKS_FS and shims for adduser and id.
FAKE_WSL = r"""#!/bin/sh
echo "wsl $*" >> "$TNWKS_CALLS"
for a in "$@"; do printf '%s\037' "$a"; done >> "$TNWKS_ARGV"; printf '\036' >> "$TNWKS_ARGV"
c=""; last=""
for a in "$@"; do [ "$last" = "-c" ] && c=$a; last=$a; done
[ -n "$c" ] || exit 0
b64=$(printf '%s' "$c" | sed -n 's#^f=\$(mktemp) \&\& echo \([A-Za-z0-9+/=]*\) | base64 -d > \$f \&\& exec /bin/sh -e \$f$#\1#p')
[ -n "$b64" ] || { echo "not the command of Invoke-WslScript"; exit 99; }
f=$(mktemp "$TNWKS_FS/tmp/tmp.XXXXXX")
printf '%s' "$b64" | base64 -d | sed "s#/etc/#$TNWKS_FS/etc/#g; s#/usr/share/#$TNWKS_FS/usr/share/#g; s#/proc/#$TNWKS_FS/proc/#g" > "$f"
PATH="$TNWKS_SHIMS:$PATH" exec /bin/sh -e "$f"
"""
SHIMS = {
    "adduser": '#!/bin/sh\n[ "$1 $2" = "-D -u" ] || exit 2\necho "$4:x:$3" >> "$TNWKS_FS/etc/passwd"\n',
    "id": '#!/bin/sh\nif [ "$1" = "-u" ] && [ -n "$2" ]; then\n'
          '  grep "^$2:" "$TNWKS_FS/etc/passwd" | cut -d : -f 3 | grep . || exit 1\nelse exec /usr/bin/id "$@"; fi\n',
}
DISTRIBUTION_CONF = DEFAULTS["wsl_move_rehearsal_distribution_conf"]


def run_account(tmp_path: Path, step: str, registered: str | None = None, disk_at: str | None = None,
                distro: str = "tnwks-moverh", pattern: str = "^tnwks-moverh", folder: str | None = None,
                config: dict | None = None, rootfs: bool = False, fakes: str = "", account: Path = ACCOUNT) -> dict:
    """Runs one step of the account script. registered: the folder where the account
    lists the distro (a, b, or None). disk_at: the folder that holds a disk file.
    rootfs: a root file system with the SHA-256 of the config. The distro's files
    are under fs/."""
    root = tmp_path / "root"
    for sub in ("a", "b", "elsewhere", "log"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    fs = tmp_path / "fs"
    for sub in ("etc", "tmp", "proc", "usr/share"):
        (fs / sub).mkdir(parents=True, exist_ok=True)
    (fs / "proc/meminfo").write_text("MemTotal:        2014784 kB\nMemFree:          1000 kB\n")
    shims = tmp_path / "shims"
    shims.mkdir(exist_ok=True)
    for name, text in SHIMS.items():
        (shims / name).write_text(text)
        (shims / name).chmod(0o755)
    system = tmp_path / "win/System32"
    system.mkdir(parents=True, exist_ok=True)
    calls = tmp_path / "calls.txt"
    calls.write_text("")
    argv = tmp_path / "argv.bin"
    argv.write_bytes(b"")
    wsl = system / "wsl.exe"
    wsl.write_text(FAKE_WSL)
    wsl.chmod(0o755)
    if disk_at:
        (root / disk_at / "ext4.vhdx").write_text("disk")
    (root / "rootfs.tar.gz").write_bytes(b"rootfs")
    cfg = {
        "name_pattern": pattern, "refused_names": ["Ubuntu"], "root": str(root), "distro": distro,
        "folder": str(root / "b"), "disk_name": "ext4.vhdx", "log_dir": str(root / "log"),
        "marker_name": "tnwks-move.marker", "rootfs": str(root / "rootfs.tar.gz"),
        "rootfs_sha256": hashlib.sha256(b"rootfs").hexdigest().upper() if rootfs else "00",
        "linux_user": "moverh", "data_token": "token", "icon_base64": DEFAULTS["wsl_move_rehearsal_icon_base64"],
        "distribution_conf": DISTRIBUTION_CONF, "wsl_conf": "[user]\ndefault = moverh\n",
        "registration_values": {"DefaultUid": 1000},
    }
    cfg.update(config or {})
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    reg = ""
    if registered:
        reg = f"$global:Reg['{{g1}}'] = @{{ DistributionName = '{distro}'; BasePath = '\\\\?\\{root / registered}'; VhdFileName = 'ext4.vhdx' }}"
    extra = f"-Folder '{folder}'" if folder else ""
    script = ACCOUNT_FAKES + fakes + f"""
$env:SystemRoot = '{tmp_path / "win"}'
$env:TNWKS_CALLS = '{calls}'
$env:TNWKS_ARGV = '{argv}'
$env:TNWKS_FS = '{fs}'
$env:TNWKS_SHIMS = '{shims}'
{reg}
& '{account}' -Config '{tmp_path / "config.json"}' -Step {step} {extra}
$code = $LASTEXITCODE
$name = if ('{folder or ""}') {{ '{step}-elsewhere' }} else {{ '{step}' }}
$file = Join-Path '{root / "log"}' "$name.json"
$result = if (Microsoft.PowerShell.Management\\Test-Path -LiteralPath $file) {{ Get-Content -LiteralPath $file -Raw | ConvertFrom-Json }} else {{ $null }}
[Console]::Out.Write((@{{ code = $code; result = $result; sets = @($global:Sets) }} | ConvertTo-Json -Compress -Depth 5))
"""
    run = pwsh(script)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    out["wsl"] = [line for line in calls.read_text().splitlines() if line]
    out["argv"] = [call.split("\x1f")[:-1] for call in argv.read_text().split("\x1e") if call]
    out["fs"] = fs
    out["disk_a"] = (root / "a/ext4.vhdx").exists()
    out["disk_b"] = (root / "b/ext4.vhdx").exists()
    return out


@needs_pwsh
def test_the_unregister_refuses_while_a_file_is_at_the_path_of_its_registration(tmp_path):
    out = run_account(tmp_path, "unregister", registered="b", disk_at="b")
    assert out["code"] == 2 and "a file is at" in out["result"]["refused"]
    assert out["wsl"] == [] and out["disk_b"]


@needs_pwsh
def test_the_read_of_a_file_at_the_path_fails_closed(tmp_path):
    """Only "not found" reads as no file: any other error (here a path that the
    read refuses) reads as a file there, so the unregister refuses."""
    text = ACCOUNT.read_text()
    func = re.search(r"function Test-TnwksFile\(.*?\n\}", text, re.DOTALL)[0]
    assert "if (Test-TnwksFile $reg.disk) {" in text and "Test-Path -LiteralPath $reg.disk" not in text
    (tmp_path / "there").write_text("x")
    run = pwsh(func + f"""
$out = @((Test-TnwksFile '{tmp_path / "there"}'), (Test-TnwksFile '{tmp_path / "none"}'),
         (Test-TnwksFile '{tmp_path / "no-dir/none"}'), (Test-TnwksFile "bad`0path"))
[Console]::Out.Write((ConvertTo-Json -Compress $out))
""")
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == [True, False, False, True]


# The rules of the disk file as the account reads and writes them (Get-Acl, Set-Acl), and
# its SID (whoami.exe /user).
STRIP_FAKES = r"""
function Get-TnwksFileSecurity {
    param([string]$Path, [string]$Sections)
    $o = [pscustomobject]@{ Sddl = $global:Sddl }
    $o | Add-Member -MemberType ScriptMethod -Name GetOwner -Value { param($t) [pscustomobject]@{ Value = ($this.Sddl -replace '^O:(.+?)D:.*$', '$1') } }
    $o | Add-Member -MemberType ScriptMethod -Name GetSecurityDescriptorSddlForm -Value { param($s) $this.Sddl -replace '^.*?(D:)', '$1' }
    $o
}
function Set-TnwksFileRules {
    param([string]$Path, [string]$Sddl)
    $global:Sets.Add("acl $(Split-Path -Leaf (Split-Path -Parent $Path))")
    $global:Sddl = ($global:Sddl -replace 'D:.*$', '') + $Sddl
}
"""


def account_with_fakes_for_the_rules(tmp_path: Path) -> Path:
    """A copy of the account script without its two functions of the security of a file
    (they call .NET methods of Windows only), so the fakes of STRIP_FAKES stand in."""
    text = ACCOUNT.read_text()
    copy, count = re.subn(r"\nfunction (Get-TnwksFileSecurity|Set-TnwksFileRules)\(.*?\n\}\n", "\n", text, flags=re.DOTALL)
    assert count == 2
    path = tmp_path / ACCOUNT.name
    path.write_text(copy)
    return path


@needs_pwsh
def test_the_strip_step_leaves_only_the_rule_of_the_account_on_its_own_disk_file(tmp_path):
    """The stated case of the phase wayback: the account, as the owner of its disk file,
    removes SYSTEM and the Administrators from its rules. It refuses a file that it does
    not own, and a missing file, with no change."""
    def run(case: str, owner: str, disk_at: str | None = "b") -> dict:
        d = tmp_path / case
        (d / "win/System32").mkdir(parents=True)
        whoami = d / "win/System32/whoami.exe"
        whoami.write_text(f"#!/bin/sh\nprintf '\"User Name\",\"SID\"\\n\"host\\\\b\",\"{B_SID}\"\\n'\n")
        whoami.chmod(0o755)
        return run_account(d, "strip-rules", disk_at=disk_at, account=account_with_fakes_for_the_rules(d), fakes=STRIP_FAKES + (
            f"$global:Sddl = 'O:{owner}D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{B_SID})'\n"))
    own = run("own", B_SID)
    assert own["code"] == 0, own
    assert own["result"]["owner_self"] is True
    assert own["result"]["system_or_admins"] == 0 and own["result"]["rules"] == 1
    assert own["sets"] == ["acl b"]
    for case, out in (("admins", run("admins", "BA")), ("none", run("none", B_SID, disk_at=None))):
        assert out["code"] == 2 and out["sets"] == [], case
    assert run("admins2", "BA")["result"]["refused"] == "the account does not own its disk file"


@needs_pwsh
def test_the_unregister_runs_only_with_no_file_at_its_path_and_no_distro_is_a_pass(tmp_path):
    out = run_account(tmp_path / "1", "unregister", registered="b", disk_at="a")
    assert out["wsl"] == ["wsl --unregister tnwks-moverh"] and out["disk_a"]
    none = run_account(tmp_path / "2", "unregister")
    assert none["code"] == 0 and none["result"]["note"] == "no distro" and none["wsl"] == []


@needs_pwsh
def test_the_import_refuses_another_path_and_passes_at_its_own(tmp_path):
    other = run_account(tmp_path / "1", "import", registered="b", disk_at="b", folder=str(tmp_path / "1/root/elsewhere"))
    assert other["code"] == 2 and "not at" in other["result"]["refused"]
    assert other["wsl"] == [] and other["sets"] == []
    same = run_account(tmp_path / "2", "import", registered="b", disk_at="b")
    assert same["code"] == 0 and same["result"]["existed"] is True and same["wsl"] == [] and same["sets"] == []


@needs_pwsh
def test_the_account_script_refuses_a_name_or_a_path_outside_its_scope(tmp_path):
    live = run_account(tmp_path / "1", "unregister", distro="Ubuntu")
    assert live["code"] == 2 and "outside the scope" in live["result"]["refused"]
    # The live name is refused even with a pattern that would take it.
    named = run_account(tmp_path / "2", "unregister", distro="Ubuntu", pattern=".*")
    assert named["code"] == 2 and "a refused name" in named["result"]["refused"]
    outside = run_account(tmp_path / "3", "import", folder=str(tmp_path))
    assert outside["code"] == 2 and "is outside" in outside["result"]["refused"]
    for out in (live, named, outside):
        assert out["wsl"] == [] and out["sets"] == []


# How Windows PowerShell 5.1 passes the arguments of a native program, and how the
# program splits its command line again (CommandLineToArgvW). 5.1 puts an argument
# with a space or a tab outside its own quotes in double quotes, and escapes no
# quote and no backslash in it. It drops an empty argument.
def ps51_command_line(args: list[str]) -> str:
    parts = []
    for arg in args:
        if arg == "":
            continue
        quotes, needs = 0, False
        for ch in arg:
            if ch == '"':
                quotes += 1
            elif ch in " \t" and quotes % 2 == 0:
                needs = True
                break
        parts.append(f'"{arg}"' if needs else arg)
    return " ".join(parts)


def windows_argv(line: str) -> list[str]:
    args, cur, quoted, have, i = [], [], False, False, 0
    while i < len(line):
        ch = line[i]
        if ch == "\\":
            n = 0
            while i < len(line) and line[i] == "\\":
                n, i = n + 1, i + 1
            if i < len(line) and line[i] == '"':
                cur.append("\\" * (n // 2))
                if n % 2:
                    cur.append('"')
                    i += 1
            else:
                cur.append("\\" * n)
            have = True
            continue
        if ch == '"':
            if quoted and i + 1 < len(line) and line[i + 1] == '"':
                cur.append('"')
                i += 2
                continue
            quoted, have = not quoted, True
        elif ch in " \t" and not quoted:
            if have:
                args.append("".join(cur))
            cur, have = [], False
        else:
            cur.append(ch)
            have = True
        i += 1
    if have:
        args.append("".join(cur))
    return args


def survives_ps51(args: list[str]) -> bool:
    return windows_argv(ps51_command_line(args)) == args


def test_the_model_of_windows_powershell_5_1_breaks_shell_text_with_quotes():
    """The model takes plain arguments through, and splits the texts that the
    account script passed before: the shell text of the prepare and of wsl.conf."""
    assert survives_ps51(["-d", "tnwks-moverh", "-e", "/bin/sh", "-c", "id -u; cat /etc/x"])
    assert survives_ps51(["--import", "tnwks-moverh", "C:\\ProgramData\\tnwks-moverh\\a", "--version", "2"])
    old_prepare = 'set -e; printf %s token > /etc/t; printf "[shortcut]\\nenabled = true\\n" > /etc/wsl-distribution.conf'
    old_wslconf = 'printf "[user]\\ndefault = moverh\\n" > /etc/wsl.conf'
    for text in (old_prepare, old_wslconf):
        got = windows_argv(ps51_command_line(["-e", "/bin/sh", "-c", text]))
        assert got != ["-e", "/bin/sh", "-c", text]
    # The text after the quote is a new argument: sh -c runs only the first part.
    assert windows_argv(ps51_command_line(["-c", old_wslconf]))[1] == "printf [user]\\ndefault"


# Each line of the Windows roles and plays that passes a -c flag (alone, or in a
# group such as -lc), in any form: in a command, or in an array that a call splats.
# Each was read: the shell text is fixed, with no quote or backslash in it.
REVIEWED_C_FLAGS = {
    # Not WSL: stat on the controller.
    ("Invoke-TnwksHostCheck.ps1", """[int](& stat -L -c '%h' "/proc/$PID/fd/$($Stream.SafeFileHandle.DangerousGetHandle())")"""),
    ("Watch-TnwksWsl.ps1", '$state = & wsl.exe -d $Distro -u root -e /bin/sh -c "runuser -u it3e -- env $probe" 2>&1 | Out-String'),
    ("Watch-TnwksWsl.ps1", '& wsl.exe -d $Distro -u root -e /bin/sh -c "runuser -u it3e -- env $cmd" 2>&1 | Out-Null'),
    ("Watch-TnwksWsl.ps1", "& wsl.exe -d $Distro -u root -e /bin/sh -c 'docker restart homelab-wsl-worker-2' 2>&1 | Out-Null"),
    ("Invoke-TnwksWslAccountTask.ps1", "$arguments = @('-d', $distro) + $user + @('-e', '/bin/sh', '-c', $command)"),
}
C_FLAG = re.compile(r"""(?:^|[\s,(@])['"]?-[a-z]*c['"]?(?=[\s,)]|$)""")


def test_no_shell_text_goes_to_wsl_exe_outside_the_safe_command():
    text = ACCOUNT.read_text()
    # Only Invoke-WslScript passes -c, with base64 in a command that has no quote
    # and no backslash of its own.
    assert text.count("'-c'") == 1
    func = re.search(r"function Invoke-WslScript\(.*?\n\}", text, re.DOTALL)[0]
    command = re.search(r"\$command = '([^']*)' -f \$b64", func)[1]
    assert command == "f=$(mktemp) && echo {0} | base64 -d > $f && exec /bin/sh -e $f"
    assert not re.search(r"['\"\\]", command)
    # Each other call of wsl.exe passes names, paths and flags only.
    for call in re.findall(r"Invoke-Wsl @\((.*?)\)", text):
        assert "/bin/sh" not in call and '"' not in call, call
    # Every -c flag in the Windows roles and plays is one that was read.
    found = set()
    paths = [*ANSIBLE.glob("roles/windows_*/files/*"), *ANSIBLE.glob("roles/windows_*/tasks/*.yml"),
             *ANSIBLE.glob("roles/windows_*/templates/*"), *ANSIBLE.glob("playbooks/windows-*.yml")]
    for path in paths:
        if not path.is_file() or path.suffix not in (".ps1", ".yml", ".j2", ".sh", ".xml", ".py"):
            continue
        for line in path.read_text().splitlines():
            if C_FLAG.search(line):
                found.add((path.name, line.strip()))
    assert found == REVIEWED_C_FLAGS
    # The parts that Watch-TnwksWsl puts into its shell text are fixed, with no quote.
    watch = (ANSIBLE / "roles/windows_wsl_maintenance/files/Watch-TnwksWsl.ps1").read_text()
    for name in ("probe", "cmd"):
        value = re.search(rf"^\s*\${name} = '([^']*)'$", watch, re.MULTILINE)[1]
        assert not re.search(r"[\"\\$]", value), value


def test_the_flag_search_finds_a_splatted_flag():
    for line in ("$a = @('-e', '/bin/sh', '-c', $x)", '& wsl.exe -e bash -lc "x"', "wsl.exe -- sh -c x",
                 '$args = @("-c", $x)'):
        assert C_FLAG.search(line), line
    for line in ("-Config $c", "--cpus 2", "$a = @('-d', $distro)", "Get-Content -Raw"):
        assert not C_FLAG.search(line), line


@needs_pwsh
def test_a_key_that_comes_twice_reads_as_no_value(tmp_path):
    func = re.search(r"function ConvertFrom-WslValues\(.*?\n\}", ACCOUNT.read_text(), re.DOTALL)[0]
    run = pwsh(func + """
$out = ConvertFrom-WslValues "uid=1000`ntoken=a`nmem_total_kib=1`nmem_total_kib=2014784`nicon_sha256=ab`n"
[Console]::Out.Write((@{ values = $out.values; repeated = @($out.repeated) } | ConvertTo-Json -Compress))
""")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["repeated"] == ["mem_total_kib"] and out["values"]["mem_total_kib"] is None
    assert out["values"]["uid"] == "1000" and out["values"]["token"] == "a" and out["values"]["icon_sha256"] == "ab"


@needs_pwsh
def test_a_script_too_long_for_one_command_line_does_not_run(tmp_path):
    out = run_account(tmp_path, "wslconf", config={"wsl_conf": "x" * 20000})
    assert out["code"] == 1 and "too long for one command line" in out["result"]["output"]
    assert out["wsl"] == []


@needs_pwsh
def test_the_account_steps_write_and_read_the_distro_through_windows_powershell_5_1(tmp_path):
    """Each argument survives the 5.1 rule; the scripts run in a real sh; each file
    has a newline at its end; the check reads each value by its key."""
    rootfs = run_account(tmp_path / "1", "import-rootfs", rootfs=True)
    assert rootfs["code"] == 0, rootfs
    assert rootfs["result"]["prepare_exit"] == 0 and rootfs["result"]["imported"] is True
    assert rootfs["result"]["existed"] is False
    fs = rootfs["fs"]
    assert (fs / "etc/tnwks-move-token").read_text() == "token\n"
    assert (fs / "etc/wsl-distribution.conf").read_text() == DISTRIBUTION_CONF
    assert DISTRIBUTION_CONF.endswith("icon = /usr/share/tnwks-move/icon.ico\n")
    icon = (fs / "usr/share/tnwks-move/icon.ico").read_bytes()
    assert hashlib.sha256(icon).hexdigest() == DEFAULTS["wsl_move_rehearsal_icon_sha256"]
    assert icon == base64.b64decode(DEFAULTS["wsl_move_rehearsal_icon_base64"])
    assert (fs / "etc/passwd").read_text() == "moverh:x:1000\n"
    assert not any((fs / "tmp").iterdir()), "the script removes its file"
    # A second setup over its own distro imports nothing and writes the files
    # again, as from the faulty prepare: no shortcut, a token with no newline.
    (fs / "etc/wsl-distribution.conf").unlink()
    (fs / "etc/tnwks-move-token").write_text("token")
    again = run_account(tmp_path / "1", "import-rootfs", registered="b")
    assert again["code"] == 0 and again["result"]["existed"] is True and again["result"]["prepare_exit"] == 0
    assert again["result"]["imported"] is False
    assert not any(c.startswith("wsl --import ") for c in again["wsl"])
    assert (fs / "etc/wsl-distribution.conf").read_text() == DISTRIBUTION_CONF
    assert (fs / "etc/tnwks-move-token").read_text() == "token\n"
    assert (fs / "etc/passwd").read_text() == "moverh:x:1000\n"
    # At another path it refuses and changes nothing.
    other = run_account(tmp_path / "1", "import-rootfs", registered="a")
    assert other["code"] == 2 and other["wsl"] == []
    wslconf = run_account(tmp_path / "1", "wslconf")
    assert wslconf["code"] == 0 and (fs / "etc/wsl.conf").read_text() == "[user]\ndefault = moverh\n"
    # The token file of the old prepare has no newline at its end.
    (fs / "etc/tnwks-move-token").write_text("token")
    check = run_account(tmp_path / "1", "check", registered="b")
    result = check["result"]
    assert result["read_exit"] == 0 and result["token"] == "token" and result["user_uid"] == "1000"
    assert result["repeated_count"] == 0 and result["repeated_keys"] == ""
    assert result["uid"].isdigit() and result["mem_total_kib"] == 2014784
    assert result["distribution_conf_sha256"] == hashlib.sha256(DISTRIBUTION_CONF.encode()).hexdigest()
    assert result["wsl_conf_sha256"] == hashlib.sha256(b"[user]\ndefault = moverh\n").hexdigest()
    assert result["icon_sha256"] == DEFAULTS["wsl_move_rehearsal_icon_sha256"]
    for out in (rootfs, again, wslconf, check):
        for call in out["argv"]:
            assert survives_ps51(call), call
    # A file that is not there reads as an empty value, and moves no other one.
    (fs / "etc/wsl.conf").unlink()
    again = run_account(tmp_path / "1", "check", registered="b")["result"]
    assert again["wsl_conf_sha256"] == "" and again["token"] == "token" and again["mem_total_kib"] == 2014784


def test_the_role_reads_back_each_file_that_it_writes_in_the_distro():
    setup = next(t for t in tasks_of("setup.yml") if t.get("name", "").startswith("A owns the distro"))
    that = setup["ansible.builtin.assert"]["that"]
    assert "check.result.distribution_conf_sha256 == wsl_move_rehearsal_distribution_conf | hash('sha256')" in that
    assert "check.result.icon_sha256 == wsl_move_rehearsal_icon_sha256" in that
    assert "check.result.user_uid == '1000'" in that and "check.result.mem_total_kib | int > 0" in that
    cycle = next(t for t in tasks_of("b_cycle.yml") if t.get("name", "").startswith("The second import of B"))
    assert "check.result.wsl_conf_sha256 == wsl_move_rehearsal_wsl_conf | hash('sha256')" in cycle["ansible.builtin.assert"]["that"]
    # A key that comes twice reads as no value: each read of the check step fails on it.
    window = next(t for t in tasks_of("window.yml") if t.get("name", "").startswith("B owns the distro"))
    for task in (setup, window, cycle):
        assert "check.result.repeated_count == 0" in task["ansible.builtin.assert"]["that"], task["name"]
    assert hashlib.sha256(base64.b64decode(DEFAULTS["wsl_move_rehearsal_icon_base64"])).hexdigest() == \
        DEFAULTS["wsl_move_rehearsal_icon_sha256"]
    configs = yaml.safe_load((ROLE / "vars/main.yml").read_text())["wsl_move_rehearsal_account_config_common"]
    assert configs["distribution_conf"] == "{{ wsl_move_rehearsal_distribution_conf }}"
    assert configs["wsl_conf"] == "{{ wsl_move_rehearsal_wsl_conf }}"


# --- The window procedure, in PowerShell --------------------------------------------------

WINDOW_FAKES = r"""
$global:Calls = New-Object System.Collections.Generic.List[string]
$global:Admins = New-Object System.Collections.Generic.List[string]
$global:Procs = New-Object System.Collections.Generic.List[object]
$global:Runs = @{}
# The compute systems of the Host Compute Service: a start task or an import starts a
# WSL VM of its account (owner WSL), and a stop task ends it. $global:HcsError: the read
# fails; $global:HcsText: the text that the read gives instead of the list.
# Add-TnwksTestSystem: a system with these properties, in the next $Reads reads only
# (-1: in each read).
$global:Vms = New-Object System.Collections.Generic.List[object]
$global:Systems = New-Object System.Collections.Generic.List[object]
class TnwksHcs {
    static [string] Enumerate() {
        $global:Calls.Add('hcs enumerate')
        if ($global:HcsError) { throw $global:HcsError }
        if ($null -ne $global:HcsText) { return $global:HcsText }
        return (& $global:HcsList)
    }
}
$global:HcsList = {
    $list = @($global:Vms | ForEach-Object {
        [pscustomobject]@{ Id = $_.Id; SystemType = 'VirtualMachine'; Owner = $_.Owner; State = 'Running' } })
    foreach ($x in $global:Systems.ToArray()) {
        if ($x.Reads -eq 0) { continue }
        $list += $x.System
        if ($x.Reads -gt 0) { $x.Reads = $x.Reads - 1 }
    }
    ConvertTo-Json -Compress -InputObject $list
}
function Add-TnwksTestVm([string]$Sid, [string]$Owner = 'WSL') {
    $global:Vms.Add([pscustomobject]@{ Id = [guid]::NewGuid().ToString(); Owner = $Owner; Sid = $Sid })
}
function Add-TnwksTestSystem([hashtable]$Properties, [int]$Reads = -1) {
    $global:Systems.Add([pscustomobject]@{ System = [pscustomobject]$Properties; Reads = $Reads })
}
# The key of the WSL installer: its install folder. $env:SystemRoot: the folder of Windows.
$global:WslInstall = 'C:\Program Files\WSL\'
$env:SystemRoot = 'C:\Windows'
function Get-ItemProperty {
    param([string]$LiteralPath, [string]$Name)
    if ($LiteralPath -ne 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Lxss\MSI' -or -not $global:WslInstall) { throw 'no such key' }
    [pscustomobject]@{ InstallLocation = $global:WslInstall }
}
function Get-LocalUser { param([string]$Name) [pscustomobject]@{ SID = [pscustomobject]@{ Value = $global:Sids[$Name] } } }
function Get-LocalGroupMember { param([string]$SID) @($global:Admins | ForEach-Object { [pscustomobject]@{ SID = [pscustomobject]@{ Value = $_ } } }) }
function Add-LocalGroupMember { param([string]$SID, $Member) $global:Calls.Add("join $Member"); $global:Admins.Add($Member) }
# $global:FailLeave: Windows refuses the leave.
# $global:OnLeave: what else happens at a leave (a script block).
function Remove-LocalGroupMember {
    param([string]$SID, $Member)
    if ($global:FailLeave) { $global:Calls.Add("refused leave $Member"); throw 'the leave is refused' }
    $global:Calls.Add("leave $Member"); [void]$global:Admins.Remove($Member)
    if ($global:OnLeave) { & $global:OnLeave }
}
# As Task Scheduler reads it (TASK_STATE): a task with a running instance is Running,
# also when it is disabled; Disabled only with no instance. Settings.Enabled is the flag.
$global:Enabled = @{}
$global:Running = @{}
# $global:QueuedPolls: after a start, each task of an account reads Queued with the
# result 0x41301 (the task is running) for that many reads of its run, though its last
# run time is already new, as a task that never ran can.
$global:Pending = @{}
function Get-ScheduledTask {
    param([string]$TaskName)
    $state = if ($global:Running[$TaskName]) { 'Running' } elseif ([int]$global:Pending[$TaskName] -gt 0) { 'Queued' } elseif ($global:Enabled[$TaskName] -eq $false) { 'Disabled' } else { 'Ready' }
    [pscustomobject]@{ State = $state; Settings = [pscustomobject]@{ Enabled = ($global:Enabled[$TaskName] -ne $false) } }
}
function Get-ScheduledTaskInfo {
    param([string]$TaskName)
    if ([int]$global:Pending[$TaskName] -gt 0) {
        $global:Pending[$TaskName] = [int]$global:Pending[$TaskName] - 1
        return [pscustomobject]@{ LastRunTime = [int]$global:Runs[$TaskName]; LastTaskResult = 0x41301 }
    }
    [pscustomobject]@{ LastRunTime = [int]$global:Runs[$TaskName]; LastTaskResult = [int]$global:Codes[$TaskName] }
}
function Disable-ScheduledTask { param([string]$TaskName) $global:Calls.Add("disable $TaskName"); $global:Enabled[$TaskName] = $false }
# $global:ThrowOnEnable: an error that no step handles (way back step 6).
function Enable-ScheduledTask {
    param([string]$TaskName)
    if ($global:ThrowOnEnable) { throw 'an error that no step handles' }
    $global:Calls.Add("enable $TaskName"); $global:Enabled[$TaskName] = $true
}
function Get-TaskSid([string]$TaskName) { if ($TaskName -like '*-a-*') { $global:Sids['tnwks-moverh-a'] } else { $global:Sids['tnwks-moverh-b'] } }
function Stop-ScheduledTask {
    param([string]$TaskName)
    $global:Calls.Add("stop $TaskName")
    if ($TaskName -like '*-start') {
        $global:Running[$TaskName] = $false
        foreach ($p in @($global:Procs | Where-Object { $_.Task -eq $TaskName })) { [void]$global:Procs.Remove($p) }
    }
}
# $global:RefuseStart: Task Scheduler refuses the start of these tasks, with an error
# over two lines. $global:NoRun: these tasks start with no error and never run.
function Start-ScheduledTask {
    param([string]$TaskName)
    $global:Calls.Add("start $TaskName")
    if (@($global:RefuseStart) -contains $TaskName) { throw "the task $TaskName can not start`r`naccess is denied" }
    if (@($global:NoRun) -contains $TaskName) { return }
    $global:Runs[$TaskName] = 1 + [int]$global:Runs[$TaskName]
    $sid = Get-TaskSid $TaskName
    if ($TaskName -like '*-start') {
        $global:Running[$TaskName] = $true
        $global:Procs.Add([pscustomobject]@{ Name = 'wsl.exe'; OwnerSid = $sid; Task = $TaskName })
        if (-not @($global:Vms | Where-Object { $_.Sid -eq $sid }).Count) { Add-TnwksTestVm $sid }
        return
    }
    if ($TaskName -like '*-import' -and -not @($global:Vms | Where-Object { $_.Sid -eq $sid }).Count) { Add-TnwksTestVm $sid }
    if ($global:QueuedPolls) { $global:Pending[$TaskName] = [int]$global:QueuedPolls }
    if ($TaskName -like '*-stop') {
        foreach ($p in @($global:Procs | Where-Object { $_.OwnerSid -eq $sid })) { [void]$global:Procs.Remove($p) }
        foreach ($v in @($global:Vms | Where-Object { $_.Sid -eq $sid })) { [void]$global:Vms.Remove($v) }
    }
    # An unregister deletes the icon of the distro in the folder of its account, unless
    # $global:KeepIcon (WSL only logs a failed delete).
    if ($TaskName -like '*-unregister' -and -not $global:KeepIcon) {
        $icon = Join-Path $(if ($TaskName -like '*-a-*') { $global:FolderA } else { $global:FolderB }) 'shortcut.ico'
        if (Test-Path -LiteralPath $icon) { Remove-Item -LiteralPath $icon -Force; $global:Calls.Add("icon deleted by $TaskName") }
    }
    # $global:OnStart: what else happens at the run of a task (a script block by task).
    if ($global:OnStart -and $global:OnStart.ContainsKey($TaskName)) { & $global:OnStart[$TaskName] }
    # $global:VmRule: the import attaches the disk file, and WSL grants its VM access.
    if ($global:VmRule -and $TaskName -like '*-import') {
        $disk = Join-Path $(if ($TaskName -like '*-a-*') { $global:FolderA } else { $global:FolderB }) 'ext4.vhdx'
        if ($global:Acls.ContainsKey($disk)) { $global:Acls[$disk] += $global:VmRule }
    }
    if ($global:Results.ContainsKey($TaskName)) {
        $r = $global:Results[$TaskName]
        $r | Add-Member -NotePropertyName time -NotePropertyValue ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -Force
        $step = ($TaskName -split '-')[-1]
        $dir = if ($TaskName -like '*-a-*') { $global:LogA } else { $global:LogB }
        [IO.File]::WriteAllText((Join-Path $dir "$step.json"), ($r | ConvertTo-Json -Compress))
    }
}
function Get-CimInstance { param([string]$ClassName) $global:Procs.ToArray() }
function Invoke-CimMethod { param($InputObject, [string]$MethodName) [pscustomobject]@{ Sid = $InputObject.OwnerSid } }
# The rules of a file, as the procedure reads and writes them (TnwksSecurity): a read
# with backup semantics always passes, and an owner may be set (the privileges of
# SYSTEM); the rules may be written only by the owner, or when they grant SYSTEM or the
# Administrators (WRITE_DAC), so a file of an account that removed both needs the take
# of ownership first. $global:SecCalls holds each write, and $global:Calls one line
# "acl <folder>/<file> <SDDL as written>" for each whole write (the take of ownership,
# then the owner and the rules in one write).
# $global:AclEdit: what Windows makes of the rules written to a disk file (the form of a
# mask, or another rule).
class TnwksSecurity {
    static [string] Enable() {
        if ($global:EnableText) { return $global:EnableText }
        $global:PrivilegesOn = $true
        return 'SeBackupPrivilege True, SeRestorePrivilege True, SeTakeOwnershipPrivilege True'
    }
    static [string] Leaf([string]$Path) { return [IO.Path]::GetFileName([IO.Path]::GetDirectoryName($Path)) + '/' + [IO.Path]::GetFileName($Path) }
    static [string] ReadSddl([string]$Path) {
        # $global:ReadSeq: the reads of a file in turn, one more for each wait (Start-Sleep).
        if ($global:ReadSeq -and $global:ReadSeq.ContainsKey($Path)) {
            $q = @($global:ReadSeq[$Path]); return $q[[Math]::Min([int]$global:ReadAt, $q.Count - 1)]
        }
        # A folder gives its files exactly the rules of $global:Inherit (as OI rules).
        if ([IO.Directory]::Exists($Path)) {
            if ($global:FolderAcls -and $global:FolderAcls.ContainsKey($Path)) { return $global:FolderAcls[$Path] }
            return 'O:BAD:P' + ("$($global:Inherit[$Path])" -replace ';ID;', ';OI;')
        }
        if (-not [IO.File]::Exists($Path)) { throw [IO.FileNotFoundException]::new($Path) }
        if ($global:Acls.ContainsKey($Path)) { return $global:Acls[$Path] }
        # A file that SYSTEM made: the rules that its folder gives, owner the Administrators.
        return 'O:BA' + [TnwksSecurity]::FileGives([TnwksSecurity]::ReadSddl([IO.Path]::GetDirectoryName($Path)), 'BA', [TnwksSecurity]::ReadGroup($Path))
    }
    # The attributes of the entry itself, as GetFileAttributes reads them (a link is not
    # followed), or -1 with no entry. $global:Attrs: the attributes of a file (by
    # folder/name).
    static [int] ReadEntryAttributes([string]$Path) {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        if (-not $item) { return -1 }
        if ($global:Attrs -and $global:Attrs.ContainsKey([TnwksSecurity]::Leaf($Path))) { return $global:Attrs[[TnwksSecurity]::Leaf($Path)] }
        return [int]$item.Attributes
    }
    # $global:Groups: the group of a file (by its path, or by folder/name); SYSTEM by default.
    static [string] ReadGroup([string]$Path) {
        if ($global:Groups) {
            foreach ($k in @($Path, [TnwksSecurity]::Leaf($Path))) { if ($global:Groups.ContainsKey($k)) { return $global:Groups[$k] } }
        }
        return 'SY'
    }
    # What a folder gives a new file in it, by the rule of inheritance (the stand-in of
    # CreatePrivateObjectSecurityEx): each rule of the folder that reaches a file (OI,
    # with or without IO and NP; CI alone does not), with the flag ID only, CREATOR
    # OWNER as the owner of the file, CREATOR GROUP as its group, and each generic
    # right as the rights of a file.
    static [string] FileGives([string]$FolderSddl, [string]$Owner, [string]$Group) {
        $names = @{ GA = [int64]0x10000000; GR = [int64]2147483648; GW = [int64]0x40000000; GX = [int64]0x20000000
            FA = [int64]0x1F01FF; FR = [int64]0x120089; FW = [int64]0x120116; FX = [int64]0x1200A0
            CC = [int64]0x1; DC = [int64]0x2; LC = [int64]0x4; SW = [int64]0x8; RP = [int64]0x10; WP = [int64]0x20
            DT = [int64]0x40; LO = [int64]0x80; CR = [int64]0x100; SD = [int64]0x10000; RC = [int64]0x20000
            WD = [int64]0x40000; WO = [int64]0x80000 }
        $generic = @{ [int64]2147483648 = [int64]0x120089; [int64]0x40000000 = [int64]0x120116
            [int64]0x20000000 = [int64]0x1200A0; [int64]0x10000000 = [int64]0x1F01FF }
        $out = ''
        $dacl = if ($FolderSddl -match 'D:[A-Z]*((\([^)]*\))*)') { $Matches[1] } else { '' }
        foreach ($m in [regex]::Matches($dacl, '\(([^)]*)\)')) {
            $f = $m.Groups[1].Value -split ';'
            $flags = @(for ($i = 0; $i + 1 -lt $f[1].Length; $i += 2) { $f[1].Substring($i, 2) })
            if ($flags -notcontains 'OI') { continue }
            [int64]$mask = 0
            if ($f[2] -match '^0x') { $mask = [Convert]::ToInt64($f[2].Substring(2), 16) }
            else { for ($i = 0; $i + 1 -lt $f[2].Length; $i += 2) { $mask = $mask -bor $names[$f[2].Substring($i, 2)] } }
            foreach ($g in $generic.Keys) { if ($mask -band $g) { $mask = ($mask -band (-bnot $g)) -bor $generic[$g] } }
            $sid = if ($f[5] -in @('CO', 'S-1-3-0')) { $Owner } elseif ($f[5] -in @('CG', 'S-1-3-1')) { $Group } else { $f[5] }
            $out += '({0};ID;0x{1:x};;;{2})' -f $f[0], $mask, $sid
        }
        return 'D:AI' + $out
    }
    static [void] SetOwner([string]$Path, [string]$Owner) {
        $s = [TnwksSecurity]::ReadSddl($Path)
        $global:SecCalls.Add("owner $([TnwksSecurity]::Leaf($Path)) $Owner")
        if ($global:SecLog) { [IO.File]::AppendAllLines($global:SecLog, [string[]]@("owner $([TnwksSecurity]::Leaf($Path)) $Owner")) }
        $global:Acls[$Path] = 'O:' + $Owner + $s.Substring($s.IndexOf('D:'))
    }
    static [void] SetRules([string]$Path, [string]$Sddl) {
        $s = [TnwksSecurity]::ReadSddl($Path)
        $now = $s.Substring(2, $s.IndexOf('D:') - 2)
        if ($now -notin @('SY', 'S-1-5-18', 'BA', 'S-1-5-32-544') -and $s.Substring($s.IndexOf('D:')) -notmatch ';;;(SY|BA|S-1-5-18|S-1-5-32-544)\)') {
            $global:SecCalls.Add("refused rules $([TnwksSecurity]::Leaf($Path))")
            throw [UnauthorizedAccessException]::new('Access is denied.')
        }
        # $global:FailRules: a write of the rules that Windows refuses.
        if ($global:FailRules) { throw [UnauthorizedAccessException]::new('a write that fails') }
        $owner = $Sddl.Substring(2, $Sddl.IndexOf('D:') - 2)
        $dacl = $Sddl.Substring($Sddl.IndexOf('D:'))
        if ($dacl -match ';[A-Z]*ID[A-Z]*;') { throw "an inherited rule in the rules written: $dacl" }
        $global:SecCalls.Add("rules $([TnwksSecurity]::Leaf($Path)) O:$owner$dacl")
        $global:Calls.Add("acl $([TnwksSecurity]::Leaf($Path)) O:$owner$dacl")
        # With no protection, the inherited rules come from the folder of the file,
        # and a CREATOR OWNER rule (CO) of the folder names the owner of this write.
        $written = $dacl
        $folder = [IO.Path]::GetDirectoryName($Path)
        if ($dacl -notmatch '^D:[A-Z]*P') {
            if ($global:FolderAcls -and $global:FolderAcls.ContainsKey($folder)) {
                $g = [TnwksSecurity]::FileGives($global:FolderAcls[$folder], $owner, [TnwksSecurity]::ReadGroup($Path))
                $written += $g.Substring(4)
            } else { $written += ("$($global:Inherit[$folder])" -replace ';;;CO\)', ";;;$owner)") }
        }
        $s = "O:$owner$written"
        if ($global:AclEdit -and $Path -like '*ext4.vhdx') { $s = & $global:AclEdit $s }
        $global:Acls[$Path] = $s
    }
}
$global:SecCalls = New-Object System.Collections.Generic.List[string]
if (-not $global:Inherit) { $global:Inherit = @{} }
function Get-Acl { throw 'the procedure reads the rules of a file through TnwksSecurity' }
function Set-Acl { throw 'the procedure writes the rules of a file through TnwksSecurity' }
# A rename of a disk file that grants SYSTEM and the Administrators nothing is refused,
# unless the run turned the privileges on (TnwksSecurity::Enable) before it.
function Move-Item {
    param([string]$LiteralPath, [string]$Destination, [switch]$Force)
    if ($LiteralPath -like '*ext4.vhdx') {
        $rules = if ($global:Acls.ContainsKey($LiteralPath)) { $global:Acls[$LiteralPath] } else { 'O:BAD:(A;ID;FA;;;SY)' }
        if (-not $global:PrivilegesOn -and $rules.Substring($rules.IndexOf('D:')) -notmatch ';;;(SY|BA|S-1-5-18|S-1-5-32-544)\)') {
            $global:Calls.Add("refused move $(Split-Path -Leaf (Split-Path -Parent $LiteralPath))")
            throw [UnauthorizedAccessException]::new('Access to the path is denied.')
        }
        $global:Calls.Add("move $(Split-Path -Leaf (Split-Path -Parent $LiteralPath)) -> $(Split-Path -Leaf (Split-Path -Parent $Destination))")
        if ($global:Acls.ContainsKey($LiteralPath)) { $global:Acls[$Destination] = $global:Acls[$LiteralPath]; $global:Acls.Remove($LiteralPath) }
    }
    Microsoft.PowerShell.Management\Move-Item -LiteralPath $LiteralPath -Destination $Destination -Force:$Force
}
function Restart-Computer { param([switch]$Force) $global:Calls.Add('restart') }
function Add-Type { }
function Start-Sleep { }
"""

OLD_SDDL = f"O:{A_SID}D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{A_SID})"
STOPPED = {"terminate_exit": 0, "shutdown_exit": 0}


def run_window(tmp_path: Path, request: dict | None = None, mode: str | None = None, state: str = "",
               disk: tuple[str, ...] = ("a",), restart: bool = False, saved_version: str = "none",
               results: dict | None = None, codes: dict | None = None, admins: tuple[str, ...] = (),
               config: dict | None = None, tasks: dict | None = None, acl_file: str | None = None,
               marker_a: bool = False, disabled: str | None = None, acl_edit: str = "", vm_rule: str = "",
               acls: dict | None = None, inherit: dict | None = None, enabled: str = "", queued_polls: int = 0,
               window: Path = WINDOW, folder_acls: dict | None = None, groups: dict | None = None, faults: str = "") -> dict:
    root = tmp_path / "root"
    for sub in ("a", "b", "state", "log/a", "log/b"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "b/tnwks-move.marker").write_text("marker")
    if marker_a:
        (root / "a/tnwks-move.marker").write_text("marker")
    for folder in disk:
        (root / folder / "ext4.vhdx").write_text("0123456789")
    (root / "state/window.state").write_text(state)
    (root / "state/window.log").write_text("")
    (root / "state/wsl-version.txt").write_text(saved_version)
    if acl_file is not None:
        (root / "state/disk.sddl").write_text(acl_file)
    if disabled is not None:
        (root / "state/window.state.disabled").write_text(disabled)
    names = {"stop": "stop", "unregister": "unregister", "import": "import"}
    cfg = {
        "root": str(root), "account_pattern": "^tnwks-moverh-", "task_pattern": "^tnwks-moverh-", "disk_name": "ext4.vhdx", "marker_name": "tnwks-move.marker",
        "state_file": str(root / "state/window.state"), "log_file": str(root / "state/window.log"),
        "acl_file": str(root / "state/disk.sddl"), "version_file": str(root / "state/wsl-version.txt"),
        "service_binary": str(root / "no-such-binary.exe"), "restart": restart, "session_check": False, "task_minutes": 1,
        "disk_rule": {"owner": DEFAULTS["wsl_move_rehearsal_disk_owner"], "new_mask": DEFAULTS["wsl_move_rehearsal_disk_new_mask"]},
        "vm_capability_sid": DEFAULTS["wsl_move_rehearsal_vm_capability_sid"],
        "present": {"account": "tnwks-moverh-a", "folder": str(root / "a"), "result_dir": str(root / "log/a"),
                    "window_tasks": {k: f"tnwks-moverh-a-{v}" for k, v in names.items()},
                    "disable_tasks": ["tnwks-moverh-a-start"], "start_tasks": ["tnwks-moverh-a-start"]},
        "new": {"account": "tnwks-moverh-b", "folder": str(root / "b"), "result_dir": str(root / "log/b"),
                "window_tasks": {k: f"tnwks-moverh-b-{v}" for k, v in names.items()},
                "start_tasks": ["tnwks-moverh-b-start"]},
        "boot": {"group_steps": False, "rehearsal_group_test": False},
    }
    for key, value in (config or {}).items():
        cfg[key] = value
    (tmp_path / "window.json").write_text(json.dumps(cfg))
    state_of = {"tnwks-moverh-a-start": "Ready", "tnwks-moverh-b-start": "Disabled"}
    state_of.update(tasks or {})
    default_results = {
        "tnwks-moverh-a-stop": STOPPED, "tnwks-moverh-b-stop": STOPPED,
        "tnwks-moverh-a-unregister": {"registered": False}, "tnwks-moverh-b-unregister": {"registered": False},
        "tnwks-moverh-a-import": {"disk": str(root / "a/ext4.vhdx")}, "tnwks-moverh-b-import": {"disk": str(root / "b/ext4.vhdx")},
    }
    default_results.update(results or {})
    # None: the task writes no result in this run.
    default_results = {k: v for k, v in default_results.items() if v is not None}
    acls = {str(root / "a/ext4.vhdx"): OLD_SDDL, **{str(root / k): v for k, v in (acls or {}).items()}}

    def ps(value) -> str:
        return "(ConvertFrom-Json '" + json.dumps(value).replace("'", "''") + "')"

    args = f"-Config '{tmp_path / 'window.json'}'"
    if request is not None:
        (tmp_path / "request.json").write_text(json.dumps(request))
        args += f" -Request '{tmp_path / 'request.json'}'"
    if mode:
        args += f" -Mode {mode}"
    script = WINDOW_FAKES + f"""
$global:Sids = @{{ 'tnwks-moverh-a' = '{A_SID}'; 'tnwks-moverh-b' = '{B_SID}' }}
$global:LogA = '{root / "log/a"}'
$global:LogB = '{root / "log/b"}'
foreach ($p in {ps(state_of)}.PSObject.Properties) {{
    $global:Enabled[$p.Name] = ($p.Value -ne 'Disabled'); $global:Running[$p.Name] = ($p.Value -eq 'Running') }}
$global:Codes = @{{}}; foreach ($p in {ps(codes or {})}.PSObject.Properties) {{ $global:Codes[$p.Name] = $p.Value }}
$global:Results = @{{}}; foreach ($p in {ps(default_results)}.PSObject.Properties) {{ $global:Results[$p.Name] = $p.Value }}
$global:Acls = @{{}}; foreach ($p in {ps(acls)}.PSObject.Properties) {{ $global:Acls[$p.Name] = $p.Value }}
foreach ($a in @({", ".join(f"'{a}'" for a in admins) or ""})) {{ $global:Admins.Add($a) }}
$global:AclEdit = {acl_edit or "$null"}
$global:VmRule = {ps(vm_rule)}
$global:EnableText = {ps(enabled)}
$global:QueuedPolls = {queued_polls}
$global:FolderA = {ps(str(root / "a"))}; $global:FolderB = {ps(str(root / "b"))}
$global:Inherit = @{{}}; foreach ($p in {ps({str(root / k): v for k, v in (inherit or {}).items()})}.PSObject.Properties) {{ $global:Inherit[$p.Name] = $p.Value }}
$global:FolderAcls = @{{}}; foreach ($p in {ps({str(root / k): v for k, v in (folder_acls or {}).items()})}.PSObject.Properties) {{ $global:FolderAcls[$p.Name] = $p.Value }}
$global:Groups = @{{}}; foreach ($p in {ps({str(root / k): v for k, v in (groups or {}).items()})}.PSObject.Properties) {{ $global:Groups[$p.Name] = $p.Value }}
if ({'$true' if any(state_of.get(t) == "Running" for t in state_of) else '$false'}) {{
    foreach ($t in @($global:Running.Keys | Where-Object {{ $global:Running[$_] }})) {{
        $global:Procs.Add([pscustomobject]@{{ Name = 'wsl.exe'; OwnerSid = (Get-TaskSid $t); Task = $t }}); Add-TnwksTestVm (Get-TaskSid $t) }}
}}
{faults}
& '{window}' {args}
$code = $LASTEXITCODE
$tasks = @{{}}; foreach ($t in @($global:Enabled.Keys)) {{ $tasks[$t] = (Get-ScheduledTask -TaskName $t).State }}
[Console]::Out.Write((@{{ code = $code; calls = @($global:Calls); admins = @($global:Admins); tasks = $tasks;
    acls = $global:Acls; sec_calls = @($global:SecCalls) }} | ConvertTo-Json -Compress -Depth 5))
"""
    run = pwsh(script)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    out["markers"] = [line.split(" ", 1)[1] for line in (root / "state/window.state").read_text().splitlines() if line.strip()]
    out["log"] = [line.split(" ", 2)[2] for line in (root / "state/window.log").read_text().splitlines() if line.strip()]
    out["disk_a"] = (root / "a/ext4.vhdx").exists()
    out["disk_b"] = (root / "b/ext4.vhdx").exists()
    out["marker_a"] = (root / "a/tnwks-move.marker").exists()
    out["root"] = str(root)
    return out


FORWARD_TEST = {"mode": "forward", "group_steps": True, "stop_after": 0, "rehearsal_group_test": True}


@needs_pwsh
def test_the_window_moves_the_file_in_order_and_logs_each_moment_in_administrators(tmp_path):
    # The start task of A runs, as the keepalive does: step 2 disables it while its
    # instance runs, and Task Scheduler then still reads it as Running.
    out = run_window(tmp_path, FORWARD_TEST, tasks={"tnwks-moverh-a-start": "Running"})
    assert out["code"] == 0, out["log"]
    assert out["tasks"] == {"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Ready"}
    calls = out["calls"]
    disk_b = f"O:{B_SID}D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{B_SID})"
    marker_a = f"O:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;{A_SID})"
    assert calls == [
        f"join {A_SID}",
        "disable tnwks-moverh-a-start", "stop tnwks-moverh-a-start", "start tnwks-moverh-a-stop",
        f"acl a/tnwks-move.marker {marker_a}",
        "move a -> b", f"acl b/ext4.vhdx {disk_b}",
        "start tnwks-moverh-a-unregister",
        "start tnwks-moverh-b-import", "start tnwks-moverh-b-stop",
        # Window step 7: the read of the compute systems through the HCS API, the
        # leave, then the read again.
        "hcs enumerate",
        f"leave {A_SID}",
        "hcs enumerate",
        "enable tnwks-moverh-b-start",
    ]
    assert out["markers"] == ["window-started", "window-done"]
    assert not out["disk_a"] and out["disk_b"] and out["marker_a"]
    assert out["admins"] == []
    groups = [line for line in out["log"] if line.startswith("group: ")]
    assert groups == ["group: tnwks-moverh-a joined Administrators", "group: tnwks-moverh-a left Administrators"]
    # The join is before step 1, the leave is step 7.
    log = out["log"]
    assert log.index(groups[0]) < next(i for i, line in enumerate(log) if line.startswith("step 1: WSL"))
    assert log.index("step 6 passed") < log.index(groups[1]) < log.index("step 7 passed")
    # The restart is behind the switch: off, the run logs it and does not restart.
    assert "restart" not in calls
    assert sum(line.startswith("would restart (the end of the window)") for line in log) == 1
    # The rules of the file before the move are saved, for the way back, and the
    # tasks that step 2 disabled, for way back step 6.
    assert (Path(out["root"]) / "state/disk.sddl").read_text() == OLD_SDDL
    assert (Path(out["root"]) / "state/window.state.disabled").read_text() == "tnwks-moverh-a-start"
    # A task that was disabled before the window is not on that list.
    before = run_window(tmp_path / "2", FORWARD_TEST, tasks={"tnwks-moverh-a-start": "Disabled"})
    assert before["code"] == 0, before["log"]
    assert (Path(before["root"]) / "state/window.state.disabled").read_text() == ""


def test_the_new_account_owns_its_moved_disk_file_with_full_control():
    """WSL grants the VM access to the disk file at each attach, as the account: the
    account needs the right to change the rules, which an owner has."""
    assert DEFAULTS["wsl_move_rehearsal_disk_owner"] == "new-account"
    assert DEFAULTS["wsl_move_rehearsal_disk_new_mask"] == "FA"
    config = yaml.safe_load((ROLE / "vars/main.yml").read_text())["wsl_move_rehearsal_window_config"]
    assert config["disk_rule"] == {"owner": "{{ wsl_move_rehearsal_disk_owner }}",
                                   "new_mask": "{{ wsl_move_rehearsal_disk_new_mask }}"}


@needs_pwsh
def test_the_rule_of_the_moved_file_comes_from_the_config(tmp_path):
    """The owner and the mask of the new account are config values. SYSTEM writes them
    with its privileges: it takes ownership, then writes the owner and the rules in one write."""
    out = run_window(tmp_path, FORWARD_TEST)
    assert out["code"] == 0, out["log"]
    assert f"acl b/ext4.vhdx O:{B_SID}D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{B_SID})" in out["calls"]
    disk = [c for c in out["sec_calls"] if " b/ext4.vhdx" in c]
    assert disk == ["owner b/ext4.vhdx BA", f"rules b/ext4.vhdx O:{B_SID}D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{B_SID})"]
    log = out["log"]
    privileges = "privileges: SeBackupPrivilege True, SeRestorePrivilege True, SeTakeOwnershipPrivilege True"
    assert log.count(privileges) == 1 and log.index(privileges) < log.index("step 4 passed")
    admins = run_window(tmp_path / "2", FORWARD_TEST, config={"disk_rule": {"owner": "administrators", "new_mask": "0x1301bf"}})
    assert admins["code"] == 0, admins["log"]
    assert f"acl b/ext4.vhdx O:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1301bf;;;{B_SID})" in admins["calls"]


@needs_pwsh
def test_the_stand_in_refuses_the_rules_of_a_file_that_grants_system_nothing_without_the_take(tmp_path):
    path = tmp_path / "b" / "ext4.vhdx"
    path.parent.mkdir()
    path.write_text("disk")
    run = pwsh(WINDOW_FAKES + f"""
$global:Calls = New-Object System.Collections.Generic.List[string]
$global:Acls = @{{ '{path}' = 'O:{B_SID}D:P(A;;FA;;;{B_SID})' }}
try {{ [TnwksSecurity]::SetRules('{path}', 'O:BAD:P(A;;FA;;;SY)'); 'written' }} catch {{ 'refused' }}
[TnwksSecurity]::SetOwner('{path}', 'BA'); [TnwksSecurity]::SetRules('{path}', 'O:BAD:P(A;;FA;;;SY)'); 'written'
""")
    assert run.returncode == 0, run.stderr
    assert run.stdout.split() == ["refused", "written"]


@needs_pwsh
def test_the_way_back_restores_a_file_whose_owner_removed_system_and_the_administrators(tmp_path):
    """The stated case: B owns its disk file and removed SYSTEM and the Administrators
    from its rules. Way back step 3 takes ownership, then writes the saved owner and rules
    in one write."""
    locked = f"O:{B_SID}D:P(A;;FA;;;{B_SID})"
    out = run_window(tmp_path, mode="wayback", state="t window-started\nt window-done\n", disk=("b",), acl_file=OLD_SDDL,
                     marker_a=True, disabled="tnwks-moverh-a-start", acls={"b/ext4.vhdx": locked},
                     tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Ready"})
    assert out["code"] == 0, out["log"]
    assert out["acls"][str(Path(out["root"]) / "a/ext4.vhdx")] == OLD_SDDL
    # The move back first, then at the old path: the take of ownership, then the owner and the rules.
    disk = [c for c in out["sec_calls"] if " a/ext4.vhdx" in c]
    assert disk == ["owner a/ext4.vhdx BA", f"rules a/ext4.vhdx {OLD_SDDL}"]
    calls = out["calls"]
    assert calls.index("move b -> a") < calls.index(f"acl a/ext4.vhdx {OLD_SDDL}")
    assert "way back 3 passed" in out["log"]


LOCKED_B = f"O:{B_SID}D:P(A;;FA;;;{B_SID})"
# The way back from window-done, with B's own disk file locked to B (strip-rules).
WAYBACK_LOCKED = {"state": "t window-started\nt window-done\n", "disk": ("b",), "acl_file": OLD_SDDL, "marker_a": True,
                  "disabled": "tnwks-moverh-a-start", "acls": {"b/ext4.vhdx": LOCKED_B},
                  "tasks": {"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Ready"}}
PRIVILEGES = "privileges: SeBackupPrivilege True, SeRestorePrivilege True, SeTakeOwnershipPrivilege True"


@needs_pwsh
def test_the_stand_in_refuses_the_rename_of_a_file_that_grants_system_nothing_until_the_privileges_are_on(tmp_path):
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
    path, to = tmp_path / "b" / "ext4.vhdx", tmp_path / "a" / "ext4.vhdx"
    path.write_text("disk")
    run = pwsh(WINDOW_FAKES + f"""
$global:Calls = New-Object System.Collections.Generic.List[string]
$global:Acls = @{{ '{path}' = '{LOCKED_B}' }}
try {{ Move-Item -LiteralPath '{path}' -Destination '{to}'; 'moved' }} catch {{ 'refused' }}
[void][TnwksSecurity]::Enable()
Move-Item -LiteralPath '{path}' -Destination '{to}'; 'moved'
""")
    assert run.returncode == 0, run.stderr
    assert run.stdout.split() == ["refused", "moved"]


@needs_pwsh
def test_each_mode_turns_the_privileges_on_at_its_start_before_the_state_read(tmp_path):
    """The move of the disk file works by the privileges, not by the right on the folders,
    so every run turns them on first, also a way back that reads no rules before its move."""
    runs = {
        "forward": run_window(tmp_path / "f", FORWARD_TEST),
        "wayback": run_window(tmp_path / "w", mode="wayback", **WAYBACK_LOCKED),
        "boot": run_window(tmp_path / "b", mode="boot", **{**WAYBACK_LOCKED, "state": "t window-started\n",
                                                           "tasks": {"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Running"}}),
        "boot, no action": run_window(tmp_path / "n", mode="boot", state="t wayback-done\n"),
    }
    for mode, out in runs.items():
        log = out["log"]
        assert out["code"] == 0, (mode, log)
        assert log.count(PRIVILEGES) == 1, (mode, log)
        assert log[0].startswith("start, request") and log[1] == PRIVILEGES, (mode, log)


@needs_pwsh
def test_a_way_back_from_window_done_moves_a_file_that_grants_system_nothing_by_the_privileges(tmp_path):
    out = run_window(tmp_path / "1", mode="wayback", **WAYBACK_LOCKED)
    assert out["code"] == 0, out["log"]
    assert "move b -> a" in out["calls"] and not out["disk_b"]
    assert out["acls"][str(Path(out["root"]) / "a/ext4.vhdx")] == OLD_SDDL
    # The same run with no call at its start: its first read of the rules comes after the
    # move, so the move is refused, as on a host.
    script = WINDOW.read_text()
    first = "    Initialize-TnwksSecurity\n    # The rehearsal only: in the group test of a boot run"
    assert script.count(first) == 1
    plain = tmp_path / "Invoke-TnwksMoveWindow.ps1"
    plain.write_text(script.replace(first, "    # The rehearsal only: in the group test of a boot run"))
    out = run_window(tmp_path / "2", mode="wayback", window=plain, **WAYBACK_LOCKED)
    assert out["code"] == 1, out["log"]
    assert "refused move b" in out["calls"] and out["disk_b"] and not out["disk_a"]
    assert "fail: Access to the path is denied." in out["log"] and PRIVILEGES not in out["log"]


@needs_pwsh
def test_the_procedure_waits_for_the_end_of_a_task_that_reads_queued_with_a_new_run_time(tmp_path):
    """A task that never ran can read a new last run time while it is Queued, or Ready with
    the result 0x41301 (the task is running): the run has not ended."""
    out = run_window(tmp_path, FORWARD_TEST, queued_polls=2)
    assert out["code"] == 0, out["log"]
    assert out["log"][-1] == "result: done"
    assert not [line for line in out["log"] if "ended with 267009" in line]


@needs_pwsh
def test_only_forward_step_2_clears_the_saved_rules_and_the_way_back_modes_keep_them(tmp_path):
    """The marker of a started window that the way back mode and the boot run write
    clears nothing: they need the saved set of the window that they undo."""
    for i, mode in enumerate(("wayback", "boot")):
        state = "t window-started\nt window-done\n" if mode == "wayback" else "t window-started\n"
        out = run_window(tmp_path / str(i), mode=mode, state=state, disk=("b",), acl_file=OLD_SDDL, marker_a=True,
                         disabled="tnwks-moverh-a-start", acls={"b/ext4.vhdx": MOVED},
                         tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Ready"})
        assert out["code"] == 0, out["log"]
        assert not any("removed the saved rules" in line for line in out["log"]), mode
        assert (Path(out["root"]) / "state/disk.sddl").read_text() == OLD_SDDL
        assert out["acls"][str(Path(out["root"]) / "a/ext4.vhdx")] == OLD_SDDL, mode


# The rules that the folders of setup give a file: SYSTEM and the Administrators full,
# CREATOR OWNER (the owner of the file), and in A's folder the read of an older drive.
OLD_INHERITS = (f"O:{A_SID}D:AI(A;;FA;;;{A_SID})(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;0x1301bf;;;{A_SID})(A;ID;FR;;;AU)")
INHERIT = {"a": "(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;0x1301bf;;;CO)(A;ID;FR;;;AU)",
           "b": "(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;0x1301bf;;;CO)"}
MOVED = f"O:{B_SID}D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{B_SID})"
BOOT_STATE = "t window-started\nt window-done\nt window-started\n"
GUARD_OK = "step 1: the disk file has the inherited rules that its folder gives, and no rule that the way back refuses"


@needs_pwsh
def test_the_way_back_puts_back_a_saved_set_that_inherits_from_the_old_folder(tmp_path):
    """A file of an older WSL: no protection, and rules that it inherits from its
    folder. Step 4 saves them and sets the protected rule at the new path; the way back
    moves the file back first and restores at the old path, where Windows computes the
    inherited rules from the old folder. The rules written hold no inherited rule."""
    out = run_window(tmp_path, FORWARD_TEST, acls={"a/ext4.vhdx": OLD_INHERITS}, inherit=INHERIT,
                     results={"tnwks-moverh-b-import": {"disk": "elsewhere"}})
    assert out["code"] == 4, out["log"]
    assert (Path(out["root"]) / "state/disk.sddl").read_text() == OLD_INHERITS
    assert out["acls"][str(Path(out["root"]) / "a/ext4.vhdx")] == OLD_INHERITS
    # The take, then the owner and the rules in one write: the inherited CREATOR
    # OWNER rule names A, not the Administrators.
    disk = [c for c in out["sec_calls"] if " a/ext4.vhdx" in c]
    assert disk == ["owner a/ext4.vhdx BA", f"rules a/ext4.vhdx O:{A_SID}D:AI(A;;FA;;;{A_SID})"]
    calls = out["calls"]
    assert calls.index("move b -> a") < calls.index(f"acl a/ext4.vhdx O:{A_SID}D:AI(A;;FA;;;{A_SID})")
    # At the new path, the folder of B would give other inherited rules: not the saved set.
    assert not any(c.startswith(f"rules b/ext4.vhdx O:{A_SID}") for c in out["sec_calls"])


@needs_pwsh
def test_the_inherited_rules_are_not_part_of_as_saved_and_their_difference_is_a_note(tmp_path):
    """Way back step 3 compares the owner, the protection and the explicit rules with the
    saved set. A file at the old path with those, but a CREATOR OWNER rule that names
    the Administrators, or no inherited rule at all, is as saved: no write, and a note of
    the difference. The inherited rules are a fact for the guard of window step 1."""
    wrong = OLD_INHERITS.replace(f"(A;ID;0x1301bf;;;{A_SID})", "(A;ID;0x1301bf;;;BA)")
    bare = f"O:{A_SID}D:AI(A;;FA;;;{A_SID})"
    for i, sddl in enumerate((wrong, bare)):
        out = run_window(tmp_path / str(i), mode="boot", state=BOOT_STATE, disk=("a",), acl_file=OLD_INHERITS, marker_a=True,
                         disabled="tnwks-moverh-a-start", acls={"a/ext4.vhdx": sddl}, inherit=INHERIT,
                         tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Disabled"})
        assert out["code"] == 0, out["log"]
        assert '"old_as_saved":true' in next(line for line in out["log"] if line.startswith("read: "))
        assert out["sec_calls"] == [] and "way back 3: the file has its saved owner and rules" in out["log"]
        assert any(line.startswith("way back 3: note: the inherited rules differ") for line in out["log"])
    # Another owner or another explicit rule is not as saved: step 3 restores.
    for i, sddl in enumerate((OLD_INHERITS.replace(f"O:{A_SID}", "O:BA"),
                              OLD_INHERITS.replace(f"(A;;FA;;;{A_SID})", f"(A;;0x1301bf;;;{A_SID})"))):
        out = run_window(tmp_path / f"r{i}", mode="boot", state=BOOT_STATE, disk=("a",), acl_file=OLD_INHERITS, marker_a=True,
                         disabled="tnwks-moverh-a-start", acls={"a/ext4.vhdx": sddl}, inherit=INHERIT,
                         tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Disabled"})
        assert out["code"] == 0, out["log"]
        assert out["sec_calls"] == ["owner a/ext4.vhdx BA", f"rules a/ext4.vhdx O:{A_SID}D:AI(A;;FA;;;{A_SID})"]
        assert out["acls"][str(Path(out["root"]) / "a/ext4.vhdx")] == OLD_INHERITS


@needs_pwsh
def test_a_privilege_that_is_not_on_stops_before_any_write_of_the_rules(tmp_path):
    out = run_window(tmp_path, FORWARD_TEST, enabled="SeBackupPrivilege True, SeRestorePrivilege False, SeTakeOwnershipPrivilege True")
    # The run stops at its start: no marker, no state read, no step.
    assert out["code"] == 1, out["log"]
    assert out["markers"] == [] and not [line for line in out["log"] if line.startswith(("read:", "step "))]
    assert "privileges: SeBackupPrivilege True, SeRestorePrivilege False, SeTakeOwnershipPrivilege True" in out["log"]
    assert any("a privilege of SYSTEM is not on" in line for line in out["log"])
    assert out["sec_calls"] == []


@needs_pwsh
def test_a_saved_set_with_a_group_part_is_restored_without_it(tmp_path):
    """Windows gives some files a group (G:). The write takes the owner and the rules of
    the SDDL, and no group; the check compares the owner and the rules."""
    with_group = OLD_SDDL.replace("D:P", "G:SYD:P")
    out = run_window(tmp_path, mode="wayback", state="t window-started\nt window-done\n", disk=("b",), acl_file=with_group,
                     marker_a=True, disabled="tnwks-moverh-a-start", acls={"b/ext4.vhdx": MOVED},
                     tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Ready"})
    assert out["code"] == 0, out["log"]
    assert [c for c in out["sec_calls"] if " a/ext4.vhdx" in c] == ["owner a/ext4.vhdx BA", f"rules a/ext4.vhdx {OLD_SDDL}"]
    assert out["acls"][str(Path(out["root"]) / "a/ext4.vhdx")] == OLD_SDDL


@needs_pwsh
def test_a_power_loss_between_the_move_back_and_the_restore_leaves_a_step_that_only_restores(tmp_path):
    """The boot run after a power loss in way back step 3: the file is at the old path
    with the rules of step 4. Step 3 does not move, and restores the saved set."""
    out = run_window(tmp_path, mode="boot", state=BOOT_STATE, disk=("a",), acl_file=OLD_INHERITS, marker_a=True,
                     disabled="tnwks-moverh-a-start", acls={"a/ext4.vhdx": MOVED}, inherit=INHERIT,
                     tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Disabled"})
    assert out["code"] == 0, out["log"]
    assert not any(c.startswith("move ") for c in out["calls"])
    assert out["acls"][str(Path(out["root"]) / "a/ext4.vhdx")] == OLD_INHERITS
    assert any(line.startswith("way back 3: restored the saved owner and rules: O:<present>D:AI") for line in out["log"])
    assert '"old_as_saved":false' in next(line for line in out["log"] if line.startswith("read: "))
    # The file at the old path with the saved set: nothing.
    done = run_window(tmp_path / "2", mode="boot", state=BOOT_STATE, disk=("a",), acl_file=OLD_INHERITS, marker_a=True,
                      disabled="tnwks-moverh-a-start", acls={"a/ext4.vhdx": OLD_INHERITS}, inherit=INHERIT,
                      tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Disabled"})
    assert done["code"] == 0, done["log"]
    assert done["sec_calls"] == [] and "way back 3: the file has its saved owner and rules" in done["log"]
    assert '"old_as_saved":true' in next(line for line in done["log"] if line.startswith("read: "))


@needs_pwsh
def test_a_window_removes_the_saved_rules_of_an_earlier_window_so_its_way_back_restores_none(tmp_path):
    """The window stops at step 3 (no shutdown result of A's stop): step 4 saved nothing,
    and the saved rules of an earlier window are gone, so the way back restores none."""
    earlier = f"O:BAD:P(A;;FA;;;BA)(A;;FA;;;{A_SID})"
    no_group = {"mode": "forward", "group_steps": False, "stop_after": 0, "rehearsal_group_test": False}
    out = run_window(tmp_path, no_group, acl_file=earlier, results={"tnwks-moverh-a-stop": None})
    assert out["code"] == 4, out["log"]
    assert "step 2: removed the saved rules of an earlier window" in out["log"]
    assert not (Path(out["root"]) / "state/disk.sddl").exists()
    assert out["sec_calls"] == [] and out["acls"][str(Path(out["root"]) / "a/ext4.vhdx")] == OLD_SDDL


VM_RULE = "(A;;FA;;;S-1-5-83-1-11-22-33-44)"
CAP_RULE = f"(A;;FA;;;{DEFAULTS['wsl_move_rehearsal_vm_capability_sid']})"


@needs_pwsh
def test_the_checks_of_the_rules_allow_the_rule_of_a_vm_and_a_write_clears_it(tmp_path):
    """WSL adds a rule of a virtual machine SID at an attach: the checks of the rules
    allow it. Step 4 and way back step 3 replace every rule and write no rule of WSL,
    and right after each, the rules must be the ones written: a stale rule of a VM goes."""
    stale = OLD_SDDL + VM_RULE
    forward = run_window(tmp_path / "1", FORWARD_TEST, vm_rule=VM_RULE, acls={"a/ext4.vhdx": stale})
    assert forward["code"] == 0, forward["log"]
    assert forward["acls"][str(Path(forward["root"]) / "b/ext4.vhdx")].endswith(f"(A;;FA;;;{B_SID}){VM_RULE}")
    # The import gives another path: the way back writes the saved rules without the
    # stale rule of WSL that they held when step 4 saved them, and not the rule of B's VM.
    back = run_window(tmp_path / "2", FORWARD_TEST, vm_rule=VM_RULE, acls={"a/ext4.vhdx": stale},
                      results={"tnwks-moverh-b-import": {"disk": "elsewhere"}})
    assert back["code"] == 4, back["log"]
    assert back["calls"].index("move b -> a") < back["calls"].index(f"acl a/ext4.vhdx {OLD_SDDL}")
    # Then A's import back attaches the file in a new VM.
    assert back["acls"][str(Path(back["root"]) / "a/ext4.vhdx")] == OLD_SDDL + VM_RULE
    # Another rule after a write is not a pass.
    other = "{ param($s) $s + '(A;;FA;;;S-1-5-21-9-9-9-1003)' }"
    out = run_window(tmp_path / "3", FORWARD_TEST, acl_edit=other)
    assert out["code"] == 1, out["log"]
    assert "fail: check failed: the moved file has another owner or other rules" in out["log"]
    assert "fail: check failed: the file has not its saved owner and rules" in out["log"]
    # A rule of a VM right after a write: not the rules written.
    vm_after = "{ param($s) $s + '" + VM_RULE + "' }"
    out = run_window(tmp_path / "4", FORWARD_TEST, acl_edit=vm_after)
    assert out["code"] == 1 and "fail: check failed: the moved file has another owner or other rules" in out["log"]
    # A rule of the VM that replaces a rule of the account is not a pass either.
    lost = "{ param($s) $s.Replace('(A;;FA;;;" + B_SID + ")', '" + VM_RULE + "') }"
    out = run_window(tmp_path / "5", FORWARD_TEST, acl_edit=lost)
    assert out["code"] == 4 and "fail: check failed: the moved file has another owner or other rules" in out["log"]


@needs_pwsh
def test_the_checks_of_the_rules_allow_one_rule_of_the_capability_of_the_vm_and_no_other_form(tmp_path):
    """While a VM runs, its disk file may also hold a rule of the capability SID of
    vmWorkerProcess (the config's value): the checks allow one, a write clears it, and
    right after a write it is not allowed. Another capability SID, two of them, a deny
    rule, or a rule of WSL in another form is a rule like any other: not a pass."""
    both = VM_RULE + CAP_RULE
    out = run_window(tmp_path / "1", FORWARD_TEST, vm_rule=both, acls={"a/ext4.vhdx": OLD_SDDL + both})
    assert out["code"] == 0, out["log"]
    assert out["acls"][str(Path(out["root"]) / "b/ext4.vhdx")].endswith(f"(A;;FA;;;{B_SID}){both}")
    # The write of step 4 dropped the stale rules of WSL that A's file held.
    assert not any(CAP_RULE in c or VM_RULE in c for c in out["sec_calls"] if c.startswith("rules "))
    # Right after a write: no rule of the capability.
    cap_after = "{ param($s) $s + '" + CAP_RULE + "' }"
    out = run_window(tmp_path / "2", FORWARD_TEST, acl_edit=cap_after)
    assert out["code"] == 1 and "fail: check failed: the moved file has another owner or other rules" in out["log"]
    # The compare that allows WSL's rules (the restore-only condition of way back step 3,
    # in a boot run): the saved set with WSL's rules is as saved, and nothing is
    # written; with a rule in any other form, step 3 restores the saved set.
    second_vm = "(A;;FA;;;S-1-5-83-1-55-66-77-88)"
    allowed = (VM_RULE, CAP_RULE, VM_RULE + second_vm, VM_RULE + CAP_RULE)
    refused = (CAP_RULE + CAP_RULE, "(A;;FA;;;S-1-15-3-1024-1-2-3-4-5-6-7-8)", f"(D;;FA;;;{A_SID})",
               VM_RULE.replace("(A;;FA;", "(D;;FA;"), VM_RULE.replace(";FA;", ";0x1301bf;"),
               VM_RULE.replace("(A;;", "(A;OI;"), "(A;;FA;;;S-1-5-83-1-11-22-33-44-55)", "(A;;FA;;;S-1-5-83-1-11-22-33)",
               "(A;;FA;;;S-1-5-83-0-11-22-33-44)")
    for i, extra in enumerate(allowed + refused):
        out = run_window(tmp_path / f"x{i}", mode="boot", state=BOOT_STATE, disk=("a",), acl_file=OLD_SDDL, marker_a=True,
                         disabled="tnwks-moverh-a-start", acls={"a/ext4.vhdx": OLD_SDDL + extra},
                         tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Disabled"})
        assert out["code"] == 0, (extra, out["log"])
        as_saved = '"old_as_saved":true' in next(line for line in out["log"] if line.startswith("read: "))
        if extra in allowed:
            assert as_saved and out["sec_calls"] == [], extra
        else:
            assert not as_saved and out["sec_calls"] == ["owner a/ext4.vhdx BA", f"rules a/ext4.vhdx {OLD_SDDL}"], extra
            assert out["acls"][str(Path(out["root"]) / "a/ext4.vhdx")] == OLD_SDDL, extra


@needs_pwsh
def test_the_window_stops_at_step_1_when_the_disk_file_has_not_the_inherited_rules_of_its_folder(tmp_path):
    """Before any change: a disk file with no protection must have the inherited rules
    that its folder gives (way back step 3 writes the explicit rules, and Windows gives
    the inherited ones from the folder). A file that lost them stops the window with
    nothing changed, and the log holds both sets, each account masked."""
    bare = f"O:{A_SID}D:AI(A;;FA;;;{A_SID})"
    out = run_window(tmp_path / "1", FORWARD_TEST, acls={"a/ext4.vhdx": bare}, inherit=INHERIT)
    assert out["code"] == 3, out["log"]
    assert out["markers"] == [] and out["sec_calls"] == [] and out["admins"] == []
    assert not any(c.startswith(("join ", "disable ", "move ")) for c in out["calls"])
    stop = next(line for line in out["log"] if line.startswith("stop at step 1: the inherited rules"))
    assert "The file: ''" in stop and "A;ID;0x1301bf;;;<present>" in stop and A_SID not in stop
    # The rules that the folder gives: a pass, as is a protected file in any folder.
    ok = run_window(tmp_path / "2", FORWARD_TEST, acls={"a/ext4.vhdx": OLD_INHERITS}, inherit=INHERIT)
    assert GUARD_OK in ok["log"]
    assert ok["log"].index(GUARD_OK) < next(
        i for i, line in enumerate(ok["log"]) if line.startswith("group: "))
    protected = run_window(tmp_path / "3", FORWARD_TEST, inherit=INHERIT)
    assert protected["code"] == 0 and GUARD_OK in protected["log"]
    # Another inherited rule than the folder gives: a stop.
    other = OLD_INHERITS.replace("(A;ID;FR;;;AU)", "(A;ID;FA;;;AU)")
    out = run_window(tmp_path / "4", FORWARD_TEST, acls={"a/ext4.vhdx": other}, inherit=INHERIT)
    assert out["code"] == 3 and out["markers"] == [], out["log"]


DENY_INHERIT = {"a": INHERIT["a"] + "(D;ID;WD;;;AU)", "b": INHERIT["b"]}


@needs_pwsh
@pytest.mark.parametrize(("name", "disk", "inherit", "refused"), [
    ("an explicit deny rule", OLD_INHERITS + "(D;;WD;;;AU)", INHERIT, "1 deny rule(s)"),
    ("an inherited deny rule", OLD_INHERITS + "(D;ID;0x40000;;;AU)", DENY_INHERIT, "1 deny rule(s)"),
    ("a deny rule with protection", OLD_SDDL + "(D;;WD;;;AU)", INHERIT, "1 deny rule(s)"),
    ("two rules of the capability", OLD_INHERITS.replace("D:AI", "D:AI" + CAP_RULE + CAP_RULE.replace("FA", "0x1f01ff")),
     INHERIT, "2 rules of the capability of the VM"),
    ("a rule of a VM in another form", OLD_INHERITS.replace("D:AI", "D:AI" + VM_RULE.replace("FA", "FR")),
     INHERIT, "1 rule(s) of a VM SID or a capability SID that are not WSL's"),
    ("a rule of another capability", OLD_INHERITS.replace("D:AI", "D:AI(A;;FA;;;S-1-15-3-1024-1-2-3)"),
     INHERIT, "1 rule(s) of a VM SID or a capability SID that are not WSL's"),
])
def test_the_window_stops_at_step_1_on_a_rule_that_the_way_back_refuses(tmp_path, name, disk, inherit, refused):
    """The guard of window step 1 stops, with nothing changed, on a rule that the check of
    the way back refuses: a deny rule, explicit or inherited (the way back writes the
    explicit ones back, and the folder gives the inherited ones again), more than one
    rule of the capability of the VM, and an explicit rule of a VM SID or a capability
    SID that is not in WSL's form. The log names what it refuses, each account masked."""
    out = run_window(tmp_path, FORWARD_TEST, acls={"a/ext4.vhdx": disk}, inherit=inherit)
    assert out["code"] == 3, (name, out["log"])
    assert out["markers"] == [] and out["sec_calls"] == [] and out["admins"] == [], name
    assert not any(c.startswith(("join ", "disable ", "move ")) for c in out["calls"]), name
    assert out["disk_a"] and not out["disk_b"], name
    stop = next(line for line in out["log"] if line.startswith("stop at step 1: the disk file has"))
    assert refused in stop and "Nothing is changed." in stop and A_SID not in stop, (name, stop)
    assert not any(line.startswith("stop at step 1: the inherited rules") for line in out["log"]), name
    assert GUARD_OK not in out["log"], name


@needs_pwsh
def test_the_guard_of_step_1_passes_the_rules_of_wsl_and_names_both_reasons_of_a_stop(tmp_path):
    """Any number of WSL's rules of a VM and one of its capability pass the guard: way
    back step 3 writes none of them back. A file that lost its inherited rules and has a
    deny rule logs both reasons."""
    wsl = OLD_INHERITS.replace("D:AI", "D:AI" + VM_RULE + VM_RULE.replace("44", "45") + CAP_RULE)
    ok = run_window(tmp_path / "1", FORWARD_TEST, acls={"a/ext4.vhdx": wsl}, inherit=INHERIT)
    assert ok["code"] == 0 and GUARD_OK in ok["log"], ok["log"]
    both = run_window(tmp_path / "2", FORWARD_TEST, acls={"a/ext4.vhdx": f"O:{A_SID}D:AI(A;;FA;;;{A_SID})(D;;WD;;;AU)"},
                      inherit=INHERIT)
    assert both["code"] == 3 and both["markers"] == [], both["log"]
    assert any(line.startswith("stop at step 1: the inherited rules") for line in both["log"])
    assert any(line.startswith("stop at step 1: the disk file has 1 deny rule(s)") for line in both["log"])


@needs_pwsh
def test_the_check_after_a_write_refuses_a_deny_rule_and_compares_the_inherited_rules(tmp_path):
    """The checks right after a write: no deny rule, explicit (a saved set that holds one
    is written back with it) or inherited (the old folder gives it again), so the way
    back fails its check on each run, as the guard of window step 1 says. Without
    -Saved (the check of the moved file in window step 4), the inherited rules are
    compared too."""
    boot = {"mode": "boot", "state": BOOT_STATE, "disk": ("b",), "marker_a": True, "disabled": "tnwks-moverh-a-start",
            "acls": {"b/ext4.vhdx": MOVED},
            "tasks": {"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Disabled"}}
    explicit = run_window(tmp_path / "1", acl_file=OLD_SDDL + "(D;;WD;;;AU)", inherit=INHERIT, **boot)
    assert explicit["code"] != 0 and "fail: check failed: the file has not its saved owner and rules" in explicit["log"]
    inherited = run_window(tmp_path / "2", acl_file=f"O:{A_SID}D:AI(A;;FA;;;{A_SID})", inherit=DENY_INHERIT, **boot)
    assert inherited["code"] != 0 and "fail: check failed: the file has not its saved owner and rules" in inherited["log"]
    # The same saved set with no deny rule in the folder: a pass.
    clean = run_window(tmp_path / "3", acl_file=f"O:{A_SID}D:AI(A;;FA;;;{A_SID})", inherit=INHERIT, **boot)
    assert clean["code"] == 0, clean["log"]
    # An inherited rule on the moved file, which has protection: not the rules written.
    gained = "{ param($s) $s + '(A;ID;FR;;;AU)' }"
    out = run_window(tmp_path / "4", FORWARD_TEST, acl_edit=gained)
    assert out["code"] != 0 and "fail: check failed: the moved file has another owner or other rules" in out["log"]


@needs_pwsh
def test_what_a_folder_gives_names_the_group_of_the_file_for_creator_group(tmp_path):
    """A CREATOR GROUP rule of a folder names the group of the file, as Windows' own
    inheritance function does with the group of the file as the creator's group. A file
    whose group is not its owner passes the guard of step 1 with the rule of its group,
    and stops with the rule of its owner."""
    folder = f"O:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OIIO;FR;;;CG)(A;;0x100002;;;{A_SID})"
    group = PERSON_SID
    own = f"O:{A_SID}D:AI(A;;FA;;;{A_SID})(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;FR;;;{group})"
    ok = run_window(tmp_path / "1", FORWARD_TEST, acls={"a/ext4.vhdx": own}, folder_acls={"a": folder},
                    groups={"a/ext4.vhdx": group})
    assert ok["code"] == 0 and GUARD_OK in ok["log"], ok["log"]
    owner = own.replace(f"FR;;;{group})", f"FR;;;{A_SID})")
    out = run_window(tmp_path / "2", FORWARD_TEST, acls={"a/ext4.vhdx": owner}, folder_acls={"a": folder},
                     groups={"a/ext4.vhdx": group})
    assert out["code"] == 3 and out["markers"] == [], out["log"]
    assert any(line.startswith("stop at step 1: the inherited rules") for line in out["log"])


@needs_pwsh
def test_the_way_back_restores_a_saved_set_with_no_inherited_rule_and_notes_what_the_folder_gives(tmp_path):
    """A saved set with the flag AI and no inherited rule: way back step 3 writes its
    owner, protection and explicit rules at the old path, and Windows gives the rules of
    the folder. The check after it compares the owner, the protection and the explicit
    rules exactly, and the inherited rules with what the folder gives: a pass, with a
    note of the difference from the saved set. The boot run never runs the guard of
    window step 1. The restore-only condition uses the same compare."""
    bare = f"O:{A_SID}D:AI(A;;FA;;;{A_SID})"
    out = run_window(tmp_path / "1", mode="boot", state=BOOT_STATE, disk=("b",), acl_file=bare, marker_a=True,
                     disabled="tnwks-moverh-a-start", acls={"b/ext4.vhdx": MOVED}, inherit=INHERIT,
                     tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Disabled"})
    assert out["code"] == 0, out["log"]
    assert out["acls"][str(Path(out["root"]) / "a/ext4.vhdx")] == OLD_INHERITS
    note = next(line for line in out["log"] if line.startswith("way back 3: note: the inherited rules differ"))
    assert "saved ''" in note and "A;ID;0x1301bf;;;<present>" in note
    assert not any("step 1: " in line and "inherited" in line for line in out["log"])
    # The file at the old path with the saved explicit rules and the inherited rules of
    # its folder: as saved, so step 3 only restores nothing.
    done = run_window(tmp_path / "2", mode="boot", state=BOOT_STATE, disk=("a",), acl_file=bare, marker_a=True,
                      disabled="tnwks-moverh-a-start", acls={"a/ext4.vhdx": OLD_INHERITS}, inherit=INHERIT,
                      tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Disabled"})
    assert done["code"] == 0, done["log"]
    assert '"old_as_saved":true' in next(line for line in done["log"] if line.startswith("read: "))
    assert done["sec_calls"] == [] and "way back 3: the file has its saved owner and rules" in done["log"]


FOLDER_VECTORS = [
    (f"O:BAD:(A;OICIID;FA;;;SY)(A;OICIID;FA;;;BA)(A;OICIID;FA;;;{PERSON_SID})", A_SID,
     f"(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;FA;;;{PERSON_SID})"),
    (("O:BAD:(A;ID;FA;;;BA)(A;OICIIOID;GA;;;BA)(A;ID;FA;;;SY)(A;OICIIOID;GA;;;SY)(A;ID;0x1301bf;;;AU)"
      "(A;OICIIOID;SDGXGWGR;;;AU)(A;ID;0x1200a9;;;BU)(A;OICIIOID;GXGR;;;BU)"), A_SID,
     "(A;ID;FA;;;BA)(A;ID;FA;;;SY)(A;ID;0x1301bf;;;AU)(A;ID;0x1200a9;;;BU)"),
    (f"O:BAD:PAI(A;OIIO;0x1301bf;;;CO)(A;OIIO;FR;;;AU)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;;0x100002;;;{A_SID})", A_SID,
     f"(A;ID;0x1301bf;;;{A_SID})(A;ID;FR;;;AU)(A;ID;FA;;;SY)(A;ID;FA;;;BA)"),
]


@needs_pwsh
def test_what_a_folder_gives_a_file_follows_the_rule_of_inheritance_on_three_real_forms():
    """The stand-in of Windows' inheritance function (TnwksSecurity::FileGives), compared
    by Get-TnwksSddlParts, on three forms of a folder: a folder of a profile, a folder
    under the root of a drive (generic rights, OI with IO), and the folder of A in the
    rehearsal (CREATOR OWNER, a rule for folders only). The host proves the function of
    Windows on the same forms against a real write (hand test 4)."""
    lib = yaml.safe_load((ROLE / "vars/main.yml").read_text())["wsl_move_rehearsal_procedure_lib"]
    lines = []
    for folder, owner, want in FOLDER_VECTORS:
        lines.append(f"$have = Get-TnwksFolderGives '{folder}' '{owner}'; $want = (Get-TnwksSddlParts 'D:AI{want}').inherited\n"
                     "[Console]::Out.WriteLine(\"$($have -eq $want)|$have|$want\")")
    run = pwsh(WINDOW_FAKES + f"$Procedure = '{WINDOW}'; $Capability = ''\n" + lib + "\n" + "\n".join(lines))
    assert run.returncode == 0, run.stderr
    results = run.stdout.strip().splitlines()
    assert len(results) == 3 and all(r.startswith("True|") for r in results), results
    # The role asks Windows: the parent, the owner as the creator with an empty DACL, the
    # generic mapping of files, and no container.
    text = WINDOW.read_text()
    assert "CreatePrivateObjectSecurityEx(parent, creator, out created, IntPtr.Zero, false, 0x1 | 0x8 | 0x10, token, ref map)" in text
    assert "map.Read = 0x120089; map.Write = 0x120116; map.Execute = 0x1200A0; map.All = 0x1F01FF;" in text
    assert '"O:" + owner + "G:" + group + "D:"' in text
    assert "Get-TnwksFolderGives $folder $Parts.owner ([TnwksSecurity]::ReadGroup($Path))" in text


@needs_pwsh
def test_the_checks_of_the_rules_read_a_mask_by_its_value(tmp_path):
    # Windows writes 0x1f01ff as FA. (An alias of an account reads as its SID only on
    # Windows: test_the_rules_compare_an_alias_and_a_sid_as_one_account.)
    named = "{ param($s) $s.Replace('0x1f01ff', 'FA') }"
    out = run_window(tmp_path / "1", FORWARD_TEST, acl_edit=named,
                     config={"disk_rule": {"owner": "administrators", "new_mask": "0x1f01ff"}})
    assert out["code"] == 0, out["log"]
    hexed = "{ param($s) $s.Replace(';FA;', ';0x1F01FF;') }"
    out = run_window(tmp_path / "2", FORWARD_TEST, acl_edit=hexed)
    assert out["code"] == 0, out["log"]
    narrower = "{ param($s) $s.Replace('(A;;FA;;;" + B_SID + ")', '(A;;0x1301bf;;;" + B_SID + ")') }"
    out = run_window(tmp_path / "3", FORWARD_TEST, acl_edit=narrower)
    assert out["code"] == 4 and "fail: check failed: the moved file has another owner or other rules" in out["log"]


@needs_pwsh
def test_the_log_holds_the_rules_before_the_move_and_after_the_import_with_each_account_masked(tmp_path):
    out = run_window(tmp_path, FORWARD_TEST, vm_rule=VM_RULE)
    assert out["code"] == 0, out["log"]
    log = out["log"]
    assert "step 4: saved the rules of the disk file; size 10; the rules: O:<present>D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;<present>)" in log
    assert ("step 6: the rules of the disk file after the import: "
            "O:<new>D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;<new>)(A;;FA;;;S-1-5-83-1-<vm>)") in log
    # The class of a VM's SID stays (S-1-5-83-1), and no SID of an account or a VM.
    assert not any(re.search(r"S-1-5-21-|S-1-5-83-\d+-\d", line) for line in log if line.startswith(("step 4", "step 6")))
    # The import that fails leaves its line too, before the way back.
    failed = run_window(tmp_path / "2", FORWARD_TEST, results={"tnwks-moverh-b-import": None},
                        codes={"tnwks-moverh-b-import": 1})
    assert failed["code"] == 4, failed["log"]
    assert any(line.startswith("step 6: the rules of the disk file after the import: O:<new>D:P(") for line in failed["log"])


@needs_pwsh
def test_the_procedure_refuses_a_rule_of_the_moved_file_that_it_does_not_know(tmp_path):
    for i, rule in enumerate(({"owner": "everyone", "new_mask": "0x1301bf"},
                              {"owner": "administrators", "new_mask": "0x1301bf;;;WD)(A"},
                              {"owner": "administrators"})):
        out = run_window(tmp_path / str(i), FORWARD_TEST, config={"disk_rule": rule})
        assert out["code"] == 3 and out["calls"] == [] and out["markers"] == [] and out["disk_a"], out["log"]
        assert out["log"][0].startswith("stop: the ") and "Nothing is changed." in out["log"][0]


@needs_pwsh
def test_the_restart_line_runs_only_with_the_switch_on(tmp_path):
    out = run_window(tmp_path, {"mode": "forward", "group_steps": False, "stop_after": 0, "rehearsal_group_test": False},
                     restart=True)
    assert out["code"] == 0, out["log"]
    assert out["calls"][-1] == "restart" and out["calls"].count("restart") == 1
    assert "restart: the end of the window" in out["log"]
    assert not any(line.startswith("would restart") for line in out["log"])


# --- The icon of the distro -------------------------------------------------------------

# The test icons, by the SID of the account of the folder: the rules and the attributes
# (FILE_ATTRIBUTE_READONLY 0x1, HIDDEN 0x2, SYSTEM 0x4; 0x20 is Archive).
ICONS = {
    "made by the account": (lambda sid: f"O:{sid}D:AI(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;FA;;;{sid})", 0x20),
    "made by the account, with generic rights": (lambda sid: f"O:{sid}D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;{sid})", 0x20),
    "the right to write, not to delete": (lambda sid: f"O:SYD:P(A;;FA;;;SY)(A;;FW;;;BA)(A;;FW;;;{sid})", 0x20),
    "rules for the children only": (lambda sid: f"O:SYD:P(A;;FA;;;SY)(A;OIIO;FA;;;BA)(A;OIIO;FA;;;{sid})", 0x20),
    "an object rule": (lambda sid: f"O:{sid}D:AI(OD;;CR;00299570-246d-11d0-a768-00aa006e0529;;S-1-1-0)(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;FA;;;{sid})", 0x20),
    "a conditional rule": (lambda sid: f"O:{sid}D:AI(XD;;FA;;;S-1-1-0;(Member_of {{SID(S-1-5-32-545)}}))(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;FA;;;{sid})", 0x20),
    "encrypted": (lambda sid: f"O:{sid}D:AI(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;FA;;;{sid})", 0x4020),
    "owned by the Administrators, no rule for the account": (lambda sid: "O:BAD:AI(A;ID;FA;;;SY)(A;ID;FA;;;BA)", 0x20),
    "a deny entry": (lambda sid: f"O:{sid}D:AI(D;;0x40000;;;S-1-1-0)(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;FA;;;{sid})", 0x20),
    "read-only": (lambda sid: f"O:{sid}D:AI(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;FA;;;{sid})", 0x21),
    "hidden": (lambda sid: f"O:{sid}D:AI(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;FA;;;{sid})", 0x22),
    "system": (lambda sid: f"O:{sid}D:AI(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;FA;;;{sid})", 0x24),
}
ICON_PROBLEM = {"owned by the Administrators, no rule for the account": "no rule that gives the right to write",
                "rules for the children only": "no rule that gives the right to write",
                # A deny of either form counts as a deny rule too.
                "an object rule": "an object or conditional rule, 1 deny rule(s)",
                "a conditional rule": "an object or conditional rule, 1 deny rule(s)",
                "encrypted": "the attribute encrypted",
                "the right to write, not to delete": "no rule that gives the right to write and delete it",
                "a deny entry": "1 deny rule(s)", "read-only": "the attribute read-only",
                "hidden": "the attribute hidden", "system": "the attribute system"}
GROUP_REAL = {"group_steps": True, "stop_after": 0, "rehearsal_group_test": False}


def icon_kw(tmp_path: Path, folder: str, name: str) -> dict:
    """The keywords of run_window for a test icon of the case name in folder a or b."""
    rules, attributes = ICONS[name]
    (tmp_path / "root" / folder).mkdir(parents=True, exist_ok=True)
    (tmp_path / "root" / folder / "shortcut.ico").write_text("icon")
    return {"acls": {f"{folder}/shortcut.ico": rules(A_SID if folder == "a" else B_SID)},
            "faults": f"$global:Attrs = @{{ '{folder}/shortcut.ico' = {attributes} }}"}


def icon_written_at(task: str, folder: str, name: str) -> str:
    """A fault: the run of task writes a test icon in folder a or b."""
    rules, attributes = ICONS[name]
    sid = A_SID if folder == "a" else B_SID
    return f"""$global:Attrs = @{{ '{folder}/shortcut.ico' = {attributes} }}
$global:OnStart = @{{ '{task}' = {{
    $icon = Join-Path $global:Folder{folder.upper()} 'shortcut.ico'
    [IO.File]::WriteAllText($icon, 'icon'); $global:Acls[$icon] = '{rules(sid)}' }} }}"""


@needs_pwsh
@pytest.mark.parametrize("name", list(ICONS))
@pytest.mark.parametrize("folder", ["a", "b"])
def test_window_step_1_reads_the_icon_in_both_folders_before_any_change(tmp_path, folder, name):
    """The old folder: an icon that the import back, as a member of the Administrators,
    can write and delete. The folder of the new account: one that the new account can
    write and delete. Each with no deny entry and none of the attributes read-only, hidden
    and system. Any other icon stops the window with nothing changed, and no step deletes
    it. An icon that passes is replaced by the import, or deleted by the unregister."""
    # The real present account is a member of the Administrators before the window.
    out = run_window(tmp_path, {"mode": "forward", **GROUP_REAL}, admins=(A_SID,), **icon_kw(tmp_path, folder, name))
    icon = Path(out["root"]) / folder / "shortcut.ico"
    passes = name.startswith("made by the account") or (folder == "a" and name.startswith("owned by the Administrators"))
    if passes:
        assert out["code"] == 0 and "step 1: no icon in either folder that the import there can not replace" in out["log"], out["log"]
        # The unregister of the present account deleted the icon of its folder.
        assert ("icon deleted by tnwks-moverh-a-unregister" in out["calls"]) == (folder == "a"), out["calls"]
        return
    assert out["code"] == 3 and out["markers"] == [] and out["sec_calls"] == [] and out["admins"] == [A_SID], out["log"]
    assert not any(c.startswith(("join ", "leave ", "disable ", "start ")) for c in out["calls"]), out["calls"]
    stop = [line for line in out["log"] if line.startswith("stop at step 1: the icon ")]
    assert len(stop) == 1 and ICON_PROBLEM[name] in stop[0] and stop[0].endswith("Nothing is changed."), out["log"]
    assert f"{folder}/shortcut.ico has " in stop[0].replace("\\", "/") and A_SID not in stop[0] and B_SID not in stop[0], stop
    assert icon.exists() and out["disk_a"] and not out["disk_b"]


@needs_pwsh
def test_window_step_1_counts_the_group_test_as_a_member(tmp_path):
    """The rehearsal's group test makes the present account a member right after step 1,
    so step 1 counts the rules of the Administrators for the icon of the old folder,
    also when the account is not a member yet."""
    out = run_window(tmp_path, FORWARD_TEST, **icon_kw(tmp_path, "a", "owned by the Administrators, no rule for the account"))
    log = out["log"]
    assert out["code"] == 0 and log[-1] == "result: done", log
    assert log.index("step 1: no icon in either folder that the import there can not replace") < log.index(
        "group: tnwks-moverh-a joined Administrators"), log


@needs_pwsh
def test_window_step_1_reads_the_old_icon_for_the_account_of_the_import_back(tmp_path):
    """Step 1 reads the membership of the present account in the group, not the request:
    with the group steps on and the account not a member, the import back would count
    the account alone, and an icon that only the Administrators can write stops the
    window. Both folders are named."""
    kw = icon_kw(tmp_path, "a", "owned by the Administrators, no rule for the account")
    both = icon_kw(tmp_path, "b", "hidden")
    kw["acls"].update(both["acls"])
    kw["faults"] = "$global:Attrs = @{ 'b/shortcut.ico' = 0x22 }"
    out = run_window(tmp_path, {"mode": "forward", **GROUP_REAL}, **kw)
    assert out["code"] == 3 and out["markers"] == [], out["log"]
    stop = [line.replace("\\", "/") for line in out["log"] if line.startswith("stop at step 1: the icon ")]
    assert len(stop) == 2 and "a/shortcut.ico has no rule that gives the right to write and delete it" in stop[0], stop
    assert "b/shortcut.ico has the attribute hidden" in stop[1], stop


@needs_pwsh
@pytest.mark.parametrize("kind", ["a directory", "a link to a good file", "a broken link"])
@pytest.mark.parametrize("folder", ["a", "b"])
def test_window_step_1_refuses_an_icon_that_is_not_a_plain_file_and_follows_no_link(tmp_path, folder, kind):
    """The read takes the entry itself: a directory, or a link (to a file with rules that
    pass, or to nothing), stops the window at step 1 with nothing changed."""
    root = tmp_path / "root"
    (root / folder).mkdir(parents=True)
    entry = root / folder / "shortcut.ico"
    acls = {}
    if kind == "a directory":
        entry.mkdir()
    else:
        target = tmp_path / "elsewhere.ico"
        if kind == "a link to a good file":
            target.write_text("icon")
            acls = {str(target): ICONS["made by the account"][0](A_SID if folder == "a" else B_SID)}
        entry.symlink_to(target)
    out = run_window(tmp_path, {"mode": "forward", **GROUP_REAL}, admins=(A_SID,), faults="$global:Acls += " + (
        "@{ " + "; ".join(f"'{k}' = '{v}'" for k, v in acls.items()) + " }"))
    stop = [line for line in out["log"] if line.startswith("stop at step 1: the icon ")]
    want = "the attribute directory" if kind == "a directory" else "the attribute reparse point"
    assert out["code"] == 3 and out["markers"] == [] and len(stop) == 1 and want in stop[0], out["log"]
    assert stop[0].endswith("Nothing is changed.") and "the rules:" not in stop[0], stop
    assert not any(c.startswith(("join ", "leave ", "disable ", "start ")) for c in out["calls"]), out["calls"]


@needs_pwsh
def test_the_stop_line_of_step_1_is_one_bounded_line(tmp_path):
    """The text of an icon read holds the rules of the file, which can be long: the stop
    line of step 1 holds it in one line, cut at its limit, and still ends the same way."""
    kw = icon_kw(tmp_path, "b", "a deny entry")
    many = "".join(f"(D;;0x40000;;;S-1-5-32-{600 + n})" for n in range(80))
    kw["acls"] = {"b/shortcut.ico": f"O:{B_SID}D:AI{many}(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;FA;;;{B_SID})"}
    out = run_window(tmp_path, {"mode": "forward", **GROUP_REAL}, admins=(A_SID,), **kw)
    stop = [line for line in out["log"] if line.startswith("stop at step 1: ")]
    assert out["code"] == 3 and out["markers"] == [] and len(stop) == 1, out["log"]
    assert "80 deny rule(s)" in stop[0] and " [cut]. An import there would fail. Nothing is changed." in stop[0], stop
    assert len(stop[0]) <= len("stop at step 1: ") + 1000 + len(" [cut]. An import there would fail. Nothing is changed."), len(stop[0])


def test_each_read_of_the_icon_takes_the_entry_itself():
    """Test-Path follows a link, and reads a broken one as no entry: no read of the icon
    uses it. Each takes the attributes of the entry (GetFileAttributesW, which does not
    follow a link)."""
    text = WINDOW.read_text()
    body = re.search(r"function Get-TnwksIconProblem\(.*?\n\}", text, re.DOTALL)[0]
    assert "Test-Path" not in body and "[TnwksSecurity]::ReadEntryAttributes($path)" in body
    assert "static extern uint GetFileAttributesW(string name);" in text
    step5 = re.search(r"# The unregister deletes the icon of the distro as its account\.\n(.*)\n", text)[1]
    assert step5.startswith("    Assert-Tnwks ([TnwksSecurity]::ReadEntryAttributes((Join-Path $present.folder $TnwksIconName)) -eq -1)"), step5


@needs_pwsh
def test_an_icon_left_in_the_old_folder_after_step_5_sends_the_window_back(tmp_path):
    out = run_window(tmp_path, {"mode": "forward", **GROUP_REAL}, admins=(A_SID,),
                     **{**icon_kw(tmp_path, "a", "made by the account"), "faults": "$global:KeepIcon = $true"})
    fail = [line for line in out["log"] if line.startswith("fail: check failed: an icon is left in ")]
    assert len(fail) == 1 and fail[0].endswith(" after the unregister"), out["log"]
    assert "the way back starts" in out["log"] and out["log"][-1] == "result: went-back", out["log"]
    assert "start tnwks-moverh-b-import" not in out["calls"] and out["admins"] == [A_SID] and LEAVE not in out["calls"], out["calls"]


@needs_pwsh
@pytest.mark.parametrize("name", list(ICONS))
def test_the_icon_of_the_new_folder_is_read_again_before_window_step_6(tmp_path, name):
    """An icon that comes into the folder of the new account after step 1: the read
    before the import of step 6 sends the window back, while the present account is
    still a member of the Administrators."""
    out = run_window(tmp_path, {"mode": "forward", **GROUP_REAL}, admins=(A_SID,), faults=icon_written_at("tnwks-moverh-a-unregister", "b", name))
    if name.startswith("made by the account"):
        assert out["code"] == 0 and out["log"][-1] == "result: done", out["log"]
        return
    fail = [line for line in out["log"] if line.startswith("fail: check failed: before the import of tnwks-moverh-b: the icon ")]
    assert len(fail) == 1 and ICON_PROBLEM[name] in fail[0], out["log"]
    assert "start tnwks-moverh-b-import" not in out["calls"] and out["log"][-1] == "result: went-back", out["log"]
    assert out["log"].index(fail[0]) < out["log"].index("the way back starts")


@needs_pwsh
@pytest.mark.parametrize("name", list(ICONS))
@pytest.mark.parametrize("member", [True, False])
def test_way_back_step_5_reads_the_old_icon_and_only_stops(tmp_path, name, member):
    """Before the import back: an icon that the present account can write, with the
    Administrators when it is a member. Any other icon stops the way back before the
    import, and nothing deletes it."""
    request = {"mode": "wayback", **GROUP_REAL, "group_steps": member}
    kw = icon_kw(tmp_path, "a", name)
    out = run_window(tmp_path, request, **{**BOOT_STARTED, **kw})
    # The import back only writes the icon: the right to delete it is not needed here.
    passes = (name.startswith("made by the account") or name == "the right to write, not to delete"
              or (member and name.startswith("owned by the Administrators")))
    if passes:
        assert out["code"] == 0 and out["log"][-1] == "result: done", out["log"]
        return
    fail = [line for line in out["log"] if line.startswith("fail: check failed: before the import back of tnwks-moverh-a: the icon ")]
    assert len(fail) == 1 and ICON_PROBLEM[name] in fail[0], out["log"]
    assert fail[0].endswith("The way back stops here, and no step deletes the icon"), fail
    assert "result: failed" in out["log"] and "start tnwks-moverh-a-import" not in out["calls"], out["log"]
    assert (Path(out["root"]) / "a/shortcut.ico").exists() and not any(c.startswith("icon deleted") for c in out["calls"])
    assert "way back 4 passed" in out["log"] and "way back 5 passed" not in out["log"]


@needs_pwsh
def test_the_window_stops_at_step_1_on_another_version_and_changes_nothing(tmp_path):
    out = run_window(tmp_path, {"mode": "forward", "group_steps": False, "stop_after": 0, "rehearsal_group_test": False},
                     saved_version="1.2.3.0")
    assert out["code"] == 3
    assert out["calls"] == [] and out["markers"] == []
    assert out["disk_a"] and not out["disk_b"] and not out["marker_a"]
    assert any(line.startswith("stop at step 1: WSL none is not the saved 1.2.3.0") for line in out["log"])


@needs_pwsh
def test_the_window_stops_after_step_5_with_the_marker_of_a_started_window_last(tmp_path):
    out = run_window(tmp_path, {"mode": "forward", "group_steps": False, "stop_after": 5, "rehearsal_group_test": False})
    assert out["code"] == 0
    assert out["markers"] == ["window-started"]
    assert not out["disk_a"] and out["disk_b"]
    assert not any("-b-" in call for call in out["calls"]) and not any(c.startswith(("join", "leave")) for c in out["calls"])


@needs_pwsh
def test_a_failed_check_after_the_start_goes_back_from_the_state_it_reads(tmp_path):
    """The import of B gives another path: the way back moves the file back, restores
    its saved owner and rules, has B unregister, A import back and its start enabled."""
    out = run_window(tmp_path, FORWARD_TEST, results={"tnwks-moverh-b-import": {"disk": "elsewhere"}})
    assert out["code"] == 4, out["log"]
    assert out["markers"] == ["window-started", "wayback-done"]
    assert out["disk_a"] and not out["disk_b"]
    assert out["acls"][str(Path(out["root"]) / "a/ext4.vhdx")] == OLD_SDDL
    calls = out["calls"]
    back = calls[calls.index("start tnwks-moverh-b-import") + 1:]
    # The start task of B is still disabled (the window failed before step 8).
    assert back[:2] == ["stop tnwks-moverh-b-start", "start tnwks-moverh-b-stop"]
    # The move back first, then the saved rules at the old path.
    assert back[2] == "move b -> a" and back[3] == f"acl a/ext4.vhdx {OLD_SDDL}"
    assert back[4:7] == ["start tnwks-moverh-b-unregister", "start tnwks-moverh-a-import", "enable tnwks-moverh-a-start"]
    # The group test: the import back started A's VM while A was in Administrators.
    # A's stop task ends it, no WSL process of A is left, and only then A leaves;
    # the play starts its start task after the run.
    assert back[7:] == ["start tnwks-moverh-a-stop", f"leave {A_SID}"] and out["admins"] == []
    log = out["log"]
    last_stop = max(i for i, line in enumerate(log) if line.startswith("task tnwks-moverh-a-stop ended with 0;"))
    assert last_stop < log.index("no WSL process of tnwks-moverh-a before it leaves the group") \
        < log.index("group: tnwks-moverh-a left Administrators") \
        < log.index("the start tasks of tnwks-moverh-a are left to the play (the group test)")
    assert "start tnwks-moverh-a-start" not in calls
    assert "the start tasks of tnwks-moverh-a are left to the play (the group test)" in out["log"]
    assert [line for line in out["log"] if line.startswith("group: ")] == [
        "group: tnwks-moverh-a joined Administrators", "group: tnwks-moverh-a left Administrators"]
    assert any(line.startswith("read: ") for line in out["log"])


@needs_pwsh
def test_the_way_back_mode_puts_a_into_administrators_at_its_step_1_and_out_at_the_end(tmp_path):
    out = run_window(tmp_path, {"mode": "wayback", "group_steps": True, "stop_after": 0, "rehearsal_group_test": True},
                     state="t window-started\nt window-done\n", disk=("b",), acl_file=OLD_SDDL, marker_a=True,
                     tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Running"},
                     disabled="tnwks-moverh-a-start")
    assert out["code"] == 0, out["log"]
    calls = out["calls"]
    assert calls[0] == f"join {A_SID}" and calls[-1] == f"leave {A_SID}"
    assert calls[-3:-1] == ["enable tnwks-moverh-a-start", "start tnwks-moverh-a-stop"]
    assert "start tnwks-moverh-a-start" not in calls
    # The list of the tasks that step 2 disabled is spent.
    assert not (Path(out["root"]) / "state/window.state.disabled").exists()
    assert calls.index("start tnwks-moverh-a-import") < calls.index(f"leave {A_SID}")
    assert out["markers"][-2:] == ["window-started", "wayback-done"]
    assert out["disk_a"] and not out["disk_b"]
    log = out["log"]
    assert log.index("group: tnwks-moverh-a joined Administrators") < log.index("way back 1 passed")
    assert log.index("way back 5 passed") < log.index("group: tnwks-moverh-a left Administrators")


@needs_pwsh
def test_a_stop_passes_only_with_a_shutdown_result_of_its_own_run(tmp_path):
    """LastTaskResult 0 can be that of an earlier stop: with no result of this run,
    or one of an earlier run, or a failed shutdown, the step fails."""
    wayback = {"mode": "wayback", "group_steps": True, "stop_after": 0, "rehearsal_group_test": True}
    stale = json.dumps({**STOPPED, "time": 1})
    for i, (result, earlier) in enumerate(((None, None), (None, stale), ({"terminate_exit": 0, "shutdown_exit": 1}, None))):
        # The group test: A's stop before it leaves Administrators.
        root = tmp_path / f"back{i}/root"
        (root / "log/a").mkdir(parents=True)
        if earlier:
            (root / "log/a/stop.json").write_text(earlier)
        out = run_window(tmp_path / f"back{i}", wayback, state="t window-started\nt window-done\n", disk=("b",),
                         acl_file=OLD_SDDL, marker_a=True, tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Running"},
                         disabled="tnwks-moverh-a-start", results={"tnwks-moverh-a-stop": result})
        # The run fails, so the play starts no task of A; A leaves the group only
        # at the end of the failed run, as at the end of any run of the group test.
        assert out["code"] == 1, out["log"]
        assert out["calls"][-2:] == ["start tnwks-moverh-a-stop", f"leave {A_SID}"]
        log = out["log"]
        fail = next(i for i, line in enumerate(log) if line.startswith("fail: check failed: the stop task of tnwks-moverh-a ended with 0, with "))
        assert "no WSL process of tnwks-moverh-a before it leaves the group" not in log
        assert fail < log.index("group: tnwks-moverh-a left Administrators") < log.index("result: failed")
        # The forward window: A's stop at step 3, before the file moves.
        root = tmp_path / f"forward{i}/root"
        (root / "log/a").mkdir(parents=True)
        if earlier:
            (root / "log/a/stop.json").write_text(earlier)
        out = run_window(tmp_path / f"forward{i}", {"mode": "forward", "group_steps": False, "stop_after": 0,
                                                    "rehearsal_group_test": False}, results={"tnwks-moverh-a-stop": result})
        assert "step 3 passed" not in out["log"] and not any(c.startswith("move ") for c in out["calls"]), out["log"]
        assert out["disk_a"] and not out["disk_b"]


# --- The error text of a failed step, and a result that an earlier run left -----------------

# An error text over lines, with the start of a group line and of a result line in it,
# and longer than a fail line may carry.
NOISY = "access denied\r\ngroup: tnwks-moverh-a joined Administrators\nresult: done\n" + "x" * 2500
# Each step of the procedure that reads the result of a task of an account (the stop has
# its own test): the mode, the task, its account, its result file, and its fail text.
ACCOUNT_STEPS = {
    "window step 5": ("forward", "tnwks-moverh-a-unregister", "a", "unregister", "the unregister of tnwks-moverh-a did not pass ("),
    "window step 6": ("forward", "tnwks-moverh-b-import", "b", "import", "the import of tnwks-moverh-b did not pass ("),
    "way back step 4": ("wayback", "tnwks-moverh-b-unregister", "b", "unregister", "the unregister of tnwks-moverh-b did not pass ("),
    "way back step 5": ("wayback", "tnwks-moverh-a-import", "a", "import", "the import back of tnwks-moverh-a did not pass ("),
}
# The stop of window step 3 goes through the same read of a result.
STOP_STEP = {"window step 3": ("forward", "tnwks-moverh-a-stop", "a", "stop", "the stop task of tnwks-moverh-a ")}


def account_step_run(tmp_path: Path, name: str, result, code: int = 0, earlier: bool = False, ahead: bool = False,
                     **kw) -> tuple[dict, str]:
    """A run of the procedure in which the task of one step gives result (None: no result
    of this run), after an earlier run that left a good result of that task (with ahead,
    with a time an hour after now). Returns the run and the fail line of the step (or '')."""
    mode, task, who, step, text = {**ACCOUNT_STEPS, **STOP_STEP}[name]
    root = tmp_path / "root"
    (root / f"log/{who}").mkdir(parents=True, exist_ok=True)
    if earlier or ahead:
        good = {"registered": False} if step == "unregister" else STOPPED if step == "stop" else {"disk": str(root / f"{who}/ext4.vhdx")}
        when = int(time.time()) + 3600 if ahead else 1
        (root / f"log/{who}/{step}.json").write_text(json.dumps({**good, "time": when}))
    request = {"mode": mode, "group_steps": False, "stop_after": 0, "rehearsal_group_test": False}
    out = run_window(tmp_path, request, results={task: result}, codes={task: code},
                     **({**BOOT_STARTED, **kw} if mode == "wayback" else kw))
    fail = [line for line in out["log"] if line.startswith(f"fail: check failed: {text}")]
    assert len(fail) <= 1, fail
    return out, fail[0] if fail else ""


@needs_pwsh
@pytest.mark.parametrize("name", list(ACCOUNT_STEPS))
def test_a_result_that_an_earlier_run_left_does_not_pass_a_step(tmp_path, name):
    """A result file of an earlier run, with good values, and no result of this run: the
    step fails, and its fail line says that there is no result of this run."""
    _, task, _, _, text = ACCOUNT_STEPS[name]
    out, fail = account_step_run(tmp_path, name, None, earlier=True)
    assert out["code"] != 0 and fail == f"fail: check failed: {text}exit code 0, no result of this run)", out["log"]
    assert any(line == f"task {task} ended with 0; result: none" for line in out["log"]), out["log"]


@needs_pwsh
@pytest.mark.parametrize("name", list(ACCOUNT_STEPS))
def test_a_failed_step_writes_the_error_text_of_its_command_in_one_bounded_line(tmp_path, name):
    out, fail = account_step_run(tmp_path, name, {"failed": NOISY, "output": "line one\r\nline two", "registered": True}, code=1)
    assert out["code"] != 0, out["log"]
    # The exit code, then the failed text and the output, each on one line and cut.
    assert fail.startswith(ACCOUNT_STEPS[name][4].join(("fail: check failed: ", "exit code 1, failed: access denied group: "))), fail
    assert "x [cut], output: line one line two)" in fail and len(fail) < 1100, fail
    # No line of the log starts with the text inside the error: each check reads a line by its start.
    raw = (Path(out["root"]) / "state/window.log").read_text().splitlines()
    assert all(re.match(r"^\S+ (forward|wayback) ", line) for line in raw), raw
    texts = [line.split(" ", 2)[2] for line in raw]
    assert not [t for t in texts if t.startswith("group: ")], texts
    assert len([t for t in texts if t.startswith("result: ")]) == 1, texts
    # The line of the task holds its result as JSON on one line, bounded too.
    task = next(t for t in texts if t.startswith(f"task {ACCOUNT_STEPS[name][1]} ended with 1; result: "))
    assert len(task) < 2100 and task.endswith(" [cut]"), task


@needs_pwsh
def test_a_task_that_starts_and_never_runs_fails_its_step_with_no_result_of_this_run(tmp_path):
    for name in ("window step 6", "way back step 5"):
        _, task, _, _, text = ACCOUNT_STEPS[name]
        out, fail = account_step_run(tmp_path / name.replace(" ", "-"), name, None, earlier=True, config={"task_minutes": 0},
                                     faults=f"$global:NoRun = @('{task}')")
        assert out["code"] != 0 and fail == f"fail: check failed: {text}did not run to its end in this run (last result 0))", (name, out["log"])
        assert f"task {task} did not run to its end in this run; last result 0; result: none" in out["log"], out["log"]
    # A task that runs and writes its result in this run, but still reads Queued with the
    # result 0x41301 at the end of the wait: no run ended, so its result is not taken.
    # The first such task is the stop of window step 3.
    out, fail = account_step_run(tmp_path / "queued", "window step 3", STOPPED, config={"task_minutes": 0}, queued_polls=100)
    assert fail == "fail: check failed: the stop task of tnwks-moverh-a did not run to its end in this run (last result 267009)", out["log"]
    assert "task tnwks-moverh-a-stop did not run to its end in this run; last result 267009; result: none" in out["log"], out["log"]


@needs_pwsh
@pytest.mark.parametrize("name", [*ACCOUNT_STEPS, *STOP_STEP])
def test_a_result_with_a_time_after_the_read_passes_no_step(tmp_path, name):
    """The time in a result file is the claim of the account. A result with good values and
    a time after the moment of the read does not pass a step: not when the task never runs
    in this run, and not when it runs and writes no result."""
    _, task, _, _, text = {**ACCOUNT_STEPS, **STOP_STEP}[name]
    never, fail = account_step_run(tmp_path / "never", name, None, ahead=True, config={"task_minutes": 0},
                                   faults=f"$global:NoRun = @('{task}')")
    assert never["code"] != 0 and fail == f"fail: check failed: {text}did not run to its end in this run (last result 0)" + (
        ")" if name in ACCOUNT_STEPS else ""), (name, never["log"])
    assert "result: done" not in never["log"], never["log"]
    ran, fail = account_step_run(tmp_path / "ran", name, None, ahead=True)
    no_result = "exit code 0, no result of this run)" if name in ACCOUNT_STEPS else "ended with 0, with no result of this run"
    assert ran["code"] != 0 and fail == f"fail: check failed: {text}{no_result}", (name, ran["log"])
    assert f"task {task} ended with 0; result: none" in ran["log"] and "result: done" not in ran["log"], ran["log"]


@needs_pwsh
def test_a_start_that_task_scheduler_refuses_logs_its_error_text_on_one_line(tmp_path):
    out, _ = account_step_run(tmp_path, "window step 6", {"disk": "unused"}, faults="$global:RefuseStart = @('tnwks-moverh-b-import')")
    assert out["code"] != 0, out["log"]
    assert "fail: the task tnwks-moverh-b-import can not start access is denied" in out["log"], out["log"]
    raw = (Path(out["root"]) / "state/window.log").read_text().splitlines()
    assert all(re.match(r"^\S+ forward ", line) for line in raw), raw


@needs_pwsh
def test_the_text_of_a_fail_line_is_one_line_and_bounded():
    script = WINDOW.read_text()
    start = script.index("function Format-TnwksText")
    end = script.index("\n}\n", script.index("function Format-TnwksTaskRun")) + 3
    run = pwsh(script[start:end] + """
$a = Format-TnwksText "one`r`ntwo`tthree`0four$([char]0x85)five$([char]0x2028)six$([char]0x2029)seven"
$b = Format-TnwksText ('y' * 400)
$c = Format-TnwksText ('z' * 50) 10
$d = Format-TnwksTaskRun ([pscustomobject]@{ ran = $true; code = 1; result = $null })
$e = Format-TnwksTaskRun ([pscustomobject]@{ ran = $true; code = 2; result = [pscustomobject]@{ failed = "f`nf"; output = '' } })
[Console]::Out.Write((@($a, $b.Length, $b.EndsWith(' [cut]'), $c, $d, $e) | ConvertTo-Json -Compress))
""")
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == ["one two three four five six seven", 306, True, "zzzzzzzzzz [cut]", "exit code 1, no result of this run",
                                      "exit code 2, failed: f f"]


@needs_pwsh
def test_the_boot_task_acts_only_after_a_started_window(tmp_path):
    for i, state in enumerate(("", "t window-started\nt window-done\n", "t window-started\nt wayback-done\n")):
        out = run_window(tmp_path / str(i), mode="boot", state=state, disk=("b",))
        assert out["code"] == 0 and out["calls"] == [], state
        assert out["markers"] == [line.split(" ", 1)[1] for line in state.splitlines()]
        assert any(line.startswith("no action: the last marker is") for line in out["log"])
        assert not out["disk_a"] and out["disk_b"]


@needs_pwsh
def test_the_boot_task_writes_its_marker_first_and_stops_on_an_unknown_state(tmp_path):
    for i, disk in enumerate((("a", "b"), ())):
        out = run_window(tmp_path / str(i), mode="boot", state="t window-started\n", disk=disk)
        assert out["code"] == 3, out["log"]
        assert out["markers"] == ["window-started", "boot-run"]
        assert out["calls"] == []
        assert out["disk_a"] == ("a" in disk) and out["disk_b"] == ("b" in disk)
        where = "both paths" if disk else "neither path"
        assert f"stop: the disk file is at {where}. Nothing is changed." in out["log"]


@needs_pwsh
def test_the_boot_task_goes_back_once_with_no_group_step_and_never_restarts_itself(tmp_path):
    out = run_window(tmp_path / "1", mode="boot", state="t window-started\n", disk=("b",), acl_file=OLD_SDDL, marker_a=True,
                     tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Running"},
                     disabled="tnwks-moverh-a-start")
    assert out["code"] == 0, out["log"]
    calls = out["calls"]
    assert calls.index("enable tnwks-moverh-a-start") < calls.index("start tnwks-moverh-a-start")
    # B's start task was running when the way back disabled it; A's runs again.
    assert out["tasks"] == {"tnwks-moverh-a-start": "Running", "tnwks-moverh-b-start": "Disabled"}
    assert out["markers"] == ["window-started", "boot-run", "wayback-done"]
    assert out["disk_a"] and not out["disk_b"]
    assert not any(c.startswith(("join", "leave")) for c in out["calls"]) and "restart" not in out["calls"]
    # A second boot finds the marker of a done way back and does nothing.
    state = (Path(out["root"]) / "state/window.state").read_text()
    again = run_window(tmp_path / "2", mode="boot", state=state, disk=("a",))
    assert again["code"] == 0 and again["calls"] == [] and again["markers"] == out["markers"]
    # A failed boot run does not restart, even with the switch on.
    failed = run_window(tmp_path / "3", mode="boot", state="t window-started\n", disk=("b",), restart=True,
                        marker_a=True, codes={"tnwks-moverh-b-stop": 1}, disabled="tnwks-moverh-a-start")
    # With no marker in the old folder, nothing moves back.
    lost = run_window(tmp_path / "4", mode="boot", state="t window-started\n", disk=("b",), acl_file=OLD_SDDL,
                      disabled="tnwks-moverh-a-start")
    assert lost["code"] == 1 and lost["disk_b"] and not lost["disk_a"]
    assert "fail: check failed: the old folder or its marker is gone: no move back" in lost["log"]
    assert failed["code"] == 1 and "restart" not in failed["calls"]


# --- The boot run of the group test --------------------------------------------------------

BOOT_GROUP = {"group_steps": True, "rehearsal_group_test": True}
BOOT_STARTED = {"state": "t window-started\n", "disk": ("b",), "acl_file": OLD_SDDL, "marker_a": True,
                "tasks": {"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Running"},
                "disabled": "tnwks-moverh-a-start"}
JOIN, LEAVE = f"join {A_SID}", f"leave {A_SID}"


def boot_group_run(tmp_path: Path, request=BOOT_GROUP, **kw) -> tuple[dict, Path]:
    """A boot run with a boot request (none with request=None), and the path of the request."""
    state = tmp_path / "root/state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / "boot-request.json"
    if request is not None:
        path.write_text(request if isinstance(request, str) else json.dumps(request))
    config = {"boot_request_file": str(path), **kw.pop("config", {})}
    return run_window(tmp_path, mode="boot", config=config, **{**BOOT_STARTED, **kw}), path


@needs_pwsh
def test_the_boot_run_of_the_group_test_joins_a_after_the_restart_and_takes_it_out_at_its_end(tmp_path):
    out, request = boot_group_run(tmp_path)
    assert out["code"] == 0, out["log"]
    calls = out["calls"]
    # Way back step 1 joins A; the import back runs in the group; A's stop task ends
    # its VM, and A leaves, before any start task of A.
    assert calls.index(JOIN) < calls.index("start tnwks-moverh-a-import") < calls.index("start tnwks-moverh-a-stop") < calls.index(LEAVE)
    assert "start tnwks-moverh-a-start" not in calls and out["admins"] == []
    assert out["markers"] == ["window-started", "boot-run", "wayback-done"]
    assert json.loads(out["log"][0].removeprefix("start, request: ")) == {"mode": "boot", "stop_after": 0, **BOOT_GROUP}
    for line in ("group: tnwks-moverh-a joined Administrators", "group: tnwks-moverh-a left Administrators",
                 "no WSL process of tnwks-moverh-a before it leaves the group",
                 "the start tasks of tnwks-moverh-a are left to the play (the group test)", "the boot request is spent: removed"):
        assert line in out["log"], line
    assert not request.exists()
    # A boot run with no request: no group step, as before, and nothing to remove.
    plain, _ = boot_group_run(tmp_path / "plain", request=None)
    assert plain["code"] == 0 and not any(c.startswith(("join", "leave")) for c in plain["calls"])
    assert "start tnwks-moverh-a-start" in plain["calls"] and not any("boot request" in line for line in plain["log"])


# Each way that a boot run of the group test can end short: a failed check before and
# after the import back, a failed stop at the end, an error that no step handles, and the
# stops before way back step 1. Whether the import back ran (A's VM may run, started in
# the group), and whether A joined.
BOOT_GROUP_FAULTS = {
    "B's stop gives no result": ({"results": {"tnwks-moverh-b-stop": None}}, False, True),
    "B's unregister lists a distro": ({"results": {"tnwks-moverh-b-unregister": {"registered": True}}}, False, True),
    "the import back fails": ({"codes": {"tnwks-moverh-a-import": 1}, "results": {"tnwks-moverh-a-import": None}}, True, True),
    "A's stop at the end fails": ({"results": {"tnwks-moverh-a-stop": None}}, True, True),
    "an error at way back step 6": ({"faults": "$global:ThrowOnEnable = $true"}, True, True),
    "another version of WSL": ({"saved_version": "1.2.3"}, False, False),
    "no list of the disabled tasks": ({"disabled": None}, False, False),
}


@needs_pwsh
@pytest.mark.parametrize("name", list(BOOT_GROUP_FAULTS))
def test_no_way_that_a_boot_run_of_the_group_test_ends_leaves_a_in_administrators(tmp_path, name):
    over, imported, joined = BOOT_GROUP_FAULTS[name]
    out, request = boot_group_run(tmp_path, **over)
    assert out["code"] != 0 or name == "another version of WSL", out["log"]
    calls = out["calls"]
    assert out["admins"] == [], (name, calls)
    assert (JOIN in calls) == joined and (LEAVE in calls) == joined, (name, calls)
    assert ("start tnwks-moverh-a-import" in calls) == imported
    if imported:
        # The stop task of A runs after the import back and before the leave.
        last_stop = max(i for i, c in enumerate(calls) if c == "start tnwks-moverh-a-stop")
        assert calls.index("start tnwks-moverh-a-import") < last_stop < calls.index(LEAVE), (name, calls)
        # One stop of A after the import back, also when it fails: the time limit of the task holds one.
        assert calls[calls.index("start tnwks-moverh-a-import"):].count("start tnwks-moverh-a-stop") == 1, (name, calls)
    assert "start tnwks-moverh-a-start" not in calls and "restart" not in calls
    # The request is spent once A is out of the group.
    assert not request.exists() and "the boot request is spent: removed" in out["log"], out["log"]


@needs_pwsh
def test_a_boot_run_after_a_power_loss_in_the_group_takes_a_out_first_and_the_check_play_shows_it(tmp_path):
    # A run that a power loss ended: A is still in Administrators, the request is still
    # there, and the last marker is that of the boot run.
    out, request = boot_group_run(tmp_path, admins=(A_SID,), state="t window-started\nt boot-run\n")
    assert out["code"] == 0, out["log"]
    assert out["calls"] == [LEAVE] and out["admins"] == []
    log = out["log"]
    assert log.index("tnwks-moverh-a is in Administrators at the start of a boot run: an earlier run did not end") < log.index(
        "group: tnwks-moverh-a left Administrators") < log.index("no action: the last marker is 'boot-run'")
    assert not request.exists()
    # The check play counts this leave as a problem: A was in the group across a restart.
    raw = (Path(out["root"]) / "state/window.log").read_text().splitlines()
    assert any("a leave with no join" in p for p in run_group_log(raw))


@needs_pwsh
def test_a_leave_that_windows_refuses_keeps_the_boot_request_for_the_next_boot_run(tmp_path):
    out, request = boot_group_run(tmp_path, faults="$global:FailLeave = $true")
    assert out["code"] == 1 and out["admins"] == [A_SID], out["log"]
    assert "the boot request stays: tnwks-moverh-a is still in Administrators" in out["log"] and request.exists()
    assert any(line.startswith("fail: the group of tnwks-moverh-a:") for line in out["log"])


@needs_pwsh
def test_a_boot_request_is_read_in_the_boot_mode_only(tmp_path):
    """A forward run and a way back run of the play leave a boot request as it is: they
    take their group steps from their own request, or from none."""
    for name, kw in (("forward", {"request": {"mode": "forward", "group_steps": False, "stop_after": 0, "rehearsal_group_test": False}}),
                     ("wayback", {"request": {"mode": "wayback", "group_steps": False, "stop_after": 0, "rehearsal_group_test": False},
                                  **BOOT_STARTED}),
                     ("wayback, no request", {"mode": "wayback", **BOOT_STARTED})):
        path = tmp_path / name / "root/state/boot-request.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(BOOT_GROUP))
        out = run_window(tmp_path / name, config={"boot_request_file": str(path)}, **kw)
        assert out["code"] == 0, (name, out["log"])
        assert not any(c.startswith(("join ", "leave ")) for c in out["calls"]), (name, out["calls"])
        assert not any("boot request" in line for line in out["log"]) and path.exists(), (name, out["log"])
        assert json.loads(out["log"][0].removeprefix("start, request: "))["rehearsal_group_test"] is False


@needs_pwsh
def test_a_boot_request_that_is_not_two_booleans_or_a_group_test_with_restart_stops_with_nothing_changed(tmp_path):
    unequal = "group_steps and rehearsal_group_test are not equal: the rehearsal has no join without its leave"
    for i, (request, config, text) in enumerate((
            ({"group_steps": "true", "rehearsal_group_test": True}, {}, "is not read: group_steps and rehearsal_group_test are not two booleans"),
            ("{not json", {}, "is not read"),
            (BOOT_GROUP, {"restart": True}, "the group test runs only with the switch restart off"),
            # The join of the group steps comes only with the leave of the group test: from
            # the request, and from the boot part of the config.
            ({"group_steps": True, "rehearsal_group_test": False}, {}, "is not read: group_steps and rehearsal_group_test are not equal"),
            ({"group_steps": False, "rehearsal_group_test": True}, {}, "is not read: group_steps and rehearsal_group_test are not equal"),
            (None, {"boot": {"group_steps": True, "rehearsal_group_test": False}}, unequal),
            (None, {"boot": {"group_steps": False, "rehearsal_group_test": True}}, unequal))):
        out, path = boot_group_run(tmp_path / str(i), request=request, config=config)
        assert out["code"] == 3 and out["calls"] == [] and out["markers"] == ["window-started"], out["log"]
        assert any(line.startswith("stop: ") and text in line and line.endswith("Nothing is changed.") for line in out["log"]), out["log"]
        assert path.exists() == (request is not None)
    # The same in the other modes, with a config of the rehearsal (it has a boot request
    # file): a request of the play with the group steps and no group test.
    for mode in ("forward", "wayback"):
        out = run_window(tmp_path / mode, {"mode": mode, "group_steps": True, "stop_after": 0, "rehearsal_group_test": False},
                         config={"boot_request_file": str(tmp_path / mode / "root/state/boot-request.json")},
                         **(BOOT_STARTED if mode == "wayback" else {}))
        assert out["code"] == 3 and out["calls"] == [] and out["log"] == [f"stop: {unequal}. Nothing is changed."], out["log"]


@needs_pwsh
def test_a_config_that_is_not_the_rehearsals_runs_the_group_steps_with_no_group_test(tmp_path):
    """The real procedure: the group steps with no group test, in each mode. A config with
    no boot request file is not that of the rehearsal, and the scope check lets it run."""
    for name, kw in (
            ("forward", {"request": {"mode": "forward", "group_steps": True, "stop_after": 0, "rehearsal_group_test": False}}),
            ("wayback", {"request": {"mode": "wayback", "group_steps": True, "stop_after": 0, "rehearsal_group_test": False},
                         **BOOT_STARTED}),
            ("boot", {"mode": "boot", "config": {"boot": {"group_steps": True, "rehearsal_group_test": False}}, **BOOT_STARTED})):
        out = run_window(tmp_path / name, **kw)
        assert not any(line.startswith("stop: ") for line in out["log"]), (name, out["log"])
        request = json.loads(out["log"][0].removeprefix("start, request: "))
        assert request["group_steps"] is True and request["rehearsal_group_test"] is False, (name, out["log"])
        assert out["code"] == 0 and out["log"][-1] == "result: done", (name, out["log"])
        if name != "forward":
            # Way back step 1 joins the present account, and no group test takes it out.
            assert JOIN in out["calls"] and LEAVE not in out["calls"], (name, out["calls"])
            assert out["calls"].index(JOIN) < out["calls"].index("start tnwks-moverh-a-import"), (name, out["calls"])
            assert out["admins"] == [A_SID] and "way back 1 passed" in out["log"], (name, out["log"])
    # A forward run that fails after the marker of a started window goes back, and
    # its way back runs the group step too.
    out = run_window(tmp_path / "back", {"mode": "forward", "group_steps": True, "stop_after": 0, "rehearsal_group_test": False},
                     results={"tnwks-moverh-b-import": None})
    assert "the way back starts" in out["log"] and out["log"][-1] == "result: went-back", out["log"]
    assert out["calls"].count(JOIN) == 1 and LEAVE not in out["calls"], out["calls"]
    assert out["calls"].index(JOIN) > out["calls"].index("start tnwks-moverh-b-import"), out["calls"]
    assert out["calls"].index(JOIN) < out["calls"].index("start tnwks-moverh-a-import"), out["calls"]
    assert out["admins"] == [A_SID], out["admins"]
    # The scope holds the path of the request too.
    out, _ = boot_group_run(tmp_path / "out", config={"boot_request_file": str(tmp_path / "elsewhere.json")})
    assert out["code"] == 3 and any("is outside" in line for line in out["log"])


# --- Window step 7: no compute system runs, then no process of the WSL package ---------

# The real procedure: the present account is a member of the Administrators before the window.
STEP_7_REAL = {"mode": "forward", "group_steps": True, "stop_after": 0, "rehearsal_group_test": False}
BEFORE = "step 7 before the leave: no compute system runs on the host"
AFTER = "step 7 after the leave: no compute system runs on the host"


def at_stop_of_b(code: str) -> str:
    """A fault: code runs at the run of the stop task of B, the last task before step 7."""
    return f"$global:OnStart = @{{ 'tnwks-moverh-b-stop' = {{ {code} }} }}"


def proc(path: str, name: str = "", sid: str = A_SID) -> str:
    """A process of the account sid with the image path (none when empty)."""
    name = name or path.rsplit("\\", 1)[-1]
    return f"$global:Procs.Add([pscustomobject]@{{ Name = '{name}'; ExecutablePath = '{path}'; OwnerSid = '{sid}' }})"


def system(props: str, reads: int = -1) -> str:
    return f"Add-TnwksTestSystem @{{ {props} }} {reads}"


@needs_pwsh
def test_window_step_7_reads_no_compute_system_and_no_process_of_the_package_before_and_after_the_leave(tmp_path):
    ok = run_window(tmp_path / "ok", STEP_7_REAL, admins=(A_SID,))
    assert ok["code"] == 0 and ok["log"][-1] == "result: done", ok["log"]
    log = ok["log"]
    assert log.index(BEFORE) < log.index("group: tnwks-moverh-a left Administrators") < log.index(AFTER) < log.index("step 7 passed")
    assert ok["calls"].count("hcs enumerate") == 2 and ok["admins"] == []
    # Any compute system fails, whatever its owner or type: the WSL VM of a distro, a WSLC VM
    # (default owner, or a name its caller chose), one with no owner, one of another type.
    for name, props, text in (
            ("a WSL VM", "Id = 'id-1'; SystemType = 'VirtualMachine'; Owner = 'WSL'; State = 'Running'",
             "type VirtualMachine, owner WSL, state Running"),
            ("a WSLC VM", "Id = 'id-2'; SystemType = 'VirtualMachine'; Owner = 'WSLC'; State = 'Running'",
             "type VirtualMachine, owner WSLC, state Running"),
            ("a chosen owner", "Id = 'id-3'; SystemType = 'VirtualMachine'; Owner = 'my-session'; State = 'Running'",
             "type VirtualMachine, owner my-session, state Running"),
            ("no owner", "Id = 'id-4'; SystemType = 'VirtualMachine'; State = 'Running'",
             "type VirtualMachine, owner none, state Running"),
            ("another type", "Id = 'id-5'; SystemType = 'Container'; Owner = 'VMMS'; State = 'Created'",
             "type Container, owner VMMS, state Created"),
            ("a long owner over lines", "Id = 'id-6'; SystemType = 'VirtualMachine'; Owner = ('x' * 90) + \"`r`nresult: done\"; State = 'Running'",
             "type VirtualMachine, owner " + "x" * 64 + " [cut], state Running")):
        out = run_window(tmp_path / name.replace(" ", "-"), STEP_7_REAL, admins=(A_SID,), faults=at_stop_of_b(system(props)))
        fail = [line for line in out["log"] if line.startswith("fail: check failed: 1 compute system(s) run on the host before the leave, after 10 reads: ")]
        assert len(fail) == 1 and fail[0].endswith(text), (name, out["log"])
        assert "id-" not in " ".join(out["log"]) and out["log"].count("result: done") == 0, (name, out["log"])
        assert out["calls"].count("hcs enumerate") == 10, (name, out["calls"])
        assert "step 7 before the leave: 1 compute system(s) listed; read again, 2 seconds apart, at most 10 reads" in out["log"]
        # The window goes back while the present account is still a member.
        assert "the way back starts" in out["log"] and "result: went-back" in out["log"], (name, out["log"])
        assert LEAVE not in out["calls"] and out["admins"] == [A_SID] and "step 7 passed" not in out["log"], (name, out["calls"])
    for name, code, text in (
            ("a read that fails", "$global:HcsError = 'HcsEnumerateComputeSystems: 0x80370100'",
             "fail: HcsEnumerateComputeSystems: 0x80370100"),
            ("a read with no list", "$global:HcsText = ''",
             "fail: the Host Compute Service gave no list of its compute systems")):
        out = run_window(tmp_path / name.replace(" ", "-"), STEP_7_REAL, admins=(A_SID,), faults=at_stop_of_b(code))
        assert [line for line in out["log"] if line.startswith(text)] and "result: went-back" in out["log"], (name, out["log"])
        assert LEAVE not in out["calls"] and out["admins"] == [A_SID], (name, out["calls"])


@needs_pwsh
def test_window_step_7_reads_a_list_again_and_passes_only_on_a_read_with_no_system(tmp_path):
    """A VM that a stop ended can stay in the list for a moment: the read is taken again,
    at most 10 reads, and only a read with no system passes."""
    gone = run_window(tmp_path / "gone", STEP_7_REAL, admins=(A_SID,), faults=at_stop_of_b(system(
        "Id = 'id-1'; SystemType = 'VirtualMachine'; Owner = 'WSL'; State = 'Running'", 2)))
    assert gone["code"] == 0 and gone["log"][-1] == "result: done", gone["log"]
    assert "step 7 before the leave: 1 compute system(s) listed; read again, 2 seconds apart, at most 10 reads" in gone["log"]
    # Three reads before the leave (the last with no system), one after it.
    assert gone["calls"].count("hcs enumerate") == 4, gone["calls"]
    # In the list at the tenth read: a fail.
    late = run_window(tmp_path / "late", STEP_7_REAL, admins=(A_SID,), faults=at_stop_of_b(system(
        "Id = 'id-1'; SystemType = 'VirtualMachine'; Owner = 'WSL'; State = 'Running'", 10)))
    assert any(line.startswith("fail: check failed: 1 compute system(s) run on the host before the leave") for line in late["log"]), late["log"]
    assert LEAVE not in late["calls"]


@needs_pwsh
def test_window_step_7_counts_each_process_of_the_wsl_package_of_the_present_account(tmp_path):
    """A process counts by its image path: in the install folder of the package (from the key
    of its installer), or a WSL command of Windows in System32. With no path, by the name
    of a named executable of the installer."""
    fails = {
        "a named file": proc("C:\\Program Files\\WSL\\wslc.exe"),
        "a file the installer takes by a pattern": proc("C:\\Program Files\\WSL\\tools\\some-tool.exe"),
        "the settings app": proc("C:\\Program Files\\WSL\\wslsettings\\wslsettings.exe"),
        "the command of Windows": proc("C:\\Windows\\System32\\wsl.exe"),
        "an old command of Windows": proc("C:\\Windows\\System32\\wslconfig.exe"),
        "no path, a named file": proc("", "container.exe"),
        "no path, a command of Windows": proc("", "wslconfig.exe"),
        "no path, bash": proc("", "bash.exe"),
        "another install folder": "$global:WslInstall = 'D:\\Apps\\WSL'; " + proc("D:\\Apps\\WSL\\wslg.exe"),
    }
    passes = {
        "a name of the installer in another folder": proc("C:\\Program Files\\Remote\\msrdc.exe"),
        "a bash of another program": proc("C:\\Program Files\\Git\\bin\\bash.exe"),
        "a process of the package of another account": proc("C:\\Program Files\\WSL\\wslg.exe", sid="S-1-5-21-9-9-9-1099"),
        "the old folder after a move of the install": "$global:WslInstall = 'D:\\Apps\\WSL'; " + proc("C:\\Program Files\\WSL\\wslg.exe"),
    }
    for name, code in {**fails, **passes}.items():
        out = run_window(tmp_path / name.replace(" ", "-"), STEP_7_REAL, admins=(A_SID,), faults=at_stop_of_b(code))
        if name in passes:
            assert out["code"] == 0 and out["log"][-1] == "result: done", (name, out["log"])
            continue
        assert "fail: check failed: 1 process(es) of the WSL package of tnwks-moverh-a run before the leave" in out["log"], (name, out["log"])
        # The read of the compute systems comes first, and passes.
        assert out["log"].index(BEFORE) < out["log"].index("fail: check failed: 1 process(es) of the WSL package of tnwks-moverh-a run before the leave")
        assert LEAVE not in out["calls"] and out["admins"] == [A_SID], (name, out["calls"])


@needs_pwsh
def test_a_fail_of_step_7_after_the_leave_goes_back_and_puts_the_account_back(tmp_path):
    for name, code, text in (
            ("a compute system", system("Id = 'id-1'; SystemType = 'VirtualMachine'; Owner = 'WSLC'; State = 'Running'"),
             "fail: check failed: 1 compute system(s) run on the host after the leave, after 10 reads: type VirtualMachine, owner WSLC, state Running"),
            ("a process", proc("C:\\Program Files\\WSL\\wslc.exe"),
             "fail: check failed: 1 process(es) of the WSL package of tnwks-moverh-a run after the leave")):
        out = run_window(tmp_path / name.replace(" ", "-"), STEP_7_REAL, admins=(A_SID,), faults="$global:OnLeave = { " + code + " }")
        log = out["log"]
        assert text in log and BEFORE in log and "step 7 passed" not in log, (name, log)
        assert log.index(BEFORE) < log.index("group: tnwks-moverh-a left Administrators") < log.index(text) < log.index("the way back starts"), (name, log)
        # Way back step 1 puts the account back.
        assert out["calls"].index(LEAVE) < out["calls"].index(JOIN) and out["admins"] == [A_SID], (name, out["calls"])
        assert "enable tnwks-moverh-b-start" not in out["calls"], (name, out["calls"])


def test_the_process_read_names_each_named_executable_of_the_wsl_installer():
    """The named executables of the installer of the package (WSL: msipackage/package.wix.in,
    at the tag 3.0.5), and the WSL commands of Windows in System32."""
    text = WINDOW.read_text()
    names = re.search(r"\$TnwksWslPackageNames = @\(([^)]*)\)", text)[1]
    package = {"wsl.exe", "wslg.exe", "wslc.exe", "container.exe", "wslhost.exe", "wslrelay.exe", "wslservice.exe",
               "wslcsession.exe", "msrdc.exe", "msal.wsl.proxy.exe", "wslsettings.exe"}
    assert set(re.findall(r"'([^']+)'", names)) == package
    assert re.search(r"\$TnwksWslSystemNames = @\('wsl.exe', 'wslconfig.exe', 'bash.exe'\)", text)
    assert "msipackage/package.wix.in, at the tag 3.0.5" in text
    assert "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Lxss\\MSI' -Name InstallLocation" in text


@needs_pwsh
def test_the_config_of_the_rehearsal_turns_the_read_of_the_vms_off_and_keeps_the_process_check(tmp_path):
    """Only a config of the rehearsal (it has a boot request file) may turn the read of the
    VMs off. The process check stays."""
    plain = {"mode": "forward", "group_steps": False, "stop_after": 0, "rehearsal_group_test": False}

    def rehearsal(name: str) -> dict:
        return {"vm_check": False, "boot_request_file": str(tmp_path / name / "root/state/boot-request.json")}

    off = run_window(tmp_path / "off", plain, admins=(A_SID,), config=rehearsal("off"),
                     faults=at_stop_of_b("Add-TnwksTestVm 'S-1-5-21-9-9-9-1099'"))
    assert off["code"] == 0 and "step 7 before the leave: the read of the compute systems is off in this config" in off["log"], off["log"]
    assert "hcs enumerate" not in off["calls"]
    one = run_window(tmp_path / "proc", plain, admins=(A_SID,), config=rehearsal("proc"),
                     faults=at_stop_of_b(proc("C:\\Program Files\\WSL\\wsl.exe")))
    assert "fail: check failed: 1 process(es) of the WSL package of tnwks-moverh-a run before the leave" in one["log"], one["log"]
    assert "the way back starts" in one["log"] and LEAVE not in one["calls"] and one["admins"] == [A_SID]


@needs_pwsh
def test_a_config_that_is_not_the_rehearsals_can_not_turn_the_read_of_the_vms_off(tmp_path):
    for name, value, text in (
            ("off", False, "stop: vm_check is off, and only a config of the rehearsal may turn it off. Nothing is changed."),
            ("text", "false", "stop: vm_check is 'false': true or false. Nothing is changed."),
            ("number", 0, "stop: vm_check is '0': true or false. Nothing is changed.")):
        out = run_window(tmp_path / name, STEP_7_REAL, admins=(A_SID,), config={"vm_check": value})
        assert out["code"] == 3 and out["calls"] == [] and out["markers"] == [] and out["log"] == [text], (name, out["log"])
    # On, by the key or with no key: the read runs.
    for name, config in (("on", {"vm_check": True}), ("none", {})):
        out = run_window(tmp_path / name, STEP_7_REAL, admins=(A_SID,), config=config)
        assert out["code"] == 0 and BEFORE in out["log"] and AFTER in out["log"], (name, out["log"])


def test_the_read_of_the_vms_calls_the_hcs_api_as_microsoft_documents_it():
    """The signatures of the reference of the HCS API (computecore.dll): HcsCreateOperation
    (context, callback) gives a handle; HcsEnumerateComputeSystems (query, operation) and
    HcsWaitForOperationResult (operation, timeoutMs, resultDocument) give an HRESULT; the
    result document is freed with LocalFree, and the operation closed."""
    text = WINDOW.read_text()
    source = text[text.index("$TnwksHcsSource = @'"):text.index("'@", text.index("$TnwksHcsSource = @'"))]
    for line in ('static extern IntPtr HcsCreateOperation(IntPtr context, IntPtr callback);',
                 'static extern int HcsEnumerateComputeSystems(string query, IntPtr operation);',
                 'static extern int HcsWaitForOperationResult(IntPtr operation, uint timeoutMs, out IntPtr resultDocument);',
                 'static extern void HcsCloseOperation(IntPtr operation);',
                 'int hr = HcsEnumerateComputeSystems(null, op);',
                 'finally { if (doc != IntPtr.Zero) LocalFree(doc); }', 'finally { HcsCloseOperation(op); }'):
        assert line in source, line
    assert source.count('[DllImport("computecore.dll", ExactSpelling = true') == 4
    assert 'CharSet = CharSet.Unicode)]\n    static extern int HcsEnumerateComputeSystems' in source
    # Every compute system counts, whatever its owner, and no tool: no process is started for the read.
    body = re.search(r"function Get-TnwksComputeSystem \{.*?\n\}", text, re.DOTALL)[0]
    assert "Owner" not in body and "Start-Process" not in body and ".exe" not in body
    # The log names a system by its type, owner and state, never its id.
    fmt = re.search(r"function Format-TnwksComputeSystem\(.*?\n\}", text, re.DOTALL)[0]
    assert "Id" not in fmt and "Format-TnwksText" in fmt


@needs_pwsh
def test_the_procedure_refuses_an_account_or_a_path_outside_its_scope(tmp_path):
    person = run_window(tmp_path / "1", FORWARD_TEST, config={"present": {
        "account": "adult1", "folder": str(tmp_path / "1/root/a"), "result_dir": str(tmp_path / "1/root/log/a"),
        "window_tasks": {}, "disable_tasks": [], "start_tasks": []}})
    outside = run_window(tmp_path / "2", FORWARD_TEST, config={"state_file": str(tmp_path / "window.state")})
    for out in (person, outside):
        assert out["code"] == 3 and out["calls"] == [] and out["disk_a"] and not out["disk_b"]
    assert person["markers"] == []


@needs_pwsh
def test_the_procedure_refuses_a_task_outside_its_scope_and_the_session_check(tmp_path):
    task = run_window(tmp_path / "1", FORWARD_TEST, config={"task_pattern": "^tnwks-other-"})
    none = run_window(tmp_path / "2", FORWARD_TEST, config={"task_pattern": ""})
    session = run_window(tmp_path / "3", FORWARD_TEST, config={"session_check": True})
    for out in (task, none, session):
        assert out["code"] == 3 and out["calls"] == [] and out["markers"] == [] and out["disk_a"], out["log"]
    assert "is outside the scope (^tnwks-other-)" in task["log"][0]
    assert none["log"][0] == "stop: the config has no task_pattern. Nothing is changed."
    assert session["log"][0] == "stop: the session check is on, and this script does not have it. Nothing is changed."


@needs_pwsh
def test_a_forward_run_after_a_started_window_with_no_end_stops_and_does_not_go_back(tmp_path):
    # The group test runs only with restart off (the procedure refuses it otherwise);
    # a run with no group step shows the stop with restart on.
    plain = {"mode": "forward", "group_steps": False, "stop_after": 0, "rehearsal_group_test": False}
    cases = [(last, request, restart) for last in ("window-started", "boot-run")
             for request, restart in ((FORWARD_TEST, False), (plain, True))]
    for i, (last, request, restart) in enumerate(cases):
        state = "t window-started\n" + ("t boot-run\n" if last == "boot-run" else "")
        out = run_window(tmp_path / str(i), request, state=state, disk=("b",), acl_file=OLD_SDDL, marker_a=True,
                         restart=restart)
        assert out["code"] == 3, out["log"]
        assert out["calls"] == [] and out["admins"] == []
        assert out["markers"] == state.replace("t ", "").split()
        assert f"stop: the last marker is '{last}': a window started and did not end. Nothing is changed." in out["log"]
        assert not out["disk_a"] and out["disk_b"]


@needs_pwsh
def test_the_way_back_moves_nothing_back_with_no_saved_rules(tmp_path):
    out = run_window(tmp_path, mode="wayback", state="t window-started\nt window-done\n", disk=("b",), marker_a=True,
                     disabled="tnwks-moverh-a-start")
    assert out["code"] == 1, out["log"]
    assert out["disk_b"] and not out["disk_a"]
    assert not any(c.startswith("move ") for c in out["calls"])
    assert "fail: check failed: no saved rules of the disk file: no move back" in out["log"]


@needs_pwsh
def test_the_way_back_moves_nothing_with_no_list_of_the_tasks_that_step_2_disabled(tmp_path):
    out = run_window(tmp_path, mode="boot", state="t window-started\n", disk=("b",), acl_file=OLD_SDDL, marker_a=True)
    assert out["code"] == 1, out["log"]
    assert out["calls"] == [] and out["disk_b"] and not out["disk_a"]
    assert "fail: check failed: no list of the tasks that step 2 disabled: nothing moves back" in out["log"]


@needs_pwsh
def test_way_back_step_6_enables_only_the_tasks_that_step_2_disabled(tmp_path):
    out = run_window(tmp_path, mode="wayback", state="t window-started\nt window-done\n", disk=("b",), acl_file=OLD_SDDL,
                     marker_a=True, tasks={"tnwks-moverh-a-start": "Disabled"}, disabled="")
    assert out["log"] and "enable tnwks-moverh-a-start" not in out["calls"]
    assert "task tnwks-moverh-a-start stays as it is: step 2 did not disable it" in out["log"]


def test_a_file_at_the_path_stops_each_unregister_of_the_procedure():
    """WSL deletes the disk file at the path of a registration: the procedure checks
    that no file is there right before each unregister, apart from the checks of the
    move before it."""
    text = WINDOW.read_text()
    forward = re.search(r"# \(5\) The unregister of the present account.*?\n(.*?)\n(.*?)\n(.*?)\n", text, re.DOTALL)
    assert forward[1].strip() == ("Assert-Tnwks (-not (Test-Path -LiteralPath $oldPath)) "
                                  "'a file is at the path of the registration of the present account'")
    assert "Invoke-TnwksAccountTask $present.window_tasks.unregister" in forward[3]
    back = re.search(r"# \(4\) For step 6: the unregister of the new account.*?\n(.*?)\n(.*?)\n(.*?)\n", text, re.DOTALL)
    assert back[1].strip() == ("Assert-Tnwks (-not (Test-Path -LiteralPath $newPath)) "
                               "'a file is at the path of the registration of the new account'")
    assert "Invoke-TnwksAccountTask $new.window_tasks.unregister" in back[3]


def test_the_procedure_flushes_the_saved_rules_and_reads_the_group_with_a_fallback():
    text = WINDOW.read_text()
    assert "Write-TnwksFile $cfg.acl_file $rules" in text and "$stream.Flush($true)" in text
    assert "Write-TnwksFile $DisabledFile" in text
    admins = re.search(r"function Get-TnwksAdminSids \{.*?\n\}", text, re.DOTALL)[0]
    assert "Get-LocalGroupMember -SID $AdministratorsSid -ErrorAction Stop" in admins
    assert "InvokeMember('objectSid'" in admins


# --- The check of the moments in Administrators -------------------------------------------

def group_log_logic() -> str:
    check = yaml.safe_load((ROLE / "tasks/check.yml").read_text())
    task = next(t for t in all_tasks(check) if t.get("name") == "Read the moments in Administrators from the log")
    script = task["ansible.windows.win_powershell"]["script"]
    return script[script.index("# --- BEGIN logic"):script.index("# --- END logic")]


def run_group_log(lines: list[str], part: str = "problems") -> list[str]:
    encoded = base64.b64encode("\n".join(lines).encode()).decode()
    run = pwsh(group_log_logic() + f"""
$lines = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')) -split "`n"
[Console]::Out.Write((ConvertTo-Json -Compress @((Test-TnwksGroupLog $lines).{part})))
""")
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout) or []


def a_run(mode: str, test: bool, events: list[str]) -> list[str]:
    request = json.dumps({"mode": mode, "group_steps": True, "stop_after": 0, "rehearsal_group_test": test}, separators=(",", ":"))
    return ([f"t {mode} start, request: {request}"] + [f"t {mode} group: tnwks-moverh-a {e} Administrators" for e in events]
            + [f"t {mode} result: done"])


@needs_pwsh
def test_the_check_play_fails_on_a_moment_in_administrators_outside_the_stated_steps():
    # The boot run of the group test joins and leaves in its own run too.
    good = (a_run("forward", True, ["joined", "left"]) + a_run("wayback", True, ["joined", "left"]) + a_run("boot", False, [])
            + a_run("boot", True, ["joined", "left"]))
    assert run_group_log(good) == []
    cases = {
        "not the group test": a_run("forward", False, ["joined", "left"]),
        "the boot task": a_run("boot", False, ["joined", "left"]),
        "no leave": a_run("wayback", True, ["joined"]),
        "no leave at boot": a_run("boot", True, ["joined"]),
        "a leave with no join": a_run("forward", True, ["left"]),
        "a join by the removal": ["t removal group: tnwks-moverh-a left Administrators"],
    }
    for name, lines in cases.items():
        assert run_group_log(good + lines), name
    # Each leave of the removal is shown; one of another group is no problem.
    removal = ["t removal group: tnwks-moverh-a left Administrators", "t removal group: tnwks-moverh-b left Remote Desktop Users"]
    assert run_group_log(good + removal, "shown")[-2:] == [
        "t removal group: tnwks-moverh-a left Administrators", "t removal group: tnwks-moverh-b left Remote Desktop Users"]
    assert run_group_log(good + removal[1:]) == []


# --- keep_task_enabled_state in the module ----------------------------------------------

TASK = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Principals><Principal id="Author"><UserId>S-1-5-21-7-7-7-1001</UserId><LogonType>S4U</LogonType></Principal></Principals>
  <Settings>{enabled}<Hidden>false</Hidden></Settings>
  <Triggers />
  <Actions Context="Author"><Exec><Command>C:\\Windows\\System32\\wsl.exe</Command></Exec></Actions>
</Task>"""


def module_functions(body: str) -> dict:
    module = TASK_MODULE.read_text()
    functions = module[module.index("function ConvertTo-Sid"):module.index("$task = Get-ScheduledTask")]
    run = pwsh(functions + body)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@needs_pwsh
def test_the_option_compares_across_the_enabled_state_and_keeps_the_state_of_the_task():
    texts = [TASK.format(enabled=e) for e in ("<Enabled>false</Enabled>", "<Enabled>true</Enabled>", "")]
    encoded = [base64.b64encode(t.encode()).decode() for t in texts]
    out = module_functions(f"""
$t = @(foreach ($e in @('{"','".join(encoded)}')) {{ [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($e)) }})
[Console]::Out.Write((ConvertTo-Json -Compress @{{
    plain = @($t | ForEach-Object {{ Get-CanonicalXml $_ }})
    keep = @($t | ForEach-Object {{ Get-CanonicalXml $_ -NoEnabled }})
    set_true = (Set-TnwksEnabled $t[0] $true)
    set_false = (Set-TnwksEnabled $t[2] $false)
}}))
""")
    # Off (the default): the state of the template counts, as before.
    assert out["plain"][0] != out["plain"][1]
    assert "<Enabled>false</Enabled>" in out["plain"][0]
    # On: the comparison leaves out the state, and nothing else.
    assert out["keep"][0] == out["keep"][1] == out["keep"][2]
    assert "Enabled" not in out["keep"][0] and "<Hidden>false</Hidden>" in out["keep"][0]
    # A registration of a task that exists takes the state that the task has.
    assert "<Enabled>true</Enabled>" in out["set_true"] and "<Enabled>false</Enabled>" not in out["set_true"]
    assert "<Enabled>false</Enabled>" in out["set_false"]


def test_the_module_takes_the_state_of_an_existing_task_only_with_the_option():
    module = TASK_MODULE.read_text()
    flow = module[module.index("$keep = $module.Params.keep_task_enabled_state"):module.index("if ($before -ne $after)")]
    assert "if ($keep -and $task) {" in flow
    assert "$xml = Set-TnwksEnabled $xml ([bool]$task.Settings.Enabled)" in flow
    assert "$after = Get-CanonicalXml $xml -NoEnabled:($keep -and [bool]$task)" in flow
    assert module.count("-Xml $xml") == 2 and "-Xml $module.Params.xml" not in module


# --- Each fact of a phase is set before a task reads it -----------------------------------

# A name of the role in a template, or in a field that Ansible reads as an expression.
FLOW_NAME = re.compile(r"(?<![\w.'\"])wsl_move_rehearsal_[a-z0-9_]+")
FLOW_EXPRESSIONS = {"when", "that", "failed_when", "changed_when", "until"}
FLOW_OWN_RESULT = {"failed_when", "changed_when", "until"}
FLOW_TEMPLATE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
# Modules that give their output in check mode too (a read).
FLOW_CHECK_MODE_READS = ("ansible.windows.win_stat",)
# Reads that the walk can not prove, each with the reason that it holds. An entry also
# hides the loss of the task that sets the name: these two would not show here if
# setup lost its include of live.yml. That fails closed at run time (the first item of
# the assert finds no baseline), not in this test.
FLOW_KNOWN = {
    # The first item of the assert fails when live.yml did not read (no baseline): an
    # assert reads its items in order, so the later items read only after it passed.
    ("setup.yml", "The baseline of the live parts is kept, and it read the registrations", "wsl_move_rehearsal_baseline"),
    ("setup.yml", "The baseline of the live parts is kept, and it read the registrations", "wsl_move_rehearsal_live"),
}


def flow_strings(value, key=None):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from flow_strings(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from flow_strings(v, key)
    elif isinstance(value, str):
        yield key, value


def flow_reads(task: dict) -> list[tuple[str, str, str]]:
    """(field, name, text) for each name of the role that a task reads with no default
    and no "is defined" right after it."""
    out = []
    body = {k: v for k, v in task.items() if k not in ("block", "rescue", "always", "register", "name")}
    for key, text in flow_strings(body):
        parts = [text] if key in FLOW_EXPRESSIONS and "{{" not in text else FLOW_TEMPLATE.findall(text)
        for part in parts:
            for m in FLOW_NAME.finditer(part):
                rest = part[m.end():]
                if re.match(r"(\.\w+|\[[^\]]*\])*\s*\|\s*default\b", rest) or re.match(r"(\.\w+|\[[^\]]*\])*\s+is\s+(not\s+)?defined", rest):
                    continue
                out.append((key, m.group(0), part.strip()[:160]))
    return out


def flow_conditions(task: dict) -> list[str]:
    w = task.get("when")
    if w is None:
        return []
    return [c for c in (w if isinstance(w, list) else [w]) if isinstance(c, str)]


def flow_evaluate(cond: str, env: dict) -> bool | None:
    """True, False, or None (not known), for the forms of condition that the role uses
    on a value that the walk knows: check mode, and a literal var of an include."""
    c = cond.strip()
    if c in ("not ansible_check_mode", "ansible_check_mode"):
        return (not env["ansible_check_mode"]) if c.startswith("not") else env["ansible_check_mode"]
    m = re.fullmatch(r"(\w+)\s*(==|!=)\s*'([^']*)'", c)
    if m and m[1] in env:
        return (env[m[1]] == m[3]) == (m[2] == "==")
    m = re.fullmatch(r"(\w+)\s+(not\s+in|in)\s+\[(.*)\]", c)
    if m and m[1] in env:
        return (env[m[1]] in re.findall(r"'([^']*)'", m[3])) == (m[2] == "in")
    return None


def flow_given(conds: list[str]) -> set[str]:
    """NAME | default(...) | length > 0: inside, NAME is given."""
    out = set()
    for c in conds:
        m = re.fullmatch(r"(wsl_move_rehearsal_\w+)\s*\|\s*default\([^)]*\)\s*\|\s*length\s*>\s*0", c.strip())
        if m:
            out.add(m[1])
    return out


def flow_run(tasks: list, file: str, have: set, local: set, env: dict, trail: list, out: list, load) -> set:
    """Walks the tasks of a file in order, into each include. A name read must be a
    default or a var of the role, a var of the task or of an include on the way, or a
    fact or result that an earlier task set for sure. A set counts for the rest of its
    own block; after the block only when each of its conditions is known to hold (a
    condition on a result of the host is not). Returns what the tasks set for sure."""
    have = set(have)
    sets = set()
    for task in tasks:
        conds = flow_conditions(task)
        values = [flow_evaluate(c, env) for c in conds]
        if False in values:
            continue
        task_vars = task.get("vars") or {}
        loop = {task.get("loop_control", {}).get("loop_var", "item")} if "loop" in task else set()
        here = local | set(task_vars) | loop | flow_given(conds)
        for key, name, text in flow_reads(task):
            if name in here or name in have or (key in FLOW_OWN_RESULT and name == task.get("register")):
                continue
            if (file, task.get("name", ""), name) in FLOW_KNOWN:
                continue
            out.append(f"{' > '.join([*trail, file])}: '{task.get('name', '?')}' reads {name}: {text}")
        literal = {k: v for k, v in task_vars.items() if isinstance(v, (str, bool))}
        inner = set()
        if "block" in task:
            inner = flow_run(task["block"], file, have, here, {**env, **literal}, trail, out, load)
            for part in ("rescue", "always"):
                flow_run(task.get(part) or [], file, have, here, {**env, **literal}, trail, out, load)
        include = task.get("ansible.builtin.include_tasks")
        if include and "{{" not in include:
            inner = flow_run(load(include), include, have, here, {**env, **literal}, [*trail, file], out, load)
        own = set()
        gives_output = not env["ansible_check_mode"] or task.get("check_mode") is False or any(m in task for m in FLOW_CHECK_MODE_READS)
        if "register" in task and gives_output:
            own.add(task["register"])
        facts = task.get("ansible.builtin.set_fact")
        if isinstance(facts, dict):
            own |= {k for k in facts if k != "cacheable"}
        if all(v is True for v in values):
            have |= inner | own
            sets |= inner | own
    return sets


def flow_load(file: str) -> list:
    return yaml.safe_load((ROLE / "tasks" / file).read_text()) or []


def flow_problems(entry: str, check_mode: bool, load=flow_load) -> list[str]:
    known = set(DEFAULTS) | set(yaml.safe_load((ROLE / "vars/main.yml").read_text()) or {})
    out: list[str] = []
    env = {"ansible_check_mode": check_mode, "wsl_move_rehearsal_phase": entry.removesuffix(".yml")}
    flow_run(load(entry), entry, known, set(), env, [], out, load)
    return out


# The phases that the rehearsal runs after its window first, then the others; check.yml is
# the check play (playbooks/windows-move-rehearsal-check.yml), in check mode, and the
# exit check after the removal. Each phase runs in a play of its own, and no fact of a
# set_fact or a register outlives its play (the fact cache keeps gathered facts only).
FLOW_ENTRIES = ["after-boot.yml", "remove-before-restart.yml", "restart.yml", "remove.yml",
                "setup.yml", "window.yml", "interrupt.yml", "wayback.yml"]


@pytest.mark.parametrize(("entry", "check_mode"), [(e, m) for e in FLOW_ENTRIES for m in (False, True)] + [("check.yml", True)],
                         ids=lambda v: {False: "run", True: "check-run"}.get(v, v) if isinstance(v, bool) else v)
def test_each_phase_sets_each_fact_before_a_task_reads_it(entry, check_mode):
    """For each phase, and the check play, in a real run and in a check run: each fact or
    result of the role that a task reads (wsl_move_rehearsal_sids, _runs, the baseline,
    the results of live.yml, groups.yml, read_accounts.yml, host_files.yml, and each fact
    that read_disk.yml and older_disk.yml need) is set by an earlier task of that phase, on
    every way to the read.

    What this test can not see: a name built at run time (a lookup of hostvars, a key in a
    var); a read that a default or "is defined" guards (it counts as safe, though the
    default may hide a fault: icon.yml reads wsl_move_rehearsal_sids.a with a default of
    '', and fails closed at run time, as its script refuses an empty SID); a condition on a result of the host (a set under it does not
    count after its block: strict, so a fault there shows, and a read that holds for a
    reason the walk can not prove is listed in FLOW_KNOWN with that reason); a task that
    fails or a rescue that ends a block early; and the vars of the inventory and of -e,
    other than the defaults of the role."""
    phases = set(DEFAULTS["wsl_move_rehearsal_phases"]) - {"none"}
    assert {e.removesuffix(".yml") for e in FLOW_ENTRIES} == phases
    problems = flow_problems(entry, check_mode)
    assert problems == [], "\n".join(problems)


def test_the_walk_of_the_facts_finds_a_read_before_its_set():
    """The walk fails on the fault that it is for: after-boot without its read of the
    accounts reads wsl_move_rehearsal_sids (in read_disk.yml, and in the read of A's batch
    right); and a register read before
    its task, or under a condition that the walk can not prove, after its block."""
    def without_accounts(file):
        tasks = flow_load(file)
        if file == "after-boot.yml":
            tasks = [t for t in tasks if t.get("ansible.builtin.include_tasks") != "read_accounts.yml"]
        return tasks
    problems = flow_problems("after-boot.yml", False, without_accounts)
    assert all("reads wsl_move_rehearsal_sids" in p for p in problems), problems
    assert any("read_disk.yml" in p for p in problems), problems
    synthetic = {
        "early.yml": [{"name": "uses", "ansible.builtin.debug": {"msg": "{{ wsl_move_rehearsal_x.output }}"}},
                      {"name": "sets", "ansible.builtin.command": "true", "register": "wsl_move_rehearsal_x"}],
        "maybe.yml": [{"name": "maybe", "when": "wsl_move_rehearsal_y.stat.exists", "block": [
                          {"name": "sets", "ansible.builtin.command": "true", "register": "wsl_move_rehearsal_x"},
                          {"name": "inside", "ansible.builtin.debug": {"msg": "{{ wsl_move_rehearsal_x.output }}"}}]},
                      {"name": "after", "ansible.builtin.debug": {"msg": "{{ wsl_move_rehearsal_x.output }}"}}],
    }
    early = flow_problems("early.yml", False, synthetic.__getitem__)
    assert len(early) == 1 and "'uses' reads wsl_move_rehearsal_x" in early[0]
    maybe = [p for p in flow_problems("maybe.yml", False, synthetic.__getitem__) if "wsl_move_rehearsal_x" in p]
    assert len(maybe) == 1 and "'after' reads" in maybe[0]
