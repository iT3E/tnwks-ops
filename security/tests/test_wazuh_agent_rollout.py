"""Standing host workflows reach agents only through inventory opt-ins."""
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import yaml

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
GROUPS = ROOT / "kubernetes/apps/security/wazuh/agent-groups"
AGENT_ROLES = {"windows_wazuh_agent", "wsl_wazuh_agent"}
STANDING = {
    "windows-host.yml": ("windows_hosts", "windows_wazuh_agent", "windows_wazuh_rollout_name"),
    "wsl-distro.yml": ("wsl_distros", "wsl_wazuh_agent", "wsl_wazuh_rollout_name"),
}
OPT_INS = {
    "inventory/host_vars/DESKTOP-HKE05KI/main.yml": {"windows_wazuh_rollout_name": "windows-site1"},
    "inventory/host_vars/WSL-02/main.yml": {"windows_wazuh_rollout_name": "windows-site2"},
    "inventory/host_vars/WSL-02-ubuntu/main.yml": {"wsl_wazuh_rollout_name": "wsl-site2"},
}


def test_hosts_workflow_reaches_only_approved_endpoints():
    workflow = (ROOT / ".github/workflows/ansible-hosts.yaml").read_text()
    assert all(f"playbooks/{play}" in workflow for play in STANDING)
    assert "wazuh-agents.yml" not in workflow
    for path in [*ANSIBLE.glob("playbooks/*.yml"), *ANSIBLE.glob("roles/**/*.yml")]:
        if path.name in {"wazuh-agents.yml", *STANDING} or AGENT_ROLES.intersection(path.parts):
            continue
        text = path.read_text()
        assert "wazuh-agents.yml" not in text, path
        assert all(role not in text for role in AGENT_ROLES), path
    for playbook, (group, role, flag) in STANDING.items():
        text = (ANSIBLE / "playbooks" / playbook).read_text()
        assert text.count(role) == 1
        assert all(other not in text for other in AGENT_ROLES - {role})
        assert "wazuh-agents.yml" not in text
        (play,) = [play for play in yaml.safe_load(text) if play["hosts"] == group]
        (agent,) = [entry for entry in play["roles"] if entry["role"] == role]
        assert agent["when"] == f"{flag} is defined"
        assert agent["vars"] == {
            "wazuh_rollout_host": "{{ inventory_hostname }}",
            "wazuh_agent_name": "{{ " + flag + " }}",
        }
        assert any(
            "ansible_play_hosts_all == [inventory_hostname]" in task["ansible.builtin.assert"]["that"]
            for task in role_tasks(role, "main.yml") if "ansible.builtin.assert" in task
        )

    opt_ins = {}
    flags = {flag for _, _, flag in STANDING.values()}
    for path in ANSIBLE.glob("inventory/**/*.yml"):
        if not path.name.endswith(".sops.yml"):
            variables = yaml.safe_load(path.read_text()) or {}
            enabled = {flag: variables[flag] for flag in flags if flag in variables}
            if enabled:
                opt_ins[path.relative_to(ANSIBLE).as_posix()] = enabled
    assert opt_ins == OPT_INS
    plays = yaml.safe_load((ANSIBLE / "playbooks/wazuh-agents.yml").read_text())
    assert plays[0]["any_errors_fatal"]
    assert {role for play in plays for role in play.get("roles", [])} == AGENT_ROLES


def test_both_endpoint_groups_disable_response_and_file_content_collection():
    for group in ("windows-host", "wsl-distro"):
        config = ElementTree.parse(GROUPS / group / "agent.conf")
        assert config.findtext("active-response/disabled") == "yes"
        assert config.find(".//*[@report_changes='yes']") is None
        assert config.find(".//command") is None


def role_tasks(role, name):
    return yaml.safe_load((ANSIBLE / "roles" / role / "tasks" / name).read_text())


def test_both_agent_templates_leave_an_endpoint_for_rc2_token_enrollment():
    for role in AGENT_ROLES:
        template = ANSIBLE / "roles" / role / "templates/ossec.conf.j2"
        config = ElementTree.parse(template)
        assert config.findtext("agent/manager/endpoint") == "MANAGER_IP"


def test_windows_role_uses_the_rc2_service_and_can_remove_the_exact_package():
    main = role_tasks("windows_wazuh_agent", "main.yml")
    present = role_tasks("windows_wazuh_agent", "present.yml")
    assert next(t["ansible.windows.win_service_info"]["name"] for t in main
                if "ansible.windows.win_service_info" in t) == "WazuhSvc"
    assert next(t["ansible.windows.win_service"]["name"] for t in present
                if "ansible.windows.win_service" in t) == "WazuhSvc"
    (removal,) = [t for t in main if "ansible.windows.win_package" in t]
    assert removal["ansible.windows.win_package"] == {
        "product_id": "{2DADA9B4-C6FF-4B30-82B4-1E0154289EA2}",
        "provider": "msi",
        "state": "absent",
    }
    assert "'absent'" in removal["when"] and "'present'" in main[-1]["when"]
    assert main[-1]["ansible.builtin.include_tasks"] == "present.yml"
    assert "ansible.windows.win_powershell" in present[-2]
    (enrollment,) = [t for t in present if "block" in t]
    assert "not ansible_check_mode" in enrollment["when"]
    (token_copy,) = [t for t in enrollment["block"] if t.get("no_log") and "ansible.windows.win_copy" in t]
    assert token_copy["diff"] is False


def test_wsl_role_verifies_configuration_before_starting(tmp_path):
    main = role_tasks("wsl_wazuh_agent", "main.yml")
    present = role_tasks("wsl_wazuh_agent", "present.yml")
    assert any(t.get("ansible.builtin.apt") == {"name": "wazuh-agent", "state": "absent"} for t in main)
    assert "ansible.builtin.setup" in main[0]
    (enrollment,) = [t for t in present if "block" in t]
    assert "not ansible_check_mode" in enrollment["when"]
    (token_copy,) = [t for t in enrollment["block"] if t.get("no_log") and "ansible.builtin.copy" in t]
    assert token_copy["diff"] is False
    assert token_copy["ansible.builtin.copy"]["mode"] == "0600"
    verification = present[-2]["ansible.builtin.command"]["argv"][-1]
    script = verification.replace('Path("/var/ossec/etc")', f"Path({str(tmp_path)!r})")
    assert script != verification
    assert present[-1]["ansible.builtin.systemd_service"]["state"] == "started"
    config = tmp_path / "ossec.conf"
    options = tmp_path / "local_internal_options.conf"
    disabled = (
        "<ossec_config><agent><manager><endpoint>manager.test:1517</endpoint></manager></agent>"
        "<active-response><disabled>yes</disabled></active-response></ossec_config>"
    )
    safe = "logcollector.remote_commands=0\nwazuh_command.remote_commands=0\nsca.remote_commands=0\n"
    for xml, flags, expected in [
        (disabled, safe, 0),
        (disabled.replace("manager.test:1517", "MANAGER_IP"), safe, 1),
        (disabled.replace("<endpoint>manager.test:1517</endpoint>", ""), safe, 1),
        (disabled.replace("yes", "no"), safe, 1),
        (disabled, safe.replace("remote_commands=0", "remote_commands=1", 1), 1),
        (disabled, safe + "sca.remote_commands=0\n", 1),
        (disabled, "", 1),
    ]:
        config.write_text(xml)
        options.write_text(flags)
        result = subprocess.run([sys.executable, "-c", script], check=False, capture_output=True)
        assert result.returncode == expected, result.stderr
