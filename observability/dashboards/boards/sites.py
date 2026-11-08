"""Sites & Failover (L2): Which site serves each workload, and what recovery evidence is missing?"""

from __future__ import annotations

from ..design import NO_EVENTS
from ..model import Board, p
from ..queries import changes, metric, since
from .common import coverage


def board() -> Board:
    return Board("tnwks-sites", "Sites & Failover", "sites", "l2",
              "Which site serves each workload, and what recovery evidence is missing?", {
            "Sites": coverage() + [
                p("Ready nodes", f'sum by (k8s_cluster) ({metric("kube_node_status_condition", "condition=\"Ready\",status=\"true\"")})', "short",
                  "Ready nodes per Kubernetes cluster. These nested Talos nodes share the Windows/WSL host's physical CPU, RAM and disk."),
                p("Cross-site observation", metric("homelab_peer_probe_success"), "bool",
                  "Fresh bounded probes from one site to the other. Observer identity is retained; this is connectivity evidence, not the remote site's internal condition.", "table", words=("Observed", "Not observed")),
            ],
            "Placement": [
                p("Workload state", metric("homelab:workload_state"), "state",
                  "Per app and site, from replica counts: ready, degraded, failed, or a deliberate standby at zero replicas. A site that stops reporting shows Unknown.", "table"),
                p("Declared placement", metric("homelab_expected_workload_info"), "short",
                  "Expected app/site/tier inventory independent of discovery. Standbys and hardware-pinned applications remain visible.", "table", column=None),
                p("Desired replicas", metric("homelab_workload_desired_replicas"), "short",
                  "Effective workload desired replicas from the local Kubernetes API, separate from git's default placement.", "table"),
                p("Ready replicas", metric("homelab_workload_ready_replicas"), "short",
                  "Observed readiness. Ready replicas do not prove the global ingress is serving this site.", "table"),
                p("Runtime overrides present", metric("homelab_site_override_present"), "bool",
                  "Presence of flux-system/site-overrides. Raw contents and secret-like values are never exposed.", "table", column="Overrides", words=("Present", "None")),
                p("Override creation age", f'time() - {metric("homelab_site_override_created_timestamp_seconds")}', "s",
                  "Age of creation, not last modification. Consult structured operation events for when a particular override changed.", "table",
                  empty="No overrides"),
            ],
            "Recovery": [
                p("Readiness evidence", metric("homelab:recovery_state"), "state",
                  "Unknown is retained whenever fencing, backup or restore evidence is absent. This is not a safe-to-promote guarantee.", "table"),
                p("Fencing evidence age", f'time() - {metric("homelab_fence_last_verified_timestamp_seconds")}', "s",
                  "Present only for verified fence evidence. DNS ownership alone cannot establish that the former primary is unable to write.", "table"),
                p("Fence job age", f'time() - {metric("homelab_fence_job_last_success_timestamp_seconds")}', "s",
                  "Last successful fleet fence Job. It may succeed with unreadable leases, so this never establishes per-app fencing.", "table"),
                p("Lease authority reachable", metric("homelab_lease_authority_reachable"), "bool",
                  "The current TXT lookup returned a recognized site or none. Reachability does not prove a former primary was fenced.", "table", column="Lease authority", words=("Reachable", "Unreachable")),
                p("Lease blocks this site", metric("homelab_site_lease_blocks_local"), "bool",
                  "One means the lease prohibits this site from serving the app. Zero alone cannot authorize promotion.", "table", column="Lease", words=("Blocks this site", "Clear")),
                p("Last restore evidence", f'time() - {metric("homelab_restore_last_success_timestamp_seconds")}', "s",
                  "Per-app restore evidence, not one successful fleet CronJob. Missing per-app results remain Unknown.", "table"),
            ],
            "History": [
                p("Serving-state changes", changes("homelab:service_state", "app"), "short",
                  "How often each app's observed state at a site changed in the range: a failover shows as a change on both sites. Sampled at each rule evaluation, so a blip shorter than that can be missed.",
                  "table", empty=NO_EVENTS),
                p("Shared VIP route changes", changes("homelab:shared_vip_route", "vip"), "short",
                  "How often the router's inferred route for each shared VIP to each site changed in the range, from its BGP sessions and preferences. Sampled at each rule evaluation.",
                  "table", empty=NO_EVENTS),
                p("Workload availability transitions", since("homelab_workload_transition_timestamp_seconds", "app, workload"), "dateTimeAsIso",
                  "When each tracked workload's Available condition last changed in the range, from Kubernetes: a scale to or from zero, or a rollout that lost availability.",
                  "table", empty=NO_EVENTS),
            ],
        }, "Sites & Failover")
