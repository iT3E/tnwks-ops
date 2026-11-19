"""The fetch proxy of the main agent (docs/apps/agent-platform.md, "Web fetch
of the main agent"): web_fetch of the gateway goes to a public site on port
443 through a proxy pod with no secret, and every other call of the pod
stays direct.

- The proxy (Envoy, kubernetes/apps/ai/agents/main/fetch-proxy) takes a
  CONNECT to a host name on port 443 and nothing else, drops each address
  inside after its own DNS lookup, and logs the host, never a path.
- Its network policy lets in the agent pod only and lets out port 443 of a
  public address only: the same ranges, a second wall.
- The agent pod reaches the proxy, and no web site directly.
- The env of the gateway sends web_fetch to the proxy, and NO_PROXY keeps
  each other call direct: the services of the cluster and the two host
  names of the agent's network policy.

The lab test runs the proxy of this repository in Docker, on a network with
no way out, with the security settings of the pod, and sends it the
requests it must refuse. Without Docker it is skipped, except in CI, where
it fails. The cases that need the internet (a public page, a redirect from a
public site) are not in this suite.
"""

import ipaddress
import json
import re
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from test_hand_written_rules import docker, scratch

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "kubernetes/apps/ai/agents/main"
PROXY = MAIN / "fetch-proxy/app"
ENVOY = PROXY / "envoy.yaml"
AGENT = MAIN / "agent/app"
CONFIG = MAIN / "agent/config/openclaw.json"
PROXY_URL = "http://fetch-proxy.agents-main.svc.cluster.local:8080"

# The ranges the proxy never reaches: the special-purpose IPv4 ranges of
# IANA (RFC 6890 and its updates) that are not public.
MUST_BLOCK = [
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.0.0.0/24", "192.0.2.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
]


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def kind(path: Path, name: str) -> dict:
    (only,) = [d for d in docs(path) if d["kind"] == name]
    return only


def envoy() -> dict:
    return yaml.safe_load(ENVOY.read_text())


def manager() -> dict:
    (listener,) = envoy()["static_resources"]["listeners"]
    (chain,) = listener["filter_chains"]
    (hcm,) = chain["filters"]
    return hcm["typed_config"]


def dns_cache() -> dict:
    (dfp,) = [f for f in manager()["http_filters"] if f["name"] == "envoy.filters.http.dynamic_forward_proxy"]
    return dfp["typed_config"]["dns_cache_config"]


def nets(ranges: list[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    return [ipaddress.ip_network(r) for r in ranges]


def filtered() -> list[str]:
    return [f'{r["address_prefix"]}/{r["prefix_len"]}' for r in dns_cache()["resolved_address_filter"]["ranges"]]


def covered(net, ranges) -> bool:
    return any(net.version == r.version and net.subnet_of(r) for r in ranges)


def cluster_ranges() -> list[ipaddress.IPv4Network]:
    """Each address, range and CIDR of the cluster settings and of the Talos
    network configs: the pods, the services, the load balancers and the
    routed networks of every cluster."""
    found = []
    for path in sorted((ROOT / "kubernetes/clusters").glob("*/cluster-settings.yaml")):
        for value in yaml.safe_load(path.read_text())["data"].values():
            value = str(value)
            if re.fullmatch(r"\d+\.\d+\.\d+\.\d+/\d+", value):
                found.append(ipaddress.ip_network(value, strict=False))
            elif m := re.fullmatch(r"(\d+\.\d+\.\d+\.\d+)-(\d+\.\d+\.\d+\.\d+)", value):
                found += [ipaddress.ip_network(m[1]), ipaddress.ip_network(m[2])]
            elif re.fullmatch(r"\d+\.\d+\.\d+\.\d+", value):
                found.append(ipaddress.ip_network(value))
    for path in sorted((ROOT / "talos").glob("*/**/*.yaml")):
        if path.name.endswith(".sops.yaml"):
            continue
        for value in re.findall(r"(?:podSubnets|serviceSubnets):\s*\[?\s*-?\s*(\d+\.\d+\.\d+\.\d+/\d+)", path.read_text()):
            found.append(ipaddress.ip_network(value))
    return found


def test_the_proxy_is_its_own_kustomization_in_agents_main():
    resources = yaml.safe_load((MAIN / "kustomization.yaml").read_text())["resources"]
    assert "./fetch-proxy/ks.yaml" in resources
    (ks,) = docs(MAIN / "fetch-proxy/ks.yaml")
    assert ks["metadata"]["name"] == "agents-main-fetch-proxy"
    assert ks["spec"]["targetNamespace"] == "agents-main"
    assert ks["spec"]["path"] == "./kubernetes/apps/ai/agents/main/fetch-proxy/app"
    assert ks["spec"]["prune"] is True
    k = yaml.safe_load((PROXY / "kustomization.yaml").read_text())
    assert k["resources"] == ["./deployment.yaml", "./networkpolicy.yaml"]
    (generated,) = k["configMapGenerator"]
    assert generated["name"] == "fetch-proxy-config" and "disableNameSuffixHash" not in generated.get("options", {})


def test_the_proxy_pod_holds_no_secret_and_no_token():
    d = kind(PROXY / "deployment.yaml", "Deployment")
    pod = d["spec"]["template"]["spec"]
    text = json.dumps(d)
    assert "secretRef" not in text and "secretKeyRef" not in text and '"secret"' not in text
    assert pod["automountServiceAccountToken"] is False
    assert pod["volumes"] == [{"name": "config", "configMap": {"name": "fetch-proxy-config"}}]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    (c,) = pod["containers"]
    assert re.fullmatch(r"docker\.io/envoyproxy/envoy:distroless-v[\d.]+@sha256:[0-9a-f]{64}", c["image"])
    assert not c["image"].endswith("0" * 64)
    assert c["securityContext"] == {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]}}
    assert c["volumeMounts"] == [{"name": "config", "mountPath": "/etc/envoy", "readOnly": True}]
    assert "env" not in c and "envFrom" not in c
    assert c["readinessProbe"] and c["livenessProbe"]
    assert c["resources"]["limits"]["memory"]
    service = kind(PROXY / "deployment.yaml", "Service")
    assert f'http://{service["metadata"]["name"]}.agents-main.svc.cluster.local:{service["spec"]["ports"][0]["port"]}' \
        == PROXY_URL


# The authority of a CONNECT: what the route to the internet must take and
# what it must refuse.
TAKEN = ["example.com:443", "www.example.org:443", "a-b.c1.io:443", "xn--bcher-kva.example:443"]
REFUSED = [
    "10.0.0.1:443", "169.254.169.254:443", "127.0.0.1:443", "[::1]:443", "[fd00::1]:443", "2130706433:443",
    "0x7f000001:443", "example.com:22", "example.com:80", "example.com:8443", "example.com:4430",
    "example.com", "inside:443", "localhost:443", "1.2.3.4.5:443", "example.com:443/path", "-a.com:443",
    "example.com:443\nx",
]


def test_the_proxy_takes_a_connect_to_a_host_name_on_443_only():
    (host,) = manager()["route_config"]["virtual_hosts"]
    assert host["domains"] == ["*"]
    to_cluster = [r for r in host["routes"] if "route" in r]
    (route,) = to_cluster
    assert route["match"]["connect_matcher"] == {}
    (header,) = route["match"]["headers"]
    assert header["name"] == ":authority"
    regex = header["string_match"]["safe_regex"]["regex"]
    for authority in TAKEN:
        assert re.fullmatch(regex, authority), authority
    for authority in REFUSED:
        assert not re.fullmatch(regex, authority), authority
    assert route["route"]["upgrade_configs"] == [{"upgrade_type": "CONNECT", "connect_config": {}}]
    # Each other request, CONNECT or not, gets a 403 from the proxy itself.
    for r in host["routes"]:
        if r is not route:
            assert r["direct_response"] == {"status": 403}
    assert {"connect_matcher": {}} in [r["match"] for r in host["routes"] if r is not route]
    assert {"prefix": "/"} in [r["match"] for r in host["routes"] if r is not route]


def test_the_proxy_drops_each_address_inside_after_its_own_lookup():
    cache = dns_cache()
    (cluster,) = envoy()["static_resources"]["clusters"]
    assert cluster["cluster_type"]["typed_config"]["dns_cache_config"] == cache
    assert cache["dns_lookup_family"] == "V4_ONLY"
    options = cache["typed_dns_resolver_config"]["typed_config"]["dns_resolver_options"]
    assert options["no_default_search_domain"] is True
    assert not cache["resolved_address_filter"].get("invert_match")
    ranges = nets(filtered())
    for r in nets(MUST_BLOCK) + cluster_ranges():
        assert covered(r, ranges), r
    assert ipaddress.ip_network("::/0") in ranges
    # A public address stays.
    for public in ("1.1.1.1/32", "93.184.215.14/32", "172.66.147.243/32"):
        assert not covered(ipaddress.ip_network(public), ranges), public


def test_the_cluster_ranges_are_found():
    # The rule above reads them from the repository: it must find some.
    assert len(cluster_ranges()) >= 6


ALLOWED_LOG = {"%START_TIME%", "%REQ(:METHOD)%", "%REQ(:AUTHORITY)%", "%RESPONSE_CODE%", "%RESPONSE_FLAGS%",
               "%UPSTREAM_HOST%", "%BYTES_RECEIVED%", "%BYTES_SENT%", "%DURATION%"}


def test_the_log_holds_the_host_and_never_a_path():
    (log,) = manager()["access_log"]
    assert log["name"] == "envoy.access_loggers.stdout"
    fields = log["typed_config"]["log_format"]["json_format"]
    assert fields["host"] == "%REQ(:AUTHORITY)%"
    assert set(fields.values()) <= ALLOWED_LOG
    assert "admin" not in envoy()


def test_the_log_of_host_names_stays_on_the_pod():
    """The owner's decision of 2026-11-19: the log of the proxy (the host of
    each request, R1) stays on the pod and does not go to Loki. Two log
    agents read pods on this cluster: the platform agent takes a pod with the
    label logs.home.arpa/ship=true (or a supervisor of a namespace that opts
    in), and the shared agent takes Job pods only. Each rule that keeps the
    proxy out is checked here, so a change of either agent fails."""
    (deploy,) = [d for d in docs(PROXY / "deployment.yaml") if d["kind"] == "Deployment"]
    labels = deploy["spec"]["template"]["metadata"]["labels"]
    assert not any(k.startswith("logs.home.arpa/") for k in labels)
    assert not any(k.startswith("openshell.ai/") for k in labels)
    assert [d["kind"] for d in docs(PROXY / "deployment.yaml")].count("Job") == 0
    namespace = kind(MAIN / "namespace.yaml", "Namespace")
    assert not any(k.startswith("logs.home.arpa/") for k in namespace["metadata"].get("labels", {}))
    vector = ROOT / "kubernetes/apps/monitoring/vector"
    platform = yaml.safe_load((vector / "agent-platform/config/vector.yaml").read_text())["sources"]
    logs = {name: src for name, src in platform.items() if src["type"] == "kubernetes_logs"}
    assert set(logs) == {"allowlist", "supervisors"}
    assert "logs.home.arpa/ship=true" in logs["allowlist"]["extra_label_selector"].split(",")
    assert logs["supervisors"]["extra_namespace_label_selector"] == "logs.home.arpa/ship-supervisors=true"
    shared = yaml.safe_load((vector / "agent/config/vector.yaml").read_text())["sources"]["kubernetes_logs"]
    assert shared["extra_label_selector"] == "${VECTOR_AGENT_POD_SELECTOR:=}"
    settings = yaml.safe_load((ROOT / "kubernetes/clusters/wsl/cluster-settings.yaml").read_text())
    assert settings["data"]["VECTOR_AGENT_POD_SELECTOR"] == "batch.kubernetes.io/job-name"
    # The agents run on this cluster only: MS-01 ships every pod.
    clusters = ROOT / "kubernetes/clusters"
    for path in clusters.glob("*/apps/kustomization.yaml"):
        if path.parts[-3] != "wsl":
            assert "apps/ai/agents" not in path.read_text(), path
    ai = yaml.safe_load((ROOT / "kubernetes/apps/ai/kustomization.yaml").read_text())["resources"]
    assert not any("agents" in r for r in ai)


def test_the_network_policy_lets_in_the_agent_and_out_port_443_of_a_public_address():
    (policy,) = docs(PROXY / "networkpolicy.yaml")
    spec = policy["spec"]
    assert spec["endpointSelector"] == {"matchLabels": {"app.kubernetes.io/name": "fetch-proxy"}}
    sources = [r.get("fromEndpoints") or r.get("fromEntities") for r in spec["ingress"]]
    assert sources == [[{"matchLabels": {"io.kubernetes.pod.namespace": "agents-main",
                                          "app.kubernetes.io/name": "agent",
                                          "io.cilium.k8s.policy.cluster": "${CLUSTER_NAME}"}}],
                       ["host"]]
    for rule in spec["ingress"]:
        assert rule["toPorts"] == [{"ports": [{"port": "8080", "protocol": "TCP"}]}]
    (rule,) = spec["egress"]
    assert set(rule) == {"toCIDRSet", "toPorts"}
    assert rule["toPorts"] == [{"ports": [{"port": "443", "protocol": "TCP"}]}]
    (cidr,) = rule["toCIDRSet"]
    assert cidr["cidr"] == "0.0.0.0/0"
    excepted = nets(cidr["except"])
    for r in nets(MUST_BLOCK) + cluster_ranges():
        assert covered(r, excepted), r
    # One list, two walls: the IPv4 ranges of the proxy are the ranges of
    # the policy.
    assert sorted(cidr["except"]) == sorted(r for r in filtered() if ":" not in r)
    assert "egressDeny" not in spec and "ingressDeny" not in spec


def agent_policy() -> dict:
    (policy,) = docs(AGENT / "networkpolicy.yaml")
    return policy["spec"]


def test_the_agent_reaches_the_proxy_and_no_web_site_directly():
    egress = agent_policy()["egress"]
    proxy = [r for r in egress if r.get("toEndpoints", [{}])[0].get("matchLabels", {}).get(
        "app.kubernetes.io/name") == "fetch-proxy"]
    (rule,) = proxy
    assert rule["toEndpoints"] == [{"matchLabels": {"io.kubernetes.pod.namespace": "agents-main",
                                                    "app.kubernetes.io/name": "fetch-proxy",
                                                    "io.cilium.k8s.policy.cluster": "${CLUSTER_NAME}"}}]
    assert rule["toPorts"] == [{"ports": [{"port": "8080", "protocol": "TCP"}]}]
    for r in egress:
        assert not {"toCIDR", "toCIDRSet", "toEntities", "toServices"} & set(r), r
    names = [f["matchName"] for r in egress for f in r.get("toFQDNs", [])]
    assert names == ["api.telegram.org", "api.search.brave.com"]
    assert not [f for r in egress for f in r.get("toFQDNs", []) if "matchPattern" in f]


def proxy_env() -> dict[str, str]:
    k = yaml.safe_load((AGENT / "kustomization.yaml").read_text())
    (generated,) = [g for g in k["configMapGenerator"] if g["name"] == "agent-fetch-proxy"]
    return dict(line.split("=", 1) for line in generated["literals"])


def no_proxy(host: str, entries: list[str]) -> bool:
    """The match of the product (src/infra/net/proxy-env.ts) and of undici
    for a name: the entry itself, or a name under it."""
    for entry in entries:
        bare = entry.removeprefix("*").removeprefix(".")
        if host == bare or host.endswith("." + bare):
            return True
    return False


def test_the_gateway_sends_web_fetch_to_the_proxy_and_each_other_call_direct():
    env = proxy_env()
    assert set(env) == {"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"}
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == PROXY_URL
    entries = env["NO_PROXY"].split(",")
    assert "*" not in entries and all(e.strip() == e and e for e in entries)
    # Each service the config names, and the two host names of the network
    # policy: direct, as they were before the proxy.
    config = CONFIG.read_text()
    hosts = {urlsplit(u).hostname for u in re.findall(r'"(https?://[^"]+)"', config)}
    assert {"litellm-app.ai.svc.cluster.local", "openshell.agents-main.svc.cluster.local",
            "alloy.monitoring.svc.cluster.local"} <= hosts
    hosts |= {f["matchName"] for r in agent_policy()["egress"] for f in r.get("toFQDNs", [])}
    hosts |= {"localhost", "127.0.0.1", urlsplit(PROXY_URL).hostname}
    for host in hosts:
        assert no_proxy(host, entries), host
    # And nothing more: a web site is never direct.
    for host in ("example.com", "telegram.org", "search.brave.com", "evil-api.telegram.org.example.com"):
        assert not no_proxy(host, entries), host


def test_only_the_gateway_container_gets_the_proxy_env():
    pod = kind(AGENT / "deployment.yaml", "Deployment")["spec"]["template"]["spec"]
    users = [c["name"] for c in pod["initContainers"] + pod["containers"]
             if {"configMapRef": {"name": "agent-fetch-proxy"}} in c.get("envFrom", [])]
    assert users == ["agent"]
    assert "PROXY" not in json.dumps([c.get("env", []) for c in pod["initContainers"] + pod["containers"]])


def test_the_config_turns_web_fetch_on_through_the_proxy():
    c = json.loads(CONFIG.read_text())
    assert c["tools"]["web"]["fetch"] == {"enabled": True, "useTrustedEnvProxy": True}
    # The tool is in the sessions of the sandbox too (the default allow list
    # of the product does not have it).
    assert "web_fetch" in c["tools"]["sandbox"]["tools"]["alsoAllow"]
    assert "web_fetch" not in c["tools"]["deny"]
    # No opening of the private-host check of the product.
    assert "ssrfPolicy" not in json.dumps(c) and "dangerouslyAllowPrivateNetwork" not in json.dumps(c)


# The lab: the proxy of this repository in Docker, with the security
# settings of the pod, on an internal network. The client is the agent image
# (node): it sends the requests and listens on 443 as the name
# inside.lab.test, so a connection of the proxy to it is counted.
CLIENT = r"""
const net = require("net");
let hits = 0;
net.createServer((c) => { hits++; c.end("HTTP/1.1 200 OK\r\n\r\n"); }).listen(443);
const [proxy, cases] = [process.argv[1], JSON.parse(process.argv[2])];
function send(req) {
  return new Promise((resolve) => {
    const s = net.connect({ host: proxy, port: 8080 });
    let d = "";
    const done = () => { s.destroy(); resolve(d.split("\r\n")[0]); };
    s.setTimeout(10000, done);
    s.on("data", (x) => { d += x; if (d.includes("\r\n\r\n")) done(); });
    s.on("error", (e) => { d = "ERROR " + e.code; done(); });
    s.on("close", done);
    s.write(req);
  });
}
async function ready() {
  for (let i = 0; i < 60; i++) {
    const ok = await new Promise((r) => { const s = net.connect({ host: proxy, port: 8080 }, () => { s.destroy(); r(true); }); s.on("error", () => r(false)); });
    if (ok) return;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("the proxy did not start");
}
(async () => {
  await ready();
  const out = {};
  for (const [name, req] of cases) out[name] = (await send(req)).replace(/^HTTP\/1\.1 (\d+).*$/, "$1");
  await new Promise((r) => setTimeout(r, 500));
  out.hits = hits;
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


def connect(target: str) -> str:
    return f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n"


LAB_CASES = [
    ["private-address", connect("10.0.0.1:443")],
    ["link-local-address", connect("169.254.169.254:443")],
    ["ipv6-address", connect("[::1]:443")],
    ["port-22", connect("inside.lab.test:22")],
    ["port-80", connect("inside.lab.test:80")],
    ["name-with-no-dot", connect("inside:443")],
    ["not-connect", "GET http://inside.lab.test/path HTTP/1.1\r\nHost: inside.lab.test\r\n\r\n"],
    ["name-inside", connect("inside.lab.test:443")],
]


def lab(config: Path) -> dict:
    """Run the proxy with a config, and the client on a network of their
    own with no way out. The result of each case, and the connections the
    proxy made to the client."""
    tool = docker()
    pod = kind(PROXY / "deployment.yaml", "Deployment")["spec"]["template"]["spec"]
    (envoy_container,) = pod["containers"]
    client_image = kind(AGENT / "deployment.yaml", "Deployment")["spec"]["template"]["spec"]["containers"][0]["image"]
    tag = uuid.uuid4().hex[:10]
    network, proxy = f"fetch-lab-{tag}", f"fetch-proxy-{tag}"
    subprocess.run([tool, "network", "create", "--internal", network], check=True, capture_output=True)
    try:
        subprocess.run([tool, "run", "-d", "--quiet", "--name", proxy, "--network", network, "--read-only",
                        "--user", "65532:65532", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                        "-v", f"{config}:/etc/envoy/envoy.yaml:ro", envoy_container["image"],
                        *envoy_container["args"]], check=True, capture_output=True)
        result = subprocess.run([tool, "run", "--rm", "--quiet", "--network", network,
                                 "--network-alias", "inside.lab.test", "--network-alias", "inside",
                                 "--sysctl", "net.ipv4.ip_unprivileged_port_start=0", "-u", "1000:1000",
                                 "--entrypoint", "node", client_image, "-e", CLIENT, proxy, json.dumps(LAB_CASES)],
                                capture_output=True, text=True, timeout=180, check=False)
        logs = subprocess.run([tool, "logs", proxy], capture_output=True, text=True, check=False)
        assert result.returncode == 0, (result.stdout + result.stderr)[-4000:] + logs.stderr[-4000:]
        return json.loads(result.stdout.strip().splitlines()[-1])
    finally:
        subprocess.run([tool, "rm", "-f", proxy], capture_output=True, check=False)
        subprocess.run([tool, "network", "rm", network], capture_output=True, check=False)


def test_the_proxy_refuses_each_request_inside_in_a_lab():
    out = lab(ENVOY)
    for name in ("private-address", "link-local-address", "ipv6-address", "port-22", "port-80",
                 "name-with-no-dot", "not-connect"):
        assert out[name] == "403", (name, out)
    # The name resolves to the client, inside: no address is left, so 503.
    assert out["name-inside"] == "503", out
    assert out["hits"] == 0, out


def test_without_its_address_filter_the_proxy_would_connect_inside():
    """The control of the test above: the same config with no address
    filter connects to the name inside. So the filter is what refuses it."""
    text = ENVOY.read_text()
    control = re.sub(r"\n *# The addresses the proxy never connects to\.\n *resolved_address_filter:\n *ranges:\n"
                     r"( *- \{address_prefix:.*\}\n)+", "\n", text)
    assert control != text
    path = scratch() / "envoy.yaml"
    path.write_text(control)
    path.chmod(0o644)
    out = lab(path)
    assert out["name-inside"] == "200", out
    assert out["hits"] == 1, out
