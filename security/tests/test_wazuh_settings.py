"""The settings job of the Wazuh indexer (kubernetes/apps/security/wazuh-settings):
the accounts and the retention of git, against a fake indexer API.

No test reaches an indexer. The fake keeps the policies, the managed indices,
the roles, the users and the role mappings in memory, answers as the APIs of
OpenSearch and of the Setup plugin answer, and applies a change of policy at its
next poll, as ISM does on its next run. The passwords are invented.
"""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/security/wazuh-settings/app"
CONFIG = APP / "config"


def invented(name: str) -> str:
    """A value that meets the password rule, made up for the tests."""
    return f"{name.title()}-1-invented"


ACCOUNT_PASSWORD = invented("account")
SETTINGS_PASSWORD = invented("settings")
CONVERTER_PASSWORD = invented("converter")


def load():
    spec = importlib.util.spec_from_file_location("wazuh_settings_apply", APP / "scripts/apply.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


apply = load()


class FakeIndexer:
    def __init__(self):
        self.users = {"tnwks-accounts": {"password": ACCOUNT_PASSWORD, "reserved": False},
                      "admin": {"password": invented("admin"), "reserved": True}}
        self.roles = {"all_access": {"reserved": True, "cluster_permissions": ["*"]}}
        self.mappings = {}
        self.policies = {"stream-events-policy": {"seq": 1, "term": 1, "policy": {"description": "shipped"}}}
        # backing index -> [policy_id, seq, term]
        self.indices = {".ds-wazuh-events-v5-system-000001": ["stream-events-policy", 1, 1],
                        ".ds-wazuh-metrics-agents-000001": [None, None, None]}
        self.pending = {}
        self.settings = None
        self.raw_count = 0
        self.writes = []
        self.seq = 10

    def handle(self, method, path, body, user, password):
        if self.users.get(user, {}).get("password") != password:
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
        if parts == ["wazuh-events-raw-v5", "_count"]:
            return (200, {"count": self.raw_count}) if self.raw_count else (404, None)
        return 400, None

    def security(self, method, parts, body):
        if parts == ["authinfo"]:
            return 200, {}
        kind, name = parts[1], parts[2]
        store = {"roles": self.roles, "internalusers": self.users, "rolesmapping": self.mappings}[kind]
        if method == "GET":
            if name not in store:
                return 404, {"status": "NOT_FOUND"}
            shown = {k: v for k, v in store[name].items() if k != "password"}
            return 200, {name: shown}
        if method == "PUT":
            if kind == "internalusers":
                store[name] = {"password": body["password"], "reserved": False}
            elif kind == "roles":
                store[name] = {**copy.deepcopy(body), "index_permissions": [
                    {"dls": "", "fls": [], "masked_fields": [], **e} for e in body["index_permissions"]]}
            else:
                store[name] = copy.deepcopy(body)
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
            prefix = parts[1].rstrip("*")
            # ISM applies a pending change on its next run.
            for index in [i for i in self.pending if i.startswith(prefix)]:
                policy_id = self.pending.pop(index)
                p = self.policies[policy_id]
                self.indices[index] = [policy_id, p["seq"], p["term"]]
            found = {i: {"index.plugins.index_state_management.policy_id": s[0], "policy_id": s[0],
                         "policy_seq_no": s[1], "policy_primary_term": s[2]}
                     for i, s in self.indices.items() if i.startswith(prefix)}
            return 200, {**found, "total_managed_indices": len(found)}
        if parts[0] in ("add", "change_policy"):
            self.pending[parts[1]] = body["policy_id"]
            return 200, {"updated_indices": 1, "failures": False, "failed_indices": []}
        return 400, None


def secrets(tmp_path):
    files = {"accounts/username": "tnwks-accounts", "accounts/password": ACCOUNT_PASSWORD,
             "settings/password": SETTINGS_PASSWORD, "converter/password": CONVERTER_PASSWORD}
    for name, value in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(value + "\n")
    config = tmp_path / "config"
    config.mkdir()
    accounts = json.loads((CONFIG / "accounts.json").read_text())
    for spec in accounts["users"].values():
        spec["password_file"] = str(tmp_path / spec["password_file"].removeprefix("/secrets/"))
    (config / "accounts.json").write_text(json.dumps(accounts))
    (config / "retention.json").write_text((CONFIG / "retention.json").read_text())
    return ["--indexer=https://indexer.test:9200", f"--config={config}",
            f"--accounts={tmp_path / 'accounts'}", "--wait=60"]


def run(fake, argv):
    lines = []
    clock = iter(range(0, 10_000, 15))
    code = apply.main(argv, send=fake.handle, sleep=lambda _: None, clock=lambda: next(clock),
                      out=lines.append)
    return code, lines


def test_a_first_run_makes_the_accounts_and_the_policies(tmp_path):
    fake = FakeIndexer()
    code, lines = run(fake, secrets(tmp_path))
    assert code == 0, lines
    assert fake.users["tnwks-settings"]["password"] == SETTINGS_PASSWORD
    assert fake.users["tnwks-converter"]["password"] == CONVERTER_PASSWORD
    assert fake.mappings["tnwks-converter"]["users"] == ["tnwks-converter"]
    assert sorted(p for p in fake.policies if p.startswith("tnwks-")) == [
        "tnwks-stream-active-responses", "tnwks-stream-events", "tnwks-stream-findings",
        "tnwks-stream-metrics", "tnwks-stream-raw-events"]
    # The shipped policy is never written.
    assert fake.policies["stream-events-policy"]["policy"] == {"description": "shipped"}
    assert fake.indices[".ds-wazuh-events-v5-system-000001"][0] == "tnwks-stream-events"
    assert fake.indices[".ds-wazuh-metrics-agents-000001"][0] == "tnwks-stream-metrics"
    assert fake.settings == {"engine": {"index_raw_events": False}}
    assert lines[-1] == "done"


def test_a_second_run_writes_only_the_setting(tmp_path):
    fake = FakeIndexer()
    argv = secrets(tmp_path)
    assert run(fake, argv)[0] == 0
    fake.writes.clear()
    code, lines = run(fake, argv)
    assert code == 0, lines
    assert fake.writes == [("PUT", "/_plugins/_setup/settings")]
    assert all("unchanged" in line or line.startswith(("setting ", "raw events", "done")) for line in lines)


def test_no_password_reaches_the_log(tmp_path):
    fake = FakeIndexer()
    _, lines = run(fake, secrets(tmp_path))
    text = "\n".join(lines)
    for value in (ACCOUNT_PASSWORD, SETTINGS_PASSWORD, CONVERTER_PASSWORD):
        assert value not in text


def test_a_changed_age_moves_each_index_to_the_new_version(tmp_path):
    fake = FakeIndexer()
    argv = secrets(tmp_path)
    assert run(fake, argv)[0] == 0
    config = Path(argv[1].split("=", 1)[1])
    retention = json.loads((config / "retention.json").read_text())
    retention["delete_age"] = "90d"
    (config / "retention.json").write_text(json.dumps(retention))
    code, lines = run(fake, argv)
    assert code == 0, lines
    events = fake.policies["tnwks-stream-events"]
    assert events["policy"]["states"][0]["transitions"][0]["conditions"] == {"min_index_age": "90d"}
    assert fake.indices[".ds-wazuh-events-v5-system-000001"][1:] == [events["seq"], events["term"]]
    assert "index .ds-wazuh-events-v5-system-000001: change_policy to tnwks-stream-events" in lines
    # The metrics policy did not change, so its index stays.
    assert "policy tnwks-stream-metrics: unchanged" in lines


def test_a_move_that_ism_does_not_apply_fails_the_run(tmp_path):
    fake = FakeIndexer()
    fake.ism_original = fake.ism

    def stuck(method, parts, path, body):
        if parts[0] == "explain":
            saved, fake.pending = fake.pending, {}
            try:
                return fake.ism_original(method, parts, path, body)
            finally:
                fake.pending = saved
        return fake.ism_original(method, parts, path, body)

    fake.ism = stuck
    code, lines = run(fake, secrets(tmp_path))
    assert code == 1
    assert lines[-1].startswith("error: ISM has not applied the move yet for .ds-wazuh-")
    assert "do not restart the indexer" in lines[-1]


def test_a_reserved_role_of_the_same_name_stops_the_run(tmp_path):
    fake = FakeIndexer()
    fake.roles["tnwks-settings"] = {"reserved": True}
    code, lines = run(fake, secrets(tmp_path))
    assert code == 1
    assert lines[-1] == "error: role tnwks-settings is reserved: the job never changes it"
    assert fake.roles["tnwks-settings"] == {"reserved": True}


def test_a_changed_password_in_the_secret_is_set(tmp_path):
    fake = FakeIndexer()
    argv = secrets(tmp_path)
    assert run(fake, argv)[0] == 0
    (tmp_path / "converter/password").write_text(invented("newconverter") + "\n")
    code, lines = run(fake, argv)
    assert code == 0, lines
    assert fake.users["tnwks-converter"]["password"] == invented("newconverter")
    assert "user tnwks-converter: password set" in lines


@pytest.mark.parametrize("value", ["short-A1", "nouppercase-12345", "NOLOWERCASE-12345", "NoDigits-abcdefgh",
                                   "NoSymbol12345abcd", "Has space-12345ab", "x" * 60 + "Aa1-xxxxx"])
def test_a_password_that_breaks_the_rule_is_never_sent(tmp_path, value):
    fake = FakeIndexer()
    argv = secrets(tmp_path)
    (tmp_path / "converter/password").write_text(value)
    code, lines = run(fake, argv)
    assert code == 1
    assert "tnwks-converter" not in fake.users
    assert lines[-1].startswith("error: the password of tnwks-converter breaks the password rule")
    assert value not in "\n".join(lines)


def test_the_account_user_with_a_wrong_password_stops_at_once(tmp_path):
    fake = FakeIndexer()
    argv = secrets(tmp_path)
    (tmp_path / "accounts/password").write_text(invented("wrong"))
    code, lines = run(fake, argv)
    assert code == 1
    assert lines == [("error: GET /_plugins/_security/api/roles/tnwks-settings as tnwks-accounts: "
                      "HTTP 401 (security_exception)")]
    assert fake.writes == []


def test_each_policy_rolls_over_by_age_and_deletes_by_index_age():
    retention = json.loads((CONFIG / "retention.json").read_text())
    policies = apply.build_policies(retention)
    assert not set(policies) & apply.SHIPPED_POLICY_IDS
    for policy in policies.values():
        hot, delete = policy["states"]
        assert hot["actions"][0]["rollover"]["min_index_age"] == retention["rollover_age"]
        (transition,) = hot["transitions"]
        assert set(transition["conditions"]) == {"min_index_age"}
        assert delete["actions"][0]["delete"] == {}
        assert policy["ism_template"][0]["priority"] == 1
    events = policies["tnwks-stream-events"]["states"][0]["transitions"][0]["conditions"]
    metrics = policies["tnwks-stream-metrics"]["states"][0]["transitions"][0]["conditions"]
    assert events == {"min_index_age": retention["delete_age"]}
    assert metrics == {"min_index_age": retention["metrics_delete_age"]}


def test_the_patterns_are_those_of_the_shipped_policies():
    # plugins/setup/src/main/resources/policies/*.json at wazuh-indexer-plugins v5.0.0-rc2.
    shipped = {
        "events": [".ds-wazuh-events-v5-*", "wazuh-events-v5*"],
        "findings": [".ds-wazuh-findings-v5-*", "wazuh-findings-v5*"],
        "raw-events": [".ds-wazuh-events-raw-v5-*", "wazuh-events-raw-v5*"],
        "active-responses": [".ds-wazuh-active-responses-*", "wazuh-active-responses*"],
        "metrics": [".ds-wazuh-metrics-*", "wazuh-metrics*"],
    }
    retention = json.loads((CONFIG / "retention.json").read_text())
    assert {s: spec["patterns"] for s, spec in retention["streams"].items()} == shipped
    assert retention["index_raw_events"] is False


def test_the_delete_age_is_the_floor_of_the_open_decision():
    # 90 days plus one rollover period, until the design gate decides.
    retention = json.loads((CONFIG / "retention.json").read_text())
    assert (retention["rollover_age"], retention["delete_age"], retention["metrics_delete_age"]) == (
        "30d", "120d", "60d")
    assert any("Open decision" in note for note in retention["notes"])


def test_the_converter_reads_no_host_field_of_the_snapshots():
    accounts = json.loads((CONFIG / "accounts.json").read_text())
    role = accounts["roles"]["tnwks-converter"]
    (snapshots,) = [p for p in role["index_permissions"] if "wazuh-metrics-agents" in p["index_patterns"]]
    assert all(not f.startswith("~") for f in snapshots["fls"])
    assert not [f for f in snapshots["fls"] if "host" in f or "os" in f.split(".") or "groups" in f]
    for permission in role["index_permissions"]:
        assert permission["allowed_actions"] == ["read"]
        assert not [p for p in permission["index_patterns"] if "events" in p or "fim" in p or "*" == p]
    assert "restapi" not in json.dumps(accounts["roles"]["tnwks-settings"])
