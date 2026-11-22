"""AllowUsers of the sshd of a Windows host (role windows_sshd) takes a list of accounts.

While a host moves its distro to another account (Design Doc 0004, section 9, step 2),
sshd allows both accounts. The bootstrap script, the role and `task wsl-host:sshd` take
the list, and each keeps the account that Ansible logs in as.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import yaml
from test_wsl_start_unelevated import needs_pwsh, pwsh

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "infrastructure/ansible/roles/windows_sshd"
SCRIPT = ROLE / "files/Install-TnwksWindowsSshd.ps1"
TASKS = ROOT / ".taskfiles/WslHostTasks.yml"


def allow_users(names: str) -> tuple[list[str] | None, str]:
    """The accounts that the script writes in AllowUsers for -AdminUser `names` (a
    PowerShell expression), with `adult1` as the account that runs it; or the error."""
    text = SCRIPT.read_text()
    block = text[text.index("# --- BEGIN allow users"):text.index("# --- END allow users")]
    encoded = base64.b64encode(block.encode()).decode()
    result = pwsh(f"""
$ErrorActionPreference = 'Stop'
. ([scriptblock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))))
try {{
    $users = Get-TnwksAllowUsers {names} 'Adult1'
    [Console]::Out.Write((ConvertTo-Json -Compress -InputObject @($users)))
}} catch {{ [Console]::Out.Write('error: ' + $_.Exception.Message) }}
""")
    assert result.returncode == 0, result.stderr
    if result.stdout.startswith("error: "):
        return None, result.stdout
    return json.loads(result.stdout), ""


@needs_pwsh
def test_allow_users_takes_one_account_or_a_list_in_order_each_once():
    assert allow_users("'Admin1'") == (["admin1"], "")
    assert allow_users("@('admin1', 'Admin2')") == (["admin1", "admin2"], "")
    # As `task wsl-host:sshd` passes a list through invoke-elevated.sh.
    assert allow_users("'admin1,admin2'") == (["admin1", "admin2"], "")
    assert allow_users("@('admin2', 'admin1', 'ADMIN2')") == (["admin2", "admin1"], "")
    # None given: the account that runs the script, as before.
    assert allow_users("@()") == (["adult1"], "")
    assert allow_users("$null") == (["adult1"], "")


@needs_pwsh
def test_allow_users_refuses_a_name_that_sshd_reads_as_more_than_one_account():
    for name in ("'admin 1'", "'*'", "'admin?'", "'!admin1'", "'admin1@host'", "'admin[1]'",
                 "'{{ ansible_user }}'", "'#admin'", "'adm\"in'"):
        users, error = allow_users(name)
        assert users is None and "not an account name for AllowUsers" in error, name


def test_the_config_has_one_allow_users_line_with_the_list():
    text = SCRIPT.read_text()
    assert re.findall(r"^AllowUsers .*$", text, re.MULTILINE) == ["AllowUsers $AllowUsersLine"]
    # The accounts of the list, then the user of the host server when there
    # is one (security/tests/test_host_mcp_user.py).
    assert "$AllowUsersLine = (@($AllowUsers) + @($Agent | Where-Object { $_ })) -join ' '" in text
    assert "[string[]]$AdminUser" in text


def test_the_role_passes_the_list_and_keeps_the_account_of_ansible():
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    assert defaults["windows_sshd_allow_users"] == "{{ [windows_sshd_admin_user] }}"
    assert defaults["windows_sshd_admin_user"] == "{{ ansible_user }}"
    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    guard = tasks[0]["ansible.builtin.assert"]
    assert "(ansible_user | lower) in (windows_sshd_allow_users | map('lower') | list)" in guard["that"]
    assert "windows_sshd_allow_users is not string" in guard["that"]
    script = next(task for task in tasks if "ansible.windows.win_powershell" in task)
    assert script["ansible.windows.win_powershell"]["parameters"]["AdminUser"] == "{{ windows_sshd_allow_users }}"
    assert tasks.index(script) > 0, "the guard runs before the script"


def test_the_bootstrap_task_passes_the_list_and_keeps_the_account_of_ansible():
    tasks = yaml.safe_load(TASKS.read_text())["tasks"]
    sshd = tasks["sshd"]
    assert "(.windows_sshd_allow_users // [.ansible_user]) | join(\",\")" in sshd["vars"]["ALLOW_USERS"]["sh"]
    commands = sshd["cmds"]
    run = next(i for i, c in enumerate(commands) if "{{.SSHD_SCRIPT}}" in str(c))
    guard = next(i for i, c in enumerate(commands) if "does not hold ansible_user" in str(c))
    assert guard < run
    assert "-AdminUser {{.ALLOW_USERS}}" in commands[run]
    check = tasks["sshd-check"]
    assert check["vars"] is sshd["vars"] or check["vars"] == sshd["vars"]
    assert any("-AdminUser {{.ALLOW_USERS}}{{if .AGENT_USER}} -AgentUser {{.AGENT_USER}}{{end}} -Check" in str(c)
               for c in check["cmds"])
    # The login check stays the account that Ansible logs in as.
    assert any('"{{.ADMIN_USER}}@{{.WINDOWS_ADDR}}" whoami' in str(c) for c in commands)


def test_the_script_is_ascii_for_windows_powershell():
    SCRIPT.read_text(encoding="ascii")
