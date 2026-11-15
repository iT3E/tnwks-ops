"""The severities that Trivy Operator collects, and the ones that the rules read
(kubernetes/apps/security/trivy-operator/README.md, "Severities")."""

import re
from pathlib import Path

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
