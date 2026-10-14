# peloton-to-garmin: pinned to a dead image, do not bump blindly

**Status:** known-broken, intentionally parked (2026-09-17).
**Symptom:** both instances fail every sync cycle with `403 Forbidden` (previously `429`) on `POST https://api.onepeloton.com/auth/login`.

Both `peloton-to-garmin-it` and `peloton-to-garmin-mh` in `home-automation`
are pinned to `ghcr.io/philosowaffle/peloton-to-garmin:v3.6.1`, which was
published **2023-11-11**. Peloton has since retired that authentication
endpoint entirely:

```
{"status":403,"message":"Access forbidden. Endpoint no longer accepting requests."}
```

The pods run, stay `1/1 Running`, log the failure, and sleep 7200s. Nothing
crashes, so this fails quietly. **No workouts have been syncing.**

Upstream tracking: [#798](https://github.com/philosowaffle/peloton-to-garmin/issues/798)
(the exact 403) and [#795](https://github.com/philosowaffle/peloton-to-garmin/issues/795)
(the auth-flow rewrite). Current upstream release is **v6.1.0** (2026-04-10).

## Why Renovate never flagged it

Upstream changed its tag scheme. Plain semver tags **stop at `v3.6.1`** —
only 9 plain `vX.Y.Z` tags were ever published, all `<= 3.6.1`. Everything
modern is prefixed by flavor:

| Flavor | Tag pattern | What it is |
| ------ | ----------- | ---------- |
| `console-*` | `console-v6.1.0`, `console-stable` | **headless sync — what we run** |
| `api-*` | `api-v6.1.0` | REST API backend |
| `webui-*` | `webui-v6.1.0` | web UI frontend |

Renovate compares against plain semver, finds nothing newer than `v3.6.1`,
and has reported the image as up to date for roughly three years. A green
Renovate dashboard is **not** evidence that a dependency is current when the
upstream tag scheme changes underneath it.

Any real fix therefore needs two things:

1. Repoint the image tag to `console-vX.Y.Z`.
2. Add a Renovate `packageRules` entry so it tracks the `console-` prefix
   going forward (otherwise it silently freezes again at whatever we pin).

## Why this is not a one-line bump

- `v4.0.0`, `v5.0.0` and `v6.0.0` each ship breaking changes with their own
  migration guides. `v5` fully removed `DeviceInfoPath` in favour of
  `DeviceInfoSettings`.
- `v6.1.0` added a Garmin **service ticket** auth path for when Cloudflare
  blocks the standard SSO login. Obtaining that ticket is a **manual,
  one-time, human step**, and the resulting DI OAuth2 token lasts only
  ~30 days. See the
  [service ticket docs](https://philosowaffle.github.io/peloton-to-garmin/authentication/garmin-service-ticket).
  That means the upgrade cannot be fully driven from git, and a token
  refresh will eventually be needed out-of-band.
- The credentials in `peloton-to-garmin-mh-secrets` have never successfully
  authenticated against Peloton (see below), so they are unverified.

Because of the manual token step, **do not** let Renovate automerge a major
bump here. Treat any version change as a hands-on migration.

## Related fix already landed

Until 2026-09-17 the `-mh` HelmRelease loaded `envFrom` from
`peloton-to-garmin-mh-secrets` but then overrode all four credential vars via
explicit `env` → `secretKeyRef` → **`peloton-to-garmin-it-secrets`**. Explicit
`env` beats `envFrom`, so `-mh` was a duplicate of `-it`: it logged into the
`it` Peloton account and uploaded to the `it` Garmin account. Two pods
authenticating as the same user on the same 7200s interval is also the likely
source of the earlier `429 Too Many Requests`.

Fixed in [#1344](https://github.com/iT3E/tnwks-ops/pull/1344). Consequence
worth remembering: the `-mh` credentials have never actually been exercised
end-to-end, so when the version upgrade happens, expect to validate Megan's
Peloton *and* Garmin logins from scratch.

## Options when picking this back up

1. **Upgrade to `console-v6.1.0`** — the real fix. Budget time for the v4→v5→v6
   migration guides plus manual Garmin service-ticket setup for both users.
2. **Retire the two deployments** — if the Peloton→Garmin sync is not worth the
   maintenance, delete the apps rather than leaving them failing on a loop.

Doing nothing is also fine, the failure is contained to these two pods, but it
should be a decision rather than an oversight. That is what this doc is for.
