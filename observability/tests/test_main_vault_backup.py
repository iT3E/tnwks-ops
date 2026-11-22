"""The backup of the share of the main trust domain (task 3.37, step C;
docs/apps/agent-platform.md, "The vault shares"): the snapshot policy of the
finance share in agents-main, to the repository of main. Each share goes to
the repository of its own trust domain only (R6).
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "kubernetes/apps/ai/agents"


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def as_finance(node):
    return yaml.safe_load(yaml.safe_dump(node).replace("agents-main", "agents-finance")
                          .replace("agents/main/", "agents/finance/"))


def test_the_backup_of_main_is_the_backup_of_finance_in_its_trust_domain():
    for name in ("ks.yaml", "app/kustomization.yaml", "app/backup.yaml"):
        main = docs(AGENTS / "main/vault-backup" / name)
        finance = docs(AGENTS / "finance/vault-backup" / name)
        assert [as_finance(d) for d in main] == finance, name


def test_the_share_of_main_goes_to_the_repository_of_main_from_the_claim_to_read():
    (policy,) = [d for d in docs(AGENTS / "main/vault-backup/app/backup.yaml") if d["kind"] == "SnapshotPolicy"]
    assert policy["metadata"]["name"] == "vault"
    assert policy["spec"]["repository"] == {"kind": "ClusterRepository", "name": "agents-main"}
    assert policy["spec"]["sources"] == [{"pvc": {"name": "vault-ro"}}]
    (state,) = [d for d in docs(AGENTS / "main/agent/app/backup.yaml") if d["kind"] == "SnapshotPolicy"]
    assert state["spec"]["repository"] == policy["spec"]["repository"]
    (ks,) = docs(AGENTS / "main/vault-backup/ks.yaml")
    assert ks["spec"]["targetNamespace"] == "agents-main"
    assert ks["spec"]["dependsOn"] == [{"name": "agents-main-vault-share"},
                                       {"name": "kopiur-agents-main-repository"}]
    assert (ROOT / "kubernetes/apps/kopiur-system/agents-main-repository/ks.yaml").is_file()


def test_the_backup_is_listed_after_the_share():
    resources = yaml.safe_load((AGENTS / "main/kustomization.yaml").read_text())["resources"]
    assert resources.index("./vault-backup/ks.yaml") > resources.index("./vault-share/ks.yaml")
