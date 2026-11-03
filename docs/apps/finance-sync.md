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

## State (Postgres database `finance_sync`)

Nothing here is in git or the image. Owner role `finance_sync`, login in
`finance-sync-secret` (`DATABASE_URL`). The image creates and migrates its own
schema (`schema_migrations`, advisory-locked) at the start of every run.
Restores are point-in-time through the cluster's WAL archiving.

| Table | Why it matters |
| --- | --- |
| `public.simplifi_token` | Rotating refresh token, committed right after each refresh so a crash can't lose it |
| `public.sync_state`, `public.sync_posted` | What has been posted. Losing it re-posts transactions as duplicates |
| `public.account_map`, `public.category_map` | Simplifi to Firefly mapping; contains people's account names |
| `amazon.*` (orders, items, transactions, matches, category_cache, sync_log, sequences) | Order, item and match history. Losing it means re-scraping Amazon |
| `amazon.rules` | Keyword to category rules built from household purchases |
| `amazon.session` | The amazon-orders library's cookies, written to `/tmp` for each run |

Credentials (Simplifi client, Firefly token, Amazon login and TOTP, database
login) are in `app/secrets.sops.yaml`.

### History: the old PVC

Until 2026-11-02 the state was files on the PVC `finance-sync-state`. It was
imported with `import-state` (one transaction; every table's row count and
content hash checked against the files) and the PVC is kept, unmounted and no
longer in the HelmRelease (`helm.sh/resource-policy: keep`), as the rollback
copy for a week. Rollback: suspend both CronJobs, run a Job with image 0.2.0
and `export-state --replace` with the PVC mounted read-write (it writes the
latest rotated token back), then revert to image 0.1.3 with the PVC mounted.

## Checks and alerts

- Each Job runs `selfcheck` first (init container): env present, state
  there (in Postgres, schema current), Firefly reachable with the token. It
  never calls Simplifi or Amazon.
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

Only while the CronJob itself isn't running (see Singleton). The host crontab entries were commented out at the cutover on 2026-11-02, and the host copies of the state are stale; don't go back to the host.
