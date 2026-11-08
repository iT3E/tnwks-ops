"""Panels more than one board shows."""

from __future__ import annotations

from ..model import Panel, p
from ..queries import metric, selector


def coverage() -> list[Panel]:
    expected = f'count(max by (k8s_cluster) ({selector("homelab_expected_site_info")}))'
    reporting = f'count(max by (k8s_cluster) ({selector("homelab:site_reporting")}) == 1) or vector(0)'
    return [
        p("Expected sites", expected,
          "short", "Declared inventory survives the disappearance of a source. This is expected scope, not health.", "stat"),
        p("Reporting sites", reporting, "short",
          "Distinct selected sites with fresh, successful evidence. Duplicate observations count once. Compare with Expected sites; partial results never certify fleet health.", "stat"),
        p("Site evidence", f'max by (k8s_cluster) ({metric("homelab:site_state")})', "coverage",
          "Measured site evidence, including explicit Unknown when an expected site stops reporting. Independent site probes distinguish unreachable from unobserved.", "table", "tnwks-sites"),
        p("Evidence age", f'time() - {metric("homelab_observer_last_success_timestamp_seconds")}', "s",
          "Age of the last successful Kubernetes inventory observation. A recent Prometheus scrape alone does not prove the collector is seeing its upstream.", "table", "tnwks-observability"),
    ]
