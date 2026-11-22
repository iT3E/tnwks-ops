"""The guard of the user of the host server: Ansible changes a host only with
-e host_mcp_agent_apply=true (docs/wsl/host-mcp-user.md, "The guard").

The static tests read the two roles: each task that can change a host runs in check mode
unless host_mcp_agent_apply is true, and each is in the list of the drift assert, which
fails a run that finds a difference and has no flag.

The container test runs the role of the distro with Ansible itself in a Debian container
with no network. As on the distro, Ansible connects as an admin that is not root, becomes
root with a password, and has no ACL tool. It meets the states that a CI apply from main
can meet:

- the switch off: the role is skipped, and nothing changes;
- the switch on, the user missing, no flag: the run fails with the drift message, and
  nothing changes, in a check run (the nightly check) and in an apply;
- the switch on, the user missing, a check run with the flag (step 1 of the owner): it
  shows the account as a change, passes, and changes nothing;
- the switch on, the flag (the owner's run): the user is made;
- the switch on, the user present and equal: the checks pass with changed=0, in an apply
  and a check run with no flag, and in a check run with the flag;
- the switch on, the key file different, no flag: the run fails with the drift message,
  and the file stays as it was;
- a drop to the account that fails, or that stays root: the run stops at the check of
  the drop, so a read that did not run as the account can not pass as "read none".

The SSH server and sudo of the container are stubs that answer as the distro does (key
sign-in, no sudo rule). The test needs Docker and the uv image: without Docker it is
skipped, except in CI. Ansible comes from the lock of infrastructure/ansible; for a run
with no network, HOST_MCP_GUARD_UV_CACHE names a uv cache that holds it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
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
    # No other file of the Ansible tree defines either one, with any value: a group
    # or host variables file, a SOPS file (its key names are plain text), a role or a
    # playbook. Only the key names are matched; no value is read or printed.
    key = re.compile(r"^\s*-?\s*[\"']?host_mcp_agent_(apply|check_only)[\"']?\s*:", re.MULTILINE)
    found = [str(path.relative_to(ANSIBLE)) for path in sorted(ANSIBLE.rglob("*"))
             if path.is_file() and path.suffix in {".yml", ".yaml", ".json"} and path != COMMON
             and "/.venv/" not in str(path) and key.search(path.read_text(errors="replace"))]
    assert found == [], f"files that define the flag of the guard: {found}"


def test_no_task_becomes_the_account_and_each_read_as_it_drops_with_runuser():
    """A become_user of an account that is not root makes Ansible share its
    module files with it, which fails on the distro: the connection user is
    an admin that is not root and no ACL tool is installed. So no task of the
    two roles becomes another user, and the reads as the account run as root
    and drop with runuser, after a check that the drop gives the account."""
    for role in (WINDOWS, DISTRO):
        for task in flat(load(role / "tasks/main.yml")):
            assert "become_user" not in task and "ansible_become_user" not in (task.get("vars") or {}), task["name"]
    tasks = {t["name"]: t for t in flat(load(DISTRO / "tasks/main.yml"))}
    names = list(tasks)
    who = tasks["The reads below run as the account"]["ansible.builtin.command"]["argv"]
    read = tasks["Try a read of each private path as the account, and of one control"]
    assert who == ["runuser", "-u", "{{ host_mcp_agent_user }}", "--", "id", "-un"]
    assert read["ansible.builtin.command"]["argv"][:4] == ["runuser", "-u", "{{ host_mcp_agent_user }}", "--"]
    assert read["loop"].startswith("{{ [host_mcp_agent_read_control] + ")
    assert names.index("The reads below run as the account") < names.index(read["name"])


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
# The connection user: an admin that is not root and becomes root with a
# password, as on the distro. Its home is not under /home, so that its
# Ansible files are not in the state that the test compares. No ACL tool is
# installed, as on the distro.
useradd -m -d /var/lib/operator1 operator1
echo "root:$LAB_BECOME" | chpasswd
if command -v setfacl; then exit 1; fi
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
    (distro,) = load(ANSIBLE / "playbooks/wsl-distro.yml")
    play = [{"name": "lab", "hosts": "lab", "gather_facts": False, "become": distro["become"],
             "roles": [{"role": entry["role"], "when": entry["when"]}]}]
    (work / "play.yml").write_text(yaml.safe_dump(play, sort_keys=False))
    become = secrets.token_hex(16)
    inventory = {"all": {"hosts": {"lab": {"ansible_connection": "local",
                                           "ansible_python_interpreter": "{{ ansible_playbook_python }}",
                                           "ansible_become_method": "su",
                                           # Made for this run; the lab has no other use for it.
                                           "ansible_become_password": become}}}}
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
        def sh(script: str, check: bool = True, user: str = "root") -> subprocess.CompletedProcess:
            home = ["-e", "HOME=/var/lib/operator1"] if user == "operator1" else []
            result = subprocess.run([tool, "exec", "--user", user, *home, name, "sh", "-c", script],
                                    capture_output=True, text=True, timeout=900, check=False)
            if check:
                assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
            return result
        sh(f"LAB_BECOME={become}\n{STUBS}")
        sh("uv sync -q --locked --project /w/infrastructure/ansible --link-mode=copy")

        def play_(enabled: bool, apply: bool, check: bool = False) -> subprocess.CompletedProcess:
            extra = f"-e host_mcp_agent_enabled={str(enabled).lower()}"
            if apply:
                extra += " -e host_mcp_agent_apply=true"
            if check:
                extra += " --check --diff"
            return sh("cd /tmp && /opt/venv/bin/ansible-playbook -i /lab/hosts.yml /lab/play.yml "
                      f"-e @/w/infrastructure/ansible/inventory/group_vars/all/host_mcp.yml -e @/lab/values.json {extra}",
                      check=False, user="operator1")
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

    # The switch on and no user, as the nightly check of CI (--check, no flag): drift, no change.
    nightly = play(enabled=True, apply=False, check=True)
    assert nightly.returncode != 0
    assert f"{DRIFT}: the account; its password field. " in nightly.stdout, nightly.stdout[-3000:]
    assert sh(STATE).stdout == before

    # Step 1 of the owner (--check with the flag): it shows the account, and passes.
    preview = play(enabled=True, apply=True, check=True)
    assert preview.returncode == 0, preview.stdout[-3000:]
    assert recap(preview)["changed"] == 1 and DRIFT not in preview.stdout
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
    # The nightly check, and step 1 of the owner, after his run: changed=0.
    for check in (play(enabled=True, apply=False, check=True), play(enabled=True, apply=True, check=True)):
        assert check.returncode == 0, check.stdout[-3000:]
        assert recap(check)["changed"] == 0 and DRIFT not in check.stdout
    assert sh(STATE).stdout == after

    # A drop to the account that fails, or that does not drop: the run stops
    # at the check of the drop, and a failed read can not pass as "read none".
    sh("cp /usr/sbin/runuser /usr/sbin/runuser.lab")
    for stub, label in (("exit 1", "a drop that fails"), ('shift 3; exec "$@"', "a drop that stays root")):
        sh(f"printf '#!/bin/sh\\n{stub}\\n' > /usr/sbin/runuser && chmod 755 /usr/sbin/runuser")
        failed = play(enabled=True, apply=False)
        assert failed.returncode != 0, label
        stop = failed.stdout.index("fatal: [lab]")
        assert "The reads below run as the account]" in failed.stdout[failed.stdout.rfind("TASK [", 0, stop):stop], label
        assert recap(failed)["failed"] == 1, label
        assert sh(STATE).stdout == after, label
    sh("mv /usr/sbin/runuser.lab /usr/sbin/runuser")

    # The key file different: drift, and the file stays as it is.
    sh("printf 'other\\n' > /home/agent1/.ssh/authorized_keys")
    changed = sh(STATE).stdout
    differ = play(enabled=True, apply=False)
    assert differ.returncode != 0
    assert f"{DRIFT}: its key file. " in differ.stdout, differ.stdout[-3000:]
    assert recap(differ)["failed"] == 1
    assert sh(STATE).stdout == changed
