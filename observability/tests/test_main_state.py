"""The first steps of the cutover of the main agent
(docs/apps/agent-platform-runbook.md, "The cutover of the main agent"):
the state volume, its inbox and its backup policy, in the order of the
runbook, and the same shape as the finance agent.

The inbox is held to the finance agent: the same image, user and node, as
the two agent pods run on one worker. The pod of the main agent and the
later steps are in test_main_agent.py.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/ai/agents/main/agent/app"
FINANCE = ROOT / "kubernetes/apps/ai/agents/finance/agent/app"
REPOSITORY = ROOT / "kubernetes/apps/kopiur-system/agents-main-repository/app/clusterrepository.yaml"


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def doc(path: Path) -> dict:
    (only,) = docs(path)
    return only


def listed() -> set[str]:
    resources = yaml.safe_load((APP / "kustomization.yaml").read_text())["resources"]
    return {r.removeprefix("./") for r in resources}


def test_the_inbox_runs_as_the_agent_on_its_node_and_volume():
    agent = doc(FINANCE / "deployment.yaml")["spec"]["template"]["spec"]
    spec = doc(APP / "state-inbox.yaml")["spec"]["template"]["spec"]
    (container,) = spec["containers"]
    assert container["image"] == agent["containers"][0]["image"]
    assert spec["nodeSelector"] == agent["nodeSelector"]
    assert spec["securityContext"] == agent["securityContext"]
    claims = {v["persistentVolumeClaim"]["claimName"] for v in spec["volumes"] if "persistentVolumeClaim" in v}
    assert claims == {"agent-state"}
    (data,) = [m for m in container["volumeMounts"] if m["name"] == "state"]
    assert data["mountPath"] == "/home/node/data"
    assert spec["automountServiceAccountToken"] is False


def test_the_inbox_holds_no_secret_and_no_network():
    text = (APP / "state-inbox.yaml").read_text()
    inbox = doc(APP / "state-inbox.yaml")
    assert "secretRef" not in text and "secretKeyRef" not in text
    assert not any("secret" in v for v in inbox["spec"]["template"]["spec"]["volumes"])
    # No policy of the namespace selects this label, so the default deny
    # holds.
    assert inbox["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/name"] != "agent"
    assert "${" not in text


def test_the_volume_is_never_pruned_and_matches_finance():
    main = doc(APP / "pvc.yaml")
    assert main["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"] == "disabled"
    assert main["spec"] == doc(FINANCE / "pvc.yaml")["spec"]


def test_the_backup_policy_is_the_one_of_finance_in_the_repository_of_main():
    main = docs(APP / "backup.yaml")
    finance = docs(FINANCE / "backup.yaml")
    assert [d["kind"] for d in main] == [d["kind"] for d in finance]
    assert main[0]["spec"]["repository"] == {"kind": "ClusterRepository", "name": "agents-main"}
    main[0]["spec"]["repository"] = finance[0]["spec"]["repository"]
    assert [d["spec"] for d in main] == [d["spec"] for d in finance]


def test_the_steps_never_meet_in_a_wrong_order():
    now = listed()
    if "state-inbox.yaml" in now:
        assert "pvc.yaml" in now
    if "backup.yaml" in now:
        # Step 2: after the volume, and only to a repository that is in git
        # and grants this namespace.
        assert "pvc.yaml" in now
        assert REPOSITORY.exists()
        allowed = doc(REPOSITORY)["spec"]["allowedNamespaces"]["list"]
        assert "agents-main" in allowed
        # The repository is applied: listed in its directory, and the
        # directory listed for Flux.
        repository_app = yaml.safe_load((REPOSITORY.parent / "kustomization.yaml").read_text())["resources"]
        assert "./clusterrepository.yaml" in repository_app
        apps = yaml.safe_load((ROOT / "kubernetes/clusters/wsl/apps/kustomization.yaml").read_text())["resources"]
        assert "../../../apps/kopiur-system/agents-main-repository/ks.yaml" in apps
