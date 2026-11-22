"""The guard of the user of the host server: Ansible changes a host only with
-e host_mcp_agent_apply=true (docs/wsl/host-mcp-user.md, "The guard").

The static tests read the two roles: each task that can change a host runs in check mode
unless host_mcp_agent_apply is true, and each is in the list of the drift assert, which
fails a run that finds a difference and has no flag.

The container test runs the role of the distro with Ansible itself, as root in a Debian
container with no network, against the states that a CI apply from main can meet:

- the switch off: the role is skipped, and nothing changes;
- the switch on, the user missing, no flag: the run fails with the drift message, and
  nothing changes;
- the switch on, the flag (the owner's run): the user is made;
- the switch on, the user present and equal, no flag: the checks pass, changed=0;
- the switch on, the key file different, no flag: the run fails with the drift message,
  and the file stays as it was.

The SSH server and sudo of the container are stubs that answer as the distro does (key
sign-in, no sudo rule). The test needs Docker and the uv image: without Docker it is
skipped, except in CI. Ansible comes from the lock of infrastructure/ansible; for a run
with no network, HOST_MCP_GUARD_UV_CACHE names a uv cache that holds it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLES = ANSIBLE / "roles"
WINDOWS = ROLES / "windows_host_mcp_user"
DISTRO = ROLES / "wsl_host_mcp_user"
COMMON = ANSIBLE / "inventory/group_vars/all/host_mcp.yml"
GUARD = "{{ host_mcp_agent_check_only | bool }}"
# The image of the controller: uv on Debian bookworm with Python 3.13.
UV_IMAGE = ("ghcr.io/astral-sh/uv:python3.13-bookworm"
            "@sha256:47965cdc9d53a515f68f78241161c901e70051ce428f12e791bd7fe19f6a631a")


def load(path: Path):
    return yaml.safe_load(path.read_text())


def flat(tasks: list[dict]) -> list[dict]:
    out = []
    for task in tasks:
        out.append(task)
        out += flat(task.get("block", []))
    return out


# The modules that read or decide, and change no host.
NO_CHANGE = {"ansible.builtin.assert", "ansible.builtin.set_fact", "ansible.builtin.debug"}


def module(task: dict) -> str | None:
    return next((k for k in task if k.startswith(("ansible.", "tnwks_"))), None)


@pytest.mark.parametrize("role", [WINDOWS, DISTRO], ids=["windows", "distro"])
def test_each_task_that_can_change_a_host_is_guarded_and_in_the_drift_list(role):
    tasks = flat(load(role / "tasks/main.yml"))
    (drift,) = [t for t in tasks if t.get("name") == "No drift of the user of the host server"]
    assert drift["ansible.builtin.assert"]["that"] == "host_mcp_agent_drift | length == 0 or (host_mcp_agent_apply | bool)"
    assert "-e host_mcp_agent_apply=true" in drift["ansible.builtin.assert"]["fail_msg"]
    listed = drift["vars"]["host_mcp_agent_drift"]
    guarded = []
    for task in tasks:
        name = module(task)
        if name is None or name in NO_CHANGE:
            continue
        if task.get("check_mode") is False:
            # A read: it runs in each mode and reports no change.
            script = (task.get("ansible.windows.win_powershell") or {}).get("script", "")
            assert task.get("changed_when") is False or "$Ansible.Changed = $false" in script, task["name"]
            continue
        assert task.get("check_mode") == GUARD, task["name"]
        guarded.append(task)
    assert guarded
    for task in guarded:
        if "register" in task:
            assert f"{task['register']} is changed" in listed, task["name"]
    # The drift assert comes after each guarded task, and before the reads of the checks.
    names = [t.get("name") for t in tasks]
    assert max(names.index(t["name"]) for t in guarded) < names.index(drift["name"])


def test_the_read_tasks_of_the_windows_role_change_nothing():
    for task in flat(load(WINDOWS / "tasks/main.yml")):
        if task.get("check_mode") is False:
            script = task["ansible.windows.win_powershell"]["script"]
            assert "$Ansible.Changed = $false" in script and "ShouldProcess" not in script, task["name"]


def test_the_flag_is_off_in_git_and_a_check_run_is_check_only():
    common = load(COMMON)
    assert common["host_mcp_agent_apply"] is False
    assert common["host_mcp_agent_check_only"] == "{{ ansible_check_mode or not (host_mcp_agent_apply | bool) }}"
    # No other plain file of the repository turns the flag on.
    for path in ANSIBLE.rglob("*.yml"):
        if ".sops." in path.name or path == COMMON:
            continue
        assert "host_mcp_agent_apply: true" not in path.read_text(), path


def test_the_comment_of_the_distro_account_is_one_that_useradd_takes():
    # useradd refuses a ":" or a newline in the comment field; the container test found it.
    comment = load(DISTRO / "defaults/main.yml")["host_mcp_agent_description"]
    assert ":" not in comment and "\n" not in comment


def test_the_distro_role_waits_for_the_account_by_the_guard_not_by_the_check_mode_of_the_run():
    (ready,) = [t for t in flat(load(DISTRO / "tasks/main.yml"))
                if t.get("name") == "The account exists, or this run makes it"]
    assert "host_mcp_agent_check_only" in ready["ansible.builtin.set_fact"]["host_mcp_agent_ready"]
    assert "ansible_check_mode" not in (DISTRO / "tasks/main.yml").read_text()


# The container test.

LAB = {
    "host_mcp_agent_user": "agent1",
    "host_mcp_agent_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGxhYmxhYmxhYmxhYmxhYmxhYmxhYmxhYmxhYmxhYmxh lab",
    "host_mcp_agent_source_distro": "198.51.100.0/24",
    "wsl_distro_user": "adult1",
    "wsl_distro_home": "/home/adult1",
}
STUBS = """set -e
cat > /usr/sbin/sshd <<'EOF'
#!/bin/sh
# The SSH server of the distro, for sshd -T: key sign-in.
echo "pubkeyauthentication yes"
echo "authorizedkeysfile .ssh/authorized_keys"
EOF
cat > /usr/local/bin/sudo <<'EOF'
#!/bin/sh
# sudo -n -l -U <user>: no rule.
echo "User $5 is not allowed to run sudo on lab."
exit 1
EOF
chmod 755 /usr/sbin/sshd /usr/local/bin/sudo
# The account of the distro, its home closed to others as on the distro (mode 0750).
useradd -m adult1 && chmod 750 /home/adult1
"""
# What the run must not change, when it changes nothing: the accounts and the homes.
STATE = "cat /etc/passwd /etc/shadow /etc/group; find /home -printf '%p %u %g %m %s\\n' | sort; " \
        "cat /home/agent1/.ssh/authorized_keys 2>/dev/null || true"


def docker():
    if shutil.which("docker"):
        return "docker"
    if os.environ.get("CI"):
        pytest.fail("Docker is needed to run the role of the distro in a container")
    pytest.skip("docker not installed")


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    tool = docker()
    work = tmp_path_factory.mktemp("guard")
    entry = next(r for p in load(ANSIBLE / "playbooks/wsl-distro.yml") for r in p["roles"]
                 if isinstance(r, dict) and r.get("role") == "wsl_host_mcp_user")
    play = [{"name": "lab", "hosts": "lab", "gather_facts": False,
             "roles": [{"role": entry["role"], "when": entry["when"]}]}]
    (work / "play.yml").write_text(yaml.safe_dump(play, sort_keys=False))
    inventory = {"all": {"hosts": {"lab": {"ansible_connection": "local",
                                           "ansible_python_interpreter": "{{ ansible_playbook_python }}",
                                           "ansible_become_method": "su"}}}}
    (work / "hosts.yml").write_text(yaml.safe_dump(inventory))
    (work / "ansible.cfg").write_text("[defaults]\nretry_files_enabled = false\nnocolor = true\n"
                                      "roles_path = /w/infrastructure/ansible/roles\n")
    (work / "values.json").write_text(json.dumps(LAB))
    for p in work.iterdir():
        p.chmod(0o644)
    work.chmod(0o755)
    name = f"host-mcp-guard-{uuid.uuid4().hex[:8]}"
    options = ["-v", f"{ROOT}:/w:ro", "-v", f"{work}:/lab:ro", "-e", "UV_PROJECT_ENVIRONMENT=/opt/venv",
               "-e", "ANSIBLE_CONFIG=/lab/ansible.cfg", "-e", "HOME=/root"]
    cache = os.environ.get("HOST_MCP_GUARD_UV_CACHE")
    network = ["--network", "none"] if cache else []
    if cache:
        options += ["-v", f"{cache}:/uvc", "-e", "UV_CACHE_DIR=/uvc", "-e", "UV_OFFLINE=1"]
    subprocess.run([tool, "run", "-d", "--name", name, *network, *options, UV_IMAGE, "sleep", "infinity"],
                   check=True, capture_output=True, text=True, timeout=600)
    try:
        def sh(script: str, check: bool = True) -> subprocess.CompletedProcess:
            result = subprocess.run([tool, "exec", name, "sh", "-c", script], capture_output=True, text=True,
                                    timeout=900, check=False)
            if check:
                assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
            return result
        sh(STUBS)
        sh("uv sync -q --locked --project /w/infrastructure/ansible --link-mode=copy")

        def play_(enabled: bool, apply: bool) -> subprocess.CompletedProcess:
            extra = f"-e host_mcp_agent_enabled={str(enabled).lower()}"
            if apply:
                extra += " -e host_mcp_agent_apply=true"
            return sh("cd /tmp && /opt/venv/bin/ansible-playbook -i /lab/hosts.yml /lab/play.yml "
                      f"-e @/w/infrastructure/ansible/inventory/group_vars/all/host_mcp.yml -e @/lab/values.json {extra}",
                      check=False)
        yield {"sh": sh, "play": play_}
    finally:
        subprocess.run([tool, "rm", "-f", name], capture_output=True, check=False)


def recap(run: subprocess.CompletedProcess) -> dict[str, int]:
    (line,) = re.findall(r"^lab\s+:(.*)$", run.stdout, re.MULTILINE)
    return {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", line)}


DRIFT = "Drift of the user of the host server"


def test_the_states_that_a_ci_apply_from_main_meets(lab):
    sh, play = lab["sh"], lab["play"]
    before = sh(STATE).stdout

    # The switch off: the role is skipped.
    off = play(enabled=False, apply=False)
    assert off.returncode == 0, off.stdout[-3000:]
    assert recap(off)["changed"] == 0 and DRIFT not in off.stdout
    assert sh(STATE).stdout == before

    # The switch on and no user, as a CI apply before the owner's run: drift, no change.
    missing = play(enabled=True, apply=False)
    assert missing.returncode != 0
    assert f"{DRIFT}: the account; its password field. " in missing.stdout, missing.stdout[-3000:]
    assert recap(missing)["failed"] == 1
    assert sh(STATE).stdout == before

    # The owner's run, with the flag: the user, its home and its key file.
    made = play(enabled=True, apply=True)
    assert made.returncode == 0, made.stdout[-4000:]
    assert recap(made)["changed"] > 0 and recap(made)["failed"] == 0
    key = sh("cat /home/agent1/.ssh/authorized_keys").stdout
    assert key == f'from="{LAB["host_mcp_agent_source_distro"]}",restrict {LAB["host_mcp_agent_public_key"]}\n'
    assert sh("getent shadow agent1 | cut -d: -f2").stdout == "*\n"
    after = sh(STATE).stdout

    # The switch on and the user present and equal, as each later CI run: changed=0.
    equal = play(enabled=True, apply=False)
    assert equal.returncode == 0, equal.stdout[-4000:]
    assert recap(equal)["changed"] == 0 and DRIFT not in equal.stdout
    assert sh(STATE).stdout == after

    # The key file different: drift, and the file stays as it is.
    sh("printf 'other\\n' > /home/agent1/.ssh/authorized_keys")
    changed = sh(STATE).stdout
    differ = play(enabled=True, apply=False)
    assert differ.returncode != 0
    assert f"{DRIFT}: its key file. " in differ.stdout, differ.stdout[-3000:]
    assert recap(differ)["failed"] == 1
    assert sh(STATE).stdout == changed
