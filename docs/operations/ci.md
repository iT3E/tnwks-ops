# CI: one entrypoint, one gate

Every PR and every push to `main` runs
[`ci.yaml`](../../.github/workflows/ci.yaml). It works out which parts of
the repo the change touches, calls the checks for those parts, and reports
one result, **Build Success**. That's the required check (with `privacy /
privacy` and `betterleaks / betterleaks`), and what Renovate's automerge waits
on.

```
changes ──┬─ kubernetes ──────────────┐
          ├─ observability ───────────┤
          ├─ home_assistant ──────────┤
          ├─ ansible ─────────────────┼─ Build Success
          ├─ security_regressions ────┤
          ├─ dependency_compatibility ┤
          ├─ workflows ───────────────┤
          └─ terraform (per root) ────┘
```

## How a run decides what to check

[`bin/ci-changes`](../../bin/ci-changes) diffs the event's base and head:

- for a PR, the PR's base and head;
- for a push, the commit before the push;
- `workflow_dispatch` turns everything on.

It matches the changed files against
[`.github/ci/domains.toml`](../../.github/ci/domains.toml) and prints one
`true`/`false` per domain, plus a JSON list per group, such as the Terraform
roots that changed. Each domain job in `ci.yaml` runs only when its domain is
`true`. A skipped job counts as passed. Build Success fails only on a job that
failed or was cancelled.

Some changes run everything:

- a change to `.github/ci/`, `ci.yaml` or `bin/ci-changes`, so a CI change
  tests all of CI;
- a missing or unreachable base commit (a new branch, a force push), because
  running too much is the safe failure.

Check what a change would run without pushing:

```bash
bin/ci-changes --base origin/main --head HEAD
```

The tests are `.github/scripts/test_ci_changes.py`. The `changes` job runs
them before it decides anything.

## The domains

| Domain | Workflow | What it checks |
| --- | --- | --- |
| `kubernetes` | [`kubernetes.yaml`](../../.github/workflows/kubernetes.yaml) | flate renders every Kustomization and HelmRelease, per cluster entrypoint; every image a PR adds exists; the rendered diff as a PR comment (never blocks) |
| `observability` | [`observability.yaml`](../../.github/workflows/observability.yaml) | Generated resources, schemas, recording rules, storage contracts. The control registry in `security/`: its schema, its generated outputs and its rule tests. The Garage/Thanos and Grafana rehearsals only when `observability_rehearsals` is on |
| `home_assistant` | [`home-assistant.yaml`](../../.github/workflows/home-assistant.yaml) | `check_config` against the pinned image, integration tests |
| `ansible` | [`ansible.yaml`](../../.github/workflows/ansible.yaml) | Playbook syntax and ansible-lint on the pinned controller. The Talos files under `talos/`: each file parses, each directory is a cluster of `talos_clusters`, and each YAML file of a cluster is in its patch lists or is named as no patch or as a live node's file, and homelab-wsl's two forms hold the same settings. The pinned `talosctl` builds each Docker cluster's machine configs from its lists, with throwaway secrets, and validates them for container mode |
| `security_regressions` | [`security-regressions.yaml`](../../.github/workflows/security-regressions.yaml) | Router output, service and OAuth callback regression tests |
| `dependency_compatibility` | [`dependency-compatibility.yaml`](../../.github/workflows/dependency-compatibility.yaml) | The compatibility gates' own tests. Its weekly report runs on its own schedule |
| `workflows` | [`actionlint.yaml`](../../.github/workflows/actionlint.yaml) | actionlint and zizmor on the workflows and local actions, and the conventions below (github-workflows' `workflow-conformance` action, plus `workflow-conformance.py` for `ci.yaml`) |

Each domain workflow is `on: workflow_call` and has no concurrency of its
own. `ci.yaml` cancels a superseded PR run as a whole. Check names show the
caller, for example `kubernetes / Render`.

## Outside the gate

- **Privacy, secret scanning, changelog, labeler.** These run on every PR, and
  on events `ci.yaml` shouldn't react to: a title edit, a label change.
- **Ansible host checks** (`ansible-hosts.yaml`) and **EdgeOS**
  (`edgeos-{plan,apply}.yaml`). They run on self-hosted runners against
  live hosts, so a host being down shouldn't block a merge. Ansible applies
  per site on push, schedule and dispatch.
- **Drift checks.** Nightly, against `main`, reported to Prometheus rather
  than to a PR: the Ansible hosts'
  ([`windows-ansible.md`](../wsl/windows-ansible.md#nightly-drift-check))
  and the Terraform roots' ([below](#terraform-drift)).

## Terraform: plan in CI, apply in Deploy

Every root under `infrastructure/terraform/environments/prod/` is a
`[groups.terraform.<name>]` entry in `domains.toml`: its paths (the root and
the modules it calls), the runner, the backend and any endpoint override.
`bin/ci-changes` prints the roots a change touched as a JSON list, and
[`terraform.yaml`](../../.github/workflows/terraform.yaml) runs one matrix
job per root:

| Field | Meaning |
| --- | --- |
| `title` | Check name, e.g. `terraform / Plan MikroTik` |
| `root` | Path under `environments/` |
| `runner` | `ubuntu-26.04` or a self-hosted label from `.github/actionlint.yaml` |
| `backend` | `local` (TFC state, plan on the runner, github-workflows' `actions/terraform`) or `tfc-remote` (plan and apply in Terraform Cloud) |
| `workspace` | The TFC workspace, `tfc-remote` only |
| `lock` | Concurrency group a root's plans and applies share, for roots that must never overlap (one device, one state lock) |
| `tf_vars` | `TF_VAR_<name>=<value>` lines, endpoint overrides that only hold on the runner. Never secrets |
| `enabled_var` | A repository variable that must be `true` for the root to run |
| `mask_sops` | SOPS files, relative to the root, whose every value is masked in the job's public log from the mask step on, before Terraform runs (`.github/scripts/sops-mask.py`; fails closed). Every line of every value must be at least 6 characters: a shorter one would be masked inside other words and could be read off, so the step fails on it, and such a value goes in a SOPS file that isn't listed. `local` only. See [`github.md`](github.md#the-mask) |
| `drift` | `true`: a nightly plan of `main` reports drift to Prometheus ([Terraform drift](#terraform-drift)). `local` with a `lock` only, on `tnwks-ops-runner` or `tnwks-ops-homelab-wsl` |

- **Plan.** `ci.yaml`'s `terraform` job, on a PR. A root's plan posts a
  sticky comment and gates Build Success: a plan that can't run (the
  runner, the device or TFC is down) blocks the merge, because merging
  would apply it. Only the owner's and Renovate's same-repo PRs plan on
  their own. Anyone else's waits in the `external-contributions`
  environment, and then only `tfc-remote` roots plan: other code never runs
  on the self-hosted runners.
- **Apply.** [`deploy.yaml`](../../.github/workflows/deploy.yaml), on push
  to `main`: the roots the push changed, in the `prod` environment (main
  only), one run at a time per root in merge order, never cancelled. CI's
  own files don't trigger an apply (`--ignore-always`). To re-apply by
  hand, dispatch Deploy with `roots` (comma-separated names, empty for all).

Adding a root is one `domains.toml` entry. The conformance job checks that
`ci.yaml` has a `terraform` job for the group.

## Terraform drift

Settings that can be changed outside Terraform (the GitHub web UI, a
device's own UI) drift from the code without anyone noticing until the next
plan. A root with `drift = true` in `domains.toml` gets a nightly check:

```
terraform-drift.yaml (13:15 UTC, or dispatch on main)
  Roots: bin/ci-changes --all, the roots with drift = true
  -> terraform.yaml, mode drift, one job per root ("terraform / Drift GitHub")
       runs-on: tnwks-ops-homelab-wsl, concurrency: the root's lock
       Mask SOPS values -> actions/terraform (mode plan) -> Report
         .github/scripts/terraform-drift.py   4 gauges, one line of counts
         PUT pushgateway.monitoring:9091/metrics/job/terraform_drift/root/<name>
  -> Prometheus (homelab-wsl) scrapes the Pushgateway every minute
       TerraformDrift, TerraformDriftCheckStale -> Alertmanager -> Telegram
```

- **Drift is a plan with changes**: the plan's detailed exit code, the
  action's `changes` output. Changes to outputs or imports alone count too.
  The check never applies, and plans `main` only (the jobs refuse any
  other ref).
- **Never beside a PR plan or an apply** of the same root: the drift job
  takes the root's `lock` concurrency group, which is why `drift` needs a
  `lock`. GitHub keeps one pending job per group and cancels the older one
  when another queues, as it already does between plans and applies: a
  drift check that queues while the root's apply runs and a PR plan or
  another apply waits cancels the waiting one (re-run it, or dispatch
  Deploy for an apply). The check runs at 05:15 Pacific to make that rare.
- **Respects `enabled_var`**: a root that is switched off isn't planned and
  reports nothing, so its check goes stale.
- **Public logs**: the same as a PR plan. The mask step runs first for a
  root with `mask_sops`, the action prints the counts and the changing
  addresses (with neutral keys for the GitHub root's private repositories),
  and the report prints one line of counts. No plan body, no progress
  lines. The conformance job fails a `terraform.yaml` whose drift job loses
  the mask step, the lock, plan mode or the report step.
- **A check that finds drift passes**, like the Ansible host check; the
  alert and the job summary show it. One that can't plan fails, and is
  reported as failed.

The metrics, per root. In Prometheus each carries `root`, `exported_job="terraform_drift"`
(the push's job; the target's own labels win) and the Pushgateway target's
`job`, `namespace`, `service`, `endpoint`, `container` and `k8s_cluster`:

| Metric | Value |
| --- | --- |
| `tnwks_terraform_drift_check_timestamp_seconds` | When the check finished |
| `tnwks_terraform_drift_check_drifted` | 1 if the plan has changes |
| `tnwks_terraform_drift_check_changed` | Addresses the plan would change (create, update, delete, replace, move) |
| `tnwks_terraform_drift_check_failed` | 1 if the plan didn't run (init, plan or the mask failed) |

Alerts, in `kube-prometheus-stack/addons/alerts/terraform-drift.yaml`
(homelab-wsl, like the Windows hosts' drift alerts):

- `TerraformDrift` (warning, after 5 minutes): the latest result within
  30 hours has drift or failed. Read the run's job summary (the addresses)
  or log (the error). Then put the change in code, or apply `main` over it
  (dispatch Deploy with `roots=<name>`), and dispatch Terraform drift to
  clear the alert; otherwise it clears with the next clean night.
- `TerraformDriftCheckStale` (warning, after 30 minutes): the result the
  Pushgateway holds for a root is over 30 hours old, or no root has
  reported for 30 hours while the Pushgateway is up (this also covers a
  check that never reported). The schedule, homelab-wsl's ops runner, the
  root's `enabled_var` or the Pushgateway is broken. One gap: right after
  a Pushgateway restart it holds nothing, so with several roots on, one
  that stopped reporting while the others still do goes unnoticed until
  its next result.

**Why a Pushgateway.** The repo had two ways for CI results to reach
Prometheus, and neither fits a job on the in-cluster ops runner:

- The Ansible host check writes a `.prom` file into windows_exporter's
  textfile directory on the site's Windows host, over SSH, from Ansible.
  The ops runner has no Ansible and no SSH key to the Windows hosts, and
  giving a public repo's runner admin on them for a metric would widen what
  it can reach.
- ARC's listener metrics (`gha_completed_jobs_total{job_name, job_result}`)
  count every runner job, but only as succeeded or failed (drift and a
  broken check look the same), with no time of the last run, and in
  memory: the listener restarts often, and a series born at its first
  count shows no increase, so the first run after a restart is invisible.
  Each site's listener also counts only its own jobs.

The Pushgateway
([`kubernetes/apps/monitoring/pushgateway`](../../kubernetes/apps/monitoring/pushgateway))
is the in-cluster counterpart of the textfile directory: the job writes its
last result, Prometheus scrapes it. It runs on homelab-wsl only, so the
drift job runs on that site's ops runner (`tnwks-ops-homelab-wsl`), whatever
the root's own `runner`. It keeps nothing on disk: Prometheus has what it
scraped, and the alerts read the last value within a window, so a restart
loses nothing they need.

Two limits keep it from being a way into Prometheus:

- **Who connects.** A `CiliumNetworkPolicy` lets only that site's ops
  runner pods (scale set `tnwks-ops-runner-homelab-wsl`: ARC labels each
  runner pod `app.kubernetes.io/component=runner` and
  `actions.github.com/scale-set-name=<scale set>`) and that site's
  Prometheus connect to port 9091, plus the host for the kubelet's probes.
  It works at L4: it doesn't tell a push from a scrape.
- **What a push becomes.** Anything that can push can send any series with
  any labels, so the ServiceMonitor filters at the scrape. With
  `honorLabels: false` the target's labels win: a pushed `job`, `namespace`,
  `k8s_cluster` and so on arrive as `exported_<name>`. Then only the four
  `tnwks_terraform_drift_check_*` gauges pushed under job `terraform_drift`
  with a root-shaped `root` are kept (not even the Pushgateway's own
  series), and of their labels only the target's, `root` and
  `exported_job`. A fake `kube_*` or `up` series, or a label a pusher
  invents, never reaches Prometheus. `pod` and `instance` are dropped too,
  so a root stays one series when the pod is replaced.

**Turning it off for a root** is the PR that removes `drift`. Its last
result stays in the Pushgateway until the pod is next replaced (a chart
upgrade, a node restart), so `TerraformDriftCheckStale` fires for that root
after 30 hours, and `TerraformDrift` keeps firing if that result was drift:
silence them in Alertmanager until then.

**Turning it on for another root** is `drift = true` on its entry, if the
check applies there: a `local` root with a `lock`, whose runner label
homelab-wsl's ops runner also carries. `test_ci_changes.py` checks all
three.

## CI stats

[`ci-stats.yaml`](../../.github/workflows/ci-stats.yaml) runs every
morning and writes a report to its job summary: runs a day by workflow and
actor, CI's time to result per event, the flake rate (CI runs that passed
only on a re-run), Actions cache usage, and runs waiting for over an hour.
The run fails, so GitHub notifies the owner, when the flake rate is over 2%
for the week, the cache is over 9 GB, or a run has waited for over an hour.
To run it locally:

```bash
GH_TOKEN=$(gh auth token) python3 .github/scripts/ci-stats.py --repo iT3E/tnwks-ops --days 7
```

## Bootstrap smoke

[`bootstrap-smoke.yaml`](../../.github/workflows/bootstrap-smoke.yaml)
runs every morning, and on a PR that changes it, to check that a fresh
cluster still bootstraps from main:

1. It boots a throwaway Talos-in-Docker cluster
   ([home-operations/talosctl-cluster-action](https://github.com/home-operations/talosctl-cluster-action))
   on the Talos and Kubernetes versions in `talos/wsl/versions.yaml`.
2. It installs Flux from the committed
   `kubernetes/clusters/wsl/flux-system/gotk-components.yaml`.
3. It points Flux at the commit over HTTPS (the repo is public), as
   `flux bootstrap` would, and applies `kubernetes/clusters/wsl/meta.yaml`.
4. It waits for `cluster-meta` and every Helm, OCI and Git source to be
   Ready.

A failure means a site rebuild would stall at the same step: the API server
rejects a Flux manifest, or a chart repository moved or died. The apps
aren't reconciled, because they need the cluster's SOPS age key.

## Adding a check

1. Add a `[domains.<name>]` entry to `.github/ci/domains.toml` with its
   paths. Add a test case to `.github/scripts/test_ci_changes.py` if the
   paths are subtle.
2. Write the workflow with `on: workflow_call`, per-job `permissions` and
   `timeout-minutes`, and no `concurrency`.
3. In `ci.yaml`:
   - add the domain to the `changes` job's `outputs`;
   - add a job named after the domain, gated on
     `needs.changes.outputs.<name> == 'true'`, that calls the workflow and
     grants every permission its jobs ask for (GitHub checks that even for
     jobs that end up skipped);
   - add that job to Build Success's `needs`.

Jobs that only call the GitHub API or run a few seconds of jq or stdlib
Python (`Changes`, `Build Success`, conformance, Renovate review's `Select`,
the compatibility `notify`) run on `ubuntu-slim`: a 1-vCPU container with
git, jq, Python 3.12, yq and gh, capped at 15 minutes. Anything that builds,
renders or installs toolchains stays on `ubuntu-26.04`.

The `workflows` domain's conformance job fails a PR that misses any of
these ([`workflow-conformance.py`](../../.github/scripts/workflow-conformance.py)),
and on any of the following (github-workflows'
[`workflow-conformance`](https://github.com/iT3E/github-workflows#actionsworkflow-conformance)
action):

- a job without `timeout-minutes`, or an `ubuntu-slim` job with more than 15;
- a runner label other than `ubuntu-26.04`, `ubuntu-slim` or a self-hosted
  label listed in `.github/actionlint.yaml`;
- a self-hosted job in a `pull_request` workflow without a same-repo check;
- an action or docker image not pinned to a SHA or digest;
- a workflow without top-level `permissions`;
- an `actions/checkout` without `persist-credentials` (`false`, or an
  explicit `true` for a job that pushes).
