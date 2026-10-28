# Renovate: what merges itself and what Ori reviews

Every Renovate PR lands in one of two tiers, decided by
[`.github/renovate/autoMerge.json5`](../../.github/renovate/autoMerge.json5):

- **Automerge.** Leaf app bumps. Renovate merges them itself once every
  status check is green, including the required **Build Success**
  ([`kubernetes.yaml`](../../.github/workflows/kubernetes.yaml): flate render
  plus "every new image exists"). If a merged bump then fails to reconcile,
  Flux error events page the War Room (`flux-system/addons/alerts`).
- **`impact/high`.** Cluster plumbing, Terraform, majors, and anything new.
  Never automerged. These go to Ori's reviewer agent, which merges or leaves
  the PR open with a reason.

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
       merges (--match-head-commit), or comments + labels ori/blocked
       its final reply goes to Ivan on Telegram
```

Triggers, all in [`renovate-review.yaml`](../../.github/workflows/renovate-review.yaml):

| Event | When it fires |
| --- | --- |
| `pull_request_target: labeled` (impact/high) | Renovate labels the PR. Usually CI hasn't finished yet, so Select skips it |
| `workflow_run: Kubernetes` completed | Build Success finished on a PR. This is normally what sends it |
| `pull_request_target: synchronize` | A new push. Clears `ori/blocked`, so each new version gets its own review |
| `pull_request_target: unlabeled` (ori/blocked) | Ivan removed the label to ask for another review |
| `schedule` 16:00 UTC | Daily sweep: re-sends anything still eligible (a failed review, a missed event) |
| `workflow_dispatch` | Manual, one PR or all |

State lives on the PR: a merged PR is closed, a blocked one carries
`ori/blocked` and a comment saying what blocks it. Nothing is remembered
anywhere else.

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
- **Gateway down.** Review jobs fail; nothing is lost. The daily sweep picks
  everything up once it's back.
