"""The standing host workflow cannot reach the staged agent roles."""
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


def test_agent_rollout_is_separate_from_the_hosts_workflow():
    workflow = (ROOT / ".github/workflows/ansible-hosts.yaml").read_text()
    assert all(f"playbooks/{play}" in workflow for play in STANDING)
    assert "wazuh-agents.yml" not in workflow
    for path in [*ANSIBLE.glob("playbooks/*.yml"), *ANSIBLE.glob("roles/**/*.yml")]:
        if path.name == "wazuh-agents.yml" or AGENT_ROLES.intersection(path.parts):
            continue
        text = path.read_text()
        assert "wazuh-agents.yml" not in text, path
        assert all(role not in text for role in AGENT_ROLES), path
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
