# Operating observability

How to reach the observability stack from each site, recover it, review it and
drill it. What's deployed and why is in
[`observability-implementation.md`](observability-implementation.md). Delivery
of alerts themselves is in [`external-monitoring.md`](external-monitoring.md).

## Access per site

Names on the shared VIPs (`dns-sites.tf`) follow the app between sites. Every
other `*.<internal domain>` name resolves through the router's wildcard to
**homelab-wsl's** ingress only. So site 2's own UIs have no name, and you reach
them from site 2's distro with `kubectl port-forward`.

| What | Normal | Site 1 down | Site 2 down |
| --- | --- | --- | --- |
| Grafana | `grafana.<internal>`, run by homelab-wsl | Fail over (below), then sign in as the local admin. oauth2-proxy runs on site 1. | Unaffected. The "Site 2" datasource errors and the Global one answers partially, with a warning. |
| Metrics | Global datasource: each site's Thanos Query, partial with a warning when a site is gone | Grafana on site 2 reads its own Prometheus (Local) and the archive | Site 1's own 15 days. Older history is unavailable, because the archive is on WSL-02. |
| Alerts | Each site's Alertmanager sends to Telegram; healthchecks.io notices silence | Site 2's Alertmanager still sends. Its UI is the Alertmanager datasource once Grafana runs on site 2. | Site 1's is unaffected |
| SLOs | `pyrra.<internal>` (site 1's Pyrra) | Site 2's Pyrra keeps evaluating; `port-forward svc/pyrra-ui 9099` from site 2 | Unaffected |
| Logs | Loki on site 1 | None: site 2 has no Loki | Unaffected |
| Archive | Garage, Store and Compactor on site 2 | Unaffected | Gone until site 2 returns; the archive alerts fire and the archive panels read Unknown |

Site 2 from a workstation: `observability/.cache/remote.py` runs a command on
WSL-02, which has that cluster's kubeconfig
(`export KUBECONFIG=$HOME/.kube/config-homelab-wsl-02;` inside the command).

## Recovery

- **Grafana:** a warm app. The failover and the sign-in limits with site 1 down
  are in "Grafana on both sites" in the implementation doc. Its database moves
  with it, and the provisioned boards are already on both sites.
- **Site 1 Prometheus volume:** see "Site 1 Prometheus volume" in the
  implementation doc. Without the mount, only Prometheus stops.
- **Archive behind or full:** `ObservabilityArchiveBehind` and
  `ObservabilityArchiveSpaceLow`. Check the Compactor first: whether it has
  halted (`thanos_compact_halted`) and how many compactions it has left. Then
  check the sidecars' upload errors. `observability/verify_live_archive.py`
  proves Store-only history per site (read-only).
- **Evidence collector:** stateless. A restart loses nothing, and its panels
  read Unknown until it reports again. Peer-dependent prerequisites, such as
  the fence, need the other site's collector too.
- **Missing sources:** `observability/audit_sources.py --kubeconfig … --output …`
  lists every panel source per site, read-only. Any empty source that isn't
  planned in `observability/contracts.py` is a gap.

## Review

**Quarterly:**

1. Run `audit_sources.py` against each site. Every empty source must be a
   planned one.
2. On the Observability board, check Objectives: window coverage, error
   budget, and any SLO without data.
3. List alerts by notifications sent over the quarter (Alertmanager / Overview,
   or `alertmanager_notifications_total`). Fix or retire the noisy ones, and
   look again at silences older than a week.
4. Confirm that the standby-restore drill succeeds weekly (Databases & Backups →
   Weekly PVC drill age) and that every warm app's recovery prerequisites
   pass.
5. Re-read the panel catalogue
   ([`observability-panel-catalogue.md`](observability-panel-catalogue.md))
   against the boards. Remove panels nobody used.

**After an incident:**

1. Did an alert fire, and did its link open the right board, scoped to the
   site, at the incident time?
2. Did any panel read Healthy while it wasn't, or Unknown when the evidence was
   there?
3. Turn each miss into a promtool fixture (`observability/rules.py`) or a test,
   then fix it. Update the runbook the alert links to.

## Resilience drills

Scheduled separately from changes. Each one disrupts a site, so it waits for an
agreed time.

| Drill | When | Passes when |
| --- | --- | --- |
| Standby restore | Automatic, Sundays 05:15 (`standby-restore` CronJob) | Every warm app's restore prerequisite is Healthy and the drill age is under 8 days |
| Grafana failover and break-glass | Agreed time | Grafana is served from site 2 with its boards, the local admin signs in, and it moves back |
| Site loss | Agreed time | The header counts one reporting site of two. The lost site reads Unknown, not Healthy. Alerts arrive from the surviving site, and healthchecks.io reports the lost one. |
| Partition (site tunnel down) | Agreed time | The Network board shows the tunnel hop failed, and each site keeps reporting itself |

Record each run below: date, drill, outcome and the follow-ups it produced.

| Date | Drill | Outcome |
| --- | --- | --- |
