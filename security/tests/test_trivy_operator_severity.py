"""The severities that Trivy Operator collects, and the ones that the rules read
(kubernetes/apps/security/trivy-operator/README.md, "Severities")."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
VALUES = yaml.safe_load((ROOT / "kubernetes/apps/security/trivy-operator/app/helmrelease.yaml").read_text())["spec"]["values"]
RULES = yaml.safe_load((ROOT / "kubernetes/apps/monitoring/observability-evidence/rules/rules.yaml").read_text())
EXPRS = [" ".join(rule["expr"].split()) for group in RULES["spec"]["groups"] for rule in group["rules"]]
# The default of trivy.severity in the chart (deploy/helm/values.yaml of the release).
CHART_SEVERITY = "UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL"
# The series of the reports of images, and the records made from them (observability/rules.py).
IMAGE_SERIES = r"(?:trivy_image_vulnerabilities|homelab:security_trivy_reports(?:_of_running)?:max)"


def matched(selectors):
    """The severities that label matchers read: = and =~, in upper case."""
    found = set()
    for value in re.findall(r'severity="([^"]*)"', selectors):
        found.add(value)
    for value in re.findall(r'severity=~"([^"]*)"', selectors):
        found.update(value.split("|"))
    return {value.upper() for value in found}


def read_by_rules():
    """Each severity that a rule reads: a matcher on the label severity, or a
    value that label_replace takes from it."""
    found = set()
    for expr in EXPRS:
        found |= matched(expr)
        found |= {value.upper() for value in re.findall(r'"severity", "[^"]*", "severity", "([^"]+)"', expr)}
    return found


def test_trivy_collects_each_severity_that_a_rule_reads():
    """The operator drops each config audit, RBAC and infra result whose
    severity trivy.severity does not list, and k8s-cis is built from them: a
    narrower key turns a control that fails into a pass."""
    collected = set(VALUES["trivy"].get("severity", CHART_SEVERITY).split(","))
    read = read_by_rules()
    assert {"CRITICAL", "HIGH", "MEDIUM", "LOW"} <= read, read
    assert read <= collected, read - collected
    assert collected == set(CHART_SEVERITY.split(","))


def test_the_ignore_policy_keeps_each_severity_that_a_rule_reads_of_an_image():
    """The policy drops the vulnerabilities that are not critical from a report,
    so a rule may read the critical count of an image only."""
    policy = VALUES["trivy"]["ignorePolicy"]
    assert "package trivy" in policy
    assert "input.VulnerabilityID" in policy
    kept = re.findall(r'input\.Severity != "([A-Z]+)"', policy)
    assert kept == ["CRITICAL"], kept
    read = set()
    for expr in EXPRS:
        for selectors in re.findall(IMAGE_SERIES + r"\{([^}]*)\}", expr):
            read |= matched(selectors)
    assert read == {"CRITICAL"}, read


def docker():
    """Docker, or a skip with its reason. CI has Docker, so there the test runs."""
    if shutil.which("docker"):
        return "docker"
    if os.environ.get("CI"):
        pytest.fail("Docker is needed to compile the ignore policy of Trivy Operator")
    pytest.skip("docker not installed: the ignore policy of Trivy Operator is not compiled")


def trivy_image():
    image = VALUES["trivy"]["image"]
    return f'{image["registry"]}/{image["repository"]}:{image["tag"]}'


def scan_with_policy(policy):
    """Trivy of the pinned image, with no network, scans a file with one fake
    secret and the policy. A policy is compiled only for a result to filter,
    so the scan needs a finding. The secret is built here, not written in the
    repository."""
    binary = docker()
    work = Path(tempfile.mkdtemp(prefix="trivy-policy-"))
    try:
        (work / "policy.rego").write_text(policy)
        (work / "data").mkdir()
        # The form of a GitHub personal token: its prefix and 36 letters and digits.
        (work / "data/found.txt").write_text("gh" + "p_" + "Ab1" * 12 + "\n")
        subprocess.run(["chmod", "-R", "a+rX", str(work)], check=True)
        return subprocess.run([
            binary, "run", "--rm", "--network", "none",
            "--mount", f"type=bind,src={work},dst=/t,readonly",
            trivy_image(), "fs", "/t/data", "--scanners", "secret", "--cache-backend", "memory",
            "--cache-dir", "/tmp/trivy", "--skip-version-check", "--disable-telemetry",
            "--format", "json", "--quiet", "--ignore-policy", "/t/policy.rego",
        ], capture_output=True, text=True, timeout=600, check=False)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_the_ignore_policy_compiles_and_keeps_a_finding_that_is_not_a_vulnerability():
    """A policy that does not compile fails each scan that has a result to
    filter, on both clusters (exit 1, "failed to apply the policy")."""
    result = scan_with_policy(VALUES["trivy"]["ignorePolicy"])
    assert result.returncode == 0, result.stderr[-4000:]
    report = json.loads(result.stdout)
    secrets = [secret for found in report.get("Results", []) for secret in found.get("Secrets", [])]
    assert len(secrets) == 1, result.stdout[-4000:]


def test_a_policy_that_does_not_compile_fails_the_scan():
    """The test above would see a broken policy."""
    broken = VALUES["trivy"]["ignorePolicy"].rstrip().removesuffix("}")
    result = scan_with_policy(broken)
    assert result.returncode == 1, result.stdout[-2000:] + result.stderr[-2000:]
    assert "failed to apply the policy" in result.stderr, result.stderr[-4000:]
