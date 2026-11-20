"""Renders values of the inventory with Ansible itself, on this host, with no host contact.

A test gives hosts with their variables (stand-ins for the encrypted ones), the host whose
values it renders, and Jinja expressions; it gets back each value as Ansible renders it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
UV = shutil.which("uv")
no_ansible = pytest.mark.skipif(UV is None and not os.environ.get("CI"),
                                reason="no uv on this workstation; CI runs these tests")


def load(*paths: str) -> dict:
    """The variables of each file, in order: a later file wins, as host vars win over group vars."""
    out: dict = {}
    for path in paths:
        out.update(yaml.safe_load((ANSIBLE / path).read_text()) or {})
    return out


def render(tmp_path: Path, hosts: dict[str, dict], targets: list[str], expressions: dict[str, str],
           extra: dict | None = None) -> dict[str, dict]:
    """Each expression (Jinja, with no braces) as Ansible renders it on each host of targets."""
    root = tmp_path / f"render-{uuid.uuid4().hex[:8]}"
    root.mkdir()
    (root / "ansible.cfg").write_text("[defaults]\nlocalhost_warning = false\nretry_files_enabled = false\n")
    # The values of the hosts, rendered here: the target runs locally, with a POSIX shell.
    local = {"ansible_connection": "local", "ansible_shell_type": "sh", "ansible_become": False,
             "ansible_python_interpreter": "{{ ansible_playbook_python }}"}
    inventory = {"all": {"hosts": {name: {**values, **(local if name in targets else {})}
                                   for name, values in hosts.items()}}}
    (root / "hosts.yml").write_text(yaml.safe_dump(inventory, sort_keys=False))
    content = "{{ {" + ", ".join(f"{json.dumps(k)}: ({v})" for k, v in expressions.items()) + "} | to_json }}"
    play = [{"name": "render", "hosts": ":".join(targets), "gather_facts": False, "tasks": [{
        "name": "Write the values", "ansible.builtin.copy": {
            "dest": str(root) + "/out-{{ inventory_hostname }}.json", "mode": "0600", "content": content}}]}]
    (root / "play.yml").write_text(yaml.safe_dump(play, sort_keys=False))
    env = {**os.environ, "ANSIBLE_CONFIG": str(root / "ansible.cfg"), "ANSIBLE_HOME": str(root / "home"),
           "ANSIBLE_NOCOLOR": "1"}
    argv = [UV or "uv", "run", "-q", "--project", str(ANSIBLE), "--locked", "ansible-playbook",
            "-i", str(root / "hosts.yml"), str(root / "play.yml")]
    for key, value in (extra or {}).items():
        argv += ["-e", json.dumps({key: value})]
    run = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=600, env=env)
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-4000:]
    return {name: json.loads((root / f"out-{name}.json").read_text()) for name in targets}
