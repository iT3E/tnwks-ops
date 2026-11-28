# peloton-to-garmin

Two headless instances in `home-automation`, `peloton-to-garmin-it` and
`peloton-to-garmin-mh` (`kubernetes/apps/home-automation/peloton-to-garmin/`),
one for each Peloton and Garmin account. Each polls Peloton every 7200 s,
converts the newest 8 workouts to FIT and uploads them to Garmin Connect.
Credentials are in each instance's `secrets.sops.yaml`.

**Status:** moved from `v3.6.1` to `console-v6.1.0` on 2026-11-28, fix
forward. `v3.6.1` had stopped syncing:
Peloton retired the login endpoint it used
([#798](https://github.com/philosowaffle/peloton-to-garmin/issues/798));
v6.0.0 has the new Peloton login
([#795](https://github.com/philosowaffle/peloton-to-garmin/issues/795)).

## Image

`ghcr.io/philosowaffle/peloton-to-garmin:console-v6.1.0`, by tag and digest.
Since v4.0.0 every tag carries its flavor:

| Flavor | Tag pattern | What it is |
| ------ | ----------- | ---------- |
| `console-*` | `console-v6.1.0`, `console-stable` | **headless sync: what we run** |
| `api-*` | `api-v6.1.0` | REST API backend |
| `webui-*` | `webui-v6.1.0` | web UI frontend |

The plain `vX.Y.Z` tags stop at `v3.6.1`, which was the console flavor.

## Renovate does not see it

Renovate's `docker` versioning reads the text before the first hyphen as the
version. `v3.6.1` parsed, but every newer tag starts with `console-`, `api-`
or `webui-`, which is no version, so Renovate found nothing newer than
`v3.6.1` and showed it as current. No rule in `.github/renovate.json5` or
`.github/renovate/` names this image or turns it off.

`console-v6.1.0` does not parse either, so Renovate now skips the image
entirely. To get updates again it needs a `packageRules` entry for
`ghcr.io/philosowaffle/peloton-to-garmin` with regex versioning on the
`console-v` prefix, for example
`"versioning": "regex:^console-v(?<major>\\d+)\\.(?<minor>\\d+)\\.(?<patch>\\d+)$"`.
Don't let it automerge a major: v4, v5 and v6 each had their own migration
guide.

## What v4 to v6 changed here

From the release notes and the migration guides (v3 to v4, v4 to v5, v5 to v6):

- **v4.0.0:** `Garmin.UploadStrategy` is gone (native upload only), so the
  env var is gone. The API and web UI flavors bind port 8080; the console
  serves no HTTP. The Service lists the one listener, the Prometheus
  endpoint on 4000.
- **v4.3.0:** P2G keeps and refreshes its Garmin tokens in its data
  directory, so `/app/data` (the `*Db.json` files) is on the PVC. Nothing
  read the old `/config` mount; the volume kept its name, so the PVC stayed.
- **v5.0.0:** .NET 9, and the image runs as `p2g` (uid and gid 1015).
  local-path makes each volume directory `0777`, so the PVC is writable.
  `DeviceInfoPath` became `DeviceInfoSettings`; we set neither.
- **v6.0.0:** the new Peloton login (Auth0, email and password, as before).
  `SessionId` is gone; we never set it. Some users had to delete `data/`
  before the new login worked: deleting the PVC's files resets P2G.
- Env vars keep the `P2G_SECTION__PROPERTY` form, and they override the
  `configuration.local.json` that the image ships.

## Garmin login

P2G signs in to Garmin SSO with the email and password. When Cloudflare
blocks that, v6.1.0 has a fallback: a one-time **service ticket** from a
browser login, which P2G exchanges for a token of about 30 days
([docs](https://philosowaffle.github.io/peloton-to-garmin/authentication/garmin-service-ticket)).
The ticket can expire within a minute, so it can't go through git and Flux
(a `P2G_GARMIN__SERVICETICKET` key in the Secret would arrive too late, and
it stays in the env after it is used). If the SSO login fails, the way is
the `webui-v6.1.0` flavor, where the ticket is pasted into the settings
page, with the same data directory. Garmin accounts with two-step
verification need the same.

## The `-mh` credentials

Until 2026-09-17 the `-mh` HelmRelease read `envFrom` from
`peloton-to-garmin-mh-secrets` but then overrode all four credential vars via
explicit `env` → `secretKeyRef` → **`peloton-to-garmin-it-secrets`**. Explicit
`env` beats `envFrom`, so `-mh` was a duplicate of `-it`: it logged into the
`it` Peloton account and uploaded to the `it` Garmin account.

Fixed in [#1344](https://github.com/iT3E/tnwks-ops/pull/1344). So the `-mh`
credentials have never been used end to end: expect to check the `-mh`
account's Peloton *and* Garmin logins from scratch.

## Checking it

The pods have no probes (the console serves no health route). Read the logs
of each pod after a start: a Peloton login, the workouts it found, and a
Garmin upload or the reason it failed. With
`P2G_OBSERVABILITY__PROMETHEUS__ENABLED`, `:4000/metrics` answers on the
Service (no ServiceMonitor scrapes it).

## Retiring it

If the sync is not worth keeping, delete the two apps (the `ks.yaml` entry in
`kubernetes/apps/home-automation/kustomization.yaml` and the cluster overlay)
rather than leave them failing on a loop.
