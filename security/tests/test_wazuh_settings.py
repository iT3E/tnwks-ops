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
CONFIG = APP / "config"
SCRIPTS = APP / "scripts"
CERTIFICATES = ROOT / "kubernetes/apps/security/wazuh/app/certificates.yaml"
# The files that the hash in the name of each Job covers, in this order.
HASHED = {
    "wazuh-accounts": ["config/accounts.json", "scripts/indexer.py", "scripts/accounts.py"],
    "wazuh-settings-git": ["config/retention.json", "scripts/indexer.py", "scripts/settings.py"],
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
    """A string in the form of a bcrypt hash, made up for the tests."""
    return "$2y$12$" + hashlib.sha256(password.encode()).hexdigest()[:53]


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
        return 400, None

    def ism(self, method, parts, path, body):
        if parts[0] == "policies":
            name = parts[1]
            if method == "GET":
                if name not in self.policies:
                    return 404, None
                p = self.policies[name]
                return 200, {"_id": name, "_seq_no": p["seq"], "_primary_term": p["term"],
                             "policy": {**copy.deepcopy(p["policy"]), "policy_id": name,
                                        "last_updated_time": 1}}
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
                         "policy_primary_term": self.indices[i][2]} for i in matched}
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


def run1(fake, argv):
    lines = []
    code = accounts_step.main(argv, send=fake.handle, out=lines.append)
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
    assert lines == [f"error: GET /_plugins/_security/api/roles as {OWN_USER}: HTTP 401 (security_exception)"]
    assert fake.writes == []


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
    assert found["streams"]["metrics"] == {"indices": 1, "off_policy": 1, "no_policy": 1}


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
    return [d for d in yaml.safe_load_all((APP / name).read_text()) if d]


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
        assert set(hot["actions"][0]["rollover"]) == {"min_index_age", "min_doc_count", "min_primary_shard_size"}
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


def test_the_delete_age_is_the_recommended_answer_of_the_open_decision():
    assert retention()["delete_age"] == "120d"
    assert any("Open decision" in note and "recommended answer of the open decision" in note
               for note in retention()["notes"])


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


def test_the_user_for_settings_has_no_security_api_and_reads_no_document():
    role = accounts()["roles"]["tnwks-settings"]
    assert "restapi" not in json.dumps(role) and "security" not in json.dumps(role["cluster_permissions"])
    for permission in role["index_permissions"]:
        assert "read" not in permission["allowed_actions"]
        assert not [a for a in permission["allowed_actions"] if a.startswith("indices:data")]


@pytest.mark.parametrize("prefix", sorted(HASHED))
def test_the_name_of_each_job_carries_the_hash_of_its_files(prefix):
    digest = hashlib.sha256(b"".join((APP / f).read_bytes() for f in HASHED[prefix])).hexdigest()[:10]
    want = f"{prefix}-{digest}"
    found = [d["metadata"]["name"] for name in ("accounts-job.yaml", "settings-job.yaml") for d in docs(name)
             if d["metadata"]["name"].startswith(prefix + "-")]
    assert found == [want], f"the Job of {prefix} must be named {want}: its files changed"
    kustomization = yaml.safe_load((APP / "kustomization.yaml").read_text())
    generated = {g["name"]: g["files"] for g in kustomization["configMapGenerator"]}
    files = {"wazuh-accounts": "wazuh-accounts-files", "wazuh-settings-git": "wazuh-settings-files"}[prefix]
    assert generated[files] == [f"./{f}" for f in HASHED[prefix]]


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
    assert float(args["margin"]) > spec["activeDeadlineSeconds"] >= float(args["wait"]) + float(
        args["login-wait"])
