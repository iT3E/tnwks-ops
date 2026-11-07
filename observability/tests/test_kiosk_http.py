"""Exercise the security boundary over HTTP, including failures upstream."""

import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kiosk_proxy import handler


@pytest.fixture
def facade():
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(
        {'homelab_house_state{domain="internet"}'}, "http://127.0.0.1:1"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def request(base, path, data=None):
    try:
        with urllib.request.urlopen(base + path, data=data, timeout=2) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


@pytest.mark.parametrize("path", [
    "/api/v1/labels", "/api/v1/series", "/api/v1/query_range?query=up",
    "/api/v1/query?query=up", "/api/v1/query?query=1%2B1&query=up",
    "/api/v1/query?query=1%2B1&match%5B%5D=up",
])
def test_facade_refuses_general_prometheus_access(facade, path):
    assert request(facade, path)[0] == 403


def test_facade_healthy_without_exposing_general_queries(facade):
    assert request(facade, "/healthz")[0] == 200
    code, result = request(facade, "/api/v1/query?query=1%2B1")
    assert code == 200 and result["data"]["resultType"] == "scalar"
    assert result["data"]["result"][1] == "2"


def test_allowed_query_with_unavailable_upstream_fails_closed(facade):
    data = urllib.parse.urlencode({"query": 'homelab_house_state{domain="internet"}'}).encode()
    code, result = request(facade, "/api/v1/query", data=data)
    assert code == 503
    assert "127.0.0.1" not in json.dumps(result)


def test_large_body_is_refused(facade):
    assert request(facade, "/api/v1/query", b"x" * 2049)[0] == 413
