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
    "wifi": "docs/network/unifi.md",
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


def dashboard_link(uid, entity=None, table=False):
    """A drilldown that keeps the incident's time range and datasource.

    Without entity it keeps the selected sites (L1 -> domain). With entity, a
    {target variable: series label} map, it opens the entity board on the clicked
    series or row instead (domain -> entity): a table row's fields, a series'
    labels. A full UID/slug route also works on cold navigation in the pinned
    browser; Grafana canonicalizes the placeholder slug.
    """
    base = f'/d/{uid}/view?from=${{__from}}&to=${{__to}}'
    if not entity:
        return base + '&${cluster:queryparam}&var-datasource=${datasource:percentencode}'
    field = "__data.fields.{}" if table else "__field.labels.{}"
    return base + "&var-datasource=${datasource:percentencode}" + "".join(
        f"&var-{var}=${{{field.format(label)}:percentencode}}" for var, label in entity.items())
