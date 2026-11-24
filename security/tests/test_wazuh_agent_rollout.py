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
STANDING = {"windows-host.yml", "wsl-distro.yml"}


def test_hosts_workflow_reaches_only_the_approved_windows_agent():
    workflow = (ROOT / ".github/workflows/ansible-hosts.yaml").read_text()
    assert all(f"playbooks/{play}" in workflow for play in STANDING)
    assert "wazuh-agents.yml" not in workflow
    for path in [*ANSIBLE.glob("playbooks/*.yml"), *ANSIBLE.glob("roles/**/*.yml")]:
        if path.name in {"wazuh-agents.yml", "windows-host.yml"} or AGENT_ROLES.intersection(path.parts):
            continue
        text = path.read_text()
        assert "wazuh-agents.yml" not in text, path
        assert all(role not in text for role in AGENT_ROLES), path
    windows_playbook = (ANSIBLE / "playbooks/windows-host.yml").read_text()
    assert windows_playbook.count("windows_wazuh_agent") == 1
    assert "wsl_wazuh_agent" not in windows_playbook
    assert "wazuh-agents.yml" not in windows_playbook
    plays = yaml.safe_load(windows_playbook)
    (windows_play,) = [play for play in plays if play["hosts"] == "windows_hosts"]
    (agent,) = [role for role in windows_play["roles"] if role["role"] == "windows_wazuh_agent"]
    assert agent["when"] == "windows_wazuh_rollout_name is defined"
    assert agent["vars"] == {
        "wazuh_rollout_host": "{{ inventory_hostname }}",
        "wazuh_agent_name": "{{ windows_wazuh_rollout_name }}",
    }
    opt_ins = {}
    for path in ANSIBLE.glob("inventory/**/*.yml"):
        if not path.name.endswith(".sops.yml"):
            variables = yaml.safe_load(path.read_text()) or {}
            if "windows_wazuh_rollout_name" in variables:
                opt_ins[path.relative_to(ANSIBLE).as_posix()] = variables["windows_wazuh_rollout_name"]
    assert opt_ins == {"inventory/host_vars/DESKTOP-HKE05KI/main.yml": "windows-site1"}
    windows = role_tasks("windows_wazuh_agent", "main.yml")
    assert "ansible_play_hosts_all == [inventory_hostname]" in windows[0]["ansible.builtin.assert"]["that"]
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
