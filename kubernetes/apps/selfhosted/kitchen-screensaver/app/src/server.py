#!/usr/bin/env python3
"""Kitchen screensaver: the page the kitchen display's screensaver shows, plus
the small JSON API behind it (docs/apps/kitchen-screensaver.md).

Standard library only (it runs on the stock Python image, the code comes from
a ConfigMap). The display only ever talks to this service: Home Assistant and
Immich are read here, server-side, with their own read-only credentials, so
the display holds neither.

    GET  /                 the page (index.html)
    GET  /api/info         weather, today's events and dinner, stars, theme
    GET  /api/photos       the album's photos: id, size, face boxes
    GET  /img/<id>         a photo, Immich's screen-sized preview (or ?size=thumbnail)
    GET  /api/ping         the page checks in (logged: loads, uptime, memory)
    GET  /healthz          liveness, no secret

Everything but /healthz needs the auth secret, as ?authsecret=<secret> (how
the ImmichFrame Android app passes it) or "Authorization: Bearer <secret>".

Environment: AUTH_SECRET, HASS_URL, HASS_TOKEN, IMMICH_URL, IMMICH_API_KEY,
IMMICH_ALBUM_NAME, plus optional PORT, PAGE (path of index.html) and the
entity ids (see Config).
"""

import base64
import collections
import hmac
import json
import logging
import os
import re
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("kitchen-screensaver")

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class Config:
    def __init__(self, env=os.environ):
        self.auth_secret = env.get("AUTH_SECRET", "")
        self.hass_url = env.get("HASS_URL", "").rstrip("/")
        self.hass_token = env.get("HASS_TOKEN", "")
        self.immich_url = env.get("IMMICH_URL", "").rstrip("/")
        self.immich_key = env.get("IMMICH_API_KEY", "")
        self.album_name = env.get("IMMICH_ALBUM_NAME", "Kitchen screensaver")
        self.port = int(env.get("PORT", "8080"))
        self.page = env.get("PAGE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"))
        self.weather = env.get("WEATHER_ENTITY", "weather.forecast_home")
        self.calendars = [c for c in env.get("CALENDARS", "calendar.family").split(",") if c]
        self.dinner = env.get("DINNER_CALENDAR", "calendar.mealie_dinner")
        self.stars = [c for c in env.get("STAR_COUNTERS", "counter.stars_kid1,counter.stars_kid2").split(",") if c]
        self.theme = env.get("THEME_ENTITY", "input_select.kitchen_theme")
        # Seconds: how long /api/info answers from memory, how often the forecast
        # and the album are re-read.
        self.info_ttl = float(env.get("INFO_TTL", "60"))
        self.forecast_ttl = float(env.get("FORECAST_TTL", "900"))
        self.album_ttl = float(env.get("ALBUM_TTL", "900"))
        self.image_cache_mb = int(env.get("IMAGE_CACHE_MB", "64"))

    def missing(self):
        need = {"AUTH_SECRET": self.auth_secret, "HASS_URL": self.hass_url, "HASS_TOKEN": self.hass_token,
                "IMMICH_URL": self.immich_url, "IMMICH_API_KEY": self.immich_key}
        return [k for k, v in need.items() if not v]


# --------------------------------------------------------------- http helpers


def http_json(method, url, headers=None, body=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


# ------------------------------------------------------------ home assistant


class WebSocketError(Exception):
    pass


class MiniWebSocket:
    """Just enough of RFC 6455 for a short request/response chat with Home
    Assistant: text frames, masking, ping/pong, fragmented messages."""

    def __init__(self, url, timeout=10):
        u = urllib.parse.urlsplit(url)
        self.secure = u.scheme in ("https", "wss")
        host, port = u.hostname, u.port or (443 if self.secure else 80)
        sock = socket.create_connection((host, port), timeout=timeout)
        if self.secure:
            import ssl

            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        self.sock = sock
        self.buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        self.sock.sendall(
            (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
             f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = self._until(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status + b" ":
            raise WebSocketError(f"upgrade refused: {status[:60]!r}")
        import hashlib

        want = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if want.lower() not in head.lower():
            raise WebSocketError("bad Sec-WebSocket-Accept")

    def _until(self, marker):
        while marker not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WebSocketError("connection closed")
            self.buf += chunk
        head, self.buf = self.buf.split(marker, 1)
        return head

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WebSocketError("connection closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, opcode, payload):
        head = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, obj):
        self._send_frame(0x1, json.dumps(obj).encode())

    def recv(self):
        parts = []
        while True:
            b0, b1 = self._read(2)
            opcode, fin, n = b0 & 0x0F, b0 & 0x80, b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise WebSocketError("closed by server")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode in (0x1, 0x2, 0x0):
                parts.append(payload)
                if fin:
                    return json.loads(b"".join(parts))

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class HomeAssistant:
    def __init__(self, cfg):
        self.cfg = cfg

    def _get(self, path):
        return http_json("GET", self.cfg.hass_url + path, {"Authorization": f"Bearer {self.cfg.hass_token}"})

    def state(self, entity_id):
        try:
            return self._get(f"/api/states/{entity_id}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def events(self, entity_id, start, end):
        q = urllib.parse.urlencode({"start": start, "end": end})
        return self._get(f"/api/calendars/{entity_id}?{q}") or []

    def daily_forecast(self, entity_id):
        """weather/subscribe_forecast is the only way a read-only user gets the
        forecast (the get_forecasts service needs control rights). The first
        event after subscribing carries the current forecast."""
        ws = MiniWebSocket(self.cfg.hass_url + "/api/websocket")
        try:
            if ws.recv().get("type") != "auth_required":
                raise WebSocketError("no auth_required")
            ws.send({"type": "auth", "access_token": self.cfg.hass_token})
            if ws.recv().get("type") != "auth_ok":
                raise WebSocketError("auth failed")
            ws.send({"id": 1, "type": "weather/subscribe_forecast", "entity_id": entity_id, "forecast_type": "daily"})
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                m = ws.recv()
                if m.get("id") != 1:
                    continue
                if m.get("type") == "result" and not m.get("success"):
                    raise WebSocketError(f"subscribe failed: {(m.get('error') or {}).get('code')}")
                if m.get("type") == "event":
                    return (m.get("event") or {}).get("forecast") or []
            raise WebSocketError("no forecast event")
        finally:
            ws.close()


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f)


def pick_today(forecast, start):
    """The daily forecast entry for the display's day that begins at `start`
    (an aware datetime); met.no's first entry is today, but don't count on it."""
    for f in forecast or []:
        d = _iso(str(f.get("datetime") or ""))
        if d and d.astimezone(start.tzinfo).date() == start.date():
            return f
    return None


def slim_event(ev):
    return {"title": ev.get("summary") or "", "start": ev.get("start") or {}, "end": ev.get("end") or {}}


class InfoService:
    """/api/info: everything the overlay box shows, cached for INFO_TTL."""

    def __init__(self, cfg, ha):
        self.cfg, self.ha = cfg, ha
        self.lock = threading.Lock()
        self.cache = {}
        self.forecast = (0.0, None)

    def _forecast(self):
        t, f = self.forecast
        if f is not None and time.monotonic() - t < self.cfg.forecast_ttl:
            return f
        try:
            f = self.ha.daily_forecast(self.cfg.weather)
            self.forecast = (time.monotonic(), f)
        except (OSError, WebSocketError, ValueError) as e:
            log.warning("forecast: %s", e)
            f = f or []
            # Try again in a minute rather than at the next TTL.
            self.forecast = (time.monotonic() - self.cfg.forecast_ttl + 60, f)
        return f

    def get(self, start, end):
        key = (start, end)
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.monotonic() - hit[0] < self.cfg.info_ttl:
                return hit[1]
            out = self._build(start, end)
            # Keep only the latest window: the display asks for today.
            self.cache = {key: (time.monotonic(), out)}
            return out

    def _build(self, start, end):
        errors = []
        out = {"weather": None, "events": None, "dinner": [], "stars": [], "theme": "default", "errors": errors}

        def guard(name, fn):
            try:
                return fn()
            except (OSError, ValueError, urllib.error.URLError) as e:
                errors.append(name)
                log.warning("%s: %s", name, e)
                return None

        w = guard("weather", lambda: self.ha.state(self.cfg.weather))
        if w and w.get("state") not in (None, "unavailable", "unknown"):
            a = w.get("attributes") or {}
            today = pick_today(self._forecast(), _iso(start))
            out["weather"] = {
                "condition": w["state"],
                "temp": _num(a.get("temperature")),
                "unit": a.get("temperature_unit") or "°",
                "high": _num((today or {}).get("temperature")),
                "low": _num((today or {}).get("templow")),
            }
        evs = []
        ok = False
        for cal in self.cfg.calendars:
            r = guard(f"calendar:{cal}", lambda cal=cal: self.ha.events(cal, start, end))
            if r is not None:
                ok = True
                evs += [slim_event(e) for e in r]
        out["events"] = evs if ok else None
        if self.cfg.dinner:
            r = guard("dinner", lambda: self.ha.events(self.cfg.dinner, start, end))
            out["dinner"] = [slim_event(e) for e in (r or [])]
        for c in self.cfg.stars:
            s = guard(c, lambda c=c: self.ha.state(c))
            if s and s.get("state") not in (None, "unavailable", "unknown"):
                out["stars"].append({
                    "key": c.split("_")[-1],
                    "name": (s.get("attributes") or {}).get("friendly_name") or c,
                    "count": _num(s.get("state")) or 0,
                })
        t = guard("theme", lambda: self.ha.state(self.cfg.theme))
        if t and t.get("state"):
            out["theme"] = t["state"]
        return out


# -------------------------------------------------------------------- immich


class Immich:
    def __init__(self, cfg):
        self.cfg = cfg

    def _h(self):
        return {"x-api-key": self.cfg.immich_key}

    def album_id(self, name):
        for a in http_json("GET", self.cfg.immich_url + "/api/albums", self._h()) or []:
            if a.get("albumName") == name:
                return a["id"]
        return None

    def album_assets(self, album_id):
        items, page = [], 1
        while page:
            r = http_json("POST", self.cfg.immich_url + "/api/search/metadata", self._h(),
                          {"albumIds": [album_id], "type": "IMAGE", "withExif": True, "size": 1000, "page": page})
            a = (r or {}).get("assets") or {}
            items += a.get("items") or []
            nxt = a.get("nextPage")
            page = int(nxt) if nxt else 0
        return items

    def faces(self, asset_id):
        return http_json("GET", f"{self.cfg.immich_url}/api/faces?id={asset_id}", self._h()) or []

    def thumbnail(self, asset_id, size):
        req = urllib.request.Request(f"{self.cfg.immich_url}/api/assets/{asset_id}/thumbnail?size={size}",
                                     headers={**self._h(), "Accept": "image/*"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read(), r.headers.get("Content-Type", "image/jpeg")


def oriented_size(asset):
    """Width and height as displayed: EXIF orientations 5-8 swap them."""
    w, h = asset.get("width"), asset.get("height")
    ex = asset.get("exifInfo") or {}
    if not w or not h:
        w, h = ex.get("exifImageWidth"), ex.get("exifImageHeight")
        if str(ex.get("orientation") or "1") in ("5", "6", "7", "8") and w and h:
            w, h = h, w
    return w, h


def face_boxes(faces):
    """Immich face boxes (pixels of the image the detector saw) as fractions,
    biggest first."""
    out = []
    for f in faces or []:
        iw, ih = f.get("imageWidth"), f.get("imageHeight")
        if not iw or not ih:
            continue
        box = [f.get("boundingBoxX1", 0) / iw, f.get("boundingBoxY1", 0) / ih,
               f.get("boundingBoxX2", 0) / iw, f.get("boundingBoxY2", 0) / ih]
        box = [round(min(1.0, max(0.0, v)), 4) for v in box]
        if box[2] > box[0] and box[3] > box[1]:
            out.append(box)
    out.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    return out


class PhotoService:
    """The album's photos and their faces, re-read every ALBUM_TTL; faces are
    cached per asset (an asset with none yet is asked again after an hour, in
    case face detection hadn't run when it arrived)."""

    def __init__(self, cfg, immich):
        self.cfg, self.immich = cfg, immich
        self.lock = threading.Lock()
        self.loaded = 0.0
        self.photos = []
        self.ids = set()
        self.faces = {}

    def get(self):
        with self.lock:
            if not self.loaded or time.monotonic() - self.loaded > self.cfg.album_ttl:
                try:
                    self._load()
                except (OSError, ValueError, urllib.error.URLError) as e:
                    log.warning("album: %s", e)
                    # Keep what we had; retry in a minute.
                    self.loaded = time.monotonic() - self.cfg.album_ttl + 60
            return self.photos

    def known(self, asset_id):
        if not self.loaded:
            self.get()
        return asset_id in self.ids

    def _load(self):
        aid = self.immich.album_id(self.cfg.album_name)
        if not aid:
            raise ValueError(f"album {self.cfg.album_name!r} not found")
        photos = []
        now = time.monotonic()
        for a in self.immich.album_assets(aid):
            if a.get("isTrashed") or a.get("type") != "IMAGE" or not UUID_RE.match(a.get("id", "")):
                continue
            w, h = oriented_size(a)
            if not w or not h:
                continue
            got = self.faces.get(a["id"])
            if got is None or (not got[1] and now - got[0] > 3600):
                try:
                    got = (now, face_boxes(self.immich.faces(a["id"])))
                except (OSError, ValueError, urllib.error.URLError):
                    got = (now, (got or (0, []))[1])
                self.faces[a["id"]] = got
            photos.append({"id": a["id"], "w": w, "h": h, "faces": got[1]})
        self.photos = photos
        self.ids = {p["id"] for p in photos}
        self.faces = {k: v for k, v in self.faces.items() if k in self.ids}
        self.loaded = time.monotonic()
        log.info("album: %d photos, %d with faces", len(photos), sum(1 for p in photos if p["faces"]))


class ImageCache:
    """The last few images in memory (the page fetches each photo once, but
    a reload or a second screen asks again)."""

    def __init__(self, max_bytes):
        self.max = max_bytes
        self.items = collections.OrderedDict()
        self.size = 0
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            v = self.items.get(key)
            if v:
                self.items.move_to_end(key)
            return v

    def put(self, key, value):
        n = len(value[0])
        if n > self.max:
            return
        with self.lock:
            if key in self.items:
                return
            self.items[key] = value
            self.size += n
            while self.size > self.max:
                _, old = self.items.popitem(last=False)
                self.size -= len(old[0])


# --------------------------------------------------------------------- server


def _iso(s):
    """Parse an ISO time with an offset (the display's local midnight)."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else None


class App:
    def __init__(self, cfg, ha=None, immich=None):
        self.cfg = cfg
        self.info = InfoService(cfg, ha or HomeAssistant(cfg))
        self.immich = immich or Immich(cfg)
        self.photos = PhotoService(cfg, self.immich)
        self.images = ImageCache(cfg.image_cache_mb * 1024 * 1024)
        self.started = time.time()

    def authorized(self, headers, query):
        got = (query.get("authsecret") or [""])[0]
        auth = headers.get("Authorization", "")
        if not got and auth.startswith("Bearer "):
            got = auth[7:]
        return bool(self.cfg.auth_secret) and hmac.compare_digest(got.encode(), self.cfg.auth_secret.encode())

    def page(self):
        with open(self.cfg.page, "rb") as f:
            return f.read()


SECURITY_HEADERS = {
    # The secret travels in the page's URL: never send it on as a Referer.
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
# https://127.0.0.1:1 and https://reload.invalid are the page's safety reload
# inside the ImmichFrame app: images that can't load (nothing listens there,
# the name doesn't exist), see index.html.
PAGE_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src 'self' blob: data: https://127.0.0.1:1 https://reload.invalid; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'")


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kitchen-screensaver"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        timeout = 120  # idle keep-alive connections

        def log_message(self, fmt, *args):
            pass  # requests are logged in _done, without the query string (it holds the secret)

        def _done(self, status, n):
            path = self.path.split("?", 1)[0]
            if path not in ("/healthz",):
                log.info("%s %s %s %d", self.command, path, status, n)

        def _send(self, status, body=b"", ctype="text/plain; charset=utf-8", headers=None):
            self.send_response(status)
            for k, v in {**SECURITY_HEADERS, **(headers or {})}.items():
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            self._done(status, len(body))

        def _json(self, obj, status=200):
            self._send(status, json.dumps(obj, separators=(",", ":")).encode(), "application/json",
                       {"Cache-Control": "no-store"})

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            try:
                self._route()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:  # noqa: BLE001 - one bad request mustn't take the server down
                log.exception("error on %s", self.path.split("?", 1)[0])
                try:
                    self._send(500, b"error")
                except OSError:
                    pass

        def _route(self):
            u = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(u.query)
            path = u.path
            if path == "/healthz":
                return self._send(200, b"ok")
            if path == "/favicon.ico":
                return self._send(204)
            if not app.authorized(self.headers, q):
                if path == "/":
                    return self._send(401, b"<!doctype html><title>Kitchen screensaver</title>"
                                           b"<body style='background:#000;color:#888;font:20px sans-serif'>"
                                           b"<p>Unauthorized: add ?authsecret=...</p>",
                                      "text/html; charset=utf-8", {"Cache-Control": "no-store"})
                return self._send(401, b"unauthorized")
            if path == "/":
                return self._send(200, app.page(), "text/html; charset=utf-8",
                                  {"Cache-Control": "no-store", "Content-Security-Policy": PAGE_CSP})
            if path == "/api/info":
                start, end = _iso((q.get("start") or [""])[0]), _iso((q.get("end") or [""])[0])
                if not start or not end or not (timedelta(0) < end - start <= timedelta(days=2)):
                    return self._json({"error": "start and end: ISO times with an offset, at most 2 days apart"}, 400)
                return self._json(app.info.get(start.isoformat(), end.isoformat()))
            if path == "/api/photos":
                return self._json({"photos": app.photos.get()})
            if path == "/api/ping":
                keep = {k: (q.get(k) or [""])[0][:40] for k in ("boot", "loads", "up", "mem", "slides", "w", "h", "via")}
                log.info("ping %s", " ".join(f"{k}={v}" for k, v in keep.items() if v))
                return self._json({"ok": True, "server_up": round(time.time() - app.started)})
            if path.startswith("/img/"):
                return self._image(path[5:], (q.get("size") or ["preview"])[0])
            return self._send(404, b"not found")

        def _image(self, asset_id, size):
            if size not in ("preview", "thumbnail") or not UUID_RE.match(asset_id) or not app.photos.known(asset_id):
                return self._send(404, b"not found")
            key = (asset_id, size)
            hit = app.images.get(key)
            if not hit:
                try:
                    hit = app.immich.thumbnail(asset_id, size)
                except urllib.error.HTTPError as e:
                    log.warning("image %s %s: HTTP %s", asset_id, size, e.code)
                    return self._send(502, b"immich error")
                except (OSError, urllib.error.URLError) as e:
                    log.warning("image %s %s: %s", asset_id, size, e)
                    return self._send(502, b"immich unreachable")
                app.images.put(key, hit)
            body, ctype = hit
            self._send(200, body, ctype, {"Cache-Control": "private, max-age=86400"})

    return Handler


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    cfg = Config()
    if cfg.missing():
        log.error("missing settings: %s", ", ".join(cfg.missing()))
        sys.exit(2)
    app = App(cfg)
    srv = ThreadingHTTPServer(("", cfg.port), make_handler(app))
    srv.daemon_threads = True
    log.info("listening on :%d (album %r)", cfg.port, cfg.album_name)
    srv.serve_forever()


if __name__ == "__main__":
    main()
