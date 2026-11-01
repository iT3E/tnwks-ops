"""Unit tests for the screensaver sync: Google parsing and the Immich diff.

Run from this directory: python3 -m unittest -v
No network: Google, Home Assistant and Immich are in-memory fakes.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from urllib.parse import parse_qs, unquote

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import gphotos  # noqa: E402
import sync  # noqa: E402

SHARE = "https://photos.app.goo.gl/AbCdEf123"
SHARE2 = "https://photos.google.com/share/AF1QipXYZ?key=KEY2"


# ---------------------------------------------------------------- fixtures


def photo_raw(key: str, w=4000, h=3000, size=123456, live=False):
    meta = {"15": 1, "525000002": [["ALBUM"]]}
    if live:
        meta["146008172"] = [None, 1633]
    return [key, [f"https://lh3.googleusercontent.com/pw/{key}=w{w}-h{h}", w, h, None, None, None, None, None,
                  [None, None, 1], [size]], 1789843143466, "dedup", -25200000, 1790048030255, ["x"], [[2]], 2, meta]


def video_raw(key: str):
    r = photo_raw(key)
    r[-1]["76647426"] = [8057, None, 1442, 1920]
    return r


def page_body(items, next_token, title="Trip"):
    inner = ["ALBUM", items, next_token, ["ALBUM", title, [1, 2]], None, None]
    outer = [["wrb.fr", "snAcKc", json.dumps(inner), None, None, None, "generic"], ["di", 99]]
    return ")]}'\n\n" + json.dumps(outer) + "\n25\n[[\"e\",4]]\n"


def share_html(album_key: str, auth: str, title="Trip"):
    return (f"<html><head><title>{title} - Google Photos</title></head><body><script>"
            f"AF_dataServiceRequests = {{'ds:0' : {{id:'snAcKc',ext: 1.0,request:[\"{album_key}\",null,null,"
            f"\"{auth}\"]}}}};</script></body></html>")


class FakeHeaders(dict):
    def get(self, k, d=None):
        for key, v in self.items():
            if key.lower() == k.lower():
                return v
        return d


class FakeGoogle:
    """Serves share pages, batchexecute pages and '=d' downloads."""

    def __init__(self):
        self.albums: dict[str, dict] = {}  # share url -> {key, auth, title, pages: [[raw...]]}
        self.fail_share: set[str] = set()
        self.same: dict[str, str] = {}  # media key -> key whose file it is
        self.downloads = 0

    def add(self, url, key, title, items, page_size=300):
        pages = [items[i:i + page_size] for i in range(0, len(items), page_size)] or [[]]
        self.albums[url] = {"key": key, "auth": "AUTH" + key, "title": title, "pages": pages}

    def request(self, method, url, data=None, headers=None, timeout=None):
        if url in self.albums:
            if url in self.fail_share:
                raise sync.HttpError(method, url, 404, b"gone")
            a = self.albums[url]
            return 200, FakeHeaders({"Content-Type": "text/html"}), share_html(a["key"], a["auth"], a["title"]).encode()
        if url.startswith(gphotos._BATCHEXECUTE):
            freq = json.loads(parse_qs(data.decode())["f.req"][0])
            album_key, page_id, _, auth = json.loads(freq[0][0][1])
            a = next(v for v in self.albums.values() if v["key"] == album_key)
            assert auth == a["auth"]
            idx = int(page_id) if page_id else 0
            nxt = str(idx + 1) if idx + 1 < len(a["pages"]) else None
            return 200, FakeHeaders(), page_body(a["pages"][idx], nxt, a["title"]).encode()
        if url.startswith("https://lh3.googleusercontent.com/") and url.endswith("=d"):
            self.downloads += 1
            key = url.split("/pw/")[1][:-2]
            return 200, FakeHeaders({"Content-Type": "image/jpeg",
                                     "Content-Disposition": f'attachment;filename="{key}.jpg"'}), \
                f"JPEG-{self.same.get(key, key)}".encode()
        raise AssertionError(f"unexpected request {method} {url}")


class FakeHA:
    def __init__(self, items):
        self._items = items
        self.renames = []

    def items(self):
        return list(self._items)

    def rename(self, item, title, description):
        self.renames.append((item.uid, title, description))
        item.summary, item.description = title, description


class FakeImmich:
    """Just enough of Immich: albums, assets with metadata, checksum dedupe, trash."""

    def __init__(self):
        self.albums_: dict[str, dict] = {}
        self.assets: dict[str, dict] = {}  # id -> {sha, meta, trashed, name}
        self.n = 0

    def _id(self):
        self.n += 1
        return f"00000000-0000-4000-8000-{self.n:012d}"

    def albums(self):
        return [dict(id=k, albumName=v["name"], description=v["desc"]) for k, v in self.albums_.items()]

    def create_album(self, name, description):
        aid = self._id()
        self.albums_[aid] = {"name": name, "desc": description, "assets": set()}
        return {"id": aid, "albumName": name, "description": description}

    def update_album(self, album_id, **f):
        self.albums_[album_id]["name"] = f["albumName"]

    def delete_album(self, album_id):
        del self.albums_[album_id]

    def albums_with_asset(self, asset_id):
        return [dict(id=k, albumName=v["name"], description=v["desc"]) for k, v in self.albums_.items()
                if asset_id in v["assets"]]

    def album_asset_ids(self, album_id):
        return {a for a in self.albums_[album_id]["assets"] if not self.assets[a]["trashed"]}

    def unalbumed_asset_ids(self):
        inalb = set().union(*[v["assets"] for v in self.albums_.values()]) if self.albums_ else set()
        return {a for a, v in self.assets.items() if not v["trashed"] and a not in inalb}

    def add_to_album(self, album_id, ids):
        self.albums_[album_id]["assets"] |= set(ids)

    def remove_from_album(self, album_id, ids):
        self.albums_[album_id]["assets"] -= set(ids)

    def media_keys(self, asset_id):
        m = self.assets[asset_id]["meta"]
        return None if m is None else list(m)

    def set_media_keys(self, asset_id, keys):
        self.assets[asset_id]["meta"] = sorted(set(keys))

    def check_duplicate(self, sha1):
        for aid, a in self.assets.items():
            if a["sha"] == sha1:
                return {"id": "x", "action": "reject", "reason": "duplicate", "assetId": aid,
                        "isTrashed": a["trashed"]}
        return {"id": "x", "action": "accept"}

    def upload(self, content, filename, mime, taken, media_key):
        import hashlib
        aid = self._id()
        self.assets[aid] = {"sha": hashlib.sha1(content).hexdigest(), "meta": [media_key], "trashed": False,
                            "name": filename}
        return aid, "created"

    def restore(self, ids):
        for i in ids:
            self.assets[i]["trashed"] = False

    def trash(self, ids):
        for i in ids:
            self.assets[i]["trashed"] = True
        for v in self.albums_.values():
            v["assets"] -= set(ids)

    # helpers for assertions
    def by_name(self, name):
        return next(v for v in self.albums_.values() if v["name"] == name)

    def live(self):
        return {a for a, v in self.assets.items() if not v["trashed"]}


def item(uid, summary, desc=None, status="needs_action"):
    return sync.TodoItem(uid=uid, summary=summary, status=status, description=desc)


def run(ha, im, g, **kw):
    s = sync.Sync(ha, im, g, combined_name="Kitchen screensaver", delay=0, sleep=lambda _x: None, **kw)
    code = s.run()
    return code, s


# ------------------------------------------------------------------ tests


class ParsingTests(unittest.TestCase):
    def test_share_urls(self):
        self.assertEqual(gphotos.find_share_urls(f"look {SHARE}, nice"), [SHARE])
        self.assertEqual(gphotos.find_share_urls(SHARE2), [SHARE2])
        self.assertEqual(gphotos.find_share_urls("https://photos.google.com/u/1/share/AF1Qx?key=K"),
                         ["https://photos.google.com/u/1/share/AF1Qx?key=K"])
        self.assertEqual(gphotos.find_share_urls("https://photos.google.com/album/xyz"), [])
        self.assertEqual(gphotos.find_share_urls(None), [])

    def test_keys_and_title(self):
        html = share_html("AF1QipAlbum", "AuthKey_1", "Beach &amp; Lake")
        self.assertEqual(gphotos.extract_keys(html), ("AF1QipAlbum", "AuthKey_1"))
        self.assertEqual(gphotos.extract_title(html), "Beach & Lake")
        with self.assertRaises(gphotos.ScrapeError):
            gphotos.extract_keys("<html>sign in</html>")

    def test_page_parse_and_items(self):
        raws, nxt, info = gphotos.parse_page(page_body([photo_raw("A1"), video_raw("V1")], "TOKEN"))
        self.assertEqual(len(raws), 2)
        self.assertEqual(nxt, "TOKEN")
        self.assertEqual(gphotos.album_title_from_info(info), "Trip")
        self.assertFalse(gphotos.is_video(raws[0]))
        self.assertTrue(gphotos.is_video(raws[1]))
        p = gphotos.parse_item(raws[0])
        self.assertEqual(p.media_key, "A1")
        self.assertEqual(p.original_url, "https://lh3.googleusercontent.com/pw/A1=d")
        self.assertEqual((p.width, p.height, p.byte_size), (4000, 3000, 123456))
        # motion photos are photos
        self.assertFalse(gphotos.is_video(photo_raw("L1", live=True)))

    def test_strict_parsing(self):
        with self.assertRaises(gphotos.ScrapeError):
            gphotos.parse_page(")]}'\n[[\"di\",1]]\n")
        with self.assertRaises(gphotos.ScrapeError):
            gphotos.parse_item(["K", ["not-a-url", 1, 2]])
        bad = photo_raw("B1")
        bad[1][0] = "https://evil.example.com/x"
        with self.assertRaises(gphotos.ScrapeError):
            gphotos.parse_item(bad)

    def test_fetch_album_paginates_and_skips_videos(self):
        g = FakeGoogle()
        g.add(SHARE, "AF1A", "Trip", [photo_raw(f"P-{i}") for i in range(650)] + [video_raw("V-1")])
        a = gphotos.fetch_album(g, SHARE)
        self.assertEqual((a.title, len(a.photos), a.videos_skipped), ("Trip", 650, 1))

    def test_empty_album_is_an_error(self):
        g = FakeGoogle()
        g.add(SHARE, "AF1A", "Trip", [video_raw("V-1")])
        with self.assertRaises(gphotos.ScrapeError):
            gphotos.fetch_album(g, SHARE)

    def test_content_disposition(self):
        self.assertEqual(gphotos.parse_content_disposition('attachment;filename="PXL_1.jpg"'), "PXL_1.jpg")
        self.assertEqual(gphotos.parse_content_disposition("attachment; filename*=UTF-8''a%20b.jpg"), "a b.jpg")
        self.assertIsNone(gphotos.parse_content_disposition(None))

    def test_tidy_item(self):
        self.assertEqual(sync.tidy_item(item("1", SHARE), "Trip"), ("Trip", SHARE))
        self.assertEqual(sync.tidy_item(item("1", f" {SHARE} ", "note"), "Trip"), ("Trip", f"{SHARE}\nnote"))
        self.assertIsNone(sync.tidy_item(item("1", "Trip", SHARE), "Trip"))
        self.assertIsNone(sync.tidy_item(item("1", f"Trip {SHARE}"), "Trip"))

    def test_plan_album_only_removes_ours(self):
        add, remove = sync.plan_album({"a", "b"}, {"b", "c", "foreign"}, {"a", "b", "c"})
        self.assertEqual((add, remove), ({"a"}, {"c"}))


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.g = FakeGoogle()
        self.g.add(SHARE, "AF1A", "Trip", [photo_raw(f"A-{i}") for i in range(5)] + [video_raw("A-v")])
        self.ha = FakeHA([item("u1", SHARE)])
        self.im = FakeImmich()

    def test_first_run_then_noop(self):
        code, s = run(self.ha, self.im, self.g)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.im.by_name("Trip")["assets"]), 5)
        self.assertEqual(len(self.im.by_name("Kitchen screensaver")["assets"]), 5)
        self.assertEqual(self.ha.renames, [("u1", "Trip", SHARE)])
        self.assertEqual(s.stats.uploaded, 5)
        downloads = self.g.downloads
        code, s = run(self.ha, self.im, self.g)
        self.assertEqual(code, 0)
        self.assertEqual(self.g.downloads, downloads, "re-run must not download again")
        self.assertEqual((s.stats.uploaded, s.stats.added, s.stats.removed, s.stats.trashed), (0, 0, 0, 0))

    def test_removed_from_google_is_removed_and_trashed(self):
        run(self.ha, self.im, self.g)
        self.g.add(SHARE, "AF1A", "Trip", [photo_raw(f"A-{i}") for i in range(3)])
        code, s = run(self.ha, self.im, self.g)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.im.by_name("Trip")["assets"]), 3)
        self.assertEqual(len(self.im.by_name("Kitchen screensaver")["assets"]), 3)
        self.assertEqual(s.stats.trashed, 2)
        self.assertEqual(len(self.im.live()), 3)

    def test_completed_item_drops_album_keeps_shared_photos(self):
        # second album shares one photo (same bytes, different Google key)
        self.g.add(SHARE2, "AF1B", "Lake", [photo_raw("B-0"), photo_raw("X-1")])
        self.g.same["X-1"] = "A-1"
        self.ha._items.append(item("u2", "Lake", SHARE2))
        run(self.ha, self.im, self.g)
        self.assertEqual(len(self.im.live()), 6, "X-1 and A-1 have the same bytes: one asset")
        self.assertEqual(len(self.im.by_name("Kitchen screensaver")["assets"]), 6)
        self.ha._items[0].status = "completed"
        code, s = run(self.ha, self.im, self.g)
        self.assertEqual(code, 0)
        self.assertNotIn("Trip", [v["name"] for v in self.im.albums_.values()])
        self.assertEqual(len(self.im.by_name("Kitchen screensaver")["assets"]), 2)
        self.assertEqual(len(self.im.live()), 2)
        self.assertEqual(s.stats.albums_deleted, 1)

    def test_scrape_failure_deletes_nothing(self):
        run(self.ha, self.im, self.g)
        before = (set(self.im.by_name("Trip")["assets"]), set(self.im.live()))
        self.g.fail_share.add(SHARE)
        code, s = run(self.ha, self.im, self.g)
        self.assertEqual(code, 1)
        self.assertEqual((set(self.im.by_name("Trip")["assets"]), set(self.im.live())), before)
        self.assertEqual((s.stats.removed, s.stats.trashed, s.stats.albums_deleted), (0, 0, 0))

    def test_layout_change_deletes_nothing(self):
        run(self.ha, self.im, self.g)
        before = set(self.im.live())
        broken = photo_raw("A-0")
        broken[1] = None  # Google reshuffles the item array
        self.g.add(SHARE, "AF1A", "Trip", [broken] + [photo_raw(f"A-{i}") for i in range(1, 5)])
        code, _s = run(self.ha, self.im, self.g)
        self.assertEqual(code, 1)
        self.assertEqual(set(self.im.live()), before)

    def test_foreign_assets_untouched(self):
        run(self.ha, self.im, self.g)
        foreign = self.im._id()
        self.im.assets[foreign] = {"sha": "f", "meta": None, "trashed": False, "name": "mine.jpg"}
        self.im.add_to_album(self.im.by_name("Kitchen screensaver") and
                             next(k for k, v in self.im.albums_.items() if v["name"] == "Kitchen screensaver"),
                             [foreign])
        other = self.im.create_album("Hand made", "")["id"]
        a0 = next(iter(self.im.by_name("Trip")["assets"]))
        self.im.add_to_album(other, [a0])
        self.ha._items[0].status = "completed"
        code, _s = run(self.ha, self.im, self.g)
        self.assertEqual(code, 0)
        self.assertIn(foreign, self.im.by_name("Kitchen screensaver")["assets"])
        self.assertIn(other, self.im.albums_)
        self.assertEqual(self.im.live(), {foreign, a0}, "a photo in an unmanaged album is kept")

    def test_readded_photo_comes_back_from_trash(self):
        run(self.ha, self.im, self.g)
        self.g.add(SHARE, "AF1A", "Trip", [photo_raw(f"A-{i}") for i in range(4)])
        run(self.ha, self.im, self.g)
        self.g.add(SHARE, "AF1A", "Trip", [photo_raw(f"A-{i}") for i in range(5)])
        code, s = run(self.ha, self.im, self.g)
        self.assertEqual(code, 0)
        self.assertEqual(s.stats.deduplicated, 1)
        self.assertEqual(len(self.im.by_name("Trip")["assets"]), 5)
        self.assertEqual(len(self.im.live()), 5)

    def test_album_renamed_in_google(self):
        run(self.ha, self.im, self.g)
        self.g.albums[SHARE]["title"] = "Trip 2"
        run(self.ha, self.im, self.g)
        self.assertEqual(len(self.im.by_name("Trip 2")["assets"]), 5)

    def test_unlisted_items_ignored_and_empty_list_clears(self):
        run(self.ha, self.im, self.g)
        self.ha._items = [item("u9", "milk")]
        code, s = run(self.ha, self.im, self.g)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.im.live()), 0)
        self.assertEqual(len(self.im.by_name("Kitchen screensaver")["assets"]), 0)

    def test_time_budget_defers(self):
        t = [0.0]
        s = sync.Sync(self.ha, self.im, self.g, combined_name="Kitchen screensaver", delay=0,
                      sleep=lambda _x: t.__setitem__(0, t[0] + 10), max_runtime=25, clock=lambda: t[0])
        self.assertEqual(s.run(), 0)
        self.assertEqual(s.stats.uploaded, 3)
        self.assertEqual(s.stats.deferred, 2)
        code, s2 = run(self.ha, self.im, self.g)
        self.assertEqual((code, s2.stats.uploaded), (0, 2))

    def test_dry_run_writes_nothing(self):
        code, _s = run(self.ha, self.im, self.g, dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual((self.im.albums_, self.im.assets, self.ha.renames), ({}, {}, []))


class HttpTests(unittest.TestCase):
    def test_retry_after(self):
        self.assertEqual(sync._retry_after("7"), 7.0)
        self.assertIsNone(sync._retry_after("Wed, 21 Oct 2026 07:28:00 GMT"))

    def test_error_messages_hide_share_links(self):
        for url in ("https://photos.google.com/share/ALBUM?key=SECRET", "https://photos.app.goo.gl/SECRET",
                    "https://lh3.googleusercontent.com/pw/SECRET=d"):
            self.assertNotIn("SECRET", str(sync.HttpError("GET", url, 404, b"")))
        self.assertIn("/api/assets", sync._redact("http://immich:2283/api/assets?x=SECRET"))
        self.assertNotIn("SECRET", sync._redact("http://immich:2283/api/assets?x=SECRET"))


if __name__ == "__main__":
    unittest.main()
