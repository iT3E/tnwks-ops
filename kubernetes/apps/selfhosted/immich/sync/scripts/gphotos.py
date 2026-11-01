"""Read a Google Photos shared album through its public share link.

Derived from google_scraper.py in album_slideshow v1.16.0
(https://github.com/eyalgal/album_slideshow, MIT License, Copyright (c) 2026
Eyal Gal; the license text is in LICENSE.album_slideshow next to this file).
Changes from upstream: synchronous and standard library only (urllib), and
strict. Upstream is a slideshow source and returns whatever it managed to
read; this module feeds a mirror that deletes what it no longer sees, so any
page that fails, any item it can't parse and an album that comes back empty
raise ScrapeError instead of returning a short list.

How it works: the share page's HTML carries the album key and the auth key
in its AF_dataServiceRequests blob. The album's items come from Google's
public batchexecute endpoint (RPC snAcKc, 300 items a page plus a
continuation token), the same call the web app makes when it scrolls.
Undocumented, no login.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# Share links the HA list may hold.
SHARE_URL_RE = re.compile(
    r"https?://(?:photos\.app\.goo\.gl/[A-Za-z0-9_-]+"
    r"|photos\.google\.com/(?:u/\d+/)?share/[A-Za-z0-9_-]+(?:\?key=[A-Za-z0-9_-]+)?)",
    re.IGNORECASE,
)

_REQUEST_RE = re.compile(
    r"snAcKc[^}]*?request:\s*\[\s*\"([A-Za-z0-9_-]+)\"\s*,\s*null\s*,\s*null\s*,\s*\"([A-Za-z0-9_-]+)\"",
    re.DOTALL,
)
_XSSI = ")]}'"
_SIZE_SUFFIX_RE = re.compile(r"=[wh]\d+(?:-[a-z0-9]+)*$", re.IGNORECASE)
_VIDEO_DURATION_KEY = "76647426"  # present on videos only
_MAX_PAGES = 200  # 60,000 items
_BATCHEXECUTE = "https://photos.google.com/u/0/_/PhotosUi/data/batchexecute"


class ScrapeError(Exception):
    """Google's page or API didn't look the way this module expects."""


@dataclass(frozen=True)
class Photo:
    media_key: str  # Google's stable id for the item
    base_url: str  # lh3.googleusercontent.com URL without a size suffix
    width: int | None
    height: int | None
    captured_ms: int | None
    byte_size: int | None

    @property
    def original_url(self) -> str:
        # "=d" is the download Google's own "Download" button gives album
        # viewers: full resolution with the camera's EXIF.
        return self.base_url + "=d"


@dataclass
class Album:
    album_key: str
    title: str | None
    photos: list[Photo] = field(default_factory=list)
    videos_skipped: int = 0


def find_share_urls(text: str | None) -> list[str]:
    if not text:
        return []
    return [m.group(0) for m in SHARE_URL_RE.finditer(text)]


def extract_keys(html: str) -> tuple[str, str]:
    m = _REQUEST_RE.search(html)
    if not m:
        raise ScrapeError("album keys not found in the share page (layout changed?)")
    return m.group(1), m.group(2)


def extract_title(html: str) -> str | None:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    title = m.group(1).strip()
    for suffix in (" - Google Photos", " – Google Photos"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    if title in ("", "Google Photos"):
        return None
    return _unescape(title)


def _unescape(s: str) -> str:
    import html as _html

    return _html.unescape(s)


def _contains_key(node: Any, key: str, depth: int = 0) -> bool:
    if depth > 6:
        return False
    if isinstance(node, dict):
        if key in node or (key.isdigit() and int(key) in node):
            return True
        return any(_contains_key(v, key, depth + 1) for v in node.values())
    if isinstance(node, list):
        return any(_contains_key(v, key, depth + 1) for v in node)
    return False


def is_video(raw: Any) -> bool:
    # Motion photos (key 146008172) are photos with a clip attached; "=d"
    # gives their still, so they count as photos.
    return _contains_key(raw, _VIDEO_DURATION_KEY)


_MIN_TS_MS = -5_364_662_400_000
_MAX_TS_MS = 4_102_444_800_000


def _ts(value: Any) -> int | None:
    return value if type(value) is int and _MIN_TS_MS <= value <= _MAX_TS_MS else None


def parse_item(raw: Any) -> Photo:
    """One album item: [mediaKey, [url, w, h, ..., [byte_size]], captured_ms, ...]."""
    if not isinstance(raw, list) or len(raw) < 2 or not isinstance(raw[0], str) or not raw[0]:
        raise ScrapeError("album item without a media key")
    visual = raw[1]
    if not isinstance(visual, list) or len(visual) < 3 or not isinstance(visual[0], str):
        raise ScrapeError(f"album item {raw[0]}: no image URL")
    url = visual[0]
    host = urlsplit(url).hostname or ""
    if not url.startswith("https://") or not host.endswith(".googleusercontent.com"):
        raise ScrapeError(f"album item {raw[0]}: unexpected image host {host!r}")
    width = visual[1] if isinstance(visual[1], int) else None
    height = visual[2] if isinstance(visual[2], int) else None
    byte_size = None
    if isinstance(visual[-1], list) and visual[-1] and isinstance(visual[-1][0], int):
        byte_size = visual[-1][0]
    # Byte size sits at a fixed index in current responses; accept either.
    for entry in visual[3:]:
        if byte_size is None and isinstance(entry, list) and len(entry) == 1 and isinstance(entry[0], int):
            byte_size = entry[0]
    return Photo(
        media_key=raw[0],
        base_url=_SIZE_SUFFIX_RE.sub("", url),
        width=width,
        height=height,
        captured_ms=_ts(raw[2]) if len(raw) > 2 else None,
        byte_size=byte_size,
    )


def parse_page(body: str) -> tuple[list[Any], str | None, Any]:
    """Parse one snAcKc response: (raw items, next page token, album info)."""
    text = body.lstrip()
    if text.startswith(_XSSI):
        text = text[len(_XSSI):]
    inner_json = None
    for line in text.lstrip().splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            outer = json.loads(line)
        except ValueError:
            continue
        for entry in outer if isinstance(outer, list) else []:
            if isinstance(entry, list) and len(entry) >= 3 and entry[:2] == ["wrb.fr", "snAcKc"]:
                inner_json = entry[2]
                break
        if inner_json is not None:
            break
    if not isinstance(inner_json, str):
        raise ScrapeError("batchexecute response has no snAcKc payload (layout changed or link revoked?)")
    try:
        inner = json.loads(inner_json)
    except ValueError as err:
        raise ScrapeError(f"snAcKc payload isn't JSON: {err}") from err
    if not isinstance(inner, list) or len(inner) < 3:
        raise ScrapeError("snAcKc payload has an unexpected shape")
    raw_items = inner[1]
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        raise ScrapeError("snAcKc payload: item list missing")
    next_page = inner[2] if isinstance(inner[2], str) and inner[2] else None
    info = inner[3] if len(inner) > 3 else None
    return raw_items, next_page, info


def album_title_from_info(info: Any) -> str | None:
    # inner[3] = [albumKey, title, [timestamps], ...]
    if isinstance(info, list) and len(info) > 1 and isinstance(info[1], str) and info[1].strip():
        return info[1].strip()
    return None


def fetch_album(http, share_url: str) -> Album:
    """Read the whole album. Raises ScrapeError (or HttpError) on any doubt."""
    status, _headers, body = http.request(
        "GET",
        share_url,
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    html = body.decode("utf-8", "replace")
    album_key, auth_key = extract_keys(html)
    album = Album(album_key=album_key, title=extract_title(html))

    seen: set[str] = set()
    tokens: set[str] = set()
    page_id: str | None = None
    for page_no in range(1, _MAX_PAGES + 1):
        payload = json.dumps([album_key, page_id, None, auth_key])
        envelope = json.dumps([[["snAcKc", payload, None, "generic"]]])
        url = f"{_BATCHEXECUTE}?rpcids=snAcKc&source-path=/share/{quote(album_key)}"
        status, _headers, body = http.request(
            "POST",
            url,
            data=f"f.req={quote(envelope)}".encode(),
            headers={
                "User-Agent": BROWSER_UA,
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Accept": "*/*",
                "Origin": "https://photos.google.com",
                "Referer": f"https://photos.google.com/share/{album_key}?key={auth_key}",
            },
        )
        raw_items, next_page, info = parse_page(body.decode("utf-8", "replace"))
        if page_no == 1:
            album.title = album_title_from_info(info) or album.title
        for raw in raw_items:
            if is_video(raw):
                album.videos_skipped += 1
                continue
            photo = parse_item(raw)
            if photo.media_key in seen:
                continue
            seen.add(photo.media_key)
            album.photos.append(photo)
        if not next_page:
            break
        if next_page in tokens:
            raise ScrapeError("batchexecute returned the same page token twice")
        tokens.add(next_page)
        page_id = next_page
    else:
        raise ScrapeError(f"album has more than {_MAX_PAGES} pages")

    if not album.photos:
        raise ScrapeError(
            f"album {album.title!r} returned no photos ({album.videos_skipped} videos skipped); "
            "refusing to treat that as an empty album"
        )
    return album


def parse_content_disposition(value: str | None) -> str | None:
    if not value:
        return None
    m = re.search(r"filename\*=UTF-8''([^;]+)", value, re.IGNORECASE)
    if m:
        from urllib.parse import unquote

        return unquote(m.group(1)).strip() or None
    m = re.search(r'filename="([^"]+)"', value) or re.search(r"filename=([^;]+)", value)
    if m:
        name = m.group(1).strip().replace("/", "_").replace("\\", "_")
        return name or None
    return None


def download_original(http, photo: Photo) -> tuple[bytes, str, str]:
    """(bytes, filename, content type) of the photo's original download."""
    status, headers, body = http.request("GET", photo.original_url, headers={"User-Agent": BROWSER_UA}, timeout=180)
    ctype = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if not (ctype.startswith("image/") or ctype == "application/octet-stream"):
        raise ScrapeError(f"photo {photo.media_key}: download is {ctype or 'untyped'}, not an image")
    if not body:
        raise ScrapeError(f"photo {photo.media_key}: empty download")
    name = parse_content_disposition(headers.get("Content-Disposition"))
    if not name:
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/heic": ".heic", "image/webp": ".webp"}.get(ctype, ".jpg")
        name = photo.media_key + ext
    return body, name, ctype
