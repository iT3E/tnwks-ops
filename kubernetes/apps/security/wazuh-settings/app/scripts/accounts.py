"""Step 1 of the settings of the Wazuh indexer: the accounts of git.

The Job wazuh-accounts-<hash> runs this script once for each version of its
files: config/accounts.json, this script and indexer.py. The name of the Job
carries the first 10 hex digits of the sha256 of those three files, one after
another, so a change of one of them makes a new Job, and Flux runs it. It does
not run each day (kubernetes/apps/security/wazuh/README.md, "Accounts").

It runs as the account user (the Secret wazuh-accounts-user), which the owner
makes once with admin and maps to the reserved role security_rest_api_access.
It owns only the users, roles and role mappings that carry its mark: the
attribute tnwks_managed_by=wazuh-accounts on a user, and a description that
starts with [wazuh-accounts] on a role or a role mapping. It:

  - stops before any write when a resource that accounts.json names exists
    without the mark, or is reserved, hidden or static;
  - never touches a resource that is reserved, hidden or static, a user that
    the upstream manifests make (admin, kibanaserver, wazuh-manager), or the
    account user itself, and accounts.json can not name one of them;
  - writes each role, user and role mapping of accounts.json that is missing
    or differs, and sets the hash of each user from the Secret
    wazuh-accounts-hashes (one key for each user). It never holds a password;
  - deletes each resource that carries its mark and that accounts.json no
    longer names;
  - reads back the resources that carry its mark, and stops when they are not
    those of accounts.json.

First it waits up to --login-wait seconds while the indexer gives no answer
or answers 503: Flux can apply the Job while the indexer starts or restarts
(security-wazuh-server has wait: false). A 401 stops the run at once.

The run exits 1 at the first error, so KubeJobFailed fires. Tests:
security/tests/test_wazuh_settings.py.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from indexer import Indexer, NoAnswer, Stop, q, read_secret

USER_MARK = {"tnwks_managed_by": "wazuh-accounts"}
MARK = "[wazuh-accounts]"
PREFIX = "tnwks-"
# The users of the indexer that the upstream manifests make
# (wazuh-kubernetes v5.0.0-rc2, docs/ref/credentials.md, "The accounts").
UPSTREAM_USERS = frozenset({"admin", "kibanaserver", "wazuh-manager"})
# A bcrypt hash, the form of the internal users of the security plugin.
BCRYPT = re.compile(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}")
KINDS = {"roles": "role", "internalusers": "user", "rolesmapping": "role mapping"}


def check_accounts(accounts: dict, own_user: str) -> None:
    for kind in ("roles", "users"):
        for name in accounts[kind]:
            if not name.startswith(PREFIX):
                raise Stop(f"{kind[:-1]} {name}: each name of accounts.json starts with {PREFIX}")
            if name in UPSTREAM_USERS or name == own_user:
                raise Stop(f"{kind[:-1]} {name}: the job never touches an upstream account or the account user")
    for name, spec in accounts["users"].items():
        for role in spec["roles"]:
            if role not in accounts["roles"]:
                raise Stop(f"user {name} names the role {role}, which accounts.json does not hold")


def _index_permission(entry: dict) -> dict:
    return {"index_patterns": sorted(entry.get("index_patterns") or []),
            "dls": entry.get("dls") or "",
            "fls": sorted(entry.get("fls") or []),
            "masked_fields": sorted(entry.get("masked_fields") or []),
            "allowed_actions": sorted(entry.get("allowed_actions") or [])}


def normal(kind: str, data: dict) -> dict:
    """The fields that the job writes, as the API gives them back."""
    if kind == "roles":
        return {"description": data.get("description") or "",
                "cluster_permissions": sorted(data.get("cluster_permissions") or []),
                "index_permissions": sorted((_index_permission(e) for e in data.get("index_permissions") or []),
                                            key=json.dumps),
                "tenant_permissions": data.get("tenant_permissions") or []}
    if kind == "internalusers":
        return {"backend_roles": sorted(data.get("backend_roles") or []),
                "attributes": data.get("attributes") or {},
                "opendistro_security_roles": sorted(data.get("opendistro_security_roles") or [])}
    return {"description": data.get("description") or "",
            **{k: sorted(data.get(k) or []) for k in ("users", "backend_roles", "hosts")}}


def wanted(accounts: dict) -> dict:
    """Each resource of accounts.json, by kind of the Security API, as the job writes it."""
    roles = {name: normal("roles", {**spec, "description": f"{MARK} {spec.get('description', '')}".strip()})
             for name, spec in accounts["roles"].items()}
    users = {name: normal("internalusers", {"attributes": USER_MARK}) for name in accounts["users"]}
    mappings = {}
    for role in accounts["roles"]:
        names = [user for user, spec in accounts["users"].items() if role in spec["roles"]]
        mappings[role] = normal("rolesmapping", {"description": f"{MARK} the users of accounts.json with {role}",
                                                 "users": names,
                                                 "backend_roles": accounts["roles"][role].get("backend_roles", [])})
    return {"roles": roles, "internalusers": users, "rolesmapping": mappings}


def marked(kind: str, data: dict) -> bool:
    if kind == "internalusers":
        attributes = data.get("attributes") or {}
        return all(attributes.get(k) == v for k, v in USER_MARK.items())
    return str(data.get("description") or "").startswith(MARK)


def protected(name: str, data: dict, own_user: str) -> bool:
    return any(data.get(flag) for flag in ("reserved", "hidden", "static")) or name in UPSTREAM_USERS \
        or name == own_user


def listing(client: Indexer, kind: str) -> dict:
    _, data = client.call("GET", f"/_plugins/_security/api/{kind}")
    return {name: value for name, value in (data or {}).items() if isinstance(value, dict)}


def read_hashes(folder: Path, users) -> dict:
    hashes = {}
    for name in users:
        value = read_secret(folder / name)
        if not BCRYPT.fullmatch(value):
            raise Stop(f"the hash of {name} in wazuh-accounts-hashes is not a bcrypt hash")
        hashes[name] = value
    return hashes


def apply(client: Indexer, accounts: dict, hashes: dict, own_user: str, log) -> None:
    want = wanted(accounts)
    current = {kind: listing(client, kind) for kind in KINDS}
    # No write before each name of git is known to be free or ours.
    for kind, label in KINDS.items():
        for name in want[kind]:
            data = current[kind].get(name)
            if data is None:
                continue
            for flag in ("reserved", "hidden", "static"):
                if data.get(flag):
                    raise Stop(f"{label} {name} is {flag}: the job never changes it")
            if not marked(kind, data):
                raise Stop(f"{label} {name} exists without the mark of the job: it is not the job's, "
                           "and the job never changes it")
    for kind, label in KINDS.items():
        for name, body in want[kind].items():
            path = f"/_plugins/_security/api/{kind}/{q(name)}"
            exists = name in current[kind]
            if kind == "internalusers":
                # The API never gives a hash back, so the job sets it each time it runs.
                client.call("PUT", path, {**body, "hash": hashes[name]})
                log(f"user {name}: {'hash set' if exists else 'created'}")
            elif exists and normal(kind, current[kind][name]) == body:
                log(f"{label} {name}: unchanged")
            else:
                client.call("PUT", path, body)
                log(f"{label} {name}: {'updated' if exists else 'created'}")
    for kind in ("rolesmapping", "internalusers", "roles"):
        for name, data in sorted(current[kind].items()):
            if name in want[kind] or not marked(kind, data) or protected(name, data, own_user):
                continue
            client.call("DELETE", f"/_plugins/_security/api/{kind}/{q(name)}")
            log(f"{KINDS[kind]} {name}: deleted, accounts.json no longer has it")
    for kind, label in KINDS.items():
        ours = {name: data for name, data in listing(client, kind).items()
                if marked(kind, data) and not protected(name, data, own_user)}
        if sorted(ours) != sorted(want[kind]):
            raise Stop(f"read back: the {label}s with the mark are {sorted(ours)}, not those of accounts.json")
        for name, data in ours.items():
            if normal(kind, data) != want[kind][name]:
                raise Stop(f"read back: {label} {name} differs from accounts.json")
    log("read back: the users, roles and role mappings with the mark are those of accounts.json")


def wait_for_indexer(client: Indexer, wait: float, log, sleep=time.sleep, clock=time.monotonic) -> None:
    """Wait while the indexer gives no answer or answers 503 (the security
    plugin is not initialized yet). Any other answer ends the wait: 200 goes
    on, and a 401 or another error stops the run."""
    end = clock() + wait
    while True:
        try:
            status, data = client.call("GET", "/_plugins/_security/authinfo", ok=None)
        except NoAnswer as error:
            status, reason = None, str(error)
        else:
            if status == 200:
                return
            if status != 503:
                raise Stop(f"GET /_plugins/_security/authinfo as {client.user}: HTTP {status}")
            reason = "HTTP 503"
        if clock() >= end:
            raise Stop(f"the indexer is not ready after {int(wait)} seconds: {reason}")
        log(f"the indexer is not ready yet ({reason}): waits")
        sleep(15)


def main(argv=None, send=None, out=print, sleep=time.sleep, clock=time.monotonic) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--indexer", required=True)
    parser.add_argument("--ca", default="")
    parser.add_argument("--accounts", required=True, help="the path of accounts.json")
    parser.add_argument("--user", required=True,
                        help="the folder of the Secret wazuh-accounts-user: the files username and password")
    parser.add_argument("--hashes", required=True,
                        help="the folder of the Secret wazuh-accounts-hashes: one file for each user")
    parser.add_argument("--login-wait", type=float, default=300, help="seconds to wait for the indexer")
    args = parser.parse_args(argv)
    try:
        folder = Path(args.user)
        own_user = read_secret(folder / "username")
        accounts = json.loads(Path(args.accounts).read_text())
        check_accounts(accounts, own_user)
        hashes = read_hashes(Path(args.hashes), accounts["users"])
        client = Indexer(args.indexer, own_user, read_secret(folder / "password"), send=send, ca_file=args.ca)
        wait_for_indexer(client, args.login_wait, out, sleep=sleep, clock=clock)
        apply(client, accounts, hashes, own_user, out)
    except Stop as error:
        out(f"error: {error}")
        return 1
    out("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
