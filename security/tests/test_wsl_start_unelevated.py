"""The start of WSL with no elevation (Design Doc 0004, section 5.9).

With wsl_start_unelevated on for a Windows host, no elevated client may create the WSL
VM or start a process in a distro, and a directory that a task with no elevation writes
is one that SYSTEM and the elevated tasks read with care. These tests render the
task templates of the Windows roles with the switch on and off, and run the scripts in
PowerShell with fakes for wsl.exe and the Windows commands.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from winacl import ACL_FAKES

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLES = ANSIBLE / "roles"
INVENTORY = ANSIBLE / "inventory"
PWSH = shutil.which("pwsh")
SYSTEM = "S-1-5-18"

no_pwsh = pytest.mark.skipif(PWSH is None and not os.environ.get("CI"),
                             reason="no pwsh on this workstation; CI runs these tests")


def needs_pwsh(test):
    """A test that runs PowerShell: CI runs these in their own job (conftest.py)."""
    return pytest.mark.pwsh(no_pwsh(test))


SWITCH_BLOCK = re.compile(r"\{% if wsl_start_unelevated \| bool %\}(.*?)(?:\{% else %\}(.*?))?\{% endif %\}", re.DOTALL)
SWITCH_INLINE = re.compile(r"\{\{ '([^']*)' if wsl_start_unelevated \| bool else '([^']*)' \}\}")


def render(path: Path, on: bool) -> str:
    """The template with each branch of the switch resolved; other Jinja stays."""
    text = path.read_text()
    text = SWITCH_INLINE.sub(lambda m: m[1] if on else m[2], text)
    text = SWITCH_BLOCK.sub(lambda m: m[1] if on else (m[2] or ""), text)
    assert "wsl_start_unelevated" not in re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL), f"{path.name}: a form of the switch that the tests do not read"
    return text


def principal(text: str) -> tuple[str, str]:
    block = re.search(r"<Principal id=\"Author\">(.*?)</Principal>", text, re.DOTALL)[1]
    block = re.sub(r"<!--.*?-->", "", block, flags=re.DOTALL)
    return re.search(r"<UserId>(.*?)</UserId>", block)[1].strip(), re.search(r"<RunLevel>(.*?)</RunLevel>", block)[1]


def action(text: str) -> str:
    command = re.search(r"<Command>(.*?)</Command>", text)[1]
    arguments = re.search(r"<Arguments>(.*?)</Arguments>", text)
    return command + " " + (arguments[1] if arguments else "")


def scripts_of(text: str, role: Path) -> list[Path]:
    return [role / "files" / name for name in re.findall(r"([\w-]+\.ps1)", action(text))
            if (role / "files" / name).exists()]


def starts_distro_process(text: str, role: Path) -> bool:
    """Whether the task runs wsl.exe with a distro, itself or in its script."""
    if re.search(r"wsl\.exe</Command>", text) and " -d " in " " + action(text):
        return True
    return any(re.search(r"wsl\.exe\b[^\n]*-d\b|wsl\.exe @", script.read_text()) for script in scripts_of(text, role))


REHEARSALS = {"windows_wsl_rehearsal", "windows_wsl_move_rehearsal", "windows_wsl_move"}


def templates() -> list[Path]:
    """The scheduled tasks of the Windows roles (a WinSW service file is not one). The
    tasks of the temporary accounts of the two rehearsals, and those of the move of the
    distro, have their own tests (test_wsl_rehearsal.py, test_wsl_move_rehearsal.py and
    test_wsl_move_role.py)."""
    return sorted(path for path in ROLES.glob("windows_*/templates/*.xml.j2")
                  if "<Task " in path.read_text() and path.parts[-3] not in REHEARSALS)


# The elevated tasks of the desktop user that may still run wsl.exe with the switch on:
# the play refuses the switch on a host with the USB attach or the monitoring volume.
STILL_ELEVATED = {"usb-attach.xml.j2", "monitoring-mount.xml.j2"}
# The wsl.exe commands that an elevated client may run: none of them creates the WSL VM
# or starts a process in a distro (LxssUserSession.cpp, the callers of _CreateVm: a
# distro start, --mount, and --manage resize, compact or set-version create the VM).
NEITHER_VM_NOR_PROCESS = ("--shutdown", "--terminate", "--list", "--status", "--version")
# A call that a switch of the task turns off, and the switch: the compaction starts the
# distro itself only without -NoDistroStart (its run in PowerShell shows it below).
SWITCHED_OFF = {("Compact-WslVhdx.ps1", "-d $Distro -e true"): "-NoDistroStart"}
WSL_CALL = re.compile(r"&\s*wsl(?:\.exe)?\b(?P<amp>[^\n]*)|-FilePath\s+'wsl(?:\.exe)?'\s+-Arguments\s+@\((?P<native>[^\n]*)\)"
                      r"|Start-Process\b[^\n]*\bwsl(?:\.exe)?\b(?P<start>[^\n]*)", re.IGNORECASE)


def wsl_calls(text: str) -> list[str]:
    """The arguments of each call of wsl.exe in a PowerShell script, without comments."""
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r"<#.*?#>", "", code, flags=re.DOTALL)
    return [next(group for group in match.groups() if group is not None).strip() for match in WSL_CALL.finditer(code)]


def elevated_wsl_calls(path: Path) -> list[str]:
    """Each wsl.exe call of a task of the desktop user that runs elevated with the switch
    on, in its action or in a script that it runs, that may create the VM or start a
    process in a distro."""
    text = render(path, on=True)
    user, level = principal(text)
    if user == SYSTEM or level != "HighestAvailable":
        return []
    calls = []
    if re.search(r"wsl(\.exe)?</Command>", text, re.IGNORECASE):
        calls.append(re.search(r"<Arguments>(.*?)</Arguments>", text)[1] if "<Arguments>" in text else "")
    for script in scripts_of(text, path.parents[1]):
        for call in wsl_calls(script.read_text()):
            switch = SWITCHED_OFF.get((script.name, call.split(" 2>")[0].strip()))
            if switch and switch in action(text):
                continue
            calls.append(call)
    return [call for call in calls if not call.startswith(NEITHER_VM_NOR_PROCESS)]


def test_with_the_switch_on_no_elevated_task_of_the_user_creates_the_vm_or_starts_a_process():
    """No elevated client ever creates the VM or starts a process in a distro: each call
    of wsl.exe by an elevated task of the user is one that does neither."""
    found = {path.name: calls for path in templates() if (calls := elevated_wsl_calls(path))}
    assert set(found) == STILL_ELEVATED, found


def test_the_scan_of_wsl_calls_finds_each_form():
    """The mutations that the scan must catch: a start of the default distro (no -d), a
    --mount, a --manage, a call through Invoke-Native, and a call of wsl with no .exe."""
    script = """
& wsl.exe --shutdown
& wsl.exe --list --running --quiet
# & wsl.exe -d Ubuntu in a comment
& wsl.exe -e true
& wsl.exe --mount --vhd $Vhdx --bare
& wsl.exe --manage Ubuntu --resize 300GB
$r = Invoke-Native -FilePath 'wsl.exe' -Arguments @('-d', $Distro, '--exec', 'true')
& wsl hostname -I
"""
    calls = [call for call in wsl_calls(script) if not call.startswith(NEITHER_VM_NOR_PROCESS)]
    assert calls == ["-e true", "--mount --vhd $Vhdx --bare", "--manage Ubuntu --resize 300GB",
                     "'-d', $Distro, '--exec', 'true'", "hostname -I"]


def test_with_the_switch_on_each_task_that_starts_the_distro_runs_with_no_elevation():
    # And the stop task of the account that owns the distro (test_wsl_move_compaction.py).
    expected = {"wsl-keepalive.xml.j2", "wsl-watchdog.xml.j2", "udp-relay.xml.j2", "lan-bridge-ip.xml.j2", "wsl-stop.xml.j2"}
    unelevated = set()
    for path in templates():
        text = render(path, on=True)
        user, level = principal(text)
        if level == "LeastPrivilege":
            unelevated.add(path.name)
            assert user != SYSTEM and "S4U" in text, path.name
    assert unelevated == expected


def test_with_the_switch_off_each_task_is_as_before():
    for path in templates():
        if path.name in ("lan-bridge-ip.xml.j2", "wsl-stop.xml.j2"):
            continue  # the role registers it only with the switch on, or the move of the distro
        text = render(path, on=False)
        _, level = principal(text)
        assert level == "HighestAvailable", path.name
        assert "-NoDistroStart" not in text and "-IpFile" not in text, path.name
    sync = render(ROLES / "windows_lan_bridge/templates/lan-bridge-sync.xml.j2", on=False)
    assert principal(sync) == ("{{ lan_bridge_user | e }}", "HighestAvailable")


def test_the_admin_steps_run_as_system_with_no_wsl_exe():
    service = render(ROLES / "windows_wsl_maintenance/templates/wsl-service.xml.j2", on=True)
    assert principal(service) == (SYSTEM, "HighestAvailable")
    assert "-ServiceOnly" in action(service)
    sync = render(ROLES / "windows_lan_bridge/templates/lan-bridge-sync.xml.j2", on=True)
    assert principal(sync) == (SYSTEM, "HighestAvailable")
    assert "-IpFile '{{ lan_bridge_ip_file }}'" in action(sync)
    script = (ROLES / "windows_lan_bridge/files/Sync-TnwksLanBridge.ps1").read_text()
    part = script[script.index("if ($IpFile) {"):script.index("} else {", script.index("if ($IpFile) {"))]
    assert "wsl.exe" not in part.lower() and "Get-WslIpv4" not in part
    compact = render(ROLES / "windows_wsl_maintenance/templates/vhdx-compact.xml.j2", on=True)
    assert "-NoDistroStart" in action(compact)
    ip = render(ROLES / "windows_lan_bridge/templates/lan-bridge-ip.xml.j2", on=True)
    assert "-WriteIpFile '{{ lan_bridge_ip_file }}'" in action(ip)


def test_each_new_task_exists_only_with_the_switch_and_the_compaction_pauses_it():
    # Once the move of the distro is done, the play leaves the address task of the present account
    # alone (test_wsl_move_start_tasks.py).
    for role, variable, when in (("windows_wsl_maintenance", "wsl_service_task", ""),
                                 ("windows_lan_bridge", "lan_bridge_ip_task", " and wsl_move_phase != 'done'")):
        tasks = yaml.safe_load((ROLES / role / "tasks/main.yml").read_text())
        named = [task for task in tasks if task.get("tnwks_win_scheduled_task", {}).get("name") == f"{{{{ {variable} }}}}"]
        assert [task.get("when") for task in named] == ["wsl_start_unelevated | bool" + when, "not (wsl_start_unelevated | bool)"]
        assert named[1]["tnwks_win_scheduled_task"]["state"] == "absent"
    compact = (ROLES / "windows_wsl_maintenance/files/Compact-WslVhdx.ps1").read_text()
    assert compact.count("'tnwks-lan-bridge-ip'") == 2, "paused, and started again at the end"


def test_the_logs_of_the_tasks_with_no_elevation_are_apart_from_their_scripts():
    """The account may write files in its log or address directory only, through
    tnwks_win_dir_acl: a script that SYSTEM or an elevated task runs stays where the
    account may not change it. The SYSTEM part of the watchdog logs where only SYSTEM
    and the Administrators may change files."""
    found = {}
    for role in ("windows_wsl_maintenance", "windows_udp_relay", "windows_lan_bridge"):
        tasks = yaml.safe_load((ROLES / role / "tasks/main.yml").read_text())
        assert not any("ansible.windows.win_acl" in task for task in tasks), role
        for task in tasks:
            # The folder of the role itself has no writer (test_windows_role_folders.py).
            if "tnwks_win_dir_acl" in task and task["tnwks_win_dir_acl"].get("items") != "reset":
                assert task["when"] == "wsl_start_unelevated | bool", role
                for item in task.get("loop", [task["tnwks_win_dir_acl"]]):
                    found[item["path"]] = item["writer"]
    assert found == {
        "{{ wsl_watchdog_log_dir }}": "{{ wsl_maintenance_writers }}", "{{ wsl_service_log_dir }}": [],
        "{{ udp_relay_log_dir }}": "{{ udp_relay_writers }}", "{{ lan_bridge_ip_dir }}": "{{ lan_bridge_writers }}"}
    defaults = yaml.safe_load((ROLES / "windows_wsl_maintenance/defaults/main.yml").read_text())
    assert defaults["wsl_service_log_dir"] != defaults["wsl_watchdog_log_dir"]
    service = (ROLES / "windows_wsl_maintenance/templates/wsl-service.xml.j2").read_text()
    assert '-LogDir "{{ wsl_service_log_dir | e }}"' in service and "wsl_watchdog_log_dir" not in service


MODULE = ANSIBLE / "playbooks/library/tnwks_win_dir_acl.ps1"
WRITER = "S-1-5-21-7-7-7-1001"
DIR_RULES = sorted([
    "S-1-5-18|FullControl|ContainerInherit, ObjectInherit|None",
    "S-1-5-32-544|FullControl|ContainerInherit, ObjectInherit|None",
    "S-1-5-32-545|ReadAndExecute, Synchronize|ContainerInherit, ObjectInherit|None",
])
WRITER_RULES = sorted(DIR_RULES + [
    # CreateFiles is WriteData on a directory: the same bit, and .NET names it WriteData.
    f"{WRITER}|WriteData, ReadAndExecute, Synchronize|None|None",
    f"{WRITER}|Modify, Synchronize|ObjectInherit|InheritOnly",
])


def run_dir_acl(have: str, writer: str = "adult1", check: bool = False, exists: bool = True, link: bool = False) -> dict:
    """Run the logic of the module tnwks_win_dir_acl with the fakes of winacl.py."""
    text = MODULE.read_text()
    logic = text[text.index("# --- BEGIN logic"):text.index("# --- END logic")]
    encoded = base64.b64encode(logic.encode()).decode()
    body = ACL_FAKES + f"""
. ([scriptblock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))))
$script:Have = {have}
$script:Exists = ${str(exists).lower()}
$script:IsLink = ${str(link).lower()}
$thrown = $null; $changed = $null
try {{ $changed = Invoke-TnwksDirAcl -Path 'C:\\ProgramData\\x\\log' -Writer '{writer}' -Check ${str(check).lower()} }}
catch {{ $thrown = $_.Exception.Message }}
$sets = @($script:Calls | Where-Object {{ $_ -is [hashtable] }} | ForEach-Object {{
    @{{ owner = $_.Acl.Owner; protected = $_.Acl.Protected; rules = @(Get-TestKeys $_.Acl.Rules) }} }})
[Console]::Out.Write((ConvertTo-Json -Depth 5 -Compress @{{
    changed = $changed; thrown = $thrown; sets = $sets; calls = @($script:Calls | Where-Object {{ $_ -is [string] }}) }}))
"""
    return json.loads(pwsh(body).stdout.splitlines()[-1])


def an_acl(rules: list[str]) -> str:
    parts = []
    for rule in rules:
        sid, rights, inheritance, propagation = rule.split("|")
        parts.append(f"(New-TestRule '{sid}' '{rights}' '{inheritance}' '{propagation}')")
    return f"New-TestAcl $true 'S-1-5-32-544' @({','.join(parts)})"


@needs_pwsh
def test_the_directory_module_sets_the_split_rule_and_changes_nothing_in_place():
    in_place = run_dir_acl(an_acl(WRITER_RULES))
    assert in_place["changed"] is False and in_place["sets"] == [] and in_place["thrown"] is None
    reader = run_dir_acl(an_acl(DIR_RULES), writer="")
    assert reader["changed"] is False and reader["sets"] == []
    old = [rule for rule in WRITER_RULES if not rule.startswith(WRITER)] + [f"{WRITER}|Modify|ContainerInherit, ObjectInherit|None"]
    other_owner = an_acl(WRITER_RULES).replace("New-TestAcl $true 'S-1-5-32-544'", f"New-TestAcl $true '{WRITER}'")
    for have in (an_acl(old), an_acl(WRITER_RULES).replace("New-TestAcl $true", "New-TestAcl $false"),
                 an_acl(DIR_RULES), other_owner):
        out = run_dir_acl(have)
        assert out["changed"] is True and out["sets"] == [{"owner": "S-1-5-32-544", "protected": True, "rules": WRITER_RULES}]
        checked = run_dir_acl(have, check=True)
        assert checked["changed"] is True and checked["sets"] == [], "a check run changes nothing"
    assert run_dir_acl(an_acl(WRITER_RULES), writer="")["sets"][0]["rules"] == DIR_RULES


@needs_pwsh
def test_the_directory_module_makes_a_missing_directory_and_stops_at_a_link():
    missing = run_dir_acl(an_acl([]), exists=False)
    assert missing["calls"][:2] == ["Get-Item", "New-Item"] and missing["sets"][0]["rules"] == WRITER_RULES
    checked = run_dir_acl(an_acl([]), exists=False, check=True)
    assert checked["changed"] is True and checked["calls"] == ["Get-Item"]
    link = run_dir_acl(an_acl(WRITER_RULES), link=True)
    assert link["thrown"] and "not a plain directory" in link["thrown"]
    assert link["calls"] == ["Get-Item"] and link["sets"] == []


def windows_hosts() -> dict[str, dict]:
    hosts = yaml.safe_load((INVENTORY / "hosts.yml").read_text())["all"]["children"]
    group = yaml.safe_load((INVENTORY / "group_vars/windows_hosts.yml").read_text())
    out = {}
    for name in hosts["windows_hosts"]["hosts"]:
        own = yaml.safe_load((INVENTORY / f"host_vars/{name}/main.yml").read_text()) or {}
        out[name] = {**group, **own}
    return out


def test_the_switch_is_off_by_default_and_on_for_site_2_first():
    group = yaml.safe_load((INVENTORY / "group_vars/windows_hosts.yml").read_text())
    assert group["wsl_start_unelevated"] is False
    assert group["wsl_start_unelevated_done"] is False
    switched = {name for name, data in windows_hosts().items() if data["wsl_start_unelevated"]}
    assert switched == {"WSL-02"}


def test_the_play_refuses_the_switch_on_a_host_with_a_role_that_is_not_split_yet():
    """The guard is the first task of the play, before any role, with each tag."""
    play = next(play for play in yaml.safe_load((ANSIBLE / "playbooks/windows-host.yml").read_text())
                if play["hosts"] == "windows_hosts")
    first = play["pre_tasks"][0]
    assert first["name"] == "The start with no elevation has each role it needs"
    assert first["tags"] == ["always"]
    guard = first["ansible.builtin.assert"]
    for role in ROLES.glob("windows_*/tasks/*.yml"):
        assert "wsl_start_unelevated_done" not in role.read_text(), role
    usb_default = yaml.safe_load((ROLES / "windows_usb_attach/defaults/main.yml").read_text())["usb_attach_bound_devices"]

    def passes(data: dict) -> bool:
        on = bool(data.get("wsl_start_unelevated"))
        usb = data.get("usb_attach_bound_devices", usb_default)
        return all([not on or len(usb) == 0, not on or not data.get("observability_storage_enabled", False)])

    assert [condition.split(" or ")[0] for condition in guard["that"][:2]] == ["not (wsl_start_unelevated | bool)"] * 2
    assert guard["that"][2] == "not (wsl_start_unelevated_done | bool) or (wsl_start_unelevated | bool)"
    for name, data in windows_hosts().items():
        assert passes(data), name
        assert not data["wsl_start_unelevated_done"] or data["wsl_start_unelevated"], name
    site1 = windows_hosts()["DESKTOP-HKE05KI"]
    assert not passes({**site1, "wsl_start_unelevated": True}), "site 1 needs the split of its two roles first"


HOST_SWITCH = re.compile(r"\(hostvars\[wsl_distro_windows_host\]\.(\w+) \| default\(false\) \| bool\)")


def fails_play(expression: str, host: dict) -> bool:
    """Reads the default of wsl_host_check_write_test_fails_play for the given Windows
    host: switches of that host, each with default(false) and bool, joined by "and".
    Any other form stops the test, so a change of the form needs a change here."""
    body = re.fullmatch(r"\{\{ (.*) \}\}", expression.strip())
    assert body, expression
    terms = body.group(1).split(" and ")
    names = [HOST_SWITCH.fullmatch(term) for term in terms]
    assert all(names), expression
    return all(bool(host.get(name.group(1), False)) for name in names)


def test_the_write_test_fails_the_play_only_where_the_start_step_is_done():
    """The holding test: the write test fails the play of a distro whose Windows host
    starts WSL with no elevation and has done the start step, and only there. A merge
    of the switch alone leaves the plays green; a host with both on gets a play that
    can fail."""
    defaults = yaml.safe_load((ROLES / "wsl_host_check/defaults/main.yml").read_text())
    expression = defaults["wsl_host_check_write_test_fails_play"]
    for switch in (False, True):
        for done in (False, True):
            host = {"wsl_start_unelevated": switch, "wsl_start_unelevated_done": done}
            assert fails_play(expression, host) == (switch and done), host
    assert fails_play(expression, {}) is False
    hosts = windows_hosts()
    distros = yaml.safe_load((INVENTORY / "hosts.yml").read_text())["all"]["children"]["wsl_distros"]["hosts"]
    for distro in distros:
        own = yaml.safe_load((INVENTORY / f"host_vars/{distro}/main.yml").read_text())
        assert "wsl_host_check_write_test_fails_play" not in own, distro
        host = hosts[own["wsl_distro_windows_host"]]
        assert fails_play(expression, host) == bool(host["wsl_start_unelevated"] and host["wsl_start_unelevated_done"])


# --- The scripts, in PowerShell ------------------------------------------------------------

def pwsh(script: str) -> subprocess.CompletedProcess:
    assert PWSH, "CI has pwsh on its runners"
    encoded = base64.b64encode(script.encode("utf-16le")).decode()
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          check=False, capture_output=True, text=True, timeout=120)


# Fakes: each records its call in $global:Calls.
FAKES = r"""
$global:Calls = New-Object System.Collections.Generic.List[string]
function wsl.exe { $global:Calls.Add('wsl ' + ($args -join ' ')); $global:LASTEXITCODE = 0; '' }
function diskpart.exe { $global:Calls.Add('diskpart'); $global:LASTEXITCODE = 0 }
function Start-ScheduledTask { param([string]$TaskName) $global:Calls.Add("start $TaskName") }
function Stop-ScheduledTask { param([string]$TaskName) }
function Enable-ScheduledTask { param([string]$TaskName) }
function Disable-ScheduledTask { param([string]$TaskName) }
function Get-ScheduledTask { param([string]$TaskName) [pscustomobject]@{ State = 'Ready' } }
function Get-CimInstance { }
function Set-Service { $global:Calls.Add('set-service') }
function Start-Service { $global:Calls.Add('start-service') }
function Start-Sleep { }
"""


def report() -> str:
    return "\n[Console]::Out.Write(($global:Calls -join \"`n\"))\n"


@needs_pwsh
def test_the_service_part_of_the_watchdog_runs_no_wsl_exe(tmp_path):
    script = ROLES / "windows_wsl_maintenance/files/Watch-TnwksWsl.ps1"
    result = pwsh(FAKES + r"""
function Get-Service { [pscustomobject]@{ StartType = 'Manual'; Status = 'Running' } }
& '""" + str(script) + "' -ServiceOnly -KeepAliveTask keepalive -LogDir '" + str(tmp_path) + "'" + report())
    calls = result.stdout.splitlines()
    assert result.returncode == 0, result.stderr
    assert calls == ["start keepalive"]


@needs_pwsh
def test_the_watchdog_with_no_elevation_leaves_a_disabled_service_to_the_system_task(tmp_path):
    script = ROLES / "windows_wsl_maintenance/files/Watch-TnwksWsl.ps1"
    result = pwsh(FAKES + r"""
function Get-Service { [pscustomobject]@{ StartType = 'Disabled'; Status = 'Stopped' } }
function Test-Admin { $false }
& '""" + str(script) + "' -SkipGateway -SkipUsb -LogDir '" + str(tmp_path) + "'\n"
        + "[Console]::Out.Write(\"exit $LASTEXITCODE`n\" + ($global:Calls -join \"`n\"))")
    lines = result.stdout.splitlines()
    assert lines[0] == "exit 1", result.stdout + result.stderr
    assert "set-service" not in lines and "start-service" not in lines
    assert not any(line.startswith("wsl") for line in lines)
    assert "tnwks-wsl-service repairs it" in (tmp_path / "watchdog.log").read_text()


@needs_pwsh
@pytest.mark.parametrize("no_start", [True, False])
def test_the_compaction_with_no_distro_start_starts_no_process_in_the_distro(tmp_path, no_start):
    script = ROLES / "windows_wsl_maintenance/files/Compact-WslVhdx.ps1"
    vhdx = tmp_path / "ext4.vhdx"
    vhdx.write_bytes(b"x")
    result = pwsh(FAKES + r"""
function Split-Path { 'E:' }
function Get-PSDrive { [pscustomobject]@{ Free = 1GB } }
& '""" + str(script) + f"' -Distro Ubuntu -Vhdx '{vhdx}' -LogDir '{tmp_path}'" + (" -NoDistroStart" if no_start else "")
        + report())
    calls = result.stdout.splitlines()
    wsl = [call for call in calls if call.startswith("wsl")]
    assert "wsl --shutdown" in wsl and "diskpart" in calls, result.stdout + result.stderr
    assert any(" -d " in call + " " for call in wsl) is (not no_start), wsl
    assert "start tnwks-wsl-keepalive" in calls and "start tnwks-wsl-watchdog" in calls


@needs_pwsh
def test_the_sync_takes_only_an_address_of_the_wsl_vm(tmp_path):
    script = ROLES / "windows_lan_bridge/files/Sync-TnwksLanBridge.ps1"
    cases = {"172.25.20.7": "ok", "172.25.16.1": "host", "10.10.91.112": "outside", "not-an-address": "none",
             "999.1.1.1": "none", "172.25.020.7": "a leading zero", "172.25.20.07": "a leading zero"}
    lines = []
    for address in cases:
        lines.append(f"""
try {{
    Write-WslIpFile -Path '{tmp_path}/a.json' -Ip '{address}'
    [Console]::Out.WriteLine('{address} ' + (Read-WslIpFile -Path '{tmp_path}/a.json' -Subnet $subnet))
}} catch {{ [Console]::Out.WriteLine('{address} refused') }}""")
    result = pwsh(f". '{script}' -Library\n"
                  "$subnet = [pscustomobject]@{ Address = '172.25.16.1'; PrefixLength = 20 }\n" + "".join(lines) + f"""
Set-Content -LiteralPath '{tmp_path}/big.json' -Value ('{{"address": "172.25.20.7", "x": "' + ('y' * 2000) + '"}}')
try {{ Read-WslIpFile -Path '{tmp_path}/big.json' -Subnet $subnet | Out-Null; 'big read' }} catch {{ 'big refused' }}
""")
    out = result.stdout.splitlines()
    assert out[:-1] == ["172.25.20.7 172.25.20.7", "172.25.16.1 refused", "10.10.91.112 refused",
                        "not-an-address refused", "999.1.1.1 refused", "172.25.020.7 refused",
                        "172.25.20.07 refused"], result.stdout + result.stderr
    assert out[-1] == "big refused"
    assert json.loads((tmp_path / "a.json").read_text())["address"] == "172.25.20.07"


@needs_pwsh
def test_the_sync_reads_the_address_file_once_and_refuses_a_link_on_the_way(tmp_path):
    """The account may write the directory of the file. A link at the file or at a
    directory above it is refused, by the path of the open file, and an error quotes no
    text of the file."""
    script = ROLES / "windows_lan_bridge/files/Sync-TnwksLanBridge.ps1"
    real = tmp_path / "real"
    (real / "wsl-ip").mkdir(parents=True)
    (real / "wsl-ip" / "address.json").write_text('{"address": "172.25.20.7"}')
    (real / "wsl-ip" / "other.json").write_text('{"address": "172.25.20.9"}')
    (real / "wsl-ip" / "link.json").symlink_to(real / "wsl-ip" / "other.json")
    (real / "wsl-ip" / "secret.json").write_text("SECRETTOKEN and more")
    (tmp_path / "above").symlink_to(real, target_is_directory=True)
    paths = {"plain": real / "wsl-ip/address.json", "link": real / "wsl-ip/link.json",
             "above": tmp_path / "above/wsl-ip/address.json", "not JSON": real / "wsl-ip/secret.json"}
    body = f". '{script}' -Library\n$subnet = [pscustomobject]@{{ Address = '172.25.16.1'; PrefixLength = 20 }}\n"
    for name, path in paths.items():
        body += (f"try {{ [Console]::Out.WriteLine('{name}: ' + (Read-WslIpFile -Path '{path}' -Subnet $subnet)) }}"
                 f" catch {{ [Console]::Out.WriteLine('{name}: ' + $_.Exception.Message) }}\n")
    out = dict(line.split(": ", 1) for line in pwsh(body).stdout.splitlines())
    assert out["plain"] == "172.25.20.7"
    assert "not a plain file" in out["link"] and "not a plain file" in out["above"], out
    assert "not a JSON object" in out["not JSON"] and "SECRET" not in out["not JSON"], out


@needs_pwsh
def test_the_sync_reads_at_most_1_kib_of_the_address_file(tmp_path):
    script = ROLES / "windows_lan_bridge/files/Sync-TnwksLanBridge.ps1"
    document = b'{"address": "172.25.20.7"}'
    body = f". '{script}' -Library\n$subnet = [pscustomobject]@{{ Address = '172.25.16.1'; PrefixLength = 20 }}\n"
    for size in (1024, 1025):
        (tmp_path / f"{size}.json").write_bytes(document + b" " * (size - len(document)))
        body += (f"try {{ [Console]::Out.WriteLine('{size}: ' + (Read-WslIpFile -Path '{tmp_path}/{size}.json' -Subnet $subnet)) }}"
                 f" catch {{ [Console]::Out.WriteLine('{size}: ' + $_.Exception.Message) }}\n")
    out = dict(line.split(": ", 1) for line in pwsh(body).stdout.splitlines())
    assert out["1024"] == "172.25.20.7"
    assert "larger than 1 KiB" in out["1025"], out


def test_the_sync_opens_the_address_file_with_no_share_of_delete():
    """While the sync reads, the file can not be renamed, replaced or deleted. Only
    Windows enforces the share mode, so this reads the one open of the script."""
    text = (ROLES / "windows_lan_bridge/files/Sync-TnwksLanBridge.ps1").read_text()
    assert re.findall(r"\[IO\.File\]::Open\((.*)\)", text) == [
        "$full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite"]
    assert "OpenRead" not in text and "FileStream]::new" not in text and "Get-Content" not in text.split("function Read-WslIpFile")[1].split("\nfunction ")[0]


def test_an_apply_starts_the_keepalive_when_it_is_not_running():
    tasks = yaml.safe_load((ROLES / "windows_wsl_maintenance/tasks/main.yml").read_text())
    keepalive = [task for task in tasks if task.get("tnwks_win_scheduled_task", {}).get("name") == "{{ wsl_keepalive_task }}"]
    assert [task["tnwks_win_scheduled_task"].get("running") for task in keepalive] == [True]


@needs_pwsh
def test_the_sync_is_quiet_when_each_rule_is_in_place():
    script = ROLES / "windows_lan_bridge/files/Sync-TnwksLanBridge.ps1"
    body = f""". '{script}' -Library
function netsh {{ 'Address         Port        Address         Port'; '0.0.0.0         80          172.25.20.7     80'
    '0.0.0.0         2222        172.25.20.7     22' }}
$config = '{{"ListenAddress":"0.0.0.0","TcpPorts":[{{"Port":80,"WslPort":80}},{{"Port":2222,"WslPort":22}}],"RetiredTcpPorts":[6001]}}' | ConvertFrom-Json
$current = Get-PortProxy
[Console]::Out.WriteLine((Test-PortProxyInPlace -Config $config -Current $current -WslIp '172.25.20.7'))
[Console]::Out.WriteLine((Test-PortProxyInPlace -Config $config -Current $current -WslIp '172.25.20.8'))
$config.RetiredTcpPorts = @(2222)
[Console]::Out.WriteLine((Test-PortProxyInPlace -Config $config -Current $current -WslIp '172.25.20.7'))
"""
    assert pwsh(body).stdout.split() == ["True", "False", "False"]


TASK_MODULE = ANSIBLE / "playbooks/library/tnwks_win_scheduled_task.ps1"
RUN_LEVEL = re.compile(r"\s*<RunLevel>LeastPrivilege</RunLevel>")


def canonical(texts: list[str]) -> list[str]:
    """The canonical form of the module tnwks_win_scheduled_task, the one that check mode compares."""
    module = TASK_MODULE.read_text()
    functions = module[module.index("function ConvertTo-Sid"):module.index("$task = Get-ScheduledTask")]
    encoded = [base64.b64encode(text.encode()).decode() for text in texts]
    body = functions + f"""
$out = @(foreach ($e in @('{"','".join(encoded)}')) {{ Get-CanonicalXml ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($e))) }})
[Console]::Out.Write((ConvertTo-Json -Compress @($out)))
"""
    run = pwsh(body)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def as_task(path: Path) -> str:
    """The template with the switch on, and each other value of Jinja a fixed one."""
    text = re.sub(r"<UserId>\{\{.*?\}\}</UserId>", "<UserId>S-1-5-21-7-7-7-1001</UserId>", render(path, True))
    return re.sub(r"\{%.*?%\}", "", re.sub(r"\{\{.*?\}\}", "x", text))


def as_exported(task: str, name: str) -> str:
    """The task as Export-ScheduledTask gives it back: with its URI, and with no
    RunLevel element for LeastPrivilege, the default."""
    exported = RUN_LEVEL.sub("", task)
    if "<RegistrationInfo>" in exported:
        return exported.replace("<RegistrationInfo>", f"<RegistrationInfo>\n    <URI>\\{name}</URI>", 1)
    return exported.replace('task">', f'task">\n  <RegistrationInfo>\n    <URI>\\{name}</URI>\n  </RegistrationInfo>', 1)


@needs_pwsh
def test_a_task_with_no_elevation_is_the_same_as_its_export():
    """Each task with no elevation reads as unchanged once it is registered, so a check
    run and the nightly drift run report no change and an apply registers it once."""
    least = [path for path in templates() if RUN_LEVEL.search(as_task(path))]
    assert {path.name for path in least} >= {"wsl-keepalive.xml.j2", "wsl-watchdog.xml.j2", "lan-bridge-ip.xml.j2", "udp-relay.xml.j2"}
    tasks = [as_task(path) for path in least]
    out = canonical(tasks + [as_exported(task, path.stem) for task, path in zip(tasks, least)])
    assert out[:len(tasks)] == out[len(tasks):]
    assert all("RunLevel" not in text for text in out)


@needs_pwsh
def test_a_change_of_the_run_level_is_still_a_change():
    keepalive = as_task(ROLES / "windows_wsl_maintenance/templates/wsl-keepalive.xml.j2")
    elevated = keepalive.replace("<RunLevel>LeastPrivilege</RunLevel>", "<RunLevel>HighestAvailable</RunLevel>")
    exported = as_exported(keepalive, "tnwks-wsl-keepalive")
    least, highest, export = canonical([keepalive, elevated, exported])
    assert least == export and highest != export
    assert "<RunLevel>HighestAvailable</RunLevel>" in highest
