"""The settings job of the Wazuh indexer: the accounts and the retention from git.

The CronJob of ../ runs this script each day on homelab-wsl-02, and a person can
start a run after a change (kubernetes/apps/security/wazuh/README.md). It uses the Python standard
library only, and talks to the indexer over HTTPS with the CA of the server.
Each run reads the state first and writes only what differs, so a second run
writes nothing. Two steps, in this order, each as its own user:

  accounts   As the account user (the Secret wazuh-settings-accounts). Makes
             each role, user and role mapping of config/accounts.json, and
             changes one that differs. A user's password is the file of its
             Secret: the job sets it only when the user can not log in with
             it. A reserved, hidden or static resource stops the run, and a
             resource that the file does not name is never read or changed.
  settings   As tnwks-settings. Writes one ISM policy of tnwks for each stream
             of config/retention.json, under an ID of its own (never one of
             the shipped IDs), and moves each backing index that is on another
             policy, or on an older version of its policy, to it
             (change_policy, or add for an index with no policy). It then
             waits until the ISM explain API names the policy and its version
             on each backing index: a restart of the indexer before that drops
             a pending change (wazuh-indexer-plugins v5.0.0-rc2,
             docs/ref/modules/setup/retention.md, "Opting in to a hard time
             ceiling"). It also sets engine.index_raw_events through the Setup
             API (docs/ref/modules/setup/api-reference.md) and prints the
             count of documents in the raw events stream.

No password and no answer of the API as it came goes into the log. The run
exits 1 at the first error, so KubeJobFailed fires; the next run starts again.
Tests: security/tests/test_wazuh_settings.py.
"""

import argparse
import base64
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# The policy IDs that the Setup plugin writes again at each start
# (plugins/setup/src/main/resources/policies/ at v5.0.0-rc2). A policy of
# tnwks never takes one of them.
SHIPPED_POLICY_IDS = frozenset({
    "stream-events-policy",
    "stream-raw-events-policy",
    "stream-findings-policy",
    "stream-metrics-policy",
    "stream-active-responses-policy",
    "ai-assistant-sessions-policy",
})
POLICY_PREFIX = "tnwks-stream-"
# The password rule of the Wazuh images: 12 to 64 characters from this set,
# with a lowercase letter, an uppercase letter, a digit and a symbol
# (wazuh-kubernetes v5.0.0-rc2, docs/ref/credentials.md, "Creating the
# credentials").
SYMBOLS = ".,_+:@%^=~-"
PASSWORD = re.compile(r"[A-Za-z0-9" + re.escape(SYMBOLS) + r"]{12,64}")
RETRY = {"count": 3, "backoff": "exponential", "delay": "1m"}
# The size and count of the shipped rollover, kept as a second condition.
SHIPPED_ROLLOVER = {"min_doc_count": 200000000, "min_primary_shard_size": "20gb"}


class Stop(Exception):
    """An error that ends the run. The text never holds a secret."""


def password_ok(value: str) -> bool:
    return bool(PASSWORD.fullmatch(value)) and all((
        any(c.islower() for c in value),
        any(c.isupper() for c in value),
        any(c.isdigit() for c in value),
        any(c in SYMBOLS for c in value),
    ))


def read_secret(path: str) -> str:
    try:
        value = Path(path).read_text().strip()
    except OSError as error:
        raise Stop(f"can not read the secret file {path}: {error.strerror}") from None
    if not value:
        raise Stop(f"the secret file {path} is empty")
    return value


class Indexer:
    """One user's calls to the indexer. send() is the transport, so a test can give
    a fake one: send(method, path, body, user, password) -> (status, data)."""

    def __init__(self, base: str, user: str, password: str, send=None, ca_file: str = ""):
        self.base = base.rstrip("/")
        self.user = user
        self._password = password
        self._send = send or self._https(ca_file)

    def _https(self, ca_file: str):
        context = ssl.create_default_context(cafile=ca_file or None)

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

    def as_user(self, user: str, password: str) -> "Indexer":
        return Indexer(self.base, user, password, send=self._send)


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


# --- retention ---------------------------------------------------------------

def build_policies(cfg: dict) -> dict:
    """The ISM policy of each stream, by policy ID."""
    policies = {}
    for stream, spec in cfg["streams"].items():
        policy_id = POLICY_PREFIX + stream
        if policy_id in SHIPPED_POLICY_IDS:
            raise Stop(f"{policy_id} is a shipped policy ID")
        delete_age = cfg[spec["delete"]]
        policies[policy_id] = {
            "description": (f"tnwks: {stream}. Rolls over at {cfg['rollover_age']} (or at the shipped size or "
                            f"count), and deletes a backing index at {delete_age} from its creation."),
            "default_state": "hot",
            "states": [
                {"name": "hot",
                 "actions": [{"retry": RETRY, "rollover": {"min_index_age": cfg["rollover_age"],
                                                           **SHIPPED_ROLLOVER}}],
                 "transitions": [{"state_name": "delete", "conditions": {"min_index_age": delete_age}}]},
                {"name": "delete", "actions": [{"retry": RETRY, "delete": {}}], "transitions": []},
            ],
            "ism_template": [{"index_patterns": list(spec["patterns"]), "priority": cfg["priority"]}],
        }
    return policies


def _comparable(policy: dict) -> dict:
    """The fields that the job writes, as the API gives them back."""
    templates = [{"index_patterns": t.get("index_patterns"), "priority": t.get("priority")}
                 for t in policy.get("ism_template") or []]
    return {"description": policy.get("description"), "default_state": policy.get("default_state"),
            "states": policy.get("states"), "ism_template": templates}


def apply_policies(client: Indexer, policies: dict, log) -> dict:
    """Write each policy that is missing or differs. Returns the version of each,
    as (seq_no, primary_term)."""
    versions = {}
    for policy_id, policy in policies.items():
        path = f"/_plugins/_ism/policies/{q(policy_id)}"
        status, data = client.call("GET", path, ok=(200, 404))
        if status == 200 and _comparable(data.get("policy") or {}) == _comparable(policy):
            versions[policy_id] = (data["_seq_no"], data["_primary_term"])
            log(f"policy {policy_id}: unchanged")
            continue
        body = {"policy": policy}
        if status == 200:
            path += f"?if_seq_no={data['_seq_no']}&if_primary_term={data['_primary_term']}"
        _, written = client.call("PUT", path, body)
        versions[policy_id] = (written["_seq_no"], written["_primary_term"])
        log(f"policy {policy_id}: {'updated' if status == 200 else 'created'}")
    return versions


def _backing(client: Indexer, pattern: str) -> dict:
    """Each backing index of a pattern, with the policy and version that ISM
    reports for it: {index: (policy_id or None, seq_no, primary_term)}."""
    status, data = client.call("GET", f"/_plugins/_ism/explain/{q(pattern)}", ok=(200, 404))
    found = {}
    for index, info in (data or {}).items() if status == 200 else ():
        if not isinstance(info, dict):
            continue  # total_managed_indices
        policy_id = info.get("policy_id") or info.get("index.plugins.index_state_management.policy_id")
        found[index] = (policy_id, info.get("policy_seq_no"), info.get("policy_primary_term"))
    return found


def _to_move(found: dict, policy_id: str, version: tuple) -> dict:
    """The indices that are not on the version of the policy. When ISM gives no
    version for an index, its policy ID alone decides."""
    moves = {}
    for index, (current, seq_no, primary_term) in found.items():
        same = current == policy_id and (seq_no is None or (seq_no, primary_term) == tuple(version))
        if not same:
            moves[index] = current
    return moves


def move_indices(client: Indexer, cfg: dict, versions: dict, log, wait: float, sleep=time.sleep,
                 clock=time.monotonic) -> None:
    """Put each backing index on the current version of its policy, and wait
    until ISM reports it."""
    pending = {}
    for stream, spec in cfg["streams"].items():
        policy_id = POLICY_PREFIX + stream
        pattern = spec["patterns"][0]  # the pattern of the backing indices
        moves = _to_move(_backing(client, pattern), policy_id, versions[policy_id])
        for index, current in sorted(moves.items()):
            action = "add" if current is None else "change_policy"
            _, data = client.call("POST", f"/_plugins/_ism/{action}/{q(index)}", {"policy_id": policy_id})
            if (data or {}).get("failures"):
                raise Stop(f"{action} {index} to {policy_id}: refused")
            log(f"index {index}: {action} to {policy_id}")
        if moves:
            pending[pattern] = (policy_id, versions[policy_id])
    end = clock() + wait
    while pending:
        for pattern, (policy_id, version) in list(pending.items()):
            if not _to_move(_backing(client, pattern), policy_id, version):
                log(f"indices {pattern}: on {policy_id}")
                del pending[pattern]
        if not pending:
            break
        if clock() >= end:
            raise Stop("ISM has not applied the move yet for " + ", ".join(sorted(pending))
                       + ": do not restart the indexer before a run ends well")
        sleep(15)


def apply_raw_events(client: Indexer, cfg: dict, log) -> None:
    value = bool(cfg["index_raw_events"])
    client.call("PUT", "/_plugins/_setup/settings", {"engine": {"index_raw_events": value}})
    log(f"setting engine.index_raw_events: {str(value).lower()}")
    status, data = client.call("GET", "/wazuh-events-raw-v5/_count", ok=(200, 404))
    count = (data or {}).get("count", 0) if status == 200 else 0
    log(f"raw events stream: {count} documents")


# --- accounts ----------------------------------------------------------------

def _guard(kind: str, name: str, data: dict) -> None:
    for flag in ("reserved", "hidden", "static"):
        if data.get(flag):
            raise Stop(f"{kind} {name} is {flag}: the job never changes it")


def _index_permission(entry: dict) -> dict:
    return {"index_patterns": sorted(entry.get("index_patterns") or []),
            "dls": entry.get("dls") or "",
            "fls": sorted(entry.get("fls") or []),
            "masked_fields": sorted(entry.get("masked_fields") or []),
            "allowed_actions": sorted(entry.get("allowed_actions") or [])}


def _role(spec: dict) -> dict:
    return {"description": spec.get("description", ""),
            "cluster_permissions": sorted(spec.get("cluster_permissions") or []),
            "index_permissions": sorted((_index_permission(e) for e in spec.get("index_permissions") or []),
                                        key=json.dumps),
            "tenant_permissions": []}


def apply_roles(client: Indexer, roles: dict, log) -> None:
    for name, spec in roles.items():
        path = f"/_plugins/_security/api/roles/{q(name)}"
        status, data = client.call("GET", path, ok=(200, 404))
        want = _role(spec)
        if status == 200:
            current = (data or {}).get(name) or {}
            _guard("role", name, current)
            if _role({**current, "tenant_permissions": []}) == want:
                log(f"role {name}: unchanged")
                continue
        client.call("PUT", path, want)
        log(f"role {name}: {'updated' if status == 200 else 'created'}")


def apply_users(client: Indexer, users: dict, log, secret=read_secret) -> None:
    for name, spec in users.items():
        path = f"/_plugins/_security/api/internalusers/{q(name)}"
        status, data = client.call("GET", path, ok=(200, 404))
        if status == 200:
            _guard("user", name, (data or {}).get(name) or {})
        password = secret(spec["password_file"])
        if not password_ok(password):
            raise Stop(f"the password of {name} breaks the password rule (12 to 64 characters, "
                       "a lowercase and an uppercase letter, a digit and a symbol)")
        own = client.as_user(name, password)
        if status == 200 and own.call("GET", "/_plugins/_security/authinfo", ok=None)[0] == 200:
            log(f"user {name}: unchanged")
            continue
        client.call("PUT", path, {"password": password, "backend_roles": [], "attributes": {}})
        if own.call("GET", "/_plugins/_security/authinfo", ok=None)[0] != 200:
            raise Stop(f"user {name} can not log in after the job set its password")
        log(f"user {name}: {'password set' if status == 200 else 'created'}")


def apply_mappings(client: Indexer, accounts: dict, log) -> None:
    for role in accounts["roles"]:
        users = sorted(name for name, spec in accounts["users"].items() if role in spec["roles"])
        want = {"users": users, "backend_roles": [], "hosts": []}
        path = f"/_plugins/_security/api/rolesmapping/{q(role)}"
        status, data = client.call("GET", path, ok=(200, 404))
        if status == 200:
            current = (data or {}).get(role) or {}
            _guard("role mapping", role, current)
            if {k: sorted(current.get(k) or []) for k in want} == want:
                log(f"role mapping {role}: unchanged")
                continue
        client.call("PUT", path, want)
        log(f"role mapping {role}: {'updated' if status == 200 else 'created'}")


def check_config(accounts: dict, retention: dict) -> None:
    for name, spec in accounts["users"].items():
        for role in spec["roles"]:
            if role not in accounts["roles"]:
                raise Stop(f"user {name} names the role {role}, which config/accounts.json does not hold")
    for stream, spec in retention["streams"].items():
        if spec["delete"] not in ("delete_age", "metrics_delete_age"):
            raise Stop(f"stream {stream}: unknown delete age {spec['delete']}")
        if not spec["patterns"][0].startswith(".ds-"):
            raise Stop(f"stream {stream}: the first pattern must be the one of the backing indices")


def main(argv=None, send=None, sleep=time.sleep, clock=time.monotonic, out=print) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--indexer", required=True)
    parser.add_argument("--ca", default="")
    parser.add_argument("--config", required=True, help="the folder of accounts.json and retention.json")
    parser.add_argument("--accounts", required=True,
                        help="the folder of the account user's Secret: the files username and password")
    parser.add_argument("--wait", type=float, default=900, help="seconds to wait for ISM")
    args = parser.parse_args(argv)
    try:
        config = Path(args.config)
        accounts = json.loads((config / "accounts.json").read_text())
        retention = json.loads((config / "retention.json").read_text())
        check_config(accounts, retention)
        folder = Path(args.accounts)
        user, password = read_secret(str(folder / "username")), read_secret(str(folder / "password"))
        admin = Indexer(args.indexer, user, password, send=send, ca_file=args.ca)
        apply_roles(admin, accounts["roles"], out)
        apply_users(admin, accounts["users"], out)
        apply_mappings(admin, accounts, out)
        settings = admin.as_user("tnwks-settings", read_secret(accounts["users"]["tnwks-settings"]["password_file"]))
        versions = apply_policies(settings, build_policies(retention), out)
        apply_raw_events(settings, retention, out)
        move_indices(settings, retention, versions, out, args.wait, sleep=sleep, clock=clock)
    except Stop as error:
        out(f"error: {error}")
        return 1
    out("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
