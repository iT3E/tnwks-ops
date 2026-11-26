"""The proxy of the code host server of main reads its token from the
mounted Secret at each request (kubernetes/apps/ai/agents/main/github-mcp).

The token job writes a new token each 30 minutes, and a token ends after
60 minutes. So the proxy must take a new value with no restart: an env var,
or a value read once at start, would send an ended token after one hour.

The container test runs the pinned proxy image with the args, the env and
the files of the pod, as its user and with a read-only root. The token
volume has the layout of a Secret volume of the kubelet: the files are in a
directory with a time stamp, behind the link "..data", and a new value
comes by a new directory and an atomic swap of that link. A fake upstream
records the Authorization of each request. No real token: fake values only.
It needs Docker: without it the test is skipped, except in CI, where it
fails.
"""

import json
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml
from test_hand_written_rules import docker, scratch

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/ai/agents/main/github-mcp/app"
PYTHON = "docker.io/library/python:3.13-alpine"
KEY = "GITHUB_MCP_TOKEN"


def deployment() -> dict:
    return next(d for d in yaml.safe_load_all((APP / "deployment.yaml").read_text())
                if d and d["kind"] == "Deployment")


def proxy() -> dict:
    (c,) = [c for c in deployment()["spec"]["template"]["spec"]["containers"] if c["name"] == "proxy"]
    return c


def mount(name: str) -> dict:
    (m,) = [m for m in proxy()["volumeMounts"] if m["name"] == name]
    return m


def test_the_proxy_mounts_the_token_as_a_whole_secret_volume():
    """A whole volume, read only: the kubelet updates a Secret volume when
    the Secret changes, but never a subPath of it, and never an env var."""
    spec = deployment()["spec"]["template"]["spec"]
    (volume,) = [v for v in spec["volumes"] if v["name"] == "token"]
    assert volume["secret"] == {"secretName": "github-mcp-app-token", "items": [{"key": KEY, "path": KEY}],
                                "defaultMode": 0o440}
    assert mount("token") == {"name": "token", "mountPath": "/var/run/github-mcp-app-token", "readOnly": True}
    assert "subPath" not in mount("token")
    assert not [e for e in proxy()["env"] if "valueFrom" in e or e["name"] == KEY]
    assert "envFrom" not in proxy()
    # Only the proxy mounts it, and nothing restarts the pod on a new token.
    (server,) = [c for c in spec["containers"] if c["name"] == "server"]
    assert not [m for m in server.get("volumeMounts", []) if m["name"] == "token"]
    assert "reloader.stakater.com/auto" not in (deployment()["metadata"].get("annotations") or {})
    # The reader reads the file of the mount at each request.
    reader = (APP / "proxy/token.js").read_text()
    assert f'"{mount("token")["mountPath"]}/{KEY}"' in reader
    template = (APP / "proxy/default.conf.template").read_text()
    assert "js_import token from /etc/nginx/templates/token.js;" in template
    assert "js_set $github_mcp_token token.read;" in template
    assert 'proxy_set_header Authorization "Bearer $github_mcp_token";' in template
    # The check of an empty token comes before the rewrite, whose "break"
    # would skip it.
    mcp = template.split("location = /mcp {")[1]
    assert mcp.index('if ($github_mcp_token = "")') < mcp.index("rewrite ^ /mcp? break;")
    kustomization = yaml.safe_load((APP / "kustomization.yaml").read_text())
    (generator,) = kustomization["configMapGenerator"]
    assert "token.js=./proxy/token.js" in generator["files"]


# The fake upstream: it records the Authorization of each request.
UPSTREAM = r'''
import http.server, json
class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with open("/rec/seen.jsonl", "a") as f:
            f.write(json.dumps({"authorization": self.headers.get("Authorization")}) + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")
    def log_message(self, *args):
        pass
http.server.ThreadingHTTPServer(("0.0.0.0", 8082), Handler).serve_forever()
'''

# One request to the proxy; prints the status and the body.
CLIENT = r'''
import sys, time, urllib.error, urllib.request
for i in range(60):
    try:
        request = urllib.request.Request(sys.argv[1], method=sys.argv[2], data=b"{}" if sys.argv[2] == "POST" else None,
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer client"})
        with urllib.request.urlopen(request, timeout=5) as r:
            print(r.status, r.read().decode().strip())
        break
    except urllib.error.HTTPError as e:
        print(e.code, e.read().decode().strip())
        break
    except OSError:
        time.sleep(0.5)
else:
    print("no answer")
'''

# The kubelet's way to change a Secret volume (the atomic writer): a new
# directory with a time stamp, a new link, and a rename over "..data". The
# files belong to root and the group of the pod (fsGroup), mode 0440.
SWAP = r'''
import os, sys, time
root, value, gid = "/t", sys.argv[1], int(sys.argv[2])
old = os.readlink(f"{root}/..data") if os.path.islink(f"{root}/..data") else None
new = f"..{time.strftime('%Y_%m_%d_%H_%M_%S')}.{time.monotonic_ns()}"
os.mkdir(f"{root}/{new}")
os.chmod(f"{root}/{new}", 0o755)
if value != "--none":
    with open(f"{root}/{new}/GITHUB_MCP_TOKEN", "w") as f:
        f.write(value)
    os.chown(f"{root}/{new}/GITHUB_MCP_TOKEN", 0, gid)
    os.chmod(f"{root}/{new}/GITHUB_MCP_TOKEN", 0o440)
os.symlink(new, f"{root}/..data_tmp")
os.rename(f"{root}/..data_tmp", f"{root}/..data")
if not os.path.islink(f"{root}/GITHUB_MCP_TOKEN"):
    os.symlink("..data/GITHUB_MCP_TOKEN", f"{root}/GITHUB_MCP_TOKEN")
if old:
    for name in os.listdir(f"{root}/{old}"):
        os.remove(f"{root}/{old}/{name}")
    os.rmdir(f"{root}/{old}")
'''


def run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=300, check=False)
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    return result.stdout


@pytest.fixture(scope="module")
def lab():
    tool = docker()
    work = scratch()
    for name, text in (("upstream.py", UPSTREAM), ("client.py", CLIENT), ("swap.py", SWAP)):
        (work / name).write_text(text)
        (work / name).chmod(0o644)
    (work / "rec").mkdir(mode=0o777)
    (work / "rec").chmod(0o777)
    (work / "token").mkdir()
    (work / "token").chmod(0o777)
    c = proxy()
    gid = deployment()["spec"]["template"]["spec"]["securityContext"]["fsGroup"]
    user = deployment()["spec"]["template"]["spec"]["securityContext"]["runAsUser"]
    env = {e["name"]: e["value"] for e in c["env"]}
    env["GITHUB_MCP_UPSTREAM"] = "upstream:8082"
    tag = uuid.uuid4().hex[:8]
    net, names = f"ghproxy-{tag}", {"up": f"ghproxy-up-{tag}", "proxy": f"ghproxy-proxy-{tag}"}
    lab = {"tool": tool, "work": work, "net": net, "names": names, "gid": gid}
    try:
        run([tool, "network", "create", "--internal", net])
        swap(lab, "fake-value-one")
        run([tool, "run", "-d", "--name", names["up"], "--network", net, "--network-alias", "upstream",
             "--read-only", "--cap-drop", "ALL", "-v", f"{work}/upstream.py:/upstream.py:ro",
             "-v", f"{work}/rec:/rec", PYTHON, "python3", "-I", "/upstream.py"])
        tmpfs = [x for p in ("/tmp", "/etc/nginx/conf.d", "/var/cache/nginx")
                 for x in ("--tmpfs", f"{p}:uid={user},gid={gid}")]
        run([tool, "run", "-d", "--name", names["proxy"], "--network", net, "--network-alias", "proxy",
             "--read-only", *tmpfs, "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
             "--user", f"{user}:{gid}", *[x for k, v in env.items() for x in ("-e", f"{k}={v}")],
             "-v", f"{APP / 'proxy/default.conf.template'}:/etc/nginx/templates/default.conf.template:ro",
             "-v", f"{APP / 'proxy/token.js'}:/etc/nginx/templates/token.js:ro",
             "-v", f"{work}/token:{mount('token')['mountPath']}:ro", c["image"], *c["args"]])
        yield lab
    finally:
        for name in names.values():
            subprocess.run([tool, "container", "remove", "-f", name], capture_output=True, check=False)
        subprocess.run([tool, "network", "remove", net], capture_output=True, check=False)


def swap(lab: dict, value: str) -> None:
    run([lab["tool"], "run", "--rm", "--network", "none", "--user", "0", "-v", f"{lab['work']}/token:/t",
         "-v", f"{lab['work']}/swap.py:/swap.py:ro", PYTHON, "python3", "-I", "/swap.py", value, str(lab["gid"])])


def drop_link(lab: dict) -> None:
    """The file of the key is gone: no link and no file."""
    run([lab["tool"], "run", "--rm", "--network", "none", "--user", "0", "-v", f"{lab['work']}/token:/t",
         PYTHON, "rm", "-f", "/t/GITHUB_MCP_TOKEN"])


def ask(lab: dict, port: int = 8080, path: str = "/mcp", method: str = "POST") -> str:
    return run([lab["tool"], "run", "--rm", "--network", lab["net"], "--read-only", "--cap-drop", "ALL",
                "--user", "65534", "-v", f"{lab['work']}/client.py:/client.py:ro", PYTHON,
                "python3", "-I", "/client.py", f"http://proxy:{port}{path}", method]).strip()


def seen(lab: dict) -> list[str]:
    path = lab["work"] / "rec/seen.jsonl"
    return [json.loads(line)["authorization"] for line in path.read_text().splitlines()] if path.exists() else []


def started(lab: dict) -> str:
    return run([lab["tool"], "inspect", "-f", "{{.State.StartedAt}} {{.RestartCount}} {{.State.Pid}}",
                lab["names"]["proxy"]]).strip()


def log(lab: dict) -> str:
    result = subprocess.run([lab["tool"], "logs", lab["names"]["proxy"]], capture_output=True, text=True,
                            timeout=60, check=False)
    return result.stdout + result.stderr


def test_a_new_token_reaches_the_upstream_with_no_restart(lab):
    swap(lab, "fake-value-one")
    first = started(lab)
    assert ask(lab).startswith("200")
    swap(lab, "fake-value-two")
    assert ask(lab).startswith("200")
    swap(lab, "fake-value-three")
    assert ask(lab).startswith("200")
    assert seen(lab)[-3:] == ["Bearer fake-value-one", "Bearer fake-value-two", "Bearer fake-value-three"]
    # The same process, never started again.
    assert started(lab) == first
    # The bearer of the client never passes.
    assert "Bearer client" not in seen(lab)


@pytest.mark.parametrize(("value", "case"), [("pending", "pending"), ("", "pending"),
                                             ("fake value", "bad"), ("--none", "missing")])
def test_with_no_good_token_the_proxy_fails_closed_with_one_line(lab, value, case):
    """The placeholder of the Secret (as Flux makes it, before the first
    run of the job), an empty value, a value with a space, and no file:
    503, nothing reaches the upstream, and one line names the case, never
    the value. The health port still answers."""
    swap(lab, "fake-value-good")
    assert ask(lab).startswith("200")
    before = len(seen(lab))
    swap(lab, value)
    answer = ask(lab)
    assert answer == "503 no token", answer
    assert len(seen(lab)) == before
    assert ask(lab, 8081, "/healthz", "GET") == "200 ok"
    deadline = time.monotonic() + 10
    while f"github-mcp-proxy token={case}" not in log(lab) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert f"github-mcp-proxy token={case}" in log(lab)
    assert "fake value" not in log(lab) and "fake-value" not in log(lab)
    # A good value again: the next request passes, with no restart.
    swap(lab, "fake-value-back")
    assert ask(lab).startswith("200")
    assert seen(lab)[-1] == "Bearer fake-value-back"


def test_with_the_link_of_the_key_gone_the_proxy_fails_closed(lab):
    swap(lab, "fake-value-good")
    drop_link(lab)
    before = len(seen(lab))
    assert ask(lab) == "503 no token"
    assert len(seen(lab)) == before
    assert "github-mcp-proxy token=missing" in log(lab)
    swap(lab, "fake-value-after")
    assert ask(lab).startswith("200")
    assert seen(lab)[-1] == "Bearer fake-value-after"
