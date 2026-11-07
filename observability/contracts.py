"""Deterministic panel-to-source, freshness and runbook catalogue."""

import json
import re
from pathlib import Path

from dashboards import catalogue
from conventions import OBSERVATION_BUDGET_SECONDS, runbook_path
from rules import records

ROOT = Path(__file__).resolve().parents[1]
PRODUCERS = {
    "homelab_": "read-only evidence collector",
    "probe_": "evidence collector probes",
    "cnpg_": "CloudNativePG PostgreSQL exporter",
    "barman_": "CNPG Barman Cloud plugin",
    "kube_": "kube-state-metrics",
    "node_": "node-exporter (shared WSL kernel)",
    "controller_runtime_": "Flux controller PodMonitors",
    "cilium_": "Cilium agent metrics",
    "hubble_": "Hubble metrics",
    "mktxp_": "router MKTXP exporter",
    "smokeping:": "WAN recording rules from smokeping-prober",
    "wan:": "WAN recording rules from router counters",
    "unms_": "UISP exporter",
    "unpoller_": "UniFi Poller (exclude federated copies)",
    "nginx_": "ingress-nginx controller",
    "certmanager_": "cert-manager",
    "cloudflared_": "cloudflared",
    "frigate_": "Frigate",
    "homeassistant_": "Home Assistant Prometheus integration",
    "windows_": "Windows exporter",
    "smartctl_": "SMART exporter",
    "prometheus_": "Prometheus self-monitoring",
    "alertmanager_": "Alertmanager self-monitoring",
    "thanos_": "Thanos component metrics",
    "pyrra_": "Pyrra SLO rules (kubernetes/apps/monitoring/pyrra/app/slos.yaml)",
    "garage_": "Garage admin metrics (WSL-02 archive)",
    "kopiur_": "Kopiur controller (PVC backups and restores)",
    "plausible_": "Plausible exporter (its stats and health API)",
    "ALERTS": "local Prometheus alert rules (current state)",
    "up": "Prometheus scrape status",
}
PLANNED = {
    "homelab_house_state": "the isolated household Grafana isn't deployed; the household view is Home Assistant's House systems (homelab:house_state)",
}


def metric_names(expression):
    """Extract selectors, not labels/functions/durations, from generated PromQL.

    Syntax and behavior are validated by promtool and the query/browser gates;
    this deliberately small lexer only indexes sources in the catalogue.
    """
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', expression)
    text = re.sub(r'\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)', '', text)
    text = re.sub(r'\{[^}]*\}', '', text)
    ignored = {"and", "or", "unless", "bool", "by", "without", "on", "ignoring", "offset",
               "group_left", "group_right"}
    return sorted({match[1] for match in re.finditer(r'(?<![\w:$])([A-Za-z_:][\w:]*)(?![\w:])', text)
                   if match[1] not in ignored and not text[match.end():].lstrip().startswith('(')})


def source_contract(name):
    if name in PLANNED:
        return {"metric": name, "producer": PLANNED[name], "availability": "planned"}
    producer = next((value for prefix, value in PRODUCERS.items()
                     if name == prefix or (prefix not in ("up", "ALERTS") and name.startswith(prefix))), None)
    if producer is None:
        raise ValueError(f"No source contract for {name}")
    return {"metric": name, "producer": producer, "availability": "configured"}


def panel_contracts():
    recording = records()

    def resolve(name, visited=()):
        if name in visited:
            raise ValueError(f"Recording rule cycle at {name}")
        if name not in recording:
            return {name}
        return set().union(*(resolve(dep, (*visited, name)) for dep in metric_names(recording[name])))

    result = []
    for board in catalogue():
        for tab, panels in board.tabs.items():
            for panel in panels:
                runbook = runbook_path(board.domain, panel.title)
                assert (ROOT / runbook).is_file(), runbook
                inputs = metric_names(panel.expr)
                assert inputs, (board.uid, panel.title)
                sources = sorted(set().union(*(resolve(name) for name in inputs)))
                result.append({
                    "dashboard": "house" if board.kiosk else board.uid, "tab": tab,
                    "panel": panel.title, "query": panel.expr, "unit": panel.unit,
                    "query_mode": "instant" if panel.kind in ("stat", "table") else "range",
                    "inputs": inputs, "sources": [source_contract(name) for name in sources],
                    "observation_budget_seconds": OBSERVATION_BUDGET_SECONDS,
                    "freshness_policy": "Interpret current state only with observations within 180s; range plots retain historical observations. Operational backup/restore objectives are separate from scrape freshness.",
                    "scope": "household aggregate only" if board.kiosk else "selected sites; explicitly described shared sources may span sites",
                    "missing": panel.empty, "query_error": "Grafana query error; never a healthy empty response",
                    "runbook": runbook, "owner": "homelab operator",
                })
    return result


def generated_contracts():
    panels = panel_contracts()
    lines = ["# Panel evidence catalogue", "", "Generated by `observability/generate.py`; edit the canonical Python definitions.", "",
             "Each row names the source metrics, observation budget and operator runbook. Project-owned recording rules are expanded; external rule families retain their own ownership. Configured means source wiring exists; it does not prove a live observation or a complete history window. Planned sources must remain Unknown. Range plots retain history; current-state freshness and operational backup/restore age are separate contracts.", "",
             "| Dashboard / tab / panel | Source metrics | Observation budget | Missing evidence | Runbook |",
             "| --- | --- | --- | --- | --- |"]
    for panel in panels:
        sources = "<br>".join(f"`{s['metric']}`" + (" (planned)" if s['availability'] == 'planned' else '') for s in panel['sources'])
        lines.append(f"| {panel['dashboard']} / {panel['tab']} / {panel['panel']} | {sources} | {panel['observation_budget_seconds']}s | {panel['missing']} | [Runbook](../../{panel['runbook']}) |")
    return {
        ROOT / "observability/generated/panel-contracts.json": json.dumps(panels, indent=2) + "\n",
        ROOT / "docs/operations/observability-panel-catalogue.md": "\n".join(lines) + "\n",
    }
