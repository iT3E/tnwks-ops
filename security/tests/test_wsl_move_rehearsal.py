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
             "The state file holds no marker", "Folder of the rehearsal", "Folders of the procedure", "The read of the live parts", "The live parts at the start",
             "The baseline of the live parts is kept, and it read the registrations", "The accounts of the rehearsal"]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    gate = next(t for t in tasks_of("setup.yml") if t.get("name") == order[6])
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
    expected = {"window.yml": ["", "wayback-done"], "wayback.yml": ["window-done"], "interrupt.yml": ["wayback-done"]}
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
                config: dict | None = None, rootfs: bool = False, fakes: str = "") -> dict:
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
function Get-Acl {
    param([string]$LiteralPath)
    $o = [pscustomobject]@{ Sddl = $global:Sddl }
    $o | Add-Member -MemberType ScriptMethod -Name GetOwner -Value { param($t) [pscustomobject]@{ Value = ($this.Sddl -replace '^O:(.+?)D:.*$', '$1') } }
    $o | Add-Member -MemberType ScriptMethod -Name SetSecurityDescriptorSddlForm -Value { param($s, $p) $this.Sddl = ($this.Sddl -replace 'D:.*$', '') + $s }
    $o
}
function Set-Acl { param([string]$LiteralPath, $AclObject) $global:Sets.Add("acl $(Split-Path -Leaf (Split-Path -Parent $LiteralPath))"); $global:Sddl = $AclObject.Sddl }
"""


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
        return run_account(d, "strip-rules", disk_at=disk_at, fakes=STRIP_FAKES + (
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
function Get-LocalUser { param([string]$Name) [pscustomobject]@{ SID = [pscustomobject]@{ Value = $global:Sids[$Name] } } }
function Get-LocalGroupMember { param([string]$SID) @($global:Admins | ForEach-Object { [pscustomobject]@{ SID = [pscustomobject]@{ Value = $_ } } }) }
function Add-LocalGroupMember { param([string]$SID, $Member) $global:Calls.Add("join $Member"); $global:Admins.Add($Member) }
function Remove-LocalGroupMember { param([string]$SID, $Member) $global:Calls.Add("leave $Member"); [void]$global:Admins.Remove($Member) }
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
    if ($global:QueuedPolls) { $global:Pending[$TaskName] = [int]$global:QueuedPolls }
    if ($TaskName -like '*-stop') { foreach ($p in @($global:Procs | Where-Object { $_.OwnerSid -eq $sid })) { [void]$global:Procs.Remove($p) } }
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
        return 'O:BA' + [TnwksSecurity]::FileGives([TnwksSecurity]::ReadSddl([IO.Path]::GetDirectoryName($Path)), 'BA')
    }
    # What a folder gives a new file in it, by the rule of inheritance (the stand-in of
    # CreatePrivateObjectSecurityEx): each rule of the folder that reaches a file (OI,
    # with or without IO and NP; CI alone does not), with the flag ID only, CREATOR
    # OWNER as the owner of the file, and each generic right as the rights of a file.
    static [string] FileGives([string]$FolderSddl, [string]$Owner) {
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
            $sid = if ($f[5] -in @('CO', 'S-1-3-0')) { $Owner } else { $f[5] }
            $out += '({0};ID;0x{1:x};;;{2})' -f $f[0], $mask, $sid
        }
        return 'D:AI' + $out
    }
    static [void] SetOwner([string]$Path, [string]$Owner) {
        $s = [TnwksSecurity]::ReadSddl($Path)
        $global:SecCalls.Add("owner $([TnwksSecurity]::Leaf($Path)) $Owner")
        $global:Acls[$Path] = 'O:' + $Owner + $s.Substring($s.IndexOf('D:'))
    }
    static [void] SetRules([string]$Path, [string]$Sddl) {
        $s = [TnwksSecurity]::ReadSddl($Path)
        $now = $s.Substring(2, $s.IndexOf('D:') - 2)
        if ($now -notin @('SY', 'S-1-5-18', 'BA', 'S-1-5-32-544') -and $s.Substring($s.IndexOf('D:')) -notmatch ';;;(SY|BA|S-1-5-18|S-1-5-32-544)\)') {
            $global:SecCalls.Add("refused rules $([TnwksSecurity]::Leaf($Path))")
            throw [UnauthorizedAccessException]::new('Access is denied.')
        }
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
                $g = [TnwksSecurity]::FileGives($global:FolderAcls[$folder], $owner)
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
               window: Path = WINDOW, folder_acls: dict | None = None) -> dict:
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
if ({'$true' if any(state_of.get(t) == "Running" for t in state_of) else '$false'}) {{
    foreach ($t in @($global:Running.Keys | Where-Object {{ $global:Running[$_] }})) {{
        $global:Procs.Add([pscustomobject]@{{ Name = 'wsl.exe'; OwnerSid = (Get-TaskSid $t); Task = $t }}) }}
}}
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
    first = "    Initialize-TnwksSecurity\n    switch ($Mode) {"
    assert script.count(first) == 1
    plain = tmp_path / "Invoke-TnwksMoveWindow.ps1"
    plain.write_text(script.replace(first, "    switch ($Mode) {"))
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
def test_a_file_whose_creator_owner_rule_names_another_account_is_restored(tmp_path):
    """The inherited rules count in the check: a file at the old path with the saved
    explicit rules, but a CREATOR OWNER rule that names the Administrators (as a write
    that set the rules before the owner leaves it), is not the saved set. Step 3
    restores it, and the rule then names A."""
    wrong = OLD_INHERITS.replace(f"(A;ID;0x1301bf;;;{A_SID})", "(A;ID;0x1301bf;;;BA)")
    out = run_window(tmp_path, mode="boot", state=BOOT_STATE, disk=("a",), acl_file=OLD_INHERITS, marker_a=True,
                     disabled="tnwks-moverh-a-start", acls={"a/ext4.vhdx": wrong}, inherit=INHERIT,
                     tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Disabled"})
    assert out["code"] == 0, out["log"]
    assert '"old_as_saved":false' in next(line for line in out["log"] if line.startswith("read: "))
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
    assert "step 1: the disk file has the inherited rules that its folder gives" in ok["log"]
    assert ok["log"].index("step 1: the disk file has the inherited rules that its folder gives") < next(
        i for i, line in enumerate(ok["log"]) if line.startswith("group: "))
    protected = run_window(tmp_path / "3", FORWARD_TEST, inherit=INHERIT)
    assert protected["code"] == 0 and "step 1: the disk file has the inherited rules that its folder gives" in protected["log"]
    # Another inherited rule than the folder gives: a stop.
    other = OLD_INHERITS.replace("(A;ID;FR;;;AU)", "(A;ID;FA;;;AU)")
    out = run_window(tmp_path / "4", FORWARD_TEST, acls={"a/ext4.vhdx": other}, inherit=INHERIT)
    assert out["code"] == 3 and out["markers"] == [], out["log"]


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
    assert '"O:" + owner + "G:" + owner + "D:"' in text
    assert "(Get-TnwksSddlParts ([TnwksSecurity]::FileGives($FolderSddl, $Owner))).inherited" in text


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
