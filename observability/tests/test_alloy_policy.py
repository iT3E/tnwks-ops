"""The network rule of Alloy (alloy/app/networkpolicy.yaml).

The rule selects the pod of Alloy and names each sender that git shows,
with its port and its cluster. The OTLP receiver of the agents (4318) takes
the agent pods, the sandbox supervisors of the workflow agents and the test
span only. A new sender in git, without a line in the rule, fails the test
that scans the repository.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/monitoring/alloy/app"
POLICY = APP / "networkpolicy.yaml"
HERE = "${CLUSTER_NAME}"
ALLOY = {"app.kubernetes.io/name": "alloy", "app.kubernetes.io/instance": "alloy"}
OTLP = "alloy.monitoring.svc.cluster.local:4318"


def policy() -> dict:
    (doc,) = [d for d in yaml.safe_load_all(POLICY.read_text()) if d]
    assert doc["kind"] == "CiliumNetworkPolicy" and doc["apiVersion"] == "cilium.io/v2"
    return doc["spec"]


def grants() -> dict:
    """Each source, as its sorted labels or ("entity", name): the set of its ports."""
    out: dict = {}
    for rule in policy()["ingress"]:
        sources = ([tuple(sorted(e["matchLabels"].items())) for e in rule.get("fromEndpoints", [])]
                   + [("entity", e) for e in rule.get("fromEntities", [])])
        assert sources and not ({"fromEndpoints", "fromEntities"} <= rule.keys())
        for to in rule["toPorts"]:
            assert "rules" not in to, "no HTTP rule: the proxy of Cilium stays out of the path"
            for port in to["ports"]:
                assert port["protocol"] == "TCP"
                for s in sources:
                    assert port["port"] not in out.setdefault(s, set()), (s, port["port"])
                    out[s].add(port["port"])
    return out


def pod(namespace: str, **labels: str) -> tuple:
    return tuple(sorted({"io.kubernetes.pod.namespace": namespace,
                         "io.cilium.k8s.policy.cluster": HERE, **labels}.items()))


AGENT = {domain: pod(f"agents-{domain}", **{"app.kubernetes.io/name": "agent"})
         for domain in ("main", "finance", "pilot")}
SUPERVISOR = pod("agents-workflows", **{"openshell.ai/boundary-role": "supervisor"})
TEST_SPAN = pod("monitoring", **{"app.kubernetes.io/name": "trace-filter-check"})
PROMETHEUS = pod("monitoring", **{"app.kubernetes.io/name": "prometheus",
                                  "operator.prometheus.io/name": "kube-prometheus-stack-prometheus"})
INGRESS = pod("networking", **{"app.kubernetes.io/name": "ingress-nginx",
                               "app.kubernetes.io/instance": "ingress-nginx"})


def test_the_rule_selects_the_pod_of_alloy_and_is_listed():
    assert policy()["endpointSelector"] == {"matchLabels": ALLOY}
    assert set(policy()) == {"endpointSelector", "ingress"}, "an ingress rule only: egress stays open"
    assert "./networkpolicy.yaml" in yaml.safe_load((APP / "kustomization.yaml").read_text())["resources"]


def test_each_sender_has_its_port_and_no_other():
    assert grants() == {
        **{agent: {"4318"} for agent in AGENT.values()},
        SUPERVISOR: {"4318"},
        TEST_SPAN: {"4318"},
        INGRESS: {"12347"},
        PROMETHEUS: {"12345"},
        ("entity", "host"): {"12345"},
    }


def test_the_ports_are_the_ports_of_alloy():
    values = yaml.safe_load((APP / "helmrelease.yaml").read_text())["spec"]["values"]
    extra = {str(p["port"]) for p in values["alloy"]["extraPorts"]}
    assert extra == {"4318", "12347"}
    assert values["ingress"]["faroPort"] == 12347 and values["ingress"]["ingressClassName"] == "nginx"
    content = values["alloy"]["configMap"]["content"]
    assert 'endpoint = "0.0.0.0:4318"' in content and "listen_port              = 12347" in content


def test_only_the_agents_the_supervisors_and_the_test_span_send_otlp():
    senders = {s for s, ports in grants().items() if "4318" in ports}
    assert senders == {*AGENT.values(), SUPERVISOR, TEST_SPAN}
    for s in senders:
        assert dict(s)["io.cilium.k8s.policy.cluster"] == HERE


def test_each_file_of_git_that_names_the_receiver_is_a_known_sender():
    """A new sender in git fails here until the rule names it."""
    known = {
        **{f"kubernetes/apps/ai/agents/{d}/agent/config/openclaw.json": AGENT[d] for d in AGENT},
        "kubernetes/apps/ai/agents/workflows/run-receiver/app/agents.yaml": SUPERVISOR,
        "kubernetes/apps/monitoring/alloy/app/scripts/trace_filter_check.py": TEST_SPAN,
    }
    found = {}
    for path in sorted((ROOT / "kubernetes").rglob("*")):
        if not path.is_file() or path.suffix not in (".yaml", ".json", ".py", ".sh"):
            continue
        rel = str(path.relative_to(ROOT))
        if "/tests/" in rel or rel.startswith("kubernetes/apps/monitoring/alloy/app/helmrelease"):
            continue
        text = path.read_text(errors="replace")
        if OTLP in text or re.search(r"host: alloy\.monitoring\.svc\.cluster\.local\s+port: 4318", text):
            found[rel] = known.get(rel)
    # The sandbox policy of the reviewer opens the same host and port.
    found.pop("kubernetes/apps/ai/agents/workflows/run-receiver/app/policies/renovate-reviewer.yaml", None)
    assert found == known, "a new sender of OTLP: add its pod to alloy/app/networkpolicy.yaml"
    for s in found.values():
        assert "4318" in grants()[s]


def test_each_egress_rule_to_the_receiver_has_its_ingress_line():
    checked = 0
    for path in sorted((ROOT / "kubernetes/apps/ai").rglob("networkpolicy.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if not isinstance(doc, dict):
                continue
            for rule in (doc.get("spec") or {}).get("egress") or []:
                to_alloy = any(e.get("matchLabels", {}).get("app.kubernetes.io/name") == "alloy"
                               for e in rule.get("toEndpoints", []))
                if not to_alloy:
                    continue
                namespace = path.relative_to(ROOT / "kubernetes/apps/ai/agents").parts[0]
                selector = doc["spec"]["endpointSelector"]["matchLabels"]
                source = pod(f"agents-{namespace}", **selector)
                assert "4318" in grants().get(source, set()), (str(path), selector)
                checked += 1
    assert checked == 3, "the agent pods of main, finance and pilot"


def test_the_supervisor_label_is_the_one_of_the_sandbox_policies():
    # The supervisors carry it (docs/apps/agent-platform.md, "Logs"); the
    # rule of the finance proxy names them the same way.
    text = (ROOT / "kubernetes/apps/ai/agents/finance/firefly-mcp/app/networkpolicy.yaml").read_text()
    assert "openshell.ai/boundary-role: supervisor" in text
