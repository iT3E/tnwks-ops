"""The local user of the host server of the main agent on each host (docs/wsl/host-mcp-user.md).

The roles windows_host_mcp_user and wsl_host_mcp_user make it, and windows_sshd gives it a
Match block of its own (Design Doc of effort 0003, section 4.2.6, "The host server"). These
tests read the roles, the playbooks, the bootstrap task and the inventory: the switch is off
until a site turns it on, the values are encrypted variables of the site, the key holds for
the source address of the cluster only, the user is in no admin group, root or the
Administrators own its startup files and its key file, and no run prints its name or key.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import yaml
from test_wsl_start_unelevated import needs_pwsh, pwsh

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLES = ANSIBLE / "roles"
INVENTORY = ANSIBLE / "inventory"
WINDOWS = ROLES / "windows_host_mcp_user"
DISTRO = ROLES / "wsl_host_mcp_user"
SSHD = ROLES / "windows_sshd"
SCRIPT = SSHD / "files/Install-TnwksWindowsSshd.ps1"
TASKS = ROOT / ".taskfiles/WslHostTasks.yml"
VALUES = ("host_mcp_agent_user", "host_mcp_agent_public_key", "host_mcp_agent_source_windows",
          "host_mcp_agent_source_distro")


def load(path: Path):
    return yaml.safe_load(path.read_text())


def tasks(role: Path) -> list[dict]:
    """Each task of a role, the tasks of a block too."""
    out = []
    for task in load(role / "tasks/main.yml"):
        out.append(task)
        out += task.get("block", [])
    return out


def task(role: Path, name: str) -> dict:
    (found,) = [t for t in tasks(role) if t.get("name") == name]
    return found


def test_the_switch_is_off_and_the_values_are_empty_in_git():
    common = load(INVENTORY / "group_vars/all/host_mcp.yml")
    assert common["host_mcp_agent_enabled"] is False
    assert all(common[name] == "" for name in VALUES)
    # The values are encrypted variables of a site: no plain file of the
    # inventory sets one.
    for path in INVENTORY.rglob("*.yml"):
        if ".sops." in path.name or path == INVENTORY / "group_vars/all/host_mcp.yml":
            continue
        data = load(path) or {}
        if isinstance(data, dict):
            assert not set(data) & set(VALUES), path


def test_the_playbooks_run_each_role_only_where_the_site_turns_it_on():
    windows = next(p for p in load(ANSIBLE / "playbooks/windows-host.yml") if p.get("hosts") == "windows_hosts")
    roles = [r["role"] for r in windows["roles"]]
    entry = windows["roles"][roles.index("windows_host_mcp_user")]
    assert entry["when"] == "host_mcp_agent_enabled | bool" and entry["tags"] == ["host-mcp"]
    assert roles.index("windows_host_mcp_user") < roles.index("windows_sshd")
    (distro,) = load(ANSIBLE / "playbooks/wsl-distro.yml")
    entry = next(r for r in distro["roles"] if r["role"] == "wsl_host_mcp_user")
    assert entry["when"] == "host_mcp_agent_enabled | bool" and entry["tags"] == ["host-mcp"]
    assert distro["become"] is True


def test_each_role_stops_before_any_change_when_a_value_is_missing():
    for role, source in ((WINDOWS, "host_mcp_agent_source_windows"), (DISTRO, "host_mcp_agent_source_distro")):
        first = tasks(role)[0]
        that = first["ansible.builtin.assert"]["that"]
        assert first["no_log"] is True
        assert any("host_mcp_agent_user is match('^[a-z][a-z0-9-]{0,19}$')" == t for t in that), role
        assert any(t.startswith("host_mcp_agent_public_key is match('^ssh-ed25519 ") for t in that), role
        assert any(t.startswith(f"{source} is match(") for t in that), role


def test_the_key_holds_for_the_source_address_only_with_no_forwarding_or_terminal():
    win = task(WINDOWS, "The key file of the host server")
    assert win["ansible.windows.win_copy"]["content"] == (
        'from="{{ host_mcp_agent_source_windows }}",restrict {{ host_mcp_agent_public_key }}\n')
    assert win["ansible.windows.win_copy"]["dest"] == "{{ host_mcp_agent_windows_dir }}\\authorized_keys"
    distro = task(DISTRO, "The key file of the host server")
    copy = distro["ansible.builtin.copy"]
    assert copy["content"] == 'from="{{ host_mcp_agent_source_distro }}",restrict {{ host_mcp_agent_public_key }}\n'
    assert (copy["owner"], copy["group"], copy["mode"]) == ("root", "root", "0644")
    for t in (win, distro):
        assert t["no_log"] is True and t["diff"] is False


def test_the_match_block_names_the_key_file_of_the_role():
    text = SCRIPT.read_text()
    folder = load(WINDOWS / "defaults/main.yml")["host_mcp_agent_windows_dir"]
    assert folder.lower().startswith("c:\\programdata\\")
    name = folder.split("\\", 2)[2].replace("\\", "/")
    assert f"$AgentKeysFile = '__PROGRAMDATA__/{name}/authorized_keys'" in text
    # The block comes after the one of the administrators, and only with a user.
    assert "if ($agentMatch) { $configWanted += \"`n\" + $agentMatch + \"`n\" }" in text
    # The drift line names the user of the host server by its role only.
    assert "AllowUsers $AllowUsersShown)" in text


def test_windows_sshd_and_the_bootstrap_task_pass_the_user_only_where_the_site_turns_it_on():
    defaults = load(SSHD / "defaults/main.yml")
    assert defaults["windows_sshd_agent_user"] == "{{ host_mcp_agent_user if host_mcp_agent_enabled | bool else '' }}"
    script = next(t for t in load(SSHD / "tasks/main.yml") if "ansible.windows.win_powershell" in t)
    assert script["ansible.windows.win_powershell"]["parameters"]["AgentUser"] == "{{ windows_sshd_agent_user }}"
    taskfile = load(TASKS)["tasks"]
    sh = taskfile["sshd"]["vars"]["AGENT_USER"]["sh"]
    assert "if (.host_mcp_agent_enabled // false) == true then .host_mcp_agent_user else" in sh
    for name in ("sshd", "sshd-check"):
        assert any("{{if .AGENT_USER}} -AgentUser {{.AGENT_USER}}{{end}}" in str(c) for c in taskfile[name]["cmds"]), name


def test_the_windows_user_is_in_users_only_and_keeps_the_network_logon():
    defaults = load(WINDOWS / "defaults/main.yml")
    # The SSH server signs a user in with a key by a network logon.
    assert defaults["host_mcp_agent_deny_rights"] == ["SeDenyInteractiveLogonRight", "SeDenyRemoteInteractiveLogonRight"]
    account = task(WINDOWS, "The account of the host server")
    script = account["ansible.windows.win_powershell"]["script"]
    assert account["no_log"] is True
    assert "Add-LocalGroupMember -SID 'S-1-5-32-545'" in script
    assert "S-1-5-32-544" not in script and "Administrators" not in script
    assert "a member of a group other than Users" in script
    assert "RandomNumberGenerator" in script and "-UserMayNotChangePassword" in script


def test_the_windows_startup_folders_belong_to_the_administrators_and_stay_empty():
    defaults = load(WINDOWS / "defaults/main.yml")
    assert defaults["host_mcp_agent_startup_dirs"] == ["Documents\\WindowsPowerShell", "Documents\\PowerShell"]
    folders = task(WINDOWS, "The startup folders of PowerShell of the account of the host server")
    acl = folders["tnwks_win_dir_acl"]
    assert acl == {"path": "{{ host_mcp_agent_profile }}\\{{ item }}", "items": "reset"}
    assert folders["loop_control"]["label"] == "{{ item }}"
    check = task(WINDOWS, "The startup folders are empty, and the user reads no profile of an account of AllowUsers"
                          " and no host key")
    script = check["ansible.windows.win_powershell"]["script"]
    assert check["check_mode"] is False and check["changed_when"] is False
    assert "Get-ChildItem -LiteralPath $p -Force" in script
    assert "'S-1-5-32-545'" in script and "ssh_host_ed25519_key" in script


def test_the_distro_user_has_its_own_group_only_and_root_owns_its_home():
    user = task(DISTRO, "The account of the host server")["ansible.builtin.user"]
    assert user["groups"] == [] and user["append"] is False
    assert user["create_home"] is False
    assert task(DISTRO, "The account has no password")["ansible.builtin.command"]["argv"][:3] == [
        "usermod", "-p", "{{ host_mcp_agent_shadow_field }}"]
    assert load(DISTRO / "defaults/main.yml")["host_mcp_agent_shadow_field"] == "*"
    home = task(DISTRO, "The home of the account of the host server, owned by root")["ansible.builtin.file"]
    assert (home["owner"], home["mode"]) == ("root", "0750")
    files = task(DISTRO, "The startup files of its shell, owned by root")
    assert (files["ansible.builtin.copy"]["owner"], files["ansible.builtin.copy"]["mode"]) == ("root", "0644")
    assert files["diff"] is False
    assert load(DISTRO / "defaults/main.yml")["host_mcp_agent_startup_files"] == [".bashrc", ".profile", ".bash_logout"]
    ssh = task(DISTRO, "The .ssh folder of the account, owned by root")["ansible.builtin.file"]
    assert (ssh["owner"], ssh["mode"]) == ("root", "0755")
    sudo = task(DISTRO, "The account has no sudo rule")
    assert "'is not allowed to run sudo' not in host_mcp_agent_sudo.stdout" == sudo["failed_when"]
    # The role adds it to no group (not docker, which is root, and not sudo).
    text = (DISTRO / "tasks/main.yml").read_text()
    assert re.findall(r"^\s*groups:.*$", text, re.MULTILINE) == ["    groups: []"]


def test_no_task_prints_the_name_the_key_or_the_source_address():
    for role in (WINDOWS, DISTRO):
        for t in tasks(role):
            if "block" in t:
                continue
            body = {k: v for k, v in t.items() if k not in ("name", "become_user", "loop_control")}
            if any(v in yaml.safe_dump(body) for v in VALUES + ("host_mcp_agent_home",)):
                label = (t.get("loop_control") or {}).get("label", "")
                assert t.get("no_log") is True or t.get("loop") and not any(v in label for v in VALUES), (role.name, t["name"])


AGENT_BLOCK = re.compile(r"# --- BEGIN agent user.*?\n(.*?)# --- END agent user", re.DOTALL)


def agent(name: str, admins: str = "@('admin1')") -> tuple[str, str]:
    """The user and the Match block that the script makes for -AgentUser `name`; or the error."""
    block = AGENT_BLOCK.search(SCRIPT.read_text())[1]
    encoded = base64.b64encode(block.encode()).decode()
    result = pwsh(f"""
$ErrorActionPreference = 'Stop'
. ([scriptblock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))))
try {{
    $u = Get-TnwksAgentUser {name} {admins}
    [Console]::Out.Write($u + '|' + (Get-TnwksAgentMatch $u))
}} catch {{ [Console]::Out.Write('error: ' + $_.Exception.Message) }}
""")
    assert result.returncode == 0, result.stderr
    if result.stdout.startswith("error: "):
        return "", result.stdout
    user, _, match = result.stdout.partition("|")
    return user, match


@needs_pwsh
def test_the_match_block_of_the_user():
    user, match = agent("'Agent1'")
    assert user == "agent1"
    assert match.split("\n") == [
        "Match User agent1",
        "       AuthorizedKeysFile __PROGRAMDATA__/tnwks-host-mcp/authorized_keys",
        "       DisableForwarding yes",
        "       PermitTTY no",
    ]
    # No user: no block, and sshd_config as before.
    assert agent("''") == ("", "")
    assert agent("$null") == ("", "")


@needs_pwsh
def test_the_user_is_one_account_and_never_one_of_allow_users():
    for name in ("'agent 1'", "'*'", "'agent?'", "'agent1,admin1'", "'agent1@host'", "'#agent'"):
        user, error = agent(name)
        assert not user and "not an account name for the user of the host server" in error, name
    user, error = agent("'Admin1'")
    assert not user and "is an account of AllowUsers" in error
