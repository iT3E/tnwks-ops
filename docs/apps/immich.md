# Immich

Self-hosted photo library at `https://immich.internal.tnwks.us` (LAN and
admin VPN only, `nginx-internal`). Its first job is holding the photos the
kitchen screensaver shows: an hourly job mirrors the Google Photos shared
albums listed in Home Assistant into Immich, and Home Assistant's slideshow
reads them back through a read-only API key.

Manifests: `kubernetes/apps/selfhosted/immich/`. WSL-only (wired from
`kubernetes/clusters/wsl/apps/kustomization.yaml`), version
`ghcr.io/immich-app/immich-server` in `app/helmrelease.yaml`.

## Pieces

One app-template HelmRelease (`immich`, namespace `selfhosted`) with four
controllers:

| Controller         | What                                                                    | Storage                              |
| ------------------ | ----------------------------------------------------------------------- | ------------------------------------ |
| `server`           | API, web UI, background workers (thumbnails, metadata, nightly DB dump) | `immich-library` PVC at `/data`      |
| `machine-learning` | Face detection and CLIP smart search, on CPU                            | `immich-model-cache` PVC at `/cache` |
| `valkey`           | Job queue for the server                                                | none (emptyDir)                      |
| `postgres`         | `ghcr.io/immich-app/postgres` with VectorChord, dedicated to Immich     | `data-immich-postgres-0` PVC         |

The shared CNPG cluster in `databases` isn't used: it doesn't carry
VectorChord, and Immich's own image is what upstream tests against. The
`cache/valkey` instance is oauth2-proxy's session store, so Immich gets its
own queue.

## Where the data lives

- **Originals and thumbnails:** PVC `immich-library` on `local-path-bulk`,
  i.e. `/var/lib/rancher/k3s/storage/bulk/` inside whichever worker the
  server first ran on. That's the WSL distro disk, which lives on the Windows
  `E:` drive (`E:\WSL\Ubuntu\ext4.vhdx`). The PVC says 200Gi, but local-path
  doesn't enforce sizes: the real ceiling is E:'s free space, which
  `WindowsDiskCritical` watches. To grow it, raise `size` (the class allows
  expansion) and make room on E:.
- **Database:** PVC `data-immich-postgres-0` on `${STORAGE_CLASS_DATABASE}`.
  Immich dumps it nightly at 02:00 into the library volume
  (`/data/backups`, 14 kept).
- **Settings:** `app/config/immich.yaml`, loaded with `IMMICH_CONFIG_FILE`.
  While a config file is in use, the admin settings page is read-only, so
  change settings in git. The OAuth placeholders in it are filled from
  `immich-secret` by the `render-config` init container at pod start.
- **Users, albums, API keys:** in the database (UI-owned, not in git).

For now the screensaver albums in Immich are a mirror: Google Photos is the
source of truth, and losing the library means re-running the sync. Anything
uploaded to Immich by hand has no other copy.

## Login

Immich's built-in OAuth against tnwks-auth (Cognito app client `immich`,
`infrastructure/terraform/modules/aws/cognito.tf`), not oauth2-proxy: the
mobile app and the Home Assistant slideshow use Immich-issued sessions and
API keys that a proxy session would block.

- Immich can't map `cognito:groups` to roles (its role claim must hold the
  literal value `admin` or `user`), so **auto-register is off**. To let
  someone in, an admin creates their Immich user with their Cognito email;
  their first "Log in with tnwks" links to it. Admin rights are set on the
  Immich user.
- The login page shows the OAuth button and the password form
  (`autoLaunch: false`), so the password accounts below keep working.
- Logout goes through Cognito's `/logout` back to `/auth/login` (the
  client's registered `logout_uri`).

## Accounts and keys

| Account or key                               | Kind                              | Used by                           | Where                                                                                        |
| -------------------------------------------- | --------------------------------- | --------------------------------- | -------------------------------------------------------------------------------------------- |
| break-glass admin                            | Immich password, admin            | people, when OAuth is down        | workstation `~/.openclaw/credentials/immich-admin.env`                                       |
| screensaver                                  | Immich password, not admin        | owns every synced photo and album | workstation `~/.openclaw/credentials/immich.env`                                             |
| `gphotos-sync (cluster CronJob)`             | API key on screensaver            | the sync job                      | `immich.env` (`IMMICH_SYNC_API_KEY`), `sync/secrets.sops.yaml`                               |
| `home-assistant album_slideshow (read-only)` | API key on screensaver            | Home Assistant's slideshow        | `immich.env` (`IMMICH_HA_API_KEY`)                                                           |
| HA token `immich-screensaver-sync`           | long-lived token on HA's Ori user | the sync job                      | `~/.openclaw/credentials/hass.env` (`HASS_SCREENSAVER_SYNC_TOKEN`), `sync/secrets.sops.yaml` |

The screensaver account owns the synced photos so the sync job can't reach
anything else in Immich, and so the read-only key Home Assistant uses sees
only those photos. Key permissions:

- sync: `asset.read`, `asset.upload`, `asset.update`, `asset.delete`,
  `album.create`, `album.read`, `album.update`, `album.delete`,
  `albumAsset.create`, `albumAsset.delete`.
- Home Assistant: `server.about`, `asset.read`, `asset.view`,
  `asset.download`, `album.read`, `person.read`, `face.read`.

## Screensaver sync

`sync/` holds a CronJob (`immich-sync`, hourly at :17) that runs
`sync/scripts/sync.py` on a stock Python image (standard library only, the
scripts come from a ConfigMap).

**Input:** Home Assistant's Local To-do list `todo.screensaver_albums`
("Screensaver albums"), editable on the kitchen display. An item counts when
its summary or description holds a Google Photos share link
(`photos.app.goo.gl/...` or `photos.google.com/share/...`). The album can
belong to anyone; the link is all it needs.

- **Open item:** the album is mirrored into an Immich album named after the
  Google album, and into the combined album **Kitchen screensaver**, which is
  the one Home Assistant shows.
- **Checked off (or deleted):** its Immich album is deleted, and photos no
  other listed album holds go to Immich's trash.
- **Bare link as the summary:** the item is renamed to the album title and
  the link moves to its description.

Share links grant access to private photos: they live only in Home
Assistant's list and in the job's memory, never in git, logs or Immich.

**What it copies:** photos only (videos are skipped; motion photos come in
as their still). Each photo is Google's original download (`=d`, full
resolution with the camera's EXIF; Google drops the location when the album
owner hides it). Photos are downloaded one at a time with a pause, with
backoff on 429 and 5xx.

**State:** none of its own. Every synced asset carries a `gphotos-sync`
metadata entry with its Google item keys, and every synced album a
`[gphotos-sync:<id>]` marker in its description. A re-run only downloads
items whose key isn't on any asset yet; Immich's checksum check turns a
photo that's in two albums (or comes back after being removed) into the
same asset.

**Removals:** a photo removed from a Google album is removed from its Immich
album; an asset no listed album holds goes to the trash (30 days), unless
someone put it in an album the job doesn't manage. The job only removes or
deletes assets and albums carrying its markers.

**Failure:** when any listed album can't be read completely (link revoked,
Google changed its page, a page of items fails or an item doesn't parse), the
run still adds what it could, removes and deletes nothing, and exits 1. The
Job fails and `KubeJobFailed` fires (Telegram). A failed Job is kept for 2h,
so a one-off failure clears on its own and a lasting one keeps alerting.
Exit 2 means Home Assistant or Immich was unreachable before anything
changed.

**Big albums:** a run stops starting downloads after 45 minutes and the next
run continues.

Run it by hand, or see what it would do:

```bash
kubectl -n selfhosted create job --from=cronjob/immich-sync immich-sync-manual
kubectl -n selfhosted logs -f job/immich-sync-manual   # ends with a "summary:" line
# what it would do, without writing anything:
kubectl -n selfhosted create job --from=cronjob/immich-sync immich-sync-dry --dry-run=client -o yaml \
  | yq '.spec.template.spec.containers[0].env += [{"name": "DRY_RUN", "value": "1"}]' \
  | kubectl apply -f -
```

The synced albums belong to the screensaver account. To see them under your
own login, share them with your Immich user while logged in as screensaver.

Tests (no network): `cd kubernetes/apps/selfhosted/immich/sync/tests && python3 -m unittest -v`.

The Google scraping is derived from
[album_slideshow](https://github.com/eyalgal/album_slideshow)'s
`google_scraper.py` v1.16.0 (MIT, license in `sync/scripts/`), made strict:
upstream returns whatever it read, which a mirror would turn into deletions.

## Break-glass

- **OAuth broken (Cognito down, bad client secret):** log in with the
  break-glass admin's email and password on the normal login page.
- **Admin password lost:** reset it from the server pod. It prints a new
  password:
  ```bash
  kubectl -n selfhosted exec -it deploy/immich-server -- immich-admin reset-admin-password
  ```
  Store the new one in `~/.openclaw/credentials/immich-admin.env` (mode 600).
- **Turn OAuth off:** set `oauth.enabled: false` in `app/config/immich.yaml`
  and merge; the pod restarts on the ConfigMap change.
- **Restore the database:** the nightly dumps are in `/data/backups` on the
  library volume; Immich's admin page (Maintenance) restores one, or follow
  upstream's
  [backup and restore](https://docs.immich.app/administration/backup-and-restore)
  guide against the `immich-postgres-0` pod.

## Upgrades

Renovate groups the server and machine-learning images (`Immich`); minors are
reviewed (Immich migrates its database on start), patches automerge. Read the
release notes for breaking changes before merging a minor. The Postgres
image's major (17) only moves with a dump and restore.
