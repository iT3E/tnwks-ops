"""Tests for the kitchen screensaver service: python3 -m unittest discover -s tests
(run from kubernetes/apps/selfhosted/kitchen-screensaver/app). Fakes stand in for
Home Assistant and Immich; nothing here talks to the network beyond localhost."""

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import server  # noqa: E402

A1 = "11111111-1111-1111-1111-111111111111"
A2 = "22222222-2222-2222-2222-222222222222"
A3 = "33333333-3333-3333-3333-333333333333"


def cfg(**kw):
    env = {"AUTH_SECRET": "s3cret", "HASS_URL": "http://ha.invalid", "HASS_TOKEN": "t", "IMMICH_URL": "http://im.invalid",
           "IMMICH_API_KEY": "k", "INFO_TTL": "0", "ALBUM_TTL": "0", "FORECAST_TTL": "0"}
    env.update(kw)
    return server.Config(env)


class FakeHA:
    def __init__(self):
        self.states = {
            "weather.forecast_home": {"state": "rainy", "attributes": {"temperature": 51.6, "temperature_unit": "°F"}},
            "counter.stars_kid1": {"state": "12", "attributes": {"friendly_name": "Kid One"}},
            "counter.stars_kid2": {"state": "7", "attributes": {"friendly_name": "Kid Two"}},
            "input_select.kitchen_theme": {"state": "halloween", "attributes": {}},
        }
        self.cal = {
            "calendar.family": [{"summary": "Soccer", "start": {"dateTime": "2026-10-31T15:00:00-07:00"},
                                 "end": {"dateTime": "2026-10-31T16:00:00-07:00"}, "description": "x", "location": "y"}],
            "calendar.mealie_dinner": [{"summary": "Tacos", "start": {"date": "2026-10-31"}, "end": {"date": "2026-11-01"}}],
        }
        self.forecast = [{"datetime": "2026-10-31T19:00:00+00:00", "temperature": 58, "templow": 41},
                         {"datetime": "2026-11-01T20:00:00+00:00", "temperature": 55, "templow": 40}]
        self.fail = set()
        self.calls = []

    def state(self, e):
        if e in self.fail:
            raise urllib.error.URLError("down")
        return self.states.get(e)

    def events(self, e, start, end):
        self.calls.append((e, start, end))
        if e in self.fail:
            raise urllib.error.URLError("down")
        return self.cal.get(e, [])

    def daily_forecast(self, e):
        if "forecast" in self.fail:
            raise server.WebSocketError("nope")
        return self.forecast


class FakeImmich:
    def __init__(self):
        self.assets = [
            {"id": A1, "type": "IMAGE", "width": 3000, "height": 4000, "isTrashed": False, "exifInfo": {"orientation": "1"}},
            {"id": A2, "type": "IMAGE", "width": None, "height": None, "isTrashed": False,
             "exifInfo": {"exifImageWidth": 4000, "exifImageHeight": 3000, "orientation": "6"}},
            {"id": A3, "type": "IMAGE", "width": 4000, "height": 3000, "isTrashed": True},
            {"id": "not-a-uuid", "type": "IMAGE", "width": 1, "height": 1},
        ]
        self.face_calls = 0

    def album_id(self, name):
        return "album-1" if name == "Kitchen screensaver" else None

    def album_assets(self, aid):
        return self.assets

    def faces(self, aid):
        self.face_calls += 1
        if aid == A1:
            return [{"boundingBoxX1": 100, "boundingBoxY1": 100, "boundingBoxX2": 200, "boundingBoxY2": 220, "imageWidth": 1000, "imageHeight": 1000},
                    {"boundingBoxX1": 500, "boundingBoxY1": 500, "boundingBoxX2": 800, "boundingBoxY2": 900, "imageWidth": 1000, "imageHeight": 1000}]
        return []

    def thumbnail(self, aid, size):
        return (f"jpeg:{aid}:{size}".encode(), "image/jpeg")


class Units(unittest.TestCase):
    def test_face_boxes_normalized_biggest_first(self):
        b = server.face_boxes(FakeImmich().faces(A1))
        self.assertEqual(b[0], [0.5, 0.5, 0.8, 0.9])
        self.assertEqual(b[1], [0.1, 0.1, 0.2, 0.22])
        self.assertEqual(server.face_boxes([{"imageWidth": 0}]), [])

    def test_oriented_size(self):
        self.assertEqual(server.oriented_size({"width": 30, "height": 40}), (30, 40))
        self.assertEqual(server.oriented_size({"exifInfo": {"exifImageWidth": 40, "exifImageHeight": 30, "orientation": "6"}}), (30, 40))

    def test_pick_today_uses_the_display_zone(self):
        f = [{"datetime": "2026-11-01T02:00:00+00:00", "temperature": 1}, {"datetime": "2026-11-01T19:00:00+00:00", "temperature": 2}]
        # 02:00 UTC on Nov 1 is still Oct 31 in Los Angeles.
        self.assertEqual(server.pick_today(f, datetime.fromisoformat("2026-10-31T00:00:00-07:00"))["temperature"], 1)
        self.assertEqual(server.pick_today(f, datetime.fromisoformat("2026-11-01T00:00:00-07:00"))["temperature"], 2)
        self.assertIsNone(server.pick_today(f, datetime.fromisoformat("2026-11-05T00:00:00-08:00")))

    def test_iso_needs_offset(self):
        self.assertIsNone(server._iso("2026-10-31T00:00:00"))
        self.assertIsNotNone(server._iso("2026-10-31T00:00:00Z"))
        self.assertIsNone(server._iso("garbage"))

    def test_image_cache_evicts_oldest(self):
        c = server.ImageCache(10)
        c.put("a", (b"12345", "x"))
        c.put("b", (b"12345", "x"))
        c.put("c", (b"123", "x"))
        self.assertIsNone(c.get("a"))
        self.assertIsNotNone(c.get("b"))
        c.put("big", (b"x" * 11, "x"))
        self.assertIsNone(c.get("big"))


class Info(unittest.TestCase):
    def test_build(self):
        ha = FakeHA()
        out = server.InfoService(cfg(), ha).get("2026-10-31T00:00:00-07:00", "2026-11-01T00:00:00-07:00")
        self.assertEqual(out["weather"], {"condition": "rainy", "temp": 52, "unit": "°F", "high": 58, "low": 41})
        self.assertEqual(out["events"], [{"title": "Soccer", "start": {"dateTime": "2026-10-31T15:00:00-07:00"},
                                          "end": {"dateTime": "2026-10-31T16:00:00-07:00"}}])
        self.assertEqual(out["dinner"][0]["title"], "Tacos")
        self.assertEqual(out["stars"], [{"key": "kid1", "name": "Kid One", "count": 12}, {"key": "kid2", "name": "Kid Two", "count": 7}])
        self.assertEqual(out["theme"], "halloween")
        self.assertEqual(out["errors"], [])
        self.assertIn(("calendar.family", "2026-10-31T00:00:00-07:00", "2026-11-01T00:00:00-07:00"), ha.calls)

    def test_failures_degrade(self):
        ha = FakeHA()
        ha.fail = {"calendar.family", "forecast", "counter.stars_kid2"}
        out = server.InfoService(cfg(), ha).get("2026-10-31T00:00:00-07:00", "2026-11-01T00:00:00-07:00")
        self.assertIsNone(out["events"])  # unknown, not "nothing today"
        self.assertEqual(out["weather"]["high"], None)
        self.assertEqual([s["key"] for s in out["stars"]], ["kid1"])
        self.assertIn("calendar:calendar.family", out["errors"])

    def test_cached(self):
        ha = FakeHA()
        svc = server.InfoService(cfg(INFO_TTL="60"), ha)
        svc.get("2026-10-31T00:00:00-07:00", "2026-11-01T00:00:00-07:00")
        ha.states["counter.stars_kid1"]["state"] = "13"
        out = svc.get("2026-10-31T00:00:00-07:00", "2026-11-01T00:00:00-07:00")
        self.assertEqual(out["stars"][0]["count"], 12)


class Photos(unittest.TestCase):
    def test_list(self):
        im = FakeImmich()
        svc = server.PhotoService(cfg(), im)
        ps = svc.get()
        self.assertEqual([p["id"] for p in ps], [A1, A2])
        self.assertEqual((ps[1]["w"], ps[1]["h"]), (3000, 4000))
        self.assertEqual(ps[0]["faces"][0], [0.5, 0.5, 0.8, 0.9])
        self.assertTrue(svc.known(A1))
        self.assertFalse(svc.known(A3))
        n = im.face_calls
        svc.get()  # faces are cached; an empty result is asked again only after an hour
        self.assertEqual(im.face_calls, n)

    def test_missing_album_keeps_old_list(self):
        im = FakeImmich()
        svc = server.PhotoService(cfg(), im)
        svc.get()
        svc.cfg.album_name = "gone"
        svc.loaded = 0.0001
        self.assertEqual(len(svc.get()), 2)


class HTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False)
        cls.page.write("<!doctype html><p>page</p>")
        cls.page.close()
        cls.app = server.App(cfg(PAGE=cls.page.name), ha=FakeHA(), immich=FakeImmich())
        cls.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.app))
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        os.unlink(cls.page.name)

    def req(self, path, method="GET", headers=None):
        r = urllib.request.Request(self.base + path, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(r, timeout=5) as f:
                return f.status, dict(f.headers), f.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_health_is_open(self):
        self.assertEqual(self.req("/healthz")[0], 200)

    def test_page_needs_secret(self):
        s, _, body = self.req("/")
        self.assertEqual(s, 401)
        self.assertIn(b"Unauthorized", body)
        self.assertEqual(self.req("/?authsecret=wrong")[0], 401)
        s, h, body = self.req("/?authsecret=s3cret")
        self.assertEqual((s, body), (200, b"<!doctype html><p>page</p>"))
        self.assertIn("connect-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(h["Referrer-Policy"], "no-referrer")
        self.assertEqual(h["Cache-Control"], "no-store")

    def test_head_for_the_apps_reachability_check(self):
        s, h, body = self.req("/?authsecret=s3cret", method="HEAD")
        self.assertEqual((s, body), (200, b""))

    def test_api_bearer(self):
        self.assertEqual(self.req("/api/photos")[0], 401)
        s, _, body = self.req("/api/photos", headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(s, 200)
        self.assertEqual(len(json.loads(body)["photos"]), 2)

    def test_info_window_checked(self):
        h = {"Authorization": "Bearer s3cret"}
        self.assertEqual(self.req("/api/info", headers=h)[0], 400)
        self.assertEqual(self.req("/api/info?start=2026-10-31T00:00:00&end=2026-11-01T00:00:00", headers=h)[0], 400)
        self.assertEqual(self.req("/api/info?start=2026-10-31T00:00:00-07:00&end=2026-11-09T00:00:00-08:00", headers=h)[0], 400)
        s, _, body = self.req("/api/info?start=2026-10-31T00%3A00%3A00-07%3A00&end=2026-11-01T00%3A00%3A00-07%3A00", headers=h)
        self.assertEqual(s, 200)
        self.assertEqual(json.loads(body)["theme"], "halloween")

    def test_images_only_from_the_album(self):
        h = {"Authorization": "Bearer s3cret"}
        s, hd, body = self.req(f"/img/{A1}?size=preview", headers=h)
        self.assertEqual((s, body, hd["Content-Type"]), (200, f"jpeg:{A1}:preview".encode(), "image/jpeg"))
        self.assertEqual(self.req(f"/img/{A3}", headers=h)[0], 404)  # trashed, not in the list
        self.assertEqual(self.req(f"/img/{A1}?size=original", headers=h)[0], 404)
        self.assertEqual(self.req("/img/../api/info", headers=h)[0], 404)
        self.assertEqual(self.req(f"/img/{A1}")[0], 401)


class FakeHAWebSocket(threading.Thread):
    """Accepts one connection and plays Home Assistant's side of a forecast subscription."""

    def __init__(self, token="t", fail_auth=False):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.token, self.fail_auth = token, fail_auth
        self.got = []

    def frame(self, c, obj, op=0x1):
        p = json.dumps(obj).encode() if op == 0x1 else obj
        n = len(p)
        head = bytes([0x80 | op]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack("!H", n))
        c.sendall(head + p)

    def read(self, c):
        b0, b1 = c.recv(1)[0], c.recv(1)[0]
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack("!H", c.recv(2))[0]
        mask = c.recv(4)
        data = b""
        while len(data) < n:
            data += c.recv(n - len(data))
        data = bytes(x ^ mask[i % 4] for i, x in enumerate(data))
        return b0 & 0x0F, data

    def run(self):
        c, _ = self.sock.accept()
        req = b""
        while b"\r\n\r\n" not in req:
            req += c.recv(1024)
        key = [l.split(b":", 1)[1].strip() for l in req.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key")][0]
        acc = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + acc + b"\r\n\r\n")
        self.frame(c, {"type": "auth_required"})
        _, d = self.read(c)
        auth = json.loads(d)
        if self.fail_auth or auth.get("access_token") != self.token:
            self.frame(c, {"type": "auth_invalid"})
            c.close()
            return
        self.frame(c, {"type": "auth_ok"})
        _, d = self.read(c)
        sub = json.loads(d)
        self.got.append(sub)
        self.frame(c, b"hi", op=0x9)  # a ping in between
        self.read(c)  # its pong
        self.frame(c, {"id": sub["id"], "type": "result", "success": True, "result": None})
        big = [{"datetime": f"2026-11-{d:02d}T19:00:00+00:00", "temperature": 50 + d, "templow": 30, "pad": "x" * 40} for d in range(1, 8)]
        self.frame(c, {"id": sub["id"], "type": "event", "event": {"type": "daily", "forecast": big}})
        try:
            self.read(c)  # close
        except (OSError, IndexError):
            pass
        c.close()


class WebSocket(unittest.TestCase):
    def test_forecast(self):
        fake = FakeHAWebSocket()
        fake.start()
        ha = server.HomeAssistant(cfg(HASS_URL=f"http://127.0.0.1:{fake.port}"))
        f = ha.daily_forecast("weather.forecast_home")
        self.assertEqual(len(f), 7)
        self.assertEqual(f[0]["temperature"], 51)
        self.assertEqual(fake.got[0]["type"], "weather/subscribe_forecast")
        self.assertEqual(fake.got[0]["forecast_type"], "daily")

    def test_bad_token(self):
        fake = FakeHAWebSocket(fail_auth=True)
        fake.start()
        ha = server.HomeAssistant(cfg(HASS_URL=f"http://127.0.0.1:{fake.port}"))
        with self.assertRaises(server.WebSocketError):
            ha.daily_forecast("weather.forecast_home")


if __name__ == "__main__":
    unittest.main()
