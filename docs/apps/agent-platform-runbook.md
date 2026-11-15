# Agent platform runbook

What to do when an alert of the agent platform fires, when an agent does
not answer on its channel, a sandbox does not start, web search does not
work or memory search finds nothing, how to restore the
state of an agent, how to stop the workflow agent fast, how to move a trust
domain to Bedrock by hand, and what to do at a limit of the Claude plan. The
parts and their config are in [`agent-platform.md`](agent-platform.md); this
page is the operator's side. Each alert of the platform links to its own
section here (`runbook_url`).

## Rules for every step

- **Read only, unless the step says "owner approval".** The read-only steps
  are `get`, `describe`, `logs`, a Loki or Prometheus query in Grafana, and a
  read of git. Each other step waits for a yes of the owner at that time.
- **A fix goes through git.** Change the manifest in a pull request, and Flux
  applies the merge. Don't edit, patch or scale an object on the cluster by
  hand: Flux puts it back at its next reconcile, and git no longer says what
  runs.
- **A restart of an agent pod is a delete of the pod** (owner approval), not
  `kubectl rollout restart`. That command sets an annotation on the pod
  template, Flux removes it at its next reconcile, and the pod starts one
  more time.
- **Each alert has severity `warning`** and goes to the default Telegram
  route of Alertmanager. The message of each rule of the platform links to
  the board of the site and to its section here. The rules of the backup
  operator have no runbook link: find their sections here by the name of the
  alert.
- **Logs in Loki.** `{namespace="agents-workflows", app="workflow-providers"}`
  (the providers job), `app="workflow-run"` (the run jobs),
  `app="run-receiver"`, `{namespace="agents-<name>", app="platform-checks"}`,
  `{namespace="agents-pilot", app="agent"}` (the init, `backup` and
  `sandbox-start` containers of the pilot's agent pod; never its `agent`
  container), `{namespace="ai", app="model-bridge"}`, and
  `{namespace="monitoring", app="agent-heartbeat-check"}`.
  [`agent-platform.md`](agent-platform.md#logs) has what ships and what
  doesn't.

## The alerts

20 alerts cover the platform: the 15 rules of
`kubernetes/apps/ai/agents/monitoring/app/` (13 in `prometheusrule.yaml`, 2
in `heartbeat-prometheusrule.yaml`), and the 5 rules of the backup operator
(the kopiur chart) that match the snapshot policy, the snapshots and the
restores of a state volume. The other rules of the backup operator are about
its repository and its controller, and `backups.md` covers them. No alert of the platform has
fired live yet; the rule tests (`observability/tests/test_hand_written_rules.py`)
are the proof of each expression.

### AgentPlatformWorkloadNotReady

**What it means.** A Deployment or StatefulSet of a trust domain, of the
sandbox controller, or the model gateway (`litellm`) or the model bridge in
`ai`, has had fewer ready pods than wanted for 10 minutes. A workload scaled
to zero by a merge does not fire.

**First checks.**

```sh
kubectl -n <namespace> get deploy,sts,pods
kubectl -n <namespace> describe pod <pod>
kubectl -n <namespace> logs <pod> -c archive      # the agent pod: the archive step
kubectl -n <namespace> logs <pod> -c doctor       # the agent pod: the state migration
kubectl -n flux-system get kustomizations | grep -E 'agents|litellm|model-bridge'
```

**Seen so far.**

- A start of the pilot's agent pod failed on a lock of its state volume and
  passed after a few restarts. The `Recreate` strategy keeps one writer, so
  the lock clears.
- A `subPath` of a `local-path` volume comes out owned by root on this
  cluster, and the archive step fails with a permission error. The pod
  mounts each volume at its root for this reason.
- After a restart of the host, the sandbox gateway stops each sandbox it had.
  The agent pod stays ready; the `sandbox-start` container starts each one
  within a minute (`sandbox-start name=<name> result=ok`). It logs nothing
  when no sandbox is stopped, so an empty log is normal.

**Fix.**

- A failed `archive` init container: the state volume is full or not
  writable. Read the line, fix the cause in git.
- A failed `doctor` after an image update: revert the update in a pull
  request (owner approval). If the pod still fails, the state moved to the
  new format: [restore after a failed update](#after-a-failed-update).
- An image that does not pull, or a bad config: revert the last merge that
  touched the workload.

### AgentPlatformCronJobFailed

**What it means.** The last run of a CronJob in a trust domain failed, and no
run passed since: the providers job, the sweep, or a daily check
(`platform-check-isolation`, `platform-check-probe`). It resolves at the next
good run. The chart rule `KubeJobFailed` fires for the same Job and keeps
firing while the failed Job is kept, so read this alert for the state of now.

**First checks.**

```sh
kubectl -n <namespace> get cronjob <cronjob>
kubectl -n <namespace> get jobs --sort-by=.metadata.creationTimestamp
kubectl -n <namespace> logs job/<job>      # while the pod is there
```

The pod GC of this cluster deletes an ended pod within seconds, so the log is
often in Loki only. A run of the providers job logs one line:
`workflow-providers result=ok`, or
`workflow-providers result=failed step=<step> name=<name>`.

**Seen so far.** On 2026-11-13 the providers job passed its first run and
failed each run after it, at its lint step: the lint of the sandbox CLI
refuses a profile that is already in the store. No provider got a new token,
the token ended one hour after the last good run, and a review got 401 from
GitHub. The alert of that day was one more line in a `KubeJobFailed` group
that already fired, so nobody saw it. The fix (`0a3961f8`) removed the lint
and logs the step of each failure. This alert came after it, and it resolves
at the next good run, so a new failure shows.

On 2026-11-15 at 04:48 UTC it fired for the first time live, for
`agents-finance/platform-check-probe`: the probe's create failed with
`sandbox already exists`. The sandbox gateway of `agents-finance` (OpenShell
0.1.8) had answered the delete of the probe of 2026-11-14 with success and
left its Sandbox record `default--platform-probe`: its log has `Deleting
sandbox from Kubernetes` and no `Sandbox deleted from Kubernetes`. The
gateway then forgot the sandbox, so the delete before the next create could
not remove it. The record stayed `SandboxSuspended` with no pod after its
`sleep 3600` ended. The probe of `agents-main` that minute deleted its record
as usual. The fix (the change that adds this entry) removes such a record
before the create, and fails a run that leaves its own record.

**Fix.**

- The step names the part: `gateway` (the sandbox gateway of
  `agents-workflows`), `profile-*` (a provider profile in
  `app/providers/`), `provider-*` (the store of the gateway), `github-token`
  (the App, its key or its installation).
- Fix the cause in git, then watch at least two runs in a row before
  anything depends on the job: the job of 2026-11-13 passed its first run
  only.
- A run now, after the fix (owner approval):
  `kubectl -n agents-workflows create job --from=cronjob/workflow-providers providers-now`.
- For the providers job, fix it within the hour: a run token lives one hour
  from the last good run.
- **The probe, `sandbox already exists` or `sandbox removed` failed.** The
  probe's lines in Loki
  (`{namespace="<namespace>", app="platform-checks"}`) say what it did:
  `leftover sandbox` is `none` or `removed: default--platform-probe, made
  <time>` (it deletes only that record, with the labels of OpenShell for
  this probe); `sandbox removed` is `gone`, or `left after the gateway
  delete; removed: ...` when the gateway left the record again. That second
  case fails the run, and the record is gone for the next run. The Sandbox
  record owns its workspace volume claim (2Gi), two Services and a
  NetworkPolicy, so its delete removes them too: the leftover of
  2026-11-14 still holds them. Read only:
  `kubectl -n <namespace> get sandboxes.agents.x-k8s.io` and the log of the
  gateway (`kubectl -n <namespace> logs openshell-0`, the lines
  `Deleting sandbox from Kubernetes` and `Sandbox deleted from Kubernetes`).
  A run now (owner approval):
  `kubectl -n <namespace> create job --from=cronjob/platform-check-probe probe-now`.
  Follow-up: the gateway fault (a delete that answers success and removes
  no record, logged at debug level only, OpenShell 0.1.8) has no upstream
  report yet.

### WorkflowProvidersStale

**What it means.** The providers job of `agents-workflows` has had no good
run in 35 minutes, or its CronJob is gone. Two runs in a row failed or did
not run (suspended, not applied, an image that does not pull). The token of
the reviewer lives one hour from its last good run, so about 25 minutes are
left before a review gets 401.

**First checks.**

```sh
kubectl -n agents-workflows get cronjob workflow-providers -o wide
kubectl -n agents-workflows get jobs -l app.kubernetes.io/name=workflow-providers
kubectl -n flux-system get kustomization agents-workflows-run-receiver
```

Then the last run in Loki, as for
[AgentPlatformCronJobFailed](#agentplatformcronjobfailed).

**Seen so far.** The outage of 2026-11-13 above. It fires as expected when
the job is suspended on purpose ([stop the workflow agent](#stop-the-workflow-agent-fast)).

**Fix.** As for [AgentPlatformCronJobFailed](#agentplatformcronjobfailed).
Each run gets the newest token at its start and keeps it for its whole
life. That token ends one hour after the providers run that made it. So a
run fails when its token ends before the run does: a new run, and also a run
in flight that started late in that hour.

### WorkflowAppDrift

**What it means.** The GitHub App of the reviewer, or its installation, has
more permissions than the list in git (`GITHUB_APP_PERMISSIONS` in
`workflows/run-receiver/app/providers.yaml`). The providers job logs
`app-drift result=drift detail=<names>` each run, and still scopes each
token to the list, so this is not an outage. It resolves 20 minutes after
the last such line.

**First checks.** In Loki:
`{namespace="agents-workflows", app="workflow-providers"} |= "app-drift"`.
The `detail=` names each extra permission, for example
`extra:app:administration` or `extra:installation:repository_selection`.

**Seen so far.** Never. At 10:35 UTC on 2026-11-14 the gauge held
`app-drift result=ok`.

**Fix.** Either the owner sets the App back in its settings (and accepts the
change on the installation), or a pull request changes the list in git
(owner approval). A permission less than the list (`missing:...`) fails the
job before any token, and shows as
[AgentPlatformCronJobFailed](#agentplatformcronjobfailed).

### AgentPlatformCheckStale

**What it means.** A daily check of a trust domain, the isolation check
(04:17) or the sandbox probe (04:47), has not passed in 26 hours, or never
passed once a run was due.

**First checks.**

```sh
kubectl -n <namespace> get cronjob <cronjob>       # SUSPEND, LAST SCHEDULE
kubectl -n <namespace> get jobs --sort-by=.metadata.creationTimestamp
```

The log of the last run is in Loki,
`{namespace="<namespace>", app="platform-checks"}`.

**Seen so far.** Not fired. The probe had two fixes in its first days: it
finds its pods through the Sandbox record (`175976a6`), and the workload pod
of a sandbox has no DNS egress (`25f1bb2b`).

**Fix.** A failed isolation check is a finding, not a fault of the check:
read which rule let a request through before you change the check. Fix the
policy or the check in git.

### AgentRunSandboxTooOld

**What it means.** A run sandbox of a workflow agent (`default--run-*`) in
`agents-workflows` is older than 80 minutes. The run job deletes its own
sandbox at its end (also on an error and on SIGTERM), and the sweep deletes
each run sandbox older than one hour every 15 minutes, so both failed.

**First checks.**

```sh
kubectl -n agents-workflows get sandboxes.agents.x-k8s.io
kubectl -n agents-workflows get cronjob workflow-sweep
kubectl -n agents-workflows get jobs -l app.kubernetes.io/name=workflow-sweep
kubectl -n agents-workflows get jobs -l app.kubernetes.io/name=workflow-run
```

The log of the sweep is in Loki,
`{namespace="agents-workflows", app="workflow-sweep"}`.

**Seen so far.** Never. The stopped-run drill below is the test of this path.

**Fix.** The route that deletes a sandbox through the sandbox gateway is the
sweep: it deletes each run sandbox older than one hour, by its name and
label, so the gateway's record goes too. The gateway takes calls only from
pods of its namespace, so there is no route from a workstation, and a delete
of the Sandbox object leaves the record of the gateway. Fix the sweep in git
first if its last run failed. Then run it now (owner approval):
`kubectl -n agents-workflows create job --from=cronjob/workflow-sweep sweep-now`.
Its log is in Loki, `{namespace="agents-workflows", app="workflow-sweep"}`.

### AgentWorkflowRunFailed

**What it means.** A run job of a workflow agent failed, for 2 hours from its
start. The caller tries again with a new key (at most 3 attempts), so an
alert can come with a review that passed later.

**First checks.**

```sh
kubectl -n agents-workflows get jobs -l app.kubernetes.io/name=workflow-run
kubectl -n agents-workflows get job <job> -o jsonpath='{.metadata.annotations.workflow-agents/result}'
```

The run log is in Loki, `{namespace="agents-workflows", app="workflow-run"}`.
The exit code of the run job says where it stopped: 1 the agent answered
with an error, 2 the environment is not complete, 3 the sandbox did not
start or answer, 4 no answer in time, 5 the pod was stopped, 6 the sandbox
was not deleted (the README of iT3E/tnwks-workflow-agents).

**Seen so far.**

- 2026-11-14, 02:29 UTC: a run failed with a 502 in 32 seconds at the limit
  of the Claude plan. See [the plan limit](#at-a-limit-of-the-claude-plan).
- 2026-11-13: a review got 401 from GitHub, because the providers job had
  not renewed the token (above).
- 2026-11-13 and 2026-11-14: the pod GC took a run pod seconds after its end
  (it keeps only the newest ended pod of the cluster), before the receiver
  read the result line. The answer was `succeeded` with
  `"output_kept": false`. Release 0.1.4 (`f1a7f9c7`) follows the log of each
  run while it runs. `output_kept: false` now means only that the receiver
  was down for the whole end of a run.

**Fix.** A failure of the agent or of a provider: fix it in git. A plan
limit: nothing to fix on the platform. The run itself is not run again by
hand; the caller retries with a new key.

### AgentDailyArchiveFailed

**What it means.** The `backup` container of an agent pod started again in
the last 2 hours after an exit with an error: the daily archive of the
product failed. A restart of the node (reason `Unknown`) does not count.

**First checks.**

```sh
kubectl -n <namespace> logs <pod> -c backup --previous
kubectl -n <namespace> logs <pod> -c backup
```

The line `daily-backup result=failed day=<day>` comes after the error of the
backup command.

**Seen so far.** The first daily backup failed with
`GatewayLockNamespaceError`: the command must see the gateway process, so the
pod shares one PID namespace (`f19083dc`). The first full run passed on
2026-11-12.

**Fix.** Fix the cause in git. The container tries again when it starts; a
day with no archive leaves the archives of the 6 days before and the
nightly snapshot.

### AgentInstructionFileChanged

**What it means.** An instruction file of the workspace of a personal agent
(`AGENTS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md`, or a file under `skills/`)
is new, changed or gone since the day before. The agent can write these
files, so a change it was not asked for can be an instruction it took from
what it read. It fires for 20 minutes after the line.

**First checks.** In Loki:
`{namespace="<namespace>", app="agent"} |= "instruction-check"`. One line for
each file: `instruction-check file=<name> change=new|changed|gone`.

**Seen so far.** On 2026-11-13 at 00:05 UTC the check named a change of
`AGENTS.md` of the pilot, from the day before. The first list had no file:
the product makes the workspace at the first turn.

**Fix.** Ask the user of the agent if they asked for the change. If not,
treat it as an instruction the agent took from what it read. Never use a
snapshot restore here: a kopiur Restore puts last night's copy over the live
volume, with the start archives and the evidence of the change.

1. **Read the change.** Owner approval, by the owner, while the pod runs.
   The file and the archive hold private text, and an archive holds
   credentials, so they stay in the owner's terminal: no Job (its log goes
   to Loki), no issue, no pull request. `<file>` is the name in the alert
   line, under the workspace: `AGENTS.md`, `SOUL.md`, `USER.md`,
   `IDENTITY.md` or `skills/<path>`.
   ```sh
   kubectl -n agents-pilot exec deploy/agent -c backup -- \
     cat /home/node/data/state/workspace/<file>
   kubectl -n agents-pilot exec deploy/agent -c backup -- \
     tar -xzOf /home/node/data/archives/daily/openclaw-backup-<day before>.tar.gz \
     --wildcards '*/state/workspace/<file>'
   ```
   The daily archive of the day of the alert already holds the change: the
   check runs after the backup of that day. The archive of the day before
   holds the file before the change.
2. **Stop the agent.** Owner approval: a pull request sets `replicas: 0`.
3. **Restore the archive of the day before.** Owner approval:
   [the restore Job](#the-restore-job) `agent-restore` with
   `ARCHIVE=daily/openclaw-backup-<day before>.tar.gz`. The state goes back
   to that day, and the state of now stays in `state.before-restore-<time>`.
   The Job refuses an archive of another release than its image
   (`restore result=failed reason=release archive=<release> image=<release>`):
   this happens when an update came after the day before. Then the next pull
   request removes that Job and adds `agent-restore-2` with the image of the
   release of the archive (its tag and digest are in the history of
   `deployment.yaml`). The agent keeps its own image: its `doctor` moves the
   restored state to its release at the start, as at an update.
4. **Start the agent.** Owner approval: a pull request removes the Job and
   sets `replicas: 1`.

Not tested.

### AgentInstructionCheckStale

**What it means.** No `instruction-check result=` line of the pilot's agent
pod in 26 hours, once its `backup` container has run for 26 hours. The check
is blind: either the backup container did not reach the check, or its lines
do not reach the log store.

**First checks.**

```sh
kubectl -n agents-pilot logs deploy/agent -c backup
kubectl -n monitoring get ds vector-agent-platform
kubectl -n monitoring logs ds/vector-agent-platform --tail=50
```

In Loki, `{namespace="agents-pilot", app="agent"}` for the last day: no line
at all is the log path, not the check.

**Seen so far.** On 2026-11-14 the platform log agent sent nothing after its
first rollout: Vector 0.61 reads a sink address with no scheme as `https`,
and the aggregator listens in plain HTTP/2. No line arrived, so each alert
that reads a line was blind. The fix (`da491ff7`) names `http://`, and a CI
test (`observability/tests/test_vector_sinks.py`) now opens each `vector`
sink of the configs against a real source.

**Fix.** A backup that waits for a gateway that is not ready logs nothing:
see [AgentPlatformWorkloadNotReady](#agentplatformworkloadnotready). A log
path fault: fix the config of the platform log agent in git.

### AgentMemoryIndexFailed

**What it means.** The newest `memory-index result=` line of the pilot's
agent pod says `failed`: at the last start of the pod, the `memory-index`
init container could not bring the memory index up to date. The pod runs,
but an index that was stale stays stale: each memory search returns `stale`
and no result, until a start that ends with `memory-index result=ok`
([agent-platform.md](agent-platform.md), "The index at each start"). The
alert clears at that start. A new pod that has not run the step yet does
not fire on the line of the pod before, and with no pod (`replicas: 0`)
nothing fires. The rule reads 15 days of samples (the retention of
Prometheus on homelab-wsl), so a pod that runs more than 15 days after a
failed start stops firing then.

**First checks.**

```sh
kubectl -n agents-pilot logs deploy/agent -c memory-index
kubectl -n agents-pilot get pods -l app.kubernetes.io/name=agent
kubectl -n ai get deploy litellm
kubectl -n ai logs deploy/litellm --since=1h
```

The step prints nothing of the output of the product's CLI, so the cause is
on the path of the embeddings: the model gateway, or the model server of the
host behind it, did not answer, or the step ran over its limit of 180
seconds (a full rebuild of a large index).

**Seen so far.** Not fired yet. The starts of the pod on 2026-11-15 logged
`memory-index result=ok`.

**Fix.** When the model gateway and the model server of the host answer
again, start the pod again (owner approval):
`kubectl -n agents-pilot delete pod -l app.kubernetes.io/name=agent`. The
step runs at the start, and the alert clears with `memory-index result=ok`.
If the step fails again while both answer, it ran over its limit: a pull
request raises `LIMIT_SECONDS` in
`kubernetes/apps/ai/agents/pilot/agent/config/scripts/memory-index.sh`, and
the change of the script starts the pod again.

### AgentBackupMoverConfigMapDenied

**What it means.** The admission policy `agents-backup-mover-configmaps`
(`kubernetes/apps/ai/agents/admission/`) refused a create, change or delete
of a ConfigMap by a backup mover ServiceAccount (`kopiur-mover`,
`kopiur-snapshot-replication-mover` or `kopiur-stream-mover`) in a namespace
of a trust domain. The write did not happen. At kopiur 0.10.20 a mover
writes no ConfigMap there ([agent-platform.md](agent-platform.md#the-backup-mover-and-configmaps)),
so there are two causes: a mover pod tried to change the config or the
scripts of an agent, which is the attack the policy is for, or a new release
of the backup operator writes a ConfigMap there. It fires for one to two
hours after each denial (`keep_firing_for: 1h`). A gap of more than an hour
in the scrape of the API server makes the series look new, and fires it once
with no denial: then the audit lines show none. The rule reads a counter of the API server that only
homelab-wsl keeps (`kubernetes/clusters/wsl/apps/kustomization.yaml`); the
other clusters run no agent.

**First checks.** In Grafana, Loki of the security streams, the audit lines
of the hour of the alert:

```logql
{security_stream="k8s-audit", k8s_cluster="homelab-wsl"} |= "configmaps" |= "kopiur" | json
  | objectRef_resource="configmaps" | responseStatus_code="422"
```

Each line names the namespace, the ConfigMap, the verb and the
ServiceAccount (`user_username`). Then, read only:

```sh
kubectl -n <namespace> get events --sort-by=.lastTimestamp
kubectl -n <namespace> get jobs
kubectl -n <namespace> get snapshotschedules.kopiur.home-operations.com
kubectl -n kopiur-system get helmrelease kopiur
```

The pod of a mover is gone seconds after it ends (the pod garbage
collection of this cluster, `terminated-pod-gc-threshold: 1` in
`talos/wsl/cluster-config.yaml`), so the audit lines and the events of the
namespace are the evidence: the events name the mover Job and its pod of
that time, and the audit lines name what it tried to write. Compare the
release of the backup operator that runs with the one in git
(`kubernetes/apps/kopiur-system/kopiur/app/helmrelease.yaml`). Events stay
about an hour on the cluster, so read them first.

**Seen so far.** Not fired yet. On 2026-11-15 the API server had counted no
denial of this policy since it was added on 2026-11-14
(`kubectl get --raw /metrics`). The other policy of the platform,
`workflow-run-jobs`, had 22 denials then; no alert reads it.

**Fix.**

1. **Stop the movers in that namespace.** Owner approval: a pull request
   sets `suspend: true` on its SnapshotSchedule (`backup.yaml` of the
   agent), so no new mover runs there while you look.
2. **A new release of the backup operator.** Its notes or its code name a
   ConfigMap write in the namespace of a snapshot: pin the operator back, or
   change the policy in
   `kubernetes/apps/ai/agents/admission/app/mover-configmaps.yaml` with that
   evidence, each by a pull request (owner approval).
3. **No such release.** Treat it as an attack on the config of the agent:
   the mover ran code that the operator does not run. Keep the snapshots
   suspended, and give the audit lines and the events to the owner, who
   decides the next step. The refused write changed nothing, so the agent
   keeps running on the config in git.
4. Set `suspend: false` again by a pull request when the cause is clear.

Not tested.

### AgentBackupRepositoryNotReady

**What it means.** The backup repository of the agent trust domains
(ClusterRepository `agents`) has been `Pending` or `Initializing` for an
hour. It never got `Ready`, so no snapshot of a trust domain runs, and the
policies have no failure to show. `Failed` or `Degraded` is the chart rule
`KopiurRepositoryNotReady` instead.

**First checks.**

```sh
kubectl get clusterrepository agents -o yaml          # the Ready condition
kubectl -n kopiur-system get cronjob aws-backup-credentials-agents
kubectl -n kopiur-system get secret aws-backup-sts-agents -o jsonpath='{.metadata.annotations.backups\.tnwks\.us/expires}'
kubectl -n kopiur-system get jobs | grep agents
```

**Seen so far.** Never.

**Fix.** A session that is not there or has expired: run the refresher once
(`docs/operations/backups.md`, "Credentials"). A `suspend: true` on the
repository: remove it by a pull request. Anything else: the condition
message and the log of the discovery Job.

### ModelBridgeRateLimited

**What it means.** The model bridge logged an `engine_error` line with the
kind `rate_limit` in the last 20 minutes, for a trust domain: the limit of
the Claude plan stopped a call. The calls of that domain fail until the
limit resets.

**First checks.** In Loki:
`{namespace="ai", app="model-bridge"} |= "engine_error"`, and the `call`
lines of the same minutes (status 429 from release 0.2.3).

**Seen so far.** 2026-11-14, 02:29 to about 05:30 UTC: the 5-hour limit of
the plan. See [the plan limit](#at-a-limit-of-the-claude-plan).

**Fix.** [At a limit of the Claude plan](#at-a-limit-of-the-claude-plan).

### AgentHeartbeatMissing

**What it means.** The daily `agent-heartbeat-check` (06:33 UTC) found no
completed heartbeat turn of an agent in Tempo in the last 26 hours, or could
not ask Tempo. It asks 3 times, 60 seconds apart, and a failed pod gets one
more try. It resolves at the next good run.

**First checks.** In Loki,
`{namespace="monitoring", app="agent-heartbeat-check"}`:
`result=missing service=<service>` is an agent that did not complete its
heartbeat, `result=error` is a check that could not ask Tempo. Then:

```sh
kubectl -n agents-pilot get pods
kubectl -n agents-pilot logs deploy/agent -c sandbox-start
kubectl -n agents-pilot get sandboxes.agents.x-k8s.io
```

**Seen so far.** Not fired. Its first scheduled run is 2026-11-15 at 06:33
UTC. A read-only run against Tempo before the merge found the heartbeat of
the pilot on 2026-11-13 and on 2026-11-14.

**Fix.** An agent pod that is ready and has no heartbeat: a stopped sandbox
(the `sandbox-start` lines), the model path (the bridge lines), or a channel
that stopped. Start the agent pod again (a delete of the pod, owner
approval), then revert the last update if it does not come back.

### AgentHeartbeatCheckStale

**What it means.** The heartbeat check itself was not scheduled in 26 hours,
or its CronJob is gone.

**First checks.**

```sh
kubectl -n monitoring get cronjob agent-heartbeat-check
kubectl -n flux-system get kustomization agents-monitoring
```

**Seen so far.** Never.

**Fix.** A suspended CronJob, or one that Flux did not apply: fix it in
git.

### KopiurLastBackupFailed

**What it means.** A rule of the kopiur chart: the last snapshot of a
SnapshotPolicy failed. On the platform the policies are `agent-state-v2` (and
`signal-state` when Signal is on) in `agents-pilot`. They write to the
repository of the agent trust domains, ClusterRepository `agents`
([`agent-platform.md`](agent-platform.md#backups-of-the-agent-trust-domains)).

**First checks.**

```sh
kubectl -n agents-pilot get snapshotpolicies,snapshots
kubectl -n agents-pilot describe snapshot <snapshot>
kubectl get clusterrepository agents
kubectl -n kopiur-system get cronjob aws-backup-credentials-agents
kubectl -n kopiur-system get pods -l app.kubernetes.io/name=aws-backup-credentials-agents
kubectl -n kopiur-system get secret aws-backup-sts-agents -o jsonpath='{.metadata.annotations.backups\.tnwks\.us/expires}'
```

**Seen so far.** The first snapshot of the pilot's state volume failed on
2026-11-12 at the connection to the repository: the mover reaches the
dual-stack name of the object store, and the network policy of the mover did
not allow it (`5e219085`). The first scheduled snapshot after the fix
passed the same day.

**Fix.** Fix the policy or the network rule in git. The schedule `H 2 * * *`
runs in local time, not in UTC. An S3 error of access names the session of
the agents repository: its refresher, its certificate, or the role kept to
`kopiur-agents/` (a policy of the agents repository that names another prefix
fails here by design). A repository in `Failed` after a session error: run the
refresher once (`docs/operations/backups.md`, "Credentials"). [`backups.md`](../operations/backups.md) has
the rest.

### KopiurBackupStale

**What it means.** A rule of the kopiur chart: no good snapshot of a policy
in 48 hours.

**First checks.** As for [KopiurLastBackupFailed](#kopiurlastbackupfailed),
and `kubectl -n agents-pilot get snapshotschedules`.

**Seen so far.** Never.

**Fix.** As for [KopiurLastBackupFailed](#kopiurlastbackupfailed).

### KopiurSnapshotFailed

**What it means.** A rule of the kopiur chart: a Snapshot of a policy is in
phase `Failed` for 10 minutes, and the policy has no good backup since.

**First checks.** As for [KopiurLastBackupFailed](#kopiurlastbackupfailed):
`kubectl -n agents-pilot describe snapshot <snapshot>` names the error.

**Seen so far.** The first snapshot of `agent-state`, on 2026-11-12 (see
KopiurLastBackupFailed).

**Fix.** As for [KopiurLastBackupFailed](#kopiurlastbackupfailed).

### KopiurBackupConsecutiveFailures

**What it means.** A rule of the kopiur chart: 3 or more snapshots of a
policy failed in a row, for 15 minutes. One fault repeats each night.

**First checks.** As for [KopiurLastBackupFailed](#kopiurlastbackupfailed),
on the newest Snapshots.

**Seen so far.** Never.

**Fix.** As for [KopiurLastBackupFailed](#kopiurlastbackupfailed). The days
since the last good snapshot have only the archives on the volume.

### KopiurRestoreFailed

**What it means.** A rule of the kopiur chart: a Restore is in phase
`Failed` for 10 minutes. On the platform it can come only from a restore of
this runbook ([after the loss of the volume](#after-the-loss-of-the-volume)).

**First checks.**

```sh
kubectl -n agents-pilot get restores
kubectl -n agents-pilot describe restore <restore>
```

The mover pod can be gone already (the pod GC), so its log is in Loki. A
mover has no name label: the read of 2026-11-14 found the snapshot mover of
`agent-state` under `{namespace="agents-pilot", app="unknown"}`.

**Seen so far.** Never. No restore has run on the platform.

**Fix.** Keep the agent at zero replicas. Fix the Restore in its pull request
(the snapshot, the mover user, the network rule of the mover), and remove the
failed Restore in the same pull request, as the restore steps say.

## An agent does not answer on its channel

The pod is ready, and a message to the agent gets no answer. No alert reads
the receive path of a channel; `AgentHeartbeatMissing` fires on the next
morning when the scheduled turn did not complete.

1. **Read only.** The pod, the sandboxes and the model path:
   ```sh
   kubectl -n agents-pilot get pods
   kubectl -n agents-pilot logs deploy/agent -c sandbox-start --tail=20
   kubectl -n agents-pilot get sandboxes.agents.x-k8s.io
   ```
   In Loki, the `call` and `engine_error` lines of the bridge
   (`{namespace="ai", app="model-bridge"}`) for the minutes of the message.
   A plan limit is [its own case](#at-a-limit-of-the-claude-plan).
   The start line of the gateway lists the plugins that loaded
   (`kubectl -n agents-pilot logs deploy/agent -c agent | grep 'http server listening'`):
   `brave, diagnostics-otel, memory-core, openshell, telegram`. A plugin
   that is missing there is missing from `plugins.allow` or its entry in
   `openclaw.json` ([agent-platform.md](agent-platform.md), "Plugins").
2. **Read only, by the owner.** The log of the `agent` container
   (`kubectl -n agents-pilot logs deploy/agent -c agent --since=30m`). It
   can hold chat content, so it never ships to Loki; the owner reads it, and
   nothing of it goes into an issue or a pull request.
3. **Start the pod again.** Owner approval: a delete of the pod
   (`kubectl -n agents-pilot delete pod -l app.kubernetes.io/name=agent`).
   The archive step and `doctor` run as at each start; a start took 32 to
   42 seconds on 2026-11-12.
4. **Revert the last update** if the agent still does not answer, in a pull
   request (owner approval).

## A sandbox does not start

A tool call of the agent fails with "sandbox is not ready", or a run of a
workflow agent ends with exit 3.

1. **Read only.**
   ```sh
   kubectl -n <namespace> get sandboxes.agents.x-k8s.io
   kubectl -n <namespace> get pods
   kubectl -n agent-sandbox-system get pods
   kubectl -n <namespace> logs deploy/agent -c sandbox-start --tail=20   # a personal agent
   ```
   Each sandbox of the product (`oc-`) should be `Ready`. The
   `sandbox-start` container starts a `Stopped` one within a minute
   (`sandbox-start name=<name> result=ok`); `result=failed` repeats each
   minute. No line at all is normal: it logs only when it starts a sandbox.
2. **Seen so far.** After a power cut restarted the host on 2026-11-12, the
   sandbox gateway left each sandbox of the pilot `Stopped`, and the product
   does not start a stopped one. The `sandbox-start` container
   (`fcce4f2a`) starts them; a sandbox stopped on purpose that day was back
   in 47 seconds.
3. **Fix.** A sandbox gateway or a sandbox controller that is not ready is
   [AgentPlatformWorkloadNotReady](#agentplatformworkloadnotready). Else
   read the `result=failed` lines and the events of the sandbox pods, and
   fix the cause in git.

## Web search does not work

The pilot's agent says that a web search failed, or that it has no search
tool. The parts are in [agent-platform.md](agent-platform.md), "Web
search". The tool runs in the gateway process of the agent pod. At this
release the product makes one request for each search, with no retry, and
a limit of 30 seconds. The model gets one of the errors below; the
gateway's log gets no line for a failed search. So the signs are the answer
in the chat and the `web_search` tool span of the turn in Tempo (service
`openclaw-agents-pilot`), which carries the error but no query and no
result.

| What the model gets | Cause |
| --- | --- |
| `provider_error: Search failed (HTTP 401)` or `(HTTP 403)`, "Check this search provider's credentials and access" | A wrong, revoked or expired key |
| `provider_error: Search failed (HTTP 429)`, "Check this search provider's quota or try again later" | The vendor's rate limit or the plan's quota. The key is shared with another deployment of the owner, and the vendor counts both |
| `provider_error: Search failed (HTTP 5xx)`, "Check this search provider's configuration and connection" | The vendor is down |
| `Search timed out after 30s` | No answer in 30 seconds: the vendor is slow, or the network policy drops the flow (below) |
| `web_search is unavailable. Enable or select a bundled or verified official search provider for sandboxed agents.` In the `agent` container's log: `[tools] dispatch_action failed: ... web_search is unavailable ...`. The tool span ends with an error in a few milliseconds, and no flow goes to the vendor | The Brave plugin loaded, but not as a verified official install. A sandboxed session (each session of the pilot) uses a search provider only when it is bundled or a verified official install, and a plugin from `plugins.load.paths` is neither. The `official-plugins` init container makes it one (below) |
| No `web_search` tool at all | The tool is not in the allow list of a sandboxed session, or the plugin did not load (below) |

(The messages come from `src/web-search/runtime-error.ts:36-70` and
`src/web-search/runtime.ts:568-573` of the product at 2026.11.3; the
sandbox filter is `src/plugins/web-provider-resolution-shared.ts:85-90`.)

1. **Read only.** The pod and its start:
   ```sh
   kubectl -n agents-pilot get pods -l app.kubernetes.io/name=agent
   kubectl -n agents-pilot get deploy agent -o jsonpath='{.spec.template.spec.containers[0].image}'
   ```
   The image must be 0.4.0 or later: earlier images load the Brave plugin
   from a path, and a sandboxed session refuses it.
2. **Read only.** The official plugins step of this start:
   ```sh
   kubectl -n agents-pilot logs deploy/agent -c official-plugins | grep '^official-plugins'
   ```
   `official-plugins id=brave version=<release> result=installed` is good:
   the step installs the plugin again from the image at each start.
   `result=failed` stops the start of the whole pod (below), and the lines
   above it are the product's installer.
3. **Read only, by the owner.** The start lines of the `agent` container
   (`kubectl -n agents-pilot logs deploy/agent -c agent | grep -E 'http server listening|plugins\]'`).
   - `http server listening (5 plugins: brave, ...)` means the plugin
     loaded. With no `brave` in that list, check the step above and the
     entries `plugins.entries.brave` and `plugins.allow` in `openclaw.json`.
   - A line `OpenClaw can't verify where this plugin came from. Review it
     with 'openclaw plugins inspect brave'` means the plugin loaded from a
     path, not as a verified official install: a sandboxed session refuses
     it. Take its path out of `plugins.load.paths`. The same line for
     `openshell` is expected: the sandbox backend loads from a path, and
     needs no trust.
   - `WEB_SEARCH_PROVIDER_INVALID_AUTODETECT` means the provider `brave`
     is set but no plugin gives it: the plugin did not load.
   - The check of trust, in the `agent` container:
     `openclaw plugins inspect brave --json` must show `"origin": "global"`,
     `"trustedOfficialInstall": true` and `"reason": "trusted-official"`.
     `openclaw sandbox explain` is not this check: it shows the tool policy
     (the names a sandbox allows, with `web_search` in it), not whether the
     provider behind the tool is trusted. So it shows `web_search` as
     allowed while each call fails.
4. **Read only.** The allow list of a sandboxed session: each session of
   the pilot is sandboxed, and the product leaves `web_search` out of one
   by default. It must be in `tools.sandbox.tools.alsoAllow`, and
   `tools.web.search.enabled` must be `true`:
   ```sh
   kubectl -n agents-pilot get configmap agent-config -o jsonpath='{.data.openclaw\.json}' | jq '.tools'
   ```
5. **Read only.** The network path: a dropped flow to the vendor shows in
   Hubble, through a read-only port-forward to `hubble-relay` in
   `kube-system`:
   ```sh
   hubble observe --namespace agents-pilot --to-fqdn api.search.brave.com --since 30m
   hubble observe --namespace agents-pilot --verdict DROPPED --since 30m
   ```
   A search makes one flow from the agent pod to `api.search.brave.com:443`,
   `FORWARDED`. A `DROPPED` flow means that the rule of
   `pilot/agent/app/networkpolicy.yaml` does not match. A flow from any
   other pod of the namespace must stay `DROPPED`.
6. **Fix, through a merge.**
   - **A wrong or revoked key:** the owner makes a new key at the vendor,
     puts it in `kubernetes/apps/ai/agents/pilot/agent/app/secret-search.sops.yaml`
     (`sops <file>`) in a pull request, and changes the other deployment
     that shares the key too. The merge changes the Secret, and Reloader
     starts the pod again.
   - **A rate limit or the vendor down:** wait. Nothing in the cluster
     changes it.
   - **The allow list, the plugin or the network rule:** a pull request
     that puts back the lines of [agent-platform.md](agent-platform.md),
     "Web search".
   - **Not a verified official install:** the image at 0.4.0 or later, the
     `official-plugins` init container before `doctor`, and no Brave path
     in `plugins.load.paths`.
7. **Turn search off,** through a merge: set `tools.web.search.enabled` to
   `false` in `openclaw.json` and take `web_search` out of
   `tools.sandbox.tools.alsoAllow`. The change of the config starts the pod
   again. Then the agent has no search tool, and nothing goes to the
   vendor.

## The official plugins step fails

The pod of the pilot does not start: its `official-plugins` init container
failed, and `AgentPlatformWorkloadNotReady` fires after 10 minutes. The
step installs the Brave plugin from the image at each start
([agent-platform.md](agent-platform.md), "Web search"). It stops the whole
pod on purpose, and does not let the gateway start without it, for two
reasons:

- The step also checks the state volume for plugins that the image did not
  put there: an install record of another plugin, or any file in
  `state/extensions`. Such a plugin would run in `doctor` and in the
  gateway, with the secrets of the agent. A start that goes on would run
  it.
- With no plugin, `doctor` tries to install it from the npm registry. The
  pod has no route there, so `doctor` waits for its timeouts and records an
  unfinished upgrade of the plugin.

1. **Read only.** The line of the step:
   ```sh
   kubectl -n agents-pilot logs deploy/agent -c official-plugins | grep '^official-plugins'
   ```
   - `result=failed reason=an install record of another plugin: <id>` or
     `reason=files in .../extensions`: something wrote a plugin to the
     state volume. Treat it as a security event: keep the pod down, take a
     copy of the volume for the review, and find the writer before a
     restore.
   - `id=brave ... result=failed`: the install itself failed. The lines
     above it are the product's installer. An image of a bad build fails
     the same step in CI, so look at the release first.
2. **Fix.**
   - **A failed install:** revert the image pin to the last release that
     started, through a merge.
   - **A plugin that the image did not put there:** do not revert the
     image; an image before 0.4.0 has no step and would start with that
     plugin. Restore the state from a backup taken before the write
     ([Restore the state of an agent](#restore-the-state-of-an-agent)),
     with owner approval.
   - Turning web search off does not skip the step.

## Memory search finds nothing

A memory search of the pilot returns `stale: true` and no result, or only
old results. The index did not come up to date at the last start of the
pod, because its embedding call failed: the model gateway, or the model
server of the host behind it, did not answer, or the step ran over its
limit. The step's line is `memory-index result=failed`, and the alert is
[AgentMemoryIndexFailed](#agentmemoryindexfailed), which has the checks and
the fix. How the step works is in [agent-platform.md](agent-platform.md),
"The index at each start".

## Restore the state of an agent

The state volume of an agent holds `state/` (the product, with its workspace)
and `archives/` (`start/` from the archive step at each start, kept 14 days;
`daily/` from the daily backup, the last 7). Kopiur copies the whole volume
each night. An archive holds credentials: it never leaves the volume or the
snapshot.

Two cases, each step marked with its test. The owner runs a restore; each
step that changes the cluster is a pull request with owner approval.

**The Signal daemon is off today.** When it is on, its volume `signal-state`
has its own start archive and snapshot policy, and its restore needs steps
of its own, which this page does not have yet: an old copy of a Signal
account can stop to work after new messages.

**The result of a Job or a Restore.** The pod GC of this cluster deletes an
ended pod within seconds, so `kubectl logs job/<name>` often fails. Read the
state of the object: `kubectl -n agents-pilot get job <name>` shows
`1/1` when the script ended with exit 0 (it runs with `set -eu`, so each
failed command fails the Job), and `kubectl -n agents-pilot get restores`
shows the phase. Then read the lines in Loki. Job pods ship through the
shared log agent on this cluster (`VECTOR_AGENT_POD_SELECTOR` in
`kubernetes/clusters/wsl/cluster-settings.yaml`), with the `app` label from
`app.kubernetes.io/name`: each run of the restore Job, whatever its name,
is `{namespace="agents-pilot", app="agent-restore"}`. On 2026-11-14 Loki held the
lines of the Job pods of the checks in `agents-pilot`; the restore Job
itself has not run.

### The restore Job

One Job does the restore inside the volume, with the agent at zero
replicas: it checks the archive, moves the state of now aside to
`state.before-restore-<time>`, and puts the restored state in place. It has
no `${...}`, because Flux substitutes those in this directory.

Add it as `kubernetes/apps/ai/agents/pilot/agent/app/restore-job.yaml`, with
the image and the `nodeSelector` of `deployment.yaml`, the archive in
`ARCHIVE`, and the file in the `kustomization.yaml` of the directory.

- **`ARCHIVE=list`** changes nothing: it lists the archives with their sizes
  and times, for the steps that pick one.
- **`ARCHIVE=daily/newest`** takes the newest daily archive on the volume,
  and logs its name (`restore archive=<name>`). Use it only when the volume
  holds a snapshot and nothing newer, as after the populator form below.
- **One name for each run.** A Job cannot run again, and its template
  cannot change: Flux fails with "field is immutable", and no Kustomization here
  sets `force`. So each run gets its own name: `agent-restore-list` for a
  list, `agent-restore` for the restore, `agent-restore-2` for a retry. The
  pull request that adds one removes the one before. The label
  `app.kubernetes.io/name: agent-restore` stays the same, so one Loki query
  finds each run.

```yaml
---
# A one-time restore of the state volume (docs/apps/agent-platform-runbook.md).
# Remove it in the pull request that starts the agent again.
apiVersion: batch/v1
kind: Job
metadata:
  # One name for each run: agent-restore-list, agent-restore, agent-restore-2.
  name: agent-restore
spec:
  backoffLimit: 0
  template:
    metadata:
      labels:
        app.kubernetes.io/name: agent-restore
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      enableServiceLinks: false
      nodeSelector: {}   # the nodeSelector of deployment.yaml
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: restore
          image: ghcr.io/it3e/tnwks-agent-image:<the tag and digest of deployment.yaml>
          env:
            # start/openclaw-state-<release>-<time>.tar.gz, or
            # daily/openclaw-backup-<day>.tar.gz, daily/newest, or list
            - name: ARCHIVE
              value: daily/openclaw-backup-YYYY-MM-DD.tar.gz
            - name: OPENCLAW_OFFLINE
              value: "1"
          command: ["sh", "-c"]
          args:
            - |
              set -eu
              DATA=/home/node/data
              if [ "$ARCHIVE" = daily/newest ]; then
                ARCHIVE=daily/$(cd "$DATA/archives/daily" && ls openclaw-backup-*.tar.gz | tail -n 1)
                echo "restore archive=$ARCHIVE"
              fi
              A="$DATA/archives/$ARCHIVE"
              stamp=$(date -u +%Y%m%dT%H%M%SZ)
              stage="$DATA/restore-$stamp"
              if [ "$ARCHIVE" = list ]; then
                ls -l --time-style=+%Y-%m-%dT%H:%MZ "$DATA/archives/start" "$DATA/archives/daily"
                exit 0
              fi
              test -f "$A"
              case "$ARCHIVE" in
                start/*)
                  (cd "$(dirname "$A")" && sha256sum -c "$(basename "$A").sha256")
                  mkdir -m 0700 "$stage"
                  tar -xzf "$A" -C "$stage"
                  new="$stage"
                  ;;
                daily/*)
                  made=$(tar -xzOf "$A" --wildcards '*/manifest.json' | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>console.log(JSON.parse(s).runtimeVersion))')
                  here=$(node /app/openclaw.mjs --version | grep -oE '[0-9]{4}\.[0-9]+\.[0-9]+' | head -n 1)
                  if [ "$made" != "$here" ]; then
                    echo "restore result=failed reason=release archive=$made image=$here"
                    exit 1
                  fi
                  node /app/openclaw.mjs backup restore "$A" --target "$stage"
                  new=$(find "$stage" -type d -path '*/payload/posix/home/node/data/state' | head -n 1)
                  test -n "$new"
                  ;;
                *)
                  echo "restore result=failed reason=archive"
                  exit 1
                  ;;
              esac
              if [ -d "$DATA/state" ]; then mv "$DATA/state" "$DATA/state.before-restore-$stamp"; fi
              mv "$new" "$DATA/state"
              chmod 0700 "$DATA/state"
              rm -rf "$stage"
              echo "restore result=ok archive=$ARCHIVE kept=state.before-restore-$stamp"
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: data
              mountPath: /home/node/data
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: agent-state
        - name: tmp
          emptyDir: {}
```

- A start archive is a plain copy of `state/`; the Job checks its checksum
  file, then unpacks it.
- A daily archive is the backup format of the product. The Job refuses it
  when the image is not the release that made it (`runtimeVersion` in the
  archive's manifest): the product refuses to run older code on state that
  a newer release wrote, and its docs say to restore with the matching
  release. `openclaw backup restore` checks the archive and unpacks it into
  a new directory; the product asks for `openclaw doctor` after, which the
  `doctor` init container runs at the next start.
- The state that was there stays in `state.before-restore-<time>`, on the
  same volume, until a later step removes it.

**Tested on 2026-11-14, locally:** the script ran in the agent image 0.2.0
(OpenClaw 2026.11.3), as uid 1000 with a read-only root, on a scratch Docker
volume. It put back the start archive and the daily archive (the file
changed after the archive came back to its old text), and it failed on an
archive that is not there. `ARCHIVE=list` listed the archives.
`ARCHIVE=daily/newest` took the newest of two daily archives, and with no
daily archive it failed with exit 1 and changed nothing. A daily
archive with another release in its manifest failed with
`restore result=failed reason=release` and left the state as it was. The
`tar -xzOf` read of one file of a daily archive (in
[AgentInstructionFileChanged](#agentinstructionfilechanged)) printed the
file. Not tested: the Job on the cluster.

### After the loss of the volume

The node or its disk is gone, or the volume is empty or broken. The newest
daily archive from before the fault is in a good snapshot. None of these
steps is tested.

The backup container makes the daily archive of a UTC day in the first hour
of that day, or at the first start of the pod that day: it checks once an
hour (`agent/config/scripts/daily-backup.sh`). The snapshot runs each day
between 02:00 and 03:00 local time, at a minute that kopiur derives from the
schedule object (`H 2 * * *`; 10:30 UTC for `agent-state`, read
`kubectl -n agents-pilot get snapshots` for `agent-state-v2`). So the snapshot
of a day holds the daily archive of that day when the pod ran.

1. **Read the state of the backups and of the volume.** Read only.
   ```sh
   kubectl -n agents-pilot get snapshots --sort-by=.metadata.creationTimestamp
   kubectl get clusterrepository agents
   kubectl -n agents-pilot get pvc agent-state
   kubectl get pv "$(kubectl -n agents-pilot get pvc agent-state -o jsonpath='{.spec.volumeName}')" \
     -o jsonpath='{.spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[0].values}'
   kubectl get nodes
   ```
   Pick the snapshot by its name, from before the fault, with phase
   `Succeeded`. Not the newest by default: the schedule runs each day
   during an outage too, so the newest can be a copy of an empty or broken
   volume. The read of 2026-11-14 showed one snapshot each day, each
   `Succeeded` from 2026-11-12.
   These are the snapshots of the policy `agent-state-v2`, in the repository
   `agents`. The snapshots from before the move (policy `agent-state`) are in
   the shared repository `s3` and have no Snapshot object any more: kopiur
   deleted the objects with the old policy and kept the snapshots. Restore
   one by [the steps for a snapshot from before the move](#a-snapshot-from-before-the-move).
2. **Decide the node and the volume first.** This decides each step after
   it. A `local-path` volume lives on one node (its PV has a node affinity,
   and the class binds at the first pod: `WaitForFirstConsumer`). The agent
   and the restore Job run only on the node of their `nodeSelector`. A
   Restore has no node setting for its mover
   (`kubectl explain restores.spec.mover`), so its mover can bind a new
   volume on any node. The PV and the class have the reclaim policy
   `Delete`: a delete of the PVC deletes the PV, and the provisioner wipes
   its directory on the node.
   Tell the cases apart by what the cluster shows, never by a guess. Take
   the name of the PV from `spec.volumeName` of the PVC (step 1).
   - **A: the PV is there, and its node is up** (the data is broken):
     `kubectl get pv <volumeName>` shows the PV, and
     `kubectl get node <node of the PV>` shows `Ready`. The `pvcRef` form,
     into the volume that is there.
   - **B: the PV is gone, and the node is up:** `kubectl get pv <volumeName>`
     answers `NotFound`, and `kubectl -n agents-pilot get pvc agent-state`
     shows `Lost`. The populator form. The stop
     pull request (step 3) takes `pvc.yaml` out of the `kustomization.yaml`
     of the directory, so Flux deletes the PVC through git: the
     Kustomization `agents-pilot-agent` has `prune: true`, and the PVC has
     no annotation that turns pruning off (read on 2026-11-14: only the
     usual `kubernetes.io/pvc-protection` finalizer, which waits until no
     pod uses the volume). No hand delete: Flux would make the PVC again at
     once, with no `dataSourceRef`.
   - **C: the node is gone for good:** the PV is there, its node is
     `NotReady` or missing in `kubectl get nodes`, and the owner says that
     the node does not come back. As B, and the stop pull request also
     changes the `nodeSelector` of `deployment.yaml` to a node that is there.
     A node that is only down still holds the data newer than the snapshot,
     so for it, wait for the node.

   **Before any stop pull request that takes `pvc.yaml` out of
   `kustomization.yaml`, set each PV that `kubectl get pv` still shows for
   this claim to `Retain` (owner approval):
   `kubectl patch pv <volumeName> -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'`.**
   Then the prune of the PVC cannot wipe the directory, also when a case
   was misread or a node comes back. A retained PV stays `Released`. Remove
   it only after the restored agent passes step 7 (owner approval): set it
   back to `Delete` with the same command, and the provisioner removes the
   PV and its directory; for a node that is gone, `kubectl delete pv
   <volumeName>` removes the object. This last part follows from how
   Kubernetes and the provisioner treat a reclaim policy, and is not tested
   on this cluster.
3. **Stop the agent and the snapshots.** Owner approval. One pull request
   sets `replicas: 0` in `pilot/agent/app/deployment.yaml`, and
   `suspend: true` in the SnapshotSchedule `agent-state-v2` of `backup.yaml`, so
   no new snapshot copies the broken volume. For B and C it also takes
   `./pvc.yaml` out of `kustomization.yaml`, and for C it changes the
   `nodeSelector`. After the merge, wait until
   `kubectl -n agents-pilot get pods -l app.kubernetes.io/name=agent` shows
   no pod, and for B and C until `kubectl -n agents-pilot get pvc` shows no
   `agent-state`. In C the PVC can stay `Terminating`: the old agent pod on
   the dead node stays `Terminating` while its Node object exists, and the
   `kubernetes.io/pvc-protection` finalizer holds the PVC for it. Then remove
   the dead node (`kubectl delete node <node>`, owner approval) or force the
   delete of the old pod
   (`kubectl -n agents-pilot delete pod <pod> --grace-period=0 --force`,
   owner approval). This follows from how Kubernetes treats a pod on a lost
   node, and is not tested on this cluster.
4. **Restore the snapshot.** Owner approval. A pull request adds a kopiur
   `Restore` to `pilot/agent/app/` (the form is in
   [`backups.md`](../operations/backups.md#a-pvc-kopiur)), with
   `source.snapshotRef.name` set to the snapshot of step 1,
   `credentialProjection.enabled: true`, and the `podSecurityContext` and
   `securityContext` of the mover in `backup.yaml` (uid 1000, the restricted
   level).
   - **A, the `pvcRef` form.** First a pull request adds the restore Job as
     `agent-restore-list` with `ARCHIVE=list`. If the archives read back,
     skip the Restore: step 5 uses the newest daily archive from before the
     fault, from that list. Else the next pull request removes the list Job
     and adds the Restore with `target.pvcRef.name: agent-state` and no
     `enableFileDeletion`. That option makes an exact mirror, and it deletes
     each file the snapshot does not have, the start archives made since the
     snapshot too. With no file deletion, `state/` comes out as a mix of the
     snapshot and the broken files. That does no harm: step 5 moves all of
     `state/` aside and puts the archive in its place.
   - **B and C, the populator form.** `target.populator: {}`, and in the same
     pull request `pvc.yaml` goes back into `kustomization.yaml` with
     `dataSourceRef: {apiGroup: kopiur.home-operations.com, kind: Restore, name: <the Restore>}`.
     The same pull request adds the restore Job as `agent-restore-list` with
     `ARCHIVE=list`: it is the first pod of the new volume, so its
     `nodeSelector` puts the volume on the node of the agent, and its list
     shows what the snapshot holds once the volume is filled.

   Wait for the phase `Completed` of the Restore
   (`kubectl -n agents-pilot get restores`). `KopiurRestoreFailed` fires on
   a failed one.

   #### A snapshot from before the move

   The shared repository `s3` no longer allows `agents-pilot`, so the
   pull request of this step also adds `agents-pilot` back to
   `allowedNamespaces` in
   `kubernetes/apps/kopiur-system/kopiur/repository/clusterrepository.yaml`,
   and the Restore names the repository and the kopia identity of the old
   policy instead of `source.snapshotRef`. The identity is fixed by the
   `identityDefaults` of `s3` (host: the namespace; user: the namespace, a
   dash and the policy name) and the source path of the PVC, as the old
   Snapshot objects showed it:

   ```yaml
   spec:
     repository:
       kind: ClusterRepository
       name: s3
     source:
       identity:
         username: agents-pilot-agent-state
         hostname: agents-pilot
         sourcePath: /pvc/agent-state
         # One of the three:
         asOf: "2026-11-14T12:00:00Z"   # the newest snapshot at or before this time
         # offset: 0                    # 0 the newest, 1 the one before
         # snapshotID: <ID>             # from the list kept at the move
   ```

   `asOf` with a time from before the fault is the usual pick. A
   `snapshotID` comes from the list that the owner kept at the move
   (`kubectl -n agents-pilot get snapshots.kopiur.home-operations.com
   -o custom-columns=NAME:.metadata.name,PHASE:.status.phase,END:.status.timing.endTime,ID:.status.snapshot.kopiaSnapshotID`,
   run before the merge). For that restore the mover gets the session and
   the password of the shared repository, which reach every snapshot of the
   household apps. So the pull request of step 6 removes `agents-pilot` from
   that list again, and no snapshot schedule of the pilot names `s3`. Not
   tested.
5. **Restore the daily archive.** Owner approval. The snapshot copies the
   live files of a running pod, so its database can be torn; the daily
   archive in it is the consistent copy. A pull request removes the list
   Job and adds [the restore Job](#the-restore-job) as `agent-restore`, with
   the image of the release that made the archive. For A,
   `ARCHIVE=daily/openclaw-backup-<the newest day from before the fault>.tar.gz`,
   from the list. For B and C, the same name from the list after the fill,
   or `ARCHIVE=daily/newest`: the new volume holds the snapshot and nothing
   newer. Wait until `kubectl -n agents-pilot get job agent-restore` shows
   `1/1`, then read `restore result=ok` in Loki
   (`{namespace="agents-pilot", app="agent-restore"}`). The script was tested
   locally (above). After a refused archive, a retry is `agent-restore-2`.
   If the snapshot ran while a daily backup was in work, `daily/newest` can
   take a part-written archive: the Job then fails with no change, and the
   retry names the archive of the day before.
6. **Start the agent and the snapshots.** Owner approval. A pull request
   removes the Restore and the Job, sets `replicas: 1`, and removes
   `suspend: true` from the SnapshotSchedule `agent-state-v2`. After a
   snapshot from before the move, it also removes `agents-pilot` from the
   `allowedNamespaces` of `s3` again. For B and C, keep `dataSourceRef` in `pvc.yaml`: the
   field cannot change on a PVC that exists, so a removal fails the apply.
   The `archive` init container makes an archive of the restored state, and
   `doctor` checks it.
7. **Check.** Read only: the pod is ready, the `sandbox-start` lines, an
   answer on the test channel (the owner), and the next
   `instruction-check result=` line. The restored state goes back to the
   time of the archive; the product says so in its warnings (approvals and
   the state of each channel go back too).
8. **Remove the old state.** Owner approval, later: the
   `state.before-restore-<time>` directory, by a Job in a pull request as
   above, with a name of its own.

### After a failed update

An update of the agent image moved the state to a new format, and the agent
does not run. The way back is the release before, with the state from before
the update. The first start archive with the name of the failed release holds
that state: the archive step ran before the `doctor` of the new release. The
newest archive is the wrong one: a later start made it from the changed
state. None of these steps is tested on the cluster.

1. **Find the archive.** Read only. In Loki,
   `{namespace="agents-pilot", app="agent"} |= "archive-state result=ok"`:
   each line names the release and the file
   (`release=<release> name=openclaw-state-<release>-<time>.tar.gz`). Take the
   first line with the failed release. Loki holds these lines only from
   2026-11-14, when the logs of the agent pod started to ship. With no line,
   use the name: the file name holds the release and the UTC time of the
   start, so the earliest name with the failed release is the one. List the
   names after step 2 with [the restore Job](#the-restore-job) as
   `agent-restore-list` and `ARCHIVE=list` (owner approval), in Loki under
   `app="agent-restore"`.
2. **Stop the agent and set the image back.** Owner approval. One pull
   request sets `replicas: 0` and reverts the image in `deployment.yaml` to
   the release before (a revert of the Renovate merge, with
   `replicas: 0` on top). Wait until no agent pod is there.
3. **Restore the archive.** Owner approval. A pull request removes the list
   Job, if there is one, and adds [the restore Job](#the-restore-job) as
   `agent-restore` with
   `ARCHIVE=start/openclaw-state-<failed release>-<time>.tar.gz`, and the
   image of the release before. A start archive has no release check. Wait
   until `kubectl -n agents-pilot get job agent-restore` shows `1/1`, then
   read `restore result=ok` in Loki. The script was tested locally (above).
4. **Start the agent.** Owner approval. A pull request removes the Job and
   sets `replicas: 1`. The start makes a new archive with the name of the
   release before.
5. **Check, and keep Renovate off that release.** Read only: as step 7
   above. The update comes back as a new Renovate pull request; hold it
   until the cause is known.
6. **Remove the old state.** As step 8 above.

## Stop the workflow agent fast

The Renovate reviewer has three parts that let it act: the App at GitHub, the
receiver that makes its runs, and the providers job that gives a run its
tokens. The route that holds is a pull request; the steps before it act at
once. None is tested yet.

1. **Suspend the App installation.** The owner, in the browser, in seconds:
   the settings of the installed App on the account (Installed GitHub Apps,
   Configure), Suspend. GitHub's docs ("Suspending a GitHub App
   installation"): a suspended App "cannot access resources owned by that
   installation account". A run cannot comment, and no new token works.
   - The receiver still makes a run for each call until step 3 lands, and
     each run can spend the plan for up to 40 minutes
     (`activeDeadlineSeconds: 2400`) before it fails at GitHub.
   - The providers job fails at `github-token` each 15 minutes. So
     [AgentPlatformCronJobFailed](#agentplatformcronjobfailed) fires and
     stays firing for the whole stop, and
     [WorkflowProvidersStale](#workflowprovidersstale) fires after 35
     minutes. Both are expected.
2. **End the runs in flight.** Owner approval, with no pull request, so it
   can come before step 3 merges. First read the result of each run job
   (`kubectl -n agents-workflows get jobs -l app.kubernetes.io/name=workflow-run`),
   then `kubectl -n agents-workflows delete job <job>`. The pod gets SIGTERM
   and deletes its sandbox in its grace period of 90 seconds; the Job and its
   kept answer go with it.
3. **Stop the receiver and the providers job.** Owner approval. One pull
   request:
   - `replicas: 0` in `workflows/run-receiver/app/deployment.yaml`: no new
     run. A caller gets no answer.
   - `suspend: true` in the CronJob of `workflows/run-receiver/app/providers.yaml`:
     no new token, and no more failed runs.
   - Leave the sweep on: it deletes each run sandbox that is left.

   The time: the checks of a pull request of this repository took about 15
   minutes on 2026-11-14, and Flux applies a merge within about a minute
   (the git source polls each minute, and a webhook receiver is there).
   `AgentPlatformWorkloadNotReady` does not fire for a workload scaled to
   zero by a merge. Repeat step 2 for any run that started before the merge.
4. **The way back.** First the owner resumes the App installation. Then
   revert the pull request: the first providers run after the revert then
   gets a token. Check two good runs in a row before the first review.

**Proposal, not approved: a hand step before the merge.** The owner can
decide to stop the receiver by hand while the pull request of step 3 waits
for its checks: `flux suspend kustomization agents-workflows-run-receiver`,
then `kubectl -n agents-workflows scale deploy/run-receiver --replicas=0`.
This is a live change outside git, which R3 of the charter does not allow
as a normal step, so it is not part of the steps above. Git catches up when
the pull request of step 3 merges and the owner runs
`flux resume kustomization agents-workflows-run-receiver`: Flux then applies
`replicas: 0` from git. A resume before that merge starts the receiver
again.

## Move a trust domain to Bedrock by hand

Only a person moves a trust domain from the Claude plan to Bedrock: the
config of the model gateway has no fallback, so nothing moves by itself. The
owner decides each move.

1. **Check the Bedrock path.** Read only: `LiteLLMBedrockCertificateExpiring`
   is not firing. Owner approval: the owner runs "Test a name" of
   [`litellm.md`](litellm.md#test-a-name) for a name already on Bedrock, if
   one is. That test reads the master key of the model gateway from a
   Secret, and it makes a call that Bedrock bills.
2. **Move the name.** Owner approval. One pull request changes the one line
   of the trust domain in `router_settings.model_group_alias` of
   `kubernetes/apps/ai/litellm/app/configmap.yaml`, for example
   `agents-pilot: claude-opus-bedrock` for the pilot alone. A move of the
   main agent (`agents-main`) also moves the finance agent
   (`agents-finance`), in the same pull request.
   [`litellm.md`](litellm.md#moving-a-name-and-back) has the rule.
3. **After the merge.** Reloader starts LiteLLM again, and calls to every
   model fail for the time of one start. A conversation goes on with its
   history; on Bedrock each call is billed by token, and there is no spend
   limit on the agent keys yet.
4. **The way back.** A revert of that merge, at the reset of the plan or
   when the owner decides.

**Tested on 2026-11-13:** the pilot's name moved to Bedrock by one line
(`9ee367b9`, 00:00 UTC) and back to the bridge by one line (`74e6356e`,
00:57 UTC), and the pilot ran its turns on Bedrock between the two.

## At a limit of the Claude plan

One plan is one budget for the owner's own sessions, the build sessions, and
each trust domain on the bridge. At its limit the calls of each of them fail
at the same time, until the limit resets. Nothing moves to Bedrock by itself.

1. **Confirm.** Read only. `ModelBridgeRateLimited` names the trust domain.
   In Loki, `{namespace="ai", app="model-bridge"} |= "engine_error"` shows
   `kind` `rate_limit`. From release 0.2.3 the caller gets HTTP 429 with the
   error type `rate_limit_error`, and the `call` lines show 429. Release
   0.2.3 sends a `retry-after` only when the engine gives the reset time. No
   call has gone through 0.2.3 at a real limit yet.
2. **What fails.** A workflow run fails, and its caller tries again with a
   new key at most 3 times; at a limit each attempt fails, so expect
   `AgentWorkflowRunFailed`. The model gateway sends each call to the bridge
   one time (`1e7d81c2`); the Anthropic SDK of the caller tries 2 more times
   and the reviewer's framework up to 6 attempts. So one model call reaches
   the bridge 18 times at most in one run, and up to 54 times for one review
   when the caller makes its 3 attempts. The pilot answers the chat with an
   error.
3. **Decide.** The owner: wait for the reset, use less of the plan (the
   build sessions too), or [move a trust domain to Bedrock](#move-a-trust-domain-to-bedrock-by-hand).
   Nothing here is a fix in git.
4. **After the reset.** A review that failed comes back with the next push
   of its pull request, or a new attempt of the caller. Read the next
   `call` lines with status 200.

**Seen so far.** 2026-11-14, from 02:29 to about 05:30 UTC: the 5-hour limit
of the plan, on the bridge before 0.2.3. That bridge logged an
`engine_error` of the kind `rate_limit`, then one of the kind `success`, and
a `call` line with status 200. The caller got an error of the type
`api_error` with the text "the engine ended the turn with an error
(success)", and the run of the review failed with a 502 in 32 seconds. From
release 0.2.3 (`04d7b4a3`) the bridge logs one `engine_error` of the kind
`rate_limit`, the caller gets a 429 `rate_limit_error`, and the `call` line
logs 429. The gateway's one attempt (`1e7d81c2`) keeps its retries from
multiplying.

## Drill: a run job stopped in the middle

A proposal for the readiness of the workflow agents: a run job that stops
in the middle leaves no run sandbox behind. It is not run yet. Each step
that changes the cluster needs the owner's yes at that time, and the drill
runs only when the owner has approved it.

**Why.** The run job deletes its sandbox at its end and on SIGTERM; the sweep
deletes a run sandbox older than one hour. Neither path has run on the
cluster for a stopped job.

**When.** Not near a run of the providers job (:07, :22, :37, :52) or of the
sweep (:00, :15, :30, :45), so each step is one cause. A real review needs a
Renovate pull request at a new head, and one model call; at a limit of the
plan, wait.

**Steps.**

1. Read only: `kubectl -n agents-workflows get sandboxes.agents.x-k8s.io`
   shows no run sandbox, and no alert of the platform fires.
2. Owner approval: start one run through the caller (a real review, or the
   call script of the receiver with the token, never on the screen).
3. Read only: wait until `get sandboxes.agents.x-k8s.io` shows the run
   sandbox `default--run-*` ready, and the run pod running.
4. **Case A, a stop with SIGTERM.** Owner approval:
   `kubectl -n agents-workflows delete pod <run pod>`. Pass: the run job logs
   exit 5, its sandbox is gone within 90 seconds, and the receiver keeps
   the result `failed` on the Job.
5. **Case B, a stop with no time to clean.** Owner approval, on a second
   run: `kubectl -n agents-workflows delete pod <run pod> --grace-period=0 --force`.
   The kubelet ends the container at once, as when a node goes away. Pass:
   the sandbox stays at first, the sweep deletes it once it is one hour old
   (within 75 minutes of its start), and `AgentRunSandboxTooOld` does not
   fire.
6. Read only, after each case: no run sandbox in
   `get sandboxes.agents.x-k8s.io`, no sandbox pod, and the sweep's log line
   for case B.
7. Record the times, the commands and each pass or fail in the readiness
   checklist of the platform.

**Rollback.** Nothing to roll back: the drill makes runs and deletes them.
If a sandbox stays past 80 minutes, the alert fires and its section above
applies.
