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
| `workflows` | [`actionlint.yaml`](../../.github/workflows/actionlint.yaml) | actionlint and zizmor on the workflows and local actions |

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
