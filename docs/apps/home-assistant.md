# Home Assistant

Home Assistant runs on `homelab-wsl` from
`kubernetes/apps/home-automation/home-assistant/`. Its recorder writes to the
shared CNPG cluster (database `home_assistant`).

## Access

There are two entry points, laid out like Jellyfin's. Neither has an
oauth2-proxy gate:

| URL | Path | Reachable from |
|---|---|---|
| `https://hass.tnwks.us` | Cloudflare → tunnel → public ingress-nginx | anywhere |
| `https://hass.internal.tnwks.us` | MikroTik's `*.internal.tnwks.us` record → Windows portproxy → nginx-internal | the LAN |

The two hostnames are distinct, so there's no split-horizon DNS.
`hass.tnwks.us` is public DNS (external-dns → Cloudflare), resolved for the LAN
by the MikroTik.

**New public hostnames and the MikroTik's cache:** the MikroTik caches "no such
name" answers for 30 minutes (the zone's SOA minimum), and Force-DNS redirects
mean everything on the LAN goes through that cache. If a hostname is looked up
in the moments before external-dns creates it, it stays unresolvable on the
LAN for up to half an hour. Run `/ip dns cache flush` on the router to clear
it.

HA authenticates users itself, with OIDC against the `tnwks-auth` Cognito pool
(hass-oidc-auth). oauth2-proxy can't front it: the Companion app's native API,
websocket and webhook calls never carry an oauth2-proxy session, and
exempting those paths would leave HA's API public anyway.

What follows from being internet-facing:

- **The break-glass local login is public.** The owner account (the HA login
  linked from the welcome screen) can be reached from anywhere, so it gets a
  long password and TOTP MFA (setup step 4).
- **No automatic IP bans.** HA's default enables banning but sets *Login
  attempts before ban* to unlimited, so nothing is ever banned. Keep it that
  way. Public clients now arrive with their real IP: the
  public ingress-nginx trusts cloudflared's `X-Forwarded-For` from the pod
  network. But every LAN client arrives as the Windows `netsh portproxy`, so a
  ban triggered from the LAN would lock out the whole LAN. Cognito handles the
  main login's brute-force protection.

## What's in git and what isn't

HA can't be fully declared in YAML. Since
[ADR-0010](https://github.com/home-assistant/architecture/blob/master/adr/0010-integration-configuration.md),
any integration that talks to a device or service is UI-only. Since 2026.8 the
HTTP server settings are too. So ownership is split:

| Git (`app/config/`, read-only in the UI)                                    | UI (`/config/.storage` on the PVC)                                     |
| --------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Core settings: name, location, units, time zone, URLs                       | Integrations: MQTT, Frigate, Telegram bot, Z-Wave JS, …                |
| Recorder, SSO (`auth_oidc`), dashboard resources                            | HTTP server: proxy trust, IP bans                                      |
| `packages/`: automations, helpers, templates                                | Entity / device / area / floor / label registries                      |
| `dashboards/`: YAML-mode dashboards (Security)                              | Users, the Overview dashboard, energy settings                         |
| Custom integration and card versions (`helmrelease.yaml`)                   | `automations.yaml`, `scripts.yaml`, `scenes.yaml` (the UI editors)     |

Back up the UI half with HA's own backups (Settings > System > Backups). Git
can't rebuild it.

Room and device tiles come from HA's built-in Overview dashboard, which is
built from area assignments. To get them, put devices in areas. They don't need
YAML.

## Layout

```
app/
├── helmrelease.yaml           # image, versions, env, mounts
├── kustomization.yaml         # one ConfigMap per directory below
├── secrets.sops.yaml          # HASS_LATITUDE / LONGITUDE / ELEVATION
├── config/
│   ├── configuration.yaml     # → /config/configuration.yaml
│   ├── packages/*.yaml        # → /config/packages/
│   ├── dashboards/*.yaml      # → /config/dashboards/
│   └── www/tnwks-kitchen/     # → /config/www/tnwks-kitchen/ (our own cards, /local/tnwks-kitchen/)
└── scripts/prepare-config.sh  # init container: installs custom integrations + cards
```

Flux variable substitution is off for these ConfigMaps, so Jinja is never
mangled. Per-cluster values reach HA as container env vars, which the config
reads with `!env_var`.

## Making a change

1. Edit or add a file under `config/`. A **new** file must also be listed under
   `configMapGenerator` in `kustomization.yaml` (CI checks this).
2. Open a PR. The `Home Assistant` workflow runs HA's `check_config` against
   the pinned image and integration versions.
3. After merge, Flux updates the ConfigMap and reloader restarts the pod.

A PR that only edits existing files under `config/` doesn't need a Changie
fragment, because automation and dashboard tuning doesn't cut a release. A PR
that touches anything else (including a new config file, since that edits
`kustomization.yaml`) needs one as usual.

Renovate bumps the image and the pinned add-ons (`hass-oidc-auth`, the
Frigate integration, the Advanced Camera Card, kiosk-mode). The same check runs on those
PRs, which catches HA's frequent renames of trigger and condition keys.

## First-time setup

**Neither URL works until step 3 is done.** On a fresh `/config` volume HA
answers every request that comes through ingress-nginx with `400 Bad Request`.
Proxy trust is UI-only, so the first steps go through a port-forward. These
steps are needed once per fresh volume, in this order.

```bash
export KUBECONFIG=~/.kube/config-homelab-wsl
kubectl -n home-automation port-forward svc/home-assistant 8123:8123
```

WSL forwards localhost, so a Windows browser can use `http://localhost:8123`.

1. **Onboarding.** Open `http://localhost:8123` and create the owner account.
   This is the local break-glass admin, so keep its password in 1Password.
   Location and units are YAML-owned and show as read-only.
2. **Over the port-forward, use the local login.** If HA asks you to sign in
   again, the welcome screen offers *Login with tnwks* and a link to the HA
   login. Use the HA login: SSO only works on the real hostnames, because
   Cognito has no `localhost` callback.
3. **Proxy trust.** Settings > System > Network > HTTP server: turn on
   *Trust X-Forwarded-For* and set *Trusted proxies* to the pod CIDR
   (`10.42.0.0/16`, the cluster's `CLUSTER_CIDR`). Both ingress-nginx
   controllers run in the pod network. Leave *Login attempts before ban* at
   unlimited (see [Access](#access)) and save. HA restarts with the new settings as a
   trial, and the trial already trusts the proxy. Open
   `https://hass.internal.tnwks.us` and **confirm the settings within 5
   minutes**, or HA reverts them. After that, both URLs work.
4. **MFA on the owner account.** Profile > Security > Multi-factor
   authentication modules: enable *Authenticator app* (TOTP). The owner login is
   internet-facing, so do this before going further.
5. **SSO.** Sign in at `https://hass.tnwks.us` with *Login with tnwks*. Cognito
   group `admins` maps to HA administrator and `viewers` to HA user. Anyone in
   neither group is rejected. HA creates the user and a person on first login.
6. **MQTT.** Settings > Devices & services > Add integration > MQTT. Use broker
   `mosquitto.home-automation.svc.cluster.local`, port `1883`, username
   `home-assistant`. The password is on the `home-assistant:` line of
   `sops -d kubernetes/apps/home-automation/mosquitto/app/secrets.sops.yaml`.
   Then reload automations (Tools > YAML > Automations). The night alert's
   MQTT trigger can't subscribe until the broker exists, so it needs the
   reload.
7. **Frigate.** Add integration > Frigate, URL
   `http://frigate.production.svc.cluster.local:5000`. This creates the
   `camera.frigate_*` entities the Security dashboard uses. Then open the
   integration's *Configure* and set the unauthenticated notification expiry to
   `86400` (one day). The phone loads alert snapshots through HA without a
   token. That's the integration's notification proxy, which by default serves
   any event's snapshot and clip unauthenticated forever, and HA is public.
8. **Phones.** On each phone that should get alerts:
   - Install the Home Assistant Companion app and connect it to
     `https://hass.tnwks.us`. With SSO the app shows a code to enter in the
     phone's browser. This is hass-oidc-auth's device-code login. Registering
     the app is all the alert needs, since it sends to every registered phone.
   - **LAN URL:** in the app, Settings > Companion app > (server) set *Internal
     URL* to `https://hass.internal.tnwks.us` and add the home Wi-Fi network(s).
     The app needs location permission to read the network name. On those
     networks the app goes direct to nginx-internal; everywhere else it uses
     the public URL.
   - So critical alerts can break through Do Not Disturb:
     - **iOS:** allow *Critical Alerts* when the app asks, or later under
       Settings > Notifications > Home Assistant > Critical Alerts.
     - **Android:** make sure Do Not Disturb lets *Alarms* through (the
       default), since critical alerts ring on the alarm stream.
9. **Backups.** Settings > System > Backups: turn on automatic backups.

Optional: add Z-Wave JS with URL
`ws://zwave-js-ui.home-automation.svc.cluster.local:3000`, then assign devices
to areas so the Overview dashboard fills in.

## Night person alerts

`config/packages/frigate_night_alerts.yaml` sends one Companion app push per
Frigate review, the first time the review becomes a person alert. The push has
a snapshot and an *Open in Frigate* button, and tapping it opens the camera
grid. It fires for the cameras in `watched_cameras`, which is all of them,
since every camera is exterior. It only fires between 23:00 and 06:00, and
only while *Night person alerts* is on. Each alert also leaves an HA
notification, one per camera.

There are two tiers. Right now every watched camera is critical
(`critical_cameras: "{{ watched_cameras }}"`). To tier them, give
`critical_cameras` an explicit list, and the cameras left out send normal
pushes. Turning *Critical night alerts* off drops everything to normal.

| Tier | iOS | Android |
|---|---|---|
| Normal | time-sensitive: gets through Focus, not DND or mute | high priority, channel *Security alerts* |
| Critical | critical alert: overrides DND and mute | alarm stream: rings through silent and vibrate, and through DND when alarms are allowed |

The push goes through HA's mobile push relay (Apple/Google). The phone loads
the snapshot from HA itself, through whichever URL the app is using, and the
Frigate integration's notification proxy serves it.

You can change the switches and the times on the Security dashboard's Alerts
view, but only until the next restart. The YAML `initial` values win, so make
lasting changes in the package. Both switches default to on deliberately: a
restart should never quietly leave alerting off.

To test it, move the start and end times on the Alerts view so the window
covers now, then walk past a camera. Restarting HA restores the real
window.

## Kitchen display

The Apolosign display in the kitchen (2160x3840 at 480 dpi, so a 720x1280 CSS
viewport) runs the **Kitchen** dashboard, `config/dashboards/kitchen.yaml`.
Its cards are ours, in `config/www/tnwks-kitchen/kitchen-cards.js`: plain
custom elements, no build step. HA caches `/local` for a long time, so bump
the `?v=` on its resource in `configuration.yaml` with every change to it.

Chores live in `config/packages/kitchen_routines.yaml`: one `input_boolean`
per chore, with `customize` attributes for who, when (period, days) and how
many stars. Checking one off adds its stars to `counter.stars_<member>`;
unchecking it by hand takes them back. Everything is unchecked at midnight,
and stars stay. A reward goal per kid is two helpers (`input_text` +
`input_number`) you can set from the UI; the Routines view shows progress
toward it.

Members have neutral keys (`adult1`, `adult2`, `kid1`, `kid2`) in every
entity ID and attribute, because the repo is public. Display names live in
`secrets.sops.yaml` (`KITCHEN_<KEY>_NAME`) and become the friendly names of
the `counter.stars_<key>` helpers through `!env_var`. The cards read a
person's name from that counter and match calendar events on it, so a new
person needs a key in `FAMILY` (kitchen-cards.js), a counter and an env var.

kiosk-mode hides HA's header and sidebar on a device once the dashboard has
been opened there with `?kiosk` (it remembers). Open it with `?clear_km_cache`
to undo that.

Calendars, the meal plan and extra to-do lists are integrations, so they're
UI-owned. The dashboard expects `calendar.family` (the family Google
calendar) and `calendar.mealie_dinner` (Mealie), and shows a "not connected"
placeholder until they exist.

## Break-glass

- **SSO broken:** on the login page's welcome screen, follow the link to the
  HA login (or go straight to `/?skip_oidc_redirect=true`) and use the owner
  account with its TOTP code.
- **Proxy settings broken** (UI unreachable through ingress): use the
  port-forward above and fix them under Settings > System > Network.
