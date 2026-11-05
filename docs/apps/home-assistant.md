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
`hass.tnwks.us` is public DNS (Cloudflare, in Terraform:
`infrastructure/terraform/environments/prod/cloudflare/public-dns.tf`), resolved for the LAN
by the MikroTik.

**New public hostnames and the MikroTik's cache:** the MikroTik caches "no such
name" answers for 30 minutes (the zone's SOA minimum), and Force-DNS redirects
mean everything on the LAN goes through that cache. If a hostname is looked up
in the moments before Terraform creates it, it stays unresolvable on the
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
│   ├── www/tnwks-kitchen/     # → /config/www/tnwks-kitchen/ (our own cards, /local/tnwks-kitchen/)
│   └── www/tnwks-security/    # → /config/www/tnwks-security/ (the alert schedule card)
└── scripts/prepare-config.sh  # init container: installs custom integrations + cards
```

Flux variable substitution is off for these ConfigMaps, so Jinja is never
mangled. Per-cluster values reach HA as container env vars, which the config
reads with `!env_var`.

## Dashboards

The sidebar, top to bottom:

- **Home** (`/home-hub`, `dashboards/house.yaml`): the default dashboard, in
  order of use. **Home**: robovac room buttons and controls, who's home,
  weather, lights (with the `light.pool_lights` group) and media, appliances,
  a power summary, cameras (a tap opens Security). **Robovac**: the map,
  full controls, consumables. **Energy**: live power and the biggest loads,
  two weeks of use vs solar per inverter, the EV charger.
- **Security** (`/home-security`): cameras and Frigate reviews.
- **Energy**: the built-in one (sources in `.storage/energy`, UI-owned).
- **Monitoring** (`/home-monitoring`, everyone): Grafana in a frame, at its
  home page with its own navigation, so every Grafana dashboard is reachable
  without listing it here. It's signed in as the kiosk identity (a Grafana
  Viewer) on every device, through `grafana-embed` with a JWT in the URL
  (`HASS_GRAFANA_URL`, in home-assistant-secret): Cognito's login page won't
  load in a frame, so oauth2-proxy can't be in front of it. To edit in
  Grafana, open it directly at `grafana.<internal domain>`. Any HA user can
  read that URL from the dashboard config, so it's a Viewer credential by
  design. The token expires 2028-10-31. Ori mints a new one with the key in
  its credentials store, then updates the secret.
- **Wall display** (`/kitchen-display`): the tablet's kiosk.

UI-owned and set over the websocket API (not in git): `default_panel` in the
frontend system data (`frontend/set_system_data`, key `core`) is `home-hub`; the
built-in Overview (`/home`, the areas view), Map, Logbook and History are
hidden from the sidebar (`frontend/update_panel` `show_in_sidebar: false`,
and `lovelace/dashboards/update` for Map). They still work by URL. A user can
still pick their own default under Profile.

### Robot vacuum

A Roborock S5 (`roborock.vacuum.s5`) on the Mi Home app, at the DHCP lease
`robovac1`. HA reaches it on UDP 54321 from the pod. Two UI-owned
integrations:

1. **Xiaomi Cloud Map Extractor** (custom, v3 config flow; installed by
   prepare-config.sh), signed in with the Xiaomi account. It gives
   `image.robovac_live_map` with the rooms and calibration as attributes,
   and stores the device token in its config entry.
2. **Xiaomi Miio** (core), added with **host + token** (the "manual" step),
   the token copied from the Map Extractor's entry. Its cloud login fails:
   Xiaomi asks for an extra verification the Miio flow can't show (the Map
   Extractor logs it as "Additional authentication required" with a URL).
   It gives `vacuum.roborock_vacuum_s5` (start/stop/dock, fan speed, room
   cleaning through `app_segment_clean`) and consumable sensors.

Rooms are the ones made in the Mi Home app, cleaned by segment id
(`app_segment_clean`), top to bottom on the map: 17 Playroom, 18 Kitchen,
16 Living room. `packages/robovac.yaml` has `script.robovac_clean_rooms`
(any of them, `rooms: ["18"]`) and one script per room, which the Clean
playroom / kitchen / living room buttons on Home run. The map card on Home
(`custom:xiaomi-vacuum-map-card`, resource in configuration.yaml) selects
the same rooms; its outlines are the image's `rooms` boxes.

Consumables on the Robovac view are gauges from each part's full life
(Roborock S5: filter 150 h, main brush 300 h, side brush 200 h, sensors 30 h)
that go yellow near the end and red when due, each with a reset button that
asks first. The remaining-time sensors show hours through the entity
registry's display unit (UI-owned).

### Other devices

Local integrations added by host (UI-owned; discovery doesn't reach the pod):
LIFX (four pool-area Minis), Google Cast (kitchen speaker, kitchen display,
living room TV), Denon AVR-S750H, Tesla Wall Connector. Sonos has no
manual-host flow, so its speakers are under `sonos:` in configuration.yaml.
`light.pool_lights` (packages/lights.yaml) groups the four LIFX bulbs. The
Pool themes card on Home (`custom:tnwks-lifx-themes-card`, in
kitchen-cards.js) paints them with `lifx.paint_theme`. Its theme list is read
at runtime from that action's selector, i.e. the aiolifx-themes library that
tracks the LIFX app's themes, so new LIFX themes arrive with HA updates and
none are listed in git. The card only sorts them into groups and picks
"In season" ones by month. A name it doesn't know lands in Moods. All of them are by DHCP address, and each has a static lease at that
address (`EXTRA_DHCP_LEASES` in `infrastructure/mikrotik/tools/vyos-to-locals.py`,
then `task mikrotik:generate`). Pin any new device added by host the same way. The LG TV wasn't
answering on its webOS ports (3000/3001) when this was set up; add it with
the TV on (webOS TV, accept the pairing prompt on screen).

## Presence

Each adult's `person` has two trackers (UI-owned, Settings > People):

- **GPS:** the Companion app's `device_tracker.<phone>`. It's the only source
  that can say *away*, and it keeps a person home while they're in the yard,
  out of Wi-Fi range.
- **Wi-Fi:** `device_tracker.<phone>_wifi` from the UniFi Network integration.
  `person` treats a router tracker's `home` as authoritative and ignores its
  `not_home`, so Wi-Fi only speeds up arrival (the controller pushes the
  connect event over its websocket).

The UniFi integration logs in to `unifi.networking.svc.cluster.local:8443` as
the controller's read-only local account `homeassistant`
(`homeassistant_password` in the UniFi SOPS file; `task unifi:bootstrap`
creates it on a rebuilt controller). Its options track **only the phones**:
client and device tracking off, wired clients off, DPI switches off, and the
phones' MACs under *Advanced > Client source*. The options form only offers
clients the controller currently sees, so a phone has to be on Wi-Fi to be
added. Detection time is 180 s. Phones keep a stable per-network MAC
(Android's default); an iPhone needs Private Wi-Fi Address set to *Fixed*,
since *Rotating* breaks tracking. UniFi creates client trackers disabled;
enable the phone's and give it the `_wifi` name.

Don't add the wall tablet's tracker to a person, and don't build anything
security-relevant (locks) on presence alone.

## Prometheus metrics

`prometheus:` in configuration.yaml exposes entity states at
`/api/prometheus` (metric prefix `homeassistant_`), and the ServiceMonitor in
`helmrelease.yaml` scrapes it every minute. The direction is one-way on
purpose: automations, presence and the Energy dashboard read their sources
directly in HA, and Prometheus is for Grafana and alerts. Nothing in HA should
depend on the monitoring stack.

HA is internet-facing, so the endpoint keeps its token requirement. The scrape
uses a long-lived token (`home-assistant-prometheus`, `prometheus.sops.yaml`)
of the HA user **Prometheus**: group *Read only*, local network only, a
password nobody kept. To replace the token: reset that user's password
(Settings > People > Users), log in as `prometheus` from the LAN and create a
long-lived token on its profile, put it in the secret with `bin/sops --set`,
then delete the old token.

Prometheus on `homelab-wsl` keeps 2 days of data on an emptyDir, so this is
not long-term history yet. HA's recorder (14 days) and its long-term
statistics are still where energy history lives.

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
     the app isn't enough for alerts: every alert goes to the named phones in the
     package's `phones`, so add a new adult's phone there (see Person alerts).
     Other app devices, like the kitchen wall tablet, get no alerts.
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

## Person alerts

`config/packages/frigate_person_alerts.yaml` holds all of Frigate's person
alerts: the night alerts to both adults' phones, and normal pushes while the adults
are away or in a weekday window.

### Night person alerts

The night alerts send one Companion app push per
Frigate review, the first time the review becomes a person alert. The push has
a snapshot and an *Open in Frigate* button, and tapping it opens the camera
grid. It fires for the cameras in `watched_cameras`, which is all of them,
since every camera is exterior. It only fires between *Night alerts start* and
*end* (set on the Alerts view, 23:00-06:00 now), and only while *Night person
alerts* is on. Each alert also leaves an HA
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

To test it, move the start and end times on the Alerts view so the window
covers now, then walk past a camera. Put the times back afterwards: they stay
where you leave them (see below).

### Away and weekday alerts

Outside the night alerts (night window with *Night person alerts* on), the same
person alerts send a **normal** push (high priority, channel *Security alerts*,
no Do Not Disturb override, no in-app notification) to named phones:

- **Away person alerts:** adult1's phone while `person.adult1` is away, and
  adult2's phone too when `person.adult2` is also away. adult2 away alone sends
  nothing. `unknown`/`unavailable` doesn't count as away.
- **Weekday person alerts:** adult1's phone on the days picked
  (`input_text.weekday_person_alerts_days`, a list like `mon,tue,wed`)
  between *Weekday alerts start* and *end*, home or not. A window that ends
  earlier than it starts runs overnight; the part after midnight belongs to
  the day it started on.

A review that matches both still sends one push per phone, and the end of the
review swaps in the animated preview silently, as at night. The phones are the
`phones` variable in the package (legacy `notify.mobile_app_<device>`
services), shared with the night alerts, so a new or renamed phone needs
updating there. Nothing calls the base `notify.notify`, which would also reach
the kitchen wall tablet.

### The Alerts view

The night and weekday windows each get a schedule card,
`custom:tnwks-alert-schedule-card` (`config/www/tnwks-security/alert-schedule-card.js`,
plain custom element, no build step): an on/off switch, a row of day buttons
(weekday only) and native From/To time pickers, plus a one-line summary such as
"Mon-Wed · 9:00 AM - 3:00 PM" that warns when no day is picked or From equals
To. Every tap calls the helper's own service, so there is nothing to save.
Bump the `?v=` on its resource in `configuration.yaml` with every change to the
file.

### Settings survive restarts

Every switch and time on the Alerts view is a helper without `initial`, so HA
restores its last value from `.storage/core.restore_state` (written at shutdown
and every 15 minutes) after a restart or a config deploy. Set them on the
dashboard, not in git. A helper with nothing to restore (a new one, or a lost
`/config` volume) starts at HA's default, off or 00:00, so set it once on the
dashboard after adding it. Don't add `initial` back: it overrides the restored
value on every start, which is how the night window used to reset on every
deploy.

## Kitchen display

The Apolosign display in the kitchen (2160x3840 at 480 dpi, so a 720x1280 CSS
viewport) runs the **Kitchen** dashboard, `config/dashboards/kitchen.yaml`.
Its cards are ours, in `config/www/tnwks-kitchen/kitchen-cards.js`: plain
custom elements, no build step. HA caches `/local` for a long time, so bump
the `?v=` on its resource in `configuration.yaml` with every change to it.

Routines (chores) are UI-owned data, so the family can add, change and
delete them on the display: `+` and `✎` next to each name on the Routines tab,
or long-press a routine. They're the items of the Local To-do list
`todo.kitchen_routines` ("Kitchen routines"), one per routine. The item's name
is the routine, its status is "done today", and its description is JSON with
who (`member`), when (`period`, `days`), the `emoji` and the stars (`points`,
optional `points_by_day`). Editing that JSON in HA's to-do panel works too,
but the dashboard is the easier way. `config/packages/kitchen_routines.yaml`
keeps the machinery: `script.kitchen_routine_set` checks or unchecks one and
adds or takes back its stars on `counter.stars_<member>`, the midnight reset
reopens everything (stars stay), `sensor.kitchen_routines` copies the list
into an attribute for templates, and the done/total and AM/PM sensors behind
the week grid are built from it. Until 2026-10-31 the routines were
`input_boolean`s in that package; they were moved into the list with their
state.

**Rewards** (the Rewards tab, `config/packages/kitchen_rewards.yaml`): the
kids spend their stars on rewards the parents set up. A kid taps their name,
then a reward they have the stars for, and confirms; the stars come off
`counter.stars_<kid>` and both adults' phones get a notification. Rewards
are the items of the Local To-do list `todo.kitchen_rewards` (name = the
reward, description JSON `{"emoji", "cost"}` plus `"members": ["kid1"]` for
a reward only one kid can pick), and every redemption or hand-made change is
an item of `todo.kitchen_stars_log` (due = when, completed = undone). Both
lists are UI-owned. Only the parents can define rewards, add or take away
stars ("± stars" on a kid) and undo an entry in Recent, and only from their
own devices: the card shows those controls to the HA users of
`person.adult1`, `person.adult2` and `person.ori` and never on the wall
display, and the package's scripts (`script.kitchen_reward_save`,
`kitchen_stars_adjust`, `kitchen_stars_undo`) refuse anyone else by
`context.user_id`. `script.kitchen_reward_redeem` is open to everyone,
including the tablet's kiosk user. The Routines tab shows each kid the
reward they can get now or the next one to save for, and "this week" on
the Home tab doesn't drop when stars are spent. Log entries older than 60
days are removed at night. The rewards replaced the per-kid reward goal
helpers (`input_text`/`input_number.routines_goal_*`) on 2026-11-01.

Members have neutral keys (`adult1`, `adult2`, `kid1`, `kid2`) in every
entity ID and attribute, because the repo is public. Display names live in
`secrets.sops.yaml` (`KITCHEN_<KEY>_NAME`) and become the friendly names of
the `counter.stars_<key>` helpers through `!env_var`. The cards read a
person's name from that counter and match calendar events on it, so a new
person needs a key in `FAMILY` (kitchen-cards.js), a counter and an env var.

When the display has been idle for 5 minutes, Android starts its screensaver,
ImmichFrame (photos, a clock and today's events; [`immichframe.md`](immichframe.md)).
A tap ends it and shows the dashboard again.

kiosk-mode hides HA's header and sidebar on a page opened with `?kiosk` (the
tabs keep it in the URL; with `&cache` kiosk-mode also remembers it in that
browser). Open it with `?clear_km_cache` to clear that cache. The dashboard's own `kiosk_mode` turns kiosk on in the tablet's browser only
(its user agent contains "Apolosign"; `WALL_UA` in kitchen-cards.js matches
the same), so after a restart or from the sidebar it's a kiosk again without
`?kiosk`. To reach the rest of HA from the tablet, tap the **Home Assistant** logo at
the right of the tab bar: it goes to Home (`/home-hub`) inside the app (no reload), with
the header and sidebar. **Wall display** in the sidebar, or 5 minutes
untouched, brings the kiosk back: an HA app that has shown the dashboard in
kiosk mode remembers it (localStorage `tnwks-kitchen-kiosk`, cleared by
`?clear_km_cache`) and returns to it from any HA page. `?disable_km` still
shows HA's menu on the dashboard itself, with a **Back to kitchen** pill
(inside the Android app that reload goes through `homeassistant://navigate/...`,
because a plain reload there opened Chrome).

Taps on the Home page: a card with `tap_page` goes to that page when tapped
outside its own buttons (kids to Routines, Dinner to Meals, Groceries and
Notes to Lists, Today and Coming up to Calendar). Any event, on any page,
opens its details (time, place with Directions, who, description). A planned
meal adds **Open in Mealie**, which opens the recipe (slug from
`mealie.get_mealplan`; `mealie.entry` in the card config is the Mealie config
entry id), and the Meals tab has **Open Mealie** for the planner. On the
tablet Mealie opens full screen inside the kiosk, in a frame with Recipes and
Close buttons, signed in as Mealie's local `kitchen` user (household
Family, can organize; password in Ori's credentials store, not in git), because
"Login with tnwks" sends the frame to Cognito, whose login page refuses to
load in a frame. Mealie's `TOKEN_TIME` is 720 hours so that session survives
quiet weeks; if it lapses, sign in again with that user (the tablet's
launcher, the Apolosign calendar app, can't pin
shortcuts, so Chrome offers no PWA install there, and Chrome would take the
screen from the kiosk). Elsewhere links leave HA, so an installed Mealie PWA
on a phone catches them.

Theme and mode belong to whoever picks them, and nobody else's screen
changes. The wall display (the HA app that has shown the dashboard in kiosk
mode, localStorage `tnwks-kitchen-kiosk`) keeps its own in
`input_select.kitchen_theme` / `input_select.kitchen_mode`, which its
screensaver reads too. Everyone else's are kept with their HA user, in HA's
frontend user data (key `tnwks-kitchen-display`, the per-user store
`frontend/set_user_data` writes), so a pick on a phone follows that person to
every device they sign in on. Someone who never picked gets Everyday in auto
mode, whatever the wall display wears. Picks a device made before 1.11.0 (localStorage
`tnwks-kitchen-theme` / `tnwks-kitchen-mode`) move to that person's user
data the first time the dashboard loads there, unless they already have one.
Only the wall display shows the holiday offer.

Weather (top right of the header): `weather.forecast_home` (met.no) only
refreshes about hourly, so the temperature shown is interpolated from the
hourly forecast between its two nearest hours, and today's high and low come
from the daily entry dated today. Tap it for details: humidity, wind, UV,
clouds, dew point, pressure, sunrise and sunset, the next 12 hours and the
next days.

Dark or light: `auto`, `dark` or `light`, set from the 🎨 sheet (the wall
display's in `input_select.kitchen_mode`, in `packages/kitchen_display.yaml`;
everyone else's with their user, as above). `auto` is dark while `sun.sun` is below the horizon. Dark is the
original look. Light keeps each theme's accent and derives the rest from it
(near-white cards on a faintly tinted page; the everyday theme stays neutral
gray), and darkens the accent and the "done" green where they're text until
they pass WCAG AA (4.5:1). The HA Companion app colors Android's status bar
from `--app-header-background-color` and the navigation bar from
`--primary-background-color` on `<html>`, read again whenever the frontend
sends `theme-update` on the external bus. HA's own theme is the default light
one, so those bars were white. While the Kitchen dashboard is on screen, the
cards set both variables to the dashboard's page color (a stylesheet with
`!important`, so HA re-applying its theme can't undo it) and send
`theme-update` through `hass.auth.external`. They take the variables back
when the dashboard goes away and while HA's menu is open, so the rest of HA
looks normal.

The dashboard is a single HA view. `kitchen-pages-card` draws the header, the
tabs and every tab's cards as pages that stay mounted, so a tab switch only
changes which page is visible (it used to be an HA view change, which rebuilt
every card and refetched its data). Cards on hidden pages skip their renders
until they're shown, and renders patch the DOM in place (`morph()`), which is
what lets the tap animations and transitions play. The tab is in the URL hash
(`/kitchen-display/home#calendar`), and the display goes back to Home after 5
minutes untouched. Tabs: Home, Calendar (Family, Week, Month and List, with
paging and a person filter), Routines, Meals (a week grid, one column per
Mealie meal calendar) and Lists (several to-do lists plus sticky notes).

Themes: `default` or a holiday (New Year, Valentine's, St. Patrick's, Easter,
4th of July, Halloween, Thanksgiving, Christmas). A holiday turns the page
into an illustrated scene (`SCENES` in kitchen-cards.js): a sky behind the
clock and a landscape along the bottom, in three depths of hills with mist
between them, lit from above, with a dark and a light palette. Thanksgiving
is a harvest moon over autumn trees, a barn, a fence, hay and pumpkins;
Christmas an aurora over snowy pines and a lit cabin; Halloween a full moon
over a haunted house, crooked trees, tombstones and jack-o'-lanterns; Easter
blossom trees, tulips and eggs; the 4th a town skyline under fireworks with a
flag; Valentine's a crescent moon over heart-shaped trees and heart
balloons; St. Patrick's a rainbow over a cottage, sheep and a pot of gold;
New Year's a city skyline and a clock tower under gold bokeh. The scene is
drawn procedurally as two inline SVGs at the page's width (seeded, so it's
the same every time) and painted once. The cards float over it: a touch
translucent, lit from the top, with a rim highlight, a soft shadow and a
hairline in the theme's color, plus each holiday's signature (`SKINS`: snow
caps and holly, purple drips and a cobweb, a gold, lace, rainbow, flag,
pastel or harvest edge, candy-striped bars), and the active tab is a lit
pill. That's one stylesheet every card adopts; nothing on the cards animates.
The greeting counts down to the holiday ("🦃 24 days to Thanksgiving") for
up to two months. Around them: a garland next
to the tabs (string lights for Christmas and Halloween, bunting for the 4th),
slow falling or drifting emoji, taps that pop the holiday's emoji, confetti
in its colors, and every two or three minutes a character crossing the
screen (bats, a ghost or Santa peeking up, a turkey or a spider walking, a
bunny hopping, fireworks). While a holiday is coming up, the wall display's
greeting line offers its theme, once per holiday and year (answers are kept
in `input_text.kitchen_theme_answered`). 🎨 next to the tabs picks any theme
any time of year. A holiday theme that was on through its holiday switches
back to `default` in the week after it; one picked after the holiday stays
(the pick time is `last_changed` of the select on the wall display, which an
HA restart resets, so a restart in that week can leave one up; for everyone
else it's saved with the pick). The screensaver just follows the select.
Animations respect the device's reduced-motion setting. The falling emoji and
the characters sit on a layer over the whole page, so they show only while
the Kitchen dashboard is open: on any other HA page in the same tab they're
hidden and paused, and they come back with the dashboard.

Calendars, the meal plan and extra to-do lists are integrations, so they're
UI-owned. The dashboard expects `calendar.family` (the family Google
calendar), `calendar.mealie_breakfast`, `_lunch`, `_dinner` and `_snack`
(Mealie), and the Local To-do lists `todo.kitchen_routines`,
`todo.things_to_do`, `todo.someday` and `todo.notes` (Groceries is
`todo.shopping_list`). Each card shows a "not
connected" or "not set up yet" note until its entity exists; the Notes card on
Home stays hidden instead.

`calendar.family` is a Remote Calendar entry pointing at the Google Family
calendar's secret iCal address (UI-owned, so the URL stays out of git).
Remote Calendar only re-downloads once a day, so
`packages/kitchen_calendar.yaml` refreshes it every 15 minutes.

The Companion app and browsers keep the card module in memory until the page
reloads, while HA pushes a new dashboard config to them live. So the cards
compare their own `?v=` with the resource URL HA serves (`lovelace/resources`,
on load, on every reconnect and every 15 minutes) and reload the page when they
differ, at most once every 10 minutes. That only works if the `?v=` changes
with the file.

## Break-glass

- **SSO broken:** on the login page's welcome screen, follow the link to the
  HA login (or go straight to `/?skip_oidc_redirect=true`) and use the owner
  account with its TOTP code.
- **Proxy settings broken** (UI unreachable through ingress): use the
  port-forward above and fix them under Settings > System > Network.
