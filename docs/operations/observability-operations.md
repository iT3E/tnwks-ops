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
| Logs | Each site's own Loki, 14 days: site 1 holds the router's syslog and its Job pods' logs, site 2 its Job pods' logs. Grafana's Loki datasource reads the site it runs on, so reach site 2's Loki from its distro with `kubectl port-forward` | Grafana on site 2 reads site 2's Loki. Site 1's logs are unavailable until it returns | Site 1's Loki is unaffected. Site 2's logs are unavailable until it returns |
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

6. Time every panel query (performance), and check contrast and what each
   panel can reveal (accessibility, privacy), as in the record below.

**Review record, 2026-11-07:**

- **Performance.** Every one of the 129 operator panel queries, as Grafana
  issues it for the default 6-hour view (all sites, Global datasource), returns
  from site 1's Thanos Query in under 0.5 s; the slowest is HTTP p95 duration
  (0.42 s, 32 series). Summed per board, the heaviest is Observability at 1.7 s,
  and Grafana runs a board's queries in parallel. Unready workloads plotted 334
  series over 6 h for 48 workloads, because each collector restart starts new
  series; it now sums per app and site.
- **Accessibility.** State and coverage values always show a word (Healthy,
  Failed, Unknown…), so no state depends on color. As colored text they failed
  WCAG AA on the light theme (Degraded 2.0:1, the others 3.0-4.3:1) and Failed
  was 4.0:1 on the dark one. State cells and stats now use a colored background,
  with a darker palette: Grafana writes white text on it at 5.1-5.5:1, and black
  on Degraded's amber at 10.2:1. Each cell keeps at least 3:1 against the dark
  page.
- **Privacy.** Operator panels aggregate cameras, Home Assistant entities and
  MQTT clients to counts, and the Wi-Fi panels name only access points. The
  pinned UniFi reference boards list client devices: acceptable on the signed-in
  operator Grafana, never on a household board.

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
| 2026-11-07 | Grafana failover and break-glass | Passed. `bin/site-failover` moved Grafana to site 2 in 40 s with no data lost (RPO 0). Site 2 served all 42 boards, the local admin signed in (a wrong password got 401), a query through its local Prometheus returned data, and site 1 reached it through the global service. It moved back in 39 s, also RPO 0, and `clear` removed the overrides on both sites. Follow-ups: `status` read an unreachable site as absent, and the switchover prompt lost its stdin to ssh (both fixed in #1920). |
| 2026-11-07 | Site loss (site 2's nodes stopped for 14 min) | Passed. The header counted 1 of 2 reporting sites and site 2 read Unknown. Site 1 fired PeerSiteUnreachable, ObservabilityArchiveUnavailable and TargetDown to Telegram, and healthchecks.io reported both of site 2's checks down. Everything recovered within 5 min of the restart. Follow-ups: the household view read Wi-Fi as Failed when only its exporter was gone (fixed in #1924). Shared VIP routes kept 10.10.95.200 on site 2: its distro's FRR held the router session up while announcing nothing, and the VIP answered from site 1 (fixed with this record). |
| 2026-11-07 | Standby restore (run by hand, owner-approved, ahead of the first Sunday run) | Passed after three fixes. The first run failed on both sites: the kustomization's namespace had moved the drill's flux-system Role, so reading site-overrides got a 403 (#1927). Then site 2 failed on `finance-sync-state`, a rollback-only PVC since finance-sync moved to Postgres, which Firefly's failover entry also still listed (#1928). And the drill couldn't cover Home Assistant, a StatefulSet with no PVC on the standby (#1929). Then both sites passed. Site 1 restored transcriber, and site 2 restored Mealie, Firefly and Home Assistant (creating its PVC), each in 5-20 s. All four warm PVC apps read restore Healthy and RTO Healthy on their standby site. Follow-ups: Home Assistant's recovery readiness on site 2 read Failed for a real reason. Its database backup there failed at 01:40Z with a barman-cloud plugin EOF, as mealie-pg's (11-06) and grafana-pg's (11-07) did. kube-state-metrics shows the plugin sidecar (a native sidecar, so its reasons are under the init-container metrics) OOMKilled at its 256Mi limit. The limit is now 512Mi (#1932). The sidecar is injected at pod creation, so a new `CNPG_ROLLOUT` setting rolled every instance on both sites to pick it up (#1934). Separately, `StandbyRestoreDrillStale` had no series until a first success, so a drill that never ran couldn't alert; it now counts from the CronJob's creation (#1933). |
| 2026-11-07 | Partition (`wg-site2` stopped on WSL-02, 20:45-20:57Z, owner-run, restored by a timer) | Passed. Site 1's Network board showed site 2's tunnel and router-BGP hops Failed, and both shared VIPs moved to site 1. Each site kept reporting itself and read the other as Unknown (site 1's queries carried the partial-response warning). healthchecks.io kept every check up, because site 2 reaches the internet directly. Burn-rate alerts fired on site 2 for Home Assistant's path and MQTT, which really were unreachable from there. Everything recovered within minutes. Follow-ups: site 2's mesh hop read Healthy while pod traffic between the sites failed. Its control plane reached site 1's clustermesh-apiserver around the downed tunnel (eth0, the LAN, the router), so the hop now also needs the site's own peer probe (#1949). Separately, `AlertmanagerClusterCrashlooping` had been firing falsely on site 2 since 19:29Z: WSL-02's slow, constantly corrected clock moves the kernel's boot time, so every process start time there drifts. It's replaced by a restart-count rule (#1950, #1951). |
