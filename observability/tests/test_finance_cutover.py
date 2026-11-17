"""The files of the cutover of the finance agent
(docs/apps/agent-platform-runbook.md, "The cutover of the finance agent"):
the inbox pod and the restore Job match the agent pod, and the steps of
kustomization.yaml never meet in a wrong order."""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/ai/agents/finance/agent/app"
CONFIG = ROOT / "kubernetes/apps/ai/agents/finance/agent/config/openclaw.json"
RUNBOOK = ROOT / "docs/apps/agent-platform-runbook.md"


def doc(name: str) -> dict:
    (only,) = [d for d in yaml.safe_load_all((APP / name).read_text()) if d]
    return only


def pod(name: str) -> dict:
    return doc(name)["spec"]["template"]["spec"]


def listed() -> set[str]:
    resources = yaml.safe_load((APP / "kustomization.yaml").read_text())["resources"]
    return {r.removeprefix("./") for r in resources}


AGENT = pod("deployment.yaml")


@pytest.mark.parametrize("name", ["state-inbox.yaml", "restore-job.yaml"])
def test_the_helper_pods_run_as_the_agent_on_its_node_and_volume(name):
    spec = pod(name)
    (container,) = spec["containers"]
    assert container["image"] == AGENT["containers"][0]["image"]
    assert spec["nodeSelector"] == AGENT["nodeSelector"]
    assert spec["securityContext"] == AGENT["securityContext"]
    claims = {v["persistentVolumeClaim"]["claimName"] for v in spec["volumes"] if "persistentVolumeClaim" in v}
    assert claims == {"agent-state"}
    (data,) = [m for m in container["volumeMounts"] if m["name"] in {"state", "data"}]
    assert data["mountPath"] == "/home/node/data"
    assert spec["automountServiceAccountToken"] is False


@pytest.mark.parametrize("name", ["state-inbox.yaml", "restore-job.yaml"])
def test_the_helper_pods_hold_no_secret_and_no_network(name):
    spec = pod(name)
    text = (APP / name).read_text()
    assert "secretRef" not in text and "secretKeyRef" not in text
    assert not any("secret" in v for v in spec["volumes"])
    # The policy of the agent selects app.kubernetes.io/name: agent; the
    # helpers carry another name, so the default deny holds for them.
    labels = doc(name)["spec"]["template"]["metadata"]["labels"]
    assert labels["app.kubernetes.io/name"] != "agent"
    # Flux substitutes ${...} in this directory.
    assert "${" not in text


def test_the_restore_job_is_the_one_of_the_runbook():
    block = RUNBOOK.read_text().split("### The restore Job", 1)[1].split("```yaml", 1)[1].split("```", 1)[0]
    runbook = yaml.safe_load(block)["spec"]["template"]["spec"]["containers"][0]["args"]
    assert pod("restore-job.yaml")["containers"][0]["args"] == runbook


def test_the_secret_holds_each_value_the_config_names():
    names = set(re.findall(r"\$\{([A-Z0-9_]+)\}", CONFIG.read_text()))
    secret = set(doc("secret.sops.yaml")["stringData"])
    search = set(doc("secret-search.sops.yaml")["stringData"])
    assert names <= secret | search
    assert secret == {"OPENCLAW_GATEWAY_TOKEN", "LITELLM_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOW_FROM"}
    assert json.loads(CONFIG.read_text())["memory"]["search"]["remote"]["apiKey"]["id"] in secret


def test_the_steps_never_meet_in_a_wrong_order():
    now = listed()
    replicas = doc("deployment.yaml")["spec"]["replicas"]
    if "backup.yaml" in now:
        assert "pvc.yaml" in now
    if "deployment.yaml" in now:
        # Step 4: the inbox is out, and the Secrets are in.
        assert "state-inbox.yaml" not in now
        assert {"pvc.yaml", "backup.yaml", "secret.sops.yaml", "secret-search.sops.yaml"} <= now
    if replicas:
        # Step 5: the restore Job is out before the agent starts.
        assert "deployment.yaml" in now and "restore-job.yaml" not in now
    if "restore-job.yaml" in now:
        assert replicas == 0 and "state-inbox.yaml" not in now
        assert "SET-ME" not in (APP / "restore-job.yaml").read_text()
