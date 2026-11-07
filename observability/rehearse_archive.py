"""Disposable Garage/Thanos acceptance test. No kubeconfig or live APIs are used."""

import json
import os
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from tests.archive_check import S3
from versions import CADDY, GARAGE, PROMETHEUS, THANOS

ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout


def eventually(function, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return function()
        except (OSError, ValueError, AssertionError, urllib.error.URLError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def get(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.read()


def main():
    suffix = secrets.token_hex(4)
    names = {role: f"obs-test-{suffix}-{role}" for role in ["garage", "tls", "store", "query"]}
    user = f"{os.getuid()}:{os.getgid()}"
    with tempfile.TemporaryDirectory(prefix="observability-archive-") as tmp:
        base = Path(tmp)
        for name in ["secrets", "metadata", "data", "blocks"]:
            (base / name).mkdir(mode=0o700)
        for key in ["admin-token", "metrics-token", "rpc-secret", "secret-key"]:
            (base / "secrets" / key).write_text(secrets.token_hex(32))
        (base / "secrets/access-key").write_text("GK" + secrets.token_hex(12))
        config = (ROOT / "kubernetes/apps/monitoring/garage/app/garage.toml").read_text()
        config = config.replace("127.0.0.1:3900", "127.0.0.1:13103").replace(
            "0.0.0.0:3903", "127.0.0.1:13104").replace(
            "0.0.0.0:3901", "127.0.0.1:13105").replace("127.0.0.1:3901", "127.0.0.1:13105")
        (base / "garage.toml").write_text(config)
        access = (base / "secrets/access-key").read_text()
        secret = (base / "secrets/secret-key").read_text()
        (base / "env").write_text(f"GARAGE_DEFAULT_BUCKET=homelab-metrics\nGARAGE_DEFAULT_ACCESS_KEY={access}\nGARAGE_DEFAULT_SECRET_KEY={secret}\n")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
            "ec_paramgen_curve:prime256v1", "-nodes", "-days", "1", "-subj", "/CN=archive-fixture",
            "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", str(base / "fixture.key"),
            "-out", str(base / "fixture.crt")], check=True, capture_output=True)
        (base / "fixture.key").chmod(0o600)
        caddy = (ROOT / "kubernetes/apps/monitoring/garage/app/Caddyfile").read_text()
        caddy = caddy.replace(":8443", "127.0.0.1:13113").replace("127.0.0.1:3900", "127.0.0.1:13103")
        (base / "Caddyfile").write_text(caddy)
        objstore = {"type": "S3", "config": {"bucket": "homelab-metrics", "region": "garage",
                    "endpoint": "127.0.0.1:13113", "insecure": False, "bucket_lookup_type": "path",
                    "access_key": access, "secret_key": secret,
                    "http_config": {"tls_config": {"ca_file": "/tls/tls.crt"}}}}
        (base / "objstore.yaml").write_text(yaml.safe_dump(objstore))
        # The direct S3 client exercises the pod-local HTTP backend. Thanos uses
        # verified HTTPS and mutual TLS for StoreAPI; all credentials are disposable.
        for file in (base / "secrets").iterdir():
            file.chmod(0o600)
        (base / "env").chmod(0o600)
        (base / "objstore.yaml").chmod(0o600)
        started = []

        def run(role, image, args, mounts=(), writable=()):
            cmd = ["run", "-d", "--name", names[role], "--network", "host", "--read-only",
                   "--memory", "768m", "--cpus", "1", "--user", user]
            if role == "garage":
                cmd += ["--env-file", str(base / "env")]
            for src, dst in mounts:
                cmd += ["--mount", f"type=bind,src={base / src},dst={dst},readonly"]
            for src, dst in writable:
                cmd += ["--mount", f"type=bind,src={base / src},dst={dst}"]
            if role == "store":
                cmd += ["--tmpfs", f"/cache:uid={os.getuid()},gid={os.getgid()}"]
            if role == "tls":
                cmd += ["--tmpfs", f"/tmp:uid={os.getuid()},gid={os.getgid()}",
                        "-e", "XDG_DATA_HOME=/tmp/data", "-e", "XDG_CONFIG_HOME=/tmp/config"]
            docker(*cmd, image, *args)
            started.append(names[role])

        try:
            run("garage", GARAGE, ["/garage", "server", "--single-node", "--default-bucket"],
                [("garage.toml", "/etc/garage.toml"), ("secrets", "/secrets")],
                [("data", "/data"), ("metadata", "/metadata")])
            eventually(lambda: get("http://127.0.0.1:13104/health"))
            tls_mounts = [("fixture.crt", "/tls/tls.crt"), ("fixture.key", "/tls/tls.key")]
            run("tls", CADDY, ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
                [("Caddyfile", "/etc/caddy/Caddyfile"), *tls_mounts])
            s3 = S3(base)
            s3.multipart_check()
            body = bytes(range(256)) * 8192
            s3.request("PUT", "acceptance/roundtrip", body)
            assert s3.request("GET", "acceptance/roundtrip") == body
            assert s3.request("GET", "acceptance/roundtrip", headers={"range": "bytes=10-31"}) == body[10:32]
            start = int(time.time()) - 18000
            samples = ["# TYPE observability_archive_fixture gauge"]
            samples += [f'observability_archive_fixture{{fixture="garage"}} 42 {start+i*60}' for i in range(181)]
            (base / "blocks/fixture.openmetrics").write_text("\n".join(samples) + "\n# EOF\n")
            docker("run", "--rm", "--network", "none", "--user", user, "--entrypoint", "/bin/promtool",
                   "--mount", f"type=bind,src={base / 'blocks'},dst=/blocks", PROMETHEUS,
                   "tsdb", "create-blocks-from", "openmetrics", "/blocks/fixture.openmetrics", "/blocks/output")
            for block in (base / "blocks/output").iterdir():
                if not (block / "meta.json").exists():
                    continue
                meta = json.loads((block / "meta.json").read_text())
                meta["thanos"] = {"labels": {"k8s_cluster": "archive-fixture"},
                                  "downsample": {"resolution": 0}, "source": "sidecar"}
                (block / "meta.json").write_text(json.dumps(meta))
                for file in sorted(block.rglob("*"), key=lambda p: p.name == "meta.json"):
                    if file.is_file():
                        s3.request("PUT", str(file.relative_to(base / "blocks/output")), file.read_bytes())
            run("store", THANOS, ["store", "--http-address=127.0.0.1:13107", "--grpc-address=127.0.0.1:13108",
                "--data-dir=/cache", "--objstore.config-file=/etc/objstore.yaml", "--sync-block-duration=10s",
                "--grpc-server-tls-cert=/tls/tls.crt", "--grpc-server-tls-key=/tls/tls.key",
                "--grpc-server-tls-client-ca=/tls/tls.crt",
                "--index-cache-size=128MB", "--chunk-pool-size=128MB", "--store.grpc.series-max-concurrency=4"],
                [("objstore.yaml", "/etc/objstore.yaml"), *tls_mounts])
            run("query", THANOS, ["query", "--http-address=127.0.0.1:13109", "--grpc-address=127.0.0.1:13110",
                "--endpoint-strict=127.0.0.1:13108", "--query.max-concurrent=2", "--query.timeout=5s",
                "--grpc-client-tls-secure", "--grpc-client-tls-ca=/tls/tls.crt",
                "--grpc-client-tls-cert=/tls/tls.crt", "--grpc-client-tls-key=/tls/tls.key",
                "--no-query.partial-response"], tls_mounts)
            query = "http://127.0.0.1:13109/api/v1/query?" + urllib.parse.urlencode({
                "query": "observability_archive_fixture", "time": start + 3600, "partial_response": "false"})

            def verify_history():
                result = json.loads(get(query))
                assert result["status"] == "success"
                assert result["data"]["result"][0]["value"][1] == "42"

            # Empty query vectors while StoreAPI discovery completes are retried.
            def ready_history():
                try:
                    verify_history()
                except IndexError as error:
                    raise AssertionError("archive not discovered yet") from error

            eventually(ready_history)
            docker("restart", "--timeout", "10", names["garage"])
            eventually(lambda: get("http://127.0.0.1:13104/health"))
            assert s3.request("GET", "acceptance/roundtrip") == body
            docker("kill", "--signal", "KILL", names["garage"])
            docker("start", names["garage"])
            eventually(lambda: get("http://127.0.0.1:13104/health"))
            assert s3.request("GET", "acceptance/roundtrip") == body
            s3.request("DELETE", "acceptance/roundtrip")
            assert b"acceptance/roundtrip" not in s3.request("GET", query="list-type=2&prefix=acceptance%2F")
            docker("restart", "--timeout", "10", names["store"])
            eventually(ready_history)
            docker("stop", "--timeout", "10", names["store"])

            def verify_missing_store():
                try:
                    get(query)
                except urllib.error.HTTPError as error:
                    assert error.code in (422, 503)
                else:
                    raise AssertionError("missing StoreAPI must not become a successful empty history")

            eventually(verify_missing_store)
            print("PASS: S3 roundtrip/range/list/delete; verified S3 HTTPS and StoreAPI mutual TLS; Thanos historical query; Garage restart and SIGKILL recovery; Store cache rebuild")
        finally:
            for name in reversed(started):
                subprocess.run(["docker", "rm", "-f", name], capture_output=True)


if __name__ == "__main__":
    main()
