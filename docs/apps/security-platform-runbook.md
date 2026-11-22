# Security platform runbook

What to do when an alert of the security platform fires. The registry, the
rules and the board are in [`security/README.md`](../../security/README.md);
this page is the operator's side. Each alert of the platform links to its
own section here (`runbook_url`).

## Rules for every step

- **Read only, unless the step says "owner approval".** The read-only steps
  are `get`, `describe`, `logs`, a Loki or Prometheus query in Grafana, a
  look at the board, and a read of git. Each other step waits for a yes of
  the owner at that time. A read on a Windows host (a service, a scheduled
  task, an agent, a log or a file there) is not in that list, so it waits
  for the owner too.
- **The cluster of each read.** The `kubectl` reads in the namespace
  `security` are for the cluster of site 2, homelab-wsl-02, where the parts
  of the platform run. Trivy Operator runs on both clusters: for it, read
  the cluster in the label `k8s_cluster` of the alert, homelab-wsl or
  homelab-wsl-02.
- **A fix goes through git.** Change the file in a pull request, and Flux
  applies the merge. Don't edit, patch or scale an object on the cluster by
  hand: Flux puts it back at its next reconcile, and git no longer says what
  runs.
- **A finding stays out of public text.** The ops repository is public. A
  check that fails, a finding, a value of a result or a weakness goes in no
  file, commit message, issue, pull request or comment. The board and the
  stream `findings` in Loki are where a finding is read.
- **The list of accepted findings is the owner's.** It is an encrypted
  Secret (`kubernetes/apps/security/accepted-findings/app/accepted.sops.yaml`).
  Only the owner opens it, changes it and merges the change
  ([`security/README.md`](../../security/README.md#accepted-findings)). An
  agent can draft a line for the owner, outside the repository.
- **Each alert has severity `warning`** and goes to the default Telegram
  route of Alertmanager. The message links to the board of the site
  (`dashboard_url`) and to its section here.
- **Logs in Loki.** The parts of the platform run in the namespace
  `security` of homelab-wsl-02, and their logs are in its Loki:
  `{namespace="security", app="security-converter"}` (the converter),
  `app="prowler"` (the Prowler runs), `app="api-checks"`, `app="trivy-operator"`
  and `app="trivy-scan-job"`, `app="aws-credentials"` (the refresher) and
  `app="trail-copy"`. Trivy Operator also runs on homelab-wsl, and its logs
  there (`app="trivy-operator"`, `app="trivy-scan-job"`) are in the Loki of
  homelab-wsl. The Vector aggregator of each cluster is
  `{namespace="monitoring", app="vector-aggregator"}`. The security streams
  have the label `security_stream` (`k8s-audit`, `routeros`, `findings`;
  `kubernetes/apps/monitoring/vector/README.md`, "The security streams").

## The alerts

15 alerts cover the platform:

- the 3 rules that `security/generate.py` writes from the registry
  (`SecurityResultLate`, `SecurityHostUnreachable`,
  `SecurityAcceptedFindingExpired`);
- the 8 rules of the group `homelab.security` in `observability/rules.py`;
- 3 rules of the group `homelab.capacity` in `observability/rules.py` for
  the room of site 2, where the platform runs (`NodeContainerMemoryNearLimit`,
  `LokiSecurityIngestAbovePlan`, `WslDriveSpaceLow`);
- `PeerSiteUnreachable`, in
  `kubernetes/apps/monitoring/kube-prometheus-stack/app/peer-rules.yaml`,
  which `SecurityHostUnreachable` holds.

The generated rule tests (`promtool test rules`, the `rule-tests` job) are
the proof of each expression of the first 14. The text of an alert names no
check, finding or value ([`security/README.md`](../../security/README.md#the-text-of-an-alert)).

### SecurityResultLate

**What it means.** The newest result of a source for a system is older than
`late_after` of the source, or the source serves no time. The safeguards
that rest on it show "No evidence" on the board. The labels name the system
and the source. The limit of each source is in
`security/registry/sources.yaml` ([`security/README.md`](../../security/README.md#the-cycle-of-each-source)).
While `SecurityHostUnreachable` fires, Alertmanager holds the late alerts of
the systems behind that site.

**First reads.**

1. The tab Sources of the board: the age of the last result of each source
   for each system, and the sources that are late.
2. The last run of the source:

   ```sh
   kubectl -n security get cronjob,job,pod
   kubectl -n security get jobs -l app.kubernetes.io/managed-by=trivy-operator
   ```

   For Prowler, the log of the run in Loki
   (`kubernetes/apps/security/prowler/README.md`, "Read a run"). For the
   host check and Wazuh, the task or the agent on the host (owner
   approval: a read on a Windows host). For a stream,
   the newest line of its `security_stream` in Loki.
3. The log of the converter, `{namespace="security", app="security-converter"}`.
   It serves the time of each source from Loki and from the files of
   results (`security/converter/README.md`, "When the converter serves no
   time").

**The fix.** The section of the source that says why a run fails:
`kubernetes/apps/security/prowler/README.md` ("When a run fails"),
`kubernetes/apps/security/trivy-operator/README.md` ("When a scan fails"),
`kubernetes/apps/security/api-checks/README.md`, and for the audit stream
`kubernetes/apps/monitoring/vector/README.md` ("The audit reader"). A change
of a source goes through git. A run by hand before the next schedule is
owner approval.

**When it ends.** When the source reports again: the rule reads the newest
sample of 10 minutes.

**Who decides.** The reads are open. The owner decides a run by hand, and
merges each fix.

### SecurityHostUnreachable

**What it means.** homelab-wsl-02 can not scrape `windows_exporter` on the
host of the other site for 5 minutes. That site is away (its power or its
link), the link between the sites is down, or the exporter on that host
stopped. While it fires, Alertmanager holds the `SecurityResultLate` alerts
with the same `behind_site`, and `PeerSiteUnreachable` of that site
(`alertmanager.yml`). So a loss of the other site gives one alert.

**First reads.**

1. The board `Sites` (`tnwks-sites`): whether the other site reports.
2. In the Prometheus of homelab-wsl-02: `up{job="windows-exporter-security"}`.
3. Whether a planned window of a host is open: the window of the move of a
   distro raises this alert after 5 minutes
   ([`docs/wsl/windows-ansible.md`](../wsl/windows-ansible.md), "Alerts
   during the window").

**The fix.** If the site is away, its power or its link comes back first.
If the site is up, the owner reads the service `windows_exporter` on its
host, and the firewall rule `windows_exporter (Prometheus)`, which admits
the address of site 2 (`windows_monitoring_remote_extra` in the host_vars
of that host); a read on the host is owner approval. A
change of either is an apply of the Windows play (owner approval,
[`docs/wsl/windows-ansible.md`](../wsl/windows-ansible.md)).

**When it ends.** At the first scrape that passes. The held alerts then
show if their cause is still there.

**Who decides.** The owner: the power and the link of a site, and each
apply on a host.

### SecurityAcceptedFindingExpired

**What it means.** An accepted finding of the system has passed its end
date. The alert counts by system, so it names no check.

**First reads.** The tab Accepted findings of the board: the count past
their end date, by system.

**The fix.** The owner reads the line in the encrypted list. The owner sets
a new end date with a reason, or removes the line and fixes the cause of the
finding ([`security/README.md`](../../security/README.md#accepted-findings)).

**When it ends.** In the cycle of the converter after Flux applies the
merged list.

**Who decides.** The owner only.

### SecurityAcceptedListUnread

**What it means.** The security converter gets no entry from the list of
accepted findings, for 30 minutes
(`homelab_security_converter_accepted_list_read` at 0). The board shows no
accepted finding, and the rule of Trivy Operator mutes none. With no list
at all the value is 1, and no alert fires.

**First reads.** The log of the converter,
`{namespace="security", app="security-converter"}`. It names the line and
the kind of the fault, and no text of the file.

**The fix.** The owner fixes that line of the list
(`security/converter/README.md`, "The form that the converter reads" and "A
file that gives no entry").

**When it ends.** In the cycle after the list is fixed.

**Who decides.** The owner only.

### SecurityAcceptedEntriesSkipped

**What it means.** Entries of the list do not fit, for one hour
(`homelab_security_converter_accepted_skipped_entries` above 0). The
converter skips them, so their findings show as fails on the board.

**First reads.** The log of the converter,
`{namespace="security", app="security-converter"}`. It names each entry by
its number, and why.

**The fix.** The owner fixes those entries (`security/converter/README.md`,
"An entry that the converter skips"). The Prowler runs skip the entries
that are not in the form, and take an entry whose source or system is not
in the registry.

**When it ends.** In the cycle after the list is fixed.

**Who decides.** The owner only.

### SecurityFindingNew

**What it means.** A check of high or critical severity on the system fails,
and one of these is true: it has more failed resources than at each time of
the day before; it had none one hour before; or it had no result in the day
before (a new check, or one that comes back after more than a day). Only
high and critical alert; the other severities are on the board. A check that
fails again after less than one hour at 0 does not alert again.

It compares one value for each check (the labels `k8s_cluster`, `source`,
`check`, `system` and `standby`, from the record
`homelab:security_check_result_high:max`), so a new pod of a source, or a
change of the severity of a check, is not a new result. Each site fires it
for its own results. On homelab-wsl, a result of the host check has the
label `standby`, and alerts only while homelab-wsl-02 does not scrape its
host. On the first day of a source, each system with a check of high or
critical severity that fails alerts one time.

**First reads.**

1. The tab Systems of the board: the failing safeguards of the system.
2. The detail of the source. For Prowler and the API checks, the stream
   `findings` in Loki, `{security_stream="findings", system="<system>"}`.
   For Trivy Operator:

   ```sh
   kubectl get clustercompliancereport k8s-cis-1.23 -o yaml
   kubectl -n <namespace> get vulnerabilityreports
   ```

**The fix.** Fix the cause through git, or the owner accepts the finding in
the encrypted list with a reason and an end date
([`security/README.md`](../../security/README.md#accepted-findings)). The
finding stays out of the text of the pull request.

**When it ends.** After one hour.

**Who decides.** The owner: whether a finding is fixed or accepted.

### SecurityImagesNotScanned

**What it means.** Images that run on a cluster with Trivy Operator have had
no vulnerability report for 36 hours (the `late_after` of `trivy`). A report
counts for a running image when it has its digest, or the same namespace,
container and image reference
(`kubernetes/apps/security/trivy-operator/README.md`, "The digest of a
report"). While one image has no report, the check `image-vulnerabilities`
of the cluster keeps a fail, and shows "no evidence" in place of a pass.
Each site fires it for its own cluster.

**First reads.** The scan jobs and the log of the operator:

```sh
kubectl -n security get jobs -l app.kubernetes.io/managed-by=trivy-operator
```

The log of the operator is `{namespace="security", app="trivy-operator"}`
in Loki.

`kubernetes/apps/security/trivy-operator/README.md`, "When a scan fails",
has the usual causes.

**The fix.** Add the registry to the network policy of the scan jobs, or
move the image to a public registry. For an image that no scan can read, the
owner adds an entry to the accepted list, an object in the form
`{"source": "trivy", "check": "images-not-scanned", "system": "<the cluster>", "reason": "<why>", "until": "YYYY-MM-DD"}`.
It mutes the alert of that cluster until its end, and
`SecurityAcceptedFindingExpired` tells the owner at the end. The entry also
mutes a new image with no report on the cluster, so keep the end near.

**When it ends.** When each running image has a report. Its 36 hours start
again at the end of an entry, and each time the count of images with no
report is 0 for one evaluation.

**Who decides.** The owner: each entry of the accepted list.

### SecurityImagesScannedByTag

**What it means.** For 7 days, containers on a cluster with Trivy Operator
have run an image that no report has, and the report of their reference
counts for them (`homelab:security_trivy_containers_by_reference:count`
above 0). The tag was pushed again after the node pulled it, so the report
is of the image that the tag points to now, not of the image that runs
(`kubernetes/apps/security/trivy-operator/README.md`, "The digest of a
report"). Each site fires it for its own cluster.

**First reads.** The board, tab Sources, "Images scanned by tag only", has
the count.

**The fix.** Restart the workload, so that it pulls the image of the tag
(owner approval), or pin a digest in its pod spec through git. To keep a tag
that moves, the owner adds an entry to the accepted list in the form
`{"source": "trivy", "check": "images-scanned-by-tag", "system": "<the cluster>", "reason": "<why>", "until": "YYYY-MM-DD"}`.
It mutes the alert of that cluster until its end.

**When it ends.** When the count is 0.

**Who decides.** The owner: a restart, and each entry of the accepted list.

### SecurityCertificateExpiring

**What it means.** A certificate for IAM Roles Anywhere of the namespace
`security` on homelab-wsl-02 ends within 30 days. The AWS sessions of one
tool of the platform come from it, and stop when it ends. The refresher
writes the end of each certificate to the renew time of its Lease, and the
rule reads `kube_lease_renew_time`. The label `lease` names the Lease.

**First reads.**

```sh
kubectl -n security get lease
kubectl -n security get cronjob aws-credentials
kubectl -n security get job
```

The log of the refresher is `{namespace="security", app="aws-credentials"}`.

**The fix.** Make a new certificate for the same CN, put it in its SOPS
Secret, and merge (`kubernetes/apps/security/aws-credentials/README.md`,
"When a certificate ends").

**When it ends.** At the next run of the refresher after the merge.

**Who decides.** The owner: the certificate and its Secret.

### SecurityTokenExpiring

**What it means.** An API token of a Prowler run ends within 30 days. The
daily run of that provider reads with the token, and fails when it ends. The
owner writes the end date of each token to the renew time of its Lease
(`kubernetes/apps/security/prowler/app/lease-*-token.yaml`). A Lease that
nobody set has a date in the past, so the alert fires at once.

**First reads.** `kubectl -n security get lease`, and the date in the file
of the Lease in git.

**The fix.** Make a new token, put it in its SOPS Secret, write its end date
to the Lease, and merge them in one pull request
(`kubernetes/apps/security/prowler/README.md`, "When a token ends"). Change
the date in git only: Flux puts back an edit of the Lease.

**When it ends.** When the Lease has the new date.

**Who decides.** The owner: the token and its Secret.

### SecurityAuditLinesRefused

**What it means.** The sink `loki_security_audit` of a Vector aggregator
dropped a request that Loki refused, and Vector does not send it again. It
fires at the first refused request and holds for 15 minutes after the last
one. Loki refuses a line that is older than 7 days, or more than one hour
older than the newest line of its stream. A request that got no answer is
not in this alert: Vector sends it again.

**First reads.**

1. The log of the aggregator of the cluster in the label `k8s_cluster`,
   `{namespace="monitoring", app="vector-aggregator"}`: the answer of Loki.
2. The number of lines, in Prometheus:
   `vector_component_discarded_events_total{component_id="loki_security_audit"}`.
3. `kubernetes/apps/monitoring/vector/README.md`, "The audit reader": how
   the reader sends the audit files of the node.

**The fix.** The repository has no procedure for this case. The owner
decides, from the answer of Loki.

**When it ends.** 15 minutes after the last refused request.

**Who decides.** The owner.

### LokiSecurityIngestAbovePlan

**What it means.** The Loki of homelab-wsl-02 took in more than twice the
planned bytes of its 90-day streams in a day, before compression. The plan
is 1.2 GB a day (`SECURITY_INGEST_PLAN_GB` in `observability/rules.py`,
with its parts in the comment there), so the alert fires above 2.4 GB in 24
hours, for one hour.

**First reads.**

1. The board `Observability` (`tnwks-observability`).
2. The stream that grew, in Loki:
   `sum by (security_stream, k8s_cluster) (bytes_over_time({security_stream=~".+"}[1d]))`.
3. In Prometheus:
   `sum by (k8s_cluster) (increase(loki_distributor_bytes_received_total{retention_hours="2160"}[1d]))`.

**The fix.** Lower the volume of that stream at its source, or its
retention. The repository has no step for either; the owner decides.

**When it ends.** When the bytes of the last 24 hours are under twice the
plan.

**Who decides.** The owner.

### NodeContainerMemoryNearLimit

**What it means.** Worker 1 of homelab-wsl-02, where the parts of the
platform run, is past 90% of the memory limit of its node container for 15
minutes. The limit is 22 GiB (`memory_nodes` of `talos_clusters` in the
Ansible inventory, and `CAPACITY_NODE_LIMIT_GIB` in `observability/rules.py`).
The scheduler does not see it, because the node reports the memory of the
whole WSL VM. This alert warns; it stops nothing.

**First reads.** The read-only steps of "Room on site 1" in
[`docs/operations/observability-operations.md`](../operations/observability-operations.md)
hold for this node too:

```sh
kubectl get --raw /api/v1/nodes/homelab-wsl-02-worker-1/proxy/stats/summary
kubectl top pod -A --sort-by=memory
kubectl get pod -A -o wide
```

**The fix.** With the go of the owner, move or shrink one pod: lower its
replicas, or move it with a node selector to another worker that has room.
More memory for the node is a size in `memory_nodes`, within the memory of
the WSL VM, and the rule then needs that size too.

**When it ends.** When the working set is under 90%.

**Who decides.** The owner.

### WslDriveSpaceLow

**What it means.** The drive under the WSL disk of the Windows host of site
2 has had less than 100 GB free for 15 minutes (`CAPACITY_DRIVE_FLOOR_GB`
in `observability/rules.py`). The disk file of the distro holds the
cluster and the stores of the platform, and WSL stops when that drive is
full. Site 1 evaluates the rule, as it scrapes both Windows hosts.

**First reads.**

1. The board `Hosts` (`tnwks-hosts`): the free space of the drive.
2. In the Prometheus of site 1:
   `min by (instance, volume) (windows_logical_disk_free_bytes{volume="C:"})`.
3. The stores of the cluster of site 2, the largest first:

   ```sh
   kubectl get pvc -A
   ```

   and in its Prometheus,
   `sort_desc(max by (namespace, persistentvolumeclaim) (kubelet_volume_stats_used_bytes))`.

**The fix.** The stores of the cluster delete at a set age: lower the age
of the largest one through git, or free space on the drive. The disk file
of WSL does not give back space that the distro freed until it is
compacted. On a host in the phase `done` of the move, the compaction is
the task of SYSTEM `tnwks-vhdx-compact-system`, run on demand
([`docs/wsl/windows-ansible.md`](../wsl/windows-ansible.md), "The start
tasks of the account that owns the distro"). Before the phase `done`, it
is the older task `tnwks-vhdx-compact` of the same role
(`windows_wsl_maintenance`). Each runs `wsl --shutdown`, which ends each
running distribution of the account that owns the distro and its WSL VM,
so the cluster of site 2 and the platform stop for the run, not only the
distro. The compaction is owner approval.

**When it ends.** When the drive has 100 GB free again.

**Who decides.** The owner: the age of a store, the space on the drive, and
each compaction.

### PeerSiteUnreachable

**What it means.** The Prometheus of the other WSL site has not answered
the `peer-federate` job of this site for 10 minutes
(`up{job="peer-federate"} == 0`). The other site is down, or its LAN bridge
(port 9090) or its Prometheus is. A site that is down can not send its own
alerts; healthchecks.io covers that case too. While `SecurityHostUnreachable`
fires for the same site, Alertmanager holds this alert.

**First reads.**

1. The board `Sites` (`tnwks-sites`).
2. `up{job="peer-federate"}` in the Prometheus of this site.
3. Whether a planned window of a host is open
   ([`docs/wsl/windows-ansible.md`](../wsl/windows-ansible.md), "Alerts
   during the window").
4. If the other site is up: "A reader can not reach Prometheus" in
   [`docs/operations/observability-operations.md`](../operations/observability-operations.md).

**The fix.** The repository has no procedure past those reads: the power
and the link of a site, and its LAN bridge, are the owner's.

**When it ends.** At the first federation scrape that passes.

**Who decides.** The owner.
