"""The vault server of finance (kubernetes/apps/ai/agents/finance/vault-server,
docs/apps/agent-platform.md, "The vault shares").

The static tests read the files: the server is the one writer of the share,
on the claim vault-rw, as the design gives it; only the central MCP gateway
reaches it, and it reaches nothing; its route on the gateway takes the key
of the finance agent and lets through the nine tools of the design only;
and the values check of the agent pod still finds one hash for its key.
"""

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
FINANCE = ROOT / "kubernetes/apps/ai/agents/finance"
SERVER = FINANCE / "vault-server"
GATEWAY = ROOT / "kubernetes/apps/ai/mcp-gateway"
CONFIG = GATEWAY / "app/config/config.yaml"
HOST = "http://vault-server.agents-finance.svc.cluster.local:8080/mcp"

# The tools of the design (Design Doc, section 4.2.5): six reads and three
# writes. The server has more (a move, a copy, an append and others); the
# rule of the gateway refuses each one that is not here.
NINE = ["create_directory", "directory_tree", "edit_file", "get_file_info", "list_directory",
        "read_multiple_files", "read_text_file", "search_files", "write_file"]


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def kind(path: Path, k: str) -> dict:
    (found,) = [d for d in docs(path) if d["kind"] == k]
    return found


def route(name: str) -> dict:
    (found,) = [r for r in yaml.safe_load(CONFIG.read_text())["routes"] if r["name"] == name]
    return found


def pod() -> dict:
    return kind(SERVER / "app/deployment.yaml", "Deployment")["spec"]["template"]["spec"]


def pod_labels() -> dict:
    return kind(SERVER / "app/deployment.yaml", "Deployment")["spec"]["template"]["metadata"]["labels"]


# The Flux Kustomization


def test_the_server_comes_after_its_share_in_the_finance_namespace():
    ks = kind(SERVER / "ks.yaml", "Kustomization")["spec"]
    assert ks["targetNamespace"] == "agents-finance"
    assert ks["path"] == "./kubernetes/apps/ai/agents/finance/vault-server/app"
    assert ks["dependsOn"] == [{"name": "agents-finance-vault-share"}]
    assert ks["wait"] is True
    listed = kind(FINANCE / "kustomization.yaml", "Kustomization")["resources"]
    assert listed.index("./vault-server/ks.yaml") > listed.index("./vault-share/ks.yaml")


# The pod


def test_one_server_writes_the_share_and_two_never_run_at_one_time():
    deployment = kind(SERVER / "app/deployment.yaml", "Deployment")["spec"]
    assert deployment["replicas"] == 1
    assert deployment["strategy"] == {"type": "Recreate"}
    (volume,) = [v for v in pod()["volumes"] if "persistentVolumeClaim" in v]
    assert volume["persistentVolumeClaim"] == {"claimName": "vault-rw"}
    (container,) = pod()["containers"]
    (mount,) = [m for m in container["volumeMounts"] if m["name"] == volume["name"]]
    assert mount == {"name": volume["name"], "mountPath": "/vault"}


def test_the_pod_is_restricted_runs_as_the_owner_of_the_share_and_holds_no_token():
    spec = pod()
    assert spec["automountServiceAccountToken"] is False
    assert spec["enableServiceLinks"] is False
    assert spec["securityContext"] == {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000,
                                       "seccompProfile": {"type": "RuntimeDefault"}}
    (container,) = spec["containers"]
    assert container["securityContext"] == {"allowPrivilegeEscalation": False,
                                            "readOnlyRootFilesystem": True,
                                            "capabilities": {"drop": ["ALL"]}}
    assert "env" not in container and "envFrom" not in container
    assert not [v for v in spec["volumes"] if "secret" in v or "projected" in v]


def test_the_memory_is_the_one_of_the_design():
    (container,) = pod()["containers"]
    assert container["resources"]["requests"]["memory"] == "128Mi"
    assert container["resources"]["limits"] == {"memory": "256Mi"}


def test_a_readiness_probe_of_the_health_path_and_no_liveness_probe():
    """A call that waits for the NFS server can hold the whole process; it
    goes on when the server is back. A restart would drop its write and not
    bring the share back."""
    (container,) = pod()["containers"]
    assert container["readinessProbe"]["httpGet"] == {"path": "/healthz", "port": "http"}
    assert "livenessProbe" not in container and "startupProbe" not in container


def test_the_image_is_a_release_by_digest():
    (container,) = pod()["containers"]
    assert re.fullmatch(r"ghcr\.io/it3e/tnwks-vault-mcp:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}",
                        container["image"])


def test_the_image_is_not_the_placeholder():
    """The image is a real release, not the zeros of the draft."""
    (container,) = pod()["containers"]
    assert not container["image"].endswith("@sha256:" + "0" * 64)


def test_the_service_is_the_mcp_port_only():
    service = kind(SERVER / "app/deployment.yaml", "Service")["spec"]
    assert service["selector"] == {"app.kubernetes.io/name": "vault-server"}
    assert service["ports"] == [{"name": "http", "port": 8080, "targetPort": "http", "protocol": "TCP"}]


# The network policy


def test_only_the_gateway_and_the_kubelet_reach_the_server_and_it_reaches_nothing():
    policy = kind(SERVER / "app/networkpolicy.yaml", "CiliumNetworkPolicy")["spec"]
    assert policy["endpointSelector"] == {"matchLabels": {"app.kubernetes.io/name": "vault-server"}}
    peers = []
    for rule in policy["ingress"]:
        ports = [p["port"] for tp in rule["toPorts"] for p in tp["ports"]]
        assert ports == ["8080"]
        for p in rule.get("fromEndpoints", []):
            peers.append((p["matchLabels"]["io.kubernetes.pod.namespace"],
                          p["matchLabels"]["app.kubernetes.io/name"]))
        peers += [("entity", e) for e in rule.get("fromEntities", [])]
    assert peers == [("ai", "mcp-gateway"), ("entity", "host")]
    assert "egress" not in policy and "egressDeny" not in policy


def test_the_server_gets_no_dns_rule_of_the_namespace():
    """The namespace's rule for any name skips a pod with this label; the
    server has no rule of its own, so it gets no DNS at all."""
    labels = pod_labels()
    assert labels["agents.home.arpa/dns"] == "none"
    (allow_dns,) = [d for d in docs(FINANCE / "namespace.yaml") if d["metadata"]["name"] == "allow-dns"]
    assert {"key": "agents.home.arpa/dns", "operator": "DoesNotExist"} in \
        allow_dns["spec"]["endpointSelector"]["matchExpressions"]


# The route of the gateway


def test_the_vault_route_lets_the_finance_agent_call_the_nine_tools_only():
    r = route("finance-vault")
    assert r["matches"] == [{"path": {"pathPrefix": "/finance-vault/"}}]
    (rule,) = r["policies"]["mcpAuthorization"]["rules"]
    names = re.findall(r'"([a-z_]+)"', rule["allow"].split("]")[0])
    assert names == NINE
    assert set(re.findall(r'apiKey\.client == "([^"]+)"', rule["allow"])) == {"finance", "finance-probe"}
    # The probe key may list, and read the facts of a path only.
    assert '(apiKey.client == "finance-probe" &&\n' in rule["allow"]
    assert '(mcp.methodName == "tools/list" || mcp.tool.name == "get_file_info")' in rule["allow"]


def test_the_vault_route_takes_the_keys_of_the_finance_agent_and_its_probe_only():
    keys = route("finance-vault")["policies"]["apiKey"]["keys"]
    assert [k["metadata"] for k in keys] == [{"client": "finance", "route": "finance-vault"},
                                             {"client": "finance-probe", "route": "finance-vault"}]
    finance = {k["metadata"]["client"]: k["keyHash"] for k in route("finance")["policies"]["apiKey"]["keys"]}
    for k in keys:
        assert k["keyHash"] == finance[k["metadata"]["client"]]


def test_the_probe_checks_the_vault_route_and_tries_the_server_straight():
    probe = FINANCE / "checks/app/mcp-gateway-probe.yaml"
    (cron,) = [d for d in docs(probe) if d["kind"] == "CronJob"]
    (container,) = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["VAULT_GATEWAY_URL"] == "http://mcp-gateway.ai.svc.cluster.local:3000/finance-vault/mcp"
    assert "http://" + env["VAULT_SERVER"] + "/mcp" == HOST
    (policy,) = [d for d in docs(probe) if d["kind"] == "CiliumNetworkPolicy"]
    out = [(p["matchLabels"]["io.kubernetes.pod.namespace"], p["matchLabels"]["app.kubernetes.io/name"],
            [x["port"] for tp in r["toPorts"] for x in tp["ports"]])
           for r in policy["spec"]["egress"] for p in r.get("toEndpoints", [])]
    assert ("agents-finance", "vault-server", ["8080"]) in out


def test_the_target_is_the_vault_server_with_no_credential():
    r = route("finance-vault")
    (backend,) = r["backends"]
    assert backend["mcp"]["statefulMode"] == "stateless"
    assert backend["mcp"]["failureMode"] == "failClosed"
    (target,) = backend["mcp"]["targets"]
    assert target["mcp"]["host"] == HOST
    # The gateway's own wait for an MCP target is 10 s unless the target
    # sets one; the route and the target wait the same time.
    assert target["policies"] == {"http": {"requestTimeout": r["policies"]["timeout"]["requestTimeout"]}}


def test_the_values_check_of_the_agent_finds_one_hash():
    """The first start step of the finance agent pod reads the hash of the
    client finance with awk; a second line with the same metadata would give
    two hashes and stop the start of the agent."""
    deployment = kind(FINANCE / "agent/app/deployment.yaml", "Deployment")
    (check,) = [c for c in deployment["spec"]["template"]["spec"]["initContainers"]
                if c["name"] == "values-check"]
    (program,) = re.findall(r"awk '([^']+)' /gateway/config.yaml", check["command"][2])
    found = subprocess.run(["awk", program, str(CONFIG)], capture_output=True, text=True,
                           check=True).stdout.split()
    (agent,) = [k for k in route("finance")["policies"]["apiKey"]["keys"]
                if k["metadata"]["client"] == "finance"]
    assert found == [agent["keyHash"]]


# The backup of the share


BACKUP = FINANCE / "vault-backup"


def test_the_backup_comes_after_the_share_and_the_repository_and_holds_nothing_up():
    ks = kind(BACKUP / "ks.yaml", "Kustomization")["spec"]
    assert ks["targetNamespace"] == "agents-finance"
    assert ks["path"] == "./kubernetes/apps/ai/agents/finance/vault-backup/app"
    assert ks["dependsOn"] == [{"name": "agents-finance-vault-share"},
                               {"name": "kopiur-agents-finance-repository"}]
    # No other Kustomization depends on it, and it does not wait.
    assert ks["wait"] is False
    listed = kind(FINANCE / "kustomization.yaml", "Kustomization")["resources"]
    assert listed.index("./vault-backup/ks.yaml") > listed.index("./vault-share/ks.yaml")


def test_the_share_goes_to_the_finance_repository_as_the_state_volume_does():
    policy = kind(BACKUP / "app/backup.yaml", "SnapshotPolicy")["spec"]
    state = kind(FINANCE / "agent/app/backup.yaml", "SnapshotPolicy")["spec"]
    assert policy["repository"] == {"kind": "ClusterRepository", "name": "agents-finance"}
    # The read-only claim: the mover never writes the share.
    assert policy["sources"] == [{"pvc": {"name": "vault-ro"}}]
    for key in ("copyMethod", "compression", "credentialProjection", "mover", "retention"):
        assert policy[key] == state[key], key
    schedule = kind(BACKUP / "app/backup.yaml", "SnapshotSchedule")["spec"]
    assert schedule["policyRef"] == {"name": "vault"}
    assert schedule["schedule"]["cron"] == "H 2 * * *"
