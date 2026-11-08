"""Kubernetes & GitOps (L2): Is desired state reconciling, and which resource or controller is blocking it?"""

from __future__ import annotations

from ..design import NO_EVENTS
from ..model import Board, p
from ..queries import NODE, metric, selector, since


def board() -> Board:
    return Board("tnwks-kubernetes", "Kubernetes & GitOps", "kubernetes", "l2",
              "Is desired state reconciling, and which resource or controller is blocking it?", {
            "Reconciliation": [
                p("Flux resource readiness", metric("homelab_flux_resource_ready"), "bool",
                  "Current Ready conditions read from the API with revision/suspension shown separately. A live controller process can still fail reconciliation.", "table", column="Ready", words=("Ready", "Not ready")),
                p("Suspended resources", metric("homelab_flux_resource_suspended"), "bool",
                  "Declared suspension; do not report deliberate suspension as a controller crash.", "table", column="Reconciling", words=("Suspended", "Active")),
                p("Reconcile failures", f'sum by (k8s_cluster, controller) (rate({metric("controller_runtime_reconcile_total", "result=\"error\"")}[5m]))', "ops",
                  "Controller process metrics supplement the CR Ready state; controller PodMonitor access needs explicit network-policy integration."),
            ],
            "Workloads": [
                p("Unready workloads", " - ".join(
                    f'sum by (k8s_cluster, app) (max by (k8s_cluster, app, kind, workload) ({selector(name)}))'
                    for name in ("homelab_workload_desired_replicas", "homelab_workload_ready_replicas")), "short",
                  "Effective desired minus Ready per tracked app/site. A zero-replica standby is not a failed application."),
                p("Node CPU utilization", f'1 - avg by (k8s_cluster, instance, kubernetes_node) (rate({metric("node_cpu_seconds_total", "mode=\"idle\"")}[$__rate_interval]))', "percentunit",
                  "CPU busy fraction per nested node view. Do not sum shared WSL kernel capacity as independent physical nodes.", link=NODE),
                p("Node memory available", metric("node_memory_MemAvailable_bytes"), "bytes",
                  "Shared WSL kernel memory view on nested Talos nodes. Cross-check Windows physical available memory.", link=NODE),
            ],
            # Each site's Prometheus keeps these for 15 days and the archive for 35,
            # so the history reads locally on either site.
            "History": [
                p("Source revisions fetched", since("homelab_flux_source_revision_timestamp_seconds", "resource, revision"), "dateTimeAsIso",
                  "Each Git or OCI revision Flux fetched in the range and when, from the source's artifact. Fetched isn't applied: the Ready transitions of the Kustomizations that use it show the apply.",
                  "table", empty=NO_EVENTS),
                p("Helm releases deployed", since("homelab_flux_release_deployed_timestamp_seconds", "resource, chart_version, app_version, release_status"), "dateTimeAsIso",
                  "The latest install or upgrade of each HelmRelease in the range, from its release history, with the chart and app version and whether it deployed or failed. An upgrade that changed only values keeps its version.",
                  "table", empty=NO_EVENTS),
                p("Flux Ready transitions",
                  'label_replace(' + since("homelab_flux_ready_transition_timestamp_seconds", "kind, resource, ready")
                  + ', "flux_kind", "$1", "kind", "(.+)")', "dateTimeAsIso",
                  "When each Flux resource's Ready condition last changed in the range, per resulting state, from the API. Only the latest change per state is kept: a resource that flapped shows its last False and its last True.",
                  "table", empty=NO_EVENTS),
                p("Node versions", f'min by (k8s_cluster, node, os_image, kubelet_version, kernel_version) (min_over_time(timestamp({selector("kube_node_info")})[$__range:5m])) * 1000', "dateTimeAsIso",
                  "When each node's Talos, kubelet and kernel versions were first seen in the range, at 5-minute resolution. Two rows for one node mean it was upgraded, and the later row is when.",
                  "table"),
            ],
        }, "Kubernetes", sections=True)
