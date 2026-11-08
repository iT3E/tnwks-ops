"""The visual vocabulary: state words and colours, table headers, empty-state wording."""

from __future__ import annotations

from grafana_foundation_sdk.models import dashboardv2 as model

STATE = {
    "0": model.ValueMappingResult(text="Unknown", color="#6b6b6b"),
    "1": model.ValueMappingResult(text="Healthy", color="#1a7f37"),
    "2": model.ValueMappingResult(text="Degraded", color="#e5ac0e"),
    "3": model.ValueMappingResult(text="Failed", color="#c4302b"),
    "4": model.ValueMappingResult(text="Standby", color="#2f6bd0"),
    "5": model.ValueMappingResult(text="Not applicable", color="#6b6b6b"),
    "6": model.ValueMappingResult(text="Maintenance", color="#8a4fb5"),
}


# Table headers for the labels the boards return (live, 2026-11-07). A label
# without one shows its raw name; test_design fails on that.
LABELS = {
    "k8s_cluster": "Site", "app": "App", "db_cluster": "Database", "observer_cluster": "Observer",
    "alertname": "Alert", "severity": "Severity", "workload": "Workload", "tier": "Tier",
    "engine": "Engine", "cronjob": "Job", "kubernetes_node": "Node", "node": "Node", "hop": "Hop",
    "vip": "Shared VIP", "k8s_app": "Component", "component": "Component",
    "target_cluster": "Remote site", "served_cluster": "Served by", "routerboard_name": "Router",
    "version": "RouterOS", "name": "Name", "pool": "Pool", "current_firmware": "Installed firmware",
    "upgrade_firmware": "Available firmware", "host": "Host", "nic": "Adapter",
    "ap_name": "Access point", "resource": "Resource", "revision": "Revision",
    "app_version": "App version", "chart_version": "Chart", "release_status": "Release",
    "flux_kind": "Kind", "ready": "Ready", "kernel_version": "Kernel", "kubelet_version": "Kubelet",
    "os_image": "OS", "volume": "Volume", "collector": "Collector", "slo": "Objective",
}


# A state panel with no answer must not inherit a healthy colour: the base
# threshold is Unknown's grey (a stat with no value is painted with it).
UNKNOWN_GREY = "#6b6b6b"


NO_ALERTS = "None"


# An event list over the selected range: empty is the normal state. Whether the
# source is reporting shows on the Observability board, not here.
NO_EVENTS = "None in range"


# Table columns that describe the scrape, not the thing being reported.
SCRAPE_COLUMNS = ("__name__", "Time", "__replica__", "alertstate", "cluster", "container", "endpoint",
                  "instance", "job", "kind", "namespace", "observer_cluster", "pod", "prometheus",
                  "service")


# Each site's colour, on graphs with one series per site. It never means good or
# bad (that's STATE): blue and purple, both >= 3:1 on the dark and light themes.
SITES = {"homelab-wsl": "#3d71d9", "homelab-wsl-02": "#a352cc"}

# A number that isn't a state is the theme's text colour. A fixed blue on every
# KPI read as a link, and a large coloured number reads as an alarm.
NEUTRAL = "text"

# A tab, top to bottom (notes: "Making Grafana beautiful", 3.4): the answer first,
# then how it got here, then the detail. role -> (row title, or None for no
# header; most columns; row height; collapsed).
SECTIONS = {
    "headline": (None, 4, "short", False),
    "status": ("Status", 3, "standard", False),
    "trends": ("Trends", 2, "standard", False),
    "detail": ("Detail", 3, "standard", False),
    "about": ("About this view", 1, "short", True),
}

# Units that count something nonnegative: their axis starts at zero, so a 1% wobble
# isn't drawn as a cliff. Ratios also end at their whole.
ZERO_BASED = {"short", "s", "ms", "bytes", "decbytes", "Bps", "bps", "binBps", "ops", "reqps",
              "percentunit", "percent", "Mbits"}
WHOLE = {"percentunit": 1, "percent": 100}
