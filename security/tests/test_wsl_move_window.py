"""The window play of the move of the distro (Design Doc 0004, section 9, steps 5 and 6; plan task 10.15).

playbooks/windows-move-window.yml, roles/windows_wsl_move/tasks/window.yml and files/Read-TnwksMoveWindow.ps1:
the status, the precheck, the start of a forward window or a way back, and the check play of step 6.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from test_wsl_start_unelevated import needs_pwsh, pwsh

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLES = ANSIBLE / "roles"
ROLE = ROLES / "windows_wsl_move"
SCRIPT = ROLE / "files/Read-TnwksMoveWindow.ps1"
WINDOW_SCRIPT = ROLES / "windows_wsl_move_rehearsal/files/Invoke-TnwksMoveWindow.ps1"
DEFAULTS = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
VARS = yaml.safe_load((ROLE / "vars/main.yml").read_text())
WINDOW = yaml.safe_load((ROLE / "tasks/window.yml").read_text())


def flat(items) -> list[dict]:
    out = []
    for task in items or []:
        out.append(task)
        for key in ("block", "rescue", "always"):
            out.extend(flat(task.get(key)))
    return out


TASKS = {t["name"]: t for t in flat(WINDOW)}


# --- The play and its tasks -----------------------------------------------------------------

def test_the_play_runs_for_one_host_with_the_move_on():
    play = yaml.safe_load((ANSIBLE / "playbooks/windows-move-window.yml").read_text())[0]
    assert play["hosts"] == "windows_hosts" and play["become"] is False and play["gather_facts"] is False
    one, on, role = play["tasks"]
    assert one["ansible.builtin.assert"]["that"] == "ansible_play_hosts_all | length == 1" and one["run_once"] is True
    assert on["ansible.builtin.assert"]["that"] == "wsl_move | default(false) | bool"
    assert role["ansible.builtin.include_role"] == {"name": "windows_wsl_move", "tasks_from": "window.yml"}


def test_a_start_and_its_precheck_run_only_in_the_phase_move():
    assert DEFAULTS["wsl_move_window_modes"] == ["status", "precheck", "forward", "wayback", "check"]
    phase = TASKS["The phase of the move lets this mode run"]["ansible.builtin.assert"]["that"]
    assert phase == [("wsl_move_phase == 'move' if wsl_move_window in ['precheck', 'forward', 'wayback'] "
                      "else wsl_move_phase in ['move', 'done']"),
                     # The check play reads the tasks of the present account: not after their deletion.
                     "wsl_move_window != 'check' or not (wsl_move_present_removed | bool)"]


def test_a_start_needs_the_host_again_no_check_run_and_not_the_login_of_the_present_account():
    login = TASKS["A start runs only when Ansible logs in as another account than the present one"]
    assert login["ansible.builtin.assert"]["that"] == "wsl_move_window_as_other | bool"
    fact = TASKS["Whether Ansible logs in as the present account"]
    assert fact["ansible.builtin.set_fact"]["wsl_move_window_as_other"] == "{{ ansible_user | lower != wsl_move_present_account | lower }}"
    assert fact["no_log"] is True
    again = TASKS["A start names the host again, and is not a check run"]
    assert again["ansible.builtin.assert"]["that"] == [
        "(wsl_move_window_host | default('')) == inventory_hostname", "not ansible_check_mode"]
    for task in (login, again):
        assert task["when"] == "wsl_move_window in ['forward', 'wayback']"
    # The guards pass before the start: the start is the last block.
    names = [t["name"] for t in WINDOW]
    assert names.index("The guards, the readiness and the check") < names.index("The start of the window") == len(names) - 1


def test_the_precheck_adds_the_login_of_ansible_to_the_readiness():
    keep = TASKS["Keep the lines of the readiness"]["ansible.builtin.set_fact"]["wsl_move_window_lines"]
    assert "'pass': wsl_move_window_as_other | bool" in keep
    assert "(login.high | bool) and (login.administrators | bool)" in keep
    login = TASKS["Read the token of the login of Ansible"]
    assert "vars" not in login and "S-1-16-12288" in login["ansible.windows.win_powershell"]["script"]


def test_each_read_runs_the_script_as_system_and_logs_nothing():
    read = yaml.safe_load((ROLE / "tasks/window_read.yml").read_text())[0]
    assert read["ansible.windows.win_powershell"]["script"] == "{{ lookup('ansible.builtin.file', 'Read-TnwksMoveWindow.ps1') }}"
    assert read["vars"]["ansible_become_user"] == "SYSTEM" and read["no_log"] is True
    assert read["check_mode"] == "{{ wsl_move_window_read_mode == 'start' and ansible_check_mode }}"


def test_the_kept_reads_come_before_the_hashes_of_the_configs():
    names = [t["name"] for t in WINDOW]
    assert names.index("The reads of today, as step 3 kept them") < names.index("The status of the window")
    today = yaml.safe_load((ROLE / "tasks/read_today.yml").read_text())
    assert {"name": "The kept reads of today", "ansible.builtin.include_tasks": "read_kept.yml"} in today


def test_the_paths_of_other_roles_are_theirs():
    check = yaml.safe_load((ROLES / "windows_host_check/defaults/main.yml").read_text())
    assert DEFAULTS["wsl_move_host_check"] == check["host_check_dir"] + "\\" + check["host_check_script"]
    bridge = yaml.safe_load((ROLES / "windows_lan_bridge/defaults/main.yml").read_text())
    address = bridge["lan_bridge_ip_file"].replace("{{ lan_bridge_ip_dir }}", bridge["lan_bridge_ip_dir"])
    assert DEFAULTS["wsl_move_bridge_address_file"] == address.replace("{{ lan_bridge_dir }}", bridge["lan_bridge_dir"])
    read = VARS["wsl_move_window_read"]
    assert "windows_host_check/files/Invoke-TnwksHostCheck.ps1', rstrip=false) | hash('sha256')" in read["host_check_sha256"]
    assert read["window_script"] == "{{ wsl_move_dir }}\\Invoke-TnwksMoveWindow.ps1"
    assert read["hashes"] == "{{ wsl_move_file_hashes }}"


def test_each_key_that_the_script_reads_is_in_the_config():
    read = VARS["wsl_move_window_read"]
    for key, sub in re.findall(r"\$c\.([a-z_0-9]+)(?:\.([a-z_0-9]+))?", SCRIPT.read_text()):
        assert key in read, key
        if sub and isinstance(read[key], dict):
            assert sub in read[key], f"{key}.{sub}"


def test_the_script_takes_the_parts_of_the_window_procedure_from_its_copy():
    text = SCRIPT.read_text()
    window = WINDOW_SCRIPT.read_text()
    for name in re.search(r"\$TnwksWindowFunctions = @\(([^)]*)\)", text).group(1).replace("\n", " ").split(","):
        assert re.search(rf"^function {name.strip().strip(chr(39))}\b", window, re.MULTILINE), name
    for name in re.search(r"\$TnwksWindowVariables = @\(([^)]*)\)", text).group(1).split(","):
        assert re.search(rf"^\${name.strip().strip(chr(39))} = ", window, re.MULTILINE), name
    load = text[text.index("function Initialize-TnwksWindowPart"):]
    assert load.index("Test-TnwksCopy $c.window_script") < load.index("Import-TnwksWindowPart")
    # The read of the disk file is the host check's own, after its SHA-256.
    read = text[text.index("function Get-TnwksHostCheckRead"):]
    assert read.index("Get-FileHash -LiteralPath $c.host_check") < read.index(". $c.host_check -Library")


def test_the_script_changes_nothing_but_the_request_and_the_start():
    text = SCRIPT.read_text()
    for word in ("Set-Acl", "Enable-ScheduledTask", "Disable-ScheduledTask", "Stop-ScheduledTask", "Remove-Item",
                 "Move-Item", "Set-ItemProperty", "Add-LocalGroupMember", "Restart-Computer", "SetRules", "SetOwner"):
        assert word not in text, word
    assert text.count("WriteAllText") == 1 and text.count("Start-ScheduledTask") == 1
    start = text[text.index("function Start-TnwksWindow"):]
    assert start.index("Get-TnwksGuard") < start.index("WriteAllText") < start.index("Start-ScheduledTask")


# --- The script, in PowerShell ----------------------------------------------------------------

TASK_FAKES = r"""
$global:Started = New-Object System.Collections.Generic.List[string]
function Get-ScheduledTask([string]$TaskName, $ErrorAction) {
    $t = $global:Tasks[$TaskName]
    if (-not $t) { return $null }
    [pscustomobject]@{ TaskName = $TaskName; State = $t.State; Settings = [pscustomobject]@{ Enabled = $t.Enabled }
        Principal = [pscustomobject]@{ UserId = $t.User }; Actions = @($t.Actions) }
}
function Get-ScheduledTaskInfo([string]$TaskName) {
    $t = $global:Tasks[$TaskName]
    [pscustomobject]@{ LastRunTime = $t.Last; LastTaskResult = $t.Result }
}
function Start-ScheduledTask([string]$TaskName) { $global:Started.Add($TaskName); $global:Tasks[$TaskName].State = 'Running' }
function Test-TnwksRestartPending { $global:Pending }
function Test-TnwksPresentSession { $global:Session }
function Get-TnwksInstalledWslVersion { $global:Version }
"""

PRESENT_TASKS = ["tnwks-lan-bridge-ip", "tnwks-wsl-watchdog"]
PRESENT_WINDOW = ["tnwks-wsl-move-present-stop", "tnwks-wsl-move-present-unregister", "tnwks-wsl-move-present-import"]
NEW_WINDOW = ["tnwks-wsl-move-new-stop", "tnwks-wsl-move-new-unregister", "tnwks-wsl-move-new-import"]
NEW_START = ["tnwks-lan-bridge-ip-account", "tnwks-wsl-watchdog-account"]


def config(tmp_path: Path, **extra) -> dict:
    copy = tmp_path / "Invoke-TnwksMoveWindow.ps1"
    copy.write_text("# a copy\n")
    cfg = {
        "way": "forward", "force_session": False,
        "present": {"account": "standin1", "folder": "C:\\Users\\standin1\\wsl", "tasks": PRESENT_TASKS,
                    "window_tasks": PRESENT_WINDOW},
        "new": {"account": "standin-wsl", "folder": "C:\\ProgramData\\tnwks-wsl-move\\disk", "start_tasks": NEW_START,
                "window_tasks": NEW_WINDOW},
        "disk_name": "ext4.vhdx", "distro": "Ubuntu",
        "state_file": str(tmp_path / "window.state"), "log_file": str(tmp_path / "window.log"),
        "request_file": str(tmp_path / "request.json"), "version_file": str(tmp_path / "wsl-version.txt"),
        "window_task": "tnwks-wsl-move-window", "boot_task": "tnwks-wsl-move-boot",
        "hashes": {str(copy): hashlib.sha256(copy.read_bytes()).hexdigest()},
        "window_script": str(copy), "rehearsed_version": "3.0.5.0", "service_binary": "C:\\x",
        "log_lines": 60, "line_limit": 300, "batch_logons": 10,
    }
    cfg.update(extra)
    return cfg


def tasks_state(way: str = "forward") -> dict:
    tasks = {name: {"State": "Ready", "Enabled": True} for name in PRESENT_TASKS + PRESENT_WINDOW + NEW_WINDOW}
    tasks.update({name: {"State": "Ready", "Enabled": way == "wayback"} for name in NEW_START})
    tasks["tnwks-wsl-move-window"] = {"State": "Ready", "Enabled": True, "Last": "2026-11-01T00:00:00Z", "Result": 0}
    tasks["tnwks-wsl-move-boot"] = {"State": "Ready", "Enabled": True}
    return tasks


def ps_value(value) -> str:
    """A JSON value as PowerShell: a hashtable of hashtables."""
    if isinstance(value, dict):
        return "@{" + "; ".join(f"'{k}' = {ps_value(v)}" for k, v in value.items()) + "}"
    if isinstance(value, bool):
        return "$true" if value else "$false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "$null"
    if isinstance(value, list):
        return "@(" + ", ".join(ps_value(v) for v in value) + ")"
    return "'" + str(value).replace("'", "''") + "'"


def run(cfg: dict, body: str, tasks: dict | None = None, pending=False, session=False, version="3.0.5.0"):
    encoded = base64.b64encode(json.dumps(cfg).encode()).decode()
    script = f"""$env:TNWKS_MOVE_WINDOW_LIBRARY = '1'
. '{SCRIPT}' -Mode status -Config ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')))
{TASK_FAKES}
$global:Tasks = {ps_value(tasks or {})}
foreach ($k in @($global:Tasks.Keys)) {{ if ($global:Tasks[$k].Last) {{ $global:Tasks[$k].Last = [datetime]$global:Tasks[$k].Last }} }}
$global:Pending = {ps_value(pending)}
$global:Session = {ps_value(session)}
$global:Version = {ps_value(version)}
{body}
"""
    result = pwsh(script)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


GUARDS = "Get-TnwksGuard | ConvertTo-Json -Compress -Depth 4"


def fails(lines: list[dict]) -> list[str]:
    return [line["name"] for line in lines if not line["pass"]]


def ready(tmp_path: Path, markers: str = "") -> None:
    (tmp_path / "window.state").write_text(markers)
    (tmp_path / "window.log").write_text("")
    (tmp_path / "wsl-version.txt").write_text("3.0.5.0")


@needs_pwsh
def test_a_forward_start_passes_each_guard_on_a_host_that_is_ready(tmp_path):
    ready(tmp_path)
    lines = run(config(tmp_path), GUARDS, tasks_state())
    assert fails(lines) == [] and len(lines) == 11
    # After a way back, a forward window may start again.
    ready(tmp_path, "2026-11-01T00:00:00Z window-started\n2026-11-01T01:00:00Z wayback-done\n")
    assert fails(run(config(tmp_path), GUARDS, tasks_state())) == []


@needs_pwsh
@pytest.mark.parametrize(("case", "line"), [
    ("copy", "the copies of the scripts and the configs on the host are the role's"),
    ("marker", "the last marker of the state file lets a run of the way forward start"),
    ("no state", "the last marker of the state file lets a run of the way forward start"),
    ("no log", "the log of the window is there"),
    ("running", "the window task is there and enabled, and does not run"),
    ("no boot", "the boot task of the way back is there and enabled"),
    ("window task", "the window tasks of both accounts are there and enabled"),
    ("present task", "each WSL task of the present account in the config is there"),
    ("no copy", "each start task of the new account is there and disabled"),
    ("enabled copy", "each start task of the new account is there and disabled"),
    ("version", "the installed WSL is the version of the rehearsal and the version that step 3 saved"),
    ("saved version", "the installed WSL is the version of the rehearsal and the version that step 3 saved"),
    ("pending", "no restart is pending for updates"),
    ("session", "no session of the present account is on the host"),
    ("unread session", "no session of the present account is on the host"),
])
def test_each_guard_stops_a_forward_start(tmp_path, case, line):
    ready(tmp_path)
    cfg, tasks, extra = config(tmp_path), tasks_state(), {}
    if case == "copy":
        Path(cfg["window_script"]).write_text("# another copy\n")
    if case == "marker":
        (tmp_path / "window.state").write_text("2026-11-01T00:00:00Z window-started\n")
    if case == "no state":
        (tmp_path / "window.state").unlink()
    if case == "no log":
        (tmp_path / "window.log").unlink()
    if case == "running":
        tasks["tnwks-wsl-move-window"]["State"] = "Running"
    if case == "no boot":
        del tasks["tnwks-wsl-move-boot"]
    if case == "window task":
        tasks[NEW_WINDOW[1]]["Enabled"] = False
    if case == "present task":
        del tasks[PRESENT_TASKS[0]]
    if case == "no copy":
        del tasks[NEW_START[0]]
    if case == "enabled copy":
        tasks[NEW_START[1]]["Enabled"] = True
    if case == "version":
        extra["version"] = "3.0.6.0"
    if case == "saved version":
        (tmp_path / "wsl-version.txt").write_text("2.6.1.0")
    if case == "pending":
        extra["pending"] = True
    if case == "session":
        extra["session"] = True
    if case == "unread session":
        extra["session"] = None
    assert fails(run(cfg, GUARDS, tasks, **extra)) == [line]


@needs_pwsh
def test_a_session_of_the_present_account_passes_only_by_the_switch(tmp_path):
    ready(tmp_path)
    lines = run(config(tmp_path, force_session=True), GUARDS, tasks_state(), session=True)
    assert fails(lines) == []
    assert "a session of the present account is on the host, and the run goes on by wsl_move_window_force_session" in [
        line["name"] for line in lines]


@needs_pwsh
@pytest.mark.parametrize(("marker", "ok"), [
    ("window-done", True), ("window-started", True), ("boot-run", True), ("wayback-done", False), (None, False)])
def test_a_way_back_starts_after_a_started_or_done_window_only(tmp_path, marker, ok):
    ready(tmp_path, f"2026-11-01T00:00:00Z {marker}\n" if marker else "")
    lines = run(config(tmp_path, way="wayback"), GUARDS, tasks_state("wayback"))
    assert fails(lines) == ([] if ok else ["the last marker of the state file lets a run of the way wayback start"])


START = """try { Start-TnwksWindow | ConvertTo-Json -Compress } catch {
    @{ error = $_.Exception.Message; started = @($global:Started) } | ConvertTo-Json -Compress }"""


@needs_pwsh
@pytest.mark.parametrize("way", ["forward", "wayback"])
def test_a_start_writes_the_request_of_its_way_and_starts_the_window_once(tmp_path, way):
    ready(tmp_path, "2026-11-01T00:00:00Z window-done\n" if way == "wayback" else "")
    out = run(config(tmp_path, way=way), START, tasks_state(way))
    request = {"mode": way, "group_steps": True, "stop_after": 0, "rehearsal_group_test": False}
    assert out["started"] is True and out["state"] == "Running"
    assert json.loads(out["request"]) == request
    assert json.loads((tmp_path / "request.json").read_text()) == request


@needs_pwsh
def test_a_start_with_a_failed_guard_writes_nothing_and_starts_nothing(tmp_path):
    ready(tmp_path)
    out = run(config(tmp_path), START, tasks_state(), pending=True)
    assert out["started"] == [] and not (tmp_path / "request.json").exists()
    assert out["error"] == "A guard of the start fails: no restart is pending for updates. Nothing is changed."


STATUS = "Get-TnwksStatus | ConvertTo-Json -Compress -Depth 4"
STATUS_FAKES = """function Get-TnwksBootTime { [datetime]'2026-11-20T03:00:00Z' }
function Get-LocalUser([string]$Name) { [pscustomobject]@{ SID = [pscustomobject]@{ Value = $(if ($Name -eq 'standin1') { 'S-1-5-21-1-2-3-1001' } else { 'S-1-5-21-1-2-3-1002' }) } } }
"""


@needs_pwsh
def test_the_status_reads_absent_for_each_file_and_task_that_is_not_there(tmp_path):
    out = run(config(tmp_path), STATUS_FAKES + STATUS, {})
    assert out["state_file"] == out["last_marker"] == out["request"] == out["log"] == "absent"
    assert out["window_task"] == out["boot_task"] == "absent" and out["disabled_list"] == "absent"
    assert out["markers"] == out["log_tail"] == out["log_ends"] == []


@needs_pwsh
def test_the_status_masks_each_account_sid_and_folder_and_bounds_each_line(tmp_path):
    (tmp_path / "window.state").write_text("2026-11-20T02:00:00Z window-started\n2026-11-20T02:30:00Z window-done\n")
    lines = [f"2026-11-20T02:00:{i:02d}Z forward line {i}" for i in range(70)]
    lines += [
        "2026-11-20T02:10:00Z forward stop at step 1: C:\\Users\\standin1\\wsl\\ext4.vhdx of standin1",
        "2026-11-20T02:11:00Z forward fail: O:S-1-5-21-1-2-3-1002D:P(A;;FA;;;S-1-5-21-9-9-9-500) " + "x" * 400,
        "2026-11-20T02:12:00Z forward result: done",
    ]
    (tmp_path / "window.log").write_text("\n".join(lines) + "\n")
    (tmp_path / "request.json").write_text('{"mode":"forward","group_steps":true}')
    tasks = tasks_state()
    out = run(config(tmp_path), STATUS_FAKES + STATUS, tasks)
    assert out["last_marker"] == "window-done" and out["state_file"] == "2 marker(s)"
    assert out["request"] == '{"mode":"forward","group_steps":true}'
    assert out["window_task"].startswith("Ready, enabled True, last run 2026-11-01T00:00:00")
    assert len(out["log_tail"]) == 60 and out["log"] == "73 line(s)"
    assert out["log_ends"][0] == "2026-11-20T02:10:00Z forward stop at step 1: <present-folder>\\ext4.vhdx of <present>"
    assert out["log_ends"][1].startswith("2026-11-20T02:11:00Z forward fail: O:<new-sid>D:P(A;;FA;;;<sid>) xxx")
    assert out["log_ends"][1].endswith(" [cut]") and len(out["log_ends"][1]) == 306
    assert out["log_ends"][2] == "2026-11-20T02:12:00Z forward result: done"
    text = json.dumps(out)
    assert "standin" not in text and "S-1-5-21-1" not in text


@needs_pwsh
@pytest.mark.parametrize(("action", "script", "runs"), [
    ({"Execute": "C:\\Windows\\System32\\wsl.exe", "Arguments": "-d Ubuntu"}, None, True),
    ({"Execute": "wsl", "Arguments": "--shutdown"}, None, True),
    ({"Execute": "powershell.exe", "Arguments": "-File \"SCRIPT\""}, "& wsl.exe -d Ubuntu -u root true\n", True),
    ({"Execute": "powershell.exe", "Arguments": "-File \"SCRIPT\""}, "wsl --terminate Ubuntu\n", True),
    ({"Execute": "powershell.exe", "Arguments": "-File \"SCRIPT\""}, "Get-Date\n", False),
    ({"Execute": "powershell.exe", "Arguments": "-File \"C:\\ProgramData\\tnwks-wsl-move\\x.ps1\""}, None, False),
    ({"Execute": "C:\\Program Files\\tnwks-wsl-tools\\tool.exe", "Arguments": ""}, None, False),
])
def test_a_task_runs_wsl_by_its_command_or_by_its_script(tmp_path, action, script, runs):
    action = {**action, "Arguments": action["Arguments"].replace("SCRIPT", "C:\\ProgramData\\tnwks-x\\s.ps1")}
    fake = f"function Get-TnwksActionScript([string]$Path) {{ if ($Path -eq 'C:\\ProgramData\\tnwks-x\\s.ps1') {{ {ps_value(script)} }} }}\n"
    out = run(config(tmp_path), fake + f"""$t = [pscustomobject]@{{ Actions = @([pscustomobject]{ps_value(action)}) }}
@{{ runs = (Test-TnwksTaskRunsWsl $t) }} | ConvertTo-Json -Compress""")
    assert out["runs"] is runs


CHECK_TOKEN = """function Get-TnwksWslProcess([string]$Sid) { @($global:Groups.Keys | ForEach-Object { [pscustomobject]@{ ProcessId = $_ } }) }
function Initialize-TnwksWindowRead { }
$line = Get-TnwksCheckToken
@{ pass = $line } | ConvertTo-Json -Compress"""


@needs_pwsh
@pytest.mark.parametrize(("groups", "ok"), [
    ({1: ["S-1-16-8192 60", "S-1-5-32-544 10"], 2: ["S-1-16-8192 60"]}, True),
    ({1: ["S-1-16-8192 60", "S-1-5-32-544 f"]}, False),
    ({1: ["S-1-16-12288 60"]}, False),
    ({1: ["S-1-5-32-545 7"]}, False),
    ({}, False),
])
def test_the_token_read_of_the_check_passes_only_for_medium_tokens_with_no_administrators(tmp_path, groups, ok):
    """Administrators as deny only (0x10) passes; enabled (0x4) fails; no process fails."""
    fake = "class TnwksMoveToken { static [string[]] Groups([int]$id) { return $global:Groups[$id] } }\n"
    table = "$global:Groups = @{" + "; ".join(f"{k} = @({', '.join(repr(g) for g in v)})" for k, v in groups.items()) + "}\n"
    out = run(config(tmp_path), fake + table + CHECK_TOKEN)
    assert out["pass"] is ok


def test_the_check_token_read_is_the_line_of_the_check():
    text = SCRIPT.read_text()
    check = text[text.index("function Get-TnwksCheck {"):text.index("# --- Status")]
    assert "Get-TnwksCheckToken $nSid" in check


@needs_pwsh
def test_the_parts_of_the_window_script_load_from_the_real_script(tmp_path):
    out = run(config(tmp_path), f"""$text = Import-TnwksWindowPart '{WINDOW_SCRIPT}' $TnwksWindowFunctions $TnwksWindowVariables
. ([scriptblock]::Create($text))
@{{ admin = $AdministratorsSid; source = ($TnwksSecuritySource -match 'public static class TnwksSecurity');
   functions = @($TnwksWindowFunctions | Where-Object {{ Get-Command $_ -CommandType Function -ErrorAction SilentlyContinue }}).Count }} | ConvertTo-Json -Compress""")
    assert out == {"admin": "S-1-5-32-544", "source": True, "functions": 6}


# The reads of the host behind the lines of the readiness and the check: each a fake, so a
# case changes one of them. The parts of the window script load as nothing.
HOST_FAKES = r"""
function Initialize-TnwksWindowPart { $script:TnwksWindowPartText = '' }
function Initialize-TnwksWindowRead { }
function Get-TnwksSid([string]$Account) { if ($Account -eq 'standin1') { 'S-1-5-21-1-2-3-1001' } else { 'S-1-5-21-1-2-3-1002' } }
function Get-TnwksBootTime { ([datetime]'2026-11-20T03:00:00Z').ToUniversalTime() }
function Get-TnwksOtherWslTask([string]$Sid) { @($global:Other) }
function Get-TnwksWslProcess([string]$Sid) { @($global:Processes[$Sid] | Where-Object { $_ }) }
function Get-TnwksCheckToken([string]$Sid) { $global:Token }
function Test-TnwksAdmin([string]$Sid) { $global:Admins -contains $Sid }
function Get-TnwksHostCheckRead([string]$Path, [string]$Account) { , @($global:Broken) }
function Get-TnwksBatchLogon([string]$Sid) { @($global:Logons) }
function Get-TnwksHiveRegistration([string]$Sid) { $global:Hives[$Sid] }
class TnwksDosDevice { static [string] Target([string]$d) { return $global:Device } }
class TnwksSecurity {
    static [string] Enable() { return 'SeBackupPrivilege True, SeRestorePrivilege True, SeTakeOwnershipPrivilege True' }
    static [string] ReadSddl([string]$p) { if ($global:Unreadable -contains $p) { throw 'no' }; return 'O:SY' }
    static [string] ReadGroup([string]$p) { return 'SY' }
}
function Get-CimInstance { [pscustomobject]@{ LocalPath = $global:Profile } }
function netsh.exe { $global:Proxy }
function Get-ScheduledTask([string]$TaskName, $ErrorAction) {
    $t = $global:Tasks[$TaskName]
    if (-not $t) { return $null }
    [pscustomobject]@{ TaskName = $TaskName; State = $t.State; Settings = [pscustomobject]@{ Enabled = $t.Enabled } }
}
function Get-ScheduledTaskInfo([string]$TaskName) { [pscustomobject]@{ LastRunTime = [datetime]$global:Tasks[$TaskName].Last; LastTaskResult = $global:Tasks[$TaskName].Result } }
$global:Other = @()
$global:Token = $true
$global:Admins = @('S-1-5-21-1-2-3-1001')
$global:Broken = @()
$global:Logons = @($true, $true)
$global:Device = '\Device\HarddiskVolume3'
$global:Unreadable = @()
$global:Processes = @{ 'S-1-5-21-1-2-3-1001' = @([pscustomobject]@{ ProcessId = 1 }) }
function New-Registration([string]$Base, [int64]$Uid) {
    [pscustomobject]@{ root = [pscustomobject]@{ DefaultDistribution = '{guid}' }; found = @([pscustomobject]@{
        DistributionName = 'Ubuntu'; BasePath = $Base; VhdFileName = 'ext4.vhdx'; DefaultUid = $Uid; Flags = 15
        KernelCommandLine = 'quiet'; DefaultEnvironment = @('HOSTTYPE=x86_64', 'LANG=C.UTF-8'); PSChildName = '{guid}' }) }
}
$global:Hives = @{
    'S-1-5-21-1-2-3-1001' = (New-Registration '\\?\C:\Users\standin1\wsl' 1000)
    'S-1-5-21-1-2-3-1002' = (New-Registration '\\?\C:\ProgramData\tnwks-wsl-move\disk' 1000)
}
"""


def host_config(tmp_path: Path) -> dict:
    (tmp_path / "registration.json").write_text(json.dumps({
        "dwords": {"DefaultUid": 1000, "Flags": 15}, "kernel_command_line": "quiet",
        "default_environment": ["HOSTTYPE=x86_64", "LANG=C.UTF-8"], "default": True, "nat": {}}))
    (tmp_path / "present.wslconfig").write_text("[wsl2]\nmemory=40GB\n# a note\nprocessors=8\n")
    (tmp_path / "prof\\.wslconfig").write_text("# Managed by tnwks-ops\n[wsl2]\nmemory=40GB\nprocessors=8\n")
    (tmp_path / "address.json").write_text('{"address": "172.20.1.2"}')
    # Written after the boot of the fakes (2026-11-20T03:00:00Z).
    after_boot = datetime(2026, 11, 20, 3, 5, tzinfo=UTC).timestamp()
    os.utime(tmp_path / "address.json", (after_boot, after_boot))
    return config(tmp_path, registration_file=str(tmp_path / "registration.json"),
                  wslconfig_file=str(tmp_path / "present.wslconfig"), bridge_address_file=str(tmp_path / "address.json"))


def after_window(tmp_path: Path) -> dict:
    (tmp_path / "window.state").write_text("2026-11-20T02:00:00Z window-started\n2026-11-20T02:30:00Z window-done\n")
    (tmp_path / "window.log").write_text("\n".join(f"2026-11-20T02:{i:02d}:00Z forward {text}" for i, text in enumerate([
        "start, request: {}", "step 5 passed", "step 7 passed", "step 8 passed", "marker window-done",
        "restart: the end of the window", "result: done"])) + "\n2026-11-20T03:01:00Z boot no action: the last marker is 'window-done'\n")
    tasks = {name: {"State": "Ready", "Enabled": False} for name in PRESENT_TASKS}
    tasks.update({name: {"State": "Running", "Enabled": True, "Last": "2026-11-20T03:02:00Z", "Result": 267009} for name in NEW_START})
    return tasks


READINESS = "Get-TnwksReadiness | ConvertTo-Json -Compress -Depth 4"
CHECK = "$global:Processes = @{}; $global:Profile = 'PROFILE'; $global:Admins = @(); $global:Proxy = @('Address Port Address Port', '0.0.0.0 80 172.20.1.2 80', '0.0.0.0 443 172.20.1.2 443')\n"


@needs_pwsh
@pytest.mark.parametrize(("case", "line"), [
    ("ready", None),
    ("other task", "no other enabled task of the present account runs WSL"),
    ("no hive", "the present account registers the distro by its name, once, at the folder of the config"),
    ("other base", "the present account registers the distro by its name, once, at the folder of the config"),
    ("subst", "the folder of today and the folder of the new disk file are on one drive, which is not a subst drive"),
    ("other drive", "the folder of today and the folder of the new disk file are on one drive, which is not a subst drive"),
    ("not running", "the distro of the present account runs"),
    ("unreadable folder", "the hardened open of the procedure reads the disk file and both folders as SYSTEM"),
    ("not plain", "the read of the host check finds the disk file of today, reads it, and it is a plain file"),
    ("not elevated", "each batch logon of the present account in the Security log had an elevated token, and it is in Administrators"),
    ("no logon", "each batch logon of the present account in the Security log had an elevated token, and it is in Administrators"),
    ("not a member", "each batch logon of the present account in the Security log had an elevated token, and it is in Administrators"),
])
def test_each_read_of_the_readiness(tmp_path, case, line):
    cfg = host_config(tmp_path)
    change = {
        "ready": "", "other task": "$global:Other = @('x')", "no hive": "$global:Hives.Remove('S-1-5-21-1-2-3-1001')",
        "other base": "$global:Hives['S-1-5-21-1-2-3-1001'].found[0].BasePath = 'C:\\Users\\standin1\\other'",
        "subst": "$global:Device = '\\??\\C:\\Users\\standin1'", "not running": "$global:Processes = @{}",
        "unreadable folder": "$global:Unreadable = @('C:\\ProgramData\\tnwks-wsl-move\\disk')",
        "not plain": "$global:Broken = @('owner', 'not-a-plain-file')", "not elevated": "$global:Logons = @($true, $false)",
        "no logon": "$global:Logons = @()", "not a member": "$global:Admins = @()", "other drive": "",
    }[case]
    if case == "other drive":
        cfg["new"]["folder"] = "D:\\tnwks-wsl-move\\disk"
    lines = run(cfg, HOST_FAKES + change + "\n" + READINESS)
    assert len(lines) == 7 and fails(lines) == ([line] if line else [])


@needs_pwsh
def test_the_readiness_and_the_check_fail_on_another_copy_of_the_window_script(tmp_path):
    cfg = config(tmp_path)
    Path(cfg["window_script"]).write_text("# another copy\n")
    for read in ("Get-TnwksReadiness", "Get-TnwksCheck"):
        lines = run(cfg, f"{read} | ConvertTo-Json -Compress -Depth 4")
        lines = lines if isinstance(lines, list) else [lines]
        assert fails(lines) == ["the copy of the window script on the host is the role's, its parts load, and both accounts are there"]


@needs_pwsh
@pytest.mark.parametrize(("case", "line"), [
    ("done", None),
    ("no restart", "the last marker is a done window, written before the boot"),
    ("went back", "the last forward run passed steps 5, 7 and 8 and ended done; the present account listed no distro at step 5"),
    ("token", "each WSL process of the new account reads Medium Mandatory Level and no Administrators group, and one runs"),
    ("present runs", "no WSL process of the present account runs"),
    ("present task", "each WSL task of the present account in the config is disabled"),
    ("other task", "no other enabled task of the present account runs WSL"),
    ("copy before boot", "each start task of the new account is enabled, and ran after the boot with result 0 or still runs"),
    ("copy failed", "each start task of the new account is enabled, and ran after the boot with result 0 or still runs"),
    ("other path", "the new account registers the distro once, at the new disk file, as its default distro when it was the default of today"),
    ("root", "the default user of the distro is the user of today, and not root"),
    ("flags", "the flags, the default environment and the kernel command line of the registration are as step 3 read them"),
    ("environment", "the flags, the default environment and the kernel command line of the registration are as step 3 read them"),
    ("proxy", "the port proxy points to the address that the new VM wrote after the boot"),
    ("wslconfig", "the .wslconfig of the new account holds each setting of the .wslconfig of today"),
    ("rules", "the read of the host check of the moved disk file passes: its owner is the new account, and its rules are the rule"),
    ("admin", "neither account is in Administrators"),
])
def test_each_line_of_the_check_play(tmp_path, case, line):
    cfg = host_config(tmp_path)
    tasks = after_window(tmp_path)
    new = "$global:Hives['S-1-5-21-1-2-3-1002'].found[0]"
    change = {
        "done": "", "token": "$global:Token = $false",
        "present runs": "$global:Processes = @{ 'S-1-5-21-1-2-3-1001' = @([pscustomobject]@{ ProcessId = 1 }) }",
        "present task": f"$global:Tasks['{PRESENT_TASKS[0]}'].Enabled = $true", "other task": "$global:Other = @('x')",
        "copy before boot": f"$global:Tasks['{NEW_START[0]}'].Last = '2026-11-20T02:59:00Z'",
        "copy failed": f"$global:Tasks['{NEW_START[1]}'].State = 'Ready'; $global:Tasks['{NEW_START[1]}'].Result = 1",
        "other path": f"{new}.BasePath = 'C:\\Users\\standin1\\wsl'", "root": f"{new}.DefaultUid = 0",
        "flags": f"{new}.Flags = 7", "environment": f"{new}.DefaultEnvironment = @('LANG=C.UTF-8')",
        "proxy": "$global:Proxy += '0.0.0.0 1883 172.20.9.9 1883'", "rules": "$global:Broken = @('other-entry')",
        "admin": "$global:Admins = @('S-1-5-21-1-2-3-1002')", "no restart": "", "went back": "", "wslconfig": "",
    }[case]
    if case == "no restart":
        (tmp_path / "window.state").write_text("2026-11-20T03:30:00Z window-done\n")
    if case == "went back":
        log = (tmp_path / "window.log").read_text().replace("forward result: done", "forward result: went-back")
        (tmp_path / "window.log").write_text(log)
    if case == "wslconfig":
        (tmp_path / "prof\\.wslconfig").write_text("[wsl2]\nmemory=8GB\nprocessors=8\n")
    body = HOST_FAKES + CHECK.replace("PROFILE", str(tmp_path / "prof")) + change + "\nGet-TnwksCheck | ConvertTo-Json -Compress -Depth 4"
    lines = run(cfg, body, tasks)
    assert len(lines) == 15 and fails(lines) == ([line] if line else [])


@needs_pwsh
@pytest.mark.parametrize(("mode", "changed"), [("status", False), ("guards", False), ("start", True)])
def test_only_the_start_reports_a_change(tmp_path, mode, changed):
    """As win_powershell runs it: $Ansible in the scope of the caller, and the mode of a run that does nothing."""
    encoded = base64.b64encode(json.dumps(config(tmp_path)).encode()).decode()
    result = pwsh(f"""$Ansible = [pscustomobject]@{{ Changed = $true }}
$env:TNWKS_MOVE_WINDOW_LIBRARY = '1'
& '{SCRIPT}' -Mode {mode} -Config ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')))
@{{ changed = $Ansible.Changed }} | ConvertTo-Json -Compress""")
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"changed": changed}
