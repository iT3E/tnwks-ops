"""The host server of main at site 1 (Design Doc of effort 0003, section
4.2.6, "The host server"; task 6.22; docs/apps/host-mcp.md).

The static tests read the files:

- The folder of main lists the Flux Kustomization (the go-live). The
  Secret is in git with ciphertext only, the
  three keys of the server and the recipients of the other Secrets of the
  namespace. The address of the SSH server of the distro is set in the
  cluster settings of site 1.
- The pod runs the image of ours by digest with the flags of the design,
  with no shared process namespace. The token, the config and the SSH key
  come from the Secret by reference only. The proxy is the one port of the
  Service and sets the token.
- The network policy lets in the gateway and the kubelet, and lets out port
  22 of two addresses only.
- The route main-host takes a key of its own, the one that the host
  approval plugin reads from MCP_HOST_KEY, and names the six read tools of
  the read step. Once the server is listed, its hash is not the placeholder:
  the store step of the lead sets it, and
  test_the_route_has_a_key_of_its_own_once_the_server_is_listed fails until
  then.
- The read list of the plugin is the nine lines of the design, for the
  profile agent (the user in the distro of site 1) only.
- Each gateway probe tries the server straight, and only a time out passes.

The lab runs the gateway at its pin with the config of git (a key of the
test in place of the placeholder), the proxy and the server from their
pinned images in one network namespace, as in the pod, and a fake Linux
host (host-mcp-sshd/: OpenSSH, one normal user, new keys each run, a pinned
host key). The server runs with the user, the read-only root, the
environment and the volumes of the manifest, so a path that the server
writes and the manifest does not give fails the lab. A read through the
gateway must come back with no error and with the output of the command,
under the role viewer on the group prod; a command of another class and a
command of the denylist are refused by the server; a file list and a file
that the user may read come back, and a file of root only fails. It needs Docker: without
it a test is skipped, except in CI. The fake host is built from its
Dockerfile, or taken from HOST_MCP_LAB_SSHD_IMAGE (an image of the same
recipe, for a run with no network).
"""

import hashlib
import ipaddress
import json
import os
import re
import secrets
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml
from test_hand_written_rules import docker, scratch
from test_main_read_servers import CONFIG, GATEWAY, PYTHON, docs, peers, ports, route
from test_mcp_gateway import PLACEHOLDER, gateway_image, run

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "kubernetes/apps/ai/agents/main"
APP = MAIN / "host-mcp/app"
DEPLOYMENT = APP / "deployment.yaml"
SECRET = APP / "secret-host-mcp.sops.yaml"
SETTINGS = ROOT / "kubernetes/clusters/wsl/cluster-settings.yaml"
POLICY = APP / "networkpolicy.yaml"
TEMPLATE = APP / "proxy/default.conf.template"
AGENT_CONFIG = MAIN / "agent/config/openclaw.json"
ROUTE = "main-host"
SERVICE = "host-mcp.agents-main.svc.cluster.local"
IMAGE = "ghcr.io/it3e/tnwks-host-mcp:0.2.0@sha256:e25c4739f47adf0c49dea1bcfc9a447a1ec2f0e885956374c4b23b127adf15df"
READS = ["list-connections", "list-sessions", "read-command", "read-session-output", "sftp-download", "sftp-list"]
WRITES = ["run-command", "privileged-command", "open-session", "close-session", "signal-process", "sftp-upload"]
# Out of every rule: a reload of the config, and the two tools that move a
# file of the server pod.
NEVER = ["reload-config", "sftp-download-file", "sftp-upload-file"]


def pod() -> dict:
    (d,) = [x for x in docs(DEPLOYMENT) if x["kind"] == "Deployment"]
    return d["spec"]["template"]["spec"]


def container(name: str) -> dict:
    (c,) = [x for x in pod()["containers"] if x["name"] == name]
    return c


def rule() -> list[str]:
    (r,) = route(ROUTE)["policies"]["mcpAuthorization"]["rules"]
    text = r["allow"]
    return [n.strip().strip('"') for n in text[text.index("[") + 1:text.index("]")].split(",") if n.strip()]


def plugin() -> dict:
    return json.loads(AGENT_CONFIG.read_text())["plugins"]["entries"]["host-approval"]["config"]


def listed() -> bool:
    k = yaml.safe_load((MAIN / "kustomization.yaml").read_text())
    return "./host-mcp/ks.yaml" in k["resources"]


def test_the_folder_of_main_lists_the_server_by_its_one_line():
    # The go-live lists ./host-mcp/ks.yaml; the way back removes that line
    # and nothing else (docs/apps/host-mcp.md, "The way back"). No other
    # entry names the folder.
    resources = yaml.safe_load((MAIN / "kustomization.yaml").read_text())["resources"]
    assert [r for r in resources if "host-mcp" in r] in ([], ["./host-mcp/ks.yaml"])


# The nine lines of the design (Design Doc of effort 0003, section 4.2.6,
# "The first lines of the read list"), in its order, for the profile of the
# user in the distro of site 1. No line is added or widened here.
READ_UNITS = ["tnwks-host-check.timer", "tnwks-gdrive-sync.timer", "tnwks-aruba-backup.timer",
              "tnwks-frr.service", "tnwks-site-routing.service"]
READ_LIST = ["uptime", "free -m", "ps -eo comm,pcpu,pmem --sort=-pcpu", "df -h", "ip addr show",
             "ip route show", "ss -t -l -n", "uname -r",
             "systemctl status --no-pager --lines=0 " + " ".join(READ_UNITS)]


def test_the_read_list_is_the_nine_lines_of_the_design_for_the_distro_profile():
    config = plugin()
    assert config["readList"] == READ_LIST
    # The profile of the user in the distro of site 1; a call to the Windows
    # profile with the same line makes a card.
    assert config["readProfile"] == "agent"
    # Each line is exact: no shell character, no free operand.
    for line in READ_LIST:
        assert re.fullmatch(r"[a-z][a-z0-9 ,=.-]*", line), line
        assert line == line.strip() and "  " not in line, line
    # The denylist of the server keeps the readers that can send data out.
    assert not [line for line in READ_LIST if line.split()[0] in ("dig", "nslookup", "host", "ping", "traceroute")]


@pytest.mark.parametrize("probe", ["main", "finance"])
def test_each_probe_tries_the_host_server_straight(probe):
    path = ROOT / f"kubernetes/apps/ai/agents/{probe}/checks/app/mcp-gateway-probe.yaml"
    (cron,) = [d for d in docs(path) if d["kind"] == "CronJob"]
    (c,) = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    env = {e["name"]: e.get("value") for e in c["env"]}
    assert f"{SERVICE}:8080" in env["GATEWAY_ONLY_SERVERS"].split(",")
    # The probe's own policy lets the try out, so a drop comes from the
    # policy of the server (test_the_policy_lets_in_the_gateway_...).
    (policy,) = [d for d in docs(path) if d["kind"] == "CiliumNetworkPolicy"]
    out = [(p["io.kubernetes.pod.namespace"], p["app.kubernetes.io/name"], ports(r))
           for r in policy["spec"]["egress"] for p in peers(r)]
    assert ("agents-main", "host-mcp", ["8080"]) in out


def sops_secret(path: Path) -> dict:
    (d,) = docs(path)
    return d


def test_the_secret_holds_ciphertext_only_for_the_three_keys():
    # No value is printed in a failure message.
    secret = sops_secret(SECRET)
    assert (secret["kind"], secret["metadata"]["name"], secret["metadata"]["namespace"]) == (
        "Secret", "host-mcp-secret", "agents-main")
    assert "data" not in secret
    assert sorted(secret["stringData"]) == sorted(["config.toml", "agent_ed25519", "HOST_MCP_TOKEN"])
    plain = [k for k, v in secret["stringData"].items()
             if not re.fullmatch(r"ENC\[AES256_GCM,data:[A-Za-z0-9+/=]+,iv:[^\]]+\]", str(v))]
    assert plain == [], f"keys with no ciphertext: {plain}"
    assert secret["sops"]["encrypted_regex"] == "^(data|stringData)$"
    # The server reads these keys from it, and the app lists it.
    assert "./secret-host-mcp.sops.yaml" in yaml.safe_load((APP / "kustomization.yaml").read_text())["resources"]


def test_the_secret_has_the_recipients_of_the_other_secrets_of_the_namespace():
    def recipients(path):
        return sorted(a["recipient"] for a in sops_secret(path)["sops"]["age"])
    others = [p for p in sorted(MAIN.glob("*/app/*.sops.yaml")) if p != SECRET]
    assert others
    mine = recipients(SECRET)
    assert mine
    differ = [str(p.relative_to(MAIN)) for p in others if recipients(p) != mine]
    assert differ == [], f"other recipients than these files: {differ}"


def test_the_address_of_the_distro_is_set_at_site_1():
    # No value is printed in a failure message: each assert is on a bool.
    data = yaml.safe_load(SETTINGS.read_text())["data"]
    assert "HOST_MCP_DISTRO_ADDR" in data
    value = str(data["HOST_MCP_DISTRO_ADDR"])
    try:
        is_address = ipaddress.ip_address(value).version == 4
    except ValueError:
        is_address = False
    assert is_address is True, "HOST_MCP_DISTRO_ADDR is not an IPv4 address"
    is_windows = value == str(data["WINDOWS_HOST_IP"])
    assert is_windows is False, "HOST_MCP_DISTRO_ADDR is the address of the Windows host"


def test_the_dump_is_of_the_pinned_image_and_the_rule_names_the_six_reads():
    dump = json.loads((GATEWAY / "tools/host.json").read_text())
    assert dump["image"] == container("server")["image"] == IMAGE
    names = {t["name"] for t in dump["tools"]}
    assert names == set(READS) | set(WRITES) | set(NEVER)
    assert rule() == READS


def test_the_route_has_a_key_of_its_own_once_the_server_is_listed():
    # With the server listed, the hash of the route is the hash of
    # MCP_HOST_KEY in agent-secret of main: the store step of the lead sets
    # both, on top of the go-live commit (docs/apps/host-mcp.md, "The
    # go-live"). This test fails until then. With the server not listed (the
    # way back), the hash may stay, or be the placeholder, which no key has.
    (key,) = route(ROUTE)["policies"]["apiKey"]["keys"]
    if listed():
        assert not PLACEHOLDER.match(key["keyHash"]), "the route key is not stored yet: host-mcp-store.py gateway-key"
    assert PLACEHOLDER.match(key["keyHash"]) or re.fullmatch(r"sha256:[0-9a-f]{64}", key["keyHash"])
    in_use = {k["keyHash"] for x in yaml.safe_load(CONFIG.read_text())["routes"] if x["name"] != ROUTE
              for k in x["policies"]["apiKey"]["keys"]}
    assert key["keyHash"] not in in_use


def test_the_route_takes_a_key_of_its_own_that_the_plugin_reads():
    r = route(ROUTE)
    (key,) = r["policies"]["apiKey"]["keys"]
    assert key["metadata"] == {"client": "main-host", "route": ROUTE}
    assert PLACEHOLDER.match(key["keyHash"]) or re.fullmatch(r"sha256:[0-9a-f]{64}", key["keyHash"])
    others = {k["keyHash"] for x in yaml.safe_load(CONFIG.read_text())["routes"] if x["name"] != ROUTE
              for k in x["policies"]["apiKey"]["keys"]}
    assert key["keyHash"] not in others
    assert r["policies"]["mcpAuthorization"]["rules"][0]["allow"].rstrip().endswith('apiKey.client == "main-host"')
    (backend,) = r["backends"]
    (target,) = backend["mcp"]["targets"]
    assert target["mcp"]["host"] == f"http://{SERVICE}:8080/mcp"
    assert "backendAuth" not in target.get("policies", {})
    assert plugin()["mcpUrl"] == f"http://mcp-gateway.ai.svc.cluster.local:3000/{ROUTE}/mcp"
    assert plugin()["mcpKeyEnv"] == "MCP_HOST_KEY"


def test_the_server_runs_with_the_flags_of_the_design():
    args = container("server")["args"]
    for flag in ("--transport=http", "--httpHost=0.0.0.0", "--hostKeyMode=strict",
                 f"--allowedHosts={SERVICE}", "--bearerToken=$(HOST_MCP_TOKEN)"):
        assert flag in args, flag
    assert "${" not in DEPLOYMENT.read_text()


def test_the_audit_log_has_a_volume_of_its_own():
    # The server writes one line for each tool call to
    # $XDG_DATA_HOME/ssh-mcp/audit.log, and with no writable place it runs no
    # command. The root is read only: a volume of its own, with a size limit.
    server = container("server")
    (data,) = [e["value"] for e in server["env"] if e["name"] == "XDG_DATA_HOME"]
    (mount,) = [m for m in server["volumeMounts"] if m["mountPath"] == data]
    assert mount["name"] == "audit" and not mount.get("readOnly")
    (volume,) = [v for v in pod()["volumes"] if v["name"] == "audit"]
    assert volume["emptyDir"]["sizeLimit"] == "64Mi"
    assert [c["name"] for c in pod()["containers"] if "audit" in {m["name"] for m in c.get("volumeMounts", [])}] == ["server"]


def test_the_pod_is_out_of_the_dns_of_the_namespace():
    (d,) = [x for x in docs(DEPLOYMENT) if x["kind"] == "Deployment"]
    assert d["spec"]["template"]["metadata"]["labels"]["agents.home.arpa/dns"] == "none"


def test_the_secrets_come_by_reference_only_and_no_process_is_shared():
    spec = pod()
    assert spec["shareProcessNamespace"] is False
    assert spec["automountServiceAccountToken"] is False
    for c in spec["containers"]:
        for e in c.get("env", []):
            if e["name"] == "HOST_MCP_TOKEN":
                assert set(e) == {"name", "valueFrom"}
                assert e["valueFrom"]["secretKeyRef"] == {"name": "host-mcp-secret", "key": "HOST_MCP_TOKEN"}
        assert c["securityContext"]["readOnlyRootFilesystem"] is True
        assert c["securityContext"]["capabilities"]["drop"] == ["ALL"]
    secrets_ = {v["name"]: v["secret"] for v in spec["volumes"] if "secret" in v}
    assert {n: [i["key"] for i in s["items"]] for n, s in secrets_.items()} == {
        "secret": ["config.toml", "agent_ed25519"]}
    mounts = {c["name"]: {m["name"] for m in c.get("volumeMounts", [])}
              for c in spec["containers"] + spec["initContainers"]}
    # The step files copies them with mode 0600 for the server, in memory.
    assert mounts["files"] == {"secret", "config", "keys"}
    assert {"config", "keys"} <= mounts["server"] and "secret" not in mounts["server"]
    assert not {"secret", "config", "keys"} & mounts["proxy"]
    for v in spec["volumes"]:
        if v["name"] in ("config", "keys"):
            assert v["emptyDir"]["medium"] == "Memory"


def test_the_service_is_the_proxy_port_only():
    (svc,) = [x for x in docs(DEPLOYMENT) if x["kind"] == "Service"]
    assert [(p["port"], p["targetPort"]) for p in svc["spec"]["ports"]] == [(8080, "mcp")]
    text = TEMPLATE.read_text()
    assert 'proxy_set_header Authorization "Bearer ${HOST_MCP_TOKEN}";' in text
    assert f"proxy_set_header Host {SERVICE};" in text
    assert container("proxy")["env"][0] == {"name": "NGINX_ENVSUBST_FILTER", "value": "^HOST_MCP_"}


def test_the_policy_lets_in_the_gateway_and_out_port_22_of_two_addresses():
    (policy,) = docs(POLICY)
    spec = policy["spec"]
    who = [(p["io.kubernetes.pod.namespace"], p["app.kubernetes.io/name"], ports(r))
           for r in spec["ingress"] for p in peers(r)]
    assert who == [("ai", "mcp-gateway", ["8080"])]
    (host,) = [r for r in spec["ingress"] if r.get("fromEntities")]
    assert host["fromEntities"] == ["host"]
    (out,) = spec["egress"]
    assert out == {"toCIDR": ["${WINDOWS_HOST_IP}/32", "${HOST_MCP_DISTRO_ADDR}/32"],
                   "toPorts": [{"ports": [{"port": "22", "protocol": "TCP"}]}]}
    assert "egressDeny" not in spec and "ingressDeny" not in spec


def test_the_route_refuses_the_key_of_main():
    """The key of the agent of main is not a key of the host route, and the
    rule lets in the client main-host only (the lab: 401 for the key of
    main)."""
    routes = yaml.safe_load(CONFIG.read_text())["routes"]
    main = {k["keyHash"] for x in routes for k in x["policies"]["apiKey"]["keys"]
            if k["metadata"]["client"] == "main"}
    assert len(main) == 1
    keys = route(ROUTE)["policies"]["apiKey"]["keys"]
    assert not main & {k["keyHash"] for k in keys}
    assert {k["metadata"]["client"] for k in keys} == {"main-host"}
    assert route(ROUTE)["policies"]["apiKey"]["mode"] == "strict"
    assert "main\"" not in route(ROUTE)["policies"]["mcpAuthorization"]["rules"][0]["allow"].replace(
        'main-host"', "")


def test_no_route_of_finance_or_of_another_key_names_a_host_tool():
    for r in yaml.safe_load(CONFIG.read_text())["routes"]:
        if r["name"] != ROUTE:
            assert SERVICE not in json.dumps(r), r["name"]
            assert not [t for t in READS + WRITES if f'"{t}"' in json.dumps(r)], r["name"]


# The lab

# The form of config.toml of the Secret (docs/apps/host-mcp.md, "The
# profiles"): the role viewer on the group prod runs read-only commands only,
# and the denylist of the design.
SERVER_CONFIG = """[defaults]
defaultProfile = "agent"
approvalMode = "auto"
commandTimeoutMs = 30000

[[profiles]]
name = "agent"
host = "{host}"
port = 22
user = "agent"
auth = "key"
keyRef = "/keys/agent_ed25519"
trustedHostKey = "{fingerprint}"
role = "viewer"
group = "prod"
approvalPolicy = "auto"
announceAgent = false

[policy]
denylist = ["^\\\\s*dig\\\\b", "^\\\\s*nslookup\\\\b", "^\\\\s*host\\\\b", "^\\\\s*ping\\\\b", "^\\\\s*traceroute\\\\b"]
"""
SSHD_DIR = Path(__file__).resolve().parent / "host-mcp-sshd"
LAB_HOST = "lab-distro"

CLIENT = r'''
import json, sys, urllib.request, urllib.error
def post(url, method, params, key=None, host=None):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if key:
        h["Authorization"] = "Bearer " + key
    if host:
        h["Host"] = host
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, body, h), timeout=30) as r:
            code, raw = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read().decode(errors="replace")
    ev = [l[5:].strip() for l in raw.splitlines() if l.startswith("data:")]
    try:
        return code, json.loads(ev[-1] if ev else raw)
    except ValueError:
        return code, None
GW, KEY = sys.argv[1], sys.argv[2]
out = {}
code, body = post(GW, "tools/list", {}, KEY)
out["list"] = sorted(t["name"] for t in body["result"]["tools"]) if code == 200 else code
for name in json.loads(sys.argv[3]):
    code, body = post(GW, "tools/call", {"name": name, "arguments": {"command": "uptime"}}, KEY)
    err = (body or {}).get("error") or {}
    out[name] = ("refused: gateway" if code == 400 and err.get("message") == "unknown resource"
                 else "passed the gateway" if code == 200 and body and "result" in body else f"other: {code}")
def call(name, arguments):
    code, body = post(GW, "tools/call", {"name": name, "arguments": {**arguments, "profile": "agent"}}, KEY)
    result = (body or {}).get("result")
    if code != 200 or not isinstance(result, dict):
        return {"outcome": f"other: {code}", "text": ""}
    text = " ".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
    return {"outcome": "error" if result.get("isError") else "ok", "text": text[:8000]}
def read(command):
    return call("read-command", {"command": command})
out["read"] = read("uptime")
out["read of another class"] = read("touch /tmp/lab-write")
out["read of the denylist"] = read("ping -c 1 lab-distro")
out["file list"] = call("sftp-list", {"remotePath": "/etc", "maxEntries": 500})
out["file read"] = call("sftp-download", {"remotePath": "/etc/lab-motd"})
out["file read, root only"] = call("sftp-download", {"remotePath": "/etc/lab-root-only"})
out["no key"] = post(GW, "tools/list", {})[0]
out["main key"] = post(GW, "tools/list", {}, sys.argv[7])[0]
out["main key, cluster route"] = post(GW.replace("/main-host/", "/main-cluster/"), "tools/list", {}, sys.argv[7])[0]
out["server, no token"] = post(sys.argv[4], "tools/list", {}, host=sys.argv[5])[0]
code, body = post(sys.argv[6], "tools/list", {}, host=sys.argv[5])
out["proxy, no token"] = code
print(json.dumps(out))
'''


def sshd_image(tool: str) -> str:
    """The fake Linux host: HOST_MCP_LAB_SSHD_IMAGE, or a build of host-mcp-sshd/."""
    given = os.environ.get("HOST_MCP_LAB_SSHD_IMAGE")
    if given:
        return given
    recipe = hashlib.sha256(b"".join((SSHD_DIR / f).read_bytes() for f in ("Dockerfile", "sshd_config")))
    tag = f"hostmcp-lab-sshd:{recipe.hexdigest()[:12]}"
    if subprocess.run([tool, "image", "inspect", tag], capture_output=True, check=False).returncode == 0:
        return tag
    built = subprocess.run([tool, "build", "-q", "-t", tag, str(SSHD_DIR)], capture_output=True, text=True,
                           timeout=600, check=False)
    if built.returncode != 0:
        if os.environ.get("CI"):
            pytest.fail("the fake host of the lab does not build: " + built.stderr[-2000:])
        pytest.skip("the fake host of the lab does not build here (no network?); set HOST_MCP_LAB_SSHD_IMAGE")
    return tag


def tmpfs_size(limit: str) -> str:
    """A sizeLimit of the manifest (16Mi) as a size of tmpfs (16m)."""
    number, unit = re.fullmatch(r"(\d+)(Ki|Mi|Gi)", limit).groups()
    return number + unit[0].lower()


def server_run(work: Path) -> list[str]:
    """The options of `docker run` for the server as the manifest runs it: the user and group of
    the pod, the read-only root, no capability, no new privileges, the plain environment, and
    each volume of the step files and of the server (an emptyDir as a tmpfs of its size limit
    with the owner and mode that the kubelet gives it, the Secret as the files of the test). The step files runs first in the same container."""
    spec, server = pod(), container("server")
    (files,) = spec["initContainers"]
    uid, gid = spec["securityContext"]["runAsUser"], spec["securityContext"]["runAsGroup"]
    fs_group = spec["securityContext"]["fsGroup"]
    volumes = {v["name"]: v for v in spec["volumes"]}
    options = ["--user", f"{uid}:{gid}", "--cap-drop", "ALL"]
    if server["securityContext"]["readOnlyRootFilesystem"]:
        options.append("--read-only")
    if server["securityContext"]["allowPrivilegeEscalation"] is False:
        options += ["--security-opt", "no-new-privileges"]
    for e in server.get("env", []):
        if "value" in e:
            options += ["-e", f"{e['name']}={e['value']}"]
    seen = set()
    for m in files["volumeMounts"] + server["volumeMounts"]:
        if m["mountPath"] in seen:
            continue
        seen.add(m["mountPath"])
        v = volumes[m["name"]]
        if "secret" in v:
            options += ["-v", f"{work}/secret:{m['mountPath']}:ro"]
        elif "emptyDir" in v:
            # As the kubelet makes an emptyDir: owned by root, open to all
            # (0777), with the group of fsGroup and its setgid bit.
            size = tmpfs_size(v["emptyDir"].get("sizeLimit", "16Mi"))
            options += ["--tmpfs", f"{m['mountPath']}:uid=0,gid={fs_group},mode=2777,size={size}"]
        else:
            raise AssertionError(f"a volume of the server that the lab does not know: {m['name']}")
    return options


@pytest.fixture(scope="module")
def lab():
    tool = docker()
    sshd = sshd_image(tool)
    work = scratch()
    key, token = f"test-host-{uuid.uuid4().hex}", secrets.token_hex(24)
    (old,) = [k["keyHash"] for k in route(ROUTE)["policies"]["apiKey"]["keys"]]
    # A key of main for the lab, in place of the hash of the real one.
    main_key = f"test-main-{uuid.uuid4().hex}"
    (main_old,) = {k["keyHash"] for x in yaml.safe_load(CONFIG.read_text())["routes"]
                   for k in x["policies"]["apiKey"]["keys"] if k["metadata"]["client"] == "main"}
    (work / "config.yaml").write_text(CONFIG.read_text().replace(
        old, "sha256:" + hashlib.sha256(key.encode()).hexdigest()).replace(
        main_old, "sha256:" + hashlib.sha256(main_key.encode()).hexdigest()))
    # New keys of the run: the key of the user and the host key of the fake host.
    (work / "k").mkdir()
    # ssh-keygen needs a passwd entry for its user, and the user of the caller
    # (the runner in CI) has none in the image. So the keys are made as root
    # in the container, then given to the caller, who can remove them.
    made = run([tool, "run", "--rm", "--network", "none", "--user", "0:0",
                "-v", f"{work}/k:/k", "--entrypoint", "sh", sshd, "-c",
                ('ssh-keygen -q -t ed25519 -N "" -f /k/agent && ssh-keygen -q -t ed25519 -N "" -f /k/host'
                 f" && chmod 644 /k/agent /k/host && chown {os.getuid()}:{os.getgid()} /k/agent /k/agent.pub"
                 " /k/host /k/host.pub && ssh-keygen -l -E sha256 -f /k/host.pub")])
    (fingerprint,) = re.findall(r"SHA256:[A-Za-z0-9+/]{43}", made.stdout)
    (work / "secret").mkdir()
    (work / "secret/config.toml").write_text(SERVER_CONFIG.format(host=LAB_HOST, fingerprint=fingerprint))
    (work / "secret/agent_ed25519").write_text((work / "k/agent").read_text())
    (work / "templates").mkdir()
    (work / "templates/default.conf.template").write_text(TEMPLATE.read_text())
    for p in work.rglob("*"):
        p.chmod(0o755 if p.is_dir() else 0o644)
    work.chmod(0o755)
    tag = uuid.uuid4().hex[:8]
    net = f"hostmcp-{tag}"
    names = {n: f"hostmcp-{n}-{tag}" for n in ("server", "proxy", "gateway", "host")}
    hardened = ["--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
    try:
        run([tool, "network", "create", "--internal", net])
        run([tool, "run", "-d", "--name", names["host"], "--network", net, "--network-alias", LAB_HOST,
             "-v", f"{work}/k:/k:ro", sshd])
        server = container("server")
        args = [a.replace("$(HOST_MCP_TOKEN)", token) for a in server["args"]]
        (files,) = pod()["initContainers"]
        # The step files of the pod, then the server, in one container with
        # the volumes of the manifest.
        start = " ".join(files["command"][2:]) + ' && exec node build/index.js "$@"'
        run([tool, "run", "-d", "--name", names["server"], "--network", net, "--network-alias", SERVICE,
             *server_run(work), "--entrypoint", "sh", IMAGE, "-c", start, "server", *args])
        time.sleep(1)
        state = run([tool, "inspect", "-f", "{{.State.Running}}", names["server"]]).stdout.strip()
        if state != "true":
            log = subprocess.run([tool, "logs", names["server"]], capture_output=True, text=True, check=False)
            raise AssertionError("the server stopped at its start: " + (log.stdout + log.stderr).replace(token, "<token>")[-3000:])
        proxy = container("proxy")
        run([tool, "run", "-d", "--name", names["proxy"], "--network", f"container:{names['server']}",
             *hardened, "--user", "101",
             "--tmpfs", "/tmp", "--tmpfs", "/var/cache/nginx", "--tmpfs", "/etc/nginx/conf.d:uid=101",
             "-e", "NGINX_ENVSUBST_FILTER=^HOST_MCP_", "-e", f"HOST_MCP_TOKEN={token}",
             "-v", f"{work}/templates:/etc/nginx/templates:ro", proxy["image"]])
        run([tool, "run", "-d", "--name", names["gateway"], "--network", net,
             "--network-alias", "mcp-gateway.ai.svc.cluster.local", *hardened, "--tmpfs", "/tmp",
             "--user", "65532", "-v", f"{work}/config.yaml:/config/config.yaml:ro",
             gateway_image(), "-f", "/config/config.yaml"])
        wait = ("import time, urllib.request\n"
                "for u in ('http://mcp-gateway.ai.svc.cluster.local:15021/healthz/ready',"
                f" 'http://{SERVICE}:8080/healthz'):\n"
                "    for i in range(30):\n"
                "        try:\n"
                "            urllib.request.urlopen(u, timeout=2); break\n"
                "        except Exception:\n"
                "            time.sleep(1)\n")
        run([tool, "run", "--rm", "--network", net, PYTHON, "python3", "-I", "-c", wait])
        time.sleep(2)
        yield {"tool": tool, "net": net, "key": key, "main_key": main_key, "token": token, "names": names}
    finally:
        for name in names.values():
            subprocess.run([tool, "container", "remove", "-f", name], capture_output=True, check=False)
        subprocess.run([tool, "network", "remove", net], capture_output=True, check=False)


def test_the_lab_route_lists_the_reads_and_refuses_the_rest(lab):
    result = run([lab["tool"], "run", "--rm", "--network", lab["net"], "--read-only", "--cap-drop", "ALL",
                  "--user", "65534", PYTHON, "python3", "-I", "-c", CLIENT,
                  f"http://mcp-gateway.ai.svc.cluster.local:3000/{ROUTE}/mcp", lab["key"],
                  json.dumps([*WRITES, *NEVER]),
                  f"http://{SERVICE}:3000/", SERVICE, f"http://{SERVICE}:8080/mcp", lab["main_key"]])
    out = json.loads(result.stdout)
    assert out["list"] == READS
    # A read comes back with no error and with the output of the command,
    # as the role viewer on the group prod.
    assert out["read"]["outcome"] == "ok", out["read"]
    assert "load average" in out["read"]["text"], out["read"]
    # A command of another class, and one of the denylist: the server refuses them.
    assert out["read of another class"]["outcome"] == "error"
    assert out["read of another class"]["text"].startswith("POLICY_DENIED: Role \"viewer\" on host group \"prod\"")
    assert out["read of the denylist"]["outcome"] == "error"
    assert "[policy].denylist" in out["read of the denylist"]["text"]
    # The two file reads: a list, and a file that the user may read; a file
    # of root only fails by the file rights of the host.
    assert out["file list"]["outcome"] == "ok" and "/etc/lab-motd" in out["file list"]["text"], out["file list"]
    assert out["file read"]["outcome"] == "ok" and "lab host ok" in out["file read"]["text"], out["file read"]
    assert out["file read, root only"]["outcome"] == "error", out["file read, root only"]
    assert "root only" not in out["file read, root only"]["text"]
    for name in WRITES + NEVER:
        assert out[name] == "refused: gateway", name
    assert out["no key"] == 401
    # The key of main: refused by the host route, taken by a route of main
    # (that target does not run in the lab, so not a 200).
    assert out["main key"] == 401
    assert out["main key, cluster route"] != 401
    # The server takes no call with no token. Through the proxy, the same
    # request passes the token check: the server answers it as a request
    # with no MCP session (400), not with a 401.
    assert out["server, no token"] == 401
    assert out["proxy, no token"] == 400


def test_the_lab_logs_hold_no_token_and_no_key(lab):
    for name in lab["names"].values():
        result = run([lab["tool"], "logs", name])
        log = result.stdout + result.stderr
        assert lab["token"] not in log, name
        assert lab["key"] not in log, name
        assert lab["main_key"] not in log, name
