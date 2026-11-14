# Renovate: what merges itself and what Ori reviews

Every Renovate PR lands in one of three tiers, decided by
[`.github/renovate/autoMerge.json5`](../../.github/renovate/autoMerge.json5):

- **Automerge.** Leaf app bumps, and every update of our own images
  (`ghcr.io/it3e/*`, see below). Renovate merges them itself once every
  status check is green, including the required **Build Success**
  ([`ci.yaml`](../../.github/workflows/ci.yaml): every check the change
  touches, such as the flate render and "every new image exists", see
  [ci.md](ci.md)). If a merged bump then fails to reconcile,
  Flux error events page the War Room (`flux-system/addons/alerts`).
- **`impact/high`.** Cluster plumbing, Terraform, majors, and anything new.
  Never automerged. **The owner opts each one in by adding `owner/review`**;
  until then it just waits (reviews cost model time, so none run on their
  own). An opted-in PR goes to Ori's reviewer agent, whose job is to land
  it: it reads the release notes, pushes whatever migration the bump needs
  onto the Renovate branch (values, API versions, regenerated files, a
  sibling bump that has to ship in the same commit, Terraform `moved`/`import`
  blocks), merges once CI is green on that head, verifies the rollout, and
  merges a revert if the live check fails. It leaves a PR open (`ori/blocked`)
  only when something outside the PR blocks it, such as a prerequisite
  upgrade, and says what that is.
- **`impact/critical`** (always together with `impact/high`). A bad merge
  can't be undone by reverting the PR: Flux itself (`kubernetes/clusters/*/
  flux-system`, `fluxcd/flux2`), Talos/Kubernetes version majors and minors,
  CNI majors and minors, and majors of data stores and storage (on-disk
  formats). The agent prepares these exactly like `impact/high`, then stops:
  it posts a "ready" comment with the head sha, the irreversible step, the
  backup or rollback it checked, and how it will verify, and labels
  `ori/awaiting-approval`. **The owner approves by adding `owner/approved`**, and
  the agent merges that exact sha. The agent also adds `impact/critical`
  itself when a review turns up something one-way (a schema or data
  migration with no downgrade, a CRD or API removal, data that gets wiped, a
  Terraform plan that changes real resources). Only the owner removes it.

**Talos and Kubernetes versions** are Renovate groups with one PR per minor.
Kubernetes bumps roll out through tuppr when they merge; a Talos bump on WSL
also needs `task talos:upgrade:wsl` after the merge. Order and support-matrix
rules: [cluster-upgrades.md](cluster-upgrades.md).

**MS-01-only paths automerge for now.** MS-01 isn't built, so updates that
touch only what `kubernetes/clusters/ms-01` deploys and WSL doesn't (Cilium,
Rook Ceph, Thanos, the Vector agent, UISP, node-feature-discovery, the CNPG
backup bits, `talos/ms-01`, `kubernetes/clusters/ms-01`) automerge behind
Build Success at any update type, and never get an `impact/*` label. The
path regex lives in the last rule of `autoMerge.json5` and is negated in
every review rule. When MS-01 goes live, delete that rule and the negations.

**Our own images automerge at every update type.** `ghcr.io/it3e/*` are
the images of our app repos (docs/development/app-repos.md), and each one
is deployed as a release, `<version>@sha256:<digest>`. The app repo's CI
already gated that release (tests, the image test, the privacy gate), so
here Build Success is the only gate: patch, 0.x minors, majors and digest
updates all automerge, at any time of day (the base schedule is "before
7am"). addLabels accumulate and a later rule can't remove a label, so the
last rule in `autoMerge.json5` isn't enough on its own: every review rule
that could match these images negates them in `matchPackageNames`
(`"!/^ghcr\\.io\\/it3e\\//"`). Matchers AND together, so in a rule that
also lists packages the negation only removes ours, and the rules that
matched by path alone (platform/everything else, Flux itself) got the
negation as their package matcher. Terraform, ImmichFrame and the Flux CLI
rules can't match an `it3e` image, so they are left as they are. A third
party's 0.x minor or major still gets `impact/high`.

**Third-party digest updates open on Mondays.** Base images such as
`python`, `busybox` and `eclipse-mosquitto` re-push the same tag several
times a week. Each re-push was its own digest PR and CI run (14 in the 30
days to 2026-11-08), so a rule in `renovate.json5` lets them open only
before 7am on Mondays. A digest PR that's still open keeps moving to the
newest digest. Our own images aren't held.

The whole chain behind these PRs runs without a hand merge: a Renovate
dependency merge in an app repo releases a patch by itself, the release
builds `:<version>`, and the PR here automerges. Template and CI-only
changes in the app repos release nothing, so they never reach this repo.

The agent pushes and merges as the owner's GitHub account, so `owner/approved` is a
rule the workflow and the agent follow, not something GitHub enforces.
Moving Ori onto its own GitHub App identity makes it enforceable: the
owner label check below fails a PR of the two upper tiers until the owner
adds its label, and the App's token can't pass it.

## The owner label check

`Owner label` ([`owner-label.yaml`](../../.github/workflows/owner-label.yaml),
logic and rules in
[`owner-label-check.py`](../../.github/scripts/owner-label-check.py)) is a
check on every PR. It is meant to be a required check of the `main` ruleset,
from GitHub Actions.

| PR | Passes when |
| --- | --- |
| No `impact/high` or `impact/critical`, now or before (automerge tier, the owner's own PRs) | Always, at once. Renovate's automerge waits on it like any other check |
| `impact/high` | `owner/review` is on, and the owner added it after the push of the head commit |
| `impact/critical` | `owner/approved` is on, and the owner added it after the push of the head commit |

"The owner added it" means the most recent `labeled` event of that label
(issue events API) has the repository owner's user ID as its actor, the
actor type `User`, and no `performed_via_github_app`. A label from
Renovate, from a workflow's `GITHUB_TOKEN`, from the reviewer's GitHub App
(its installation token, or a user token it holds for the owner) or from
anyone else fails. A label taken off and put back by someone else fails,
because the latest event counts. "After the push" compares that event with
the newest entry of the activity API for the PR branch, which must be the
head commit. So a new push needs a new label, whoever pushed. The check
verifies this itself and doesn't rely on `renovate-review.yaml` clearing
labels on a push. Taking `impact/high` off doesn't take a PR out of its
tier: a `labeled` event in its history is enough.

It fails closed. An API error, a page of events it can't read, more than 20
pages, a missing event or push (retried 3 times, 10 seconds apart, for the
APIs' lag), a head that moved, a PR in a tier from another repository, or
one of the four labels missing from the repository (renamed or deleted)
fails it with one error line saying why.

It runs on `pull_request_target` (opened, reopened, synchronize, labeled,
unlabeled), so the workflow and the script come from `main` and a PR can't
change what decides. It checks out only the script, from `main`, runs no PR
code, holds no secret, and its token can only read. Each run reports on the
head commit of its event.

What it can't tell apart: anything that acts with the owner's own account
and no GitHub App in between (the browser, `gh`, a personal access token).
The reviewer on the host today pushes and labels that way, so the check
doesn't bind it; it binds the reviewer once it runs as its GitHub App.

## How an impact/high PR reaches Ori

```
Renovate opens PR, labels impact/high
Owner adds owner/review
  -> renovate-review.yaml  "Select" (GitHub-hosted)
       eligible? open, renovate[bot], this repo, impact/high, owner/review,
                 no ori/blocked, Build Success = success on the head sha
  -> renovate-review.yaml  "Review #<n>" (runs-on: openclaw-hooks)
       POST http://10.5.0.1:18789/hooks/agent   (OpenClaw gateway, WSL host)
         {"repo","pr","head_sha"}, agentId renovate, waitForCompletion
       job fails unless the run ended ok
  -> OpenClaw agent "renovate"
       re-checks the same gates, reviews (homelab-dependency-updates skill)
       migrates on the branch (new push = new review), merges
       (--match-head-commit) and verifies, or comments + labels ori/blocked
       impact/critical: stops at ori/awaiting-approval until owner/approved
       its final reply goes to the owner on Telegram
```

Triggers, all in [`renovate-review.yaml`](../../.github/workflows/renovate-review.yaml):

| Event | When it fires |
| --- | --- |
| `pull_request_target: labeled` (owner/review) | The owner asked for a review. Sends it if Build Success already passed |
| `workflow_run: CI` completed | Build Success finished on a PR. Sends it if `owner/review` is already on |
| `pull_request_target: synchronize` | A new push (Renovate's, or the agent's migration commit). Clears `ori/blocked`, `ori/awaiting-approval` and `owner/approved`, so each new head gets its own review. A push from Renovate also clears `owner/review`, so a rebase or newer version waits for the owner again; the agent's push keeps it |
| `pull_request_target: unlabeled` (ori/blocked) | The owner removed the label to ask for another review |
| `pull_request_target: labeled` (owner/approved) | The owner approved an `impact/critical` PR; the agent merges the sha it named |
| `schedule` 16:00 UTC | Daily sweep: re-sends anything still eligible (a failed review, a missed event). Only PRs with `owner/review` |
| `workflow_dispatch` | Manual, one PR or all |

State lives on the PR: a merged PR is closed, a blocked one carries
`ori/blocked` and a comment saying what blocks it, and a critical one waiting
on the owner carries `ori/awaiting-approval` and the "ready" comment. Nothing is
remembered anywhere else.

Reviews never overlap. The `openclaw-hooks` scale set has one runner, so
GitHub queues the next review until the current one finishes, and a
per-PR concurrency group collapses duplicate events for the same PR. A queued
job re-checks the PR before it calls the gateway and skips it if it was merged,
pushed or blocked in the meantime. The `Idempotency-Key` is
`renovate-review-<pr>-<sha>`, so re-running a job replays the first run's result
instead of starting a second review (while the gateway still has it in memory).

## Pieces

| Piece | Where | Notes |
| --- | --- | --- |
| Workflow | `.github/workflows/renovate-review.yaml` | No job checks out PR code, so `pull_request_target` and `workflow_run` (both run main's copy) are safe with the secret |
| Runner scale set `openclaw-hooks` | `kubernetes/apps/tools/actions-runner-controller/openclaw-runner/` | `minRunners: 0`, `maxRunners: 1`, no secrets, runs as uid 1001. Same GitHub App as `sites-builder` |
| Environment `openclaw-hooks` | repo settings | Secret `OPENCLAW_HOOK_TOKEN`; variables `OPENCLAW_NOTIFY_CHANNEL` / `_ACCOUNT` / `_TO` (where the agent's reply goes). Deployment branches: `main` only |
| Label `owner/review` | `.github/labels.yaml` | Only the owner sets it. Required for any review. Cleared by a Renovate push |
| Check `Owner label` | `.github/workflows/owner-label.yaml`, `.github/scripts/owner-label-check.py` | Fails an `impact/high` PR without the owner's `owner/review`, and an `impact/critical` PR without the owner's `owner/approved`, added after the head's push. Passes every other PR (above) |
| Label `ori/blocked` | `.github/labels.yaml` | Set by the agent, cleared by a new push or by hand |
| Labels `impact/critical`, `ori/awaiting-approval`, `owner/approved` | `.github/labels.yaml` | Critical tier: Renovate or the agent sets the first, the agent sets the second, only the owner sets the third. A new push clears the last two |
| OpenClaw hook | `~/.openclaw/openclaw.json` on the WSL host, `hooks.*` | `allowedAgentIds: ["renovate"]`, no caller session keys. Token copy in `~/.openclaw/credentials/openclaw-hooks.env` |
| OpenClaw agent `renovate` | `agents.entries.renovate`, workspace `~/.openclaw/workspace-renovate` | Tools: `exec`, `process`, `read` only. Its `AGENTS.md` is the review contract |
| Rendered diff comment | `.github/workflows/kubernetes.yaml` (`Rendered diff`), `.github/scripts/flate-diff-comment.py` (the body; github-workflows' `sticky-comment` posts it) | One sticky comment per PR (`<!-- flate-diff -->`, from `github-actions[bot]`) with `flate diff` against the base branch for `wsl` and `wsl-02`. Secrets aren't rendered. The review starts from it: what a chart bump actually changes in the manifests |

The OpenClaw pieces live outside this repo, on the WSL host. If they are
rebuilt, recreate the agent and the `hooks` block above, and put the same
token in the GitHub environment.

## When something goes wrong

- **Review job red.** The run didn't end `ok` (agent error, timeout, gateway
  restart mid-run, or `curl` couldn't reach `10.5.0.1:18789`). The PR is
  still eligible, so the next daily sweep retries it. To retry now: re-run the
  job, or `gh workflow run renovate-review.yaml -f pr=<n>`.
- **Nothing happened for an impact/high PR.** It needs `owner/review`, and
  Renovate's last push may have cleared it. Otherwise look at the Select log
  of the latest run: each PR prints `eligible` or `skipped, <why>`.
- **A PR you want reviewed again.** Remove `ori/blocked`.
- **An `impact/critical` PR you want merged.** Read Ori's "ready" comment,
  then add `owner/approved`. If Renovate or Ori pushed after that comment, the
  push cleared the approval and the PR gets a fresh review first.
- **`Owner label` red on a PR you labeled.** Its error line says why. If
  the label is older than the last push, or someone else added it last,
  remove the label and add it again. An API hiccup: re-run the job.
- **Gateway down.** Review jobs fail; nothing is lost. The daily sweep picks
  everything up once it's back.
