"""Disposable Alloy acceptance test. No kubeconfig or live APIs are used.

Runs the pinned Alloy with the config of
kubernetes/apps/monitoring/alloy/app/helmrelease.yaml and checks that Alloy
loads it and that every component reports healthy. CI renders the HelmRelease
but never runs Alloy, so a config that Alloy refuses would otherwise show up
only on the cluster.
"""

import json
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from versions import ALLOY

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "kubernetes/apps/monitoring/alloy/app/helmrelease.yaml"
API = "http://127.0.0.1:13501"
# Flux substitutes these from cluster-settings and cluster-secrets.
STAND_INS = {"${SECRET_DOMAIN}": "example.invalid"}


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout


def eventually(function, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return function()
        except (OSError, ValueError, AssertionError, urllib.error.URLError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read()


def config_text():
    release = yaml.safe_load(RELEASE.read_text())
    content = release["spec"]["values"]["alloy"]["configMap"]["content"]
    for variable, value in STAND_INS.items():
        content = content.replace(variable, value)
    assert "${" not in content, "a Flux variable in the Alloy config has no stand-in here"
    return content


def components():
    found = json.loads(get(API + "/api/v0/web/components"))
    states = {c["localID"]: c["health"]["state"] for c in found}
    assert states and all(s == "healthy" for s in states.values()), states
    return states


def main():
    name = f"obs-test-{secrets.token_hex(4)}-alloy"
    with tempfile.TemporaryDirectory(prefix="observability-alloy-") as tmp:
        base = Path(tmp)
        (base / "config.alloy").write_text(config_text())
        base.chmod(0o755)
        (base / "config.alloy").chmod(0o644)
        try:
            docker("run", "-d", "--name", name, "--network", "host", "--read-only",
                   "--memory", "512m", "--cpus", "1", "--cap-drop", "ALL", "--tmpfs", "/tmp",
                   "--mount", f"type=bind,src={base},dst=/etc/alloy,readonly",
                   "--entrypoint", "/bin/alloy", ALLOY, "run",
                   "--server.http.listen-addr=127.0.0.1:13501",
                   "--storage.path=/tmp/alloy", "/etc/alloy/config.alloy")
            eventually(lambda: get(API + "/-/ready"))
            states = eventually(components, timeout=30)
            for component, state in sorted(states.items()):
                print(f"{component}: {state}")
            print(f"Alloy loaded the config: {len(states)} components healthy")
        except Exception:
            logs = subprocess.run(["docker", "logs", name], check=False, capture_output=True, text=True)
            print(logs.stdout[-4000:], logs.stderr[-4000:])
            raise
        finally:
            subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


if __name__ == "__main__":
    main()
