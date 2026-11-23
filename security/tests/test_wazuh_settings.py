"""The two steps of the settings of the Wazuh indexer
(kubernetes/apps/security/wazuh-settings): step 1, the accounts of git, and
step 2, the retention of git, against a fake indexer API; and the files that
run them.

No test reaches an indexer. The fake keeps the users, roles, role mappings,
policies and managed indices in memory, answers as the APIs of OpenSearch and
of the Setup plugin answer, and applies a change of policy at its next poll, as
ISM does on its next run. The passwords and hashes are invented.

test_each_age_is_pinned is the check of a merge that deletes data: a change of
an age of retention.json fails it until the same change edits PINNED.
"""

import copy
import fnmatch
import hashlib
import importlib.util
import json
import re
import stat
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/security/wazuh-settings/app"
# Step 1 and the network rule, in their own Flux Kustomization.
ACCOUNTS = ROOT / "kubernetes/apps/security/wazuh-settings/accounts"
CONFIG = APP / "config"
SCRIPTS = APP / "scripts"
CERTIFICATES = ROOT / "kubernetes/apps/security/wazuh/app/certificates.yaml"
NOTIFICATIONS_ENABLED = CERTIFICATES.parent / "config/notifications-enabled"
ADMIN_ENABLED = CERTIFICATES.parent / "config/dashboard/admin-enabled"
# The files that the hash in the name of each Job covers, in this order.
HASHED = {
    "wazuh-accounts": ["config/accounts.json", "scripts/indexer.py", "scripts/accounts.py"],
    "wazuh-settings-git": ["config/retention.json", "scripts/indexer.py", "scripts/settings.py", "scripts/monitors.py"],
}
# The ages of each policy of tnwks: (rollover, delete). A merge that lowers one
# deletes the data older than the new age at the next run of ISM, with no way
# back, and it is the owner's merge (kubernetes/apps/security/wazuh/README.md,
# "Retention"). So a change of an age fails test_each_age_is_pinned until the
# same change edits this pin, where the review sees it.
PINNED = {
    "tnwks-stream-events": ("30d", "120d"),
    "tnwks-stream-findings": ("30d", "120d"),
    "tnwks-stream-raw-events": ("30d", "120d"),
    "tnwks-stream-metrics": ("30d", "60d"),
}
OWN_USER = "tnwks-accounts"


def invented(name: str) -> str:
    """A value that meets the password rule, made up for the tests."""
    return f"{name.title()}-1-invented"


def invented_hash(password: str) -> str:
    """A string in the form of a bcrypt hash, made up for the tests from the
    letters and digits of an invented password: no hash function."""
    letters = "".join(c for c in password if c.isalnum())
    return "$2y$12$" + (letters * 53)[:53]


ACCOUNT_PASSWORD = invented("account")
SETTINGS_PASSWORD = invented("settings")
CONVERTER_PASSWORD = invented("converter")


def load(name):
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(f"wazuh_{name}", SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPTS))
    return module


accounts_step = load("accounts")
settings_step = load("settings")


class FakeIndexer:
    def __init__(self):
        self.store = {
            "internalusers": {
                OWN_USER: {"hash": invented_hash(ACCOUNT_PASSWORD), "attributes": {}},
                "admin": {"hash": invented_hash(invented("admin")), "reserved": True},
            },
            "roles": {"all_access": {"reserved": True, "cluster_permissions": ["*"]}},
            "rolesmapping": {},
        }
        self.policies = {"stream-events-policy": {"seq": 1, "term": 1, "policy": {"description": "shipped"}}}
        # index -> [policy_id, seq, term]
        self.indices = {".ds-wazuh-events-v5-system-000001": ["stream-events-policy", 1, 1],
                        ".ds-wazuh-metrics-agents-000001": [None, None, None]}
        self.pending = {}
        # index -> the managed index metadata of a failed step, as explain shows it
        self.failed = {}
        self.settings = None
        self.documents = {}
        self.writes = []
        self.seq = 10

    def add_settings_user(self):
        self.store["internalusers"]["tnwks-settings"] = {
            "hash": invented_hash(SETTINGS_PASSWORD), "attributes": dict(accounts_step.USER_MARK)}

    def handle(self, method, path, body, user, password):
        known = self.store["internalusers"].get(user)
        if not known or known["hash"] != invented_hash(password):
            return 401, {"error": {"type": "security_exception"}}
        if method != "GET":
            self.writes.append((method, path.split("?")[0]))
        parts = path.split("?")[0].strip("/").split("/")
        if parts[:2] == ["_plugins", "_security"]:
            return self.security(method, parts[2:], body)
        if parts[:2] == ["_plugins", "_ism"]:
            return self.ism(method, parts[2:], path, body)
        if parts == ["_plugins", "_setup", "settings"] and method == "PUT":
            self.settings = body
            return 200, {"message": "Settings updated successfully.", "status": 200}
        if len(parts) == 3 and parts[1:] == ["_stats", "docs"]:
            if parts[0] not in self.documents:
                return 404, {"error": {"type": "index_not_found_exception"}}
            return 200, {"_all": {"primaries": {"docs": {"count": self.documents[parts[0]]}}}}
        return 400, None

    def security(self, method, parts, body):
        if parts == ["authinfo"]:
            return 200, {}
        kind = parts[1]
        store = self.store[kind]
        if len(parts) == 2 and method == "GET":
            return 200, {n: {k: v for k, v in d.items() if k != "hash"} for n, d in store.items()}
        name = parts[2]
        if method == "GET":
            if name not in store:
                return 404, {"status": "NOT_FOUND"}
            return 200, {name: {k: v for k, v in store[name].items() if k != "hash"}}
        if method == "PUT":
            if kind == "internalusers":
                assert "password" not in body
                store[name] = copy.deepcopy(body)
            elif kind == "roles":
                store[name] = {**copy.deepcopy(body), "index_permissions": [
                    {"dls": "", "fls": [], "masked_fields": [], **e} for e in body["index_permissions"]]}
            else:
                store[name] = copy.deepcopy(body)
            return 200, {"status": "OK"}
        if method == "DELETE":
            del store[name]
            return 200, {"status": "OK"}
        if method == "PATCH" and kind == "rolesmapping" and name == "all_access":
            groups = store[name]["backend_roles"]
            for operation in body:
                index = operation["path"].removeprefix("/backend_roles/")
                if operation["op"] == "add":
                    assert index == "-"
                    groups.append(operation["value"])
                else:
                    assert operation["op"] == "remove"
                    del groups[int(index)]
            return 200, {"status": "OK"}
        return 400, None

    def ism(self, method, parts, path, body):
        if parts[0] == "policies":
            name = parts[1]
            if method == "GET":
                if name not in self.policies:
                    return 404, None
                p = self.policies[name]
                # As the ISM API gives a policy back: the defaults it fills in
                # (copy_alias of a rollover) and the fields it adds.
                policy = copy.deepcopy(p["policy"])
                for state in policy.get("states") or []:
                    for action in state.get("actions") or []:
                        if "rollover" in action:
                            action["rollover"].setdefault("copy_alias", False)
                for template in policy.get("ism_template") or []:
                    template["last_updated_time"] = 1
                return 200, {"_id": name, "_seq_no": p["seq"], "_primary_term": p["term"],
                             "policy": {**policy, "policy_id": name, "last_updated_time": 1,
                                        "schema_version": 27, "error_notification": None}}
            if name in self.policies and f"if_seq_no={self.policies[name]['seq']}" not in path:
                return 409, {"error": {"type": "version_conflict_engine_exception"}}
            self.seq += 1
            self.policies[name] = {"seq": self.seq, "term": 1, "policy": copy.deepcopy(body["policy"])}
            return 201, {"_id": name, "_seq_no": self.seq, "_primary_term": 1}
        if parts[0] == "explain":
            prefixes = tuple(p.rstrip("*") for p in parts[1].split(","))
            matched = [i for i in self.indices if i.startswith(prefixes)]
            # ISM applies a pending change on its next run.
            for index in [i for i in self.pending if i in matched]:
                policy_id = self.pending.pop(index)
                p = self.policies[policy_id]
                self.indices[index] = [policy_id, p["seq"], p["term"]]
            found = {i: {"index.plugins.index_state_management.policy_id": self.indices[i][0],
                         "policy_id": self.indices[i][0], "policy_seq_no": self.indices[i][1],
                         "policy_primary_term": self.indices[i][2],
                         **self.failed.get(i, {"step": {"name": "attempt_rollover",
                                                        "step_status": "condition_not_met"},
                                               "action": {"name": "rollover", "failed": False},
                                               "retry_info": {"failed": False, "consumed_retries": 0}})}
                     for i in matched}
            return 200, {**found, "total_managed_indices": len(found)}
        if parts[0] in ("add", "change_policy"):
            self.pending[parts[1]] = body["policy_id"]
            return 200, {"updated_indices": 1, "failures": False, "failed_indices": []}
        return 400, None


# --- step 1 ------------------------------------------------------------------

def step1_files(tmp_path, hashes=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    user = tmp_path / "user"
    user.mkdir()
    (user / "username").write_text(OWN_USER + "\n")
    (user / "password").write_text(ACCOUNT_PASSWORD + "\n")
    folder = tmp_path / "hashes"
    folder.mkdir()
    for name, value in (hashes or {"tnwks-settings": invented_hash(SETTINGS_PASSWORD),
                                   "tnwks-converter": invented_hash(CONVERTER_PASSWORD)}).items():
        (folder / name).write_text(value + "\n")
    accounts = tmp_path / "accounts.json"
    accounts.write_text((CONFIG / "accounts.json").read_text())
    return ["--indexer=https://indexer.test:9200", f"--accounts={accounts}", f"--user={user}",
            f"--hashes={folder}"]


def run1(fake, argv, sleeps=None):
    lines = []
    clock = iter(range(0, 100_000, 15))
    code = accounts_step.main(argv, send=fake.handle, out=lines.append,
                              sleep=(sleeps.append if sleeps is not None else lambda _: None),
                              clock=lambda: next(clock))
    return code, lines


def test_step1_makes_the_accounts_with_its_mark_and_no_password(tmp_path):
    fake = FakeIndexer()
    code, lines = run1(fake, step1_files(tmp_path))
    assert code == 0, lines
    users, roles, mappings = (fake.store[k] for k in ("internalusers", "roles", "rolesmapping"))
    assert users["tnwks-settings"] == {"hash": invented_hash(SETTINGS_PASSWORD), "backend_roles": [],
                                       "attributes": {"tnwks_managed_by": "wazuh-accounts"},
                                       "opendistro_security_roles": []}
    assert users["tnwks-converter"]["hash"] == invented_hash(CONVERTER_PASSWORD)
    assert roles["tnwks-converter"]["description"].startswith("[wazuh-accounts] ")
    assert mappings["tnwks-converter"]["users"] == ["tnwks-converter"]
    assert mappings["tnwks-settings"]["description"].startswith("[wazuh-accounts] ")
    assert lines[-2:] == [("read back: the users, roles and role mappings with the mark are those of "
                           "accounts.json"), "done"]
    # The account user and the reserved resources are as they were.
    assert users[OWN_USER] == {"hash": invented_hash(ACCOUNT_PASSWORD), "attributes": {}}
    assert roles["all_access"] == {"reserved": True, "cluster_permissions": ["*"]}


def test_step1_again_sets_only_the_hashes(tmp_path):
    fake = FakeIndexer()
    argv = step1_files(tmp_path)
    assert run1(fake, argv)[0] == 0
    fake.writes.clear()
    code, lines = run1(fake, argv)
    assert code == 0, lines
    assert fake.writes == [("PUT", "/_plugins/_security/api/internalusers/tnwks-settings"),
                           ("PUT", "/_plugins/_security/api/internalusers/tnwks-converter")]
    assert "role tnwks-settings: unchanged" in lines and "role mapping tnwks-converter: unchanged" in lines


def test_step1_reconciles_only_the_dashboard_group_in_the_admin_mapping(tmp_path):
    fake = FakeIndexer()
    mapping = {"backend_roles": ["admin", "another-group"], "users": ["existing-user"], "reserved": False}
    fake.store["rolesmapping"]["all_access"] = mapping
    switch = tmp_path / "admin-enabled"
    argv = [*step1_files(tmp_path / "step1"), f"--dashboard-admin={switch}"]

    switch.write_text("true\n")
    assert run1(fake, argv)[0] == 0
    assert mapping["backend_roles"] == ["admin", "another-group", "wazuh-admins"]
    fake.writes.clear()
    assert run1(fake, argv)[0] == 0
    assert all(write[0] != "PATCH" for write in fake.writes)

    switch.write_text("false\n")
    assert run1(fake, argv)[0] == 0
    assert mapping == {"backend_roles": ["admin", "another-group"], "users": ["existing-user"], "reserved": False}


@pytest.mark.parametrize("kind", ["roles", "internalusers", "rolesmapping"])
def test_step1_never_takes_a_resource_without_its_mark(tmp_path, kind):
    fake = FakeIndexer()
    fake.store[kind]["tnwks-settings"] = ({"hash": invented_hash("x"), "attributes": {}}
                                          if kind == "internalusers" else {"description": "made by hand"})
    before = copy.deepcopy(fake.store)
    code, lines = run1(fake, step1_files(tmp_path))
    assert code == 1
    assert lines[-1].endswith("tnwks-settings exists without the mark of the job: it is not the job's, "
                              "and the job never changes it")
    assert fake.writes == [] and fake.store == before


def test_step1_never_changes_a_reserved_resource(tmp_path):
    fake = FakeIndexer()
    fake.store["roles"]["tnwks-settings"] = {"reserved": True, "description": "[wazuh-accounts] x"}
    code, lines = run1(fake, step1_files(tmp_path))
    assert code == 1
    assert lines[-1] == "error: role tnwks-settings is reserved: the job never changes it"
    assert fake.writes == []


def test_step1_deletes_only_what_carries_its_mark_and_git_no_longer_has(tmp_path):
    fake = FakeIndexer()
    mark = dict(accounts_step.USER_MARK)
    fake.store["internalusers"].update({
        "tnwks-old": {"hash": invented_hash("old"), "attributes": mark},
        "someone": {"hash": invented_hash("someone"), "attributes": {}},
    })
    fake.store["internalusers"][OWN_USER]["attributes"] = mark
    fake.store["roles"].update({
        "tnwks-old": {"description": "[wazuh-accounts] old"},
        "kept-reserved": {"description": "[wazuh-accounts] x", "reserved": True},
        "by-hand": {"description": "made by hand"},
    })
    fake.store["rolesmapping"]["tnwks-old"] = {"description": "[wazuh-accounts] old", "users": ["tnwks-old"]}
    code, lines = run1(fake, step1_files(tmp_path))
    assert code == 0, lines
    assert "tnwks-old" not in fake.store["internalusers"]
    assert "tnwks-old" not in fake.store["roles"] and "tnwks-old" not in fake.store["rolesmapping"]
    for kept in ("someone", OWN_USER, "admin"):
        assert kept in fake.store["internalusers"]
    assert {"kept-reserved", "by-hand", "all_access"} <= set(fake.store["roles"])
    assert "user tnwks-old: deleted, accounts.json no longer has it" in lines


@pytest.mark.parametrize("name", ["admin", "kibanaserver", "wazuh-manager", OWN_USER, "settings"])
def test_step1_refuses_an_upstream_account_its_own_or_a_name_without_the_prefix(tmp_path, name):
    fake = FakeIndexer()
    argv = step1_files(tmp_path, {"tnwks-settings": invented_hash("a"), "tnwks-converter": invented_hash("b"),
                                  name: invented_hash("c")})
    path = Path(argv[1].split("=", 1)[1])
    accounts = json.loads(path.read_text())
    accounts["users"][name] = {"roles": ["tnwks-converter"]}
    path.write_text(json.dumps(accounts))
    code, lines = run1(fake, argv)
    assert code == 1 and fake.writes == []
    assert lines[-1].startswith(f"error: user {name}: ")


def test_step1_refuses_a_hash_that_is_not_bcrypt_and_never_prints_it(tmp_path):
    fake = FakeIndexer()
    code, lines = run1(fake, step1_files(tmp_path, {"tnwks-settings": SETTINGS_PASSWORD,
                                                    "tnwks-converter": invented_hash("b")}))
    assert code == 1 and fake.writes == []
    assert lines[-1] == "error: the hash of tnwks-settings in wazuh-accounts-hashes is not a bcrypt hash"
    assert SETTINGS_PASSWORD not in "\n".join(lines)


def test_step1_with_a_wrong_password_stops_at_once(tmp_path):
    fake = FakeIndexer()
    argv = step1_files(tmp_path)
    (tmp_path / "user/password").write_text(invented("wrong"))
    code, lines = run1(fake, argv)
    assert code == 1
    assert lines == [f"error: GET /_plugins/_security/authinfo as {OWN_USER}: HTTP 401"]
    assert fake.writes == []


def not_ready(fake, answers):
    """The indexer gives these answers to authinfo first: None for no answer."""
    real = fake.handle

    def handle(method, path, body, user, password):
        if path == "/_plugins/_security/authinfo" and answers:
            answer = answers.pop(0)
            if answer is None:
                raise accounts_step.NoAnswer(f"{method} {path}: no answer (Connection refused)")
            return answer, None
        return real(method, path, body, user, password)
    return handle


def test_step1_waits_while_the_indexer_restarts(tmp_path):
    """security-wazuh-server has wait: false, so Flux can apply the Job of step
    1 while the indexer starts or restarts: step 1 waits for it."""
    fake = FakeIndexer()
    fake.handle = not_ready(fake, [None, 503, None])
    sleeps = []
    code, lines = run1(fake, step1_files(tmp_path), sleeps)
    assert code == 0, lines
    assert sleeps == [15, 15, 15]
    refused = "the indexer is not ready yet (GET /_plugins/_security/authinfo: no answer (Connection refused)): waits"
    assert lines[:3] == [refused, "the indexer is not ready yet (HTTP 503): waits", refused]
    assert lines[-1] == "done"


def test_step1_stops_when_the_indexer_is_not_ready_in_time(tmp_path):
    fake = FakeIndexer()
    fake.handle = not_ready(fake, [503] * 1000)
    argv = step1_files(tmp_path) + ["--login-wait=60"]
    code, lines = run1(fake, argv)
    assert code == 1
    assert lines[-1] == "error: the indexer is not ready after 60 seconds: HTTP 503"
    assert fake.writes == []


def test_step1_waits_no_longer_than_the_deadline_of_its_job():
    job = yaml.safe_load((ACCOUNTS / "accounts-job.yaml").read_text())
    (container,) = job["spec"]["template"]["spec"]["containers"]
    wait = int(next(a for a in container["args"] if a.startswith("--login-wait=")).split("=")[1])
    assert wait < job["spec"]["activeDeadlineSeconds"]


# --- step 2 ------------------------------------------------------------------

DAY = 86400
NOW = 1_800_000_000


def step2_files(tmp_path, password=SETTINGS_PASSWORD):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "password").write_text(password + "\n")
    retention = tmp_path / "retention.json"
    retention.write_text((CONFIG / "retention.json").read_text())
    results = tmp_path / "results"
    results.mkdir()
    return ["--indexer=https://indexer.test:9200", f"--retention={retention}",
            f"--secret={tmp_path}", f"--results={results}", "--wait=60", "--login-wait=60"]


def run2(fake, argv, cert_end=NOW + 60 * DAY):
    lines = []
    clock = iter(range(0, 100_000, 15))
    code = settings_step.main(argv, send=fake.handle, sleep=lambda _: None, clock=lambda: next(clock),
                              now=lambda: NOW, ends=lambda *_: [cert_end, NOW + 3000 * DAY], out=lines.append)
    return code, lines


def results(argv) -> dict:
    folder = Path(argv[3].split("=", 1)[1])
    return json.loads((folder / "wazuh-settings/indices.json").read_text())


@pytest.mark.parametrize(("before", "after"), [("true", "false"), ("false", "true")])
def test_step2_checks_the_notification_switch_after_taking_the_lock(tmp_path, monkeypatch, before, after):
    fake = FakeIndexer()
    fake.add_settings_user()
    argv = step2_files(tmp_path)
    switch = tmp_path / "notifications-enabled"
    switch.write_text(before + "\n")
    argv += [f"--notifications-enabled={switch}", "--monitor-cluster=fixture"]
    events = []
    flock = settings_step.fcntl.flock

    def acquire(fd, mode):
        flock(fd, mode)
        switch.write_text(after + "\n")
        events.append("locked")

    monkeypatch.setattr(settings_step.fcntl, "flock", acquire)
    monkeypatch.setattr(settings_step.monitors, "wait_for_channel",
                        lambda _, enabled, *_args: events.append(("checked", enabled)))
    monkeypatch.setattr(settings_step.monitors, "apply",
                        lambda _, cluster, enabled, *_args, **_kwargs: events.append(("applied", enabled)))
    code, log = run2(fake, argv)
    assert code == 0, log
    assert events == ["locked", ("checked", after == "true"), ("applied", after == "true")]


def test_step2_makes_the_policies_moves_the_indices_and_writes_the_counts(tmp_path):
    fake = FakeIndexer()
    fake.add_settings_user()
    fake.documents["wazuh-events-raw-v5"] = 0
    argv = step2_files(tmp_path)
    code, lines = run2(fake, argv)
    assert code == 0, lines
    assert sorted(p for p in fake.policies if p.startswith("tnwks-")) == sorted(PINNED)
    # The shipped policy is never written.
    assert fake.policies["stream-events-policy"]["policy"] == {"description": "shipped"}
    assert fake.indices[".ds-wazuh-events-v5-system-000001"][0] == "tnwks-stream-events"
    assert fake.indices[".ds-wazuh-metrics-agents-000001"][0] == "tnwks-stream-metrics"
    assert "index .ds-wazuh-metrics-agents-000001: add to tnwks-stream-metrics" in lines
    assert fake.settings == {"engine": {"index_raw_events": False}}
    found = results(argv)
    assert (found["source"], found["time"], found["moves"]) == ("wazuh-settings", NOW, "applied")
    assert (found["indices"], found["off_policy"], found["no_policy"]) == (2, 0, 0)
    assert found["documents"] == {"wazuh-events-raw-v5": 0, "wazuh-active-responses": 0}
    assert lines[-1] == "done"


def test_step2_writes_the_counts_for_the_converter_to_read(tmp_path):
    fake = FakeIndexer()
    fake.add_settings_user()
    argv = step2_files(tmp_path)
    assert run2(fake, argv)[0] == 0
    folder = Path(argv[3].split("=", 1)[1]) / "wazuh-settings"
    assert stat.S_IMODE(folder.stat().st_mode) == 0o755
    assert stat.S_IMODE((folder / "indices.json").stat().st_mode) == 0o644
    assert sorted(p.name for p in folder.iterdir()) == ["indices.json"]


def test_step2_again_writes_only_the_setting(tmp_path):
    fake = FakeIndexer()
    fake.add_settings_user()
    argv = step2_files(tmp_path)
    assert run2(fake, argv)[0] == 0
    fake.writes.clear()
    code, lines = run2(fake, argv)
    assert code == 0, lines
    assert fake.writes == [("PUT", "/_plugins/_setup/settings")]


def test_step2_waits_through_a_restart(tmp_path):
    fake = FakeIndexer()
    fake.add_settings_user()
    fake.handle = not_ready(fake, [None, 503, None])
    code, lines = run2(fake, step2_files(tmp_path))
    assert code == 0, lines
    assert sum("the indexer is not ready yet" in line for line in lines) == 3


@pytest.mark.parametrize("answer", [None, 503, 403])
def test_step2_stops_without_writes_when_login_never_succeeds(tmp_path, answer):
    fake = FakeIndexer()
    fake.add_settings_user()
    fake.handle = not_ready(fake, [answer] * 8)
    code, lines = run2(fake, step2_files(tmp_path) + ["--login-wait=21"])
    assert code == 1
    assert fake.writes == []
    if answer == 403:
        assert len(lines) == 1 and lines[-1].endswith("HTTP 403")
    else:
        assert "not ready after 21 seconds" in lines[-1]


def test_step2_waits_for_step1_to_make_its_user(tmp_path):
    fake = FakeIndexer()
    original, calls = fake.handle, []

    def handle(method, path, body, user, password):
        calls.append(path)
        if len(calls) == 3:
            fake.add_settings_user()
        return original(method, path, body, user, password)

    fake.handle = handle
    code, lines = run2(fake, step2_files(tmp_path))
    assert code == 0, lines
    assert lines.count("tnwks-settings can not log in yet: waits for step 1") == 2


def test_step2_stops_when_its_user_never_comes(tmp_path):
    fake = FakeIndexer()
    code, lines = run2(fake, step2_files(tmp_path))
    assert code == 1 and fake.writes == []
    assert lines[-1].startswith("error: tnwks-settings can not log in: step 1 has not made it")


def test_step2_moves_each_index_to_the_new_version_of_a_changed_policy(tmp_path):
    fake = FakeIndexer()
    fake.add_settings_user()
    argv = step2_files(tmp_path)
    assert run2(fake, argv)[0] == 0
    path = Path(argv[1].split("=", 1)[1])
    retention = json.loads(path.read_text())
    retention["delete_age"] = "150d"
    path.write_text(json.dumps(retention))
    code, lines = run2(fake, argv)
    assert code == 0, lines
    events = fake.policies["tnwks-stream-events"]
    assert events["policy"]["states"][0]["transitions"][0]["conditions"] == {"min_index_age": "150d"}
    assert fake.indices[".ds-wazuh-events-v5-system-000001"][1:] == [events["seq"], events["term"]]
    assert "index .ds-wazuh-events-v5-system-000001: change_policy to tnwks-stream-events" in lines
    assert "policy tnwks-stream-metrics: unchanged" in lines


# The forms of a failed step in the ISM explain API (index-management 3.6.0.0,
# ManagedIndexMetaData, ActionMetaData, StepMetaData, PolicyRetryInfoMetaData):
# a step that failed while ISM still retries its action, an action whose
# retries are used up, and a policy that failed.
FAILED_STEPS = {
    "step": {"step": {"name": "attempt_rollover", "step_status": "failed"},
             "action": {"name": "rollover", "failed": False},
             "retry_info": {"failed": False, "consumed_retries": 0},
             "info": {"message": "Failed to evaluate conditions for rollover",
                      "cause": "no permissions for [indices:monitor/stats]"}},
    "action": {"action": {"name": "rollover", "failed": True, "consumed_retries": 3},
               "retry_info": {"failed": False, "consumed_retries": 0}},
    "policy": {"retry_info": {"failed": True, "consumed_retries": 0}},
    "missing_retry_state": {"step": {"step_status": "failed"}},
}


@pytest.mark.parametrize("form", ("action", "policy", "missing_retry_state"))
def test_step2_counts_an_index_at_a_failed_step_and_fails(tmp_path, form):
    """ISM runs each step of a policy of tnwks with the roles of the user that
    wrote and moved it. A step that the role does not allow fails, the index
    stays on its policy, and only the count shows it: the run writes it and
    fails, so KubeJobFailed fires."""
    fake = FakeIndexer()
    fake.add_settings_user()
    argv = step2_files(tmp_path)
    assert run2(fake, argv)[0] == 0
    assert results(argv)["failed"] == 0
    fake.failed[".ds-wazuh-events-v5-system-000001"] = FAILED_STEPS[form]
    code, lines = run2(fake, argv)
    assert code == 1
    found = results(argv)
    assert (found["moves"], found["off_policy"], found["failed"]) == ("applied", 0, 1)
    assert found["streams"]["events"]["failed"] == 1
    assert found["streams"]["metrics"]["failed"] == 0
    assert lines[-1].startswith("error: ISM stopped at a failed step on indices of events:")


def test_step2_does_not_count_a_step_that_ism_will_retry(tmp_path):
    fake = FakeIndexer()
    fake.add_settings_user()
    argv = step2_files(tmp_path)
    fake.failed[".ds-wazuh-events-v5-system-000001"] = FAILED_STEPS["step"]
    code, lines = run2(fake, argv)
    assert code == 0, lines
    assert results(argv)["failed"] == 0


def test_step2_counts_no_failure_for_a_step_that_waits_or_completed(tmp_path):
    fake = FakeIndexer()
    fake.add_settings_user()
    argv = step2_files(tmp_path)
    fake.failed[".ds-wazuh-events-v5-system-000001"] = {
        "step": {"name": "attempt_transition_step", "step_status": "completed"},
        "action": {"name": "transition", "failed": False}, "retry_info": {"failed": False}}
    code, lines = run2(fake, argv)
    assert code == 0, lines
    assert results(argv)["failed"] == 0


def test_step2_names_both_problems_when_a_move_waits_and_a_step_failed(tmp_path):
    fake = FakeIndexer()
    fake.add_settings_user()
    argv = step2_files(tmp_path)
    fake.failed[".ds-wazuh-metrics-agents-000001"] = FAILED_STEPS["policy"]
    real = fake.ism

    def stuck(method, parts, path, body):
        if parts[0] == "explain":
            fake.pending.pop(".ds-wazuh-events-v5-system-000001", None)
        return real(method, parts, path, body)
    fake.ism = stuck
    code, lines = run2(fake, argv)
    assert code == 1
    assert "ISM has not applied the move yet for events" in lines[-1]
    assert "ISM stopped at a failed step on indices of metrics" in lines[-1]
    assert results(argv)["failed"] == 1


def test_step2_moves_the_first_index_of_a_stream_from_the_shipped_policy(tmp_path):
    """The Setup plugin puts the first backing index of a stream on the shipped
    policy at its start (wazuh-indexer-plugins v5.0.0-rc2, StreamIndex.java,
    registerWithISM). A run moves it, and the count goes back to 0."""
    fake = FakeIndexer()
    fake.add_settings_user()
    argv = step2_files(tmp_path)
    assert run2(fake, argv)[0] == 0
    assert results(argv)["off_policy"] == results(argv)["no_policy"] == 0
    # Between two runs: a start of the indexer registers the first index of the
    # findings stream on its shipped policy, and a rollover of the events
    # stream makes an index that ISM puts on the policy of tnwks by its
    # ism_template priority (index-management 3.6.0.0, ManagedIndexCoordinator).
    fake.policies["stream-findings-policy"] = {"seq": 2, "term": 1, "policy": {"description": "shipped"}}
    fake.indices[".ds-wazuh-findings-v5-system-000001"] = ["stream-findings-policy", 2, 1]
    events = fake.policies["tnwks-stream-events"]
    fake.indices[".ds-wazuh-events-v5-system-000002"] = ["tnwks-stream-events", events["seq"], events["term"]]
    writes = len(fake.writes)
    code, lines = run2(fake, argv)
    assert code == 0, lines
    moves = [w for w in fake.writes[writes:] if "/_ism/add/" in w[1] or "/_ism/change_policy/" in w[1]]
    assert moves == [("POST", "/_plugins/_ism/change_policy/.ds-wazuh-findings-v5-system-000001")]
    findings = fake.policies["tnwks-stream-findings"]
    assert fake.indices[".ds-wazuh-findings-v5-system-000001"] == [
        "tnwks-stream-findings", findings["seq"], findings["term"]]
    assert results(argv)["off_policy"] == results(argv)["no_policy"] == 0


SHIPPED_STATES = [("hot", ["retry", "rollover"], ["delete"]), ("delete", ["retry", "delete"], [])]


def test_each_policy_has_the_states_and_actions_of_the_shipped_policies():
    """The shipped stream policies at v5.0.0-rc2 have a state hot with the
    rollover and a state delete with the delete
    (plugins/setup/src/main/resources/policies/). Each policy of tnwks has the
    same states with the same actions, so a move from a shipped policy finds
    the state of the index in the new policy."""
    for policy_id, policy in settings_step.build_policies(retention()).items():
        assert policy["default_state"] == "hot", policy_id
        shape = [(st["name"], [k for a in st["actions"] for k in a], [t["state_name"] for t in st["transitions"]])
                 for st in policy["states"]]
        assert shape == SHIPPED_STATES, policy_id
        (hot, delete) = policy["states"]
        assert list(hot["transitions"][0]["conditions"]) == ["min_index_age"], policy_id
        assert delete["actions"][0]["delete"] == {}, policy_id


def test_step2_writes_the_counts_and_fails_when_ism_does_not_apply_a_move(tmp_path):
    fake = FakeIndexer()
    fake.add_settings_user()
    original = fake.ism

    def stuck(method, parts, path, body):
        if parts[0] == "explain":
            saved, fake.pending = fake.pending, {}
            try:
                return original(method, parts, path, body)
            finally:
                fake.pending = saved
        return original(method, parts, path, body)

    fake.ism = stuck
    argv = step2_files(tmp_path)
    code, lines = run2(fake, argv)
    assert code == 1
    assert lines[-1] == ("error: ISM has not applied the move yet for events, metrics: do not restart the "
                         "indexer before a run ends well")
    found = results(argv)
    assert (found["moves"], found["off_policy"], found["no_policy"]) == ("pending", 2, 1)
    assert found["streams"]["metrics"] == {"indices": 1, "off_policy": 1, "no_policy": 1, "failed": 0}


def test_step2_moves_nothing_when_a_certificate_renews_soon(tmp_path):
    fake = FakeIndexer()
    fake.add_settings_user()
    argv = step2_files(tmp_path)
    # The leaf renews 720 hours before its end: here in 30 minutes.
    code, lines = run2(fake, argv, cert_end=NOW + 720 * 3600 + 1800)
    assert code == 0, lines
    assert not [w for w in fake.writes if "/_ism/add/" in w[1] or "/_ism/change_policy/" in w[1]]
    assert any(line.startswith("moves: none in this run") for line in lines)
    found = results(argv)
    assert (found["moves"], found["off_policy"], found["no_policy"]) == ("skipped", 2, 1)


def test_step2_counts_documents_in_the_streams_that_must_hold_none(tmp_path):
    fake = FakeIndexer()
    fake.add_settings_user()
    fake.documents.update({"wazuh-events-raw-v5": 3, "wazuh-active-responses": 1})
    argv = step2_files(tmp_path)
    assert run2(fake, argv)[0] == 0
    assert results(argv)["documents"] == {"wazuh-events-raw-v5": 3, "wazuh-active-responses": 1}


@pytest.mark.parametrize("value", ["short-A1", "nouppercase-12345", "NOLOWERCASE-12345", "NoDigits-abcdefgh",
                                   "NoSymbol12345abcd", "Has space-12345ab", "x" * 60 + "Aa1-xxxxx"])
def test_step2_refuses_a_password_that_breaks_the_rule(tmp_path, value):
    fake = FakeIndexer()
    code, lines = run2(fake, step2_files(tmp_path, password=value))
    assert code == 1 and fake.writes == []
    assert lines[-1].startswith("error: the password of tnwks-settings breaks the password rule")
    assert value not in "\n".join(lines)


def test_no_password_and_no_hash_reaches_the_log(tmp_path):
    fake = FakeIndexer()
    _, lines1 = run1(fake, step1_files(tmp_path / "one"))
    _, lines2 = run2(fake, step2_files(tmp_path / "two"))
    text = "\n".join(lines1 + lines2)
    assert lines2[-1] == "done"
    for value in (ACCOUNT_PASSWORD, SETTINGS_PASSWORD, CONVERTER_PASSWORD, invented_hash(SETTINGS_PASSWORD)):
        assert value not in text


# --- the files of git ----------------------------------------------------------

def retention():
    return json.loads((CONFIG / "retention.json").read_text())


def accounts():
    return json.loads((CONFIG / "accounts.json").read_text())


def docs(name):
    folder = ACCOUNTS if name == "accounts-job.yaml" else APP
    return [d for d in yaml.safe_load_all((folder / name).read_text()) if d]


def pod(workload: dict) -> dict:
    spec = workload["spec"]
    return (spec["jobTemplate"]["spec"] if "jobTemplate" in spec else spec)["template"]["spec"]


def days(age: str) -> int:
    assert re.fullmatch(r"[1-9][0-9]*d", age), age
    return int(age[:-1])


def ages(policy: dict) -> tuple:
    hot = policy["states"][0]
    return hot["actions"][0]["rollover"]["min_index_age"], hot["transitions"][0]["conditions"]["min_index_age"]


def test_each_age_is_pinned():
    """A merge that lowers an age deletes data, with no way back: the owner's merge.
    The pin is in this file, so the check runs on each run of CI with no need of
    the base branch, and a change of an age needs a deliberate edit of PINNED."""
    found = {policy_id: ages(policy) for policy_id, policy in settings_step.build_policies(retention()).items()}
    for policy_id, (rollover, delete) in PINNED.items():
        assert policy_id in found, f"{policy_id} is gone: its stream falls back to the shipped policy"
        new_rollover, new_delete = found[policy_id]
        assert days(new_delete) >= days(delete), (
            f"{policy_id}: the delete age goes from {delete} to {new_delete}. That merge deletes the data "
            "older than the new age, with no way back, and it is the owner's merge. Edit PINNED in the same "
            "change.")
        assert days(new_rollover) >= days(rollover), (
            f"{policy_id}: the rollover age goes from {rollover} to {new_rollover}. Edit PINNED in the same "
            "change, after the owner's word.")
    assert found == PINNED, "an age changed or a policy was added: edit PINNED in the same change"


def test_each_policy_rolls_over_by_age_and_deletes_by_index_age():
    policies = settings_step.build_policies(retention())
    assert not set(policies) & settings_step.SHIPPED_POLICY_IDS
    for policy in policies.values():
        hot, delete = policy["states"]
        assert set(hot["actions"][0]["rollover"]) == {"min_index_age", "min_doc_count", "min_primary_shard_size",
                                                      "copy_alias"}
        assert hot["actions"][0]["rollover"]["copy_alias"] is False
        (transition,) = hot["transitions"]
        assert set(transition["conditions"]) == {"min_index_age"}
        assert delete["actions"][0]["delete"] == {}
        assert policy["ism_template"][0]["priority"] == 1


def test_the_patterns_are_those_of_the_shipped_policies():
    # plugins/setup/src/main/resources/policies/*.json at wazuh-indexer-plugins v5.0.0-rc2.
    shipped = {
        "events": [".ds-wazuh-events-v5-*", "wazuh-events-v5*"],
        "findings": [".ds-wazuh-findings-v5-*", "wazuh-findings-v5*"],
        "raw-events": [".ds-wazuh-events-raw-v5-*", "wazuh-events-raw-v5*"],
        "metrics": [".ds-wazuh-metrics-*", "wazuh-metrics*"],
    }
    assert {s: spec["patterns"] for s, spec in retention()["streams"].items()} == shipped
    assert retention()["index_raw_events"] is False


def test_the_delete_age_is_the_floor_that_the_design_sets():
    # 90 days as a floor: the delete at 90 days plus one rollover period.
    assert days(retention()["delete_age"]) == 90 + days(retention()["rollover_age"])
    assert any("design of effort 0004 sets 90 days as a floor" in note for note in retention()["notes"])


def test_no_password_and_no_hash_is_in_git():
    for user in accounts()["users"].values():
        assert set(user) == {"roles"}
    assert not re.search(r"\$2[aby]\$", (CONFIG / "accounts.json").read_text())


def test_each_name_of_the_accounts_has_the_prefix_and_is_no_upstream_account():
    for kind in ("roles", "users"):
        for name in accounts()[kind]:
            assert name.startswith("tnwks-") and name not in accounts_step.UPSTREAM_USERS


def test_the_converter_reads_no_host_field_and_no_event():
    role = accounts()["roles"]["tnwks-converter"]
    (snapshots,) = [p for p in role["index_permissions"] if "wazuh-metrics-agents" in p["index_patterns"]]
    assert all(not f.startswith("~") for f in snapshots["fls"])
    assert not [f for f in snapshots["fls"] if "host" in f or "os" in f.split(".") or "groups" in f]
    for permission in role["index_permissions"]:
        if permission["allowed_actions"] == ["read"]:
            assert not [p for p in permission["index_patterns"] if "*" in p and "metrics-agents" not in p]
            assert not [p for p in permission["index_patterns"] if "events" in p or "fim" in p]
        else:
            # The size of each index, which shows no content.
            assert permission["allowed_actions"] == ["indices:monitor/stats"]


# The streams of the policies of tnwks, by the name of each data stream and of
# its backing indices: OpenSearch names a backing index .ds-<stream>-<generation>
# (OpenSearch 3.6.0, DataStream.getDefaultBackingIndexName). ISM applies a policy
# to a new backing index only when the user who wrote it holds
# indices:admin/opensearch/ism/managedindex on the name that the template
# matched (index-management 3.6.0.0, ManagedIndexCoordinator.canPolicyManagedIndex).
STREAM_NAMES = [
    ".ds-wazuh-events-v5-*", "wazuh-events-v5*",
    ".ds-wazuh-findings-v5-*", "wazuh-findings-v5*",
    ".ds-wazuh-events-raw-v5-*", "wazuh-events-raw-v5*",
    ".ds-wazuh-metrics-*", "wazuh-metrics*",
]


def test_the_index_permissions_of_each_role_are_pinned():
    roles = accounts()["roles"]
    assert roles["tnwks-settings"]["index_permissions"] == [
        {"index_patterns": STREAM_NAMES, "allowed_actions": ["indices:admin/opensearch/ism/*"]},
        # What ISM calls with this role for a policy of tnwks (wazuh/README.md,
        # "Retention"): the rollover of a data stream asks for the stats of the
        # stream name and rolls it over (OpenSearch 3.6.0,
        # TransportRolloverAction), the rollover step asks for the stats of the
        # backing index (index-management 3.6.0.0, AttemptRolloverStep), and the
        # delete step deletes the backing index (AttemptDeleteStep).
        {"index_patterns": DATA_STREAMS, "allowed_actions": ["indices:monitor/stats", "indices:admin/rollover"]},
        {"index_patterns": BACKING_INDICES, "allowed_actions": ["indices:monitor/stats", "indices:admin/delete"]},
        {"index_patterns": [".ds-wazuh-active-responses-*", "wazuh-active-responses*"],
         "allowed_actions": ["indices:monitor/stats"]},
        {"index_patterns": [".wazuh-settings"],
         "allowed_actions": ["indices:data/write/index", "indices:data/write/bulk[s]"]},
        {"index_patterns": ["wazuh-findings-v5-*", ".ds-wazuh-findings-v5-*"],
         "fls": ["@timestamp", "wazuh.rule.id", "wazuh.rule.level"],
         "allowed_actions": ["indices:data/read/search*"]},
    ]
    assert roles["tnwks-converter"]["index_permissions"] == [
        {"index_patterns": ["wazuh-states-sca", "wazuh-states-vulnerabilities", ".wazuh-cti-consumers"],
         "allowed_actions": ["read"]},
        {"index_patterns": ["wazuh-metrics-agents", ".ds-wazuh-metrics-agents-*"],
         "fls": ["@timestamp", "wazuh.agent.id", "wazuh.agent.name", "wazuh.agent.status",
                 "wazuh.agent.last_seen", "wazuh.agent.disconnected_at"],
         "allowed_actions": ["read"]},
        {"index_patterns": ["wazuh-*", ".ds-wazuh-*", ".wazuh-*"], "allowed_actions": ["indices:monitor/stats"]},
    ]
    assert roles["tnwks-converter"]["cluster_permissions"] == ["cluster_composite_ops_ro"]


DATA_STREAMS = [p for p in STREAM_NAMES if not p.startswith(".ds-")]
BACKING_INDICES = [p for p in STREAM_NAMES if p.startswith(".ds-")]


def allowed(role: dict, action: str, name: str) -> bool:
    return any(action in e["allowed_actions"] and any(fnmatch.fnmatchcase(name, p) for p in e["index_patterns"])
               for e in role["index_permissions"])


@pytest.mark.parametrize("stream", ["wazuh-events-v5-system-activity", "wazuh-findings-v5-vulnerabilities",
                                    "wazuh-events-raw-v5", "wazuh-metrics-agents", "wazuh-metrics-comms"])
def test_ism_may_roll_over_and_delete_the_indices_of_each_stream_with_the_role_for_settings(stream):
    """ISM runs each step of a policy of tnwks with the roles of tnwks-settings,
    the user that wrote and moved it: the rollover and the delete need these
    rights, and a delete never reaches the name of a stream."""
    role = accounts()["roles"]["tnwks-settings"]
    backing = f".ds-{stream}-000001"
    assert allowed(role, "indices:monitor/stats", stream) and allowed(role, "indices:admin/rollover", stream)
    assert allowed(role, "indices:monitor/stats", backing) and allowed(role, "indices:admin/delete", backing)
    assert not allowed(role, "indices:admin/delete", stream)
    assert not allowed(role, "indices:admin/rollover", backing)
    for other in (".ds-wazuh-states-sca-000001", "wazuh-states-sca", ".ds-wazuh-active-responses-000001",
                  ".ds-wazuh-ai-assistant-sessions-000001", ".wazuh-settings"):
        assert not allowed(role, "indices:admin/delete", other), other


@pytest.mark.parametrize("stream", ["wazuh-events-v5-system-activity", "wazuh-findings-v5-vulnerabilities",
                                    "wazuh-events-raw-v5", "wazuh-metrics-agents", "wazuh-metrics-comms"])
def test_the_user_for_settings_may_manage_each_stream_and_its_backing_indices(stream):
    def matched(name):
        return any(fnmatch.fnmatchcase(name, p) for p in STREAM_NAMES)

    for name in (stream, f".ds-{stream}-000001", f".ds-{stream}-000002"):
        assert matched(name), name
    for pattern in (p for s in retention()["streams"].values() for p in s["patterns"]):
        assert matched(pattern.rstrip("*") + "x"), pattern


def test_the_user_for_settings_has_no_security_api_and_reads_only_monitor_fields():
    role = accounts()["roles"]["tnwks-settings"]
    assert "restapi" not in json.dumps(role) and "security" not in json.dumps(role["cluster_permissions"])
    # The five ISM actions that the script calls, by their names in
    # index-management 3.6.0.0 (transport/action/*/*Action.kt, NAME), and no
    # other ISM action.
    assert role["cluster_permissions"] == [
        "cluster:admin/opendistro/ism/policy/get",
        "cluster:admin/opendistro/ism/policy/write",
        "cluster:admin/opendistro/ism/managedindex/add",
        "cluster:admin/opendistro/ism/managedindex/change",
        "cluster:admin/opendistro/ism/managedindex/explain",
        "cluster:admin/setup/settings/update",
        "indices:data/write/bulk",
        "cluster:admin/opendistro/alerting/monitor/search",
        "cluster:admin/opendistro/alerting/monitor/write",
        "cluster:admin/opendistro/alerting/monitor/delete",
        "cluster:admin/opensearch/notifications/configs/get",
        "cluster:admin/opensearch/notifications/feature/publish",
        "indices:data/read/msearch",
    ]
    for permission in role["index_permissions"]:
        assert "read" not in permission["allowed_actions"]
        if permission["index_patterns"] == [".wazuh-settings"]:
            # The Setup plugin indexes the settings document with the rights of
            # the caller (wazuh-indexer-plugins v5.0.0-rc2, SettingsIndex.java),
            # which OpenSearch runs as a bulk request.
            assert permission["allowed_actions"] == ["indices:data/write/index", "indices:data/write/bulk[s]"]
        elif not permission.get("fls"):
            assert not [a for a in permission["allowed_actions"] if a.startswith("indices:data")]


@pytest.mark.parametrize("prefix", sorted(HASHED))
def test_the_name_of_each_job_carries_the_hash_of_its_files(prefix):
    inputs = [APP / f for f in HASHED[prefix]]
    if prefix == "wazuh-settings-git":
        inputs.append(NOTIFICATIONS_ENABLED)
    else:
        inputs.append(ADMIN_ENABLED)
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in inputs)).hexdigest()[:10]
    want = f"{prefix}-{digest}"
    found = [d["metadata"]["name"] for name in ("accounts-job.yaml", "settings-job.yaml") for d in docs(name)
             if d["metadata"]["name"].startswith(prefix + "-")]
    assert found == [want], f"the Job of {prefix} must be named {want}: its files changed"
    folder, files, base = {"wazuh-accounts": (ACCOUNTS, "wazuh-accounts-files", "../app/"),
                           "wazuh-settings-git": (APP, "wazuh-settings-files", "./")}[prefix]
    kustomization = yaml.safe_load((folder / "kustomization.yaml").read_text())
    generated = {g["name"]: g["files"] for g in kustomization["configMapGenerator"]}
    expected_files = [base + f for f in HASHED[prefix]]
    if prefix == "wazuh-accounts":
        expected_files.append("../../wazuh/app/config/dashboard/admin-enabled")
    assert generated == {files: expected_files}


def test_the_jobs_of_git_stay_and_the_job_of_step2_is_the_pod_of_the_cronjob():
    (accounts_job,) = docs("accounts-job.yaml")
    (settings_job,) = docs("settings-job.yaml")
    (cron,) = docs("settings-cronjob.yaml")
    for job in (accounts_job, settings_job):
        assert "ttlSecondsAfterFinished" not in job["spec"]
        assert job["metadata"]["annotations"] == {"kustomize.toolkit.fluxcd.io/force": "enabled"}
    assert settings_job["spec"]["template"] == cron["spec"]["jobTemplate"]["spec"]["template"]
    assert settings_job["spec"]["activeDeadlineSeconds"] == cron["spec"]["jobTemplate"]["spec"][
        "activeDeadlineSeconds"]


def test_each_secret_is_only_in_the_container_that_uses_it():
    (accounts_job,) = docs("accounts-job.yaml")
    (settings_job,) = docs("settings-job.yaml")
    (cron,) = docs("settings-cronjob.yaml")

    def secrets(workload):
        return {v["secret"]["secretName"] for v in pod(workload)["volumes"] if "secret" in v}

    assert secrets(accounts_job) == {"wazuh-ca", "wazuh-accounts-user", "wazuh-accounts-hashes"}
    assert secrets(settings_job) == secrets(cron) == {"wazuh-ca", "wazuh-settings-user"}
    for workload in (accounts_job, settings_job, cron):
        assert len(pod(workload)["containers"]) == 1 and "initContainers" not in pod(workload)
    for workload in (accounts_job, cron):
        ca = [v for v in pod(workload)["volumes"] if v["name"] == "ca"]
        assert ca == [{"name": "ca", "secret": {"secretName": "wazuh-ca",
                                                "items": [{"key": "ca.crt", "path": "ca.crt"}]}}]


def test_step2_waits_out_a_renewal_of_the_certificates():
    (cron,) = docs("settings-cronjob.yaml")
    spec = cron["spec"]["jobTemplate"]["spec"]
    args = dict(a.removeprefix("--").split("=", 1) for a in pod(cron)["containers"][0]["args"])
    renewals = {d["spec"]["renewBefore"] for d in yaml.safe_load_all(CERTIFICATES.read_text())
                if d and d.get("kind") == "Certificate"}
    assert renewals == {args["renew-before"]}
    # No move starts in the last margin before a renewal, and a run ends inside it.
    waits = float(args["wait"]) + 2 * float(args["login-wait"])
    assert float(args["margin"]) > spec["activeDeadlineSeconds"] > waits


def test_step2_at_a_restart_of_the_indexer_starts_no_move(tmp_path):
    """A restart makes the Setup plugin write its shipped policies again. A run
    after it, with the same retention.json, finds each index on its policy."""
    fake = FakeIndexer()
    fake.add_settings_user()
    argv = step2_files(tmp_path)
    assert run2(fake, argv)[0] == 0
    fake.seq += 1
    fake.policies["stream-events-policy"] = {"seq": fake.seq, "term": 2, "policy": {"description": "shipped"}}
    events = fake.policies["tnwks-stream-events"]
    # A rollover after the restart: the new index takes the policy of tnwks by its template.
    fake.indices[".ds-wazuh-events-v5-system-000002"] = ["tnwks-stream-events", events["seq"], events["term"]]
    fake.writes.clear()
    code, lines = run2(fake, argv)
    assert code == 0, lines
    assert fake.writes == [("PUT", "/_plugins/_setup/settings")]
    found = results(argv)
    assert (found["moves"], found["indices"], found["off_policy"]) == ("applied", 3, 0)


def test_step2_moves_no_index_that_explain_shows_on_its_policy_with_no_version(tmp_path):
    fake = FakeIndexer()
    fake.add_settings_user()
    fake.indices = {".ds-wazuh-findings-v5-system-000001": ["tnwks-stream-findings", None, None]}
    argv = step2_files(tmp_path)
    code, lines = run2(fake, argv)
    assert code == 0, lines
    assert not [w for w in fake.writes if "/_ism/add/" in w[1] or "/_ism/change_policy/" in w[1]]


def test_the_guard_reads_the_handshake_and_the_ca_file_and_no_api(monkeypatch):
    """The ends come from the certificate that the indexer serves and from the CA
    file of the pod, so the pod needs no token and no right in Kubernetes."""
    opened = []

    class Raw:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class Context:
        def wrap_socket(self, raw, server_hostname):
            opened.append(server_hostname)
            served = {"notAfter": "Feb  1 00:00:00 2027 GMT"}
            return type("Tls", (Raw,), {"getpeercert": lambda self: served})()

        def get_ca_certs(self):
            return [{"notAfter": "Jan  1 00:00:00 2036 GMT"}]

    monkeypatch.setattr(settings_step.socket, "create_connection", lambda address, timeout: (
        opened.append(address), Raw())[1])
    monkeypatch.setattr(settings_step, "tls_context", lambda ca_file: Context())
    ends = settings_step.certificate_ends("https://wazuh-indexer.security.svc.cluster.local:9200", "/ca/ca.crt")
    assert ends == [1801440000.0, 2082758400.0]
    assert opened == [("wazuh-indexer.security.svc.cluster.local", 9200), "wazuh-indexer.security.svc.cluster.local"]
    for name in ("accounts-job.yaml", "settings-job.yaml", "settings-cronjob.yaml"):
        (workload,) = docs(name)
        assert pod(workload)["automountServiceAccountToken"] is False and "serviceAccountName" not in pod(workload)


def test_the_account_user_file_grants_the_security_api_and_holds_no_hash():
    user = json.loads((CONFIG / "account-user.json").read_text())
    assert user["role"] == "security_rest_api_access"
    assert user["user"].startswith("tnwks-") and user["user"] not in accounts()["users"]
    assert user["user"] not in accounts_step.UPSTREAM_USERS and user["attributes"] == {}
    assert not [k for k in user if "hash" in k or "password" in k]
    assert not re.search(r"\$2[aby]\$", (CONFIG / "account-user.json").read_text())
    # Step 1 never reads it: no Job mounts it.
    for folder in (ACCOUNTS, APP):
        kustomization = yaml.safe_load((folder / "kustomization.yaml").read_text())
        assert not [f for g in kustomization["configMapGenerator"] for f in g["files"] if "account-user" in f]


def test_step2_writes_no_policy_again_when_the_api_gives_it_back_with_its_defaults(tmp_path):
    fake = FakeIndexer()
    fake.add_settings_user()
    argv = step2_files(tmp_path)
    assert run2(fake, argv)[0] == 0
    seqs = {name: p["seq"] for name, p in fake.policies.items() if name.startswith("tnwks-")}
    code, lines = run2(fake, argv)
    assert code == 0, lines
    assert {name: p["seq"] for name, p in fake.policies.items() if name.startswith("tnwks-")} == seqs
    assert [line for line in lines if line.startswith("policy ")] == [
        f"policy {name}: unchanged" for name in seqs]
    assert not [line for line in lines if "change_policy" in line]
