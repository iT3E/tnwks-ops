"""The declared AI lists (observability/inventory.py AI_*) against the manifests
that Flux applies: a workload, CronJob or snapshot policy that git deploys and
the list does not name fails here, and so does a declared one that nothing
deploys. The AI board shows the list, so a row that nothing deploys would read
Unknown for ever, and a part missing from the list would never show.

The walk starts at the entries under apps/ai of the app list of homelab-wsl
and follows the kustomization lists and the Flux Kustomizations (spec.path,
spec.targetNamespace), so a manifest that is in git but not listed (a
restore test, a draft) does not count."""

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "observability"))
from inventory import AI_BACKUPS, AI_PARTS, AI_RESULT_WINDOWS, AI_RESULTS, AI_SCHEDULES

AI = ROOT / "kubernetes/apps/ai"
# The workload that each chart of the domain makes.
CHARTS = {"openshell": ("statefulset", "openshell"), "agent-sandbox": ("deployment", "agent-sandbox-controller"),
          "litellm": ("deployment", "litellm"), "playwright-mcp": ("deployment", "playwright-mcp")}
WORKLOADS = {"Deployment": "deployment", "StatefulSet": "statefulset"}


SITE_APPS = ROOT / "kubernetes/clusters/wsl/apps"


def walk(directory, namespace=""):
    """Each document that a kustomization list applies, with its namespace."""
    spec = yaml.safe_load((directory / "kustomization.yaml").read_text())
    namespace = spec.get("namespace") or namespace
    for entry in spec.get("resources", []) + spec.get("components", []):
        yield from resource((directory / entry).resolve(), namespace)


def resource(target, namespace=""):
    if target.is_dir():
        yield from walk(target, namespace)
        return
    for doc in yaml.safe_load_all(target.read_text()):
        if not isinstance(doc, dict):
            continue
        if doc.get("kind") == "Kustomization" and doc["apiVersion"].startswith("kustomize.toolkit.fluxcd.io"):
            yield from walk(ROOT / doc["spec"]["path"].removeprefix("./"),
                            doc["spec"].get("targetNamespace") or namespace)
        else:
            yield doc["metadata"].get("namespace") or namespace, doc


def applied():
    entries = [entry for entry in yaml.safe_load((SITE_APPS / "kustomization.yaml").read_text())["resources"]
               if (SITE_APPS / entry).resolve().is_relative_to(AI)]
    assert entries
    seen = {}
    for entry in entries:
        for namespace, doc in resource((SITE_APPS / entry).resolve()):
            seen[(namespace, doc["kind"], doc["metadata"]["name"])] = doc
    return seen


def test_each_applied_workload_of_the_ai_domain_is_a_declared_part():
    found = set()
    for namespace, kind, name in applied():
        if kind in WORKLOADS:
            found.add((namespace, WORKLOADS[kind], name))
        elif kind == "HelmRelease":
            assert name in CHARTS, name
            found.add((namespace, *CHARTS[name]))
    declared = {(part["namespace"], part["kind"], part["workload"]) for part in AI_PARTS}
    assert found == declared, (sorted(found - declared), sorted(declared - found))


def test_each_cronjob_of_the_ai_domain_is_a_declared_schedule():
    found = {(namespace, name) for namespace, kind, name in applied() if kind == "CronJob"}
    # The check of the trace filter of the agents lives with the trace pipeline.
    traces = [path for path in (ROOT / "kubernetes/apps/monitoring").rglob("*.yaml")
              if re.search(r"(?m)^kind: CronJob$", path.read_text())
              and re.search(r"(?m)^  name: trace-filter-check$", path.read_text())]
    assert traces
    found.add(("monitoring", "trace-filter-check"))
    declared = {(item["namespace"], item["cronjob"]) for item in AI_SCHEDULES}
    assert found == declared, (sorted(found - declared), sorted(declared - found))


def test_each_snapshot_policy_of_a_trust_domain_is_a_declared_backup():
    found = {(namespace, name) for namespace, kind, name in applied() if kind == "SnapshotPolicy"}
    declared = {(item["namespace"], item["policy"]) for item in AI_BACKUPS}
    assert found == declared, (sorted(found - declared), sorted(declared - found))


def test_each_declared_result_line_is_printed_by_the_platform():
    """The scripts of the agent pods are shared (pilot/agent/config/scripts), so a
    line is found once. Each trust domain with a result line of the agent pod
    has an agent pod; app-drift is the line of the providers job of workflows."""
    assert {item["check"] for item in AI_RESULTS} == set(AI_RESULT_WINDOWS)
    text = "\n".join(path.read_text(errors="replace") for path in AI.rglob("*")
                     if path.is_file() and path.suffix in (".sh", ".yaml", ".py")
                     and not path.name.endswith(".sops.yaml"))
    for check in AI_RESULT_WINDOWS:
        assert re.search(rf"\b{re.escape(check)} result=", text), check
    agents = {part["trust_domain"] for part in AI_PARTS if part["workload"] == "agent"}
    assert {item["trust_domain"] for item in AI_RESULTS if item["check"] != "app-drift"} <= agents
