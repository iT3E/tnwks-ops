"""The values of Trivy Operator that keep each running image with a report
(kubernetes/apps/security/trivy-operator/README.md, "When a scan fails")."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
VALUES = yaml.safe_load((ROOT / "kubernetes/apps/security/trivy-operator/app/helmrelease.yaml").read_text())["spec"]["values"]


def test_the_scan_cache_is_in_memory_and_the_databases_on_the_volume():
    """The containers of one scan job run at the same time: a file scan cache
    admits one of them, and the others stop with no report."""
    trivy = VALUES["trivy"]
    assert trivy["configFile"]["cache"] == {"backend": "memory"}
    mount = VALUES["trivyOperator"]["scanJobCustomVolumesMount"]
    assert trivy["imageScanCacheDir"] in [entry["mountPath"] for entry in mount]



def test_a_scan_reads_one_layer_at_a_time_under_its_limit():
    """Five layers at a time took a scan container to its limit of 512 MiB on
    large images, and it was killed with no report (README.md, "Memory").
    --slow (trivy.slow) no longer sets it in Trivy 0.76."""
    trivy = VALUES["trivy"]
    assert trivy["configFile"]["scan"]["parallel"] == 1
    assert mebibytes(trivy["resources"]["limits"]["memory"]) == 512


def mebibytes(quantity):
    """A memory quantity of Kubernetes or of GOMEMLIMIT, in MiB."""
    number, unit = re.fullmatch(r"(\d+)(Mi|MiB|Gi|GiB)", quantity).groups()
    return int(number) * (1024 if unit.startswith("G") else 1)


def test_the_operator_has_its_limit_and_gomemlimit_is_90_percent_of_it():
    """The limit of ../README.md, "Memory" (640 MiB since 2026-11-15), and the
    soft limit of the Go runtime at 90% of it (go.dev/doc/gc-guide)."""
    limit = mebibytes(VALUES["resources"]["limits"]["memory"])
    assert limit == 640
    env = {entry["name"]: entry["value"] for entry in VALUES["extraEnv"]}
    assert mebibytes(env["GOMEMLIMIT"]) == limit * 90 // 100
