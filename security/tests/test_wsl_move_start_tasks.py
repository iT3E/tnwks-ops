"""The start tasks of the account that owns the distro (Design Doc 0004, section 5.12; plan task 10.10).

Each WSL role registers a copy of each of its start tasks for that account, under a name of
its own. The phase of a host (wsl_move_phase) decides: off (every host today) is as before;
move registers each copy disabled and keeps the enabled state of each start task of either
account as the window set it; done keeps each copy enabled and leaves the tasks of the
present account alone. Both accounts may write to the folders of the start tasks while the
move runs; once it is done, only the account that owns the distro.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import yaml
from test_wsl_start_unelevated import needs_pwsh, pwsh
from winacl import ACL_FAKES

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLES = ANSIBLE / "roles"
GROUP = yaml.safe_load((ANSIBLE / "inventory/group_vars/windows_hosts.yml").read_text())
TASK_MODULE = ANSIBLE / "playbooks/library/tnwks_win_scheduled_task.ps1"
DIR_MODULE = ANSIBLE / "playbooks/library/tnwks_win_dir_acl.ps1"
MOVE = "{{ wsl_move_phase == 'move' }}"
SYSTEM_BECOME = {"ansible_become": True, "ansible_become_method": "ansible.builtin.runas", "ansible_become_user": "SYSTEM"}


def tasks(role: str) -> list[dict]:
    return yaml.safe_load((ROLES / role / "tasks/main.yml").read_text())


def named(role: str, name: str) -> dict:
    return next(task for task in tasks(role) if task.get("name") == name)


# --- The phase -------------------------------------------------------------------------

def test_the_phase_is_off_on_every_host_until_wsl_move_is_on():
    assert GROUP["wsl_move_done"] is False
    assert GROUP["wsl_move_present_removed"] is False
    assert GROUP["wsl_move_task_suffix"] == "-account"
    phase = " ".join(GROUP["wsl_move_phase"].split())
    assert phase == ("{{ 'off' if not (wsl_move | default(false) | bool) "
                     "else ('done' if wsl_move_done | bool else 'move') }}")
    assert len(GROUP["wsl_move_new_description"]) <= 48
    # Only site 2 turns wsl_move on, and its done switch; no host sets the phase itself.
    for path in (ANSIBLE / "inventory/host_vars").glob("*/main.yml"):
        data = yaml.safe_load(path.read_text()) or {}
        assert "wsl_move_phase" not in data, path
        assert data.get("wsl_move", False) is (path.parent.name == "WSL-02"), path
        assert data.get("wsl_move_done", False) is (path.parent.name == "WSL-02"), path
        assert data.get("wsl_move_present_removed", False) is (path.parent.name == "WSL-02"), path


# --- windows_wsl_maintenance -----------------------------------------------------------

MAINTENANCE = "windows_wsl_maintenance"
DEFAULTS = yaml.safe_load((ROLES / MAINTENANCE / "defaults/main.yml").read_text())


def test_the_present_start_tasks_keep_their_state_while_the_move_runs_and_are_left_once_done():
    for name in ("Watchdog task (every 5 min and at boot)", "Keepalive task (one wsl.exe client from boot)",
                 "vhdx compaction task (on demand)"):
        task = named(MAINTENANCE, name)
        module = task["tnwks_win_scheduled_task"]
        assert module["keep_task_enabled_state"] == MOVE, name
        assert "s4u_user" not in module and "vars" not in task, name
        assert "wsl_move_phase != 'done'" in task["when"], name
    assert named(MAINTENANCE, "Keepalive task (one wsl.exe client from boot)")["when"] == (
        "wsl_keepalive_enabled | bool and wsl_move_phase != 'done'")


def test_the_copies_are_tasks_of_the_account_that_owns_the_distro_registered_as_system():
    task = named(MAINTENANCE, "Watchdog and keepalive tasks of the account that owns the distro")
    module = task["tnwks_win_scheduled_task"]
    assert task["when"] == "wsl_move_phase != 'off'"
    assert module["s4u_user"] == "{{ wsl_move_new_account }}"
    assert module["s4u_description"] == "{{ wsl_move_new_description }}"
    assert module["keep_task_enabled_state"] == MOVE
    assert module["running"] == "{{ item.running }}"
    # While the move runs, a copy is registered disabled: only the window enables it.
    assert "regex_replace('<Settings>', '<Settings><Enabled>false</Enabled>' if wsl_move_phase == 'move'" in module["xml"]
    assert "count=1" in module["xml"]
    become = {key: value for key, value in task["vars"].items() if key.startswith("ansible_become")}
    assert become == SYSTEM_BECOME
    assert task["vars"]["wsl_maintenance_user"] == "{{ wsl_move_new_account }}"
    assert task["vars"]["wsl_watchdog_task_args"] == "{{ wsl_watchdog_args_account }}"
    assert DEFAULTS["wsl_watchdog_task_args"] == "{{ wsl_watchdog_args }}"
    watchdog = (ROLES / MAINTENANCE / "templates/wsl-watchdog.xml.j2").read_text()
    assert "{{ wsl_watchdog_task_args | e }}" in watchdog and "wsl_watchdog_args" not in watchdog
    assert task["no_log"] is True
    loop = " ".join(task["loop"].split())
    assert "'name': wsl_watchdog_task_account, 'template': 'wsl-watchdog.xml.j2', 'running': false" in loop
    assert "'name': wsl_keepalive_task_account, 'template': 'wsl-keepalive.xml.j2', 'running': true" in loop
    assert "if wsl_keepalive_enabled | bool else []" in loop
    assert DEFAULTS["wsl_watchdog_task_account"] == "{{ wsl_watchdog_task }}{{ wsl_move_task_suffix }}"
    assert DEFAULTS["wsl_keepalive_task_account"] == "{{ wsl_keepalive_task }}{{ wsl_move_task_suffix }}"


def test_the_move_needs_the_start_with_no_elevation():
    task = named(MAINTENANCE, "The move of the distro needs the start with no elevation")
    assert task["ansible.builtin.assert"]["that"] == "wsl_move_phase == 'off' or wsl_start_unelevated | bool"
    names = [t.get("name") for t in tasks(MAINTENANCE)]
    assert names.index("The move of the distro needs the start with no elevation") < names.index(
        "Watchdog task (every 5 min and at boot)")


def test_the_copy_of_the_watchdog_names_the_copy_of_the_keepalive():
    args = " ".join(DEFAULTS["wsl_watchdog_args_account"].split())
    pattern = re.search(r"regex_replace\('(.*?)' ~ \(wsl_keepalive_task \| regex_escape\) ~ '(.*?)',", args)
    # The regex as Ansible reads it from the YAML text: each backslash as it is.
    regex = pattern[1] + re.escape("tnwks-wsl-keepalive") + pattern[2]
    replace = lambda text: re.sub(regex, r"\g<1>" + "tnwks-wsl-keepalive-account", text)
    host = yaml.safe_load((ANSIBLE / "inventory/host_vars/WSL-02/main.yml").read_text())
    assert replace(host["wsl_watchdog_args"]) == "-SkipGateway -SkipUsb -KeepAliveTask tnwks-wsl-keepalive-account"
    assert replace("-KeepAliveTask tnwks-wsl-keepalive -SkipUsb") == "-KeepAliveTask tnwks-wsl-keepalive-account -SkipUsb"
    assert replace("-KeepAliveTask tnwks-wsl-keepalive2") == "-KeepAliveTask tnwks-wsl-keepalive2"
    assert replace("") == ""
    assert "'\\g<1>' ~ wsl_keepalive_task_account" in args


def test_the_copy_of_the_watchdog_skips_the_check_of_the_wsl_service():
    """A batch logon of a standard account may not query the WSL service (the service
    control manager and the service grant that to interactive and service logons, SYSTEM
    and the Administrators). The copy skips check 1, which the WSL service task (SYSTEM)
    does; the copy exists only with the start with no elevation, which has that task."""
    args = " ".join(DEFAULTS["wsl_watchdog_args_account"].split())
    assert args.startswith("{{ (wsl_watchdog_args | regex_replace(")
    assert args.endswith("~ ' -SkipService') | trim }}")
    assert "SkipService" not in str(DEFAULTS["wsl_watchdog_args"])
    task = named(MAINTENANCE, "WSL service task (SYSTEM, every 5 min and at boot)")
    assert task["when"] == "wsl_start_unelevated | bool"
    script = (ANSIBLE / "roles/windows_wsl_maintenance/files/Watch-TnwksWsl.ps1").read_text()
    assert "[switch]$SkipService" in script
    assert "if (-not $SkipService -and -not (Test-WslService)) {" in script


def test_the_wsl_service_task_starts_the_copy_of_the_keepalive_once_the_move_is_done():
    assert DEFAULTS["wsl_service_keepalive_task"] == (
        "{{ wsl_keepalive_task_account if wsl_move_phase == 'done' else wsl_keepalive_task }}")
    service = (ROLES / MAINTENANCE / "templates/wsl-service.xml.j2").read_text()
    assert '-KeepAliveTask "{{ wsl_service_keepalive_task | e }}"' in service
    assert "wsl_keepalive_task |" not in service


def test_both_accounts_write_the_log_of_the_watchdog_while_the_move_runs():
    writers = " ".join(DEFAULTS["wsl_maintenance_writers"].split())
    assert writers == ("{{ [wsl_maintenance_user] if wsl_move_phase == 'off' else ([wsl_maintenance_user, "
                       "wsl_move_new_account] if wsl_move_phase == 'move' else [wsl_move_new_account]) }}")
    logs = named(MAINTENANCE, "Log directories of the watchdog and the WSL service task")
    assert logs["loop"][0] == {"path": "{{ wsl_watchdog_log_dir }}", "writer": "{{ wsl_maintenance_writers }}"}
    assert logs["loop"][1] == {"path": "{{ wsl_service_log_dir }}", "writer": []}


# --- tnwks_win_scheduled_task: running and a disabled task -----------------------------

TASK = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Settings>{enabled}<Hidden>false</Hidden></Settings>
  <Triggers><BootTrigger><Enabled>true</Enabled></BootTrigger></Triggers>
</Task>"""


def run_running(state: str, keep: bool, exists: bool = True, enabled: str = "") -> dict:
    module = TASK_MODULE.read_text()
    function = module[module.index("function Test-TnwksEnabled"):module.index("# Each exception of the chain")]
    block = module[module.index("if ($module.Params.running) {"):module.index("$current = Get-ScheduledTask")]
    xml = base64.b64encode(TASK.format(enabled=enabled).encode()).decode()
    body = function + f"""
$script:Started = 0
function Get-ScheduledTask {{ param($TaskPath, $TaskName, $ErrorActionPreference) [pscustomobject]@{{ State = '{state}' }} }}
function Start-ScheduledTask {{ param($TaskPath, $TaskName) $script:Started++ }}
$module = [pscustomobject]@{{ Params = @{{ running = $true }}; Result = @{{ changed = $false }} }}
$check = $false
$path = '\\'; $name = 'tnwks-x'
$keep = ${str(keep).lower()}
$task = {'[pscustomobject]@{ State = "x" }' if exists else '$null'}
$xml = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{xml}'))
{block}
[Console]::Out.Write((ConvertTo-Json -Compress @{{ started = $script:Started; changed = $module.Result.changed }}))
"""
    run = pwsh(body)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.splitlines()[-1])


@needs_pwsh
def test_running_does_not_start_a_task_that_is_disabled_when_the_state_is_the_tasks_own():
    # As before: with no option, a task that does not run is started.
    assert run_running("Ready", keep=False) == {"started": 1, "changed": True}
    assert run_running("Disabled", keep=False) == {"started": 1, "changed": True}
    assert run_running("Running", keep=True) == {"started": 0, "changed": False}
    # With the option, a disabled task stays as it is.
    assert run_running("Ready", keep=True) == {"started": 1, "changed": True}
    assert run_running("Disabled", keep=True) == {"started": 0, "changed": False}
    # A new task in a check run: the state of its xml (a trigger's Enabled does not count).
    assert run_running("", keep=True, exists=False, enabled="<Enabled>false</Enabled>")["started"] == 0
    assert run_running("", keep=True, exists=False, enabled="<Enabled>true</Enabled>")["started"] == 1
    assert run_running("", keep=True, exists=False)["started"] == 1


# --- tnwks_win_dir_acl: a list of writers ----------------------------------------------

DIR_RULES = sorted([
    "S-1-5-18|FullControl|ContainerInherit, ObjectInherit|None",
    "S-1-5-32-544|FullControl|ContainerInherit, ObjectInherit|None",
    "S-1-5-32-545|ReadAndExecute, Synchronize|ContainerInherit, ObjectInherit|None",
])


def writer_rules(sid: str) -> list[str]:
    return [f"{sid}|WriteData, ReadAndExecute, Synchronize|None|None", f"{sid}|Modify, Synchronize|ObjectInherit|InheritOnly"]


def run_writers(writers: str) -> dict:
    text = DIR_MODULE.read_text()
    logic = text[text.index("# --- BEGIN logic"):text.index("# --- END logic")]
    encoded = base64.b64encode(logic.encode()).decode()
    body = ACL_FAKES + f"""
. ([scriptblock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))))
$script:Have = New-TestAcl $true 'S-1-5-32-544' @()
$script:Exists = $true
$script:IsLink = $false
$changed = Invoke-TnwksDirAcl -Path 'C:\\ProgramData\\x\\log' -Writer {writers} -Check $false
$sets = @($script:Calls | Where-Object {{ $_ -is [hashtable] }} | ForEach-Object {{ @{{ rules = @(Get-TestKeys $_.Acl.Rules) }} }})
[Console]::Out.Write((ConvertTo-Json -Depth 5 -Compress @{{ changed = $changed; sets = $sets }}))
"""
    return json.loads(pwsh(body).stdout.splitlines()[-1])


@needs_pwsh
def test_the_folder_module_gives_each_writer_of_a_list_the_rules_of_a_writer():
    # The fakes translate kid1 to S-1-5-21-7-7-7-1002 and each other name to S-1-5-21-7-7-7-1001.
    one = run_writers("'adult1'")["sets"][0]["rules"]
    assert sorted(one) == sorted(DIR_RULES + writer_rules("S-1-5-21-7-7-7-1001"))
    two = run_writers("@('adult1', 'kid1')")["sets"][0]["rules"]
    assert sorted(two) == sorted(DIR_RULES + writer_rules("S-1-5-21-7-7-7-1001") + writer_rules("S-1-5-21-7-7-7-1002"))
    # An empty name and a name twice give no rule more; no writer gives the rules of a folder that is read only.
    assert sorted(run_writers("@('adult1', '', 'adult1')")["sets"][0]["rules"]) == sorted(one)
    assert sorted(run_writers("@()")["sets"][0]["rules"]) == DIR_RULES
    assert sorted(run_writers("''")["sets"][0]["rules"]) == DIR_RULES


def test_the_folder_module_takes_one_writer_or_a_list():
    text = DIR_MODULE.read_text()
    assert "writer = @{ type = 'list'; elements = 'str'; default = @() }" in text
    assert "param([string]$Path, [string[]]$Writer, [bool]$Check, [string]$Readers = 'users')" in text
    assert "foreach ($writer in @($WriterSid | Where-Object { $_ } | Select-Object -Unique)) {" in text


# --- windows_lan_bridge ----------------------------------------------------------------

LAN = "windows_lan_bridge"
LAN_DEFAULTS = yaml.safe_load((ROLES / LAN / "defaults/main.yml").read_text())
SYNC = ROLES / LAN / "files/Sync-TnwksLanBridge.ps1"


def test_the_address_task_of_the_present_account_keeps_its_state_and_is_left_once_done():
    task = named(LAN, "Address task (desktop user, no elevation)")
    assert task["tnwks_win_scheduled_task"]["keep_task_enabled_state"] == MOVE
    assert task["when"] == "wsl_start_unelevated | bool and wsl_move_phase != 'done'"
    assert "s4u_user" not in task["tnwks_win_scheduled_task"] and "vars" not in task


def test_the_copy_of_the_address_task_is_a_task_of_the_account_that_owns_the_distro():
    task = named(LAN, "Address task of the account that owns the distro")
    module = task["tnwks_win_scheduled_task"]
    assert task["when"] == "wsl_move_phase != 'off'"
    assert module["name"] == "{{ lan_bridge_ip_task_account }}"
    assert module["s4u_user"] == "{{ wsl_move_new_account }}"
    assert module["s4u_description"] == "{{ wsl_move_new_description }}"
    assert module["keep_task_enabled_state"] == MOVE
    # The same template as the task of the present account: the same triggers, the same
    # action, the same file.
    assert "lookup('ansible.builtin.template', 'lan-bridge-ip.xml.j2')" in module["xml"]
    assert "regex_replace('<Settings>', '<Settings><Enabled>false</Enabled>' if wsl_move_phase == 'move'" in module["xml"]
    become = {key: value for key, value in task["vars"].items() if key.startswith("ansible_become")}
    assert become == SYSTEM_BECOME and task["vars"]["lan_bridge_user"] == "{{ wsl_move_new_account }}"
    assert task["no_log"] is True and task["notify"] == "Run the LAN bridge sync"
    assert LAN_DEFAULTS["lan_bridge_ip_task_account"] == "{{ lan_bridge_ip_task }}{{ wsl_move_task_suffix }}"
    assert named(LAN, "The move of the distro needs the start with no elevation")["ansible.builtin.assert"]["that"] == (
        "wsl_move_phase == 'off' or wsl_start_unelevated | bool")


def test_both_accounts_write_the_address_file_while_the_move_runs():
    writers = " ".join(LAN_DEFAULTS["lan_bridge_writers"].split())
    assert writers == ("{{ [lan_bridge_user] if wsl_move_phase == 'off' else ([lan_bridge_user, "
                       "wsl_move_new_account] if wsl_move_phase == 'move' else [wsl_move_new_account]) }}")
    folder = named(LAN, "Directory of the address file")
    assert folder["tnwks_win_dir_acl"] == {"path": "{{ lan_bridge_ip_dir }}", "writer": "{{ lan_bridge_writers }}"}


def test_the_handler_runs_the_address_task_that_is_enabled_before_the_sync():
    tasks_ = " ".join(LAN_DEFAULTS["lan_bridge_ip_tasks"].split())
    assert tasks_ == ("{{ [lan_bridge_ip_task] if wsl_move_phase == 'off' else ([lan_bridge_ip_task, "
                      "lan_bridge_ip_task_account] if wsl_move_phase == 'move' else [lan_bridge_ip_task_account]) }}")
    handler = yaml.safe_load((ROLES / LAN / "handlers/main.yml").read_text())[0]["ansible.windows.win_powershell"]
    assert handler["parameters"]["Tasks"] == "{{ (lan_bridge_ip_tasks if wsl_start_unelevated | bool else []) + [lan_bridge_task] }}"
    assert handler["parameters"]["SkipDisabled"] == MOVE
    assert "if ($SkipDisabled -and (Get-ScheduledTask -TaskName $Task).State -eq 'Disabled') { continue }" in handler["script"]


def test_after_the_restart_the_address_of_the_copy_reaches_the_sync_first():
    """The copy has the triggers of the address task, and the sync waits longer after a
    VM NIC event: at boot, with the task of the present account disabled, the copy writes
    the file before the sync reads it."""
    ip = (ROLES / LAN / "templates/lan-bridge-ip.xml.j2").read_text()
    sync = (ROLES / LAN / "templates/lan-bridge-sync.xml.j2").read_text()
    assert "<Delay>PT15S</Delay>" in ip
    assert "<Delay>{{ 'PT45S' if wsl_start_unelevated | bool else 'PT15S' }}</Delay>" in sync
    for trigger in ("<LogonTrigger />", "<TimeTrigger>", "EventID=232"):
        assert trigger in ip and trigger in sync, trigger
    assert "-WriteIpFile '{{ lan_bridge_ip_file }}'" in ip
    assert "-IpFile '{{ lan_bridge_ip_file }}'" in sync


def run_part(arguments: str, fakes: str) -> str:
    """The main part of the sync script, after its functions, with fakes of the host."""
    text = SYNC.read_text()
    main = text[text.index("if ($Library) { return }") + len("if ($Library) { return }"):]
    encoded = base64.b64encode(main.encode()).decode()
    body = f""". '{SYNC}' -Library
{fakes}
{arguments}
. ([scriptblock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))))
"""
    run = pwsh(body)
    assert run.returncode == 0, run.stdout + run.stderr
    return run.stdout


@needs_pwsh
def test_after_the_restart_the_copy_writes_the_address_and_the_sync_sets_the_proxy(tmp_path):
    """The order of the window's restart: the task of the present account is disabled,
    so only the copy runs. Its run writes the new address of the VM; the run of the
    sync (SYSTEM) reads it and points each rule at it."""
    address = tmp_path / "address.json"
    (tmp_path / "ports.json").write_text(json.dumps(
        {"ListenAddress": "0.0.0.0", "TcpPorts": [{"Port": 2222, "WslPort": 22}, {"Port": 443, "WslPort": 443}],
         "RetiredTcpPorts": []}))
    # The copy: its own wsl.exe answers with the address of the VM after the restart.
    run_part(f"$WriteIpFile = '{address}'; $IpFile = ''; $WslDistro = 'Ubuntu'",
             "function wsl.exe { '172.25.20.9 10.0.0.1' }")
    assert json.loads(address.read_text())["address"] == "172.25.20.9"
    # The sync: the rules still point at the address of the VM before the restart.
    out = run_part(f"$WriteIpFile = ''; $IpFile = '{address}'; $ConfigPath = '{tmp_path / 'ports.json'}'",
                   """function Require-Admin {}
function Get-WslSubnet { [pscustomobject]@{ Address = '172.25.16.1'; PrefixLength = 20 } }
function wsl.exe { throw 'the sync runs no wsl.exe' }
$script:Rules = @{ '0.0.0.0:2222' = '172.25.20.7:22'; '0.0.0.0:443' = '172.25.20.7:443' }
function netsh {
    $a = @($args)
    if ($a -contains 'show') {
        'Address         Port        Address         Port'
        foreach ($k in $script:Rules.Keys) { $l = $k -split ':'; $c = $script:Rules[$k] -split ':'; "$($l[0])         $($l[1])        $($c[0])     $($c[1])" }
        return
    }
    $kv = @{}; foreach ($x in $a) { if ($x -match '^(\\w+)=(.*)$') { $kv[$Matches[1]] = $Matches[2] } }
    $key = "$($kv.listenaddress):$($kv.listenport)"
    if ($a -contains 'delete') { $script:Rules.Remove($key) }
    if ($a -contains 'add') { $script:Rules[$key] = "$($kv.connectaddress):$($kv.connectport)" }
    [Console]::Out.WriteLine("netsh $($a -join ' ')")
    $global:LASTEXITCODE = 0
}""")
    assert "netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=2222 connectaddress=172.25.20.9 connectport=22" in out
    assert "netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=443 connectaddress=172.25.20.9 connectport=443" in out
    assert "172.25.20.9" in out.split("Final portproxy table:")[1]


# --- windows_udp_relay -----------------------------------------------------------------

UDP = "windows_udp_relay"
UDP_DEFAULTS = yaml.safe_load((ROLES / UDP / "defaults/main.yml").read_text())


def test_the_relays_of_the_present_account_keep_their_state_and_are_left_once_done():
    task = named(UDP, "Relay tasks (desktop user, at boot and logon, restarted on exit)")
    module = task["tnwks_win_scheduled_task"]
    assert module["keep_task_enabled_state"] == MOVE and module["running"] is True
    assert task["when"] == "wsl_move_phase != 'done'" and "s4u_user" not in module and "vars" not in task
    assert named(UDP, "The move of the distro needs the start with no elevation")["ansible.builtin.assert"]["that"] == (
        "wsl_move_phase == 'off' or wsl_start_unelevated | bool")


def test_each_relay_has_a_copy_of_the_account_that_owns_the_distro():
    task = named(UDP, "Relay tasks of the account that owns the distro")
    module = task["tnwks_win_scheduled_task"]
    assert task["when"] == "wsl_move_phase != 'off'" and task["loop"] == "{{ udp_relays }}"
    assert module["name"] == "{{ item.task }}{{ wsl_move_task_suffix }}"
    assert module["s4u_user"] == "{{ wsl_move_new_account }}"
    assert module["s4u_description"] == "{{ wsl_move_new_description }}"
    assert module["keep_task_enabled_state"] == MOVE and module["running"] is True
    assert "lookup('ansible.builtin.template', 'udp-relay.xml.j2')" in module["xml"]
    assert "regex_replace('<Settings>', '<Settings><Enabled>false</Enabled>' if wsl_move_phase == 'move'" in module["xml"]
    become = {key: value for key, value in task["vars"].items() if key.startswith("ansible_become")}
    assert become == SYSTEM_BECOME and task["vars"]["udp_relay_user"] == "{{ wsl_move_new_account }}"
    assert task["no_log"] is True and task["notify"] == "Restart UDP relays"


def test_both_accounts_write_the_logs_of_the_relays_while_the_move_runs():
    writers = " ".join(UDP_DEFAULTS["udp_relay_writers"].split())
    assert writers == ("{{ [udp_relay_user] if wsl_move_phase == 'off' else ([udp_relay_user, "
                       "wsl_move_new_account] if wsl_move_phase == 'move' else [wsl_move_new_account]) }}")
    folder = named(UDP, "Log directory of the relays")
    assert folder["tnwks_win_dir_acl"] == {"path": "{{ udp_relay_log_dir }}", "writer": "{{ udp_relay_writers }}"}


def test_the_handler_restarts_the_relays_of_the_phase_that_are_enabled():
    names = " ".join(UDP_DEFAULTS["udp_relay_task_names"].split())
    assert names == ("{{ (udp_relays | map(attribute='task') | list if wsl_move_phase != 'done' else []) "
                     "+ (udp_relays | map(attribute='task') | map('regex_replace', '$', wsl_move_task_suffix) | list "
                     "if wsl_move_phase != 'off' else []) }}")
    handler = yaml.safe_load((ROLES / UDP / "handlers/main.yml").read_text())[0]["ansible.windows.win_powershell"]
    assert handler["parameters"] == {"Tasks": "{{ udp_relay_task_names }}", "SkipDisabled": MOVE}
    script = handler["script"]
    assert script.index("if ($SkipDisabled -and $task.State -eq 'Disabled') { continue }") < script.index("Stop-ScheduledTask")


# --- windows_usb_attach ----------------------------------------------------------------

USB = "windows_usb_attach"
USB_DEFAULTS = yaml.safe_load((ROLES / USB / "defaults/main.yml").read_text())
USB_SCRIPT = ROLES / USB / "files/Sync-TnwksUsbAttach.ps1"


def test_the_attach_task_of_the_present_account_keeps_its_state_and_is_left_once_done():
    """The way back enables it again as it is: the task of today."""
    task = named(USB, "Sync task (desktop user; logon, every 15 min, after a WSL vNIC reset)")
    module = task["tnwks_win_scheduled_task"]
    assert module["keep_task_enabled_state"] == MOVE and module["name"] == "{{ usb_attach_task }}"
    assert task["when"] == "wsl_move_phase != 'done'" and "s4u_user" not in module and "vars" not in task
    assert named(USB, "The move of the distro needs the start with no elevation")["ansible.builtin.assert"]["that"] == (
        "wsl_move_phase == 'off' or wsl_start_unelevated | bool")


def test_the_attach_task_has_a_copy_of_the_account_that_owns_the_distro():
    task = named(USB, "Sync task of the account that owns the distro")
    module = task["tnwks_win_scheduled_task"]
    assert task["when"] == "wsl_move_phase != 'off'"
    assert module["name"] == "{{ usb_attach_task_account }}"
    assert USB_DEFAULTS["usb_attach_task_account"] == "{{ usb_attach_task }}{{ wsl_move_task_suffix }}"
    assert module["s4u_user"] == "{{ wsl_move_new_account }}"
    assert module["s4u_description"] == "{{ wsl_move_new_description }}"
    assert module["keep_task_enabled_state"] == MOVE
    assert "lookup('ansible.builtin.template', 'usb-attach.xml.j2')" in module["xml"]
    assert "regex_replace('<Settings>', '<Settings><Enabled>false</Enabled>' if wsl_move_phase == 'move'" in module["xml"]
    become = {key: value for key, value in task["vars"].items() if key.startswith("ansible_become")}
    assert become == SYSTEM_BECOME and task["vars"]["usb_attach_user"] == "{{ wsl_move_new_account }}"
    assert task["no_log"] is True


def test_the_bind_stays_in_the_play_as_admin_and_both_accounts_write_the_log_while_the_move_runs():
    bind = named(USB, "Devices are bound (shared, forced) for usbip")
    assert "bind --force --hardware-id" in bind["ansible.windows.win_powershell"]["script"]
    assert "vars" not in bind and "become" not in bind
    writers = " ".join(USB_DEFAULTS["usb_attach_writers"].split())
    assert writers == ("{{ [usb_attach_user] if wsl_move_phase == 'off' else ([usb_attach_user, "
                       "wsl_move_new_account] if wsl_move_phase == 'move' else [wsl_move_new_account]) }}")
    folder = named(USB, "Log directory of the attach task")
    assert folder["tnwks_win_dir_acl"] == {"path": "{{ usb_attach_log_dir }}", "writer": "{{ usb_attach_writers }}"}
    assert folder["when"] == "wsl_start_unelevated | bool"
    root = named(USB, "USB attach directory")["tnwks_win_dir_acl"]
    assert root["keep"] == ["{{ usb_attach_log_dir }}"]


def test_the_attach_task_runs_with_no_elevation_only_with_the_switch():
    from test_wsl_start_unelevated import principal, render
    template = ROLES / USB / "templates/usb-attach.xml.j2"
    on, off = render(template, on=True), render(template, on=False)
    assert principal(on) == ("{{ usb_attach_user | e }}", "LeastPrivilege")
    assert principal(off) == ("{{ usb_attach_user | e }}", "HighestAvailable")
    assert ('-NoElevation -LogPath "{{ usb_attach_log_dir | e }}\\sync.log" '
            '-StatePath "{{ usb_attach_log_dir | e }}\\sync-state.json"') in on
    assert "-NoElevation" not in off and "usb_attach_log_dir" not in off


def test_the_start_tasks_of_the_new_account_take_the_copy_where_a_device_is_bound():
    derived = " ".join(GROUP["wsl_move_new_start_tasks"].split())
    assert ("+ (['tnwks-usb-attach'] if usb_attach_bound_devices | default(['the default of the role']) "
            "| length > 0 else [])") in derived
    assert USB_DEFAULTS["usb_attach_task"] == "tnwks-usb-attach" and USB_DEFAULTS["usb_attach_bound_devices"]


USB_FAKES = r"""
$global:Calls = New-Object System.Collections.Generic.List[string]
function usbipd {
    $global:Calls.Add('usbipd ' + ($args -join ' ')); $global:LASTEXITCODE = 0
    if ($args[0] -eq '--version') { '4.3.0' }
    if ($args[0] -eq 'state') { $global:State }
}
function wsl.exe { $global:Calls.Add('wsl ' + ($args -join ' ')); $global:LASTEXITCODE = 0 }
function Test-Admin { $global:Admin }
"""


def run_usb(tmp_path: Path, args: str, admin: bool, attached: bool = False) -> dict:
    """The script, with the identity read and Test-Admin as stand-ins (Windows only)."""
    text = USB_SCRIPT.read_text()
    assert text.count("function Test-Admin {") == 1 and text.count("[Security.Principal.WindowsIdentity]::GetCurrent().Name") == 1
    text = text.replace("function Test-Admin {", "function Test-AdminOfWindows {")
    text = text.replace("[Security.Principal.WindowsIdentity]::GetCurrent().Name", "'standin1'")
    text = text.replace("exit $exitCode", "$global:Exit = $exitCode")
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "Sync-TnwksUsbAttach.ps1"
    script.write_text(text)
    client = "172.25.20.9" if attached else ""
    state = json.dumps({"Devices": [{"BusId": "1-6", "InstanceId": "USB\\VID_0658&PID_0200\\5&1", "ClientIPAddress": client}]})
    result = pwsh(USB_FAKES + f"$global:Admin = ${str(admin).lower()}\n$global:State = '{state}'\n"
                  + f"& '{script}' -LogPath '{tmp_path / 'sync.log'}' -StatePath '{tmp_path / 'sync-state.json'}' {args} | Out-Null\n"
                  + "[Console]::Out.Write((@{ calls = @($global:Calls); exit = $global:Exit } | ConvertTo-Json -Compress))")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    out["log"] = (tmp_path / "sync.log").read_text() if (tmp_path / "sync.log").exists() else ""
    return out


@needs_pwsh
def test_with_no_elevation_the_script_attaches_with_no_bind_and_restarts_the_worker(tmp_path):
    out = run_usb(tmp_path, "-NoElevation", admin=False)
    assert out["exit"] == 0, out["log"]
    assert out["calls"] == ["usbipd --version", "usbipd state", "usbipd attach --wsl --busid 1-6",
                            "wsl -d Ubuntu --exec docker restart homelab-wsl-worker-2", "usbipd list"]
    assert "no bind with no elevation; the play binds it" in out["log"]


@needs_pwsh
def test_with_no_elevation_an_attached_device_is_left_as_it_is(tmp_path):
    out = run_usb(tmp_path, "-NoElevation", admin=False, attached=True)
    assert out["exit"] == 0 and out["calls"] == ["usbipd --version", "usbipd state", "usbipd list"], out["log"]


@needs_pwsh
def test_the_elevated_script_binds_then_attaches_as_before_and_stops_with_no_admin_right(tmp_path):
    elevated = run_usb(tmp_path / "1", "", admin=True)
    assert elevated["exit"] == 0, elevated["log"]
    assert elevated["calls"][:4] == ["usbipd --version", "usbipd state", "usbipd bind --force --busid 1-6",
                                     "usbipd attach --wsl --busid 1-6"]
    plain = run_usb(tmp_path / "2", "", admin=False)
    assert plain["exit"] == 1 and plain["calls"] == [], plain["log"]
    assert "not running as Administrator" in plain["log"]
