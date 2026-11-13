"""Turn each IAM Roles Anywhere certificate of the security platform into an AWS session.

For each entry of SESSIONS, aws_signing_helper presents the X.509 certificate
(issued by the tnwks-ops CA) and gets a 4-hour session as the role of that
entry. The session goes into one Secret of this namespace, which one tool
reads. The end time of the certificate goes into the renew time of one Lease,
which kube-state-metrics serves as kube_lease_renew_time: that series is how
the alert for a certificate near its end knows the date. Runs every 30
minutes. README.md of this app has the contract.

One entry that fails does not stop the others, and the run then fails as a
whole. Never prints credential values.
"""

import base64
import datetime
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
HELPER = "/tmp/aws_signing_helper"
ANNOTATIONS = "security.tnwks.us"
UTC = datetime.UTC


def env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"missing env {name}")
    return value


def fetch_helper(path: str = HELPER) -> None:
    """Download the helper in pieces, so that it is never whole in memory."""
    url, want = env("HELPER_URL"), env("HELPER_SHA256")
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=120) as resp, open(path, "wb") as out:
        while chunk := resp.read(1 << 16):
            digest.update(chunk)
            out.write(chunk)
    got = digest.hexdigest()
    if got != want:
        os.remove(path)
        raise RuntimeError(f"aws_signing_helper checksum mismatch: got {got}, want {want}")
    os.chmod(path, 0o755)


def _element(der: bytes, offset: int) -> tuple[int, int, int]:
    """One DER element at `offset`: its tag, and where its value starts and ends."""
    tag, length = der[offset], der[offset + 1]
    offset += 2
    if length & 0x80:
        size = length & 0x7F
        length = int.from_bytes(der[offset:offset + size], "big")
        offset += size
    return tag, offset, offset + length


def certificate_not_after(pem: str) -> datetime.datetime:
    """The end of the validity of the first certificate of a PEM text.

    The standard library has no public call for this, so it walks the DER
    form: Certificate, tbsCertificate, then past the version, the serial
    number, the signature algorithm and the issuer to the validity.
    """
    found = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.DOTALL)
    if not found:
        raise ValueError("no certificate in the PEM text")
    der = ssl.PEM_cert_to_DER_cert(found.group(0))
    _, start, _ = _element(der, 0)
    _, start, _ = _element(der, start)
    tag, _, end = _element(der, start)
    offset = end if tag == 0xA0 else start
    for _ in range(3):
        _, _, offset = _element(der, offset)
    _, start, _ = _element(der, offset)
    _, _, offset = _element(der, start)
    tag, start, end = _element(der, offset)
    text = der[start:end].decode("ascii")
    if tag == 0x17:  # UTCTime: two digits of the year, 1950 to 2049
        text = ("20" if int(text[:2]) < 50 else "19") + text
    elif tag != 0x18:  # GeneralizedTime
        raise ValueError(f"unexpected time tag {tag:#x}")
    return datetime.datetime.strptime(text, "%Y%m%d%H%M%SZ").replace(tzinfo=UTC)


def session(entry: dict) -> dict:
    cmd = [
        HELPER, "credential-process",
        "--certificate", f"{entry['certificate']}/tls.crt",
        "--private-key", f"{entry['certificate']}/tls.key",
        "--trust-anchor-arn", env("TRUST_ANCHOR_ARN"),
        "--profile-arn", entry["profile_arn"],
        "--role-arn", entry["role_arn"],
        "--session-duration", env("SESSION_SECONDS"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0:
        # The helper's errors don't contain credentials.
        raise RuntimeError(f"credential-process failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def secret_patch(creds: dict, region: str, not_after: datetime.datetime) -> dict:
    def b64(text: str) -> str:
        return base64.b64encode(text.encode()).decode()

    return {
        "metadata": {
            "annotations": {
                f"{ANNOTATIONS}/expires": creds["Expiration"],
                f"{ANNOTATIONS}/certificate-not-after": not_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        },
        "data": {
            "AWS_ACCESS_KEY_ID": b64(creds["AccessKeyId"]),
            "AWS_SECRET_ACCESS_KEY": b64(creds["SecretAccessKey"]),
            "AWS_SESSION_TOKEN": b64(creds["SessionToken"]),
            "AWS_REGION": b64(region),
        },
    }


def lease_patch(not_after: datetime.datetime) -> dict:
    """The renew time of the Lease is the end of the certificate (a MicroTime)."""
    return {"spec": {"renewTime": not_after.strftime("%Y-%m-%dT%H:%M:%S.000000Z")}}


def patch(path: str, body: dict) -> None:
    with open(f"{SA}/token") as f:
        token = f.read()
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ["KUBERNETES_SERVICE_PORT"]
    req = urllib.request.Request(
        f"https://{host}:{port}{path}",
        data=json.dumps(body).encode(),
        method="PATCH",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/merge-patch+json",
        },
    )
    ctx = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        resp.read()


def refresh(entry: dict, namespace: str, region: str, now: datetime.datetime) -> str:
    """One entry: the Lease first, so the end date is known even when AWS refuses the session."""
    with open(f"{entry['certificate']}/tls.crt") as f:
        not_after = certificate_not_after(f.read())
    patch(f"/apis/coordination.k8s.io/v1/namespaces/{namespace}/leases/{entry['lease']}", lease_patch(not_after))
    creds = session(entry)
    patch(f"/api/v1/namespaces/{namespace}/secrets/{entry['secret']}", secret_patch(creds, region, not_after))
    days = (not_after - now).days
    return (f"refreshed {namespace}/{entry['secret']}, expires {creds['Expiration']};"
            f" its certificate ends {not_after:%Y-%m-%d}, in {days} days")


def main() -> None:
    entries = json.loads(env("SESSIONS"))
    namespace, region = env("NAMESPACE"), env("AWS_REGION")
    fetch_helper()
    failed = []
    for entry in entries:
        try:
            print(refresh(entry, namespace, region, datetime.datetime.now(UTC)))
        except Exception as error:  # noqa: BLE001 - each entry stands alone; the run fails below
            failed.append(entry["name"])
            print(f"{entry['name']}: {type(error).__name__}: {error}", file=sys.stderr)
    if failed:
        sys.exit(f"failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
