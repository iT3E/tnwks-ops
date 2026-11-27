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
| Logs | Each site's own Loki, 14 days: site 1 holds the router's syslog and its Job pods' logs, site 2 its Job pods' logs. On site 2 a stream with the label `security_stream` stays 90 days, and Prometheus scrapes Loki once a minute (`kubernetes/clusters/wsl-02/apps/kustomization.yaml`). Grafana's Loki datasource reads the site it runs on, so reach site 2's Loki from its distro with `kubectl port-forward` | Grafana on site 2 reads site 2's Loki. Site 1's logs are unavailable until it returns | Site 1's Loki is unaffected. Site 2's logs are unavailable until it returns |
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
- **A reader can not reach Prometheus** (a board with no data on one
  datasource, Pyrra empty, `up{job="peer-federate"}` at 0 on the other
  site): see "The network rule of Prometheus" in the implementation doc.
  `hubble observe --pod
  monitoring/prometheus-kube-prometheus-stack-prometheus-0 --verdict
  DROPPED` names the source, and `--type l7 --http-status 403` shows a
  request that the rule refused. If the pod is NotReady, look first for a
  403 to the host on `/-/ready` or `/-/healthy`. The quick way back is a
  revert of the rule's PR.
- **A client can not reach Grafana** (a board or the UI times out, the
  health probe of Grafana fails): the network policy of Grafana
  (`kubernetes/apps/monitoring/grafana/app/networkpolicy.yaml`) admits on
  port 3000 only the internal ingress of each site, and Prometheus, the
  evidence and the image renderer of its own site, and the kubelet probes.
  `hubble observe --to-namespace monitoring --to-label
  app.kubernetes.io/name=grafana --verdict DROPPED` names the source. A new
  client gets a line in the policy, and `observability/tests/test_grafana_policy.py`
  lists it. The quick way back is a revert of the policy's PR.
- **Flux object not Ready:** `FluxResourceNotReady` (group
  `homelab.evidence`, `observability/rules.py`): a Kustomization,
  HelmRelease or source of Flux on one site has had Ready False for one
  hour. Each site evaluates it on its own Prometheus, from the evidence
  collector of that site (`homelab_flux_resource_ready`), and its own
  Alertmanager sends it, so it covers site 2 too. The event alerts of Flux
  (`kubernetes/apps/flux-system/addons/alerts`) are in the tree of site 1
  only.
  - It fires once for each object and site. A suspended object does not
    fire. A first install or a reconcile in progress has Ready Unknown, and
    does not fire.
  - An object that fails is tried again each minute or so, and its Ready
    condition is Unknown for a moment at each try; with `wait: true` it is
    Unknown for the whole try, up to its timeout (15 or 20 minutes here).
    The rule holds the last True or False of the last 30 minutes, so those
    moments do not start the hour again. It ends at the first Ready True.
  - Limits: a timeout over about 25 minutes can still start the hour again.
    A deleted object, or a collector that stopped, keeps the alert for up to
    30 minutes.
  - Not covered yet: an object that stays Unknown. A rule for it needs to
    leave out the objects that never have a condition (the OCI
    HelmRepositories, 7 on each site), or it fires with no end.
  - An object that waits for a failed one (a `dependsOn`) reports Ready
    False too, so one fault can fire several alerts. Fix the first one.

  Steps, read only first:
  1. The Ready message: `flux get kustomizations -A` (or `helmreleases`,
     `sources all`) on that site, or `kubectl -n <namespace> describe
     <kind> <name>`. The Kubernetes board, tab Reconciliation, lists the
     objects that are not Ready.
  2. The events of the object and the log of its controller
     (`kubectl -n flux-system logs deploy/kustomize-controller` or
     `helm-controller`).
  3. The fix is a change in git, in a pull request. After the merge,
     `flux reconcile` of the object on that site, with the go of the owner,
     or wait for its interval.
- **Archive behind or full:** `ObservabilityArchiveBehind` and
  `ObservabilityArchiveSpaceLow`. Check the Compactor first: whether it has
  halted (`thanos_compact_halted`) and how many compactions it has left. Then
  check the sidecars' upload errors. `observability/verify_live_archive.py`
  proves Store-only history per site (read-only).
- **Room on site 2:** three alerts of the group `homelab.capacity`
  (`observability/rules.py`). `NodeContainerMemoryNearLimit`: worker 1 of
  homelab-wsl-02 is past 90% of the memory limit of its node container (the
  kubelet's `node_memory_working_set_bytes`, scraped on that site).
  `LokiSecurityIngestAbovePlan`: its Loki took in more than twice the planned
  bytes of its 90-day streams in a day, before compression.
  `WslDriveSpaceLow`: the drive under the WSL disk of WSL-02 has less than
  100 GB free (evaluated by site 1, which scrapes both Windows hosts).
- **Room on site 1:** four alerts of the group `homelab.capacity`
  (`observability/rules.py`) on the memory of each node container of
  homelab-wsl, scraped on that site: the kubelet's
  `node_memory_working_set_bytes`, and cAdvisor's `container_memory_rss` of
  the root cgroup of the node (`id="/"`). Each node
  container has a Docker memory limit: 15 GiB for a worker
  (`memory_workers` of `talos_clusters` in the Ansible inventory, and the
  `memory_nodes` entries that move the running workers to that size) and
  4 GiB for the control plane (`memory_controlplanes`).
  `observability/tests/test_node_container_memory.py` holds the rules equal
  to the inventory.
  - `WorkerContainerMemoryNearLimit`: a worker past 98% of its limit for 15
    minutes is a warning, and past 99% for 2 minutes it is critical. While
    the critical one fires, the inhibit rule of Alertmanager holds back the
    warning of the same name. It does not compare the node, so it also holds
    back the warning of the other worker: read both nodes (step 1).
  - `ControlPlaneContainerMemoryNearLimit` (warning): the control plane
    past 98% for 15 minutes.
  - `NodeContainerRssNearLimit` (warning): the resident memory of a node
    past 85% of the limit of its role for 15 minutes.
  - `NodeContainerRssMissing` (warning): for an hour,
    `NodeContainerRssNearLimit` can not read a node of site 1 that
    kube-state-metrics has (`kube_node_info`), so it can not fire for that
    node. The alert names the node.

  What the numbers mean:
  - The kubelet does not see these limits. It reports the memory of the
    whole WSL VM, so its allocatable memory is the VM, the scheduler places
    pods by their requests against the memory of the VM, and the kubelet
    does not evict, because it sees free memory.
  - The scheduler has no stop at the memory limit of a worker. The pod
    limit of the kubelet (110) stops it on worker 1, which had 99 pods at
    12 of its then 13 GiB on 2026-11-27. It does not stop it on worker 2,
    whose pods are larger. A higher pod limit removes the one stop on
    worker 1.
  - These alerts warn. They do not stop the scheduler, and a rollout or a
    burst of Jobs can cross to the limit in minutes; that is why the
    critical rule of a worker waits only 2 minutes.
  - At the limit, the kernel kills processes inside the node container by
    their score (BestEffort pods first, then Burstable ones). The kubelet
    does not evict first.
  - The control plane runs no workload pods. Its working set also counts
    active file pages, for example of the database file of etcd, which the
    kernel can take back before it kills. So the working set sits well past
    90% while the anonymous memory is far lower, and the rule waits for 98%:
    there the kernel has almost nothing left to take back. At the time of
    this change the control plane was past 90% and under 98%, so the rule is
    quiet. The working set less the resident memory is mostly file pages.
  - A worker's working set counts file pages the same way (the data files
    of Prometheus, Loki and the databases), and they grow into whatever
    limit the worker has: on 2026-11-27, at 15 GiB, worker 1 sat at 92-95%
    with 7.7 GiB of anonymous memory. So its rules wait for 98% and 99%,
    and `NodeContainerRssNearLimit` is the early sign of real pressure.
  - The resident memory is the anonymous memory of the node container,
    which the kernel can not take back (the nodes have no swap). It is
    `rssBytes` in the summary of the kubelet (step 1), and the series
    `container_memory_rss{id="/"}` of cAdvisor gives the same figure. It
    leaves out tmpfs, shared memory and kernel memory, which also count
    against the limit and which the kernel can not take back either. So the
    resident memory is a floor of what the kernel can not take back, not
    all of it. The
    chart drops each cAdvisor series of a cgroup with no pod, so the scrape
    of site 1 (`kubernetes/clusters/wsl/apps/kustomization.yaml`) replaces
    the chart's cAdvisor relabel list with a copy of it that spares that one
    series of each node. The other series of the scrape do not change, and
    no rule or board of the chart reads a cAdvisor series with an empty
    `container` or `image`. A new chart version can change its default
    list. `observability/tests/test_node_container_memory.py` fetches the
    chart version that the HelmRelease names, checks it against the digest
    in its registry, and compares its default list with the copy. So a
    chart bump that changes the list fails that test: copy the new list to
    the test and to the patch of site 1, with the change that spares the
    one series.
  - `NodeContainerRssMissing` makes a silent gap loud: a node whose root
    series the rule can not read gets no alert. The causes are a relabel
    that no longer keeps the series, a node with a name outside the form
    of Talos in Docker, or a label of the kubelet that changed. The guard
    compares by node name, so a series of a name that kube-state-metrics no
    longer has can not hide a node. When no root series is left at all,
    each node fires. Compare `container_memory_rss{id="/"}` of site 1 with
    the nodes.
  - `NodeContainerRssNearLimit` fires when a node has little left that the
    kernel can take back. At the time of this change the resident memory
    was about 74% of the limit of the control plane and 53% and 63% of the
    workers, so the rule is quiet.

  Steps, read only first:
  1. The memory of each node:
     `kubectl get --raw /api/v1/nodes/<node>/proxy/stats/summary`, the
     `node.memory` fields `workingSetBytes` and `rssBytes`, or the series in
     Prometheus.
  2. The largest pods: `kubectl top pod -A --sort-by=memory`, and the node
     of each from `kubectl get pod -A -o wide`. Note the pods that came to
     the node last.
  3. With the go of the owner, move or shrink one: lower its replicas, or
     move it with a node selector to the other worker when that worker has
     room. A pod that the scheduler puts back on the same node needs its
     memory request raised.

  More memory for a node is a size of its own in `memory_nodes`, set on the
  running container by `06-talos-node-memory.yml` (`task talos:memory:wsl`),
  within the memory of the
  WSL VM. The rules then need that size too (the test fails until they
  have it).
- **Certificates of the security platform:** `SecurityCertificateExpiring`
  (group `homelab.security`, `observability/rules.py`) fires 30 days before
  a certificate for IAM Roles Anywhere of the namespace `security` on
  homelab-wsl-02 ends. Its series is `kube_lease_renew_time`: the credential
  refresher writes the end of each certificate to a Lease
  (`kubernetes/apps/security/aws-credentials/README.md` has the renewal).
- **API tokens of Prowler:** `SecurityTokenExpiring` (group
  `homelab.security`) fires 30 days before the token of the Prowler run for
  Cloudflare ends. The owner writes the end date to the renew time
  of the token's Lease (`kubernetes/apps/security/prowler/README.md`, "When a
  token ends").
- **Audit lines refused:** `SecurityAuditLinesRefused`, of the group
  `homelab.security` (`observability/rules.py`). The sink
  `loki_security_audit` of a Vector aggregator dropped a request that Loki
  refused, and Vector does not send it again. The alert fires at the first
  refused request and holds for 15 minutes after the last one. Loki refuses
  a line that is older than 7 days, or more than one hour older than the
  newest line of its stream: the log of the aggregator pod has the answer,
  and `vector_component_discarded_events_total` of the sink has the number
  of lines. A request that got no answer is not in this alert: Vector sends
  it again (`kubernetes/apps/monitoring/vector/README.md`).
- **The list of accepted findings:** `SecurityAcceptedListUnread` and
  `SecurityAcceptedEntriesSkipped`, of the group `homelab.security`
  (`observability/rules.py`). The first fires when the security converter
  on homelab-wsl-02 gets no entry from a list that is there, for 30
  minutes; the second when entries of the list are skipped, for one hour.
  With no list at all, neither fires. The log of the converter has the
  number of the line or the entry and why, and no text of the list
  (`security/converter/README.md`, "The list of accepted findings").
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
