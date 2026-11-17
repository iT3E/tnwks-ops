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
import json
import re
from pathlib import Path

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
    assert on == {"WSL-02"}
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
    others = [path for path in ROLES.glob("*/tasks/*.yml") if path.parent.parent.name != ROLE.name]
    assert others and not any("keep_task_enabled_state" in path.read_text() for path in others)
    for path in ANSIBLE.glob("playbooks/*.yml"):
        assert "keep_task_enabled_state" not in path.read_text(), path.name
    setup = next(t for t in tasks_of("setup.yml") if t.get("name") == "Tasks of the accounts")
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
    order = ["The state file holds no marker", "Folder of the rehearsal", "Folders of the procedure", "The read of the live parts", "The live parts at the start",
             "The baseline of the live parts is kept, and it read the registrations", "The accounts of the rehearsal"]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    gate = next(t for t in tasks_of("setup.yml") if t.get("name") == order[5])
    assert "(wsl_move_rehearsal_baseline.output | last).registrations is not match('not read')" in gate["ansible.builtin.assert"]["that"]
    marker = next(t for t in tasks_of("setup.yml") if t.get("name") == "The state file holds no marker")
    assert marker["ansible.builtin.assert"]["that"] == "(wsl_move_rehearsal_setup_markers.output | last | int) == 0"
    # Each read of the rules converts an alias to its SID, as the procedure does.
    assert "$f[-1] = ConvertTo-Sid $f[-1]" in (ROLE / "tasks/read_window.yml").read_text()
    # No cmdlet of the role takes a plain password.
    for path in ROLE.rglob("*"):
        if path.is_file():
            assert "-AsPlainText" not in path.read_text(), path.name


def test_each_run_of_the_procedure_names_the_last_marker_that_its_phase_expects():
    expected = {"window.yml": "", "wayback.yml": "window-done", "interrupt.yml": "wayback-done"}
    for phase, marker in expected.items():
        runs = [t for t in tasks_of(phase) if t.get("ansible.builtin.include_tasks") == "run_procedure.yml"]
        assert [t["vars"]["wsl_move_rehearsal_last_marker"] for t in runs] == [marker], phase
    run = (ROLE / "tasks/run_procedure.yml").read_text()
    assert "if ($last -ne $LastMarker) { throw" in run
    restart = (ROLE / "tasks/restart.yml").read_text()
    assert "in ['window-started', 'wayback-done']" in restart


def test_the_play_waits_past_the_limit_of_the_window_task_and_the_worst_case_is_under_it():
    limit = re.search(r"<ExecutionTimeLimit>PT(\d+)H</ExecutionTimeLimit>", (ROLE / "templates/system-task.xml.j2").read_text())
    limit_minutes = int(limit[1]) * 60
    wait = int(re.search(r"\$deadline = \(Get-Date\)\.AddMinutes\((\d+)\)", (ROLE / "tasks/run_procedure.yml").read_text())[1])
    assert wait > limit_minutes
    minutes = yaml.safe_load((ROLE / "vars/main.yml").read_text())["wsl_move_rehearsal_window_config"]["task_minutes"]
    # Four tasks of an account in the window and four waits for no WSL process
    # (2 minutes each); in the way back three tasks and the start tasks (3 minutes),
    # or in the group test four tasks (the stop of A before it leaves the group).
    window = (ROLE / "files/Invoke-TnwksMoveWindow.ps1").read_text()
    assert window.count("Wait-TnwksNoWsl $") == 4
    worst = 4 * minutes + 4 * 2 + max(3 * minutes + 3, 4 * minutes)
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
    assert "id = [int]$_.ProcessId" in live and "created = $_.CreationDate.ToUniversalTime().ToString('o')" in live
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
    assert config["boot"] == {"group_steps": False}
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


def run_account(tmp_path: Path, step: str, registered: str | None = None, disk_at: str | None = None,
                distro: str = "tnwks-moverh", pattern: str = "^tnwks-moverh", folder: str | None = None) -> dict:
    """Runs one step of the account script. registered: the folder where the account
    lists the distro (a, b, or None). disk_at: the folder that holds a disk file."""
    root = tmp_path / "root"
    for sub in ("a", "b", "elsewhere", "log"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    system = tmp_path / "win/System32"
    system.mkdir(parents=True, exist_ok=True)
    calls = tmp_path / "calls.txt"
    calls.write_text("")
    wsl = system / "wsl.exe"
    wsl.write_text('#!/bin/sh\necho "wsl $*" >> "$TNWKS_CALLS"\nexit 0\n')
    wsl.chmod(0o755)
    if disk_at:
        (root / disk_at / "ext4.vhdx").write_text("disk")
    config = {
        "name_pattern": pattern, "refused_names": ["Ubuntu"], "root": str(root), "distro": distro,
        "folder": str(root / "b"), "disk_name": "ext4.vhdx", "log_dir": str(root / "log"),
        "marker_name": "tnwks-move.marker", "rootfs": str(root / "rootfs.tar.gz"), "rootfs_sha256": "00",
        "linux_user": "moverh", "data_token": "token", "icon_base64": "AA==", "registration_values": {"DefaultUid": 1000},
    }
    (tmp_path / "config.json").write_text(json.dumps(config))
    reg = ""
    if registered:
        reg = f"$global:Reg['{{g1}}'] = @{{ DistributionName = '{distro}'; BasePath = '\\\\?\\{root / registered}'; VhdFileName = 'ext4.vhdx' }}"
    extra = f"-Folder '{folder}'" if folder else ""
    script = ACCOUNT_FAKES + f"""
$env:SystemRoot = '{tmp_path / "win"}'
$env:TNWKS_CALLS = '{calls}'
{reg}
& '{ACCOUNT}' -Config '{tmp_path / "config.json"}' -Step {step} {extra}
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


# --- The window procedure, in PowerShell --------------------------------------------------

WINDOW_FAKES = r"""
$global:Calls = New-Object System.Collections.Generic.List[string]
$global:Admins = New-Object System.Collections.Generic.List[string]
$global:Procs = New-Object System.Collections.Generic.List[object]
$global:Runs = @{}
function Get-LocalUser { param([string]$Name) [pscustomobject]@{ SID = [pscustomobject]@{ Value = $global:Sids[$Name] } } }
function Get-LocalGroupMember { param([string]$SID) @($global:Admins | ForEach-Object { [pscustomobject]@{ SID = [pscustomobject]@{ Value = $_ } } }) }
function Add-LocalGroupMember { param([string]$SID, $Member) $global:Calls.Add("join $Member"); $global:Admins.Add($Member) }
function Remove-LocalGroupMember { param([string]$SID, $Member) $global:Calls.Add("leave $Member"); [void]$global:Admins.Remove($Member) }
# As Task Scheduler reads it (TASK_STATE): a task with a running instance is Running,
# also when it is disabled; Disabled only with no instance. Settings.Enabled is the flag.
$global:Enabled = @{}
$global:Running = @{}
function Get-ScheduledTask {
    param([string]$TaskName)
    $state = if ($global:Running[$TaskName]) { 'Running' } elseif ($global:Enabled[$TaskName] -eq $false) { 'Disabled' } else { 'Ready' }
    [pscustomobject]@{ State = $state; Settings = [pscustomobject]@{ Enabled = ($global:Enabled[$TaskName] -ne $false) } }
}
function Get-ScheduledTaskInfo { param([string]$TaskName) [pscustomobject]@{ LastRunTime = [int]$global:Runs[$TaskName]; LastTaskResult = [int]$global:Codes[$TaskName] } }
function Disable-ScheduledTask { param([string]$TaskName) $global:Calls.Add("disable $TaskName"); $global:Enabled[$TaskName] = $false }
function Enable-ScheduledTask { param([string]$TaskName) $global:Calls.Add("enable $TaskName"); $global:Enabled[$TaskName] = $true }
function Get-TaskSid([string]$TaskName) { if ($TaskName -like '*-a-*') { $global:Sids['tnwks-moverh-a'] } else { $global:Sids['tnwks-moverh-b'] } }
function Stop-ScheduledTask {
    param([string]$TaskName)
    $global:Calls.Add("stop $TaskName")
    if ($TaskName -like '*-start') {
        $global:Running[$TaskName] = $false
        foreach ($p in @($global:Procs | Where-Object { $_.Task -eq $TaskName })) { [void]$global:Procs.Remove($p) }
    }
}
function Start-ScheduledTask {
    param([string]$TaskName)
    $global:Calls.Add("start $TaskName")
    $global:Runs[$TaskName] = 1 + [int]$global:Runs[$TaskName]
    $sid = Get-TaskSid $TaskName
    if ($TaskName -like '*-start') {
        $global:Running[$TaskName] = $true
        $global:Procs.Add([pscustomobject]@{ Name = 'wsl.exe'; OwnerSid = $sid; Task = $TaskName })
        return
    }
    if ($TaskName -like '*-stop') { foreach ($p in @($global:Procs | Where-Object { $_.OwnerSid -eq $sid })) { [void]$global:Procs.Remove($p) } }
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
function New-FakeAcl([string]$Sddl) {
    $acl = [pscustomobject]@{ Sddl = $Sddl }
    $acl | Add-Member -MemberType ScriptMethod -Name SetSecurityDescriptorSddlForm -Value { param($s, $parts) $this.Sddl = $s }
    $acl
}
function Get-Acl { param([string]$LiteralPath) New-FakeAcl $(if ($global:Acls.ContainsKey($LiteralPath)) { $global:Acls[$LiteralPath] } else { 'O:BAD:(A;ID;FA;;;SY)' }) }
function Set-Acl { param([string]$LiteralPath, $AclObject) $global:Calls.Add("acl $(Split-Path -Leaf (Split-Path -Parent $LiteralPath))/$(Split-Path -Leaf $LiteralPath) $($AclObject.Sddl)"); $global:Acls[$LiteralPath] = $AclObject.Sddl }
function Move-Item {
    param([string]$LiteralPath, [string]$Destination, [switch]$Force)
    if ($LiteralPath -like '*ext4.vhdx') {
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
               marker_a: bool = False, disabled: str | None = None) -> dict:
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
        "present": {"account": "tnwks-moverh-a", "folder": str(root / "a"), "result_dir": str(root / "log/a"),
                    "window_tasks": {k: f"tnwks-moverh-a-{v}" for k, v in names.items()},
                    "disable_tasks": ["tnwks-moverh-a-start"], "start_tasks": ["tnwks-moverh-a-start"]},
        "new": {"account": "tnwks-moverh-b", "folder": str(root / "b"), "result_dir": str(root / "log/b"),
                "window_tasks": {k: f"tnwks-moverh-b-{v}" for k, v in names.items()},
                "start_tasks": ["tnwks-moverh-b-start"]},
        "boot": {"group_steps": False},
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
    acls = {str(root / "a/ext4.vhdx"): OLD_SDDL}

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
if ({'$true' if any(state_of.get(t) == "Running" for t in state_of) else '$false'}) {{
    foreach ($t in @($global:Running.Keys | Where-Object {{ $global:Running[$_] }})) {{
        $global:Procs.Add([pscustomobject]@{{ Name = 'wsl.exe'; OwnerSid = (Get-TaskSid $t); Task = $t }}) }}
}}
& '{WINDOW}' {args}
$code = $LASTEXITCODE
$tasks = @{{}}; foreach ($t in @($global:Enabled.Keys)) {{ $tasks[$t] = (Get-ScheduledTask -TaskName $t).State }}
[Console]::Out.Write((@{{ code = $code; calls = @($global:Calls); admins = @($global:Admins); tasks = $tasks;
    acls = $global:Acls }} | ConvertTo-Json -Compress -Depth 5))
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
    disk_b = f"O:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1301bf;;;{B_SID})"
    marker_a = f"O:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;{A_SID})"
    assert calls == [
        f"join {A_SID}",
        "disable tnwks-moverh-a-start", "stop tnwks-moverh-a-start", "start tnwks-moverh-a-stop",
        f"acl a/tnwks-move.marker {marker_a}",
        "move a -> b", f"acl b/ext4.vhdx {disk_b}",
        "start tnwks-moverh-a-unregister",
        "start tnwks-moverh-b-import", "start tnwks-moverh-b-stop",
        f"leave {A_SID}",
        "enable tnwks-moverh-b-start",
    ]
    assert out["markers"] == ["window-started", "window-done"]
    assert not out["disk_a"] and out["disk_b"] and out["marker_a"]
    assert out["admins"] == []
    groups = [line for line in out["log"] if line.startswith("group: ")]
    assert groups == ["group: tnwks-moverh-a joined Administrators", "group: tnwks-moverh-a left Administrators"]
    # The join is before step 1, the leave is step 7.
    log = out["log"]
    assert log.index(groups[0]) < next(i for i, line in enumerate(log) if line.startswith("step 1:"))
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


@needs_pwsh
def test_the_restart_line_runs_only_with_the_switch_on(tmp_path):
    out = run_window(tmp_path, {"mode": "forward", "group_steps": False, "stop_after": 0, "rehearsal_group_test": False},
                     restart=True)
    assert out["code"] == 0, out["log"]
    assert out["calls"][-1] == "restart" and out["calls"].count("restart") == 1
    assert "restart: the end of the window" in out["log"]
    assert not any(line.startswith("would restart") for line in out["log"])


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
    for i, last in enumerate(("window-started", "boot-run")):
        state = "t window-started\n" + ("t boot-run\n" if last == "boot-run" else "")
        out = run_window(tmp_path / str(i), FORWARD_TEST, state=state, disk=("b",), acl_file=OLD_SDDL, marker_a=True,
                         restart=True)
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
    good = a_run("forward", True, ["joined", "left"]) + a_run("wayback", True, ["joined", "left"]) + a_run("boot", False, [])
    assert run_group_log(good) == []
    cases = {
        "not the group test": a_run("forward", False, ["joined", "left"]),
        "the boot task": a_run("boot", False, ["joined", "left"]),
        "no leave": a_run("wayback", True, ["joined"]),
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
