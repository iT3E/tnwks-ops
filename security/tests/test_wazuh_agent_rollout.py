"""The standing host workflow cannot reach the staged agent roles."""
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
