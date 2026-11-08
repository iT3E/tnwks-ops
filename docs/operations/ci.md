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
          └─ workflows ───────────────┘
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
| `observability` | [`observability.yaml`](../../.github/workflows/observability.yaml) | Generated resources, schemas, recording rules, storage contracts. The Garage/Thanos and Grafana rehearsals only when `observability_rehearsals` is on |
| `home_assistant` | [`home-assistant.yaml`](../../.github/workflows/home-assistant.yaml) | `check_config` against the pinned image, integration tests |
| `ansible` | [`ansible.yaml`](../../.github/workflows/ansible.yaml) | Playbook syntax and ansible-lint on the pinned controller |
| `security_regressions` | [`security-regressions.yaml`](../../.github/workflows/security-regressions.yaml) | Router output, service and OAuth callback regression tests |
| `dependency_compatibility` | [`dependency-compatibility.yaml`](../../.github/workflows/dependency-compatibility.yaml) | The compatibility gates' own tests. Its weekly report runs on its own schedule |
| `workflows` | [`actionlint.yaml`](../../.github/workflows/actionlint.yaml) | actionlint and zizmor on the workflows and local actions; `workflow-conformance.py` for the conventions below |

Each domain workflow is `on: workflow_call` and has no concurrency of its
own. `ci.yaml` cancels a superseded PR run as a whole. Check names show the
caller, for example `kubernetes / Render`.

## Outside the gate

- **Privacy, secret scanning, changelog, labeler.** These run on every PR, and
  on events `ci.yaml` shouldn't react to: a title edit, a label change.
- **Terraform plans** (`*-plan.yaml`) and **Ansible host checks**
  (`ansible-hosts.yaml`). They run on self-hosted runners against live
  systems, so a host or runner being down shouldn't block a merge. Applies
  run on push to `main` in their own workflows.

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

The `workflows` domain's conformance job
([`workflow-conformance.py`](../../.github/scripts/workflow-conformance.py))
fails a PR that misses any of these, and on:

- a job without `timeout-minutes`;
- a runner label other than `ubuntu-26.04` or a self-hosted label listed in
  `.github/actionlint.yaml`;
- a self-hosted job in a `pull_request` workflow without a same-repo check;
- an action or docker image not pinned to a SHA or digest;
- a workflow without top-level `permissions`;
- an `actions/checkout` without `persist-credentials: false`.
