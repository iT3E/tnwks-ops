"""The calls of the two Wazuh jobs to the indexer: accounts.py and settings.py.

Python standard library only. HTTPS with the CA of the server, and basic
authentication as one user. No password, no hash and no answer of the API as
it came goes into the log. Tests: security/tests/test_wazuh_settings.py.
"""

import base64
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# The password rule of the Wazuh images: 12 to 64 characters from this set,
# with a lowercase letter, an uppercase letter, a digit and a symbol
# (wazuh-kubernetes v5.0.0-rc2, docs/ref/credentials.md, "Creating the
# credentials").
SYMBOLS = ".,_+:@%^=~-"
PASSWORD = re.compile(r"[A-Za-z0-9" + re.escape(SYMBOLS) + r"]{12,64}")


class Stop(Exception):
    """An error that ends the run. The text never holds a secret."""


def password_ok(value: str) -> bool:
    return bool(PASSWORD.fullmatch(value)) and all((
        any(c.islower() for c in value),
        any(c.isupper() for c in value),
        any(c.isdigit() for c in value),
        any(c in SYMBOLS for c in value),
    ))


def read_secret(path) -> str:
    try:
        value = Path(path).read_text().strip()
    except OSError as error:
        raise Stop(f"can not read the secret file {path}: {error.strerror}") from None
    if not value:
        raise Stop(f"the secret file {path} is empty")
    return value


def tls_context(ca_file: str) -> ssl.SSLContext:
    """A client context that checks the certificate and the name of the server,
    with TLS 1.2 or later."""
    context = ssl.create_default_context(cafile=ca_file or None)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


class Indexer:
    """One user's calls to the indexer. send() is the transport, so a test can give
    a fake one: send(method, path, body, user, password) -> (status, data)."""

    def __init__(self, base: str, user: str, password: str, send=None, ca_file: str = ""):
        self.base = base.rstrip("/")
        self.user = user
        self._password = password
        self._send = send or self._https(ca_file)

    def _https(self, ca_file: str):
        context = tls_context(ca_file)

        def send(method, path, body, user, password):
            data = None if body is None else json.dumps(body).encode()
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            request = urllib.request.Request(self.base + path, data=data, method=method, headers={
                "Authorization": f"Basic {token}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, context=context, timeout=30) as response:
                    return response.status, _parse(response.read())
            except urllib.error.HTTPError as error:
                return error.code, _parse(error.read())
            except (urllib.error.URLError, TimeoutError) as error:
                raise Stop(f"{method} {path}: no answer ({getattr(error, 'reason', error)})") from None
        return send

    def call(self, method: str, path: str, body=None, ok=(200, 201)):
        status, data = self._send(method, path, body, self.user, self._password)
        if ok is not None and status not in ok:
            raise Stop(f"{method} {path} as {self.user}: HTTP {status}{_reason(data)}")
        return status, data


def _parse(raw: bytes):
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _reason(data) -> str:
    """The type of an error of OpenSearch, never its whole answer."""
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and isinstance(error.get("type"), str):
            return f" ({error['type']})"
        if isinstance(data.get("message"), str) and len(data["message"]) <= 200:
            return f" ({data['message']})"
    return ""


def q(name: str) -> str:
    return urllib.parse.quote(name, safe="*,")
