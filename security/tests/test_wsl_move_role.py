"""The role of the real move of the distro (windows_wsl_move, Design Doc 0004, section 9, step 3).

It runs the procedure of the rehearsal of the move, byte for byte, with the config of the
real move. These tests read the role: its config, the rules of what it writes only once,
the refresh of the copies while no window is started, and that no name of an account
reaches a log.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import ansible_render
import pytest
import yaml
from test_wsl_start_unelevated import needs_pwsh, pwsh

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLE = ANSIBLE / "roles/windows_wsl_move"
REHEARSAL = ANSIBLE / "roles/windows_wsl_move_rehearsal"
DEFAULTS = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
VARS = yaml.safe_load((ROLE / "vars/main.yml").read_text())
SECRET = ("wsl_move_present_account", "wsl_move_new_account", "wsl_move_present_folder", "wsl_move_rehearsed_wsl_version")


def tasks(name: str) -> list[dict]:
    """The tasks of a file of the role, with each block flattened."""
    out = []

    def walk(items):
        for task in items or []:
            out.append(task)
            for key in ("block", "rescue", "always"):
                walk(task.get(key))

    walk(yaml.safe_load((ROLE / "tasks" / name).read_text()))
    return out


ALL = [task for name in sorted(p.name for p in (ROLE / "tasks").glob("*.yml")) for task in tasks(name)]


def test_the_real_config_has_no_boot_request_and_reads_the_vms():
    """The boot request and vm_check off are the rehearsal's only (Test-TnwksScope)."""
    window = VARS["wsl_move_window_config"]
    assert "boot_request_file" not in window
    assert not any("boot_request" in p.read_text() for p in ROLE.rglob("*") if p.is_file())
    assert window["vm_check"] is True
    assert window["boot"] == {"group_steps": True, "rehearsal_group_test": False}
    assert window["disk_rule"] == {"owner": "new-account", "new_mask": "FA"}
    assert window["session_check"] == "{{ wsl_move_session_check | bool }}"
    assert DEFAULTS["wsl_move_session_check"] is False
    assert window["restart"] == "{{ wsl_move_restart | bool }}" and DEFAULTS["wsl_move_restart"] is True


def test_the_scope_is_the_volume_of_the_disk_file_and_the_two_accounts():
    assert VARS["wsl_move_root"] == "{{ (wsl_move_disk_dir | string)[:3] }}"
    window = VARS["wsl_move_window_config"]
    assert window["root"] == "{{ wsl_move_root }}"
    assert window["task_pattern"] == "^tnwks-"
    assert window["account_pattern"].strip() == (
        "^({{ wsl_move_present_account | regex_escape }}|{{ wsl_move_new_account | regex_escape }})$")
    common = VARS["wsl_move_account_config_common"]
    assert common["name_pattern"] == "^{{ wsl_move_distro | regex_escape }}$"
    assert common["refused_names"] == [] and common["root"] == "{{ wsl_move_root }}"
    # The play stops before any change when the new folder is not on the volume of today.
    asserts = [t for t in ALL if t.get("name") == "The folder of the new disk file is on the volume of the disk file today"]
    that = asserts[0]["ansible.builtin.assert"]["that"]
    assert "(wsl_move_present_folder | lower).startswith(wsl_move_root | lower)" in that
    assert DEFAULTS["wsl_move_disk_dir"] == "{{ wsl_move_dir }}\\disk"
    assert DEFAULTS["wsl_move_dir"] == "C:\\ProgramData\\tnwks-wsl-move"


def test_no_name_of_an_account_is_a_default_or_in_a_public_file():
    for name in SECRET:
        assert name not in DEFAULTS, name
    inventory = ANSIBLE / "inventory"
    for path in [*inventory.glob("host_vars/**/*.yml"), *inventory.glob("group_vars/**/*.yml"),
                 *inventory.glob("group_vars/*.yml")]:
        if path.name.startswith("secrets"):
            continue
        data = yaml.safe_load(path.read_text()) or {}
        assert not set(data) & set(SECRET), path


def test_each_task_that_takes_a_name_of_an_account_or_its_folder_logs_nothing():
    names = re.compile(r"wsl_move_(present_account|new_account|present_folder|accounts|registration|wslconfig"
                       r"|wslconfig_present|as_present|runs|today_read)\b")
    for task in ALL:
        if "block" in task or "ansible.builtin.include_tasks" in task:
            continue
        body = {k: v for k, v in task.items() if k not in ("name", "when", "loop_control")}
        if names.search(str(body)):
            assert task.get("no_log") is True, task.get("name")


def test_the_scripts_are_the_rehearsals_and_each_copy_is_checked():
    assert VARS["wsl_move_scripts_src"] == "{{ role_path }}/../windows_wsl_move_rehearsal/files"
    for name in VARS["wsl_move_scripts"]:
        assert (REHEARSAL / "files" / name).is_file(), name
    hashes = VARS["wsl_move_file_hashes"]
    assert "wsl_move_scripts_src ~ '/' ~ name" in hashes and "hash('sha256')" in hashes
    assert "wsl_move_configs.items()" in hashes


def test_the_copies_change_only_while_no_window_is_started():
    host = yaml.safe_load((ROLE / "tasks/host_files.yml").read_text())
    block = host[0]
    assert block["when"] == "not wsl_move_window_started | bool"
    changing = block["block"]
    assert {next(k for k in t if k not in ("name", "vars", "loop", "loop_control", "when", "no_log")) for t in changing} == {
        "ansible.windows.win_copy", "ansible.windows.win_powershell", "tnwks_win_scheduled_task"}
    # Outside the block, the play only reads.
    for task in host[1:]:
        module = next(k for k in task if k not in ("name", "vars", "when", "check_mode", "register", "no_log"))
        assert module in ("ansible.windows.win_powershell", "ansible.builtin.assert", "ansible.builtin.set_fact"), task["name"]
        if module == "ansible.windows.win_powershell":
            assert task["check_mode"] is False and "$Ansible.Changed = $false" in task[module]["script"]
    started = next(t for t in ALL if t.get("name") == "Whether a window is started")
    assert started["ansible.builtin.set_fact"]["wsl_move_window_started"] == (
        "{{ (wsl_move_marker_read.output | last | default('')) not in ['', 'wayback-done'] }}")
    final = host[-1]
    assert final["when"] == "wsl_move_window_started | bool or not ansible_check_mode"


def test_the_present_account_registers_its_own_tasks_and_no_password_is_reset():
    host = tasks("host_files.yml")
    present = next(t for t in host if t.get("name") == "Tasks of the present account, while Ansible logs in as it")
    module = present["tnwks_win_scheduled_task"]
    assert "s4u_user" not in module and "ansible_become" not in present.get("vars", {})
    assert present["when"] == "wsl_move_as_present | bool"
    new = next(t for t in host if t.get("name") == "Tasks of the new account")
    assert new["tnwks_win_scheduled_task"]["s4u_user"] == "{{ wsl_move_new_account }}"
    assert new["vars"]["ansible_become_user"] == "SYSTEM"
    assert DEFAULTS["wsl_move_account_steps"] == {"present": ["stop", "unregister", "import"],
                                                 "new": ["probe", "stop", "unregister", "import"]}
    assert len(DEFAULTS["wsl_move_new_description"]) <= 48


def test_what_step_3_writes_once_is_written_only_when_absent():
    """A host where the procedure ran before keeps its marker, state, log, version and reads."""
    scripts = {t["name"]: t["ansible.windows.win_powershell"]["script"] for t in ALL if "ansible.windows.win_powershell" in t}
    marker = scripts["The marker file in the folder of the new disk file, only when absent"]
    assert "if (Test-Path -LiteralPath $Path) { return }" in marker
    state = scripts["The state file, the log and the saved version of WSL, only when absent"]
    assert state.count("if (-not (Test-Path -LiteralPath") == 2
    today = scripts["Read the registration, the VM address and .wslconfig of the present account, only when absent"]
    assert "if ($have) { return }" in today
    assert today.count("if (-not (Test-Path -LiteralPath $") == 2


def test_the_probe_passes_only_for_a_new_run_with_a_result():
    run = tasks("run_task.yml")[0]
    when = run["failed_when"]
    assert "(wsl_move_run_out.output | last).ran" in when
    assert "(wsl_move_run_out.output | last).result is not none" in when
    runner = (REHEARSAL / "files/Invoke-TnwksStartTask.ps1").read_text()
    assert "if ([int64]$doc.time -ge $start) { $read = $doc }" in runner


def test_the_present_tasks_are_tasks_of_the_roles():
    text = "\n".join(p.read_text() for p in (ANSIBLE / "roles").glob("*/defaults/main.yml"))
    text += "\n".join(p.read_text() for p in (ANSIBLE / "inventory/host_vars").glob("*/main.yml"))
    for name in DEFAULTS["wsl_move_present_tasks"]:
        assert name in text and name.startswith("tnwks-"), name
    rehearsal = yaml.safe_load((REHEARSAL / "defaults/main.yml").read_text())
    assert DEFAULTS["wsl_move_vm_capability_sid"] == rehearsal["wsl_move_rehearsal_vm_capability_sid"]
    assert DEFAULTS["wsl_move_service_binary"] == rehearsal["wsl_move_rehearsal_service_binary"]


# The template of each task that the procedure may disable, in its role.
TASK_TEMPLATES = {
    "tnwks-lan-bridge-ip": "windows_lan_bridge/templates/lan-bridge-ip.xml.j2",
    "tnwks-udp-relay": "windows_udp_relay/templates/udp-relay.xml.j2",
    "tnwks-udp-relay-syslog": "windows_udp_relay/templates/udp-relay.xml.j2",
    "tnwks-vhdx-compact": "windows_wsl_maintenance/templates/vhdx-compact.xml.j2",
    "tnwks-wsl-keepalive": "windows_wsl_maintenance/templates/wsl-keepalive.xml.j2",
    "tnwks-wsl-watchdog": "windows_wsl_maintenance/templates/wsl-watchdog.xml.j2",
}


def test_the_way_back_starts_only_the_long_running_tasks_of_the_present_account():
    """The compaction and each periodic task are disabled by the window, and never started by the way back."""
    disable, start = DEFAULTS["wsl_move_present_tasks"], DEFAULTS["wsl_move_present_start_tasks"]
    assert VARS["wsl_move_window_config"]["present"]["disable_tasks"] == "{{ wsl_move_present_tasks }}"
    assert VARS["wsl_move_window_config"]["present"]["start_tasks"] == "{{ wsl_move_present_start_tasks }}"
    assert set(start) <= set(disable) and len(start) == len(set(start)) > 0
    for name in disable:
        xml = (ANSIBLE / "roles" / TASK_TEMPLATES[name]).read_text()
        long_running = "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml and "<Repetition>" not in xml
        assert (name in start) == long_running, name
    assert "tnwks-vhdx-compact" in disable and "tnwks-vhdx-compact" not in start
    assert {"tnwks-wsl-watchdog", "tnwks-lan-bridge-ip"} <= set(disable) - set(start)


def test_the_role_is_off_until_a_host_turns_it_on():
    group = yaml.safe_load((ANSIBLE / "inventory/group_vars/windows_hosts.yml").read_text())
    assert group["wsl_move"] is False
    play = yaml.safe_load((ANSIBLE / "playbooks/windows-host.yml").read_text())
    roles = [r for p in play for r in p.get("roles", []) if isinstance(r, dict)]
    entry = next(r for r in roles if r.get("role") == "windows_wsl_move")
    assert entry["when"] == "wsl_move | bool"
    # Before the WSL roles, which register the start tasks of the account that it makes.
    names = [r.get("role") for r in roles]
    for role in ("windows_udp_relay", "windows_lan_bridge", "windows_usb_attach", "windows_wsl_maintenance"):
        assert names.index("windows_wsl_move") < names.index(role), role


def test_a_script_that_changes_something_takes_what_if():
    for task in ALL:
        script = task.get("ansible.windows.win_powershell", {}).get("script", "")
        if re.search(r"Set-Acl|New-Item|WriteAll|New-LocalUser|Add-LocalGroupMember", script):
            assert "SupportsShouldProcess" in script, task["name"]


@needs_pwsh
@pytest.mark.parametrize("task", [t for t in ALL if "ansible.windows.win_powershell" in t], ids=lambda t: t["name"])
def test_the_powershell_of_the_role_parses(task):
    script = task["ansible.windows.win_powershell"]["script"]
    encoded = base64.b64encode(script.encode()).decode()
    result = pwsh("$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "'))\n"
                  "$errors = $null\n"
                  "[void][System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$null, [ref]$errors)\n"
                  "[Console]::Out.Write($errors.Count)")
    assert result.stdout.strip() == "0", task["name"]


@pytest.mark.parametrize(("path", "ok"), [
    ("C:\\ProgramData\\tnwks-wsl-move\\disk", True),
    ("C:\\Program Files\\WSL\\wslservice.exe", True),
    ("C:\\Users\\standin1\\AppData\\Local\\wsl\\{0a1b}", True),
    ("C:/ProgramData/tnwks-wsl-move", False),
    ("\\\\?\\C:\\ProgramData\\tnwks-wsl-move", False),
    ("C:\\Users\\STANDI~1\\AppData", False),
    ("C:\\ProgramData\\..\\x", False),
    ("C:\\ProgramData\\.\\x", False),
    ("C:\\ProgramData\\x\\", False),
    ("C:\\ProgramData\\\\x", False),
    ("C:\\ProgramData\\x.", False),
    ("C:\\ProgramData\\x ", False),
    ("C:", False),
    ("ProgramData\\x", False),
])
def test_the_paths_are_in_the_long_normalized_form(path, ok):
    """The open compares the final path with the config's: each path is long and normalized."""
    assert bool(re.match(VARS["wsl_move_path_form"], path)) is ok
    for name in ("wsl_move_dir", "wsl_move_disk_dir", "wsl_move_present_folder", "wsl_move_service_binary"):
        assert "{{ " + name + " }}" in VARS["wsl_move_paths"]
    that = next(t for t in ALL if t.get("name") == "Each path of the procedure is in the long, normalized form")
    assert that["ansible.builtin.assert"]["that"] == "wsl_move_paths | reject('match', wsl_move_path_form) | list | length == 0"


def test_only_the_tasks_that_act_for_the_new_account_run_as_system():
    """The registration of its tasks (its password is reset) and the run of its probe.

    And the reads of the window play (window_read.yml), which read the state of both accounts as SYSTEM.
    """
    becoming = sorted(t["name"] for t in ALL if (t.get("vars") or {}).get("ansible_become_user") == "SYSTEM")
    assert becoming == ["Run the task {{ wsl_move_run.key }}-{{ wsl_move_run.step }}", "Tasks of the new account",
                        "The window play, {{ wsl_move_window_read_mode }}"]
    for task in ALL:
        assert "become" not in task and "become_user" not in task, task.get("name")


def test_each_apply_stops_before_any_write_on_a_wsl_that_is_not_the_rehearsals():
    """Step 3 of the design: the version of WSL is the version of the rehearsal of the move."""
    first = tasks("main.yml")[0]["ansible.builtin.assert"]["that"]
    assert "wsl_move_rehearsed_wsl_version | default('') | length > 0" in first
    stop = "throw 'The installed WSL is not the version of the rehearsal of the move. Nothing is changed.'"
    changing = ("tnwks_win_dir_acl", "ansible.windows.win_file", "ansible.windows.win_copy", "tnwks_win_scheduled_task",
                "ansible.windows.win_service", "ansible.windows.win_user_right", "ansible.windows.win_regedit",
                "ansible.builtin.include_tasks")
    main = tasks("main.yml")
    names = [t.get("name") for t in main]
    check = main[names.index("The installed WSL is the version of the rehearsal of the move")]
    module = check["ansible.windows.win_powershell"]
    assert module["parameters"]["Rehearsed"] == "{{ wsl_move_rehearsed_wsl_version }}"
    assert stop in module["script"] and check["check_mode"] is False
    # Before each task that changes something, and the first task that reads the host.
    first_change = min(i for i, t in enumerate(main) if any(k in t for k in changing)
                       or "SupportsShouldProcess" in str(t.get("ansible.windows.win_powershell", {}).get("script", "")))
    first_read = min(i for i, t in enumerate(main) if "ansible.windows.win_powershell" in t)
    assert names.index("The installed WSL is the version of the rehearsal of the move") == first_read < first_change
    # The task that saves the version compares first, and writes nothing on another one.
    save = main[names.index("The state file, the log and the saved version of WSL, only when absent")]["ansible.windows.win_powershell"]
    assert save["parameters"]["Rehearsed"] == "{{ wsl_move_rehearsed_wsl_version }}"
    script = save["script"]
    assert script.index(stop) < script.index("WriteAllText") and script.count("WriteAllText") == 2
    assert "[IO.File]::WriteAllText($Version, $installed)" in script


@needs_pwsh
def test_the_version_check_stops_on_another_version_and_writes_nothing(tmp_path):
    main = tasks("main.yml")
    save = next(t for t in main if t.get("name") == "The state file, the log and the saved version of WSL, only when absent")
    script = save["ansible.windows.win_powershell"]["script"]
    encoded = base64.b64encode(script.encode()).decode()
    out = {}
    for name, rehearsed in (("same", "2.0.0.0"), ("other", "2.0.1.0")):
        folder = tmp_path / name
        folder.mkdir()
        body = f"""$Ansible = [pscustomobject]@{{ Changed = $false }}
$binary = '{folder}/wslservice.exe'
[IO.File]::WriteAllText($binary, 'x')
$code = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))
$code = $code.Replace('[Diagnostics.FileVersionInfo]::GetVersionInfo($Binary).FileVersion', "'2.0.0.0'")
$block = [scriptblock]::Create($code)
try {{ & $block -State '{folder}/s' -Log '{folder}/l' -Version '{folder}/v' -Binary $binary -Rehearsed '{rehearsed}'; 'ran' }}
catch {{ $_.Exception.Message }}
"""
        result = pwsh(body)
        out[name] = (result.stdout.strip().splitlines()[-1], sorted(p.name for p in folder.iterdir()))
    assert out["same"] == ("ran", ["l", "s", "v", "wslservice.exe"])
    assert out["other"] == ("The installed WSL is not the version of the rehearsal of the move. Nothing is changed.",
                            ["wslservice.exe"])
    assert (tmp_path / "same/v").read_text() == "2.0.0.0"


def test_site_2_turns_the_move_on_with_the_copies_that_its_roles_register():
    host = yaml.safe_load((ANSIBLE / "inventory/host_vars/WSL-02/main.yml").read_text())
    assert host["wsl_move"] is True and host["wsl_start_unelevated"] is True
    # The secrets are in the encrypted file only, never in the public one.
    assert not set(SECRET) & set(host)
    group = yaml.safe_load((ANSIBLE / "inventory/group_vars/windows_hosts.yml").read_text())
    assert group["wsl_move_task_suffix"] == "-account"
    roles = ANSIBLE / "roles"
    maintenance = yaml.safe_load((roles / "windows_wsl_maintenance/defaults/main.yml").read_text())
    bridge = yaml.safe_load((roles / "windows_lan_bridge/defaults/main.yml").read_text())
    # The copies that the WSL roles register on this host (test_wsl_move_start_tasks.py).
    copies = [maintenance["wsl_watchdog_task"], bridge["lan_bridge_ip_task"]]
    copies += [maintenance["wsl_keepalive_task"]] if host["wsl_keepalive_enabled"] else []
    copies += [relay["task"] for relay in host["udp_relays"]]
    # The list is derived in the group variables from the same values, with the names of the roles' defaults.
    assert "wsl_move_new_start_tasks" not in host
    derived = " ".join(group["wsl_move_new_start_tasks"].split())
    assert derived == ("{{ (['tnwks-wsl-watchdog', 'tnwks-lan-bridge-ip'] + (['tnwks-wsl-keepalive'] "
                       "if wsl_keepalive_enabled | default(false) | bool else []) + (udp_relays | default([]) "
                       "| map(attribute='task') | list)) | map('regex_replace', '$', wsl_move_task_suffix) | list }}")
    assert [maintenance["wsl_watchdog_task"], bridge["lan_bridge_ip_task"], maintenance["wsl_keepalive_task"]] == [
        "tnwks-wsl-watchdog", "tnwks-lan-bridge-ip", "tnwks-wsl-keepalive"]
    # The rule of the role's assert holds for this host.
    present = [t for t in DEFAULTS["wsl_move_present_tasks"] if t != "tnwks-vhdx-compact"]
    assert sorted(copies) == sorted(present)
    assert maintenance["vhdx_compact_task"] == "tnwks-vhdx-compact"
    check = next(t for t in tasks("main.yml")
                 if t.get("name") == "The start tasks of the new account are the copies of the tasks of the present account")
    assert "reject('equalto', 'tnwks-vhdx-compact')" in check["ansible.builtin.assert"]["that"]


GUARD = "An apply as another account than the present one comes after step 3 ran as the present account"


def test_an_apply_as_the_administrator_stops_before_any_write_until_step_3_ran_as_the_present_account():
    """Step 4 (the SSH user is the administrator account) can not apply before step 3 kept the reads of today."""
    main = tasks("main.yml")
    names = [t.get("name") for t in main]
    guard = main[names.index(GUARD)]
    module = guard["ansible.windows.win_powershell"]
    assert guard["when"] == "not wsl_move_as_present | bool" and guard["check_mode"] is False
    assert module["parameters"] == {"Registration": "{{ wsl_move_registration_file }}",
                                    "Wslconfig": "{{ wsl_move_wslconfig_file }}",
                                    "Names": "{{ wsl_move_window_tasks.present.values() | list }}"}
    assert "SupportsShouldProcess" not in module["script"] and "$Ansible.Changed = $false" in module["script"]
    changing = ("tnwks_win_dir_acl", "ansible.windows.win_file", "ansible.windows.win_copy", "tnwks_win_scheduled_task",
                "ansible.windows.win_service", "ansible.builtin.include_tasks")
    first_change = min(i for i, t in enumerate(main) if any(k in t for k in changing)
                       or "SupportsShouldProcess" in str(t.get("ansible.windows.win_powershell", {}).get("script", "")))
    assert names.index("Ansible logs in as the present account, or its window tasks are there") < names.index(GUARD)
    assert names.index(GUARD) < first_change


@needs_pwsh
def test_the_guard_of_step_4_needs_the_kept_reads_and_the_window_tasks(tmp_path):
    script = next(t for t in tasks("main.yml") if t.get("name") == GUARD)["ansible.windows.win_powershell"]["script"]
    encoded = base64.b64encode(script.encode()).decode()
    out = {}
    for case, files, registered in (("kept", ["r", "w"], 3), ("no reads", [], 3), ("one read", ["r"], 3),
                                    ("no tasks", ["r", "w"], 2)):
        folder = tmp_path / case.replace(" ", "-")
        folder.mkdir()
        for name in files:
            (folder / name).write_text("{}")
        body = f"""$Ansible = [pscustomobject]@{{ Changed = $false }}
$registered = @('tnwks-wsl-move-present-stop', 'tnwks-wsl-move-present-unregister', 'tnwks-wsl-move-present-import')[0..({registered} - 1)]
function Get-ScheduledTask([string]$TaskName, $ErrorAction) {{ if ($registered -contains $TaskName) {{ [pscustomobject]@{{ TaskName = $TaskName }} }} }}
$code = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))
try {{
  & ([scriptblock]::Create($code)) -Registration '{folder}/r' -Wslconfig '{folder}/w' -Names @('tnwks-wsl-move-present-stop', 'tnwks-wsl-move-present-unregister', 'tnwks-wsl-move-present-import')
  'passed'
}} catch {{ $_.Exception.Message }}
"""
        out[case] = pwsh(body).stdout.strip().splitlines()[-1]
    assert out["kept"] == "passed"
    stop = ("Ansible does not log in as the present account, and step 3 has not run as that account: the reads of "
            "today or the window tasks of the present account are not there. Nothing is changed.")
    assert out["no reads"] == out["one read"] == out["no tasks"] == stop


def test_the_account_and_the_disk_file_follow_the_phase_before_any_change():
    """The done change: the owner of the distro and the file of the compaction and the host check."""
    names = [t.get("name") for t in tasks("main.yml")]
    gate = "The account that owns the distro and the disk file follow the phase of the move"
    assert names.index(gate) < names.index("Folder of the procedure")
    that = [" ".join(line.split()) for line in next(t for t in tasks("main.yml") if t.get("name") == gate)[
        "ansible.builtin.assert"]["that"]]
    desktop = ("(windows_desktop_user | default('') | lower) == ((wsl_move_new_account if wsl_move_phase == 'done' "
               "else wsl_move_present_account) | lower)")
    folder = ("(vhdx_compact_vhdx | default('') | ansible.builtin.win_dirname | lower) == ((wsl_move_disk_dir "
              "if wsl_move_phase == 'done' else wsl_move_present_folder) | lower)")
    name = "(vhdx_compact_vhdx | default('') | ansible.builtin.win_basename | lower) == (wsl_move_disk_name | lower)"
    assert that == [desktop, folder, name]


def test_on_site_2_the_administrator_logs_in_and_the_distro_keeps_its_owner_and_user():
    """Step 4 (plan task 10.14): the inventory of site 2."""
    host = yaml.safe_load((ANSIBLE / "inventory/host_vars/WSL-02/main.yml").read_text())
    distro = yaml.safe_load((ANSIBLE / "inventory/host_vars/WSL-02-ubuntu/main.yml").read_text())
    # The account that owns the distro follows the phase of the move, from the move's own values.
    assert " ".join(host["windows_desktop_user"].split()) == (
        "{{ wsl_move_new_account if wsl_move_phase == 'done' else wsl_move_present_account }}")
    # The user of the distro is the user of today, and the jump logs in to the distro as that user.
    assert distro["wsl_distro_user"] == "{{ hostvars['WSL-02'].wsl_move_present_account }}"
    # The jump takes the same value: a value of the distro host that takes hostvars can not be read through hostvars.
    assert "{{ wsl_move_present_account }}@{{ hostvars['WSL-02-ubuntu'].wsl_distro_ssh_host }}" in (
        host["ansible_ssh_common_args"])
    assert "wsl_distro_user" not in host["ansible_ssh_common_args"]
    # The disk file is the moved file from the done switch, and before it the file in the folder of the
    # present account, not of the SSH user. The folder of the move is the role's own.
    assert host["wsl_move_disk_dir"] == DEFAULTS["wsl_move_disk_dir"].replace("{{ wsl_move_dir }}", DEFAULTS["wsl_move_dir"])
    assert host["vhdx_compact_vhdx"] == (
        "{{ wsl_move_disk_dir if wsl_move_phase == 'done' else wsl_move_present_folder }}\\" + DEFAULTS["wsl_move_disk_name"])
    # No public value of site 2 takes the owner or the user of the distro from the SSH user.
    for path in ("inventory/host_vars/WSL-02/main.yml", "inventory/host_vars/WSL-02-ubuntu/main.yml"):
        code = [line for line in (ANSIBLE / path).read_text().splitlines() if not line.lstrip().startswith("#")]
        assert not any("ansible_user" in line for line in code), path


@needs_pwsh  # In the job of the pwsh tests, which runs ansible-playbook too.
@ansible_render.no_ansible
def test_the_values_of_site_2_in_each_phase_render_as_windows_paths(tmp_path):
    """Ansible renders the disk file of the compaction and the host check, the account that owns the
    distro and the gate of the host check of site 2, with stand-ins for its encrypted values. Each path has
    one backslash between its names: a doubled one would make the standing check report a missing file."""
    site2 = ansible_render.load(
        "roles/windows_wsl_move/defaults/main.yml", "roles/windows_host_check/defaults/main.yml",
        "inventory/group_vars/windows_hosts.yml", "inventory/host_vars/WSL-02/main.yml")
    standins = {"wsl_move_present_account": "standin1", "wsl_move_new_account": "standin-wsl",
                "wsl_move_present_folder": "C:\\Users\\standin1\\AppData\\Local\\wsl\\{0a1b}",
                "wsl_move_rehearsed_wsl_version": "3.0.5.0", "ansible_user": "standin-admin"}
    distro = {"wsl_distro_ssh_host": "192.0.2.10", "ansible_host": "192.0.2.10", "ansible_port": 22,
              "ansible_ssh_private_key_file": "/dev/null"}
    hosts = {"WSL-02": {**site2, **standins}, "WSL-02-move": {**site2, **standins, "wsl_move_done": False},
             "WSL-02-ubuntu": distro}
    names = ("vhdx_compact_vhdx", "host_check_wsl_disk", "host_check_wsl_disk_account", "windows_desktop_user",
             "wsl_move_phase")
    out = ansible_render.render(tmp_path, hosts, ["WSL-02", "WSL-02-move"],
                                {**{name: name for name in names}, "rules": "host_check_wsl_disk_rules | bool"})
    assert out["WSL-02"] == {
        "vhdx_compact_vhdx": "C:\\ProgramData\\tnwks-wsl-move\\disk\\ext4.vhdx",
        "host_check_wsl_disk": "C:\\ProgramData\\tnwks-wsl-move\\disk\\ext4.vhdx",
        "host_check_wsl_disk_account": "standin-wsl", "windows_desktop_user": "standin-wsl",
        "wsl_move_phase": "done", "rules": True}
    assert out["WSL-02-move"] == {
        "vhdx_compact_vhdx": "C:\\Users\\standin1\\AppData\\Local\\wsl\\{0a1b}\\ext4.vhdx",
        "host_check_wsl_disk": "C:\\Users\\standin1\\AppData\\Local\\wsl\\{0a1b}\\ext4.vhdx",
        "host_check_wsl_disk_account": "standin1", "windows_desktop_user": "standin1",
        "wsl_move_phase": "move", "rules": False}


def test_the_done_switch_stops_the_host_before_the_wsl_roles_until_a_window_ended_done():
    """Merged before a done window, the done switch would turn each WSL role to the tasks of the new account.
    The guard runs after the read of the marker and before the first change, with each tag, and the role runs
    before the WSL roles in a play with no rescue: its failure ends the play for the host."""
    main = tasks("main.yml")
    names = [t.get("name") for t in main]
    guard = main[names.index("The done switch comes after a done window")]
    assert guard["ansible.builtin.assert"]["that"] == "(wsl_move_marker_read.output | last | default('')) == 'window-done'"
    assert guard["when"] == "wsl_move_phase == 'done'" and guard["tags"] == ["always"]
    read = main[names.index("Read the last marker of the state file")]
    assert read["check_mode"] is False and read["tags"] == ["always"]
    assert names.index("Whether a window is started") < names.index("The done switch comes after a done window") < (
        names.index("Folder of the procedure"))
    play = next(p for p in yaml.safe_load((ANSIBLE / "playbooks/windows-host.yml").read_text())
                if p["hosts"] == "windows_hosts")
    assert not any(key in play for key in ("ignore_errors", "max_fail_percentage", "ignore_unreachable"))
    roles = [role["role"] for role in play["roles"]]
    for wsl_role in ("windows_udp_relay", "windows_lan_bridge", "windows_wsl_maintenance", "windows_host_check",
                     "windows_host_settings"):
        assert roles.index("windows_wsl_move") < roles.index(wsl_role), wsl_role
    for role in play["roles"]:
        assert "ignore_errors" not in role, role["role"]


def test_only_windows_host_settings_writes_the_wslconfig_of_the_new_account_once_done():
    """The role writes the .wslconfig of the new account in its own form while the move runs; from the done
    switch, windows_host_settings writes and checks it from its template, at the profile of the new account."""
    write = next(t for t in ALL if t.get("name") == ".wslconfig of the new account")
    assert write["when"] == "wsl_move_phase != 'done'"
    settings = yaml.safe_load((ANSIBLE / "roles/windows_host_settings/defaults/main.yml").read_text())
    assert settings["wslconfig_path"] == "C:\\Users\\{{ windows_desktop_user }}\\.wslconfig"
    site2 = yaml.safe_load((ANSIBLE / "inventory/host_vars/WSL-02/main.yml").read_text())
    assert site2["wslconfig_write"] is True
