# Firefly III

The household ledger: accounts, transactions, budgets and categories, at
`https://firefly.internal.tnwks.us` (LAN and admin VPN only,
`nginx-internal`, no public hostname). It is API-first: almost nothing is
entered through the UI. A bank-sync job writes transactions in through the
API, and the finance agent reads and writes it through the API, never with a
delete: in the cluster through its MCP server and a proxy that holds its
token and lets through an allow list of calls
([agent-platform.md](agent-platform.md#the-finance-app)).

Manifests: `kubernetes/apps/production/firefly/`. WSL-only (wired from
`kubernetes/clusters/wsl/apps/production/kustomization.yaml`), image
`fireflyiii/core` in `app/helmrelease.yaml`. Upstream:
[docs.firefly-iii.org](https://docs.firefly-iii.org),
[GitHub](https://github.com/firefly-iii/firefly-iii) (AGPL-3.0).

Moved here from a Docker Compose stack on the WSL host on 2026-11-02.

## Pieces

| Piece | Where |
| --- | --- |
| App | Deployment `firefly`, port 8080, probes on `/health` |
| Database | `firefly` on its own CNPG cluster `databases/firefly-pg` (`kubernetes/components/cnpg-cluster`, a replica on homelab-wsl-02), owned by the `firefly` role (`db/secret-firefly-role.sops.yaml`), `PGSQL_SSL_MODE=require` |
| Secrets | `app/secrets.sops.yaml` (`firefly-secret`): `APP_KEY`, `STATIC_CRON_TOKEN`, `DB_USERNAME`/`DB_PASSWORD`, `oauth-private.key`, `oauth-public.key` |
| Daily jobs | CronJob `firefly-firefly-cron`, 03:00 Pacific (`timeZone` set; it was 03:00 UTC until 2026-11-02), calls `/api/v1/cron/<token>` (recurring transactions, auto-budgets, bill warnings) |
| Attachments | PVC `firefly-upload`, backed up by kopiur |

## Login

Web login is tnwks-auth (Cognito) through oauth2-proxy, members of `admins`
only, the same pattern as Grafana. Firefly has no OIDC client; its
`remote_user_guard` trusts the `X-Auth-Request-Email` header that
ingress-nginx sets from oauth2-proxy, and matches it to the Firefly user's
email. An email it doesn't know is created as a new, empty user, which is why
the ingress filters on the group. The owner account's email is the Cognito
login's email.

- `/api` is a separate Ingress (`firefly-api`) without oauth2-proxy, because
  API clients use personal access tokens. It clears the `X-Auth-Request-*`
  headers, and the API guard ignores them anyway.
- Logout goes to Cognito's `/logout`, which ends the SSO session and clears
  the oauth2-proxy cookie.
- A 403 right after a change to oauth2-proxy's groups claim means the session
  predates it: sign out at `https://oauth2.internal.tnwks.us/oauth2/sign_out`
  and in again.
- Break-glass: set `AUTHENTICATION_GUARD` back to `web` for password login.
- Residual risk, shared with Grafana's auth proxy: a pod that talks to the
  Firefly Service directly can send the header itself. Cilium enforces
  NetworkPolicy, but no policy restricts Firefly's ingress yet.

## The Passport keypair: read this before touching the secret

Firefly's API tokens (personal access tokens) are RS256 JWTs signed by
`storage/oauth-private.key` and checked against `storage/oauth-public.key`
(Laravel Passport). The tokens' rows live in the database, but a token is only
accepted if its signature verifies, so **a correct database with a different
keypair rejects every existing token with a 401** while the web UI (session
login) keeps working.

The image generates a new pair on boot when the files are missing. On the old
Compose stack they existed only in the container's writable layer, so any
container recreate would have silently broken every API client. Here they are
mounted from the SOPS secret (read-only, `0440`, group `www-data` through
`fsGroup: 33`). The image's entrypoint runs `chmod 600` on them, which fails
on the read-only mount and is harmless: the entrypoint has no `errexit`.

Consequences:

- Never delete `oauth-*.key` from the secret to "reset" something. Every API
  client breaks.
- Rotating the keypair is a planned change: generate a new pair, update the
  secret, then reissue every personal access token and update its clients.
- Checking a backup of the pair: plain `sha256sum` of two public-key files can
  differ for the same key because of PEM formatting. Compare
  `openssl rsa -pubin -in pub -pubout | sha256sum` with
  `openssl rsa -in priv -pubout | sha256sum`.

## Image version

Pinned (`version-<x.y.z>`), never `latest`. Firefly runs its schema
migrations on boot, so an image bump is a database migration: bump it in its
own PR, never together with an infrastructure change.

## Clients

All API clients authenticate with a personal access token and use
`https://firefly.internal.tnwks.us`. The internal name resolves through the
router's `internal.tnwks.us` wildcard and the `*.internal.tnwks.us`
certificate, so clients on the WSL host need no DNS changes.

## Looking at the data directly

Read-only questions are best answered through the API. When the API seems
wrong:

```bash
POD=$(kubectl get pods -n databases -l 'cnpg.io/cluster=firefly-pg,cnpg.io/instanceRole=primary' -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n databases "$POD" -c postgres -- env PGOPTIONS='-c default_transaction_read_only=on' \
  psql -U postgres -d firefly -c 'select count(*) from transaction_journals'
```

Look the primary up by label each time; it moves on failover.

## Restore

The database is in the nightly cnpg barman-cloud backup with point-in-time
recovery (`docs/operations/backups.md`). The secret is in git. Attachments are
in kopiur. Account IDs are referenced by external mappings, so restore with
`pg_restore` (IDs and sequences preserved), never by exporting and
re-importing through the UI or CSV, which renumbers accounts.

### Restore the database

`firefly-pg` archives its WAL all the time and takes a base backup at 01:30
UTC each day, with 30 days of retention (`kubernetes/components/cnpg-cluster`).
So it can go back to any second in those 30 days. A recovery always makes a
new Cluster; it never changes a running one (CloudNativePG 1.30, docs
"Recovery").

**One record or a few, the usual case** (a wrong write through the API):
keep the live database, and read the old values from a copy.

1. Note the time just before the wrong write, in UTC.
2. Bring back `kubernetes/apps/production/firefly/restore-test/` from git
   history: the cleanup deletes it after each test. Take the last version,
   from the parent of the commit that removed it
   (`C=$(git log -1 --format=%H --diff-filter=D -- kubernetes/apps/production/firefly/restore-test/ks.yaml)`,
   then `git checkout "$C^" -- kubernetes/apps/production/firefly/restore-test`),
   and uncomment its wiring line in
   `kubernetes/clusters/wsl/apps/production/kustomization.yaml` (the cleanup
   keeps it as a comment). In `restore/cluster.yaml`, set
   `bootstrap.recovery.recoveryTarget.targetTime` to that time (docs
   "Recovery", "PITR from an object store" and "Recovery targets"), give the
   check Job a new name, and merge. Flux builds
   `databases/firefly-pg-restore-test` at that time.
3. Read the old values from the copy, read-only (`kubectl exec` into
   `firefly-pg-restore-test-1`, as in "Looking at the data directly"), and
   write them back through the API, so the IDs stay.
4. Comment out the wiring line again, remove the directory, and merge.
   Flux deletes the copy, its volume and the Job.

**The whole database** (it is lost, or wrong from a point on): stop the app,
`finance-sync` and every client that writes, then follow
`kubernetes/components/cnpg-cluster/README.md`, "Restore": a patch in
`db/kustomization.yaml` that replaces `bootstrap.initdb` with
`bootstrap.recovery` (with `recoveryTarget` for a point in time), a new
`CNPG_SERVER_NAME` generation, then delete the Cluster and its volume and
merge. Every write after the target is lost. The replica on homelab-wsl-02
follows the old archive folder, so it must be built again from the new one
(`docs/wsl/multi-site.md`). This path has not been run for `firefly-pg`.

**The restore test** (`kubernetes/apps/production/firefly/restore-test/`,
wired on homelab-wsl only, removed after each run):

- A Cluster `firefly-pg-restore-test` in `databases` recovers from the
  archive folder `homelab-wsl` of the ObjectStore `firefly-pg` to the latest
  WAL. It has no `plugins` section, so it never archives WAL or takes a
  backup, and nothing writes to the archive of `firefly-pg`. Its Services
  have new names that no app uses. A NetworkPolicy lets in only the check
  Job (5432) and the operator (8000).
- Then the Job `firefly-restore-check` logs only counts and times: tables,
  migrations, rows of `transactions`, `transaction_journals` and `accounts`,
  and the newest `created_at` of a journal. It fails on a copy that is not
  promoted, has fewer than 59 migrations (the files of Firefly 6.5.9), or has
  one of the three tables missing or empty. Loki:
  `{namespace="databases", app="firefly-restore-check"}`.
- It doesn't read `firefly-pg`: no read path exists without a new
  credential. To compare, run the same counts on the live database
  ("Looking at the data directly"). Each live count must be the same or
  larger (rows written since the copy's last WAL), and the newest journal
  close to the copy's.
- How long: a base backup of about 50 MB (the database is about 36 MB) and
  the WAL of less than a day. The 2026-11-02 drill recovered nine databases
  in 80 seconds, so expect a few minutes, most of it the start of the pod.

What a passing run proves: the archive of `firefly-pg` on homelab-wsl holds a
base backup and WAL that the operator recovers into a working database with
a complete schema and rows in its main tables. What it does not prove: that
the copy equals the live database row for row, a recovery to a point in time
(a second run with `targetTime`), the app on the restored data, a recovery
on homelab-wsl-02, or a recovery after the whole cluster is lost (the
operator, the backup plugin and the backup credentials must be back first).
