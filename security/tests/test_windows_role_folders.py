"""The folders of the Windows roles under C:\\ProgramData (Design Doc 0004, section 7, row 23).

Each folder of a role lets only SYSTEM and the Administrators make or change files, and
Users read. A folder that no role owns (windows_locked_dirs) is for SYSTEM and the
Administrators only. The account that owns the distro writes only to
the log and address folders and the inbox, each with rules of its own. These tests read
the rules that the play sets for each folder, the owner that each item below takes, and
which task with no elevation writes where.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from test_wsl_start_unelevated import MODULE, needs_pwsh, principal, pwsh, render, templates
from winacl import ACL_FAKES

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLES = ANSIBLE / "roles"
INVENTORY = ANSIBLE / "inventory"
ACCOUNT = "S-1-5-21-7-7-7-1001"
ADMINS = "S-1-5-32-544"
SYSTEM = "S-1-5-18"

# Each folder of a role under C:\ProgramData, and what sets its rules.
FOLDERS = {
    "lan_bridge_dir": "module",
    "wsl_watchdog_dir": "module",
    "vhdx_compact_dir": "module",
    "udp_relay_dir": "module",
    "usb_attach_dir": "module",
    "observability_storage_dir": "module",
    # The role sets its own rules, with the same result (test_host_check_windows.py).
    "host_check_dir": "host check",
    # The inbox: the account of the distro may make and change files.
    "host_check_inbox_dir": "inbox",
    # SYSTEM, the Administrators, Users read, and each service reads.
    "coral_detector_root": "coral",
    # The rehearsal of the account change, on WSL-02 while it runs: a role
    # folder whose log and disk folders the temporary account writes to.
    "wsl_rehearsal_dir": "rehearsal",
}
# The folders below a role folder that the account writes to, with their own call
# of the module and a writer. The SYSTEM log of the watchdog has its own call too.
OWN_RULES = {
    "lan_bridge_dir": ["lan_bridge_ip_dir"],
    "wsl_watchdog_dir": ["wsl_watchdog_log_dir", "wsl_service_log_dir"],
    "udp_relay_dir": ["udp_relay_log_dir"],
}


def defaults() -> dict[str, tuple[str, str]]:
    """Each value of the defaults of the Windows roles: name -> (role, value)."""
    out = {}
    for path in sorted(ROLES.glob("windows_*/defaults/main.yml")):
        for name, value in (yaml.safe_load(path.read_text()) or {}).items():
            out[name] = (path.parts[-3], value)
    return out


def module_calls(role: str) -> list[dict]:
    """Each call of tnwks_win_dir_acl in the tasks of a role, one for each item of a loop."""
    calls = []
    for task in yaml.safe_load((ROLES / role / "tasks/main.yml").read_text()):
        if "tnwks_win_dir_acl" not in task:
            continue
        args = task["tnwks_win_dir_acl"]
        for item in task.get("loop", [None]):
            call = {}
            for key, value in args.items():
                field = re.fullmatch(r"\{\{ item\.(\w+) \}\}", value) if isinstance(value, str) else None
                call[key] = item[field[1]] if field else value
            call["when"] = task.get("when")
            calls.append(call)
    return calls


def var(text: str) -> str:
    return re.fullmatch(r"\{\{ (\w+) \}\}", text)[1]


def test_each_folder_of_a_role_is_in_the_list():
    found = {name for name, (_, value) in defaults().items()
             if isinstance(value, str) and value.lower().startswith("c:\\programdata\\")}
    assert found == set(FOLDERS), "a new folder under C:\\ProgramData needs its rules here"


def test_each_folder_of_a_role_lets_only_system_and_the_administrators_change_it():
    """Each call has no writer, resets each item below, and runs on each host with the role."""
    values = defaults()
    for name, how in FOLDERS.items():
        if how != "module":
            continue
        role = values[name][0]
        calls = [call for call in module_calls(role) if call["path"] == f"{{{{ {name} }}}}"]
        assert len(calls) == 1, name
        call = calls[0]
        assert call.get("writer", "") == "" and call["items"] == "reset" and call["when"] is None, name
        kept = sorted(var(path) for path in call.get("keep", []))
        assert kept == sorted(OWN_RULES.get(name, [])), name


def test_each_folder_with_rules_of_its_own_is_below_its_role_folder_and_has_its_call():
    values = defaults()
    for parent, children in OWN_RULES.items():
        role = values[parent][0]
        calls = {call["path"]: call for call in module_calls(role)}
        for child in children:
            assert values[child][1].startswith(f"{{{{ {parent} }}}}\\"), child
            assert f"{{{{ {child} }}}}" in calls, child
    writers = {var(call["path"]): call.get("writer", "") for role in {r for r, _ in values.values()}
               for call in module_calls(role) if call.get("writer")}
    assert set(writers) == {"lan_bridge_ip_dir", "wsl_watchdog_log_dir", "udp_relay_log_dir"}


def test_the_folders_of_the_host_check_and_the_detector_let_no_user_make_files():
    host_check = (ROLES / "windows_host_check/tasks/main.yml").read_text()
    assert host_check.count("$acl.SetAccessRuleProtection($true, $false)") >= 2
    assert "'S-1-5-32-545', 'ReadAndExecute, Synchronize'" in host_check
    assert "The files of the host check directory take its owner and rules" in host_check
    coral = yaml.safe_load((ROLES / "windows_coral_detector/tasks/main.yml").read_text())
    acl = next(task for task in coral if "ansible.windows.win_acl" in task)
    assert sorted((item["user"], item["rights"]) for item in acl["loop"]) == [
        (SYSTEM, "FullControl"), (ADMINS, "FullControl"), ("S-1-5-32-545", "ReadAndExecute")]
    inheritance = next(task for task in coral if "ansible.windows.win_acl_inheritance" in task)
    assert inheritance["ansible.windows.win_acl_inheritance"]["state"] == "absent"


def test_a_folder_that_no_role_owns_is_for_system_and_the_administrators_only():
    group = yaml.safe_load((INVENTORY / "group_vars/windows_hosts.yml").read_text())
    assert group["windows_locked_dirs"] == []
    site1 = yaml.safe_load((INVENTORY / "host_vars/DESKTOP-HKE05KI/main.yml").read_text())
    assert site1["windows_locked_dirs"] == ["C:\\ProgramData\\tnwks-cleanup-backup"]
    play = yaml.safe_load((ANSIBLE / "playbooks/windows-host.yml").read_text())
    hosts = next(p for p in play if p["hosts"] == "windows_hosts")
    task = next(t for t in hosts["pre_tasks"] if "tnwks_win_dir_acl" in t)
    assert task["tnwks_win_dir_acl"] == {"path": "{{ item }}", "readers": "none", "items": "reset"}
    assert task["loop"] == "{{ windows_locked_dirs }}" and "when" not in task


# The write targets in the arguments of a task: a log directory, a log file in a
# directory, the address file, or an output redirect into a directory.
WRITES = [
    re.compile(r"-LogDir \"\{\{ (\w+) \| e \}\}\""),
    re.compile(r"-LogPath \"\{\{ (\w+) \| e \}\}\\"),
    re.compile(r"-WriteIpFile '\{\{ (\w+) \}\}'"),
    re.compile(r"\*&gt;&gt; '\{\{ (\w+) \}\}\\"),
]
WRITER_DIRS = {"lan_bridge_ip_dir", "wsl_watchdog_log_dir", "udp_relay_log_dir"}


def test_each_task_with_no_elevation_writes_only_where_the_account_has_a_write_rule():
    """With the switch on, a task of the account with no elevation writes only to a
    folder of its own rules. Each other write into a role folder comes from SYSTEM or
    from an elevated task, which the Administrators rule covers."""
    values = defaults()
    unelevated = {}
    for path in templates():
        text = render(path, True)
        user, level = principal(text) if "<RunLevel>" in text else ("", "")
        if user == SYSTEM or level != "LeastPrivilege":
            continue
        targets = {match for pattern in WRITES for match in pattern.findall(text)}
        # The address file is in the address folder.
        targets = {"lan_bridge_ip_dir" if t == "lan_bridge_ip_file" else t for t in targets}
        assert targets <= WRITER_DIRS, (path.name, targets)
        unelevated[path.name] = sorted(targets)
    assert unelevated == {
        "lan-bridge-ip.xml.j2": ["lan_bridge_ip_dir"],
        "udp-relay.xml.j2": ["udp_relay_log_dir"],
        "wsl-keepalive.xml.j2": [],
        "wsl-watchdog.xml.j2": ["wsl_watchdog_log_dir"],
    }
    assert values["lan_bridge_ip_file"][1].startswith("{{ lan_bridge_ip_dir }}\\")


ITEM_FAKES = r"""
$script:Tree = @{}
$script:Acls = @{}
function Get-ChildItem {
    param([string]$LiteralPath, [switch]$Recurse, [switch]$Force)
    $script:Calls.Add("list $LiteralPath")
    if ($script:Tree.ContainsKey($LiteralPath)) { $script:Tree[$LiteralPath] }
}
function Get-Acl { param([string]$LiteralPath) $script:Calls.Add('Get-Acl'); if ($script:Acls.ContainsKey($LiteralPath)) { $script:Acls[$LiteralPath] } else { $script:Have } }
function Add-TestItem([string]$Parent, [string]$Name, [bool]$Dir, $Acl, [bool]$Link = $false) {
    $attributes = [IO.FileAttributes]::Normal
    if ($Dir) { $attributes = [IO.FileAttributes]::Directory }
    if ($Link) { $attributes = $attributes -bor [IO.FileAttributes]::ReparsePoint }
    $path = "$Parent\$Name"
    if (-not $script:Tree.ContainsKey($Parent)) { $script:Tree[$Parent] = [System.Collections.Generic.List[object]]::new() }
    $script:Tree[$Parent].Add([pscustomobject]@{ FullName = $path; PSIsContainer = $Dir; Attributes = $attributes })
    $script:Acls[$path] = $Acl
}
"""

TOP = "C:\\ProgramData\\tnwks-watchdog"


def run_items(setup: str, keep: list[str], check: bool = False) -> dict:
    text = MODULE.read_text()
    logic = text[text.index("# --- BEGIN logic"):text.index("# --- END logic")]
    keep_list = ",".join(f"'{path}'" for path in keep) or "''"
    body = ACL_FAKES + ITEM_FAKES + logic + f"""
$plain = {{ param($Owner) New-TestAcl $false $Owner @((New-TestRule 'S-1-5-32-545' 'ReadAndExecute' 'None' 'None' $true)) }}
{setup}
$thrown = $null; $count = $null
try {{ $count = Invoke-TnwksDirItems -Path '{TOP}' -Keep @({keep_list}) -Check ${str(check).lower()} }}
catch {{ $thrown = $_.Exception.Message }}
$sets = @($script:Calls | Where-Object {{ $_ -is [hashtable] }} | ForEach-Object {{
    @{{ path = $_.Path; owner = $_.Acl.Owner; protected = $_.Acl.Protected; rules = @($_.Acl.Rules).Count }} }})
[Console]::Out.Write((ConvertTo-Json -Depth 5 -Compress @{{
    count = $count; thrown = $thrown; sets = $sets; lists = @($script:Calls | Where-Object {{ $_ -is [string] -and $_ -like 'list *' }}) }}))
"""
    run = pwsh(body)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.splitlines()[-1])


TREE = rf"""
Add-TestItem '{TOP}' 'Watch-TnwksWsl.ps1' $false (& $plain '{ACCOUNT}')
Add-TestItem '{TOP}' 'watchdog.log' $false (& $plain '{ADMINS}')
Add-TestItem '{TOP}' 'service.log' $false (& $plain '{SYSTEM}')
Add-TestItem '{TOP}' 'old.ps1' $false (New-TestAcl $true '{ADMINS}' @((New-TestRule '{ACCOUNT}' 'FullControl' 'None' 'None')))
Add-TestItem '{TOP}' 'sub' $true (New-TestAcl $false '{ADMINS}' @((New-TestRule '{ACCOUNT}' 'Modify' 'None' 'None')))
Add-TestItem '{TOP}\sub' 'a.txt' $false (& $plain '{ACCOUNT}')
Add-TestItem '{TOP}\sub' 'b.txt' $false (& $plain '{ADMINS}')
Add-TestItem '{TOP}' 'log' $true (New-TestAcl $true '{ADMINS}' @((New-TestRule '{ACCOUNT}' 'CreateFiles' 'None' 'None')))
Add-TestItem '{TOP}\log' 'watchdog.log' $false (& $plain '{ACCOUNT}')
"""


@needs_pwsh
def test_each_item_below_a_role_folder_takes_the_owner_and_the_rules_of_the_folder():
    out = run_items(TREE, keep=[f"{TOP}\\log"])
    reset = [f"{TOP}\\Watch-TnwksWsl.ps1", f"{TOP}\\old.ps1", f"{TOP}\\sub", f"{TOP}\\sub\\a.txt"]
    assert out["thrown"] is None and out["count"] == 4
    assert sorted(s["path"] for s in out["sets"]) == sorted(reset)
    assert all(s["owner"] == ADMINS and s["protected"] is False and s["rules"] == 0 for s in out["sets"])
    assert f"list {TOP}\\log" not in out["lists"], "a folder of keep is not read"
    checked = run_items(TREE, keep=[f"{TOP}\\log"], check=True)
    assert checked["count"] == 4 and checked["sets"] == [], "a check run changes nothing"
    kept_out = run_items(TREE, keep=[])
    assert f"{TOP}\\log" in [s["path"] for s in kept_out["sets"]], "without keep, the log folder is reset too"


@needs_pwsh
def test_a_role_folder_in_place_has_no_item_to_change():
    tree = rf"""
Add-TestItem '{TOP}' 'Watch-TnwksWsl.ps1' $false (& $plain '{ADMINS}')
Add-TestItem '{TOP}' 'service.log' $false (& $plain '{SYSTEM}')
"""
    out = run_items(tree, keep=[])
    assert out["count"] == 0 and out["sets"] == []


@needs_pwsh
def test_a_link_below_a_role_folder_stops_the_reset_before_any_change():
    tree = TREE + rf"""
Add-TestItem '{TOP}\sub' 'elsewhere' $true (& $plain '{ADMINS}') $true
"""
    out = run_items(tree, keep=[f"{TOP}\\log"])
    assert out["thrown"] and "link or a junction" in out["thrown"]
    assert out["sets"] == []


IN_PLACE = rf"""
$all = 'ContainerInherit, ObjectInherit'
$script:Have = New-TestAcl $true '{ADMINS}' @(
    (New-TestRule '{SYSTEM}' 'FullControl' $all 'None'),
    (New-TestRule '{ADMINS}' 'FullControl' $all 'None'),
    (New-TestRule 'S-1-5-32-545' 'ReadAndExecute, Synchronize' $all 'None'))
"""


def run_module(setup: str, items: str, readers: str = "users", keep: list[str] | None = None) -> dict:
    """The entry of the module, Invoke-TnwksDirAclModule, as the module runs it."""
    text = MODULE.read_text()
    logic = text[text.index("# --- BEGIN logic"):text.index("# --- END logic")]
    keep_list = ",".join(f"'{path}'" for path in (keep or [])) or ""
    body = ACL_FAKES + ITEM_FAKES + logic + f"""
function Test-Path {{ param([string]$LiteralPath) $script:Exists }}
$plain = {{ param($Owner) New-TestAcl $false $Owner @((New-TestRule 'S-1-5-32-545' 'ReadAndExecute' 'None' 'None' $true)) }}
{setup}
$run = Invoke-TnwksDirAclModule -Params @{{ path = '{TOP}'; writer = ''; readers = '{readers}'; items = '{items}'; keep = @({keep_list}) }} -Check $false
$sets = @($script:Calls | Where-Object {{ $_ -is [hashtable] }} | ForEach-Object {{
    @{{ path = $_.Path; owner = $_.Acl.Owner; rules = @(Get-TestKeys $_.Acl.Rules) }} }})
[Console]::Out.Write((ConvertTo-Json -Depth 5 -Compress @{{ changed = $run.changed; items_reset = $run.items_reset; sets = $sets }}))
"""
    run = pwsh(body)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.splitlines()[-1])


@needs_pwsh
def test_the_module_resets_the_items_and_says_changed_when_only_an_item_changes():
    out = run_module(IN_PLACE + TREE, items="reset", keep=[f"{TOP}\\log"])
    assert out["items_reset"] == 4 and out["changed"] is True
    assert TOP not in [s["path"] for s in out["sets"]], "the folder itself is in place"
    assert len(out["sets"]) == 4


@needs_pwsh
def test_the_module_leaves_the_items_with_items_keep():
    out = run_module(IN_PLACE + TREE, items="keep")
    assert (out["items_reset"], out["changed"], out["sets"]) == (0, False, [])


@needs_pwsh
def test_readers_none_leaves_users_out_of_the_folder():
    out = run_module(IN_PLACE, items="reset", readers="none")
    (folder,) = [s for s in out["sets"] if s["path"] == TOP]
    assert folder["owner"] == ADMINS and out["changed"] is True
    assert [rule.split("|")[0] for rule in folder["rules"]] == [SYSTEM, ADMINS]
    again = run_module(IN_PLACE, items="reset")
    assert again["sets"] == [] and again["changed"] is False, "with Users as readers the folder is in place"


def test_the_folder_of_the_rehearsal_is_a_role_folder_with_two_writer_folders():
    tasks = yaml.safe_load((ROLES / "windows_wsl_rehearsal/tasks/main.yml").read_text())
    block = next(task for task in tasks if task.get("when") == "wsl_rehearsal_state == 'present'")["block"]
    folder = next(task for task in block if task["name"] == "Folder of the rehearsal")["tnwks_win_dir_acl"]
    assert folder == {"path": "{{ wsl_rehearsal_dir }}", "items": "reset",
                      "keep": ["{{ wsl_rehearsal_log_dir }}", "{{ wsl_rehearsal_disk_dir }}"]}
    writers = next(task for task in block if task["name"] == "Log and disk folders of the rehearsal")
    assert writers["tnwks_win_dir_acl"]["writer"] == "{{ wsl_rehearsal_account }}"
    assert writers["loop"] == folder["keep"]


def test_users_may_read_a_folder_unless_the_call_says_readers_none():
    spec = re.search(r"readers = @\{([^}]*)\}", MODULE.read_text())[1]
    assert "default = 'users'" in spec and "choices = @('users', 'none')" in spec
