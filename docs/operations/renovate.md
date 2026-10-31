# Renovate: what merges itself and what Ori reviews

Every Renovate PR lands in one of three tiers, decided by
[`.github/renovate/autoMerge.json5`](../../.github/renovate/autoMerge.json5):

- **Automerge.** Leaf app bumps. Renovate merges them itself once every
  status check is green, including the required **Build Success**
  ([`kubernetes.yaml`](../../.github/workflows/kubernetes.yaml): flate render
  plus "every new image exists"). If a merged bump then fails to reconcile,
  Flux error events page the War Room (`flux-system/addons/alerts`).
- **`impact/high`.** Cluster plumbing, Terraform, majors, and anything new.
  Never automerged. These go to Ori's reviewer agent, whose job is to land
  them: it reads the release notes, pushes whatever migration the bump needs
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
  `ori/awaiting-approval`. **Ivan approves by adding `ivan/approved`**, and
  the agent merges that exact sha. The agent also adds `impact/critical`
  itself when a review turns up something one-way (a schema or data
  migration with no downgrade, a CRD or API removal, data that gets wiped, a
  Terraform plan that changes real resources). Only Ivan removes it.

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

The agent pushes and merges as Ivan's GitHub account, so `ivan/approved` is a
rule the workflow and the agent follow, not something GitHub enforces.
Moving Ori onto its own GitHub App identity would make it enforceable with a
ruleset.

## How an impact/high PR reaches Ori

```
Renovate opens PR, labels impact/high
  -> renovate-review.yaml  "Select" (GitHub-hosted)
       eligible? open, renovate[bot], this repo, impact/high,
                 no ori/blocked, Build Success = success on the head sha
  -> renovate-review.yaml  "Review #<n>" (runs-on: openclaw-hooks)
       POST http://10.5.0.1:18789/hooks/agent   (OpenClaw gateway, WSL host)
         {"repo","pr","head_sha"}, agentId renovate, waitForCompletion
       job fails unless the run ended ok
  -> OpenClaw agent "renovate"
       re-checks the same gates, reviews (homelab-dependency-updates skill)
       migrates on the branch (new push = new review), merges
       (--match-head-commit) and verifies, or comments + labels ori/blocked
       impact/critical: stops at ori/awaiting-approval until ivan/approved
       its final reply goes to Ivan on Telegram
```

Triggers, all in [`renovate-review.yaml`](../../.github/workflows/renovate-review.yaml):

| Event | When it fires |
| --- | --- |
| `pull_request_target: labeled` (impact/high) | Renovate labels the PR. Usually CI hasn't finished yet, so Select skips it |
| `workflow_run: Kubernetes` completed | Build Success finished on a PR. This is normally what sends it |
| `pull_request_target: synchronize` | A new push (Renovate's, or the agent's migration commit). Clears `ori/blocked`, `ori/awaiting-approval` and `ivan/approved`, so each new head gets its own review |
| `pull_request_target: unlabeled` (ori/blocked) | Ivan removed the label to ask for another review |
| `pull_request_target: labeled` (ivan/approved) | Ivan approved an `impact/critical` PR; the agent merges the sha it named |
| `schedule` 16:00 UTC | Daily sweep: re-sends anything still eligible (a failed review, a missed event) |
| `workflow_dispatch` | Manual, one PR or all |

State lives on the PR: a merged PR is closed, a blocked one carries
`ori/blocked` and a comment saying what blocks it, and a critical one waiting
on Ivan carries `ori/awaiting-approval` and the "ready" comment. Nothing is
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
| Label `ori/blocked` | `.github/labels.yaml` | Set by the agent, cleared by a new push or by hand |
| Labels `impact/critical`, `ori/awaiting-approval`, `ivan/approved` | `.github/labels.yaml` | Critical tier: Renovate or the agent sets the first, the agent sets the second, only Ivan sets the third. A new push clears the last two |
| OpenClaw hook | `~/.openclaw/openclaw.json` on the WSL host, `hooks.*` | `allowedAgentIds: ["renovate"]`, no caller session keys. Token copy in `~/.openclaw/credentials/openclaw-hooks.env` |
| OpenClaw agent `renovate` | `agents.entries.renovate`, workspace `~/.openclaw/workspace-renovate` | Tools: `exec`, `process`, `read` only. Its `AGENTS.md` is the review contract |

The OpenClaw pieces live outside this repo, on the WSL host. If they are
rebuilt, recreate the agent and the `hooks` block above, and put the same
token in the GitHub environment.

## When something goes wrong

- **Review job red.** The run didn't end `ok` (agent error, timeout, gateway
  restart mid-run, or `curl` couldn't reach `10.5.0.1:18789`). The PR is
  still eligible, so the next daily sweep retries it. To retry now: re-run the
  job, or `gh workflow run renovate-review.yaml -f pr=<n>`.
- **Nothing happened for an impact/high PR.** Look at the Select log of the
  latest run: each PR prints `eligible` or `skipped, <why>`.
- **A PR you want reviewed again.** Remove `ori/blocked`.
- **An `impact/critical` PR you want merged.** Read Ori's "ready" comment,
  then add `ivan/approved`. If Renovate or Ori pushed after that comment, the
  push cleared the approval and the PR gets a fresh review first.
- **Gateway down.** Review jobs fail; nothing is lost. The daily sweep picks
  everything up once it's back.
