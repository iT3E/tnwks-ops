#!/usr/bin/env python3
"""Run on the owner's controller during the approved enrollment window."""
import argparse
import base64
import getpass
import http.client
import json
import os
import socket
import ssl
import warnings
from pathlib import Path

API_NAME = "wazuh-api.security.svc.cluster.local"
GROUPS = ("windows-host", "wsl-distro")


class ForwardedAPI(http.client.HTTPSConnection):
    """Connect to the local port-forward and verify the manager's service name."""

    def __init__(self, port: int, tls: ssl.SSLContext):
        super().__init__(API_NAME, port=port, timeout=30, context=tls)
        self.tls = tls

    def connect(self):
        stream = socket.create_connection(("127.0.0.1", self.port), self.timeout)
        self.sock = self.tls.wrap_socket(stream, server_hostname=API_NAME)


def call(api, method, path, auth, body=None, kind="application/json", ok=(200,)):
    api.request(method, path, body, {"Authorization": auth, "Content-Type": kind})
    response = api.getresponse()
    raw = response.read()
    if response.status not in ok:
        raise SystemExit(f"{method} {path} returned HTTP {response.status}")
    document = json.loads(raw)
    if document.get("error", 0):
        raise SystemExit(f"{method} {path} returned an API error")
    return document.get("data", {})


def prepare(api, user: str, password: str, group: str, address: str, config: bytes) -> str:
    basic = base64.b64encode(f"{user}:{password}".encode()).decode()
    auth = "Bearer " + call(api, "POST", "/security/user/authenticate", "Basic " + basic)["token"]
    groups = call(api, "GET", f"/groups?q=name%3D{group}", auth)
    if groups["total_affected_items"] == 0:
        call(api, "POST", "/groups", auth, json.dumps({"group_id": group}))
    call(api, "PUT", f"/groups/{group}/configuration", auth, config, "application/xml")
    body = json.dumps({"address": address, "port": 1517, "ttl": "1h", "max_uses": 1})
    return call(api, "POST", "/agents/enrollment-tokens", auth, body)["token"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=15500)
    parser.add_argument("--ca", required=True, type=Path)
    parser.add_argument("--user", required=True)
    parser.add_argument("--group", required=True, choices=GROUPS)
    parser.add_argument("--manager-name", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    args = parser.parse_args()

    context = ssl.create_default_context(cafile=str(args.ca))
    config = Path(__file__).with_name(args.group).joinpath("agent.conf").read_bytes()
    api = ForwardedAPI(args.port, context)
    fd = os.open(args.token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as token_file:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                password = getpass.getpass("Wazuh manager API password: ")
            token_file.write(prepare(api, args.user, password, args.group, args.manager_name, config) + "\n")
    except BaseException:
        args.token_file.unlink()
        raise
    finally:
        api.close()


if __name__ == "__main__":
    main()
