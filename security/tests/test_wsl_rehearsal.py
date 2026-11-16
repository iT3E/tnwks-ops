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
STEPS = DEFAULTS["wsl_rehearsal_steps"] + ["start"]


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
    assert set(account["ansible.windows.win_powershell"]["parameters"]) == {"Name"}, "no password goes in"
    text = (ROLE / "tasks/main.yml").read_text() + (ROLE / "defaults/main.yml").read_text()
    assert not re.search(r"password\s*:", text, re.IGNORECASE)


def test_the_account_gets_one_right_and_the_rights_of_its_folders():
    right = task_named("The temporary account may log on as a batch job")["ansible.windows.win_user_right"]
    assert right == {"name": "SeBatchLogonRight", "users": ["{{ wsl_rehearsal_account }}"], "action": "add"}
    rights = [task for task in present() if "ansible.windows.win_user_right" in task]
    assert len(rights) == 1
    folder = task_named("Folder of the rehearsal")["tnwks_win_dir_acl"]
    assert folder.get("writer", "") == ""
    writers = task_named("Log and disk folders of the rehearsal")
    assert writers["tnwks_win_dir_acl"]["writer"] == "{{ wsl_rehearsal_account }}"
    assert writers["loop"] == ["{{ wsl_rehearsal_log_dir }}", "{{ wsl_rehearsal_disk_dir }}"]
    for name in ("wsl_rehearsal_log_dir", "wsl_rehearsal_disk_dir"):
        assert DEFAULTS[name].startswith("{{ wsl_rehearsal_dir }}\\")
    assert DEFAULTS["wsl_rehearsal_disk"].startswith("{{ wsl_rehearsal_disk_dir }}\\")


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


def test_the_apply_runs_no_task_in_a_check_run_and_writes_only_in_its_folder_and_profile():
    block = next(task for task in TASKS if task.get("when") == "wsl_rehearsal_state == 'present'")
    runs = next(task for task in block["block"] if task.get("name") == "Run the tasks of the account")
    assert runs["when"] == "not ansible_check_mode"
    for task in present():
        args = next(iter(v for k, v in task.items() if k.startswith(("ansible.", "tnwks_"))), {})
        for key in ("dest", "path"):
            if isinstance(args, dict) and key in args:
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


def test_the_import_waits_for_the_hive_and_the_profile_of_the_account():
    names = [task["name"] for task in present()]
    guard = names.index("The task of the account ran in its own profile")
    assert names.index("The probe makes the profile of the account") < guard < names.index(".wslconfig of the account")
    assert guard < names.index("The import of the distro")
    script = task_named("The task of the account ran in its own profile")["ansible.windows.win_powershell"]["script"]
    assert "$probe.hive" in script and "Users" in script
