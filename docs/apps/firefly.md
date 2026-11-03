# Firefly III

The household ledger: accounts, transactions, budgets and categories, at
`https://firefly.internal.tnwks.us` (LAN and admin VPN only,
`nginx-internal`, no public hostname). It is API-first: almost nothing is
entered through the UI. A bank-sync job writes transactions in through the
API, and the finance agent reads it through the API for reports.

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
| Database | `firefly` on the shared cnpg cluster, owned by its own `firefly` role (`databases/cloudnative-pg/cluster/database-firefly.yaml`, `managed.roles` in `cluster.yaml`), `PGSQL_SSL_MODE=require` |
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
  Firefly Service directly can send the header itself. Flannel does not
  enforce NetworkPolicy, so nothing in the cluster blocks that today.

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
POD=$(kubectl get pods -n databases -l 'cnpg.io/cluster=postgres,cnpg.io/instanceRole=primary' -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n databases "$POD" -c postgres -- psql -U postgres -d firefly -c 'select count(*) from transaction_journals'
```

Look the primary up by label each time; it moves on failover.

## Restore

The database is in the nightly cnpg barman-cloud backup with point-in-time
recovery (`docs/operations/backups.md`). The secret is in git. Attachments are
in kopiur. Account IDs are referenced by external mappings, so restore with
`pg_restore` (IDs and sequences preserved), never by exporting and
re-importing through the UI or CSV, which renumbers accounts.
