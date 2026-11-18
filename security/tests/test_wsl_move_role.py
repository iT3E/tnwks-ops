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
    """The registration of its tasks (its password is reset) and the run of its probe."""
    becoming = sorted(t["name"] for t in ALL if (t.get("vars") or {}).get("ansible_become_user") == "SYSTEM")
    assert becoming == ["Run the task {{ wsl_move_run.key }}-{{ wsl_move_run.step }}", "Tasks of the new account"]
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
