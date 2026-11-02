"""Turn the homelab-wsl backup certificate into AWS STS credentials.

IAM Roles Anywhere: aws_signing_helper presents the X.509 certificate
(CN homelab-wsl-backups, issued by the tnwks-ops CA) and gets a 4-hour session
as tnwks-rolesanywhere-backups, which may only read and write the backup
bucket. This writes the session into the Secret `aws-backup-sts` in every
namespace in TARGET_NAMESPACES, where barman-cloud (databases) and kopiur
(kopiur-system) read it. Runs every 30 minutes; see docs/operations/backups.md.

Never prints credential values.
"""

import base64
import hashlib
import json
import os
import ssl
import subprocess
import sys
import urllib.request

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
HELPER = "/tmp/aws_signing_helper"


def env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"missing env {name}")
    return value


def fetch_helper() -> None:
    url, want = env("HELPER_URL"), env("HELPER_SHA256")
    with urllib.request.urlopen(url, timeout=120) as resp:
        data = resp.read()
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        sys.exit(f"aws_signing_helper checksum mismatch: got {got}, want {want}")
    with open(HELPER, "wb") as f:
        f.write(data)
    os.chmod(HELPER, 0o755)


def session() -> dict:
    cmd = [
        HELPER, "credential-process",
        "--certificate", "/certs/tls.crt",
        "--private-key", "/certs/tls.key",
        "--trust-anchor-arn", env("TRUST_ANCHOR_ARN"),
        "--profile-arn", env("PROFILE_ARN"),
        "--role-arn", env("ROLE_ARN"),
        "--session-duration", env("SESSION_SECONDS"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        # The helper's errors don't contain credentials.
        sys.exit(f"credential-process failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def patch_secret(namespace: str, name: str, creds: dict, region: str) -> None:
    b64 = lambda s: base64.b64encode(s.encode()).decode()
    body = {
        "metadata": {
            "annotations": {"backups.tnwks.us/expires": creds["Expiration"]},
        },
        "data": {
            "AWS_ACCESS_KEY_ID": b64(creds["AccessKeyId"]),
            "AWS_SECRET_ACCESS_KEY": b64(creds["SecretAccessKey"]),
            "AWS_SESSION_TOKEN": b64(creds["SessionToken"]),
            "AWS_REGION": b64(region),
        },
    }
    with open(f"{SA}/token") as f:
        token = f.read()
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ["KUBERNETES_SERVICE_PORT"]
    req = urllib.request.Request(
        f"https://{host}:{port}/api/v1/namespaces/{namespace}/secrets/{name}",
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


def main() -> None:
    fetch_helper()
    creds = session()
    region = env("AWS_REGION")
    name = env("SECRET_NAME")
    for ns in env("TARGET_NAMESPACES").split(","):
        patch_secret(ns.strip(), name, creds, region)
        print(f"refreshed {ns.strip()}/{name}, expires {creds['Expiration']}")


if __name__ == "__main__":
    main()
