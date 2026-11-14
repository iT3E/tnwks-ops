# Agent platform runbook

What to do when an alert of the agent platform fires, when an agent does
not answer on its channel or a sandbox does not start, how to restore the
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
  route of Alertmanager. The message links to the board of the site and to
  this page.
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

15 alerts cover the platform: the 13 rules of
`kubernetes/apps/ai/agents/monitoring/app/` (11 in `prometheusrule.yaml`, 2
in `heartbeat-prometheusrule.yaml`), and the 2 rules of the kopiur chart that
match the snapshot policy of each state volume. No alert of the platform has
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
  within a minute (`sandbox-start name=<name> result=ok`).

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
A run in flight keeps the token of its start for its whole life, so only new
runs fail.

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

**Fix.** Delete the sandbox through the sandbox gateway, not as a Kubernetes
object, so the gateway's record goes too. Fix the sweep in git, then run it
now (owner approval):
`kubectl -n agents-workflows create job --from=cronjob/workflow-sweep sweep-now`.

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
treat it as an instruction the agent took from what it read: stop the agent
(scale it to zero in a pull request, owner approval), read the file in the
daily archive of the day before and of today, and restore the archive from
before the change ([after the loss of the volume](#after-the-loss-of-the-volume),
with that archive in place of the newest).

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
SnapshotPolicy failed. On the platform the policies are `agent-state` (and
`signal-state` when Signal is on) in `agents-pilot`.

**First checks.**

```sh
kubectl -n agents-pilot get snapshotpolicies,snapshots
kubectl -n agents-pilot describe snapshot <snapshot>
```

**Seen so far.** The first snapshot of the pilot's state volume failed on
2026-11-12 at the connection to the repository: the mover reaches the
dual-stack name of the object store, and the network policy of the mover did
not allow it (`5e219085`). The first scheduled snapshot after the fix
passed the same day.

**Fix.** Fix the policy or the network rule in git. The schedule `H 2 * * *`
runs in local time, not in UTC. [`backups.md`](../operations/backups.md) has
the rest.

### KopiurBackupStale

**What it means.** A rule of the kopiur chart: no good snapshot of a policy
in 48 hours.

**First checks.** As for [KopiurLastBackupFailed](#kopiurlastbackupfailed),
and `kubectl -n agents-pilot get snapshotschedules`.

**Seen so far.** Never.

**Fix.** As for [KopiurLastBackupFailed](#kopiurlastbackupfailed).

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
   minute.
2. **Seen so far.** After a power cut restarted the host on 2026-11-12, the
   sandbox gateway left each sandbox of the pilot `Stopped`, and the product
   does not start a stopped one. The `sandbox-start` container
   (`fcce4f2a`) starts them; a sandbox stopped on purpose that day was back
   in 47 seconds.
3. **Fix.** A sandbox gateway or a sandbox controller that is not ready is
   [AgentPlatformWorkloadNotReady](#agentplatformworkloadnotready). Else
   read the `result=failed` lines and the events of the sandbox pods, and
   fix the cause in git.

## Restore the state of an agent

The state volume of an agent holds `state/` (the product, with its workspace)
and `archives/` (`start/` from the archive step at each start, kept 14 days;
`daily/` from the daily backup, the last 7). Kopiur copies the whole volume
each night. An archive holds credentials: it never leaves the volume or the
snapshot.

Two cases, each step marked with its test. The owner runs a restore; each
step that changes the cluster is a pull request with owner approval.

### The restore Job

One Job does the restore inside the volume, with the agent at zero
replicas: it checks the archive, moves the state of now aside to
`state.before-restore-<time>`, and puts the restored state in place. It has
no `${...}`, because Flux substitutes those in this directory.

Add it as `kubernetes/apps/ai/agents/pilot/agent/app/restore-job.yaml`, with
the image and the `nodeSelector` of `deployment.yaml`, the archive in
`ARCHIVE`, and the file in the `kustomization.yaml` of the directory:

```yaml
---
# A one-time restore of the state volume (docs/apps/agent-platform-runbook.md).
# Remove it in the pull request that starts the agent again.
apiVersion: batch/v1
kind: Job
metadata:
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
            # daily/openclaw-backup-<day>.tar.gz
            - name: ARCHIVE
              value: daily/openclaw-backup-YYYY-MM-DD.tar.gz
            - name: OPENCLAW_OFFLINE
              value: "1"
          command: ["sh", "-c"]
          args:
            - |
              set -eu
              DATA=/home/node/data
              A="$DATA/archives/$ARCHIVE"
              stamp=$(date -u +%Y%m%dT%H%M%SZ)
              stage="$DATA/restore-$stamp"
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
archive that is not there. Not tested: the release check with a different
release, and the Job on the cluster.

### After the loss of the volume

The node or its disk is gone, or the volume is empty or broken. The newest
daily archive is in the newest good snapshot.

1. **Read the state of the backups.** Read only.
   ```sh
   kubectl -n agents-pilot get snapshots --sort-by=.metadata.creationTimestamp
   kubectl -n agents-pilot get pvc agent-state
   kubectl get pv | grep agents-pilot
   ```
   Pick the snapshot (the newest with phase `Succeeded`, or an older one
   from before the fault). The read of 2026-11-14 showed one snapshot each
   day at 10:30 UTC, each `Succeeded` from 2026-11-12. Not tested as a step
   of a restore.
2. **Stop the agent.** Owner approval. A pull request sets `replicas: 0` in
   `pilot/agent/app/deployment.yaml`. After the merge, wait until
   `kubectl -n agents-pilot get pods -l app.kubernetes.io/name=agent` shows
   no pod. Not tested.
3. **Restore the snapshot into the volume.** Owner approval. A pull request
   adds a kopiur `Restore` to `pilot/agent/app/` (the form is in
   [`backups.md`](../operations/backups.md#a-pvc-kopiur)):
   `source.fromPolicy.name: agent-state` (or `source.snapshotRef.name` for an
   older one), `target.pvcRef.name: agent-state`,
   `options.enableFileDeletion: true`, `credentialProjection.enabled: true`,
   and the `podSecurityContext` and `securityContext` of the mover in
   `backup.yaml` (uid 1000, the restricted level). Wait for the Restore's
   phase `Completed` (`kubectl -n agents-pilot get restores`). Not tested.
   - If the volume itself is gone, Flux makes a new empty PVC from
     `pvc.yaml`. A `local-path` volume binds to the node of its first pod,
     and the agent runs only on the node of its `nodeSelector`, so the mover
     must be that first pod on that node. A Restore has no node setting
     (`kubectl explain restores.spec.mover`). Check where the volume landed
     (`kubectl get pv`) before the next step; if it is on another node, the
     populator form of a Restore (`target.populator`, a PVC with a
     `dataSourceRef`) binds it at the agent's first start. Not tested.
4. **Restore the newest daily archive.** Owner approval. The snapshot copies
   the live files of a running pod, so its database can be torn; the daily
   archive in it is the consistent copy. A pull request adds
   [the restore Job](#the-restore-job) with
   `ARCHIVE=daily/openclaw-backup-<newest day>.tar.gz`, with the image of the
   release that made it. Wait for `restore result=ok` in
   `kubectl -n agents-pilot logs job/agent-restore`. Not tested on the
   cluster; the script was tested locally (above).
5. **Start the agent.** Owner approval. A pull request removes the Restore
   and the Job, and sets `replicas: 1`. The `archive` init container makes
   an archive of the restored state, and `doctor` checks it. Not tested.
6. **Check.** Read only: the pod is ready, the `sandbox-start` lines, an
   answer on the test channel (the owner), and the next
   `instruction-check result=` line. The restored state goes back to the
   time of the archive; the product says so in its warnings (approvals and
   the state of each channel go back too). Not tested.
7. **Remove the old state.** Owner approval, later: the
   `state.before-restore-<time>` directory, by a Job in a pull request as
   above. Not tested.

### After a failed update

An update of the agent image moved the state to a new format, and the agent
does not run. The way back is the release before, with the state from before
the update. The first start archive with the name of the failed release holds
that state: the archive step ran before the `doctor` of the new release. The
newest archive is the wrong one: a later start made it from the changed
state.

1. **Find the archive.** Read only. In Loki,
   `{namespace="agents-pilot", app="agent"} |= "archive-state result=ok"`:
   each line names the release and the file
   (`release=<release> name=openclaw-state-<release>-<time>.tar.gz`). Take the
   first line with the failed release. Not tested.
2. **Stop the agent and set the image back.** Owner approval. One pull
   request sets `replicas: 0` and reverts the image in `deployment.yaml` to
   the release before (a revert of the Renovate merge, with
   `replicas: 0` on top). Wait until no agent pod is there. Not tested.
3. **Restore the archive.** Owner approval. A pull request adds
   [the restore Job](#the-restore-job) with
   `ARCHIVE=start/openclaw-state-<failed release>-<time>.tar.gz`, and the
   image of the release before. Wait for `restore result=ok`. Not tested on
   the cluster; the script was tested locally (above).
4. **Start the agent.** Owner approval. A pull request removes the Job and
   sets `replicas: 1`. The start makes a new archive with the name of the
   release before. Not tested.
5. **Check, and keep Renovate off that release.** Read only: as step 6
   above. The update comes back as a new Renovate pull request; hold it
   until the cause is known. Not tested.
6. **Remove the old state.** As step 7 above. Not tested.

## Stop the workflow agent fast

The Renovate reviewer has three parts that let it act: the App at GitHub, the
receiver that makes its runs, and the providers job that gives a run its
tokens. Stop them in this order. None is tested yet.

1. **Suspend the App installation.** The owner, in the browser, in seconds:
   the settings of the installed App on the account (Installed GitHub Apps,
   Configure), Suspend. GitHub's docs ("Suspending a GitHub App
   installation"): a suspended App "cannot access resources owned by that
   installation account". A run in flight can't comment, and no new token
   works. The providers job then fails at `github-token`, which is
   expected.
2. **Stop the receiver and the providers job.** Owner approval. One pull
   request:
   - `replicas: 0` in `workflows/run-receiver/app/deployment.yaml`: no new
     run. A caller gets no answer.
   - `suspend: true` in the CronJob of `workflows/run-receiver/app/providers.yaml`:
     no new token.
   - Leave the sweep on: it deletes each run sandbox that is left.

   After the merge, `WorkflowProvidersStale` fires after 35 minutes; that is
   expected while the stop holds. `AgentPlatformWorkloadNotReady` does not
   fire for a workload scaled to zero by a merge.
3. **A run in flight.** Read only first: read the result of each run job
   (`kubectl -n agents-workflows get jobs -l app.kubernetes.io/name=workflow-run`).
   A run lives 40 minutes at most and deletes its sandbox at its end. To end
   one now (owner approval): `kubectl -n agents-workflows delete job <job>`.
   The pod gets SIGTERM and deletes its sandbox in its grace period of 90
   seconds; the Job and its kept answer go with it.
4. **The way back.** Revert the pull request, then the owner resumes the App
   installation. The next run of the providers job makes new tokens; check
   two runs in a row before the first review.

## Move a trust domain to Bedrock by hand

Only a person moves a trust domain from the Claude plan to Bedrock: the
config of the model gateway has no fallback, so nothing moves by itself. The
owner decides each move.

1. **Check the Bedrock path.** Read only:
   `LiteLLMBedrockCertificateExpiring` is not firing, and the owner runs
   "Test a name" of [`litellm.md`](litellm.md#test-a-name) for a name
   already on Bedrock, if one is.
2. **Move the name.** Owner approval. One pull request changes the one line
   of the trust domain in `router_settings.model_group_alias` of
   `kubernetes/apps/ai/litellm/app/configmap.yaml`, for example
   `agents-pilot: claude-opus-bedrock`. The finance agent follows the main
   agent in the same pull request. [`litellm.md`](litellm.md#moving-a-name-and-back)
   has the rule.
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
   `kind` `rate_limit`, and the `call` lines show 429. Release 0.2.3 sends a
   `retry-after` only when the engine gives the reset time. No call has gone
   through 0.2.3 at a real limit yet.
2. **What fails.** A workflow run fails, and its caller tries again with a
   new key at most 3 times; at a limit each attempt fails, so expect
   `AgentWorkflowRunFailed`. The model gateway sends each call to the bridge
   one time (`1e7d81c2`); the Anthropic SDK of the caller tries 2 more times
   and the reviewer's framework up to 6 attempts, so one model call reaches
   the bridge 18 times at most. The pilot answers the chat with an error.
3. **Decide.** The owner: wait for the reset, use less of the plan (the
   build sessions too), or [move a trust domain to Bedrock](#move-a-trust-domain-to-bedrock-by-hand).
   Nothing here is a fix in git.
4. **After the reset.** A review that failed comes back with the next push
   of its pull request, or a new attempt of the caller. Read the next
   `call` lines with status 200.

**Seen so far.** 2026-11-14, from 02:29 to about 05:30 UTC: the 5-hour limit
of the plan. The run of a review got a 502 in 32 seconds, and the bridge
logged `rate_limit` and then an error of the kind `success` with status 200.
Release 0.2.3 of the bridge (`04d7b4a3`) answers a limit with 429 and logs
the real status; the gateway's one attempt (`1e7d81c2`) keeps its retries
from multiplying.

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
