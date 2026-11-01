#!/usr/bin/env python3
"""Mirror the Google Photos shared albums listed in a Home Assistant to-do
list into Immich, for the kitchen screensaver (docs/apps/immich.md).

Each to-do item whose summary or description holds a photos.app.goo.gl or
photos.google.com/share link is an album:
  * needs_action: mirrored into an Immich album named after the Google album
    (photos only, the original download), and into the combined album;
  * completed: no longer synced; its Immich album is deleted and photos no
    other synced album holds go to Immich's trash.
A bare-link summary is renamed to the album title with the link moved to the
description.

Safety: everything happens as a dedicated Immich account and only touches
albums carrying the [gphotos-sync:...] marker and assets carrying the
gphotos-sync metadata key. If any listed album can't be read completely, the
run adds what it can, removes and deletes nothing, and exits non-zero.
Deleted photos sit in Immich's trash for 30 days.

Standard library only: runs on a stock python image with this directory
mounted from a ConfigMap.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import gphotos

log = logging.getLogger("gphotos-sync")

MARKER_RE = re.compile(r"\[gphotos-sync:([a-z0-9-]+)\]")
COMBINED = "combined"
META_KEY = "gphotos-sync"
RETRY_STATUSES = {429, 500, 502, 503, 504}


# --------------------------------------------------------------------- HTTP


class HttpError(Exception):
    def __init__(self, method: str, url: str, status: int, body: bytes):
        self.status = status
        self.body = body
        super().__init__(f"{method} {_redact(url)} -> HTTP {status}: {body[:300]!r}")


class Http:
    """urllib with retries: 429/5xx and network errors back off and retry."""

    def __init__(self, attempts: int = 5, backoff: float = 2.0, timeout: float = 60.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.attempts = attempts
        self.backoff = backoff
        self.timeout = timeout
        self.sleep = sleep

    def request(self, method: str, url: str, data: bytes | None = None,
                headers: dict[str, str] | None = None, timeout: float | None = None):
        delay = self.backoff
        for attempt in range(1, self.attempts + 1):
            req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                    return resp.status, resp.headers, resp.read()
            except urllib.error.HTTPError as err:
                body = err.read() if err.fp else b""
                if err.code not in RETRY_STATUSES or attempt == self.attempts:
                    raise HttpError(method, url, err.code, body) from None
                wait = _retry_after(err.headers.get("Retry-After")) or delay
                log.warning("%s %s: HTTP %s, retry %d in %.0fs", method, _redact(url), err.code, attempt, wait)
            except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError) as err:
                if attempt == self.attempts:
                    raise
                wait = delay
                log.warning("%s %s: %s, retry %d in %.0fs", method, _redact(url), err, attempt, wait)
            self.sleep(min(wait, 300))
            delay *= 2
        raise AssertionError("unreachable")


def _retry_after(value: str | None) -> float | None:
    if value and value.strip().isdigit():
        return float(value.strip())
    return None


def _redact(url: str) -> str:
    """A URL safe for logs. Google share links and image URLs grant access to
    the photos, so only their host is kept; elsewhere the query goes."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if host == "photos.app.goo.gl" or host.endswith("googleusercontent.com") or (
            host == "photos.google.com" and "/share/" in parts.path):
        return f"{parts.scheme}://{host}/(redacted)"
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


# ----------------------------------------------------------- Home Assistant


@dataclass
class TodoItem:
    uid: str
    summary: str
    status: str
    description: str | None

    @property
    def share_url(self) -> str | None:
        urls = gphotos.find_share_urls(self.summary) + gphotos.find_share_urls(self.description)
        return urls[0] if urls else None


class HomeAssistant:
    def __init__(self, http: Http, base: str, token: str, entity: str):
        self.http, self.base, self.token, self.entity = http, base.rstrip("/"), token, entity

    def _post(self, path: str, body: dict) -> Any:
        _s, _h, raw = self.http.request(
            "POST", self.base + path, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        return json.loads(raw) if raw else None

    def items(self) -> list[TodoItem]:
        resp = self._post("/api/services/todo/get_items?return_response", {"entity_id": self.entity})
        try:
            raw_items = resp["service_response"][self.entity]["items"]
        except (KeyError, TypeError) as err:
            raise RuntimeError(f"Home Assistant returned no items for {self.entity}") from err
        return [
            TodoItem(uid=i["uid"], summary=i.get("summary") or "", status=i.get("status") or "",
                     description=i.get("description"))
            for i in raw_items
        ]

    def rename(self, item: TodoItem, title: str, description: str) -> None:
        self._post("/api/services/todo/update_item",
                   {"entity_id": self.entity, "item": item.uid, "rename": title, "description": description})


def tidy_item(item: TodoItem, title: str | None) -> tuple[str, str] | None:
    """(new summary, new description) when the summary is just the link."""
    url = item.share_url
    if not title or not url or item.summary.strip() != url:
        return None
    desc = (item.description or "").strip()
    if url not in desc:
        desc = f"{url}\n{desc}".strip()
    return title, desc


# ------------------------------------------------------------------- Immich


class Immich:
    def __init__(self, http: Http, base: str, api_key: str):
        self.http, self.base, self.key = http, base.rstrip("/") + "/api", api_key

    def call(self, method: str, path: str, body: Any = None) -> Any:
        headers = {"x-api-key": self.key, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        _s, _h, raw = self.http.request(method, self.base + path, data=data, headers=headers)
        return json.loads(raw) if raw else None

    def albums(self) -> list[dict]:
        return self.call("GET", "/albums?isOwned=true")

    def create_album(self, name: str, description: str) -> dict:
        return self.call("POST", "/albums", {"albumName": name, "description": description})

    def update_album(self, album_id: str, **fields: Any) -> None:
        self.call("PATCH", f"/albums/{album_id}", fields)

    def delete_album(self, album_id: str) -> None:
        self.call("DELETE", f"/albums/{album_id}")

    def albums_with_asset(self, asset_id: str) -> list[dict]:
        return self.call("GET", f"/albums?assetId={asset_id}")

    def _search(self, filt: dict) -> list[dict]:
        out: list[dict] = []
        body: dict[str, Any] = {"filter": filt, "size": 1000}
        while True:
            page = self.call("POST", "/search/metadata", body)["assets"]
            out.extend(page["items"])
            cursor = page.get("nextCursor")
            if not cursor:
                return out
            body["cursor"] = cursor

    def album_asset_ids(self, album_id: str) -> set[str]:
        return {a["id"] for a in self._search({"albumIds": {"any": [album_id]}})}

    def unalbumed_asset_ids(self) -> set[str]:
        return {a["id"] for a in self._search({"hasAlbums": {"eq": False}})}

    def add_to_album(self, album_id: str, ids: Iterable[str]) -> None:
        for chunk in _chunks(sorted(ids), 500):
            self.call("PUT", f"/albums/{album_id}/assets", {"ids": chunk})

    def remove_from_album(self, album_id: str, ids: Iterable[str]) -> None:
        for chunk in _chunks(sorted(ids), 500):
            self.call("DELETE", f"/albums/{album_id}/assets", {"ids": chunk})

    def media_keys(self, asset_id: str) -> list[str] | None:
        """The Google media keys this job recorded on the asset, or None if it isn't ours."""
        for entry in self.call("GET", f"/assets/{asset_id}/metadata") or []:
            if entry.get("key") == META_KEY:
                keys = (entry.get("value") or {}).get("mediaKeys")
                return [k for k in keys if isinstance(k, str)] if isinstance(keys, list) else []
        return None

    def set_media_keys(self, asset_id: str, keys: list[str]) -> None:
        self.call("PUT", f"/assets/{asset_id}/metadata",
                  {"items": [{"key": META_KEY, "value": {"mediaKeys": sorted(set(keys))}}]})

    def check_duplicate(self, sha1_hex: str) -> dict:
        res = self.call("POST", "/assets/bulk-upload-check",
                        {"assets": [{"id": "x", "checksum": sha1_hex}]})["results"][0]
        return res

    def upload(self, content: bytes, filename: str, mime: str, taken: str, media_key: str) -> tuple[str, str]:
        boundary = uuid.uuid4().hex
        fields = {
            "fileCreatedAt": taken,
            "fileModifiedAt": taken,
            "filename": filename,
            "metadata": json.dumps([{"key": META_KEY, "value": {"mediaKeys": [media_key]}}]),
        }
        parts = []
        for name, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        safe_name = filename.replace('"', "_")
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="assetData"; filename="{safe_name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode() + content + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode())
        _s, _h, raw = self.http.request(
            "POST", self.base + "/assets", data=b"".join(parts), timeout=300,
            headers={"x-api-key": self.key, "Accept": "application/json",
                     "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        res = json.loads(raw)
        return res["id"], res["status"]

    def restore(self, ids: list[str]) -> None:
        self.call("POST", "/trash/restore/assets", {"ids": ids})

    def trash(self, ids: Iterable[str]) -> None:
        for chunk in _chunks(sorted(ids), 500):
            self.call("DELETE", "/assets", {"ids": chunk, "force": False})


def _chunks(items: list[str], n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


# --------------------------------------------------------------------- sync


def source_id(album_key: str) -> str:
    return hashlib.sha256(album_key.encode()).hexdigest()[:16]


def marker_of(description: str | None) -> str | None:
    m = MARKER_RE.search(description or "")
    return m.group(1) if m else None


def album_description(sid: str) -> str:
    return ("Mirrored from a Google Photos shared album by the kitchen screensaver sync "
            "(Home Assistant list Screensaver albums). Changes here are overwritten.\n"
            f"[gphotos-sync:{sid}]")


COMBINED_DESCRIPTION = ("Every photo the kitchen screensaver shows: all albums on the Home Assistant "
                        "list Screensaver albums. Kept by the screensaver sync; changes here are "
                        f"overwritten.\n[gphotos-sync:{COMBINED}]")


def taken_iso(ms: int | None) -> str:
    if ms is None:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


@dataclass
class Stats:
    albums: int = 0
    photos: int = 0
    videos_skipped: int = 0
    uploaded: int = 0
    deduplicated: int = 0
    added: int = 0
    removed: int = 0
    albums_deleted: int = 0
    trashed: int = 0
    renamed_items: int = 0
    deferred: int = 0


def plan_album(want: set[str], current: set[str], mine: set[str]) -> tuple[set[str], set[str]]:
    """(to add, to remove) for one album. Only our own assets are ever removed."""
    return want - current, (current & mine) - want


class Sync:
    def __init__(self, ha: HomeAssistant, immich: Immich, google: Http, *, combined_name: str,
                 dry_run: bool = False, delay: float = 1.0, max_runtime: float = 2700,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.ha, self.immich, self.google = ha, immich, google
        self.combined_name, self.dry_run, self.delay = combined_name, dry_run, delay
        self.deadline = clock() + max_runtime
        self.clock, self.sleep = clock, sleep
        self.errors: list[str] = []
        self.stats = Stats()

    def error(self, msg: str) -> None:
        log.error(msg)
        self.errors.append(msg)

    # ---- writes, skipped in dry-run
    def _w(self, what: str, fn: Callable[[], Any]) -> Any:
        if self.dry_run:
            log.info("dry-run: would %s", what)
            return None
        return fn()

    def run(self) -> int:
        items = self.ha.items()
        listed = [i for i in items if i.share_url]
        active = [i for i in listed if i.status == "needs_action"]
        log.info("list: %d items, %d with an album link, %d active", len(items), len(listed), len(active))

        scraped: dict[str, tuple[gphotos.Album, TodoItem]] = {}
        read_failures = 0
        for item in active:
            try:
                album = gphotos.fetch_album(self.google, item.share_url)
            except Exception as err:  # noqa: BLE001 - every failure counts the same
                read_failures += 1
                self.error(f"album for list item {item.summary[:60]!r}: can't read it: {err}")
                continue
            sid = source_id(album.album_key)
            if sid in scraped:
                log.warning("list item %r is the same album as another item; skipping it", item.summary[:60])
                continue
            log.info("google album %r (%s): %d photos, %d videos skipped",
                     album.title, sid, len(album.photos), album.videos_skipped)
            scraped[sid] = (album, item)
            self.stats.photos += len(album.photos)
            self.stats.videos_skipped += album.videos_skipped
            tidy = tidy_item(item, album.title)
            if tidy:
                try:
                    self._w(f"rename list item to {tidy[0]!r}", lambda: self.ha.rename(item, *tidy))
                    self.stats.renamed_items += 1
                except Exception as err:  # noqa: BLE001
                    self.error(f"renaming list item {tidy[0]!r}: {err}")
        complete = read_failures == 0
        self.stats.albums = len(scraped)

        # ---- what's in Immich now
        ours: dict[str, dict] = {}
        combined: dict | None = None
        for a in self.immich.albums():
            m = marker_of(a.get("description"))
            if m == COMBINED and combined is None:
                combined = a
            elif m and m != COMBINED:
                ours[m] = a
        if combined is None:
            combined = self._w(f"create album {self.combined_name!r}",
                               lambda: self.immich.create_album(self.combined_name, COMBINED_DESCRIPTION)) \
                or {"id": "dry-run-combined", "albumName": self.combined_name}
        members: dict[str, set[str]] = {}
        for a in [*ours.values(), combined]:
            members[a["id"]] = set() if a["id"].startswith("dry-run") else self.immich.album_asset_ids(a["id"])

        keys_of: dict[str, list[str] | None] = {}
        for aid in set().union(*members.values()):
            keys_of[aid] = self.immich.media_keys(aid)
        mine = {aid for aid, keys in keys_of.items() if keys is not None}
        by_key = {k: aid for aid, keys in keys_of.items() if keys for k in keys}
        log.info("immich: %d synced albums, %d assets in them (%d ours)", len(ours), len(keys_of), len(mine))

        # ---- each listed album
        desired: set[str] = set()
        for sid, (album, item) in scraped.items():
            title = album.title or item.summary.strip() or sid
            ia = ours.get(sid)
            if ia is None:
                ia = self._w(f"create album {title!r}",
                             lambda: self.immich.create_album(title, album_description(sid))) \
                    or {"id": f"dry-run-{sid}", "albumName": title}
                ours[sid] = ia
                members[ia["id"]] = set()
            elif ia.get("albumName") != title:
                self._w(f"rename album {ia.get('albumName')!r} to {title!r}",
                        lambda: self.immich.update_album(ia["id"], albumName=title))
            want: set[str] = set()
            for photo in album.photos:
                aid = by_key.get(photo.media_key)
                if aid is None:
                    if self.clock() > self.deadline:
                        self.stats.deferred += 1
                        continue
                    try:
                        aid = self.import_photo(photo)
                    except Exception as err:  # noqa: BLE001
                        self.error(f"photo {photo.media_key} in {title!r}: {err}")
                        continue
                    if aid is None:  # dry-run
                        continue
                    by_key[photo.media_key] = aid
                    mine.add(aid)
                want.add(aid)
            add, remove = plan_album(want, members[ia["id"]], mine)
            self.apply_album(ia, add, remove)
            members[ia["id"]] = (members[ia["id"]] - remove) | add
            desired |= want

        # ---- combined album, dropped albums, orphans
        current = members[combined["id"]]
        add, remove = plan_album(desired, current, mine)
        if not complete:
            remove = set()
        self.apply_album(combined, add, remove)

        if not complete:
            self.error(f"{read_failures} listed album(s) couldn't be read: nothing removed or deleted this run")
            return 1

        for sid, ia in list(ours.items()):
            if sid not in scraped:
                log.info("album %r is no longer listed: deleting it", ia.get("albumName"))
                self._w(f"delete album {ia.get('albumName')!r}", lambda: self.immich.delete_album(ia["id"]))
                self.stats.albums_deleted += 1

        orphans = mine - desired
        for aid in self.immich.unalbumed_asset_ids() if not self.dry_run else set():
            if aid not in desired and aid not in keys_of:
                if self.immich.media_keys(aid) is not None:
                    orphans.add(aid)
        if orphans:
            keep = set()
            for aid in orphans:
                # Never delete an asset someone put in an album we don't manage.
                for a in self.immich.albums_with_asset(aid) or []:
                    if marker_of(a.get("description")) is None:
                        keep.add(aid)
            if keep:
                log.warning("%d unsynced photos are in albums we don't manage; leaving them", len(keep))
            orphans -= keep
            if orphans:
                log.info("moving %d photos no synced album holds to the trash", len(orphans))
                self._w(f"trash {len(orphans)} assets", lambda: self.immich.trash(orphans))
                self.stats.trashed += len(orphans)
        return 1 if self.errors else 0

    def apply_album(self, album: dict, add: set[str], remove: set[str]) -> None:
        name = album.get("albumName")
        if add:
            self._w(f"add {len(add)} to {name!r}", lambda: self.immich.add_to_album(album["id"], add))
            self.stats.added += len(add)
        if remove:
            log.info("removing %d photos from %r (gone from Google)", len(remove), name)
            self._w(f"remove {len(remove)} from {name!r}", lambda: self.immich.remove_from_album(album["id"], remove))
            self.stats.removed += len(remove)

    def import_photo(self, photo: gphotos.Photo) -> str | None:
        content, filename, mime = gphotos.download_original(self.google, photo)
        self.sleep(self.delay)
        sha1 = hashlib.sha1(content).hexdigest()
        if self.dry_run:
            log.info("dry-run: would upload %s (%d bytes)", filename, len(content))
            return None
        dup = self.immich.check_duplicate(sha1)
        if dup.get("action") == "reject" and dup.get("reason") == "duplicate" and dup.get("assetId"):
            aid = dup["assetId"]
            self.adopt(aid, photo.media_key, trashed=bool(dup.get("isTrashed")))
            self.stats.deduplicated += 1
            return aid
        aid, status = self.immich.upload(content, filename, mime, taken_iso(photo.captured_ms), photo.media_key)
        if status == "duplicate":
            self.adopt(aid, photo.media_key, trashed=False)
            self.stats.deduplicated += 1
        else:
            self.stats.uploaded += 1
        return aid

    def adopt(self, aid: str, media_key: str, trashed: bool) -> None:
        """An existing asset (same file) now also stands for this Google item."""
        if trashed:
            self.immich.restore([aid])
        keys = self.immich.media_keys(aid) or []
        if media_key not in keys:
            self.immich.set_media_keys(aid, keys + [media_key])


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    env = os.environ
    try:
        ha = HomeAssistant(Http(), env.get("HASS_URL", "http://home-assistant.home-automation.svc.cluster.local:8123"),
                           env["HASS_TOKEN"], env.get("TODO_ENTITY", "todo.screensaver_albums"))
        immich = Immich(Http(), env.get("IMMICH_URL", "http://immich-server.selfhosted.svc.cluster.local:2283"),
                        env["IMMICH_API_KEY"])
        sync = Sync(ha, immich, Http(attempts=6, backoff=5.0),
                    combined_name=env.get("COMBINED_ALBUM_NAME", "Kitchen screensaver"),
                    dry_run=env.get("DRY_RUN", "") in ("1", "true", "yes"),
                    delay=float(env.get("DOWNLOAD_DELAY", "1.0")),
                    max_runtime=float(env.get("MAX_RUNTIME", "2700")))
    except KeyError as err:
        log.error("missing setting %s", err)
        return 2
    try:
        code = sync.run()
    except Exception as err:  # noqa: BLE001 - HA or Immich unreachable, unexpected API answer
        log.exception("sync aborted: %s", err)
        code = 2
    s = sync.stats
    log.info("summary: albums=%d photos=%d videos_skipped=%d uploaded=%d deduplicated=%d added=%d removed=%d "
             "albums_deleted=%d trashed=%d items_renamed=%d deferred=%d errors=%d exit=%d",
             s.albums, s.photos, s.videos_skipped, s.uploaded, s.deduplicated, s.added, s.removed,
             s.albums_deleted, s.trashed, s.renamed_items, s.deferred, len(sync.errors), code)
    if s.deferred:
        log.info("%d photos left for the next run (time budget)", s.deferred)
    return code


if __name__ == "__main__":
    sys.exit(main())
