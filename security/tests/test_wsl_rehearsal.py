"""The rehearsal of the account change (role windows_wsl_rehearsal, Design Doc 0004,
section 9, step 0).

A temporary account with no admin group owns a distro of its own and starts it from
its own S4U tasks with no elevation. These tests read what the role grants and to
whom, render each task of the account and compare it with the form that
Export-ScheduledTask gives back, and run the write test and the script of the steps
with fakes.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from test_wsl_start_unelevated import RUN_LEVEL, as_exported, canonical, needs_pwsh, pwsh

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLE = ANSIBLE / "roles/windows_wsl_rehearsal"
INVENTORY = ANSIBLE / "inventory"
DEFAULTS = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
TASKS = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
TEMPLATE = (ROLE / "templates/rehearsal-task.xml.j2").read_text()
SCRIPT = ROLE / "files/Invoke-TnwksRehearsal.ps1"
WRITE_TEST = ROLE / "files/write-test.sh"
STEPS = DEFAULTS["wsl_rehearsal_steps"] + ["start", "remove"]


def present() -> list[dict]:
    """The tasks of the state present, with the tasks of each inner block."""
    block = next(task for task in TASKS if task.get("when") == "wsl_rehearsal_state == 'present'")
    out = []
    for task in block["block"]:
        out.extend(task.get("block", [task]))
    return out


def task_named(name: str) -> dict:
    return next(task for task in present() if task["name"] == name)


VALUES = {
    "wsl_rehearsal_account": "S-1-5-21-7-7-7-1002",
    "wsl_rehearsal_distro": DEFAULTS["wsl_rehearsal_distro"],
    "wsl_rehearsal_dir": DEFAULTS["wsl_rehearsal_dir"],
    "wsl_rehearsal_log_dir": "C:\\ProgramData\\tnwks-rehearsal\\log",
    "wsl_rehearsal_write_test_dir": DEFAULTS["wsl_rehearsal_write_test_dir"],
    "wsl_rehearsal_disk": "C:\\ProgramData\\tnwks-rehearsal\\disk\\mount-test.vhdx",
}


def render(item: str) -> str:
    """The template for one task: the if, elif and else of `item`, then each value."""
    def choose(match: re.Match) -> str:
        parts = re.split(r"\{% (?:(elif|else)(?: item == '(\w+)')?) %\}", match[2])
        branches = [(match[1], parts[0])]
        for i in range(1, len(parts), 3):
            branches.append((parts[i + 1] if parts[i] == "elif" else None, parts[i + 2]))
        for want, body in branches:
            if want is None or want == item:
                return body
        return ""
    # The innermost if first: one that holds no other if.
    inner = re.compile(r"\{% if item == '(\w+)' %\}((?:(?!\{% if ).)*?)\{% endif %\}", re.DOTALL)
    text = TEMPLATE
    while inner.search(text):
        text = inner.sub(choose, text)
    assert "{%" not in text, "a form of Jinja that the test does not read"
    text = text.replace("{{ item }}", item)
    text = text.replace("{{ wsl_rehearsal_rootfs.url | basename | e }}", DEFAULTS["wsl_rehearsal_rootfs"]["url"].rsplit("/", 1)[1])
    text = text.replace("{{ wsl_rehearsal_rootfs.sha256 | upper }}", DEFAULTS["wsl_rehearsal_rootfs"]["sha256"].upper())
    for name, value in VALUES.items():
        text = text.replace(f"{{{{ {name} | e }}}}", value)
    assert "{{" not in text, text
    return text


def test_each_task_of_the_account_runs_as_it_by_s4u_with_no_elevation_and_no_trigger():
    for step in STEPS:
        text = render(step)
        principal = re.search(r"<Principal id=\"Author\">(.*?)</Principal>", text, re.DOTALL)[1]
        assert "<UserId>S-1-5-21-7-7-7-1002</UserId>" in principal and "<LogonType>S4U</LogonType>" in principal
        assert "<RunLevel>LeastPrivilege</RunLevel>" in principal, step
        assert "<Triggers />" in text and "Trigger>" not in text, step
    assert "/bin/sleep infinity" in render("start") and "<ExecutionTimeLimit>PT0S<" in render("start")
    assert "-Step import" in render("import") and DEFAULTS["wsl_rehearsal_rootfs"]["sha256"].upper() in render("import")
    assert "-Step write" in render("write") and "-WriteTestDir" in render("write")
    assert "-Step mount" in render("mount") and "mount-test.vhdx" in render("mount")
    assert "-Step remove" in render("remove") and "-RootFs" not in render("remove")


@needs_pwsh
def test_each_task_of_the_account_reads_as_unchanged_once_registered():
    """The lesson of the module tnwks_win_scheduled_task: the template and the form that
    Export-ScheduledTask gives back (with its URI, no RunLevel for LeastPrivilege, and
    an empty Triggers element) have the same canonical form."""
    tasks = [render(step) for step in STEPS]
    exported = [as_exported(task, f"tnwks-rehearsal-{step}") for task, step in zip(tasks, STEPS)]
    assert all(not RUN_LEVEL.search(text) and "<Triggers />" in text for text in exported)
    out = canonical(tasks + exported)
    assert out[:len(tasks)] == out[len(tasks):]


def test_the_account_has_no_admin_group_and_a_password_that_nothing_keeps():
    account = task_named("The temporary account")
    assert account["no_log"] is True
    script = account["ansible.windows.win_powershell"]["script"]
    assert "RandomNumberGenerator" in script and "New-LocalUser" in script
    assert "S-1-5-32-545" in script and "is a member of Administrators" in script
    assert "Add-LocalGroupMember -SID 'S-1-5-32-544'" not in script
    assert set(account["ansible.windows.win_powershell"]["parameters"]) == {"Name", "Description"}, "no password goes in"
    text = (ROLE / "tasks/main.yml").read_text() + (ROLE / "defaults/main.yml").read_text()
    assert not re.search(r"password\s*:", text, re.IGNORECASE)


def test_the_name_and_the_description_of_the_account_fit_new_localuser():
    r"""New-LocalUser of Windows PowerShell 5.1 takes a name of 1 to 20 characters,
    without " / \ [ ] : ; | = , + * ? < > @, and a description of 0 to 48. A check run
    never calls it, so only these tests see its limits."""
    name, description = DEFAULTS["wsl_rehearsal_account"], DEFAULTS["wsl_rehearsal_account_description"]
    assert 1 <= len(name) <= 20 and not set(name) & set('"/\\[]:;|=,+*?<>@') and name.strip(". ")
    assert len(description) <= 48
    parameters = task_named("The temporary account")["ansible.windows.win_powershell"]["parameters"]
    assert parameters == {"Name": "{{ wsl_rehearsal_account }}", "Description": "{{ wsl_rehearsal_account_description }}"}


# A fake of each cmdlet of local accounts that the task calls. New-LocalUser has the
# parameter sets and the limits of Windows PowerShell 5.1 (its documentation: a name
# of 1 to 20 characters, a description of 0 to 48, a password of 127 at most).
ACCOUNT_FAKES = r"""
$Ansible = [pscustomobject]@{ Changed = $null }
$script:Made = $null
$script:Users = @()
function Get-LocalUser { param($Name, $ErrorAction) $script:Made }
function New-LocalUser {
    [CmdletBinding(DefaultParameterSetName = 'Password')]
    param(
        [Parameter(Mandatory, Position = 0)][ValidateLength(1, 20)][string]$Name,
        [Parameter(Mandatory, ParameterSetName = 'Password')][SecureString]$Password,
        [Parameter(ParameterSetName = 'Password')][switch]$PasswordNeverExpires,
        [Parameter(Mandatory, ParameterSetName = 'NoPassword')][switch]$NoPassword,
        [DateTime]$AccountExpires, [switch]$AccountNeverExpires, [switch]$Disabled, [string]$FullName,
        [switch]$UserMayNotChangePassword, [ValidateLength(0, 48)][string]$Description)
    $plain = [Net.NetworkCredential]::new('', $Password).Password
    if ($plain.Length -gt 127) { throw "a password of $($plain.Length) characters" }
    $script:Made = [pscustomobject]@{ Name = $Name; Description = $Description; Length = $plain.Length
                                      SID = [pscustomobject]@{ Value = 'S-1-5-21-7-7-7-1002' } }
    $script:Made
}
function Get-LocalGroupMember { param($SID) if ($SID -eq 'S-1-5-32-545') { $script:Users } }
function Add-LocalGroupMember { param($SID, $Member) $script:Users += $Member }
"""


def run_account_task(description: str) -> str:
    task = task_named("The temporary account")["ansible.windows.win_powershell"]
    body = ACCOUNT_FAKES + "$f = {\n" + task["script"] + "\n}\n" + (
        f"try {{ & $f -Name '{DEFAULTS['wsl_rehearsal_account']}' -Description '{description}' | Out-Null\n"
        "  'made {0} {1} {2} users {3}' -f $script:Made.Name, $script:Made.Description.Length, $script:Made.Length, "
        "@($script:Users).Count }\n"
        "catch { 'failed ' + $_.Exception.Message }")
    run = pwsh(body)
    assert run.returncode == 0, run.stderr
    return run.stdout.strip().splitlines()[-1]


@needs_pwsh
def test_the_account_task_calls_new_localuser_within_its_limits():
    """The path of a real apply, with the fakes: the account is made with the
    description of the defaults and a password of 68 characters, and joins Users."""
    description = DEFAULTS["wsl_rehearsal_account_description"]
    assert run_account_task(description) == f"made tnwks-rehearsal {len(description)} 68 users 1"
    assert run_account_task("x" * 49).startswith("failed "), "the fake holds the limit of the description"


def test_the_account_gets_the_batch_right_and_the_rights_of_its_folders():
    right = task_named("The temporary account may log on as a batch job")
    assert right["ansible.windows.win_user_right"] == {"name": "SeBatchLogonRight", "users": ["{{ wsl_rehearsal_sid }}"], "action": "add"}
    rights = [task for task in present() if "ansible.windows.win_user_right" in task]
    assert len(rights) == 2, "the batch right, and the three logons it may not use"
    folder = task_named("Folder of the rehearsal")["tnwks_win_dir_acl"]
    assert folder.get("writer", "") == ""
    writers = task_named("Log and disk folders of the rehearsal")
    assert writers["tnwks_win_dir_acl"]["writer"] == "{{ wsl_rehearsal_account }}"
    assert writers["loop"] == ["{{ wsl_rehearsal_log_dir }}", "{{ wsl_rehearsal_disk_dir }}"]
    for name in ("wsl_rehearsal_log_dir", "wsl_rehearsal_disk_dir"):
        assert DEFAULTS[name].startswith("{{ wsl_rehearsal_dir }}\\")
    assert DEFAULTS["wsl_rehearsal_disk"].startswith("{{ wsl_rehearsal_disk_dir }}\\")


def test_the_account_may_not_log_on_in_another_way_and_has_no_tile():
    deny = task_named("The temporary account may not log on at the keyboard, by Remote Desktop or from the network")
    assert deny["ansible.windows.win_user_right"] == {"name": "{{ item }}", "users": ["{{ wsl_rehearsal_sid }}"], "action": "add"}
    assert deny["loop"] == "{{ wsl_rehearsal_deny_rights }}"
    assert DEFAULTS["wsl_rehearsal_deny_rights"] == [
        "SeDenyInteractiveLogonRight", "SeDenyRemoteInteractiveLogonRight", "SeDenyNetworkLogonRight"]
    assert "SeDenyBatchLogonRight" not in DEFAULTS["wsl_rehearsal_deny_rights"], "S4U is a batch logon"
    tile = task_named("The temporary account is not on the sign-in screen")["ansible.windows.win_regedit"]
    assert tile == {"path": "{{ wsl_rehearsal_userlist_key }}", "name": "{{ wsl_rehearsal_account }}", "data": 0, "type": "dword"}
    assert DEFAULTS["wsl_rehearsal_userlist_key"] == "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon\\SpecialAccounts\\UserList"


def test_a_check_run_before_the_account_exists_skips_what_names_it():
    """A check run on a host with no account yet: each task that names the account (its
    SID, the writer of its folders, the principal of its tasks) waits for the apply."""
    block = next(task for task in TASKS if task.get("when") == "wsl_rehearsal_state == 'present'")
    assert block["vars"]["wsl_rehearsal_sid"] == "{{ wsl_rehearsal_user.output | default([]) | last | default('') }}"
    read = task_named("Read the temporary account")
    assert read["check_mode"] is False and read["register"] == "wsl_rehearsal_user"
    assert "$Ansible.Changed = $false" in read["ansible.windows.win_powershell"]["script"]
    names = [task["name"] for task in present()]
    assert names.index("The temporary account") < names.index("Read the temporary account") < names.index(
        "The temporary account may log on as a batch job")
    gated = {"The temporary account may log on as a batch job",
             "The temporary account may not log on at the keyboard, by Remote Desktop or from the network",
             "Log and disk folders of the rehearsal", "Tasks of the temporary account"}
    for task in present():
        text = yaml.safe_dump({k: v for k, v in task.items() if k != "name"})
        names_it = "wsl_rehearsal_sid }}" in text or "writer" in text or task["name"] == "Tasks of the temporary account"
        if names_it:
            assert task["name"] in gated, task["name"]
    for name in gated:
        assert task_named(name)["when"] == "wsl_rehearsal_sid | length > 0", name
    note = task_named("A check run before the account exists")
    assert note["when"] == "wsl_rehearsal_sid | length == 0" and "does not exist yet" in note["ansible.builtin.debug"]["msg"]


def test_the_play_stops_when_the_import_did_not_end_and_says_changed_only_for_an_import():
    assert task_named("The probe makes the profile of the account")["changed_when"] is False
    assert task_named("The import of the distro")["changed_when"] is False
    names = [task["name"] for task in present()]
    assert names.index("The import of the distro") + 1 == names.index("The distro is imported")
    assert names.index("The distro is imported") < names.index("The start of the distro, with nobody signed in")
    script = task_named("The distro is imported")["ansible.windows.win_powershell"]["script"]
    assert "$import.exit -ne 0" in script and "-notcontains $Distro" in script and "throw" in script
    assert "$Ansible.Changed = -not $import.existed" in script


def test_the_wslconfig_has_a_memory_limit_and_no_key_for_networking():
    content = task_named(".wslconfig of the account")["ansible.windows.win_copy"]["content"]
    keys = dict(line.split("=", 1) for line in content.splitlines() if "=" in line and not line.startswith("#"))
    assert keys == {"memory": "{{ wsl_rehearsal_memory }}", "processors": "1", "swap": "0"}
    assert re.fullmatch(r"\d+GB", DEFAULTS["wsl_rehearsal_memory"])
    assert not re.search(r"network", content.split("[wsl2]", 1)[1], re.IGNORECASE)


def test_the_root_file_system_is_pinned_by_its_digest():
    rootfs = DEFAULTS["wsl_rehearsal_rootfs"]
    assert re.fullmatch(r"https://dl-cdn\.alpinelinux\.org/alpine/v\d+\.\d+/releases/x86_64/alpine-minirootfs-[\d.]+-x86_64\.tar\.gz", rootfs["url"])
    assert re.fullmatch(r"[0-9a-f]{64}", rootfs["sha256"])
    controller = yaml.safe_load((ROLE / "tasks/controller.yml").read_text())
    download = next(task for task in controller if "ansible.builtin.get_url" in task)["ansible.builtin.get_url"]
    assert download["checksum"] == "sha256:{{ wsl_rehearsal_rootfs.sha256 }}"
    stat_task = task_named("The root file system has its pinned digest on the host")
    assert stat_task["ansible.windows.win_stat"]["checksum_algorithm"] == "sha256"
    assert "wsl_rehearsal_rootfs.sha256" in stat_task["failed_when"]


def test_the_rehearsal_runs_on_the_site_2_host_only():
    group = yaml.safe_load((INVENTORY / "group_vars/windows_hosts.yml").read_text())
    assert group["wsl_rehearsal"] is False
    on = {path.parent.name for path in INVENTORY.glob("host_vars/*/main.yml")
          if (yaml.safe_load(path.read_text()) or {}).get("wsl_rehearsal")}
    assert on == {"WSL-02"}
    play = yaml.safe_load((ANSIBLE / "playbooks/windows-host.yml").read_text())
    hosts = next(p for p in play if p["hosts"] == "windows_hosts")
    role = next(r for r in hosts["roles"] if r["role"] == "windows_wsl_rehearsal")
    assert role["when"] == "wsl_rehearsal | bool"
    assert hosts["roles"][-1] is role, "after every other role, so a failed step stops no other role"


def test_the_apply_runs_no_task_in_a_check_run_and_writes_only_in_its_folder_and_profile():
    block = next(task for task in TASKS if task.get("when") == "wsl_rehearsal_state == 'present'")
    runs = next(task for task in block["block"] if task.get("name") == "Run the tasks of the account")
    assert runs["when"] == "not ansible_check_mode"
    for task in present():
        args = next(iter(v for k, v in task.items() if k.startswith(("ansible.", "tnwks_"))), {})
        for key in ("dest", "path"):
            if isinstance(args, dict) and key in args:
                if "ansible.windows.win_regedit" in task:
                    continue  # a registry key, not a file
                assert args[key].startswith(("{{ wsl_rehearsal_dir", "{{ wsl_rehearsal_log_dir", "{{ wsl_rehearsal_disk",
                                             "{{ wsl_rehearsal_profile.output", "{{ item }}",
                                             "{{ wsl_rehearsal_write_test_dir }}\\tnwks-rehearsal-write-test.tmp")), (task["name"], args[key])


# --- the write test, in a shell of this runner -------------------------------------------


def run_write_test(directory: Path) -> str:
    return subprocess.run(["sh", str(WRITE_TEST), str(directory)], check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.skipif(os.geteuid() == 0, reason="root passes the mode of a directory")
def test_the_write_test_names_a_refusal_by_its_errno(tmp_path):
    closed = tmp_path / "closed"
    closed.mkdir()
    closed.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert run_write_test(closed).startswith("refused EACCES ")
    finally:
        closed.chmod(stat.S_IRWXU)


def test_the_write_test_says_written_and_leaves_no_file(tmp_path):
    assert run_write_test(tmp_path) == "written"
    assert list(tmp_path.iterdir()) == []


def test_the_write_test_makes_no_file_over_one_that_is_there(tmp_path):
    left = tmp_path / "tnwks-rehearsal-write-test.tmp"
    left.write_text("x")
    assert run_write_test(tmp_path).startswith("other EEXIST ")
    assert left.read_text() == "x"


def test_the_play_removes_the_one_file_that_the_write_test_tries():
    name = re.search(r'file="\$dir/([^"]+)"', WRITE_TEST.read_text())[1]
    task = task_named("No file of the write test")["ansible.windows.win_file"]
    assert task == {"path": "{{ wsl_rehearsal_write_test_dir }}\\" + name, "state": "absent"}


def test_the_write_test_fails_on_a_directory_that_is_not_there(tmp_path):
    assert run_write_test(tmp_path / "missing").startswith("other NOT_LISTED ")


# --- the script of the steps, with fakes of wsl.exe and whoami.exe -------------------------


def run_step(tmp_path: Path, step: str, wsl: str, whoami: str = "", extra: str = "") -> dict:
    system = tmp_path / "System32"
    system.mkdir(exist_ok=True)
    for name, body in (("wsl.exe", wsl), ("whoami.exe", whoami)):
        path = system / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    log = tmp_path / "log"
    log.mkdir(exist_ok=True)
    body = f"""
$env:SystemRoot = '{tmp_path}'
$env:LOCALAPPDATA = '{tmp_path}/local'
& '{SCRIPT}' -Step {step} -LogDir '{log}' -Distro tnwks-rehearsal {extra}
"""
    run = pwsh(body)
    assert run.returncode == 0, run.stderr
    return json.loads((log / f"{step}.json").read_text())


@needs_pwsh
def test_the_probe_reads_the_level_and_the_administrators_group(tmp_path):
    user = 'if [ "$1" = /user ]; then printf \'"User Name","SID"\\n"host\\\\tnwks-rehearsal","S-1-5-21-7-7-7-1002"\\n\'; exit 0; fi; '
    medium = user + 'printf \'"Group Name","Type","SID","Attributes"\\n"Everyone","Well-known group","S-1-1-0","Enabled"\\n"Mandatory Label\\\\Medium Mandatory Level","Label","S-1-16-8192",""\\n\''
    out = run_step(tmp_path, "probe", wsl="exit 0", whoami=medium)
    assert out["medium"] is True and out["administrators"] == [] and out["step"] == "probe"
    assert out["user"] == "S-1-5-21-7-7-7-1002" and out["hive"] is False and out["distros"] == []
    high = medium.replace("S-1-16-8192", "S-1-16-12288").replace('"Everyone","Well-known group","S-1-1-0","Enabled"',
                                                                  '"BUILTIN\\\\Administrators","Alias","S-1-5-32-544","Enabled"')
    out = run_step(tmp_path, "probe", wsl="exit 0", whoami=high)
    assert out["medium"] is False and out["administrators"] == ["Enabled"]


@needs_pwsh
def test_the_import_checks_the_digest_and_skips_a_distro_that_is_there(tmp_path):
    rootfs = tmp_path / "rootfs.tar.gz"
    rootfs.write_bytes(b"not the pinned file")
    calls = tmp_path / "calls"
    wsl = f'echo "$@" >> {calls}; exit 0'
    out = run_step(tmp_path, "import", wsl=wsl, extra=f"-RootFs '{rootfs}' -Sha256 {'0' * 64}")
    assert out["exit"] == -1 and "not the pinned one" in out["output"]
    assert "--import" not in calls.read_text()
    there = 'case "$1" in --list) echo tnwks-rehearsal ;; esac; exit 0'
    out = run_step(tmp_path, "import", wsl=there, extra=f"-RootFs '{rootfs}' -Sha256 {'0' * 64}")
    assert out["exit"] == 0 and out["existed"] is True


@needs_pwsh
def test_the_write_step_runs_the_test_as_root_on_the_drvfs_path(tmp_path):
    calls = tmp_path / "calls"
    out = run_step(tmp_path, "write", wsl=f'echo "$@" > {calls}; echo "refused EACCES sh: denied"',
                   extra="-WriteTestDir 'C:\\Program Files\\windows_exporter\\textfile_inputs'")
    args = calls.read_text().strip()
    assert args.startswith("-d tnwks-rehearsal -u root -e /bin/sh ") and args.endswith("/write-test.sh /mnt/c/Program Files/windows_exporter/textfile_inputs")
    assert out["output"] == "refused EACCES sh: denied" and out["directory"] == "/mnt/c/Program Files/windows_exporter/textfile_inputs"


@needs_pwsh
def test_the_mount_step_mounts_bare_and_unmounts(tmp_path):
    calls = tmp_path / "calls"
    out = run_step(tmp_path, "mount", wsl=f'echo "$@" >> {calls}; exit 0', extra="-Disk 'C:\\x\\mount-test.vhdx'")
    assert calls.read_text().splitlines() == ["--mount --vhd C:\\x\\mount-test.vhdx --bare", "--unmount C:\\x\\mount-test.vhdx"]
    assert out["mount_exit"] == 0 and out["unmount_exit"] == 0


@needs_pwsh
def test_a_warning_of_wsl_on_stderr_stays_text_in_the_result(tmp_path):
    wsl = 'echo "wsl: a warning on stderr" >&2; echo "done"; exit 0'
    out = run_step(tmp_path, "mount", wsl=wsl, extra="-Disk 'C:\\x\\mount-test.vhdx'")
    # The two streams of the fake reach the pipe in either order.
    assert out["mount_exit"] == 0 and sorted(out["mount_output"].splitlines()) == ["done", "wsl: a warning on stderr"]


def test_each_stderr_redirect_runs_under_continue():
    """Under Stop, Windows PowerShell 5.1 stops at the first line that a program
    writes to stderr when it is redirected; PowerShell 7 does not, so the test of the
    step above can not see it. Each 2>&1 of the role runs where the preference is
    Continue: in Invoke-Wsl, or in its own script block."""
    script = SCRIPT.read_text()
    function = script[script.index("function Invoke-Wsl"):script.index("function Get-Distros")]
    assert "$ErrorActionPreference = 'Continue'" in function and function.count("2>&1") == 1
    assert script.count("2>&1") == 1
    for path in (ROLE / "tasks/main.yml", ANSIBLE / "playbooks/windows-rehearsal-check.yml"):
        lines = [line for line in path.read_text().splitlines() if "2>&1" in line]
        assert lines and all(re.search(r"& \{ \$ErrorActionPreference = 'Continue'; [^}]*2>&1", line) for line in lines), path.name


def test_the_import_waits_for_the_hive_and_the_profile_of_the_account():
    names = [task["name"] for task in present()]
    guard = names.index("The task of the account ran in its own profile")
    assert names.index("The probe makes the profile of the account") < guard < names.index(".wslconfig of the account")
    assert guard < names.index("The import of the distro")
    script = task_named("The task of the account ran in its own profile")["ansible.windows.win_powershell"]["script"]
    assert "$probe.hive" in script and "Users" in script


# --- the removal (task 10.6) -----------------------------------------------------------


def absent() -> list[dict]:
    """The tasks of the state absent, with the tasks of each inner block, at any depth."""
    def flat(tasks: list[dict]) -> list[dict]:
        out = []
        for task in tasks:
            out.extend(flat(task["block"]) if "block" in task else [task])
        return out
    block = next(task for task in TASKS if task.get("when") == "wsl_rehearsal_state == 'absent'")
    return flat(block["block"])


def test_the_state_is_absent_and_the_controller_downloads_nothing():
    assert DEFAULTS["wsl_rehearsal_state"] == "absent"
    assert DEFAULTS["wsl_rehearsal_tasks"] == "{{ wsl_rehearsal_steps + ['start', 'remove'] }}"
    controller = yaml.safe_load((ROLE / "tasks/controller.yml").read_text())
    assert all(task["when"] == "wsl_rehearsal_state == 'present'" for task in controller)


def test_the_removal_stops_the_vm_as_the_account_before_the_profile_goes():
    names = [task["name"] for task in absent()]
    order = ["The start task stops", "Script of the removal", "The task of the removal", "The removal runs as the account",
             "No task of the account", "The account may no longer log on as a batch job", "The deny rights of the account go",
             "No value for the account on the sign-in screen", "No profile of the account",
             "No temporary account", "No folder of the rehearsal", "No file of the write test"]
    assert [name for name in names if name in order] == order
    block = next(task for task in TASKS if task.get("when") == "wsl_rehearsal_state == 'absent'")
    stop = next(task for task in block["block"] if task["name"] == "The distro and the VM stop, as the account")
    assert "not ansible_check_mode" in stop["when"]
    remove = next(task for task in absent() if task["name"] == "The task of the removal")
    assert remove["vars"] == {"item": "remove"}
    run = next(task for task in absent() if task["name"] == "The removal runs as the account")["ansible.windows.win_powershell"]
    assert "Start-ScheduledTask -TaskName $Task" in run["script"] and "shutdown_exit" in run["script"]


LAST_OUTPUT = re.compile(r"\{\{ (\w+)\.output \| default\(\[\]\) \| last \| default\(''\) \}\}")
LENGTH = re.compile(r"(\w+) \| length > 0")


def stop_block_runs(user_output: list, profile_output: list, check_mode: bool = False) -> bool:
    """Whether "The distro and the VM stop, as the account" runs, by its `when` and the
    vars of the absent block, from what the two reads gave. The test knows the two
    forms that the block uses, as Jinja reads them, and fails on any other."""
    block = next(task for task in TASKS if task.get("when") == "wsl_rehearsal_state == 'absent'")
    outputs = {"wsl_rehearsal_user": user_output, "wsl_rehearsal_profile_read": profile_output}
    values = {}
    for name, expression in block["vars"].items():
        m = LAST_OUTPUT.fullmatch(expression)
        assert m, expression
        # `last` of an empty list is undefined, and `default('')` makes it ''.
        values[name] = outputs[m[1]][-1] if outputs[m[1]] else ""
    stop = next(task for task in block["block"] if task["name"] == "The distro and the VM stop, as the account")
    results = []
    for condition in stop["when"]:
        if condition == "not ansible_check_mode":
            results.append(not check_mode)
        else:
            m = LENGTH.fullmatch(condition)
            assert m, condition
            results.append(len(values[m[1]]) > 0)
    return all(results)


def test_with_no_profile_the_task_of_the_removal_is_neither_registered_nor_run():
    """No profile: no task of the account ever ran, so it has no hive, no distro and no
    VM. The removal then goes on with the tasks, rights, tile, account and folder,
    and registers no task of the account."""
    sid = "S-1-5-21-7-7-7-1002"
    assert stop_block_runs([sid], ["C:\\Users\\tnwks-rehearsal"]) is True
    assert stop_block_runs([sid], [""]) is False
    assert stop_block_runs([sid], []) is False
    assert stop_block_runs([""], [""]) is False, "no account"
    assert stop_block_runs([sid], ["C:\\Users\\tnwks-rehearsal"], check_mode=True) is False
    block = next(task for task in TASKS if task.get("when") == "wsl_rehearsal_state == 'absent'")
    stop = next(task for task in block["block"] if task["name"] == "The distro and the VM stop, as the account")
    assert {"The task of the removal", "The removal runs as the account"} <= {task["name"] for task in stop["block"]}
    read = next(task for task in absent() if task["name"] == "Read the profile of the account")
    assert read["check_mode"] is False and read["register"] == "wsl_rehearsal_profile_read"
    assert "Win32_UserProfile -Filter \"SID = '$Sid'\"" in read["ansible.windows.win_powershell"]["script"]
    names = [task["name"] for task in absent()]
    assert names.index("Read the temporary account") < names.index("Read the profile of the account") < names.index(
        "The start task stops")


def test_no_task_of_the_play_runs_wsl_exe():
    """The administrator session never starts or stops WSL: each wsl.exe is in a task of
    the account (Invoke-TnwksRehearsal.ps1, or the start task)."""
    for task in present() + absent():
        script = task.get("ansible.windows.win_powershell", {}).get("script", "")
        assert "wsl" not in script.lower().replace("wsl_rehearsal", ""), task["name"]
    check = yaml.safe_load((ANSIBLE / "playbooks/windows-rehearsal-check.yml").read_text())[0]["tasks"]
    for task in check:
        script = task.get("ansible.windows.win_powershell", {}).get("script", "")
        # It names wsl.exe only to find its processes, and never runs it.
        assert not re.search(r"(^|[;{(]\s*)(&|Start-Process)[^\n]*wsl", script, re.IGNORECASE | re.MULTILINE), task["name"]
        assert "--shutdown" not in script and "--terminate" not in script, task["name"]


def test_the_removal_takes_away_each_part_that_the_rehearsal_made():
    tasks = next(task for task in absent() if task["name"] == "No task of the account")
    assert tasks["tnwks_win_scheduled_task"]["state"] == "absent" and tasks["loop"] == "{{ wsl_rehearsal_tasks }}"
    rights = [task for task in absent() if "ansible.windows.win_user_right" in task]
    assert [r["ansible.windows.win_user_right"]["name"] for r in rights] == ["SeBatchLogonRight", "{{ item }}"]
    assert rights[1]["loop"] == "{{ wsl_rehearsal_deny_rights }}"
    for r in rights:
        assert r["ansible.windows.win_user_right"]["action"] == "remove"
        assert r["ansible.windows.win_user_right"]["users"] == ["{{ wsl_rehearsal_sid }}"]
        assert r["when"] == "wsl_rehearsal_sid | length > 0"
    tile = next(task for task in absent() if "ansible.windows.win_regedit" in task)["ansible.windows.win_regedit"]
    assert tile == {"path": "{{ wsl_rehearsal_userlist_key }}", "name": "{{ wsl_rehearsal_account }}", "state": "absent"}
    block = next(task for task in TASKS if task.get("when") == "wsl_rehearsal_state == 'absent'")
    assert block["vars"]["wsl_rehearsal_sid"] == "{{ wsl_rehearsal_user.output | default([]) | last | default('') }}"
    read = next(task for task in absent() if task["name"] == "Read the temporary account")
    assert read["check_mode"] is False and read["register"] == "wsl_rehearsal_user"
    profile = next(task for task in absent() if task["name"] == "No profile of the account")["ansible.windows.win_powershell"]
    assert "Remove-CimInstance" in profile["script"] and "$profile.Loaded" in profile["script"]
    account = next(task for task in absent() if task["name"] == "No temporary account")["ansible.windows.win_powershell"]
    assert "Remove-LocalUser" in account["script"]
    files = [task["ansible.windows.win_file"] for task in absent() if "ansible.windows.win_file" in task]
    assert {"path": "{{ wsl_rehearsal_dir }}", "state": "absent"} in files
    assert any(f["path"].endswith("tnwks-rehearsal-write-test.tmp") and f["state"] == "absent" for f in files)


def test_the_exit_check_reads_the_accounts_and_the_batch_right():
    play = yaml.safe_load((ANSIBLE / "playbooks/windows-rehearsal-check.yml").read_text())
    tasks = play[0]["tasks"]
    checks = next(t for t in tasks if t["name"] == "Run the checks")
    exit_check = next(t for t in tasks if t["name"] == "The exit check of the removal")
    assert checks["when"] == "wsl_rehearsal_state == 'present'" and exit_check["when"] == "wsl_rehearsal_state == 'absent'"
    script = exit_check["ansible.windows.win_powershell"]["script"]
    for name in ("account", "rights", "tasks", "profile", "folder", "tile"):
        assert f"Add-Check '{name}'" in script
    params = exit_check["ansible.windows.win_powershell"]["parameters"]
    assert params["Rights"] == "{{ ['SeBatchLogonRight'] + wsl_rehearsal_deny_rights }}"
    assert params["UserList"] == "{{ wsl_rehearsal_userlist_key }}"
    assert "secedit.exe /export /areas USER_RIGHTS" in script and "S-1-5-32-544" in script and "Start-ScheduledTask" not in script


EXIT_FAKES = r"""
$Ansible = [pscustomobject]@{ Changed = $null }
function Get-LocalUser { @([pscustomobject]@{ Name = 'admin1'; Enabled = $true }) }
function Get-LocalGroupMember { param($SID) @([pscustomobject]@{ Name = 'host\admin1'; SID = [pscustomobject]@{ Value = 'S-1-5-21-7-7-7-1001' } }) }
function Get-ItemProperty {
    param($LiteralPath, $Name, $ErrorAction)
    if ($LiteralPath -like '*\ProfileList') { [pscustomobject]@{ ProfilesDirectory = $env:FAKE_USERS } }
}
function Get-ScheduledTask { param($TaskName, $ErrorAction) @() }
function Get-CimInstance { param([Parameter(Position = 0)]$ClassName) @() }
# secedit.exe: writes the policy that $env:FAKE_POLICY holds, and exits with $env:FAKE_EXIT.
function secedit.exe {
    $cfg = $args[[array]::IndexOf($args, '/cfg') + 1]
    if ($env:FAKE_POLICY) { Set-Content -LiteralPath $cfg -Value $env:FAKE_POLICY }
    Write-Error 'a line on stderr'
    $global:LASTEXITCODE = [int]$env:FAKE_EXIT
}
"""


def run_exit_check(tmp_path: Path, policy: str, code: int = 0, removed: str = "S-1-5-21-7-7-7-1002",
                   check: str = "rights") -> str:
    users = tmp_path / "Users"
    users.mkdir(exist_ok=True)
    play = yaml.safe_load((ANSIBLE / "playbooks/windows-rehearsal-check.yml").read_text())
    task = next(t for t in play[0]["tasks"] if t["name"] == "The exit check of the removal")
    body = EXIT_FAKES + f"""
$env:TEMP = '{tmp_path}'
$env:FAKE_POLICY = @'
{policy}
'@
$env:FAKE_EXIT = '{code}'
$env:FAKE_USERS = '{users}'
$f = {{
{task["ansible.windows.win_powershell"]["script"]}
}}
try {{ $r = & $f -Account tnwks-rehearsal -RemovedSid '{removed}' -Prefix tnwks-rehearsal -Dir '{tmp_path / "gone"}' -Rights @('SeBatchLogonRight', 'SeDenyNetworkLogonRight') -UserList 'x'
  '{check} {{0}} {{1}}' -f $r.{check}.pass, $r.{check}.value }}
catch {{ 'failed ' + $_.Exception.Message }}
"""
    run = pwsh(body)
    assert run.returncode == 0, run.stderr
    return run.stdout.strip().splitlines()[-1]


@needs_pwsh
def test_the_exit_check_reads_the_rights_from_secedit_and_stops_when_it_fails(tmp_path):
    """The read of secedit runs under Continue, so a line on stderr stays text, and an
    exit code that is not 0 stops the check. A holder that is the account fails it."""
    clean = "[Privilege Rights]\nSeBatchLogonRight = Administrators,Backup Operators\nSeDenyNetworkLogonRight = Guest"
    assert run_exit_check(tmp_path, clean) == (
        "rights True SeBatchLogonRight: Administrators, Backup Operators; SeDenyNetworkLogonRight: Guest")
    left = clean.replace("= Guest", "= Guest,host\\tnwks-rehearsal")
    assert run_exit_check(tmp_path, left).startswith("rights False ")
    assert run_exit_check(tmp_path, clean, code=1).startswith("failed secedit did not export the rights (1)")
    assert run_exit_check(tmp_path, "", code=0).startswith("failed secedit did not export the rights (0)")


@needs_pwsh
def test_the_rights_check_fails_only_on_the_sid_of_the_account_and_notes_another(tmp_path):
    """The SID of the removed account fails the check, also when it names no account.
    A SID of another origin that names no account is shown as a note and passes."""
    clean = "[Privilege Rights]\nSeBatchLogonRight = Administrators\nSeDenyNetworkLogonRight = Guest"
    ours = clean.replace("= Guest", "= Guest,*S-1-5-21-7-7-7-1002")
    out = run_exit_check(tmp_path, ours)
    assert out.startswith("rights False ") and "S-1-5-21-7-7-7-1002 (the account of the rehearsal)" in out
    stale = clean.replace("= Guest", "= Guest,*S-1-5-21-9-9-9-1500")
    out = run_exit_check(tmp_path, stale)
    assert out.startswith("rights True ") and "S-1-5-21-9-9-9-1500 (note: names no account, not the SID of the rehearsal)" in out


@needs_pwsh
def test_the_profile_check_finds_a_folder_of_the_account_with_no_profile_entry(tmp_path):
    """A profile folder without its registry entry would hold the disk of a distro."""
    clean = "[Privilege Rights]\nSeBatchLogonRight = Administrators"
    out = run_exit_check(tmp_path, clean, check="profile")
    assert out.startswith("profile True none (no profile, no folder ")
    (tmp_path / "Users" / "someone").mkdir()
    assert run_exit_check(tmp_path, clean, check="profile").startswith("profile True ")
    (tmp_path / "Users" / "tnwks-rehearsal.HOST").mkdir()
    out = run_exit_check(tmp_path, clean, check="profile")
    assert out.startswith("profile False ") and out.endswith("tnwks-rehearsal.HOST")


def test_a_remove_task_that_does_not_register_stops_the_removal_and_says_what_is_left():
    block = next(task for task in TASKS if task.get("when") == "wsl_rehearsal_state == 'absent'")
    stop = next(task for task in block["block"] if task["name"] == "The distro and the VM stop, as the account")
    guarded = next(task for task in stop["block"] if task["name"] == "The task of the removal")
    assert [task["name"] for task in guarded["block"]] == ["The task of the removal"]
    message = guarded["rescue"][0]["ansible.builtin.fail"]["msg"]
    for part in ("ansible_failed_result.msg", "The removal stops here", "the account", "its profile", "its tasks",
                 "the three deny rights", "tile", "wsl_rehearsal_dir", "waits for a run where the task registers"):
        assert part in message, part
    read = next(task for task in absent() if task["name"] == "Read the profile of the account")
    assert "$ErrorActionPreference = 'Stop'" in read["ansible.windows.win_powershell"]["script"]


def test_the_exit_check_needs_the_sid_of_the_removed_account():
    play = yaml.safe_load((ANSIBLE / "playbooks/windows-rehearsal-check.yml").read_text())
    tasks = play[0]["tasks"]
    names = [t["name"] for t in tasks]
    assert names.index("The SID of the removed account is given") < names.index("The exit check of the removal")
    given = tasks[names.index("The SID of the removed account is given")]
    assert given["when"] == "wsl_rehearsal_state == 'absent'"
    assert "wsl_rehearsal_removed_sid" in given["ansible.builtin.assert"]["that"]
    exit_check = tasks[names.index("The exit check of the removal")]["ansible.windows.win_powershell"]
    assert exit_check["parameters"]["RemovedSid"] == "{{ wsl_rehearsal_removed_sid }}"


@needs_pwsh
def test_the_remove_step_unregisters_the_distro_and_shuts_down_its_own_session(tmp_path):
    calls = tmp_path / "calls"
    state = tmp_path / "registered"
    state.write_text("tnwks-rehearsal")
    wsl = (f'echo "$@" >> {calls}; case "$1" in --list) cat {state} ;; --unregister) : > {state} ;; '
           f'--terminate) exit 1 ;; esac; exit 0')
    out = run_step(tmp_path, "remove", wsl=wsl)
    assert calls.read_text().splitlines() == ["--terminate tnwks-rehearsal", "--list --quiet", "--unregister tnwks-rehearsal",
                                              "--shutdown", "--list --quiet"]
    assert out["unregister_exit"] == 0 and out["shutdown_exit"] == 0 and out["distros"] == [] and out["terminate_exit"] == 1


# --- the logic of the check play, the gate of step 1 --------------------------------------

CHECK_PLAY = yaml.safe_load((ANSIBLE / "playbooks/windows-rehearsal-check.yml").read_text())[0]["tasks"]
CHECK_SCRIPT = next(t for t in CHECK_PLAY if t["name"] == "Run the checks")["ansible.windows.win_powershell"]["script"]
CHECK_LOGIC = CHECK_SCRIPT[CHECK_SCRIPT.index("# --- BEGIN logic"):CHECK_SCRIPT.index("# --- END logic")]
ACCOUNT_SID = "S-1-5-21-7-7-7-1002"
GOOD_PROBE = {"medium": True, "user": ACCOUNT_SID, "administrators": [], "hive": True,
              "profile": "C:\\Users\\tnwks-rehearsal", "integrity_sids": ["S-1-16-8192"], "distros": ["tnwks-rehearsal"]}
MEDIUM = f"user={ACCOUNT_SID} integrity=0x2000 elevation=3 admins=absent"


def check(call: str, data: object) -> dict:
    """Runs one Test-Tnwks* function of the check play on `data` (as JSON) and gives
    its @{ pass; value }."""
    body = CHECK_LOGIC + f"""
$data = @'
{json.dumps(data)}
'@ | ConvertFrom-Json
$out = {call}
[Console]::Out.Write((ConvertTo-Json -Compress @{{ pass = $out.pass; value = $out.value }}))
"""
    run = pwsh(body)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.splitlines()[-1])


def probe(**change) -> dict:
    return check("Test-TnwksProbe $data '" + ACCOUNT_SID + "' 'tnwks-rehearsal'", {**GOOD_PROBE, **change})


@needs_pwsh
def test_the_probe_check_takes_only_a_medium_token_of_the_account_with_no_administrators():
    assert probe()["pass"] is True
    assert probe(administrators=["Enabled"])["pass"] is False
    assert probe(administrators=["Group used for deny only"])["pass"] is False, "the token of the task itself has no such group"
    assert probe(medium=False, integrity_sids=["S-1-16-12288"])["pass"] is False
    assert probe(user="S-1-5-21-7-7-7-1001")["pass"] is False
    assert probe(hive=False)["pass"] is False
    assert probe(profile="C:\\Users\\Default")["pass"] is False
    assert probe(profile="C:\\Windows\\System32\\config\\systemprofile")["pass"] is False
    assert check("Test-TnwksProbe $null 'x' 'y'", None)["pass"] is False


@needs_pwsh
def test_the_import_check_needs_exit_0_and_the_distro_in_a_later_task():
    call = "Test-TnwksImport $data.import $data.probe 'tnwks-rehearsal'"
    assert check(call, {"import": {"exit": 0}, "probe": GOOD_PROBE})["pass"] is True
    assert check(call, {"import": {"exit": 1}, "probe": GOOD_PROBE})["pass"] is False
    assert check(call, {"import": {"exit": 0}, "probe": {**GOOD_PROBE, "distros": []}})["pass"] is False
    assert check(call, {"import": None, "probe": GOOD_PROBE})["pass"] is False


def row(name: str, token: str = MEDIUM, mine: bool = True, pid: int = 10) -> dict:
    return {"name": name, "id": pid, "mine": mine, "token": token}


@needs_pwsh
def test_the_token_check_counts_only_the_wsl_processes_of_the_account():
    call = "Test-TnwksTokens @($data)"
    good = [row("wsl"), row("wslhost", pid=11), row("wslrelay", pid=12)]
    assert check(call, good)["pass"] is True
    # A VM process, or a process of another user, is not counted, read or not.
    vm = row("vmmemWSL", "error open 5", mine=False, pid=20)
    other = row("wsl", "user=S-1-5-21-7-7-7-1001 integrity=0x3000 elevation=2 admins=enabled", mine=False, pid=21)
    out = check(call, [*good, vm, other])
    assert out["pass"] is True and "vmmemWSL 20 (not counted)" in out["value"]
    # Each of these fails.
    assert check(call, [*good, row("wslhost", "error open 5", pid=13)])["pass"] is False, "a process of the account not read"
    assert check(call, [row("wsl", MEDIUM.replace("0x2000", "0x3000"))])["pass"] is False, "high integrity"
    assert check(call, [row("wsl", MEDIUM.replace("0x2000", "0x1000"))])["pass"] is False, "low integrity"
    assert check(call, [row("wsl", MEDIUM.replace("admins=absent", "admins=enabled"))])["pass"] is False
    assert check(call, [row("wsl", MEDIUM.replace("admins=absent", "admins=deny-only"))])["pass"] is True
    assert check(call, [row("wslhost")])["pass"] is False, "no wsl.exe of the account"
    assert check(call, [])["pass"] is False


@needs_pwsh
def test_the_write_check_takes_only_eacces_or_eperm():
    call = "Test-TnwksWrite $data"
    for output, passes in [("refused EACCES sh: denied", True), ("refused EPERM sh: not permitted", True),
                           ("refused ENOENT sh: no such file", False), ("other ENOENT sh: no such file", False),
                           ("other EEXIST sh: file exists", False), ("other EIO sh: i/o error", False),
                           ("other UNKNOWN sh: x", False), ("written", False), ("other NOT_LISTED ls: x", False),
                           ("x refused EACCES", False), ("", False)]:
        assert check(call, {"exit": 0, "output": output})["pass"] is passes, output
    assert check("Test-TnwksWrite $null", None)["pass"] is False


@needs_pwsh
def test_the_mount_check_needs_both_exits_0():
    call = "Test-TnwksMount $data"
    assert check(call, {"mount_exit": 0, "unmount_exit": 0})["pass"] is True
    assert check(call, {"mount_exit": 1, "unmount_exit": 0})["pass"] is False
    assert check(call, {"mount_exit": 0, "unmount_exit": -1})["pass"] is False


# --- the profile gate of the apply ---------------------------------------------------------

RUN_TASK = task_named("The probe makes the profile of the account")["ansible.windows.win_powershell"]["script"]
GUARD = task_named("The task of the account ran in its own profile")["ansible.windows.win_powershell"]["script"]
TASK_FAKES = r"""
$Ansible = [pscustomobject]@{ Changed = $null }
$script:Runs = 0
function Get-ScheduledTaskInfo { param($TaskName) [pscustomobject]@{ LastRunTime = $script:Runs; LastTaskResult = 0 } }
function Start-ScheduledTask { param($TaskName) $script:Runs++ }
function Get-ScheduledTask { param($TaskName) [pscustomobject]@{ State = 'Ready' } }
function Start-Sleep { param($Seconds) }
function Get-LocalUser { param($Name) [pscustomobject]@{ SID = [pscustomobject]@{ Value = "SID-of-$Name" } } }
function Get-CimInstance {
    param([Parameter(Position = 0)]$ClassName, $Filter)
    $sid = [regex]::Match($Filter, "SID = '([^']+)'").Groups[1].Value
    @{ 'SID-of-tnwks-rehearsal' = 'C:\Users\tnwks-rehearsal'; 'SID-of-someone' = 'C:\Users\someone' }[$sid] |
        ForEach-Object { [pscustomobject]@{ LocalPath = $_ } }
}
"""


@needs_pwsh
def test_the_wslconfig_goes_to_the_profile_of_the_account_by_its_sid():
    body = TASK_FAKES + "$f = {\n" + RUN_TASK + "\n}\n" + "[Console]::Out.Write((& $f -Task t -Account 'tnwks-rehearsal'))"
    run = pwsh(body)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip().splitlines()[-1] == "C:\\Users\\tnwks-rehearsal"


def run_guard(tmp_path: Path, profile_arg: str, probe_doc: dict) -> str:
    path = tmp_path / "probe.json"
    path.write_text(json.dumps(probe_doc))
    body = TASK_FAKES + "$f = {\n" + GUARD + f"\n}}\ntry {{ & $f -Path '{path}' -Profile '{profile_arg}'; 'ok' }} catch {{ $_.Exception.Message }}"
    run = pwsh(body)
    assert run.returncode == 0, run.stderr
    return run.stdout.strip().splitlines()[-1]


@needs_pwsh
def test_the_profile_gate_takes_only_the_profile_that_the_task_of_the_account_saw(tmp_path):
    good = {"hive": True, "profile": "C:\\Users\\tnwks-rehearsal"}
    assert run_guard(tmp_path, "C:\\Users\\tnwks-rehearsal", good) == "ok"
    assert "not loaded" in run_guard(tmp_path, "C:\\Users\\tnwks-rehearsal", {**good, "hive": False})
    assert "not one folder under Users" in run_guard(tmp_path, "C:\\Users\\someone", good), "another profile"
    assert "not one folder under Users" in run_guard(tmp_path, "", good), "no profile"
    assert "not one folder under Users" in run_guard(tmp_path, "C:\\", {**good, "profile": "C:\\"})
    assert "not one folder under Users" in run_guard(tmp_path, "C:\\Users\\a\\b", {**good, "profile": "C:\\Users\\a\\b"})


# --- the registration of an S4U task for the account (tnwks_win_scheduled_task) ------------

TASK_MODULE = ANSIBLE / "playbooks/library/tnwks_win_scheduled_task.ps1"


def module_functions() -> str:
    module = TASK_MODULE.read_text()
    return module[module.index("function ConvertTo-Sid"):module.index("$task = Get-ScheduledTask")]


# A fake of Set-LocalUser and of the Schedule.Service object: each call goes to
# $global:Calls, with the password in clear so the test can follow it, and
# $global:Folders names the folders that are there. $global:Fail makes
# RegisterTask throw an access denied whose message holds every argument;
# $global:FailUser does the same in Set-LocalUser.
SERVICE_FAKE = r"""
$global:Calls = New-Object System.Collections.Generic.List[string]
function Set-LocalUser([string]$Name, [securestring]$Password) {
    $clear = [Net.NetworkCredential]::new('', $Password).Password
    $global:Calls.Add("set-localuser $Name password=$clear")
    if ($global:FailUser) { throw [InvalidOperationException]::new("Set-LocalUser -Name $Name -Password $clear failed") }
}
function New-FakeFolder([string]$Path) {
    $f = [pscustomobject]@{ Path = $Path }
    $f | Add-Member ScriptMethod RegisterTask {
        param($Name, $Xml, $Flags, $User, $Password, $LogonType)
        $global:Calls.Add("register $($this.Path) $Name flags=$Flags user=$User password=$Password logon=$LogonType xml=$($Xml.Length)")
        if ($global:Fail) {
            throw [Runtime.InteropServices.COMException]::new("RegisterTask($Name, $Flags, $User, $Password, $LogonType): Access is denied.", -2147024891)
        }
    }
    $f | Add-Member ScriptMethod CreateFolder {
        param($Name)
        if ("\$Name" -in $global:Folders) { throw [Runtime.InteropServices.COMException]::new('Cannot create a file when that file already exists.', -2147024713) }
        $global:Calls.Add("create $Name"); New-FakeFolder "\$Name"
    }
    $f
}
# The reads behind the refusals: by default an account that the module may reset.
$global:Sid = 'S-1-5-21-7-7-7-1002'; $global:Admins = @('S-1-5-21-7-7-7-500'); $global:Desc = 'd'
$global:Caller = 'S-1-5-18'; $global:Types = @(4, 5); $global:NoSuch = $false
function Get-TnwksAccountSid([string]$User) { if ($global:NoSuch) { throw 'No mapping' }; $global:Sid }
function Get-TnwksAccountDescription([string]$Sid) { $global:Desc }
function Get-TnwksAdministratorSids { $global:Admins }
function Get-TnwksCallerSid { $global:Caller }
function Get-TnwksLogonTypes([string]$Sid) { $global:Types }
$service = [pscustomobject]@{}
$service | Add-Member ScriptMethod Connect { $global:Calls.Add('connect') }
$service | Add-Member ScriptMethod GetFolder {
    param($Path)
    if ($Path -notin $global:Folders) { throw [Runtime.InteropServices.COMException]::new('The system cannot find the file specified.', -2147024894) }
    New-FakeFolder $Path
}
"""


def run_module(body: str) -> list[str]:
    run = pwsh(module_functions() + SERVICE_FAKE + body)
    assert run.returncode == 0, run.stderr
    return run.stdout.strip().splitlines()


PASSWORD = re.compile(r"password=(\S+)")


@needs_pwsh
def test_each_registration_sets_a_new_password_on_the_account_and_registers_by_s4u_with_it():
    out = run_module(r"""
$global:Folders = @('\')
$first = Register-TnwksS4uTask -Service $service -Path '\' -Name 'tnwks-rehearsal-probe' -Xml '<Task />' -User 'tnwks-rehearsal' -Description 'd'
$second = Register-TnwksS4uTask -Service $service -Path '\' -Name 'tnwks-rehearsal-import' -Xml '<Task />' -User 'tnwks-rehearsal' -Description 'd'
$global:Folders = @('\')
Register-TnwksS4uTask -Service $service -Path '\Rehearsal\' -Name 't' -Xml '<Task />' -User 'tnwks-rehearsal' -Description 'd'
$global:Folders = @('\', '\Rehearsal')
Register-TnwksS4uTask -Service $service -Path '\Rehearsal\' -Name 'u' -Xml '<Task />' -User 'tnwks-rehearsal' -Description 'd'
"output: $(@($first) + @($second) -join '|')"
$global:Calls
""")
    assert out[0] == "output: ", "the function gives nothing back"
    calls = [PASSWORD.sub("password=<p>", line) for line in out[1:]]
    assert calls == [
        "set-localuser tnwks-rehearsal password=<p>", "connect",
        "register \\ tnwks-rehearsal-probe flags=6 user=tnwks-rehearsal password=<p> logon=2 xml=8",
        "set-localuser tnwks-rehearsal password=<p>", "connect",
        "register \\ tnwks-rehearsal-import flags=6 user=tnwks-rehearsal password=<p> logon=2 xml=8",
        "set-localuser tnwks-rehearsal password=<p>", "connect", "create Rehearsal",
        "register \\Rehearsal t flags=6 user=tnwks-rehearsal password=<p> logon=2 xml=8",
        "set-localuser tnwks-rehearsal password=<p>", "connect",
        "register \\Rehearsal u flags=6 user=tnwks-rehearsal password=<p> logon=2 xml=8",
    ]
    passwords = [PASSWORD.search(line)[1] for line in out[1:] if "password=" in line]
    # The value set on the account is the value that registers, and each registration has its own.
    assert passwords[0::2] == passwords[1::2]
    assert len(set(passwords[0::2])) == 4
    for password in passwords:
        assert len(password) <= 127 and re.search(r"[A-Z]", password) and re.search(r"[a-z]", password)
        assert re.search(r"[0-9]", password) and re.search(r"[^A-Za-z0-9]", password)


@needs_pwsh
def test_a_failed_registration_names_the_hresult_and_never_the_password():
    out = run_module(r"""
$global:Folders = @('\')
$global:Fail = $true
try { Register-TnwksS4uTask -Service $service -Path '\' -Name 't' -Xml '<Task />' -User 'u' -Description 'd'; 'no failure' } catch { "text: $($_.Exception.Message)" }
$global:Fail = $false
$global:FailUser = $true
try { Register-TnwksS4uTask -Service $service -Path '\' -Name 't' -Xml '<Task />' -User 'u' -Description 'd'; 'no failure' } catch { "text: $($_.Exception.Message)" }
$global:Calls
""")
    texts = [line for line in out if line.startswith("text: ")]
    passwords = {PASSWORD.search(line)[1] for line in out if line.startswith(("set-localuser", "register"))}
    assert len(texts) == 2 and len(passwords) == 2
    assert "System.Runtime.InteropServices.COMException 0x80070005: RegisterTask(t, 6, u, <password>, 2): Access is denied." in texts[0]
    assert "System.InvalidOperationException 0x80131509: Set-LocalUser -Name u -Password <password> failed" in texts[1]
    for text in texts:
        assert not any(password in text for password in passwords)


@needs_pwsh
@pytest.mark.parametrize(("setup", "reason"), [
    ("$global:NoSuch = $true", "u is not an account of this host"),
    ("$global:Admins += $global:Sid", "u is a member of Administrators"),
    ("$global:Desc = 'a person'", "the Description of u is 'a person', not 'd'"),
    ("$global:Caller = $global:Sid", "u is the account of this process"),
    ("$global:Types = @(4, 3)", "u has a logon session open (type 3): the account of a session or of a person"),
    ("$global:Types = @(10)", "u has a logon session open (type 10): the account of a session or of a person"),
    ("$global:Types = @(2, 11)", "u has a logon session open (type 2, 11): the account of a session or of a person"),
])
def test_the_module_refuses_to_reset_an_account_that_is_not_one_for_it_and_changes_nothing(setup, reason):
    out = run_module(setup + r"""
$global:Folders = @('\')
try { Register-TnwksS4uTask -Service $service -Path '\' -Name 't' -Xml '<Task />' -User 'u' -Description 'd'; 'no failure' } catch { "text: $($_.Exception.Message)" }
"calls: $($global:Calls.Count)"
""")
    assert out[0] == f"text: refused, nothing is changed: {reason}. s4u_user resets the password of the account."
    assert out[1] == "calls: 0", "no Set-LocalUser and no registration"


def test_no_cmdlet_takes_the_plain_password():
    module = TASK_MODULE.read_text()
    function = module[module.index("function Register-TnwksS4uTask"):module.index("$task = Get-ScheduledTask")]
    uses = [line.strip() for line in function.splitlines() if "$fresh" in line]
    assert uses == [
        "$fresh = New-TnwksPassword",
        "$secure = [Net.NetworkCredential]::new('', $fresh).SecurePassword",
        "[void]$folder.RegisterTask($Name, $Xml, 6, $User, $fresh, 2)",
        "throw [InvalidOperationException]::new((Format-TnwksError $_.Exception).Replace($fresh, '<password>'))",
        "$fresh = $null",
    ]
    assert "Set-LocalUser -Name $User -Password $secure" in function
    assert "ConvertTo-SecureString" not in module and "AsPlainText" not in module


@needs_pwsh
def test_the_exception_chain_names_each_hresult_and_the_fields_of_a_cim_error():
    out = run_module(r"""
Format-TnwksError ([Runtime.InteropServices.COMException]::new('Access is denied.', -2147024891))
$inner = [Runtime.InteropServices.COMException]::new('The user name or password is incorrect.', -2147023570)
Format-TnwksError ([InvalidOperationException]::new('outer', $inner))
class FakeCimException : Exception {
    [int]$StatusCode = 1
    $ErrorData = [pscustomobject]@{ CimInstanceProperties = @(
        [pscustomobject]@{ Name = 'error_Code'; Value = 2147942405 },
        [pscustomobject]@{ Name = 'Message'; Value = 'Access is denied.' },
        [pscustomobject]@{ Name = 'CIMStatusCode'; Value = $null },
        [pscustomobject]@{ Name = 'ErrorSource'; Value = 'PS_ScheduledTask' }) }
    FakeCimException([string]$m) : base($m) {}
}
Format-TnwksError ([FakeCimException]::new('A general error occurred that is not covered by a more specific error code.'))
""")
    assert out[0] == "System.Runtime.InteropServices.COMException 0x80070005: Access is denied."
    assert out[1] == ("System.InvalidOperationException 0x80131509: outer <- "
                      "System.Runtime.InteropServices.COMException 0x8007052E: The user name or password is incorrect.")
    assert out[2].endswith("(status 1) [error_Code=2147942405; Message=Access is denied.]")


def test_the_password_is_reset_only_for_a_registration_and_reaches_no_output():
    module = TASK_MODULE.read_text()
    main = module[module.index("$task = Get-ScheduledTask"):]
    changed = main.index("if ($before -ne $after) {")
    not_check = main.index("if (-not $check) {", changed)
    call = main.index("Register-TnwksS4uTask -Service")
    # The reset sits inside the registration: not when equal, not in check mode.
    assert changed < not_check < call < main.index("\nif ($module.Params.running)")
    assert module.count("New-TnwksPassword") == 2 and module.count("Set-LocalUser") == 1
    function = module[module.index("function Register-TnwksS4uTask"):module.index("$task = Get-ScheduledTask")]
    assert "New-TnwksPassword" in function and "Set-LocalUser" in function
    # The failure of the S4U path passes only its text, never the error record.
    assert '$module.FailJson("$call failed: $($_.Exception.Message)")' in main
    # Nothing that goes to the result, the diff or a warning names the password.
    assert not any("password" in line.lower() for line in module.splitlines() if "$module." in line)


@needs_pwsh
def test_a_task_that_fails_to_log_on_is_named_with_the_cause():
    script = task_named("The probe makes the profile of the account")["ansible.windows.win_powershell"]["script"]
    assert "if ($code -eq '0x8007052E')" in script and "an earlier password " in script and "of the account does not start" in script
    out = pwsh(r"""'0x{0:X8}' -f [uint32]2147943726; '0x{0:X8}' -f [int]-2147023570""")
    assert out.stdout.split() == ["0x8007052E", "0x8007052E"]


def test_only_the_tasks_of_the_account_register_by_s4u_and_the_other_path_is_unchanged():
    module = TASK_MODULE.read_text()
    assert "$task = Register-ScheduledTask -TaskPath $path -TaskName $name -Xml $module.Params.xml -Force" in module
    assert "s4u_user = @{ type = 'str' }" in module
    assert "required_by = @{ s4u_user = @(, 's4u_description') }" in module
    named = [task for task in present() if "tnwks_win_scheduled_task" in task]
    assert sorted(task["name"] for task in named) == ["Tasks of the temporary account", "The start of the distro, with nobody signed in"]
    assert all(task["tnwks_win_scheduled_task"]["s4u_user"] == "{{ wsl_rehearsal_account }}" for task in named)
    # The role passes the Description that the module checks before a reset.
    assert all(task["tnwks_win_scheduled_task"]["s4u_description"] == "{{ wsl_rehearsal_account_description }}" for task in named)
    others = [path for path in (ANSIBLE / "roles").glob("*/tasks/*.yml") if path.parent.parent.name != "windows_wsl_rehearsal"]
    assert others and not any("s4u_user" in path.read_text() for path in others)


SYSTEM_BECOME = {
    "ansible_become": True,
    "ansible_become_method": "ansible.builtin.runas",
    "ansible_become_user": "SYSTEM",
}
BECOME_KEYS = {"become", "become_user", "become_method", "become_flags"}


def all_tasks(entries: list) -> list[dict]:
    """Each task, block and play in a task file or a playbook, at any depth."""
    out = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        out.append(entry)
        for key in ("block", "rescue", "always", "tasks", "pre_tasks", "post_tasks", "handlers"):
            out.extend(all_tasks(entry.get(key)))
    return out


def becomes(task: dict) -> bool:
    """A task that sets become, by keyword or by variable, to anything but off."""
    names = set((task.get("vars") or {}).keys())
    if any(name.startswith(("ansible_become", "ansible_runas")) for name in names):
        return True
    return bool(BECOME_KEYS & set(task.keys())) and task.get("become") is not False


def test_only_the_tasks_that_act_on_a_task_of_the_account_run_as_system():
    windows = sorted((ANSIBLE / "roles").glob("windows_*/tasks/*.yml")) + [
        ANSIBLE / "playbooks/windows-host.yml", ANSIBLE / "playbooks/windows-rehearsal-check.yml"]
    becoming = {}
    for path in windows:
        for task in all_tasks(yaml.safe_load(path.read_text())):
            if becomes(task):
                becoming.setdefault(path.relative_to(ANSIBLE).as_posix(), []).append(task)
    # No other task of any role or play, Linux ones included, uses runas or SYSTEM.
    for path in sorted((ANSIBLE / "roles").glob("*/tasks/*.yml")) + sorted((ANSIBLE / "playbooks").glob("*.yml")):
        for task in all_tasks(yaml.safe_load(path.read_text())):
            values = [str(value) for key, value in {**task, **(task.get("vars") or {})}.items()
                      if key in BECOME_KEYS or key.startswith(("ansible_become", "ansible_runas"))]
            if any("runas" in value or value.upper() == "SYSTEM" for value in values):
                assert task in becoming.get(path.relative_to(ANSIBLE).as_posix(), []), (path, task.get("name"))
    rehearsal = "roles/windows_wsl_rehearsal/tasks/main.yml"
    check = "playbooks/windows-rehearsal-check.yml"
    assert sorted(becoming) == [check, rehearsal]
    assert sorted(task["name"] for task in becoming[rehearsal]) == [
        "Tasks of the temporary account",
        "The import of the distro",
        "The probe makes the profile of the account",
        "The start of the distro, with nobody signed in",
    ]
    assert [task["name"] for task in becoming[check]] == ["Run the checks"]
    for task in becoming[rehearsal] + becoming[check]:
        become = {key: value for key, value in task["vars"].items() if key.startswith("ansible_become")}
        assert become == SYSTEM_BECOME, task["name"]
        assert not BECOME_KEYS & set(task.keys()), task["name"]
    # Each task of the role that registers, starts or stops a task is one of them.
    acting = [task for task in present() if "tnwks_win_scheduled_task" in task
              or re.search(r"(Start|Stop|Unregister|Register)-ScheduledTask", str(task.get("ansible.windows.win_powershell", {}).get("script", "")))]
    assert sorted(task["name"] for task in acting) == sorted(task["name"] for task in becoming[rehearsal])
    # The inventory keeps become off for every other task of a Windows host.
    group = yaml.safe_load((INVENTORY / "group_vars/windows_hosts.yml").read_text())
    assert group["ansible_become"] is False
