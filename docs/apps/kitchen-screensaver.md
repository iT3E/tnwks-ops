# Kitchen screensaver

The page the kitchen display shows as its screensaver: photos from Immich's
**Kitchen screensaver** album with a box of household info over them, at
`https://kitchen-screensaver.internal.tnwks.us` (LAN and admin VPN only,
`nginx-internal`). The display opens it in the ImmichFrame Android app's
screensaver (a "dream"), which starts after 5 idle minutes; a tap goes back
to the Home Assistant dashboard. The [ImmichFrame server](immichframe.md)
stays deployed as the fallback.

Manifests: `kubernetes/apps/selfhosted/kitchen-screensaver/`. WSL-only like
Immich (wired from `kubernetes/clusters/wsl/apps/kustomization.yaml`). The
service is `app/src/server.py` (Python, standard library only) and the page
`app/src/index.html`, both from a ConfigMap, on the same pinned stock Python
image as the `immich-sync` CronJob. There's no image of its own to build.

## Why not ImmichFrame's own page

ImmichFrame showed all-day events as "12:00 AM - 12:00 AM", had no Home
Assistant data (weather, dinner, stars, house theme), cropped every
landscape photo to a strip of its middle, and paid no attention to faces.
This page does those things and keeps both credentials server-side.

## What it shows

- **Photos**, a new one every 30 s with a 2 s crossfade. The next photo is
  fetched and decoded before it fades in over the old one, which stays
  opaque underneath, so there's no dark frame in between.
  - Portrait photos fill the screen.
  - Landscape photos come in pairs, stacked top and bottom. A landscape photo
    with no partner (an album with a single landscape photo) sits on a
    blurred copy of itself.
  - The crop keeps the faces Immich detected in view (`object-position` from
    the face boxes, with room for hair and chin).
  - Order: a shuffled pass through the album, then another. The pass is
    remembered between screensaver runs (localStorage `ks_deck` in the
    ImmichFrame app), so every photo shows once before any repeats; photos
    added mid-pass join it at random spots. The sync job runs hourly; the
    server re-reads the album every 5 minutes (`ALBUM_TTL`) and the page
    every 10, so a synced change shows up within about 15 minutes.
- **The box** (bottom left, translucent, tinted by the house theme):
  - 12-hour time, weekday and date;
  - weather from `weather.forecast_home`: icon, temperature, condition and
    today's high and low (the daily forecast);
  - a holiday line: the greeting on the day ("Happy Halloween!"), or a
    countdown while that holiday's theme is on ("Christmas in 5 days");
  - what's left of today on `calendar.family`: all-day events first (a
    multi-day event shows on each of its days), then timed ones that haven't
    ended, "Now" for one under way; at most 5 lines with "+N more today", or
    "Nothing else today";
  - tonight's dinner from `calendar.mealie_dinner` when one is planned
    (until 9 PM);
  - the kids' stars, `counter.stars_kid1` and `counter.stars_kid2`, labelled
    with each counter's friendly name (the names live in Home Assistant, not
    here).
- **Theme:** `input_select.kitchen_theme` picks the palette, the same
  palettes, holiday dates and windows as the Kitchen dashboard
  (`kitchen-cards.js` in the Home Assistant config; keep the two in step
  when a theme is added). A theme past its holiday shows as the everyday one;
  switching the select back is the dashboard's job, this page only reads.
- **Burn-in and faces:** the box moves a few pixels every 3 minutes. When it
  would cover the photo's main face, it moves to the opposite corner (top
  right), or whichever corner covers the fewest faces. With reduced motion
  on, photos and the box change without animation.

## How it works

The display only talks to this service. The service reads Home Assistant and
Immich with its own read-only credentials, so the display holds neither.

| Path              | What                                                                                                                                            |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `/`               | The page. One self-contained file, no navigation (see below).                                                                                   |
| `/api/info`       | Weather, today's events and dinner, stars, theme. The page passes its local midnight-to-midnight window, so the service needs no time zone.     |
| `/api/photos`     | The album's photos: id, size, face boxes (fractions of the photo).                                                                              |
| `/img/<id>`       | A photo from the album (Immich's preview), nothing else.                                                                                        |
| `/api/ping`       | The page checks in every 10 minutes; the pod log shows `ping boot=... loads=... up=... mem=... slides=...` (page loads, uptime, JS heap in MB). |
| `/healthz`        | Liveness, no secret.                                                                                                                            |

- **Refresh:** the page asks for `/api/info` every 3 minutes and at
  midnight; the service answers from memory for 60 s and re-reads the
  forecast every 15 minutes. `/api/photos` every 30 minutes; the service
  re-reads the album every 15 minutes and each photo's faces once (a photo
  with no faces yet is asked again after an hour, in case face detection
  hadn't run).
- **Photo size:** Immich's preview, which `immich/app/config/immich.yaml`
  sizes for the display: 2160 px on the short side, so a portrait photo is
  2160x2880 and a landscape one 2880x2160 (the default 1440 looked soft on
  the 4K panel). The service keeps the last 64 MiB of photos in memory; the
  display's WebView doesn't cache, and fetches each photo once.
- **Home Assistant:** REST for states and calendars; the daily forecast
  through the websocket (`weather/subscribe_forecast`), because the
  `weather.get_forecasts` service needs control rights the read-only user
  doesn't have.
- **No navigation:** the ImmichFrame app's screensaver WebView tries to send
  every navigation it sees (a link, a redirect, `location.href = ...`, even
  `location.reload()`) to the external browser (`shouldOverrideUrlLoading` in
  its `ScreenSaverService.kt`), and from the screensaver that crashes the
  app. Everything after the first load is `fetch()`.
- **Days of running:** the page reuses its DOM and frees each photo when
  it's gone. Every 6 hours, at a photo change and only when `/healthz`
  answers, it has itself loaded afresh. Not with `location.reload()`: the
  app's WebViewClient treats a reload as a navigation too, and its attempt to
  start the browser from the screensaver crashes the app
  (`AndroidRuntimeException: Calling startActivity() from outside of an
  Activity context requires the FLAG_ACTIVITY_NEW_TASK flag`, seen on the
  display on 2026-10-31; the dream ends and the dashboard shows). Instead the
  page loads an image that can't load: `https://127.0.0.1:1` (nothing listens
  there on the display), then, if that didn't do it, `https://reload.invalid`
  (no such name). The app reloads its URL 5 s after a request fails like
  that (`onReceivedError`), and that load comes from the app, so it's
  allowed. A dropped connection at this service doesn't work: the ingress
  answers it with a 502, and HTTP errors don't count. After a safety reload
  the next `ping` line in the pod log carries `via=port` or `via=dns`
  (checked on the display on 2026-10-31 with `test=1&reload=60`: a fresh
  load with `via=port` every minute or so, the screensaver kept running). In
  a normal browser the page uses `location.reload()`.
- **Pod restarts:** for the same reason, a restart of this pod shows up on
  the display as a short black gap (or the app's error page), then the page
  again.

### Accounts and secrets

`app/secrets.sops.yaml` (`kitchen-screensaver-secret`). Workstation copies:
`~/.openclaw/credentials/kitchen-screensaver.env`.

| Key              | What                                                                                                                                                                                                                                                            |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AUTH_SECRET`    | Protects the page and API: `?authsecret=<secret>` (how the ImmichFrame app passes it) or `Authorization: Bearer <secret>`. The page reads it from its own URL and sends it as a Bearer token, and is served with `Referrer-Policy: no-referrer`.                     |
| `HASS_TOKEN`     | Long-lived token of the Home Assistant user **Kitchen screensaver**: group `system-read-only` (reads states and calendars, can't call services), local network only, no password (it was removed once the token existed, so nobody can log in as it).            |
| `IMMICH_API_KEY` | Immich key `kitchen-screensaver (read-only)` on the screensaver account: `album.read`, `asset.read`, `asset.view`, `face.read`. See [`immich.md`](immich.md).                                                                                                   |

Both accounts are UI-owned (Home Assistant's and Immich's databases), not in
git. To replace the HA token: log in to Home Assistant as an admin, give the
Kitchen screensaver user a password for a moment (Settings > People >
Users), log in as it, create a long-lived token under its profile
(Security), remove the password again, then update the Secret with
`bin/sops` and merge. The service never logs the query string, so the
secret doesn't reach the pod log.

**Changing `AUTH_SECRET`:** update the Secret, merge, then set the new
secret in the ImmichFrame app on the display (below).

## The kitchen display

Apolosign, Android 16, 2160x3840 portrait (720x1280 CSS px); the screensaver
setup itself (the ImmichFrame app from Google Play, `screensaver_components`,
the Apolosign overlay widget set to None) is in
[`immichframe.md`](immichframe.md#the-kitchen-display).

ImmichFrame app settings (open the app, then swipe down or press up for its
settings):

- **Server URL:** `https://kitchen-screensaver.internal.tnwks.us`
- **Authorization Secret:** this service's `AUTH_SECRET`
- **Use WebView?** on

The app adds `?authsecret=` itself, and checks the URL with a `HEAD`
request before loading it. After changing a setting, run
`adb shell am force-stop com.immichframe.immichframe` (the app's main screen
starts an unauthenticated remote-control server on port 53287 that the
screensaver doesn't need) and go back to the dashboard.

There's no way to start the screensaver from adb on this firmware: wait 5
idle minutes, or use the Preview button in Android's screen saver settings
(`adb shell am start -a android.settings.DREAM_SETTINGS`).

### Switching back

- **To the plain ImmichFrame page:** set the app's Server URL to
  `https://immichframe.internal.tnwks.us` and its Authorization Secret to
  ImmichFrame's `AUTH_SECRET` (`immichframe.md`, Secrets). Nothing else
  changes; that server is still running.
- **To the Google Photos screensaver** (with the Apolosign's clock-and-agenda
  overlay):
  ```bash
  adb shell settings put secure screensaver_components com.google.android.apps.photos/com.google.android.apps.photos.daydream.PhotosDreamService
  ```
  then set the Apolosign screensaver app's Calendar Widget back to Standard
  (`adb shell am start -n com.elclcd.screensaver/.view.DreamSettingActivity`).
  A tap doesn't end that dream; its overlay handles taps.

To retire this service, remove its `ks.yaml` from the WSL apps kustomization;
Flux prunes it.

## Changing the page

Edit `app/src/index.html` or `server.py` and merge: the ConfigMap change
restarts the pod (reloader), and the display picks up the new page at its
next reload or the next time the screensaver starts. The ConfigMap is
excluded from Flux's `${VAR}` substitution (the page's JavaScript uses
template literals).

Before the PR:

```bash
cd kubernetes/apps/selfhosted/kitchen-screensaver/app
python3 -m unittest discover -s tests      # fakes for Home Assistant and Immich
```

To look at it like the display does, run `src/server.py` on a workstation
with the credentials from `kitchen-screensaver.env` and
`HASS_URL=https://hass.internal.tnwks.us`,
`IMMICH_URL=https://immich.internal.tnwks.us`, `PORT=8765`, and render
`http://localhost:8765/?authsecret=<secret>` at a 720x1280 viewport
(Playwright: intercept `/api/info` and `/api/photos` to try themes,
calendars and photo mixes; `page.clock.set_fixed_time` for a date).
With `test=1` in the query string, `interval=<s>` shortens the time between
photos and `reload=<s>` the time until the safety reload; without it both are
ignored, so a test URL left in the app can't keep the display reloading.

## Troubleshooting

- **The display is dark and off the network after a screensaver test:** a
  crash of the app's screensaver (any navigation in the page does it, see
  "No navigation") ends the dream with the idle timeout long past, so the
  display goes straight to sleep, and asleep it drops off Wi-Fi (no ping, no
  adb). A touch or the power button brings it back; adb's wireless port can
  be a different one afterwards.

- **Display shows ImmichFrame's error page or "Unauthorized":** the app's
  secret doesn't match `AUTH_SECRET`, or the host doesn't resolve from the
  LAN (`*.internal.tnwks.us` is the router's wildcard to the Windows host's
  ingress port proxy, see [`../wsl/lan-exposure.md`](../wsl/lan-exposure.md)).
- **Box but no photos:** `kubectl -n selfhosted logs deploy/kitchen-screensaver`
  shows `album: ...` warnings when Immich is unreachable, the album name
  changed (`IMMICH_ALBUM_NAME`) or the key was revoked.
- **No weather, events or stars:** the log names what failed (`weather`,
  `calendar:calendar.family`, `forecast`, ...). A 401 means the HA token was
  revoked.
- **Is the page alive on the display?** `ping` lines in the pod log every 10
  minutes while the screensaver runs; `loads` counts page loads on that
  display, `mem` is the page's JS heap.
