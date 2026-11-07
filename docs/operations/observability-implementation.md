# Observability implementation

Implementation worktree: `feat/observability-platform`. The approved design and
resource decisions are in the Grafana design, budget and object-storage notes.

The network migration agent owns routing, Cilium, the cluster network settings
and v2.0.0. This branch does not merge, deploy, change those files or edit
release/version files while that work is ongoing. Activation patches that touch
cluster aggregators or telemetry switches are prepared separately and must be
rebased and rendered against the completed v2.0.0 network state.

## Delivery evidence

| Work | State | Evidence |
| --- | --- | --- |
| Review and measured budgets | Complete | Design, resource and local S3 notes |
| Pinned dashboard toolchain | In progress | `observability/pyproject.toml`, `uv.lock` |
| Inventory, label and unknown-state contracts | Pending | |
| Generated L0/L1/L2 dashboards and legacy navigation | Pending | |
| Recording/alert rules, fixtures and CI | Pending | |
| Garage/Thanos/Pyrra and evidence collector | Pending | |
| Persistent C: monitoring volume preparation | Pending | |
| Two independent Grafanas and upgrade rehearsal | Pending | |
| Local integration/browser and failure fixtures | Pending | |
| Refresh against v2.0.0 and cluster integration | Waiting for network migration | No production activation |

Nothing in the pending rows is deployed merely because source files exist.
