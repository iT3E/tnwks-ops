"""The credential refresher of the security platform keeps its contract.

kubernetes/apps/security/aws-credentials: the script reads the end of a
certificate, writes it to a Lease, and writes a session to a Secret. The names
in its manifests agree with each other.
"""

import base64
import datetime
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest
import yaml

APP = Path(__file__).resolve().parents[2] / "kubernetes/apps/security/aws-credentials/app"
UTC = datetime.UTC

# Two self-signed certificates made for this test with a key that was thrown
# away. The first ends in 2027 (a UTCTime), the second in 2081 (a
# GeneralizedTime, the form of an end date from 2050 on).
ENDS_2027 = """-----BEGIN CERTIFICATE-----
MIIBiTCCAS6gAwIBAgIUKrTPvoeYQGzVZYE+F0b5bkv7o2MwCgYIKoZIzj0EAwIw
KjENMAsGA1UECgwEdGVzdDEZMBcGA1UEAwwQdGVzdC1jZXJ0aWZpY2F0ZTAeFw0y
NjExMTMxMzE3MDJaFw0yNzExMTMxMzE3MDJaMCoxDTALBgNVBAoMBHRlc3QxGTAX
BgNVBAMMEHRlc3QtY2VydGlmaWNhdGUwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNC
AARTfb6o+BfcUx89n3cfT/9z3pthu2o+ni7L4EiCQB21XsvP6PHnCaZp19xm+bUS
pmSK1mcB0FbUeJr6KiG1juzTozIwMDAdBgNVHQ4EFgQUS5oHg5IM5M66qwYe3zt8
KNz7abAwDwYDVR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAgNJADBGAiEAv5vWmswg
pL5WAffcbzxbx8qL7ikn70ryBgG2gyxPNlACIQC4tpGz9wSgpXgGQn/I4HuVBXDV
GG9nT38N4AVc8u5JKg==
-----END CERTIFICATE-----
"""
ENDS_2081 = """-----BEGIN CERTIFICATE-----
MIIBijCCATCgAwIBAgIUIRXHG/5dOMwzKdFzhrcQ6ATHve4wCgYIKoZIzj0EAwIw
KjENMAsGA1UECgwEdGVzdDEZMBcGA1UEAwwQdGVzdC1jZXJ0aWZpY2F0ZTAgFw0y
NjExMTMxMzE3MDJaGA8yMDgxMDgxNjEzMTcwMlowKjENMAsGA1UECgwEdGVzdDEZ
MBcGA1UEAwwQdGVzdC1jZXJ0aWZpY2F0ZTBZMBMGByqGSM49AgEGCCqGSM49AwEH
A0IABBZ9eEagaMD5sucuWYmBxM4SoxfMCh0FwHlOqqP3rbY3A99fQjgHO4Qi/4wj
KuPOK3K1MRm7kP5J6ucooDUxRX6jMjAwMB0GA1UdDgQWBBQZp2PvYUFFUQQAhNT7
EIMQz5pNZjAPBgNVHRMBAf8EBTADAQH/MAoGCCqGSM49BAMCA0gAMEUCIDJtiNF1
bqrtJOVQFN6cJ6v2PutSsVAoKeaz0k8ziioXAiEAz7ivFEWdMVIpTr9jyhCqL057
1h1BpmZ9E1CogMS4Z90=
-----END CERTIFICATE-----
"""

CREDS = {
    "AccessKeyId": "an-access-key-id",
    "SecretAccessKey": "a-secret-access-key",
    "SessionToken": "a-session-token",
    "Expiration": "2026-11-13T16:00:00Z",
}


@pytest.fixture(scope="module")
def refresh():
    spec = importlib.util.spec_from_file_location("refresh", APP / "scripts/refresh.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def documents(name):
    return [doc for doc in yaml.safe_load_all((APP / name).read_text()) if doc]


def sessions():
    (cronjob,) = documents("cronjob.yaml")
    (container,) = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    env = {item["name"]: item.get("value") for item in container["env"]}
    return cronjob, container, json.loads(env["SESSIONS"])


def test_the_end_of_a_certificate_is_read_in_both_time_forms(refresh):
    assert refresh.certificate_not_after(ENDS_2027) == datetime.datetime(
        2027, 11, 13, 13, 17, 2, tzinfo=UTC)
    assert refresh.certificate_not_after(ENDS_2081) == datetime.datetime(
        2081, 8, 16, 13, 17, 2, tzinfo=UTC)


def test_the_first_certificate_of_a_chain_counts(refresh):
    assert refresh.certificate_not_after(ENDS_2027 + ENDS_2081).year == 2027


def test_a_text_with_no_certificate_is_an_error(refresh):
    with pytest.raises(ValueError):
        refresh.certificate_not_after("not a certificate")


def test_the_lease_gets_the_end_as_a_micro_time(refresh):
    end = datetime.datetime(2027, 11, 13, 13, 17, 2, tzinfo=UTC)
    assert refresh.lease_patch(end) == {"spec": {"renewTime": "2027-11-13T13:17:02.000000Z"}}


def test_the_secret_gets_the_session_and_both_end_times(refresh):
    end = datetime.datetime(2027, 11, 13, 13, 17, 2, tzinfo=UTC)
    body = refresh.secret_patch(CREDS, "us-west-2", end)
    decoded = {key: base64.b64decode(value).decode() for key, value in body["data"].items()}
    assert decoded == {
        "AWS_ACCESS_KEY_ID": "an-access-key-id",
        "AWS_SECRET_ACCESS_KEY": "a-secret-access-key",
        "AWS_SESSION_TOKEN": "a-session-token",
        "AWS_REGION": "us-west-2",
    }
    assert body["metadata"]["annotations"] == {
        "security.tnwks.us/expires": "2026-11-13T16:00:00Z",
        "security.tnwks.us/certificate-not-after": "2027-11-13T13:17:02Z",
    }


def entry(tmp_path, name="audit"):
    directory = tmp_path / name
    directory.mkdir()
    (directory / "tls.crt").write_text(ENDS_2027)
    return {"name": name, "certificate": str(directory), "profile_arn": "profile",
            "role_arn": "role", "secret": f"{name}-sts", "lease": f"{name}-certificate"}


def test_a_refresh_writes_the_lease_then_the_secret_and_prints_no_credential(
        refresh, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(refresh, "patch", lambda path, body: calls.append((path, body)))
    monkeypatch.setattr(refresh, "session", lambda item: CREDS)
    now = datetime.datetime(2027, 10, 1, tzinfo=UTC)
    line = refresh.refresh(entry(tmp_path), "security", "us-west-2", now)
    assert [path for path, _ in calls] == [
        "/apis/coordination.k8s.io/v1/namespaces/security/leases/audit-certificate",
        "/api/v1/namespaces/security/secrets/audit-sts",
    ]
    assert "its certificate ends 2027-11-13, in 43 days" in line
    for secret in ("an-access-key-id", "a-secret-access-key", "a-session-token"):
        assert secret not in line


def test_the_lease_has_the_end_even_when_the_session_is_refused(refresh, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(refresh, "patch", lambda path, body: calls.append(path))

    def refused(item):
        raise RuntimeError("credential-process failed")

    monkeypatch.setattr(refresh, "session", refused)
    with pytest.raises(RuntimeError):
        refresh.refresh(entry(tmp_path), "security", "us-west-2", datetime.datetime.now(UTC))
    assert calls == ["/apis/coordination.k8s.io/v1/namespaces/security/leases/audit-certificate"]


def test_one_entry_that_fails_does_not_stop_the_next_and_the_run_fails(
        refresh, monkeypatch, capsys):
    monkeypatch.setenv("SESSIONS", json.dumps([{"name": "audit"}, {"name": "trail"}]))
    monkeypatch.setenv("NAMESPACE", "security")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setattr(refresh, "fetch_helper", lambda: None)
    done = []

    def one(item, namespace, region, now):
        if item["name"] == "audit":
            raise RuntimeError("refused")
        done.append(item["name"])
        return "refreshed"

    monkeypatch.setattr(refresh, "refresh", one)
    with pytest.raises(SystemExit) as stop:
        refresh.main()
    assert stop.value.code == "failed: audit"
    assert done == ["trail"]
    assert "audit: RuntimeError: refused" in capsys.readouterr().err


def test_a_helper_with_another_checksum_is_not_kept(refresh, tmp_path, monkeypatch):
    payload = b"a helper binary"
    monkeypatch.setenv("HELPER_URL", "https://example.invalid/helper")
    monkeypatch.setattr(refresh.urllib.request, "urlopen",
                        lambda url, timeout: io.BytesIO(payload))
    target = tmp_path / "helper"

    monkeypatch.setenv("HELPER_SHA256", "0" * 64)
    with pytest.raises(RuntimeError):
        refresh.fetch_helper(str(target))
    assert not target.exists()

    monkeypatch.setenv("HELPER_SHA256", hashlib.sha256(payload).hexdigest())
    refresh.fetch_helper(str(target))
    assert target.read_bytes() == payload
    assert target.stat().st_mode & 0o111


def test_the_manifests_name_the_same_secrets_and_leases():
    """One Secret and one Lease for each entry, and the Role admits those and no other."""
    _, _, entries = sessions()
    secrets = {doc["metadata"]["name"] for doc in documents("secret-sts.yaml")}
    leases = {doc["metadata"]["name"] for doc in documents("lease.yaml")}
    assert {item["secret"] for item in entries} == secrets
    assert {item["lease"] for item in entries} == leases
    role = next(doc for doc in documents("rbac.yaml") if doc["kind"] == "Role")
    by_resource = {rule["resources"][0]: rule for rule in role["rules"]}
    assert set(by_resource["secrets"]["resourceNames"]) == secrets
    assert set(by_resource["leases"]["resourceNames"]) == leases
    for rule in role["rules"]:
        assert rule["verbs"] == ["get", "patch"]


def test_each_entry_reads_a_mounted_certificate():
    cronjob, container, entries = sessions()
    mounts = {mount["mountPath"]: mount["name"] for mount in container["volumeMounts"]}
    volumes = {volume["name"]: volume
               for volume in cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"]}
    certificates = set()
    for item in entries:
        volume = volumes[mounts[item["certificate"]]]
        certificates.add(volume["secret"]["secretName"])
    assert certificates == {"aws-security-audit-certificate", "aws-security-trail-certificate"}


def test_the_job_stays_inside_the_contract_of_the_namespace():
    """kubernetes/apps/security/README.md: the node selector, and 64 MiB for the refresher."""
    cronjob, container, _ = sessions()
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert pod["nodeSelector"] == {"kubernetes.io/hostname": "${SECURITY_NODE}"}
    assert container["resources"]["limits"] == {"memory": "64Mi"}
    assert container["resources"]["requests"]["memory"]
