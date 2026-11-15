"""The start of WSL with no elevation (Design Doc 0004, section 5.9).

WSL gives the services of a distro the access to the Windows drives of the wsl.exe
that created the distro instance, and each other process the access of the wsl.exe
that started it. With wsl_start_unelevated on for a Windows host, no task that runs
elevated as the desktop user may start a process in the distro. These tests render the
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

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLES = ANSIBLE / "roles"
INVENTORY = ANSIBLE / "inventory"
PWSH = shutil.which("pwsh")
SYSTEM = "S-1-5-18"

needs_pwsh = pytest.mark.skipif(PWSH is None and not os.environ.get("CI"),
                                reason="no pwsh on this workstation; CI runs these tests")

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


def templates() -> list[Path]:
    """The scheduled tasks of the Windows roles (a WinSW service file is not one)."""
    return sorted(path for path in ROLES.glob("windows_*/templates/*.xml.j2") if "<Task " in path.read_text())


# The elevated tasks of the desktop user that may still run wsl.exe with the switch on:
# the compaction does not start the distro with -NoDistroStart, and the play refuses the
# switch on a host with the USB attach or the monitoring volume.
STILL_ELEVATED = {"vhdx-compact.xml.j2", "usb-attach.xml.j2", "monitoring-mount.xml.j2"}


def test_with_the_switch_on_no_elevated_task_of_the_user_starts_a_process_in_the_distro():
    checked = []
    for path in templates():
        text = render(path, on=True)
        user, level = principal(text)
        if user == SYSTEM or level != "HighestAvailable":
            continue
        if starts_distro_process(text, path.parents[1]):
            checked.append(path.name)
            assert path.name in STILL_ELEVATED, f"{path.name} starts a process in the distro, elevated"
    assert set(checked) == STILL_ELEVATED


def test_with_the_switch_on_each_task_that_starts_the_distro_runs_with_no_elevation():
    expected = {"wsl-keepalive.xml.j2", "wsl-watchdog.xml.j2", "udp-relay.xml.j2", "lan-bridge-ip.xml.j2"}
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
        if path.name == "lan-bridge-ip.xml.j2":
            continue  # the role registers it only with the switch on
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
    for role, variable in (("windows_wsl_maintenance", "wsl_service_task"), ("windows_lan_bridge", "lan_bridge_ip_task")):
        tasks = yaml.safe_load((ROLES / role / "tasks/main.yml").read_text())
        named = [task for task in tasks if task.get("tnwks_win_scheduled_task", {}).get("name") == f"{{{{ {variable} }}}}"]
        assert [task.get("when") for task in named] == ["wsl_start_unelevated | bool", "not (wsl_start_unelevated | bool)"]
        assert named[1]["tnwks_win_scheduled_task"]["state"] == "absent"
    compact = (ROLES / "windows_wsl_maintenance/files/Compact-WslVhdx.ps1").read_text()
    assert compact.count("'tnwks-lan-bridge-ip'") == 2, "paused, and started again at the end"


def test_the_logs_of_the_tasks_with_no_elevation_are_apart_from_their_scripts():
    """The account may change the log directory only: a script that SYSTEM or an
    elevated task runs stays where the account may not change it."""
    for role, directory in (("windows_wsl_maintenance", "wsl_watchdog_log_dir"), ("windows_udp_relay", "udp_relay_log_dir"),
                            ("windows_lan_bridge", "lan_bridge_ip_dir")):
        tasks = yaml.safe_load((ROLES / role / "tasks/main.yml").read_text())
        acls = [task["ansible.windows.win_acl"] for task in tasks if "ansible.windows.win_acl" in task]
        assert [acl["path"] for acl in acls] == [f"{{{{ {directory} }}}}"], role
        assert acls[0]["rights"] == "Modify"
        defaults = yaml.safe_load((ROLES / role / "defaults/main.yml").read_text())
        assert defaults[directory].endswith(("\\log", "\\wsl-ip")), role


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
    tasks = yaml.safe_load((ROLES / "windows_wsl_maintenance/tasks/main.yml").read_text())
    guard = tasks[0]["ansible.builtin.assert"]
    assert tasks[0]["name"] == "The start with no elevation has each role it needs"
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
             "999.1.1.1": "none"}
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
                        "not-an-address refused", "999.1.1.1 refused"], result.stdout + result.stderr
    assert out[-1] == "big refused"
    assert json.loads((tmp_path / "a.json").read_text())["address"] == "999.1.1.1"
