# Agent platform runbook

What to do when an alert of the agent platform fires, when an agent does
not answer on its channel, a sandbox does not start, web search does not
work or memory search finds nothing, how to restore the
state of an agent, how the finance agent moves from the host, a draft of the move of the main agent, how to stop the workflow agent fast, how to move a trust
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

24 alerts cover the platform: the 17 rules of
`kubernetes/apps/ai/agents/monitoring/app/` (15 in `prometheusrule.yaml`, 2
in `heartbeat-prometheusrule.yaml`), the 2 rules of the model gateway in
`kubernetes/apps/ai/litellm/app/prometheusrule.yaml`, and the 5 rules of the
backup operator (the kopiur chart) that match the snapshot policy, the
snapshots and the restores of a state volume. The other rules of the backup
operator are about its repository and its controller, and `backups.md`
covers them. Each of the 19 rules in `kubernetes/apps/ai/` opens the AI
board in Grafana (`tnwks-ai`), for the site whose Prometheus raised it
(`observability/tests/test_alert_links.py`). No alert of the platform has
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
(`platform-check-isolation`, `platform-check-probe`, and in `agents-finance`
`platform-check-firefly-probe`, `platform-check-firefly-dns` and
`platform-check-mcp-gateway-probe`). It resolves at the next
good run. The chart rule `KubeJobFailed` fires for the same Job and keeps
firing while the failed Job is kept, so Alertmanager drops it for these Jobs
and for the run jobs; it stays in the Alertmanager UI. Read this alert for
the state of now.

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
- **The gateway probe** (`platform-check-mcp-gateway-probe`,
  [mcp-gateway.md](mcp-gateway.md#the-probe)). Its lines in Loki
  (`{namespace="agents-finance", app="platform-checks"}`) name each check.
  A 401 for the probe key or the key with no tool: the value of the key in
  `secret-mcp-gateway-probe.sops.yaml` and its hash in the gateway's config
  differ. Until the owner's values step, the checks with a key print
  `skipped: the key is pending` and pass; that is not a fault, and the
  values step ends it. The checks with no key and with an unknown key, and
  the straight connection to the MCP server, always run. A
  `connected` for the straight connection: the policy of the MCP server
  lets in more than the gateway; fix it before the next run.
  `agents-main` has the same probe for its vault route only, with the key
  `main-probe` (no Secret yet, so its key checks print `skipped`). In each
  namespace, `other vault server` with `connected`, or `other vault: probe
  key` with anything but a 401, is a path across the two trust domains
  (R6): the policy of that vault server or the keys of that route are
  wrong; fix it before the next run.

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

**A record with no pod.** This alert reads pods, so it does not see a
Sandbox record that the gateway left with no pod (OpenShell 0.1.8 can answer
a delete with success and forget the sandbox). The run receiver checks the
record of each run after its end. A record that stays is one line in its
log, and the Job annotation `workflow-agents/sandbox-record` says
`left: <sandbox>`:

```text
{namespace="agents-workflows", app="run-receiver"} |= "Sandbox record"
```

The sweep removes the record after one hour and fails once
(AgentPlatformCronJobFailed), with an `ERROR sweep:` line that names the
sandbox.

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
the last 2 hours after an exit code from 1 to 127: the daily archive of the
product failed (`daily-backup.sh` ends with exit 1). A stop of the node does
not count: the kubelet stops the container with SIGTERM (exit 143, reason
`Error`), or the node goes down with it (exit 255, reason `Unknown`). The
rule reads the exit code
(`kube_pod_container_status_last_terminated_exitcode`), because the reason
`Error` is the same for exit 1 and exit 143.

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
2026-11-12. The first backup of the main agent (2026-11-19) failed with
`Cannot capture required config file /home/node/config/telegram-groups.json:
file identity could not be pinned`: the command refuses an include file
that is a link, and each file of a projected volume is one. The backup
container of main now runs on a copy of the config with regular files. A
new include in the config of an agent needs its file in that copy too
(`observability/tests/test_main_agent_backup.py` checks it). On 2026-11-21
the rule of that time (reason `Error`) fired for the three agent pods after a
planned stop of the node: the container ended with exit 143 at the stop, and
no archive failed. The rule now reads the exit code.

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

### AgentAutomationsSyncFailed

**What it means.** The newest `automations-sync result=` line of an agent
pod says `failed`: the `automations-sync` container could not make the
scheduled jobs of the product equal to `automations.json` of the config
([agent-platform.md](agent-platform.md), "Scheduled jobs"). A pass runs at
the start of the pod and then each hour, and the ok line of a later pass
clears the alert. The jobs stay as they were: a list that does not parse
changes nothing. With no line for 3 hours (no pod), it stops firing.

**First checks.**

```sh
kubectl -n agents-finance logs deploy/agent -c automations-sync --tail=20
kubectl -n agents-finance get pods -l app.kubernetes.io/name=agent
```

The `reason=` of the last pass:

- `bad-list`: `automations.json` does not parse, has no `jobs` list, or an
  entry has a bad or missing field (`key`, `cron`, `message`, `enabled`)
  or a key twice. Nothing changed.
- `job`: an add, a turn on or off, or a remove failed; the line of each
  such job names its key.
- `list`: the gateway did not answer the list of jobs.
- `connect`: the gateway did not answer for 10 minutes after the start.
- `no-zone` or `no-chat`: the container has no `TZ` or no
  `TELEGRAM_ALLOW_FROM`. Nothing changed; the line comes again each hour,
  so the alert stays on until the fix.

The script prints nothing of the output of the product's CLI (it can hold
the chat id and the message).

**Seen so far.** Not fired yet.

**Fix.** `bad-list`, `no-zone`, `no-chat`: a pull request fixes the list or
the env, and the change starts the pod again. `job`, `list`, `connect`:
check the agent container (`kubectl -n agents-finance logs deploy/agent -c
agent --tail=50` shows the gateway, not chat content in its first lines);
the next pass runs within the hour. A failed pass leaves each job as it
was, so to stop the jobs while the fault lasts, stop them in a chat
([Turn off the scheduled jobs](#turn-off-the-scheduled-jobs)).

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

**What it means.** A backup repository of the agent trust domains
(ClusterRepository `agents`, or `agents-finance`: the label `name`) has been
`Pending` or `Initializing` for an hour. It never got `Ready`, so no snapshot of a trust domain runs, and the
policies have no failure to show. `Failed` or `Degraded` is the chart rule
`KopiurRepositoryNotReady` instead.

**First checks.**

```sh
kubectl get clusterrepository agents -o yaml          # the Ready condition
kubectl -n kopiur-system get cronjob aws-backup-credentials-agents
kubectl -n kopiur-system get secret aws-backup-sts-agents -o jsonpath='{.metadata.annotations.backups\.tnwks\.us/expires}'
kubectl -n kopiur-system get jobs | grep agents
# for agents-finance: the same, with the names of that repository
kubectl get clusterrepository agents-finance -o yaml
kubectl -n kopiur-system get cronjob aws-backup-credentials-agents-finance
kubectl -n kopiur-system get secret aws-backup-sts-agents-finance -o jsonpath='{.metadata.annotations.backups\.tnwks\.us/expires}'
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

### ModelPlanNearLimit

**What it means.** The newest `plan_limit` line of the model bridge says
that a window of the Claude plan is near its limit (`allowed_warning`: the
warning of the vendor) or at it (`rejected`), and the window did not reset
since. The label `window` names it: `five_hour`, `seven_day`, or a week
window of one model family (`seven_day_opus`, `seven_day_sonnet`). The calls
still work while it is near; at the limit the calls of each trust domain stop
until the reset, and `ModelBridgeRateLimited` fires too. It warns in the
chat and does not page.

**First checks.** Read only.

1. The AI board, tab "Limits and security": the use of the window, the time
   to its reset, and the tokens of each trust domain (which domain uses the
   plan).
2. In Loki, the lines of the last hours:
   `{namespace="ai", app="model-bridge"} |= "plan_limit"` for the readings,
   and `|= "\"event\":\"call\""` for the calls of each trust domain.
3. The scheduled work of the agents in those hours (the Checks tab of the
   board, and the runs of `agents-workflows`).

**Seen so far.** Never. The series start with bridge 0.3.0.

**Fix.** Nothing breaks while the window is only near its limit. When the
reset is near, wait. When it is far, the owner can slow the work that uses
the plan (a scheduled job or a workflow run that loops) until the reset.
When the limit stops calls, see
[At a limit of the Claude plan](#at-a-limit-of-the-claude-plan). An alert
that fires often on the week window is a sign that the plan is too small
for the work: that is a decision of the owner.

### AgentHeartbeatMissing

**What it means.** The daily `agent-heartbeat-check` (06:33 UTC) found no
completed heartbeat turn of an agent in Tempo in the last 26 hours, or could
not ask Tempo. It asks 3 times, 60 seconds apart, and a failed pod gets one
more try. It resolves at the next good run.

**First checks.** In Loki,
`{namespace="monitoring", app="agent-heartbeat-check"}`:
`result=missing service=<service>` is an agent that did not complete its
heartbeat, `result=error` is a check that could not ask Tempo. The check
has one line for each service, and the alert fires when one of them is not
`result=ok`. The namespace is the end of the service name
(`openclaw-agents-pilot` is `agents-pilot`, `openclaw-agents-finance` is
`agents-finance`, `openclaw-agents-main` is `agents-main`). Then, with that
namespace:

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

### ModelGatewayCallsFailing

**What it means.** More than half of the model calls of the key of a trust
domain failed in the last 15 minutes, at least 3 of them, and this went on
for 20 minutes. It reads the series of the model gateway
([`litellm.md`](litellm.md#metrics)): the calls and the failed calls of the
key `agents-<name>`. The hold is longer than the window, so one turn with
its retries (about 3 minutes) does not fire it. The failures must go on
for about 5 minutes or more. The key of the metrics scrape and the key of
the transcriber are not a trust domain, and do not fire it.

**First checks.** The Model calls tab of the AI board, "Failed model calls
by status", for the key of the alert:

- 429: the limit of the Claude plan, if the name goes to the plan. The model
  bridge logs it too, and `ModelBridgeRateLimited` fires.
- 401 or 403: a key or a model list. Is the key of the agent the one in
  `keys.json`, and does its entry list the name? The `ensure-keys` sidecar
  logs each key it made or changed (`kubectl -n ai logs deploy/litellm -c
  ensure-keys --tail=20`).
- 5xx: the model bridge, Bedrock or the vendor. The log of the gateway is in
  Loki, `{namespace="ai", app="litellm"}`, and the bridge has its own
  (`{namespace="ai", app="model-bridge"}`).

**Seen so far.** Not fired. The promtool cases in
`observability/rule-tests/litellm.yaml` prove that one turn with its retries
does not fire it, that failures for 20 minutes fire it, and that a quiet
trust domain, a domain whose failures are a quarter and the scrape key do
not.

**Fix.** By the status. [At a limit of the Claude
plan](#at-a-limit-of-the-claude-plan) for a 429. A wrong key or model list:
fix the entry of `keys.json` by a pull request. The bridge or Bedrock down:
their own sections, or [move the trust domain](#move-a-trust-domain-to-bedrock-by-hand)
when the plan is the fault and the owner says so.

### LiteLLMBedrockCertificateExpiring

**What it means.** The client certificate of IAM Roles Anywhere, which
gives the model gateway (`litellm`) its Bedrock credentials, expires on
2027-03-28. The rule fires from 2027-02-26, 30 days before. It does not read
the certificate: its expression is a fixed date
(`vector(time()) > 1803600000`). Without the certificate, each name of the
model gateway that goes to Bedrock fails: the summaries of the transcriber,
and each trust domain that was moved to Bedrock
([by hand](#move-a-trust-domain-to-bedrock-by-hand)).

**First checks.** The date is in [`litellm.md`](litellm.md#bedrock-credentials).
Do not decrypt the Secret to read it. The sidecar that exchanges the
certificate for credentials logs each refresh:

```sh
kubectl -n ai logs deploy/litellm -c aws-credentials --tail=20
```

**Seen so far.** Never. The promtool cases in
`observability/rule-tests/litellm.yaml` prove that it is quiet before the
date and fires from it.

**Fix.** The owner renews the certificate, as
[`litellm.md`](litellm.md#bedrock-credentials) says: new PEM files in
`app/secret-roles-anywhere.sops.yaml`. In the same change, move the date
in `app/prometheusrule.yaml` to 30 days before the new end, and the dates of
the cases in `observability/rule-tests/litellm.yaml` with it.

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
   The Messages tab of the AI board shows where the message stopped, by
   trust domain: no row in "Messages by channel" is the receive path of
   the channel; a row in "Turns that ended in an error" is the turn; a row
   in "Replies that failed" is the send path, with the class of the error.
   The series come once a minute, so wait one minute after the message.
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
   cm=$(kubectl -n agents-pilot get deploy agent \
     -o jsonpath='{.spec.template.spec.volumes[?(@.name=="config")].configMap.name}')
   kubectl -n agents-pilot get configmap "$cm" -o jsonpath='{.data.openclaw\.json}' | jq '.tools'
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

The pod of an agent (pilot or finance) does not start: its
`official-plugins` init container failed, and
`AgentPlatformWorkloadNotReady` fires after 10 minutes. The
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
     above it are the product's installer. The step runs with
     `OPENCLAW_DEBUG=1`, so the CLI of the product prints the cause:
     `[openclaw] Reason: <text>` and a stack, after
     `[openclaw] The CLI command failed.` (a pod from before 2026-11-17
     printed that line only). The step has no secret, so the reason holds
     none. In Loki, for finance:
     `{namespace="agents-finance"} |= "official-plugins" |~ "Reason|result="`.
     An image of a bad build fails the same step in CI, so look at the
     release first.

   **Seen so far.** 2026-11-17, finance, 09:41 to 09:43 UTC: one merge gave
   two rollouts (see [agent-platform.md](agent-platform.md), "A start of
   the agent pod"). The first new pod was stopped during its
   `official-plugins` step, before the installer printed a line. The step
   of the next pod failed four times, each in about 5 seconds, with no
   reason in the log, and passed on its fifth run at 09:43:39. The cause of
   the four failures is not known. The product serializes plugin installs
   with a lease in the state database (`plugins.lifecycle.lease`), but its
   CLI waits for a held lease (up to 10 minutes in release 2026.11.3), so a
   lease alone does not explain a failure in 5 seconds. Since that day a
   merge gives one rollout, and the next failure prints its reason.
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

## Turn off the scheduled jobs

The scheduled jobs of an agent (finance today) come from
`agent/config/automations.json` through the `automations-sync` container
([agent-platform.md](agent-platform.md), "Scheduled jobs"). They live in the
state database of the product. A revert of the sidecar, or of the list, in
one step leaves each job on: with no list the sync prints
`automations-sync result=none` and changes nothing, and with no sidecar
nothing changes the jobs. So turn them off first, then take parts out.

1. **One job, now, with no merge.** The owner stops it in a chat. It stays
   off at each pass (`automations-sync key=<key> result=ok state=stopped`)
   until git changes its `enabled` from `false` to `true`.
2. **Each job, through git.** A pull request sets `"enabled": false` on
   each entry of `automations.json` (the jobs stay, off), or sets the file
   to `{"jobs": []}` (the sync removes each `flux:` job). Both keep the file
   valid JSON: a list that does not parse changes nothing and ends
   `automations-sync result=failed reason=bad-list`.
3. **After the merge.** The ConfigMap gets a new name, so the pod starts
   again (about 2 minutes), and the pass at the start applies the list.
   **Read only:**
   ```sh
   kubectl -n agents-finance logs deploy/agent -c automations-sync --tail=20
   ```
   - With `false`: `automations-sync key=<key> result=ok state=off` for
     each job, then `automations-sync result=ok jobs=<n>`.
   - With `{"jobs": []}`: `automations-sync key=<key> result=removed` for
     each job, then `automations-sync result=ok jobs=0`.
   - `automations-sync key=<key> result=failed`, or a pass line with
     `result=failed`: the job is not off yet. See
     [AgentAutomationsSyncFailed](#agentautomationssyncfailed); the next
     pass runs within the hour.
4. **Only then,** if the goal is to take the sidecar or the list out, a
   second pull request does it. A job that is off stays off in the state,
   and a removed job is gone.

To turn a job on again: `"enabled": true` in git, in a pull request. When
the job was `false` in git, the next pass turns it on
(`state=on`). With `replicas: 0` (the way back of the cutover) no job runs,
because no gateway runs, but the jobs stay in the state as they are and run
again at the next start of the pod: turn them off first if they must not.

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

   `kubectl -n agents-pilot get snapshots.kopiur.home-operations.com` can show
   the snapshots from before the move as `Discovered` objects (they came while
   `s3` allowed `agents-pilot` again for the delete of the old policy,
   [`agent-platform.md`](agent-platform.md#move-a-trust-domain-to-another-repository)).
   Their `status.snapshot` holds the identity and the snapshot ID to use below.

   The shared repository `s3` does not allow `agents-pilot` after the move, so the
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

## The cutover of the finance agent

The finance agent moves from the host to `agents-finance`. Five merges and
four steps of the owner on the host, each with a check and a stop point. The
files are in `kubernetes/apps/ai/agents/finance/agent/app/`, and each merge
adds or removes lines in its `kustomization.yaml` (its comment has the same
order). The rules of this page hold: each merge is a pull request with the
go of the owner, and nothing is changed on the cluster by hand.

Three facts set the order:

- **One bot polls from one place.** Telegram gives the updates of a bot to
  one poller. Two gateways with the same token fight (`409 Conflict`), and
  each loses messages. So the host stops the finance bot before the pod
  starts. A message sent between the two waits at Telegram, up to 24 hours,
  and the pod gets it when it starts.
- **The volume binds on the node of its first pod** (local-path waits for
  the first consumer). The agent runs on `homelab-wsl-worker-2`. A backup
  mover that came first would make the volume on any worker. So the inbox
  (`state-inbox.yaml`, with the node of the agent) comes before the policy.
- **The copy holds the finance agent only, with no credential** (R6). A
  backup archive of the product holds each agent of the host and its
  credentials, so the owner makes the copy by hand, from the files of the
  finance agent.

### The order of the merges

| Step | The change | What the merge applies | The check | The way back |
| --- | --- | --- | --- | --- |
| 1 | List `pvc.yaml` and `state-inbox.yaml` | The volume `agent-state` and the inbox pod, on the node of the agent. No product code, no secret, no network | `kubectl -n agents-finance get pvc agent-state` is `Bound`; `kubectl get pv <its volume> -o jsonpath='{.spec.nodeAffinity}'` names `homelab-wsl-worker-2`; `kubectl -n agents-finance logs deploy/agent-state-inbox` is `state-inbox result=ready` | Take the inbox out. The volume stays: it has `kustomize.toolkit.fluxcd.io/prune: disabled` |
| 2 | List `backup.yaml` | The snapshot policy and schedule to the repository `agents-finance`. The first snapshot runs at once, of the empty volume | `kubectl -n agents-finance get snapshots.kopiur.home-operations.com`: one `Succeeded`. No `KopiurSnapshotFailed` | None needed: the policy stays with the volume |
| 3 | The owner puts the values in `secret.sops.yaml`, in the key list of the model gateway and the hash of the finance key in the config of the MCP gateway ([Before the cutover](#before-the-cutover)) | The key `agents-finance` at the model gateway, and the hash of the client `finance` at the MCP gateway. The Secret file is not listed yet, so nothing in `agents-finance` | `kubectl -n ai logs deploy/litellm -c ensure-keys` has `agents-finance: created` (or `updated`) `(models: agents-finance, agents-finance-embeddings)`; `kubectl -n ai get deploy mcp-gateway` is ready after its roll | Revert. The key stays at the model gateway until it is removed in the admin UI |
| Host | The owner stops the finance bot on the host, makes the copy and the seed of the vault, and puts both archives in the volume ([The copy of the state](#the-copy-of-the-state)) | Nothing in git | The checksum and the counts of the copy | Turn the bot on again on the host |
| 4 | Out `state-inbox.yaml`; in `restore-job.yaml` (with `ARCHIVE` and `VAULT` set), `secret.sops.yaml`, `secret-search.sops.yaml` and `deployment.yaml` (still `replicas: 0`) | The Job checks each member of the two archives, then puts the copy in place as `state/` and the vault in `vault/`. The two Secrets and the Deployment at zero: no pod | `kubectl -n agents-finance get job agent-restore` is `1/1`; in Loki, `{namespace="agents-finance", app="agent-restore"}` has `restore result=ok archive=start/<name> files=<n> vault-notes=<m>`, with the two counts of the owner. A refused archive is `restore result=failed reason=<kind>` (for example `member-type archive=state kinds=link` or `too-large archive=vault`), and a failed unpack is `reason=unpack` or `reason=vault-unpack`: fix the copy on the host and run again as `agent-restore-2`; the volume is as it was | Revert step 4, then turn the bot on again on the host. The copy stays in `archives/start/` |
| 5 | Out `restore-job.yaml`; `replicas: 1` in `deployment.yaml` | The agent pod starts and polls the bot | [The cutover](#the-cutover) | [The way back](#the-way-back) |

After step 5: the restore test of the state (the template
`finance/agent/restore-test/`, with the first nightly snapshot that holds
the state), and `openclaw-agents-finance` in `SERVICES` of
`agents/monitoring/app/heartbeat-check.yaml` after its first heartbeat in
Tempo, so the check never fails on a heartbeat that has not come yet.

Between the steps no alert fires: the Deployment at zero has no pod that is
not ready (with replicas 1 and a value that is still `pending`, the
`values-check` step stops the start with `values-check result=failed
key=<name> reason=placeholder`, and AgentPlatformWorkloadNotReady fires
after 10 minutes), the inbox is ready when it runs, the restore Job ends with exit 0
(a failed Job is `KubeJobFailed`, and that is a real fault), and the alerts
of the backup container and of the heartbeat need a pod or a service in
`SERVICES` first.

### Before the cutover

The values are the owner's (R4), and none of them goes through an agent or a
session. Each goes in with `sops <file>` in place of `pending` (the file has
five placeholders now), in a pull request. The Secret is not applied until
step 4, so a merge of the values starts nothing.

| Key of `agent-secret` | The value |
| --- | --- |
| `OPENCLAW_GATEWAY_TOKEN` | A new random value: `openssl rand -hex 32` |
| `LITELLM_API_KEY` | A new key: `sk-agents-finance-` and `openssl rand -hex 24`. The same value goes in a new entry of `keys.json` in `kubernetes/apps/ai/litellm/app/secret-keys.sops.yaml`: alias `agents-finance`, models `["agents-finance", "agents-finance-embeddings"]` ([litellm.md](litellm.md#keys-of-the-agent-platform)). The key list has no such entry today |
| `TELEGRAM_BOT_TOKEN` | The token of the finance bot, the one the host uses now |
| `TELEGRAM_ALLOW_FROM` | The numeric Telegram user id of the owner, as on the host |
| `MCP_GATEWAY_KEY` | A new random value: `openssl rand -hex 32`. Its hash goes in the config of the MCP gateway, for the client `finance`, in the same pull request ([mcp-gateway.md](mcp-gateway.md#the-steps-of-the-owner)); the `values-check` step of the pod stops the start with `reason=hash` when the two differ |

Then the token of the finance app for its proxy, if it is not in yet:
[agent-platform.md](agent-platform.md#the-steps-of-the-owner-for-the-finance-app),
step 1. The check of each value prints only `ok` or `not ok` (`not ok` for
the placeholder):

```bash
F=kubernetes/apps/ai/agents/finance/agent/app/secret.sops.yaml
for k in OPENCLAW_GATEWAY_TOKEN LITELLM_API_KEY TELEGRAM_BOT_TOKEN TELEGRAM_ALLOW_FROM MCP_GATEWAY_KEY; do
  printf '%s ' "$k"
  bin/sops -d --extract "[\"stringData\"][\"$k\"]" "$F" \
    | awk '{ v = v $0 } END { print (length(v) > 0 && v != "pending") ? "ok" : "not ok" }'
done
```

Before step 5 the owner also decides the open points that the plan lists
for this step: the model path of finance records (the raw terms page and
the settings of the plan, D65), and the risk of web search, write access to
the finance app and finance data in one session
([agent-platform.md](agent-platform.md#the-finance-app)).

### The inventory of a move

Before the copy, list what the agent has on the host, and for each item
where it goes on the platform:

- the workspace: the instruction files, the memory, the skills and the
  scripts (the copy, below);
- the vault or another folder the agent reads (the seed, below);
- the channel account and its token, and each other credential (new values
  in the Secrets, never a copy);
- the MCP servers and the tools of the config (the config in git);
- **the scheduled jobs of the product**: they live in the state database of
  the host gateway, not in a file of the workspace, so the copy does not
  hold them. List them with `openclaw cron list --all --json` (the
  schedule, the zone, the session, the delivery and the message of each),
  turn them off on the host at step 1 of [The cutover](#the-cutover), and
  declare them in git
  ([agent-platform.md](agent-platform.md), "Scheduled jobs"). The finance
  move missed them at first.

### The copy of the state

The owner, on the host, after the finance bot is off (step 1 of
[The cutover](#the-cutover)), so nothing writes the files during the copy.
The copy holds the **workspace of the finance agent only** (the owner's
decision of 2026-11-17): its instruction
files (`AGENTS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md`, `TOOLS.md`), its
memory (`MEMORY.md`, `memory/`), its skills and its scripts. The pod makes
the rest at its first start.

Left out, each on purpose:

- The other agents of the host: their directories under `agents/`, their
  workspaces, their sessions and memory.
- Each credential: `credentials/`, `.env`, `secrets.json`, the shared
  database `state/openclaw.sqlite` (it holds the shared auth profiles), and
  the token file of the finance app that the scripts read today.
- The database of the finance agent, `agents/<id>/agent/openclaw-agent.sqlite`.
  It holds the chat history, and also the auth profiles of that agent, so a
  credential of the host. The chat history stays on the host. What the agent
  must keep is in its memory files.
- `openclaw.json`: the config of the pod is in git.
- The memory index: the `memory-index` step of the pod builds it again,
  with the embeddings of finance.
- `.git` of the workspace, and logs.
- What the real run of 2026-11-17 left out too: backup files (`*.bak`,
  `*.orig`, `*~`), caches (`__pycache__/`, `.cache/`, `node_modules/`), and
  zone marker files (`*:Zone.Identifier`, which Windows writes next to a
  downloaded file).

The copy holds no database of the product, so it needs no release match:
the host stays on its release for the copy.

The steps, in one shell on the host. `<id>` is the id of the finance agent
on the host, `V` the folder of the finance vault:

```bash
# 1. The workspace of the finance agent.
openclaw agents list --json      # the "workspace" of the agent <id>
WS=<that path>
STAGE=$(mktemp -d)
mkdir -m 0700 "$STAGE/state" "$STAGE/state/workspace"
tar -C "$WS" --exclude=./.git --exclude='*.bak' --exclude='*.orig' --exclude='*~' \
  --exclude=__pycache__ --exclude=.cache --exclude=node_modules --exclude='*:Zone.Identifier' \
  -cf - . | tar -C "$STAGE/state/workspace" -xf -
```

2. **Change the files in the copy, not on the host.** The first check of
   the instruction files on the pod lists the files as they come in
   (`instruction-check result=first`), so a change made here never fires
   `AgentInstructionFileChanged`.
   - **The scripts.** Each script that calls the finance app: the base URL
     becomes `http://firefly-proxy.agents-finance.svc.cluster.local:8081`,
     the read port of the proxy (the paths stay `/api/v1/...`). Take out
     the read of the token file and the `Authorization` header: the proxy
     sets the token. A script only reads; the read port refuses each write.
   - **The instruction files** (`AGENTS.md`, `TOOLS.md`, a skill). Each line
     that names the address of the app or the token file changes to:
     - read data in a script through the read port above, with `python3`;
     - keep each script in a file of the workspace and run it as one
       command alone, `python3 <file>.py`: no `cd <dir> &&` before it, no
       `;` after it, no `sh -c`, and no pipe into `python3`. The exec tool
       refuses those forms ("complex interpreter invocation detected"; see
       "How a script runs" in [agent-platform.md](agent-platform.md));
     - make each create or update with the `firefly__` tools, never with a
       script;
     - never delete (the MCP gateway, the tools and the proxy refuse it);
     - read and test rules (`firefly__get_rule*`, `firefly__test_rule*`),
       and never write one (the owner's decision: rule writes stay
       refused): propose a new or changed rule in the chat, and the owner
       adds it in the app (a rule can delete a transaction);
     - update a split transaction with `firefly__update_transaction` and the
       `transaction_journal_id` of each split (an update that leaves out a
       split deletes it).
   - Then **no file that steers the agent** names the old address or the
     token file: the scripts, the instruction files and the skills, and
     the operative lines of the memory file (a rule or a step it follows).
     A line of history in the memory file or a daily memory file (what was
     done, on a date) can keep the old name: add a note that it is
     history, for example "(before the move to the platform)". The rule
     was "no file names them", and the real run showed that history lines
     name them too. Each command lists the files that still name them;
     read each one:

     ```bash
     grep -rIl '<the old address of the app>' "$STAGE/state"
     grep -rIl '<the name of the token file>' "$STAGE/state"
     ```

```bash
# 3. No secret, no link out of the copy.
betterleaks dir --redact --no-banner "$STAGE/state"   # no finding (see below)
find "$STAGE/state" -type l                           # prints nothing
find "$STAGE/state" \( -name '.env' -o -name '*.sqlite*' -o -iname '*token*' -o -iname '*credential*' \)   # prints nothing

# The scan reports each finding, redacted, and exits 1. Read each one: a
# real secret goes out of the copy or is changed; a value that only looks
# like one (an example, a hash) can stay. Run it again until the findings
# left are the ones you read.

# 4. The archive, in the form of a start archive (the restore Job reads it).
release=$(openclaw --version | grep -oE '[0-9]{4}\.[0-9]+\.[0-9]+' | head -n 1)
name=openclaw-state-$release-$(date -u +%Y%m%dT%H%M%SZ).tar.gz
tar -czf "$STAGE/$name" -C "$STAGE/state" .
(cd "$STAGE" && sha256sum "$name" > "$name.sha256")
find "$STAGE/state/workspace" -type f | wc -l       # the count of the copy: write it down

# 5. Into the volume, through the inbox (step 1 of the merges).
tar -C "$STAGE" -cf - "$name" "$name.sha256" \
  | kubectl -n agents-finance exec -i deploy/agent-state-inbox -- tar -C /home/node/data/archives/start -xf -
kubectl -n agents-finance exec deploy/agent-state-inbox -- \
  sh -c "cd /home/node/data/archives/start && sha256sum -c $name.sha256"   # <name>: OK

# 6. The seed of the vault. The notes can hold a secret (a password or a
#    key written in a note): read each finding, and take the note out or
#    change it in the vault folder first. The agent reads each note.
betterleaks dir --redact --no-banner "$V"
vault=vault-$(date -u +%Y%m%dT%H%M%SZ).tar
tar -C "$V" --exclude=.obsidian --exclude=.trash --exclude=.git -cf "$STAGE/$vault" .
tar -tf "$STAGE/$vault" | grep -c '\.md$'       # the count of notes: write it down
tar -C "$STAGE" -cf - "$vault" \
  | kubectl -n agents-finance exec -i deploy/agent-state-inbox -- tar -C /home/node/data/archives/seed -xf -
```

The restore Job of step 4 checks each member of both archives before it
writes a file: only plain files and directories, no link, no device, no
absolute path, no `..`, no setuid or setgid bit, at most 20000 members and
2 GiB in all for each archive, and in the copy of the state, nothing outside
`./workspace/`. So a link or a stray file that the checks on the host
missed stops the Job, with the kind of the member in its line and never its
name. Then it unpacks both archives into `restore-<time>/` and puts them in
place only when both are out; a failed unpack removes that directory, so
the volume is as it was.

7. Give the names of the two archives (not their content) to the pull
   request of step 4, as the values of `ARCHIVE` (`start/<name>`) and
   `VAULT` (`<vault>`) in `restore-job.yaml`.
8. After the exit check, remove `$STAGE` on the host. It is a copy of the
   workspace, and the host keeps the original.

### The cutover

Each step has a stop point: at a failed check, stop and take
[The way back](#the-way-back) from that step.

1. **The host stops the finance agent.** The owner, on the host: turn off
   the Telegram account of the finance bot in the config of the host
   gateway (`channels.telegram.accounts.<account>.enabled` to `false`), and
   turn off the scheduled jobs of the finance agent (`openclaw cron list
   --all`, then `openclaw cron disable <id>` for each). At the release of
   the host the gateway applies the account change live, with no restart
   (the real run of 2026-11-17). Check: `openclaw channels status --probe`
   shows the finance account not running, and a message to the finance bot
   gets no answer. Start the host gateway again only if the channel state
   still shows the account as running: the other agents of the host stop
   for that restart. From here the finance agent is off until step 4.
2. **The copy** ([The copy of the state](#the-copy-of-the-state)). Check:
   the checksum is `OK`, and the two counts are written down.
3. **Merge step 4.** Checks: as in the table. Stop point: the restore Job
   fails. It refuses before it writes, so the volume is as it was.
4. **Merge step 5.** Checks, read only:
   - `kubectl -n agents-finance get pods -l app.kubernetes.io/name=agent`:
     `3/3` and `Running`, no restart.
   - In Loki, `{namespace="agents-finance", app="agent"}`:
     `archive-state result=ok`, `official-plugins id=brave ... result=installed`,
     `memory-index result=ok`, and `sandbox-start` with no `result=failed`.
   - No `409 Conflict` of Telegram: the host does not poll any more.
5. **The first message.** The owner writes to the finance bot: a question
   that the memory answers (for example: what do you know about how we
   work). The answer comes from the pod, and it knows the memory of the
   host.

### The exit check

The exit check of phase 5, as the plan asks: the restore test passes, the
check between domains passes, and one chat turn, one tool call and a read
of the vault work. The lead reads; the owner makes the turns.

**Steps.**

1. **One tool call.** The owner asks for the summary of this month. The
   session calls `firefly__get_summary`. The MCP gateway logs the call
   with its client: `kubectl -n ai logs deploy/mcp-gateway | grep '"client":"finance"'`
   shows `"gen_ai.tool.name":"get_summary"` and `"http.status":200`. The
   proxy logs it too:
   `kubectl -n agents-finance logs deploy/firefly-proxy | grep 'firefly-proxy port=8080'`
   shows `refused=0` and `status=200`.
2. **A read of the vault.** The owner asks for a fact that only a note of
   the vault holds. The answer names it.
3. **The scheduled checks** of `agents-finance`, on their next runs:
   `platform-check-isolation` (04:17), `platform-check-probe` (04:47),
   `platform-check-firefly-probe` (05:17), `platform-check-firefly-dns`
   (05:27) and `platform-check-mcp-gateway-probe` (05:37, with no
   `skipped` line) pass: `kubectl -n agents-finance get jobs`.
4. **The backups.** The first nightly snapshot with the state is
   `Succeeded`, and the daily archive of the backup container is in the log
   (`daily-backup result=ok`) with `instruction-check result=first`.
5. **The restore test**, with that snapshot (the template
   `finance/agent/restore-test/`, owner's go): the check Job, at the path of
   the agent pod, passes (`restore-check result=ok`). Three counts, each
   in its own way:
   - `vault-notes` **equals** the `vault-notes` of the restore Job of step
     4 (no tool of the agent writes the vault). The reader compares them:
     the count is not in git.
   - `workspace-files` and `copy-files`: the files of the workspace now,
     and the plain files of the copy of the cutover. The agent adds files,
     so the first is most often higher. `copy-files-missing` counts the
     files of the copy that are not in the workspace any more (no name):
     the agent may delete a file it no longer needs, so it is a count to
     read, not a failure. A high count is a finding.
   - `bytes` is **close to** the size that the snapshot records:
     `kubectl -n agents-finance get snapshots.kopiur.home-operations.com <name> -o jsonpath='{.status.stats.sizeBytes}'`.
     The snapshot records the total logical size, and `bytes` is the sum
     of the plain files, so the two can differ. A difference is a finding
     to read, not a failed check.

   A failed check Job fires `KubeJobFailed` (`agents-finance`,
   `agent-restore-check`); a failed or stuck Restore fires
   `KopiurRestoreFailed`. The cleanup is two pull requests. The first
   takes `./agent/restore-test/ks.yaml` out of the list again, with no
   other edit; Flux then removes the Restore, the check Job and the
   scratch volume. Check: `kubectl -n agents-finance get restore,job,pvc`
   shows no `agent-state-restore-test` and no `agent-restore-check`. The
   second sets the name of the snapshot back to its placeholder. Not both
   in one: Flux applies the placeholder to the Restore of the child
   Kustomization while the parent prunes it, and the deletion then waits
   for the health check of the child (60 minutes), with the copy on the
   scratch volume until then.
6. **The heartbeat.** The first heartbeat of `openclaw-agents-finance` is
   in Tempo; then the pull request that adds it to `SERVICES`.

**Pass.** Each step above passed, and no alert of `agents-finance` fired
from step 1 of the merges to here, but the ones named in a step.

**Record in the evidence:** the pull request and the head of each step, the
names of the archive, the Job, the snapshot and the restore test, the log
lines above, and the two counts.

**Cleanup.**

- The cleanup pull request of the restore test.
- On the host, after the way back is closed (the owner decides when; the
  daily archives of the pod go back 7 days): take the finance agent out of
  the host config, delete the token of the finance bot and the token file
  of the app from the host, and revoke that old token of the app in the app
  (the proxy has its own). Then the host holds no credential of finance.

### The way back

The way back takes out only what must go: the agent and the inbox. The
volume, its backup policy, its snapshots and the copy in `archives/start/`
stay; they hold no credential of the host. Flux never prunes the volume
(`kustomize.toolkit.fluxcd.io/prune: disabled` on `pvc.yaml`). To remove it
for real, one pull request takes the annotation out, and the next one takes
the file and `backup.yaml` out: Flux then deletes the claim, and the reclaim
policy wipes the volume on the node. The snapshots stay in the repository of
finance until their retention ends.

- **Before step 5:** a pull request takes out the files of step 4 (the
  restore Job, the two Secrets, the Deployment) and the inbox, if it is
  still in. Then turn the finance bot on again on the host. Nothing of the
  host changed.
- **After step 5:** a pull request sets `replicas: 0`; the pod stops and
  stops the polling. The scheduled jobs then do not run, but they stay on
  in the state of the agent; turn them off first
  ([Turn off the scheduled jobs](#turn-off-the-scheduled-jobs)) if the pod
  may start again. Then turn the bot on again on the host, with the state
  of the day of the cutover. The memory written on the platform since then
  is not on the host. To keep it, copy the workspace out before the pull
  request, read only:

  ```bash
  kubectl -n agents-finance exec deploy/agent -c backup -- \
    tar -C /home/node/data/state/workspace -cf - . > finance-workspace-back.tar
  ```

  and merge by hand the files the owner wants (the scripts there use the
  read port, so keep the scripts of the host).

### Who does what, in order

| # | Step | Who | Blocks |
| --- | --- | --- | --- |
| 1 | The MCP gateway merged and ready ([mcp-gateway.md](mcp-gateway.md)) | Lead writes, owner merges | Step 3 |
| 2 | Merges 1 and 2 (volume, inbox, backup policy) and their checks | Lead | Step 4 |
| 3 | The values of `agent-secret` (with `MCP_GATEWAY_KEY` and its hash at the MCP gateway) and the entry of `keys.json`, and the token of the finance app | Owner (R4) | Step 4 |
| 4 | The model path of finance records (D65), and the risk of web search, writes and finance data in one session | Owner (gate) | Step 5 |
| 5 | The go of the cutover, for a time with no other change | Owner (gate) | The host steps |
| 6 | The host: the finance bot off, the copy of the workspace, the vault | Owner (host) | Step 4 |
| 7 | The pull requests of steps 4 and 5 (the name of the archive from the owner), and their checks | Lead writes, owner merges | The exit check |
| 8 | The first message, the tool call and the vault read | Owner | The exit check |
| 9 | The checks, the backups, the restore test, the heartbeat entry | Lead (the restore test with the owner's go) | The exit check |
| 10 | The host cleanup, after the way back closes | Owner (host) | None |

## The seed of the finance share

The first notes of the finance share, from the copy of the vault on the
state volume of the finance agent, with the go of the owner. What the Job
does and its lines are in
[agent-platform.md](agent-platform.md#the-seed-of-the-finance-share). The
copy on the state volume stays as it is, and nothing reads the share before
the agent mounts it (step D of task 3.36), so a seed that goes wrong costs
no note.

Before: the claims `vault-rw` and `vault-ro` of `agents-finance` are
`Bound`, `kubectl -n flux-system get kustomization agents-finance-agent
agents-finance-vault-share` shows each `Ready`, and the agent pod runs.
Ask the owner not to make a note through the vault route while the seed
runs: a note in the share stops the Job (`share-not-empty`, or `rename` if
it lands in the minutes of the copy).

| Step | Change | What it does | Check | Way back |
| --- | --- | --- | --- | --- |
| 1 | In: the folder `kubernetes/apps/ai/agents/finance/vault-seed/` and its line `./vault-seed/ks.yaml` in the `kustomization.yaml` of `finance`, with `SKIP` and `TAKE` empty | The scan, the finding lines, and the copy if there is no finding | `kubectl -n agents-finance get job vault-seed`; in Loki, `{namespace="agents-finance", app="vault-seed"} \|= "result="` | Revert step 1. Flux deletes the Job; a folder `.seed-incoming-*` of a stopped copy goes at the next run |
| 2 | Only with `reason=findings`: the ids of the owner in `SKIP` or `TAKE`, and the name `vault-seed-2` (then `-3`) | The same, with his decisions | The same, with the new name | Revert step 2 |
| 3 | Out: `./vault-seed/ks.yaml` from that list | Flux deletes the Kustomization, the Job and its ConfigMap. The notes stay in the share | `kubectl -n flux-system get kustomization agents-finance-vault-seed` is not found | n/a: the Job did its one run |

**The findings, step 2.** Only the owner reads the finding lines:
`{namespace="agents-finance", app="vault-seed"} |= "finding"` in Grafana.
Each line has the id, the rule, the line and the relative path of the file,
and never the value. The lead reads only the line `report=ok findings=<n>
undecided=<n>` and the result line. For each finding the owner says one of:

- **skip**: the file stays out of the share. Its id goes in `SKIP`.
- **take**: the file goes as it is (a false hit, or a value he accepts in
  the vault). Its id goes in `TAKE`.
- **change it first**: he takes the value out of the note through the
  finance agent, which writes its copy. The file changes, so its id
  changes, and the next run shows the new state.

The owner gives the ids, never a name, and the pull request holds the ids
only. A finding in `.obsidian`, `.trash` or `.git` shows
`decision=left-out-...` and needs no decision, since that file never moves.
An id that is not a finding of the run (the note changed) fails the run
with `stale-decision`: read the lines of that run, and decide again.

**The pass rule, step 1 or 2.** The Job is `1/1`, and the result line is
`vault-seed result=ok` with:

- `source-notes` the same as `vault-notes` in the result line of the restore
  Job of the cutover, or more by the notes that the agent made since (the
  owner says so);
- `files` plus the four `left-out-*` counts the same as `source-files` (the
  Job checks this too, and fails with `verify` if not);
- `left-out-findings` the count of the ids in `SKIP`, and `taken-findings`
  the count in `TAKE`;
- `links=0 other=0`: the restore Job let no link in, so another count means
  the copy on the state volume changed in a way to look at.

With `reason=source-missing`, `source-empty` or `nothing-to-copy`, the Job
found no notes in `vault/` of the state volume (it mounts the volume at its
root and reads that folder only). Do not change the path to make it pass:
check with the owner where the restore Job of the cutover put the vault
(`vault-notes` in its result line), then fix the Job.

Then step 3, and then the mount of the agent (step D).

**The way back.** Before step D nothing reads the share, so a wrong seed
harms no note: the copy on the state volume is the original. To empty the
share again, its files must go on the claim of the share in `storage`; the
vault server has no delete tool. That is a cleanup Job in the same form, or
the owner on the NFS server, and it is not in git today. A failure with
`rename` leaves a part of the copy in the share: empty it the same way
before a second run.

## The restore test of the finance share

The proof that a nightly snapshot of the finance share comes back whole:
the template `kubernetes/apps/ai/agents/finance/vault-backup/restore-test/`,
in the form of the restore test of the state volume. Three pull
requests: the run and the cleanup with the owner's go, then the placeholder.

**The first change** lists `./vault-backup/restore-test/ks.yaml` in
`finance/kustomization.yaml`, with the name of the newest `Succeeded`
snapshot of the policy `vault` in `restore/restore.yaml` and as `SNAPSHOT`
in `check/job.yaml` (the same name; a test holds the two equal):

```sh
kubectl -n agents-finance get snapshots.kopiur.home-operations.com | grep '^vault-'
```

Then Flux:

1. Makes the scratch claim `vault-restore-test` (the default class,
   `local-path`, 2 GiB, on the node of the restore mover), and the
   Restore `vault-restore-test` fills it from the snapshot. It reads the
   repository `agents-finance` only. The share, its claims `vault-rw` and
   `vault-ro`, the vault server and the agent keep running and do not
   change.
2. When the Restore is `Completed`, runs the Job `vault-restore-check`. It
   mounts the scratch claim and the live share (`vault-ro`), both read
   only, and compares them by relative path and SHA-256 of each file. It
   has no network, no token and no Secret, and keeps its lists in memory.

**Pass.** `kubectl -n agents-finance get job vault-restore-check` is `1/1`,
and in Loki, `{namespace="agents-finance", app="vault-restore-check"}` has
`vault-restore-check result=ok files=<n> notes=<m> bytes=<b>`. The line
`side=restored` and the line `side=live` have the same counts, and the
line `compare` has `only-live=0 only-restored=0 same-dirs=yes
same-links=yes`. The lines hold flags and counts only, never a name. The
count of the share is not in git: the Job reads it from the share at run
time.

**A failure** is `result=failed reason=<list>`:

| Reason | Meaning |
| --- | --- |
| `snapshot-name` | `SNAPSHOT` is not `vault-` and 14 digits of a real time. Nothing was read |
| `empty`, `no-notes` | The restore holds no file, or no note |
| `files`, `notes`, `bytes` | A count of the restore is not the count of the share |
| `content` | A file of one side is not in the other with the same path and the same SHA-256 |
| `dirs`, `links` | The folders or the links are not the same |
| `unreadable`, `live-unreadable` | uid 1000 can not read a path of the restore, or of the share |
| `share-changed` | Added to the others when a file of the share changed after the time in the name of the snapshot (`live-newer=<n>`): a write through the vault route, not a fault of the restore. Run the test again after the next nightly snapshot: the second change, then a new first change with the new name |

A failed Job fires `KubeJobFailed` (`agents-finance`,
`vault-restore-check`); a failed or stuck Restore fires
`KopiurRestoreFailed`.

**The second change**, after the result is read and recorded, takes the
line `./vault-backup/restore-test/ks.yaml` out of `finance/kustomization.yaml`,
with no other edit. Flux prunes the two Kustomizations, and with them the
Restore (and its copy of the credentials), the Job and the scratch claim;
the reclaim policy `Delete` of the class wipes its folder on the node. The
restored notes must not stay behind. Check: `kubectl -n agents-finance get
restore,job,pvc` shows no `vault-restore-test` and no
`vault-restore-check`, and `kubectl -n flux-system get kustomization
agents-finance-vault-restore-test` is not found.

**The third change**, only after that check, sets the name back to
`vault-SET-ME` in both files, so the directory is a template again. Not in
the second change: Flux then applies the placeholder to the Restore of the
child Kustomization while the parent prunes it. The Restore waits for a
snapshot that is not there, and the deletion waits for the health check of
the child (45 minutes), with the copy on the scratch claim until then. The
cleanup of the run of 2026-11-20 did both in one change and met this.

**The run of 2026-11-20.** The Restore of the nightly snapshot of
2026-11-19 was `Completed` at 04:51 UTC, and the Job printed `result=ok`
at 04:52: the same files, notes, bytes, folders and links on both sides,
nothing unreadable, and no file of the share newer than the snapshot. The
next change took the test out of the list.

## The cutover of the main agent

**Draft.** The main agent moves from the host to `agents-main` (phase 6 of
the platform, milestone M7a). It follows [The cutover of the finance
agent](#the-cutover-of-the-finance-agent): the same files, the same order
of merges, the same checks and the same rules. This section names only what
is different. The files are in `kubernetes/apps/ai/agents/main/agent/`.

The differences from finance:

- **One Telegram account first, Signal later.** The main agent moves with
  one Telegram account: the direct chat of the owner and one group (D108).
  Telegram moves first. Signal moves later, with its own go of the owner,
  its own steps and its own exit check ([The Signal move](#the-signal-move)).
  Until then the host keeps the Signal number, and the main agent on the
  platform has Telegram only. WhatsApp does not move (D103).
- **The group.** A Telegram chat id is a key of the config of the product,
  and the product puts an env value in a value only, not in a key. So the
  config includes `telegram-groups.json`, and that file comes from the
  Secret `agent-telegram-groups` of the owner, beside the ConfigMap in the
  volume `config`. The file is one JSON object with one key, the chat id of
  the group, and the settings of that group as on the host:
  `{"<chat id>": {"requireMention": false}}`. The values check stops the
  start on any other form (`reason=form`), so there is no `"*"` entry: the
  host has one, with `requireMention: true`, and it lets the bot answer in
  any group it is added to. No chat id is in git.
- **Who may write to the bot.** The host account of main has `dmPolicy:
  pairing`. The copy holds the workspace only, so no pairing of the host
  moves. The platform has `dmPolicy: allowlist` with the owner's id
  (`TELEGRAM_ALLOW_FROM`), as the product docs advise for a bot of one
  owner. In the group, the senders are the same list: the config sets no
  `groupAllowFrom`, so the product uses `allowFrom`.
- **Settings as on the host:** `historyLimit: 15` and `streaming.mode:
  partial` on the account.
- **Thinking level:** `thinkingDefault: high`, by the owner's word of
  2026-11-20. The host has `off`. The model of the platform does not take
  `off`, so the product used `low` and wrote `Thinking level "off" is not
  supported` in each run. Proof after a change: the start line `agent
  model: litellm/agents-main (thinking=high, ...)`, and no such line in the
  next scheduled run.
- **Not as on the host:** the heartbeat is off on the host, and the pod
  has the daily heartbeat of the platform (one short turn a day, which the
  heartbeat check of the platform reads).
- **The tools of the first step of M7a.** Built-in tools of the product
  only (the plan of the platform, the proposal of task 6.14): files, code
  and the shell in the sandbox, web search with the key of main, web fetch
  through the fetch proxy of main ([Web fetch of the main
  agent](agent-platform.md#web-fetch-of-the-main-agent)), a look at
  an image (the model reads it), memory search on the workspace, and the
  `message` tool (D16), which can reach only the chats of the bot: the
  direct chat of the owner and the group. No finance app (D69), no vault
  (task 3.37), no new image (the model gateway has no image route), and no
  scheduled job: the Monday job moves with its skill at the Signal step
  (D110). The read tools of M7b come after it, through the MCP gateway
  ([The read step of M7b (main)](#the-read-step-of-m7b-main)).
- **The owner's decisions of 2026-11-19 for the first step:** the
  `progress_card` tool is in the sandbox list, and each session of main
  sees the other sessions of main, as on the host (`tools.sessions.visibility`
  and `agents.defaults.sandbox.sessionToolsVisibility`, both `all`): text of
  the direct chat can show in an answer in the group. Web fetch comes
  through an egress proxy pod of its own, a later change. The tool that
  writes skills is a later step.
- **Gaps of the first step**, each a later step: voice notes (the host has
  a speech to text command that the image does not have); messages to the
  other agents of the host (`tools.agentToAgent`; on the platform each
  trust domain has one agent); the memory plugins of the host
  (`active-memory`, `memory-wiki`, dreaming: task 3.37); the ACP plugin;
  and each skill of the workspace that needs a tool that this step does not
  have.
- **Messages between agents stop.** On the host the main agent and the
  finance agent can send messages to each other. On the platform they
  can not (Design Doc, section 4.1.3, the owner's decision of 2026-11-10).
  The owner tells the household before the cutover.
- **A repository of its own.** The backup policy of step 2 uses the
  ClusterRepository `agents-main` ([the finance
  repository](../operations/backups.md#the-finance-repository), with the
  names of main).

### The test run beside the host

The owner's decision of 2026-11-19: before any cutover, the main agent on
the platform runs beside the main agent on the host for a time. Telegram
gives the updates of a bot to one poller, so the two can not share a bot:
the pod gets the **test bot of the pilot**, by the owner's decision of
2026-11-19 ("move it"), and the host keeps its bot, its Signal number and
its scheduled jobs. Nothing changes on the host. The pilot's Telegram
channel goes off in the same change ([The pilot
agent](agent-platform.md#the-pilot-agent)), so the bot has one poller.

What is different from the steps below in the test run:

- **No host step.** The host agent is not stopped. The copy of the
  workspace was made while it ran, with a check that no file of the copy
  changed in that time (the run of 2026-11-19: none did).
- **The copy** leaves out more than the copy of finance: the scratch folder
  `tmp/` of the workspace (about 1.1 GB; the pod keeps an archive of the
  state for each start and seven daily archives, so a copy with it fills the
  10 GiB volume in about a week), files over 50 MB, the Python
  environments, and each link. Each finding of the secret scan was made
  blank in the copy only. The copy of `AGENTS.md` has one more section,
  which says that this is a test copy and what it does not have yet. The
  run of 2026-11-19: 1300 files, about 100 MB.
- **Step 4b moves the test bot** (below): its token goes into the Secret
  of main, in place of the placeholder, and the channel of the pilot goes
  off. Main stays at zero replicas in that change. Step 5 comes after the
  lead saw that the pilot polls no more.
- **The two workspaces go apart** from the time of the copy. Before the
  real cutover the owner decides what moves: a new copy of the host
  workspace in place of the one of the test run, or the one of the test
  run.
- **The real cutover** follows the steps below, later, with its own go of
  the owner.

### The order of the merges

| Step | The change | What the merge applies | The check | The way back |
| --- | --- | --- | --- | --- |
| 1 | List `pvc.yaml` and `state-inbox.yaml` | The volume `agent-state` and the inbox pod, on the node of the agent pods. No product code, no secret, no network | `kubectl -n agents-main get pvc agent-state` is `Bound`; `kubectl get pv <its volume> -o jsonpath='{.spec.nodeAffinity}'` names the node of the agent; `kubectl -n agents-main logs deploy/agent-state-inbox` is `state-inbox result=ready` | Take the inbox out. The volume stays: it has `kustomize.toolkit.fluxcd.io/prune: disabled` |
| Repo | The repository `agents-main` ([the steps](../operations/backups.md#the-finance-repository), with the names of main) | Four merges, as for finance | `kubectl get clusterrepository agents-main` is `Ready` | The same steps in the reverse order |
| 2 | List `backup.yaml` | The snapshot policy and schedule to the repository `agents-main` | `kubectl -n agents-main get snapshots.kopiur.home-operations.com`: one `Succeeded`. No `KopiurSnapshotFailed` | None needed: the policy stays with the volume |
| 2b | The entry `agents-main-embeddings` of the model gateway | The model gateway pod rolls. No key lists the entry yet | `kubectl -n ai get deploy litellm` is ready; its log has no config error | Revert |
| 3a | List `networkpolicy.yaml` and `deployment.yaml` at `replicas: 0`, with the config | The network policy, the two ConfigMaps and the Deployment at zero: no pod, so nothing reads a Secret | `kubectl -n agents-main get deploy agent` is `0/0`; `kubectl -n agents-main get cnp agent` exists | Revert |
| 3b | The owner's values: the three Secret files, not listed yet, and the entry `agents-main` in the key list of the model gateway ([Before the cutover](#before-the-cutover-main)) | The key `agents-main` at the model gateway. Nothing in `agents-main` | `kubectl -n ai logs deploy/litellm -c ensure-keys` has `agents-main: created` (or `updated`) `(models: agents-main, agents-main-embeddings)` | Revert. The key stays at the model gateway until it is removed in the admin UI |
| 3c | The fetch proxy, after 3a and before 5: `main/fetch-proxy/`, the rule to it in the agent's network policy, the ConfigMap `agent-fetch-proxy` and `tools.web.fetch` in the config | The proxy pod, its Service and its policy. The agent's policy, its config and a new pod template. At zero replicas no agent pod starts, and step 5 starts it with the proxy env; at one, the agent pod rolls once. The restore Job of step 4 does not change | `kubectl -n agents-main get deploy fetch-proxy` is `1/1`; `kubectl -n agents-main get cnp fetch-proxy agent` exist; `kubectl -n agents-main logs deploy/fetch-proxy` has no `[error]` line | Revert. The agent then has no web fetch |
| Host | The owner stops the Telegram account of the main agent on the host and its scheduled jobs, makes the copy of the main agent, and puts it in the volume | Nothing in git | The checksum and the count of the copy | Turn the account and the jobs on again on the host |
| 4 | Out `state-inbox.yaml`; in the three Secrets and `restore-job.yaml` (with `ARCHIVE` set; `VAULT` empty) | The Job checks each member of the archive, then puts the copy in place as `state/`. The Secrets. No pod | `kubectl -n agents-main get job agent-restore` is `1/1`; in Loki, `{namespace="agents-main", app="agent-restore"}` has `restore result=ok archive=start/<name> files=<n> vault-notes=none`, with the count of the owner (`none`: no vault archive) | Revert step 4, then turn the account on again on the host. The copy stays in `archives/start/` |
| 4b | The move of the test bot: `TELEGRAM_BOT_TOKEN` in `main/agent/app/secret.sops.yaml` holds the token of the pilot's test bot, and `channels.telegram.enabled: false` in `pilot/agent/config/openclaw.json` | The Secret `agent-secret` of main (no pod reads it: main is at `0/0`). The pilot pod starts again once, with no Telegram account | `kubectl -n agents-pilot get deploy agent` is `1/1` with the new pod; `kubectl -n agents-pilot logs deploy/agent -c agent \| grep -c '\[telegram\]'` is `0`, and its line `http server listening` names no `telegram`; `kubectl -n agents-main get deploy agent` stays `0/0` | Main at `0/0` first, then revert: the pilot polls the bot again |
| 5 | Out `restore-job.yaml`; `replicas: 1` in `deployment.yaml` | The agent pod starts and polls the bot | [The cutover](#the-cutover-on-telegram) | [The way back](#the-way-back-of-the-main-agent) |

After step 5: the restore test of the state (a template
`main/agent/restore-test/`, made from the one of finance, with no vault
count), and `openclaw-agents-main` in `SERVICES` of
`agents/monitoring/app/heartbeat-check.yaml` after its first heartbeat in
Tempo.

### Before the cutover (main)

The values are the owner's (R4), and none of them goes through an agent or
a session. He makes each file in a pull request: a Secret in plain YAML at
the path below, then `sops -e -i <file>` (the rule of `kubernetes/` in
`.sops.yaml`), or `sops <file>` to put a value in place of `pending` in a
file that exists. The files are not listed until step 4, so the merge
starts nothing. The pod checks each value at its start (`values-check`).

**The run of 2026-11-19.** The owner gave the making of the three files and
of the key entry to the lead. One script made them: two new random values,
and the user id and the group id of the Telegram account, read from the
config of the host by their key paths. No value was printed, and none is in
another file. Two values are not as the table says, for [the test run
beside the host](#the-test-run-beside-the-host):

- `TELEGRAM_BOT_TOKEN` was the placeholder `pending` until step 4b, which
  put in the token of the pilot's test bot (a script copied it from the
  Secret of the pilot, with no value printed; the user id was the same in
  both files already). The token of the host never goes to the pod while
  the host uses it.
- `BRAVE_API_KEY` is the search key of the host, by the owner's decision. The
  pilot uses the same key, so it is not a key of main alone. A key of main
  alone is a later step.

| File, Secret | Key | The value |
| --- | --- | --- |
| `secret.sops.yaml`, `agent-secret` | `OPENCLAW_GATEWAY_TOKEN` | A new random value: `openssl rand -hex 32` |
| | `LITELLM_API_KEY` | A new key: `sk-agents-main-` and `openssl rand -hex 24`. The same value goes in a new entry of `keys.json` in `kubernetes/apps/ai/litellm/app/secret-keys.sops.yaml`: alias `agents-main`, models `["agents-main", "agents-main-embeddings"]` ([litellm.md](litellm.md#keys-of-the-agent-platform)) |
| | `TELEGRAM_BOT_TOKEN` | The token of the Telegram account of the main agent, the one the host uses now |
| | `TELEGRAM_ALLOW_FROM` | The numeric Telegram user id of the owner, as on the host |
| `secret-search.sops.yaml`, `agent-search-secret` | `BRAVE_API_KEY` | A search key of main, of its own (D64), never the key of another trust domain |
| `secret-telegram-groups.sops.yaml`, `agent-telegram-groups` | `telegram-groups.json` | One JSON object with one key: the numeric chat id of the group (it starts with `-`), with the settings of that group on the host: `{"<chat id>": {"requireMention": false}}` |

The check of each value prints only `ok` or `not ok`, as for finance:

```bash
F=kubernetes/apps/ai/agents/main/agent/app/secret.sops.yaml
for k in OPENCLAW_GATEWAY_TOKEN LITELLM_API_KEY TELEGRAM_BOT_TOKEN TELEGRAM_ALLOW_FROM; do
  printf '%s ' "$k"
  bin/sops -d --extract "[\"stringData\"][\"$k\"]" "$F" \
    | awk '{ v = v $0 } END { print (length(v) > 0 && v != "pending") ? "ok" : "not ok" }'
done
```

Before step 5 the owner also decides the open points of M7a in the plan
(D112, the rows of D3 for main), and he tells the household that messages
between the main agent and the finance agent stop.

### The copy of the main agent

As [The inventory of a move](#the-inventory-of-a-move) and [The copy of the
state](#the-copy-of-the-state) of finance, with these changes:

- The copy holds the **workspace of the main agent only**: not the finance
  agent, not the reviewer, and no other agent of the host (R6).
- No seed of a vault, unless the inventory finds a folder that the main
  agent reads. Then the owner decides if it comes as a seed, as for finance
  (D71).
- No step for the finance app. Each instruction file, script or skill that
  names a tool that the main agent does not get on the platform changes in
  the copy, not on the host, as in step 2 of the finance copy. The rule for
  a script holds: one file, run as `python3 <file>.py`.
- The scheduled jobs of the main agent are in the inventory, and they go
  off on the host at the host step of the cutover. None runs on the
  platform until the Signal step (the Monday job, D110).
- The Signal account state stays on the host. It is not in this copy.

### The cutover on Telegram

As [The cutover](#the-cutover) of finance, with these changes:

1. **The host stops the bot.** The owner turns off the Telegram account of
   the main agent in the config of the host gateway, and its scheduled
   jobs. The Signal channel of the host stays on. Check: the account is not
   running, and a message to the bot gets no answer.
2. **The copy.** Check: the checksum is `OK`, and the count is written down.
3. **Merge step 4.** Stop point: the restore Job fails.
4. **Merge step 5.** Checks, read only: the pod is ready with no restart,
   the start lines of the pod have no `result=failed`, and Telegram has no
   `409 Conflict`.
5. **The first messages**, by the owner: in the direct chat, a question
   that the memory answers; in the group, the same, with no mention of the
   bot (`requireMention: false`).

### The exit check on Telegram

1. **One turn in each chat** with a tool call: a web search and a web
   fetch of a public page in the direct chat, and in the group a `message`
   sent to the direct chat of the owner (D16). The log of the fetch proxy
   has one line for the fetch, with the host and `"code":200`
   ([The fetch proxy](#the-fetch-proxy-main)).
2. **The scheduled checks** of `agents-main` pass on their next runs:
   `platform-check-isolation` and `platform-check-probe`.
3. **The backups.** The first nightly snapshot with the state is
   `Succeeded`, and the daily archive of the backup container is in the
   log with `instruction-check result=first`.
4. **The restore test** with that snapshot (owner's go), as for finance,
   with no vault count.
5. **The heartbeat.** The first heartbeat of `openclaw-agents-main` is in
   Tempo; then the pull request that adds it to `SERVICES`. The first one
   came at 2026-11-19 15:05 UTC (`openclaw.run`, `trigger=heartbeat`,
   `outcome=completed`). The product puts the heartbeat at a fixed time of
   day, from the device identity in the agent's state and the agent id, so
   it comes at about 15:05 UTC each day, and the check of 06:33 UTC finds
   the one of the day before in its 26 hours.

**Pass.** Each step passed, and no alert of `agents-main` fired from step 1
of the merges, but the ones named in a step.

### The Signal move

**Waits for the owner**, with its own go, after the main agent runs on
Telegram. It needs three things first:

1. **The test of the Signal copy** (Design Doc, section 11, and the Signal
   half of pilot condition C5): with a test number, does the copy of the
   account on the host still work after the daemon on the platform handled
   messages? The pilot has not run it: the pilot has no test Signal number
   yet.
2. **The notice to the household:** a time when Signal can be down.
3. **The Signal daemon of main:** the daemon of the pilot
   (`pilot/signal-daemon/`) as `main/signal-daemon/`, with its volume, its
   backup policy in the repository `agents-main`, and a network policy that
   lets in the agent pod of main only.

**No way back, unless the test says so.** The Design Doc says: the first
message on the platform is the point of no return, unless the test showed
that the old copy works. If the test shows that the copy on the host stops
working, the way back after the first message is a new link of the account
by the owner, and the members who use Signal get no message until then.
The Telegram channel still works in that time.

The steps, in short (each one waits for the test and the go):

1. The host stops its Signal channel.
2. The owner copies the account state of the host into the volume of the
   daemon. How is open: the daemon has no inbox and no restore step yet.
3. A merge turns on the daemon, the Signal channel of the agent, its plugin,
   the rule to the daemon in the network policy of the agent, and the two
   Signal keys.
4. **The exit check on Signal:** one turn on Signal, and the evidence
   records the result of the test of the copy.

### The fetch proxy (main)

The way of a web fetch and its walls are in [Web fetch of the main
agent](agent-platform.md#web-fetch-of-the-main-agent). When a fetch fails,
read the log of the proxy:
`kubectl -n agents-main logs deploy/fetch-proxy --since=1h`. Each line is
one request, as JSON.

| The line | What it means | What to do |
| --- | --- | --- |
| `"code":200` | The proxy connected to the site. A failure after that is the site's answer, or TLS | Nothing on the proxy |
| `"code":403` with an IP address, another port, a name with no dot, or a method that is not `CONNECT` | The proxy refused it by design: only a host name on port 443, over HTTPS | None. A `http://` URL fails; the agent can try `https://` |
| `"code":503` with `"flags":"DF"` | The name has no public IPv4 address: it resolves inside (a private, shared, link-local or loopback range), has IPv6 only, or does not resolve | None, when it resolves inside. Check the name with a public DNS tool |
| `"code":503` with `"flags":"UF"` or `"UT"` | The site did not answer on port 443 | None on the proxy |
| No line | The request did not reach the proxy: the product refused the URL first (its log has `blocked URL fetch`), the host is in `NO_PROXY`, or the proxy pod is down | `kubectl -n agents-main get pods -l app.kubernetes.io/name=fetch-proxy` |

**The rehearsal on the cluster** (the rows of section 4.2.7 of the design).
The lab test covers the config in Docker; these rows cover the network
policies of Cilium, the DNS of the cluster, the restricted level and a turn
of the model. Each one runs after step 5, and none changes the live proxy.

| Rehearsal | Pass rule |
| --- | --- |
| A public page through the agent | In a chat turn, `web_fetch` of a public page answers. The log of the proxy has one line with its host, and no line has its path or query |
| A name that resolves inside | A fetch of a public name that resolves to a pod, node or LAN address fails. The target gets no connection |
| A redirect to an inside address | A public page that redirects to an inside name fails at the second hop. The target gets no connection |
| A port but 443, and plain HTTP | A fetch of a site on port 8443 and of an `http://` page each fail. The log of the proxy has a 403 for each that reaches it |
| The agent pod has no other path out | A direct connection from the agent pod to a public address on 443 that its policy does not name gets no answer |
| The second wall | A separate test pod runs the proxy config with no address filter, under a copy of the egress rules of the proxy's network policy that selects the test pod only. The test pod has a label of its own, never `app.kubernetes.io/name: fetch-proxy` (the Service and the agent would then reach it), and no agent pod reaches it. A fetch through it of a name that resolves inside gets no connection. The live proxy does not change: never edit or restart it for this row. Remove the test pod and its policy after |
| The direct paths | A chat turn, a web search and a model call each answer, and none of them is a line in the log of the proxy |
| The pod | The proxy pod is ready at the restricted level, with no restart for one day |

The proxy holds no state. A restart loses only its log
(`kubectl -n agents-main rollout restart deploy/fetch-proxy`, an owner
approval, as each live change). To stop web fetch fast, a pull request sets
`replicas: 0` in `main/fetch-proxy/app/deployment.yaml`: each fetch then
fails, and the rest of the agent works.

### The read step of M7b (main)

The cluster, Flux and Terraform servers of main go live in one pull
request: their pods, their routes on the MCP gateway with the hash of
the key of the main agent (the key of its vault route), and their server
entries in the config of the agent
([mcp-gateway.md](mcp-gateway.md), "The read servers of main"). The code
host server is one more pull request, when its read credential is there.

**Before the push** (the lead, on the branch, from the repo root):

```bash
bin/main-mcp-key
```

The key of main exists since its vault step, so it prints
`secret.sops.yaml key=MCP_GATEWAY_KEY kept ok`, then
`config.yaml route=main-vault unchanged ok` and one line
`config.yaml route=<route> set ok` for each read route. It never prints
the value or the hash, and it leaves the Secret file as it is. Commit
`kubernetes/apps/ai/mcp-gateway/app/config/config.yaml`. On the branch of
the code host server, run it again: it sets the hash on the new route.
Without it, a test fails
(`test_the_route_takes_the_main_key_only_and_sets_no_credential`): the
read routes would answer the agent with a 401.

**After the merge.** The new config of the agent and the new gateway
config roll the agent pod once (Flux applies the pod template with both
ConfigMaps at once). The new gateway config also rolls the gateway pod and
the finance agent pod, which mounts the same file. Each read below takes no chat content: the access log of the
gateway has the route, the method, the tool name, the status and the
client, never the arguments or the result.

| Read | Pass rule |
| --- | --- |
| `kubectl -n flux-system get kustomizations agents-main-cluster-mcp agents-main-flux-mcp agents-main-terraform-mcp agents-main-read-access` | Each `Ready` |
| `kubectl -n agents-main get deploy cluster-mcp flux-mcp terraform-mcp agent` | Each ready, with no restart |
| `kubectl -n agents-main logs deploy/agent -c values-check` | `values-check result=ok` |
| The owner asks the agent, in his chat, for one read of each server: the namespaces of the cluster, the state of the Flux Kustomizations, the latest version of a provider | The agent answers each one |
| `kubectl -n ai logs deploy/mcp-gateway --since=30m \| jq -c 'select(.client == "main") \| [.route, ."mcp.method.name", ."gen_ai.tool.name", ."http.status"]'` | For each of `default/main-cluster`, `default/main-flux` and `default/main-terraform`: a `tools/list` and at least one `tools/call` with a kept tool name, status 200. No 401 or 403 for the client `main`. The 405 of the stream at each start is noise ([mcp-gateway.md](mcp-gateway.md), "Logs and metrics") |
| `sum by (k8s_cluster, route, status) (increase(agentgateway_requests_total{route=~"default/main-(cluster\|flux\|terraform)"}[1h]))`, through thanos-query | A 200 series for each route, on site 1. No 5xx series |
| The board ([observability-operations.md](../operations/observability-operations.md)): the rows of the parts of main | Cluster, Flux and Terraform MCP server healthy. The code host MCP server has no row until its step |
| The next run of the gateway probe of main (`kubectl -n agents-main get jobs -l app.kubernetes.io/component=mcp-gateway-probe`) | Complete. It covers the vault route only, with its own key |

**The rehearsal rows** of the design that the lab did not cover (section
4.2.6, "Not tested"): the DNS rules of Cilium and the reach of each pod
(a pod of another namespace gets no connection to a server; only the
gateway does), and the slowest read under 30 s. A read that gets a `504`
at the gateway brings the step back to the owner.

**The way back** is one revert of the squash commit: the servers, their
routes and hashes, the read bindings and the three server entries go out
together. The key of main and its vault route stay, and the agent pod
starts again with its vault tools.

### The way back of the main agent

- **Telegram, before step 5:** a pull request takes out the files of step 4
  and the inbox, if it is still in. Then the owner turns the account on
  again on the host.
- **Telegram, after step 5:** as for finance: set `replicas: 0`, then the
  owner turns the account on again on the host, with the state of the day of the cutover. The memory written on
  the platform since then is not on the host; copy it out first, read only,
  as for finance.
- **Signal:** see [The Signal move](#the-signal-move).

The volume, its backup policy and its snapshots stay, as for finance.

### Who does what, in order (main)

| # | Step | Who | Blocks |
| --- | --- | --- | --- |
| 1 | Merge 1 (volume, inbox) and its check | Lead writes, owner merges | Step 4 |
| 2 | The repository `agents-main`: the role (Terraform apply), the certificate and the password (SOPS values), the session, the repository | Lead writes; the merge of the role is the approval of its apply; the owner makes the values, or gives them to the lead as for finance | Merge 2 |
| 3 | Merge 2 (backup policy) and its check | Lead writes, owner merges | Step 4 |
| 4 | Merges 2b (the embeddings entry), 3a (the pod at zero) and 3c (the fetch proxy), and their checks | Lead writes, owner merges | Step 4 of the merges |
| 5 | The rows of D3 for main and D112; then the values of the three Secrets and the entry of `keys.json` (step 3b) | Owner (gate, then R4) | Step 4 of the merges |
| 6 | The notice to the household: messages between the agents stop | Owner | Step 7 |
| 7 | The go of the cutover on Telegram, for a time with no other change | Owner (gate) | The host steps |
| 8 | The host: the Telegram account off, the scheduled jobs off, the copy | Owner (host) | Merge 4 |
| 9 | The pull requests of steps 4 and 5, and their checks | Lead writes, owner merges | The exit check |
| 10 | The first messages and the tool calls, in the direct chat and the group | Owner | The exit check |
| 11 | The checks, the backups, the restore test, the heartbeat entry | Lead (the restore test with the owner's go) | The exit check |
| 12 | The test of the Signal copy, the notice of a Signal down time, and the go of the Signal move | Owner (gate) | The Signal move |
| 13 | The host cleanup, after the way back of each channel closes | Owner (host) | None |

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
From bridge 0.3.0, `ModelPlanNearLimit` warns before the limit, and the tab
"Limits and security" of the AI board shows the use of each window and its
reset time.

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
   when the caller makes its 3 attempts. A personal agent answers the chat
   with an error.
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
in the middle leaves no run sandbox behind, and a Sandbox record that stays
is seen and removed. It is not run yet. Each step that changes the cluster
needs the owner's yes at that time, and the drill runs only when the owner
has approved it. These steps are for run receiver 0.2.0 and later.

**Why.** The run job deletes its sandbox at its end and on SIGTERM; the sweep
deletes a run sandbox older than one hour; the receiver checks the Sandbox
record of each run 60 s after its end. None of these has run on the cluster
for a stopped job.

**When.** Not near a run of the providers job (:07, :22, :37, :52), of the
sweep (:00, :15, :30, :45) or of the daily checks (04:17, 04:47), so each
step is one cause. A start at :09 to :12, :24 to :27, :39 to :42 or :54 to
:57 leaves the run and its record check clear of them. A real review needs
a Renovate pull request at a head with no review yet, and one model call;
at a limit of the plan, wait.

**Steps.**

1. Read only: `kubectl -n agents-workflows get sandboxes.agents.x-k8s.io`
   shows no run sandbox, and no alert of the platform's own rules
   (`kubernetes/apps/ai/agents/monitoring/app/prometheusrule.yaml`, such as
   `AgentRunSandboxTooOld`, `AgentWorkflowRunFailed`,
   `AgentPlatformCronJobFailed` and `AgentPlatformWorkloadNotReady`) fires.
   A chart alert `KubeJobFailed` for a failed Job kept from before the drill
   can fire; note its Job names so a new one is clear. Start a watch
   that prints the run pod, the Sandbox records and the pods of each
   sandbox as they change, and follows the log of the run pod (`get` and
   `logs -f` only).
2. Owner approval: start one run with one call to the receiver, with no
   retry (a real review, with the token in the shell of the owner and never
   on the screen).
3. Read only: wait for the line `INFO run_job: sandbox <name> is ready after
   N s` in the log of the run pod. The input goes to the agent right after
   it, and a review takes 15 to 50 s, so the stop comes 2 to 5 s after that
   line.
4. **Case A, a stop with SIGTERM.** Owner approval:
   `kubectl -n agents-workflows delete pod <run pod> --wait=false`. Pass:
   - the run job logs exit 5 and `sandbox <name> deleted`, and its result
     line says `failed`, `"error": "the run job was stopped"` and
     `"sandbox_deleted": true`;
   - the call gets `502` with that result, and the Job keeps it in the
     annotation `workflow-agents/result`;
   - within 90 s, `get sandboxes.agents.x-k8s.io` shows no run sandbox and
     no sandbox pod is left;
   - about 60 to 70 s after the end of the Job, the annotation
     `workflow-agents/sandbox-record` of the Job says `gone`, and the
     receiver logs no `Sandbox record` line.
   A value `left: <sandbox>` with one `ERROR run_receiver: run <job>: the
   Sandbox record of <sandbox> is still there` line is the fault of the
   gateway that answers a delete with success and keeps the record: case B
   then applies to it.
5. **Case B, a stop with no time to clean.** Owner approval, on a second
   run: `kubectl -n agents-workflows delete pod <run pod> --grace-period=0 --force`.
   The pod object goes at once. The kubelet still sends SIGTERM and gives
   the container about 2 s before it kills it, which is too short for the
   delete of the sandbox in most cases; if the run job logs `sandbox <name>
   deleted`, the case did not leave a sandbox, and its pass is that of case
   A. Pass:
   - the Job fails with no result line; the receiver keeps `failed` with
     `"output_kept": false`;
   - about 60 to 70 s after the end of the Job, the annotation
     `workflow-agents/sandbox-record` says `left: <sandbox>`, found by the
     prefix of the run key, and the receiver logs one ERROR line for it;
   - the sandbox and its pods stay at first (the agent can still post its
     comment);
   - the first sweep after the record is one hour old (within 75 minutes of
     its start) deletes it: the sweep log says `<sandbox> deleted: its
     Sandbox record is gone`, or, if the gateway kept the record, `the
     gateway answered the delete and left the Sandbox record; removed
     through the API` (a delete under the admission policy
     `workflow-sweep-deletes`, and one failed sweep);
   - `AgentRunSandboxTooOld` does not fire.
6. Read only, after each case: no run sandbox in
   `get sandboxes.agents.x-k8s.io`, no sandbox pod, and the lines above in
   Loki: `{namespace="agents-workflows", app="workflow-run"}`,
   `{namespace="agents-workflows", app="run-receiver"} |= "Sandbox record"`
   and `{namespace="agents-workflows", app="workflow-sweep"}`.
7. Record the times, the commands and each pass or fail in the readiness
   checklist of the platform.

**Alerts during the drill.** `AgentWorkflowRunFailed` fires for each
stopped run and clears 2 hours after the start of its Job. The chart rule
`KubeJobFailed` fires for each failed run Job for as long as the Job stays
(7 days). In case B, if the sweep removed the record through the API, its
Job fails: `AgentPlatformCronJobFailed` fires and clears at the next good
sweep, and `KubeJobFailed` fires for the failed sweep Job for as long as it
is kept (the CronJob keeps the last 3 failed Jobs).

**Rollback.** Nothing to roll back: the drill makes runs and deletes them.
If a sandbox stays past 80 minutes, the alert fires and its section above
applies.
