# finance-sync

The two scheduled jobs that feed [Firefly III](firefly.md), run as CronJobs in
`production` since 2026-11 (they were host crontab entries on the WSL box):

| CronJob | When (Pacific) | What |
| --- | --- | --- |
| `finance-sync-simplifi` | daily 06:00 | Pulls new bank transactions from Simplifi into Firefly, then reconciles every account's balance against Simplifi and corrects drift |
| `finance-sync-amazon` | Mondays 07:00 | Scrapes Amazon order history and enriches the matching Firefly transactions with items and categories |

Manifests: `kubernetes/apps/production/finance-sync/`. Code and image:
[`iT3E/tnwks-finance-sync`](https://github.com/iT3E/tnwks-finance-sync) (private
repo), image `ghcr.io/it3e/tnwks-finance-sync` (public, and must stay free of
data: its CI fails the build if a state file is in the image). WSL-only.

## Singleton: never run two copies

Simplifi rotates its refresh token on every login and the sync writes the new
one back. Two copies (the old host cron, a second cluster, a manual run while
the CronJob runs) would each invalidate the other's token. Hence
`concurrencyPolicy: Forbid`, no entry in the shared `apps/production` list,
and the host crontab entries removed at the cutover. `backoffLimit: 0` for a
related reason: the sync records what it posted only when it finishes, so an
automatic retry of a failed run could post transactions twice.

## State (PVC `finance-sync-state`, mounted at `/state`)

Nothing here is in git or the image. kopiur backs it up nightly.

| File | Written by | Why it matters |
| --- | --- | --- |
| `simplifi_token.json` | sync, every run | Rotating refresh token. A stale copy cannot log in |
| `sync_state.json` | sync | What has been posted. Losing it re-posts transactions as duplicates |
| `account_map.json`, `category_map.json` | sync | Simplifi to Firefly account and category mapping; contains people's account names |
| `amazon_orders.db` | amazon | Order, item and match history. Losing it means re-scraping Amazon |
| `amazon_rules.json` | by hand | Keyword to category rules built from household purchases |
| `amazonorders/` | amazon | The amazon-orders library's session (cookies) |

Credentials (Simplifi client, Firefly token, Amazon login and TOTP) are in
`app/secrets.sops.yaml`.

## Checks and alerts

- Each Job runs `selfcheck` first (init container): env present, state files
  there, `/state` writable, Firefly reachable with the token. It never calls
  Simplifi or Amazon.
- A failed Job fires the cluster-wide `KubeJobFailed`. The image exits
  non-zero on real failures (the exit-code table is in its README).
- `FinanceSyncStale` (no successful sync in 26 h), `FinanceAmazonEnrichStale`
  (8 days) and `FinanceSyncNeverSucceeded` in `app/prometheusrule.yaml`.
- The sync pushes three alerts to Alertmanager itself (pod logs are not
  shipped to Loki on this cluster, and the Jobs keep only the last 3 runs'
  logs, so nothing can be grepped later). Labels and summaries carry no
  account or institution names; those stay in the Job log.
  - `FinanceReconciliationDrift`: a balance was off by $100 or more and was
    corrected.
  - `FinanceBankConnectionBroken`: one per broken bank connection in Simplifi
    (a feed that died quietly), keyed by a stable `connection` label (a hash
    of the Simplifi account id). It stays firing while broken and reminds once
    a day (Alertmanager route). To mute a connection you already know about:
    `amtool silence add alertname=FinanceBankConnectionBroken connection=<id>`
    or the Alertmanager UI.
  - `FinanceRefreshTimeout`: the wait for bank refreshes timed out. With
    "already fresh" institutions in the same run it's the signature of an old
    bug (waiting on institutions that were already fresh).
- Logs: `kubectl -n production logs job/<job>` for the last 3 runs. Anything
  that needs a longer history should be an alert, not a grep.

## Running one by hand

```bash
kubectl -n production create job --from=cronjob/finance-sync-simplifi sync-manual-$(date +%s)
```

Only while the CronJob itself isn't running (see Singleton). The host crontab entries were commented out at the cutover on 2026-11-02; to roll back, suspend both CronJobs, copy `simplifi_token.json`, `sync_state.json`, the two maps and `amazon_orders.db` from the PVC back to the host, and uncomment them.
