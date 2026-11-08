"""PromQL building blocks: site scope, series identity, alert and SLO selectors, entity links."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


CLUSTERS = ("homelab-wsl", "homelab-wsl-02")


SCOPE = 'k8s_cluster=~"$cluster"'


ALERT_SCOPE = 'cluster=~"$cluster"'


# Each site's collector and rules report on both sites, so the global view returns
# these once per observer. max keeps the worst state, and Unknown (0) yields to a
# real answer. Keys are the series identity without the observing Prometheus.
IDENTITY = {
    "homelab:service_state": ("app",),
    "homelab:workload_state": ("app",),
    "homelab:recovery_state": ("app",),
    "homelab:network_path_state": ("hop",),
    "homelab:ingress_state": ("name",),
    "homelab:ingress_cert_remaining_seconds": ("name",),
    "homelab:shared_vip_route": ("vip",),
    "homelab:database_backup_state": ("namespace", "db_cluster"),
    "homelab:app_rpo_state": ("app",),
    "homelab:other_database_state": ("workload", "engine"),
    "homelab:app_rto_state": ("app",),
    "homelab:app_backup_age_seconds": ("app",),
    "homelab_expected_database_info": ("namespace", "db_cluster", "app"),
    "homelab_expected_workload_info": ("app", "tier", "kind", "workload"),
}


def alerts(state: str) -> str:
    """Real alerts by the site that raised them; heartbeats carry severity none."""
    selector = f'ALERTS{{{ALERT_SCOPE},alertstate="{state}",severity!="none"}}'
    return ('label_replace(max by (cluster, alertname, severity) '
            f'({selector}), "k8s_cluster", "$1", "cluster", "(.+)")')


def selector(name: str, extra: str = "") -> str:
    if name.startswith(("unpoller_", "cnpg_")):
        extra = 'source_site=""' + ("," + extra if extra else "")
    return f"{name}{{{SCOPE}{',' + extra if extra else ''}}}"


def metric(name: str, extra: str = "") -> str:
    keys = IDENTITY.get(name)
    if keys is None:
        return selector(name, extra)
    return f"max by (k8s_cluster, {', '.join(keys)}) ({selector(name, extra)})"


def since(name: str, by: str) -> str:
    """Events kept as series whose value is the event time (from the API, not the
    scrape): the latest per label set in the selected range, in ms for Grafana."""
    return (f'max by (k8s_cluster, {by}) (max_over_time({selector(name)}[$__range])) * 1000 > $__from')


def changes(name: str, by: str) -> str:
    """Sampled state changes in the selected range, rows with none dropped."""
    return f'max by (k8s_cluster, {by}) (changes({selector(name)}[$__range])) > 0'


LONG_WINDOW_SECONDS = 28 * 86400


def slo_indicators() -> list[tuple[str, str]]:
    """(name, bool_gauge selector) for each Pyrra SLO, from the deployed definitions."""
    docs = yaml.safe_load_all((ROOT / "kubernetes/apps/monitoring/pyrra/app/slos.yaml").read_text())
    return [(doc["metadata"]["name"], doc["spec"]["indicator"]["bool_gauge"]["metric"])
            for doc in docs if doc and doc.get("kind") == "ServiceLevelObjective"]


def per_slo(template: str) -> str:
    """One series per site and SLO: the template on its selector scoped to $cluster, labelled slo."""
    parts = []
    for name, metric_selector in slo_indicators():
        scoped = metric_selector.replace("{", "{" + SCOPE + ",", 1)
        parts.append(f'label_replace({template.format(sel=scoped)}, "slo", "{name}", "", "")')
    return " or ".join(parts)


# Entity boards (pinned upstream, chart mixins and the kept classic boards),
# opened on the clicked series: domain -> entity.
NGINX = ("nginx", {"ingress": "ingress"})


NODE = ("200ac8fdbfbb74b39aff88118e4d1c2c", {"cluster": "k8s_cluster", "node": "kubernetes_node"})


# The diagnose boards (L3, boards/host.py, wifi_clients.py, wan_clients.py).
HOST = ("tnwks-host", {"host": "instance"})
WIFI_CLIENT = ("tnwks-wifi-clients", {"client": "name"})
WAN_CLIENT = ("tnwks-wan-clients", {"client": "client"})


ACCESS_POINT = ("home-wifi", {"ap": "name"})


# Boards not generated here, by UID: the words a link to them uses.
OTHER_BOARDS = {NGINX[0]: "NGINX", NODE[0]: "Node (Pods)", ACCESS_POINT[0]: "Home Wi-Fi"}


# A list that spans areas (Homelab's Needs attention) opens each row's own board,
# from the row's hidden `board` column.
ROW_BOARD = "${__data.fields.board}"


# The site 2 host, a UniFi Wi-Fi client (its Windows computer name, as in the
# Ansible inventory). No household device is named in the repository; the Wi-Fi
# clients board reads names from UniFi at runtime.
SITE2_WIFI_CLIENT = "WSL-02"


def host_cpu_busy() -> str:
    return f'1 - avg by (k8s_cluster, instance) (rate({selector("windows_cpu_time_total", 'mode="idle"')}[$__rate_interval]))'


def host(name: str, extra: str = "") -> str:
    """A Windows exporter series for the picked host (the Host board's $host)."""
    return selector(name, 'instance=~"$host"' + ("," + extra if extra else ""))


def wifi(name: str, extra: str = "") -> str:
    """A UniFi client series, the poller's own copy (not the other site's federated one).
    One controller sees the whole house, so these aren't per site: no site scope."""
    return f'{name}{{source_site="",wired="false"{"," + extra if extra else ""}}}'


def band(expr: str) -> str:
    """UniFi's radio codes as the band a person would name."""
    for code, words in (("ng", "2.4 GHz"), ("na", "5 GHz"), ("6e", "6 GHz")):
        expr = f'label_replace({expr}, "radio", "{words}", "radio", "{code}")'
    return expr


def wan(direction: str, picked: bool = False) -> str:
    """Per-client WAN bits/s from flow export (wan-latency.yaml rules), every device or
    the picked ones ($client). The rules sum away the site label and only site 1's
    router exports flows: no site scope."""
    return f'wan:client_app_{direction}_bits:rate2m' + ('{client=~"$client"}' if picked else "")


def worst(*sources: str) -> str:
    """One state for many: Failed > Degraded > Unknown > Healthy over every source's
    states. Standby, Not applicable and Maintenance don't count against it. Each
    source is tagged, so `or` keeps series whose labels match another source's."""
    tagged = " or ".join(f'label_replace(({source}) != 4 != 5 != 6, "source", "{i}", "", "")'
                         for i, source in enumerate(sources))
    states = f"({tagged})"
    rank = (f"max(({states} == bool 3) * 4 + ({states} == bool 2) * 3"
            f" + ({states} == bool 0) * 2 + ({states} == bool 1))")
    return f"(({rank}) == bool 4) * 3 + (({rank}) == bool 3) * 2 + (({rank}) == bool 1)"


def site_state() -> str:
    """Each site's evidence once: the other observer's row for it is Unknown by design."""
    return f'max by (k8s_cluster) ({selector("homelab:site_state")})'


def house(domain: str) -> str:
    """A site's own household domain state (house_records), scoped like an alert."""
    return f'homelab:house_state{{domain="{domain}",{ALERT_SCOPE}}}'
