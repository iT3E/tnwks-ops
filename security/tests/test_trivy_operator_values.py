"""The values of Trivy Operator that keep each running image with a report
(kubernetes/apps/security/trivy-operator/README.md, "When a scan fails")."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
VALUES = yaml.safe_load((ROOT / "kubernetes/apps/security/trivy-operator/app/helmrelease.yaml").read_text())["spec"]["values"]
RULES = (ROOT / "kubernetes/apps/monitoring/observability-evidence/rules/rules.yaml").read_text()


def test_the_scan_cache_is_in_memory_and_the_databases_on_the_volume():
    """The containers of one scan job run at the same time: a file scan cache
    admits one of them, and the others stop with no report."""
    trivy = VALUES["trivy"]
    assert trivy["configFile"]["cache"] == {"backend": "memory"}
    mount = VALUES["trivyOperator"]["scanJobCustomVolumesMount"]
    assert trivy["imageScanCacheDir"] in [entry["mountPath"] for entry in mount]


def test_a_report_holds_the_severity_that_the_rules_read_and_no_other():
    """With every severity, a report can be larger than one object of the API
    server. The rules read the critical count only."""
    assert VALUES["trivy"]["severity"] == "CRITICAL"
    assert VALUES["trivy"]["ignoreUnfixed"] is True
    read = set(re.findall(r'severity="([A-Za-z]+)"', RULES))
    assert read == {"Critical"}, read
