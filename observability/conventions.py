"""Shared operator evidence and navigation conventions."""

OBSERVATION_BUDGET_SECONDS = 180
RUNBOOKS = {
    "overview": "docs/wsl/multi-site.md",
    "sites": "docs/wsl/multi-site.md",
    "data": "docs/operations/backups.md",
    "network": "docs/network/architecture.md",
    "kubernetes": "docs/operations/cluster-upgrades.md",
    "apps": "docs/network/access.md",
    "home": "docs/apps/home-assistant.md",
    "hosts": "docs/operations/grafana-resource-budget.md",
    "observability": "docs/operations/observability-implementation.md",
}
PANEL_RUNBOOKS = {
    "Windows disk free": "docs/operations/grafana-resource-budget.md",
    "Windows available memory": "docs/operations/grafana-resource-budget.md",
    "Disk temperature": "docs/operations/grafana-resource-budget.md",
    "Notification failures": "docs/operations/external-monitoring.md",
    "Failed notifications": "docs/operations/external-monitoring.md",
    "AP uptime": "docs/network/unifi.md",
    "AP uplink": "docs/network/unifi.md",
    "Wi-Fi collector failures": "docs/network/unifi.md",
}


def runbook_path(domain, panel=None):
    return PANEL_RUNBOOKS.get(panel, RUNBOOKS[domain])


def dashboard_link(uid):
    # A full UID/slug route also works on cold navigation in the pinned browser.
    # Grafana canonicalizes the placeholder slug; the UID remains authoritative.
    return (f'/d/{uid}/view?from=${{__from}}&to=${{__to}}'
            '&${cluster:queryparam}&var-datasource=${datasource:percentencode}')
