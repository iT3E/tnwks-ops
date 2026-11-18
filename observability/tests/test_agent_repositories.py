"""The Kopia repositories of the agent trust domains (docs/operations/backups.md,
"The finance repository" and "The main repository"): one repository for each
trust domain, each with its own prefix, role, session, password and
refresher, and no prefix that starts with another one.

The repository of a trust domain is a directory
kubernetes/apps/kopiur-system/agents-<domain>-repository/. The pilot's
repository, agents-repository/, is the first one and has older names.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
KOPIUR = ROOT / "kubernetes/apps/kopiur-system"
MAIN_TF = ROOT / "infrastructure/terraform/environments/prod/aws/main.tf"
SETTINGS = ROOT / "kubernetes/clusters/wsl/cluster-settings.yaml"
APPS = ROOT / "kubernetes/clusters/wsl/apps/kustomization.yaml"
DOMAINS = sorted(p.name.removesuffix("-repository").removeprefix("agents-")
                 for p in KOPIUR.glob("agents-*-repository"))


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def doc(path: Path) -> dict:
    (only,) = docs(path)
    return only


def terraform_entries() -> dict[str, dict]:
    """The entries of rolesanywhere_backups_prefixed in main.tf."""
    block = MAIN_TF.read_text().split("rolesanywhere_backups_prefixed = {", 1)[1]
    entries = {}
    for name, body in re.findall(r'"([a-z-]+)" = \{(.*?)\n    \}', block, re.DOTALL):
        prefix = re.search(r'prefix\s*=\s*"([^"]+)"', body)
        names = re.search(r'common_names\s*=\s*\["([^"]+)"\]', body)
        if prefix and names:
            entries[name] = {"prefix": prefix.group(1), "cn": names.group(1),
                             "exclusive": "exclusive    = true" in body or "exclusive = true" in body}
    return entries


def test_there_is_a_repository_for_finance_and_main():
    assert {"finance", "main"} <= set(DOMAINS)


@pytest.mark.parametrize("domain", DOMAINS)
def test_the_repository_matches_its_terraform_entry(domain):
    app = KOPIUR / f"agents-{domain}-repository/app"
    repository = doc(app / "clusterrepository.yaml")
    entry = terraform_entries()[f"agents-{domain}"]
    assert entry["exclusive"]
    assert repository["metadata"]["name"] == f"agents-{domain}"
    assert repository["spec"]["backend"]["s3"]["prefix"] == entry["prefix"]
    # One repository for each trust domain: its own domain, and the
    # namespace of its maintenance.
    assert repository["spec"]["allowedNamespaces"]["list"] == ["kopiur-system", f"agents-{domain}"]
    assert repository["spec"]["backend"]["s3"]["auth"]["secretRef"]["name"] == f"aws-backup-sts-agents-{domain}"
    assert repository["spec"]["encryption"]["passwordSecretRef"]["name"] == f"kopiur-repository-agents-{domain}"


@pytest.mark.parametrize("domain", DOMAINS)
def test_the_refresher_uses_its_own_certificate_role_and_session(domain):
    app = KOPIUR / f"agents-{domain}-repository/app"
    cron = doc(app / "cronjob.yaml")
    spec = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    env = {e["name"]: e["value"] for e in spec["containers"][0]["env"]}
    key = f"RA_AGENTS_{domain.upper()}_BACKUPS"
    assert env["PROFILE_ARN"] == "${" + key + "_PROFILE_ARN}"
    assert env["ROLE_ARN"] == "${" + key + "_ROLE_ARN}"
    assert env["SECRET_NAME"] == f"aws-backup-sts-agents-{domain}"
    assert env["TARGET_NAMESPACES"] == "kopiur-system"
    (certificate,) = [v for v in spec["volumes"] if "secret" in v]
    assert certificate["secret"]["secretName"] == f"aws-backup-certificate-agents-{domain}"
    assert spec["serviceAccountName"] == f"aws-backup-credentials-agents-{domain}"
    settings = doc(SETTINGS)["data"]
    assert f"{key}_PROFILE_ARN" in settings and f"{key}_ROLE_ARN" in settings
    # The comment of the cronjob names the CN of the terraform entry.
    assert terraform_entries()[f"agents-{domain}"]["cn"] in (app / "cronjob.yaml").read_text()


@pytest.mark.parametrize("domain", DOMAINS)
def test_the_refresher_may_patch_its_own_session_only(domain):
    app = KOPIUR / f"agents-{domain}-repository/app"
    (role,) = [d for d in docs(app / "rbac.yaml") if d["kind"] == "Role"]
    (rule,) = role["rules"]
    assert rule["resourceNames"] == [f"aws-backup-sts-agents-{domain}"]
    assert rule["verbs"] == ["get", "patch"]


def test_the_refreshers_and_the_maintenance_run_at_their_own_minutes():
    crons = [doc(KOPIUR / f"agents-{d}-repository/app/cronjob.yaml")["spec"]["schedule"] for d in DOMAINS]
    crons.append(doc(KOPIUR / "agents-repository/app/cronjob.yaml")["spec"]["schedule"])
    assert len(set(crons)) == len(crons)
    quick = [doc(KOPIUR / f"agents-{d}-repository/app/clusterrepository.yaml")["spec"]["maintenance"]["schedule"]
             ["quick"]["cron"] for d in DOMAINS]
    assert len(set(quick)) == len(quick)


def test_no_prefix_starts_with_another_one():
    prefixes = [e["prefix"] for e in terraform_entries().values()]
    for a in prefixes:
        for b in prefixes:
            assert a == b or not b.startswith(a.removesuffix("/"))


@pytest.mark.parametrize("domain", DOMAINS)
def test_the_repository_is_listed_only_with_real_settings_and_its_secrets(domain):
    """A listed repository directory has its two SOPS files and settings
    that are not placeholders; a listed ClusterRepository is in a listed
    directory."""
    app = KOPIUR / f"agents-{domain}-repository/app"
    ks = f"../../../apps/kopiur-system/agents-{domain}-repository/ks.yaml"
    resources = yaml.safe_load(APPS.read_text())["resources"]
    if ks not in resources:
        # The ClusterRepository comes after the session (step 4 after step 3).
        listed_here = yaml.safe_load((app / "kustomization.yaml").read_text())["resources"]
        assert "./clusterrepository.yaml" not in listed_here
        return
    for name in ("secret-certificate.sops.yaml", "secret-password.sops.yaml"):
        assert (app / name).exists(), name
    key = f"RA_AGENTS_{domain.upper()}_BACKUPS"
    settings = doc(SETTINGS)["data"]
    assert settings[f"{key}_PROFILE_ARN"].startswith("arn:aws:rolesanywhere:")
    assert settings[f"{key}_ROLE_ARN"].startswith("arn:aws:iam:")
