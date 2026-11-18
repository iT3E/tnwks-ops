"""The scrape of the API server on homelab-wsl keeps the checks of three policies.

kubernetes/clusters/wsl/apps/kustomization.yaml patches the HelmRelease of
kube-prometheus-stack. It replaces the regex of the first metric relabeling of
the API server with the regex of the HelmRelease plus one name, and adds rules
that keep only the series of the admission policy
agents-backup-mover-configmaps with enforcement_action="deny" (the alert
AgentBackupMoverConfigMapDenied), and the series of the two policies of NFS
volumes with enforcement_action "audit" or "deny" (the count of the volumes
they would refuse, then refuse). The first test checks that the regex stays
the one of the HelmRelease plus that name. The second runs the patched rules
in Prometheus, in a container with no network, against a sample of the
metrics of an API server.
"""

import copy
import json
import re
import subprocess

import yaml
from test_hand_written_rules import ROOT, docker, scratch

from versions import PROMETHEUS

HELMRELEASE = ROOT / "kubernetes/apps/monitoring/kube-prometheus-stack/app/helmrelease.yaml"
WSL = ROOT / "kubernetes/clusters/wsl/apps/kustomization.yaml"
RELABELINGS = "/spec/values/kubeApiServer/serviceMonitor/metricRelabelings"
NAME = "apiserver_validating_admission_policy_check"
POLICY = "agents-backup-mover-configmaps"
NFS_POLICIES = ("nfs-pod-volumes", "nfs-persistent-volumes")


def base_relabelings():
    release = yaml.safe_load(HELMRELEASE.read_text())
    return release["spec"]["values"]["kubeApiServer"]["serviceMonitor"]["metricRelabelings"]


def wsl_operations():
    """The JSON patch operations of homelab-wsl on the HelmRelease."""
    for entry in yaml.safe_load(WSL.read_text())["patches"]:
        outer = yaml.safe_load(entry["patch"])
        if outer.get("kind") != "Kustomization" or outer["metadata"]["name"] != "kube-prometheus-stack":
            continue
        for inner in outer["spec"]["patches"]:
            if inner["target"] == {"kind": "HelmRelease", "name": "kube-prometheus-stack"}:
                return yaml.safe_load(inner["patch"])
    raise AssertionError("no patch of the HelmRelease kube-prometheus-stack in " + str(WSL))


def wsl_relabelings():
    """The relabelings of the API server after the operations of homelab-wsl."""
    rules = copy.deepcopy(base_relabelings())
    touched = 0
    for operation in wsl_operations():
        path = operation["path"]
        if not path.startswith(RELABELINGS + "/"):
            continue
        touched += 1
        rest = path[len(RELABELINGS) + 1 :].split("/")
        if operation["op"] == "add" and rest == ["-"]:
            rules.append(operation["value"])
        elif operation["op"] == "replace" and len(rest) == 2:
            rules[int(rest[0])][rest[1]] = operation["value"]
        else:
            raise AssertionError(f"an operation this test does not read: {operation}")
    assert touched, "homelab-wsl no longer patches the relabelings of the API server"
    return rules


def names(regex):
    found = re.fullmatch(r"\((.*)\)_\(\.\+\)", regex)
    assert found, regex
    return found.group(1).split("|")


def test_the_wsl_regex_is_the_regex_of_the_helmrelease_plus_one_name():
    base = names(base_relabelings()[0]["regex"])
    wsl = names(wsl_relabelings()[0]["regex"])
    assert sorted(wsl) == sorted(base + [NAME])


SAMPLE = f"""\
# TYPE apiserver_validating_admission_policy_check_total counter
apiserver_validating_admission_policy_check_total{{enforcement_action="deny",error_type="invalid_error",policy="{POLICY}",policy_binding="{POLICY}"}} 2
apiserver_validating_admission_policy_check_total{{enforcement_action="warn",error_type="invalid_error",policy="{POLICY}",policy_binding="{POLICY}"}} 1
apiserver_validating_admission_policy_check_total{{enforcement_action="deny",error_type="invalid_error",policy="workflow-run-jobs",policy_binding="workflow-run-jobs"}} 22
apiserver_validating_admission_policy_check_total{{enforcement_action="audit",error_type="invalid_error",policy="nfs-pod-volumes",policy_binding="nfs-pod-volumes"}} 3
apiserver_validating_admission_policy_check_total{{enforcement_action="warn",error_type="invalid_error",policy="nfs-pod-volumes",policy_binding="nfs-pod-volumes"}} 3
apiserver_validating_admission_policy_check_total{{enforcement_action="deny",error_type="invalid_error",policy="nfs-persistent-volumes",policy_binding="nfs-persistent-volumes"}} 1
apiserver_validating_admission_policy_check_total{{enforcement_action="audit",error_type="invalid_error",policy="nfs-volumes-other",policy_binding="nfs-volumes-other"}} 4
# TYPE apiserver_validating_admission_policy_check_duration_seconds histogram
apiserver_validating_admission_policy_check_duration_seconds_bucket{{enforcement_action="deny",error_type="invalid_error",policy="{POLICY}",policy_binding="{POLICY}",le="+Inf"}} 2
apiserver_validating_admission_policy_check_duration_seconds_sum{{enforcement_action="deny",error_type="invalid_error",policy="{POLICY}",policy_binding="{POLICY}"}} 0.01
apiserver_validating_admission_policy_check_duration_seconds_count{{enforcement_action="deny",error_type="invalid_error",policy="{POLICY}",policy_binding="{POLICY}"}} 2
# TYPE apiserver_request_total counter
apiserver_request_total{{code="200",verb="GET"}} 5
# TYPE unlisted_metric_total counter
unlisted_metric_total 1
"""

SCRIPT = """\
set -eu
httpd -p 127.0.0.1:8080 -h /t/srv
prometheus --config.file=/t/prometheus.yml --storage.tsdb.path=/prometheus/data \
  --web.listen-address=127.0.0.1:9090 >/prometheus/log 2>&1 &
query='http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22apiserver%22%7D%3D%3D1'
for i in $(seq 1 60); do
  case "$(wget -qO- "$query" 2>/dev/null || true)" in *'"value"'*) break ;; esac
  sleep 1
done
sleep 3
wget -qO- 'http://127.0.0.1:9090/api/v1/query?query=%7Bjob%3D%22apiserver%22%7D'
echo
"""


def snake(rule):
    keys = {"sourceLabels": "source_labels", "targetLabel": "target_label"}
    return {keys.get(key, key): value for key, value in rule.items()}


def test_prometheus_keeps_only_the_checks_of_the_three_policies():
    binary = docker()
    work = scratch()
    (work / "srv").mkdir()
    (work / "srv/metrics").write_text(SAMPLE)
    config = {
        "scrape_configs": [{
            "job_name": "apiserver",
            "scrape_interval": "1s",
            "fallback_scrape_protocol": "PrometheusText0.0.4",
            "static_configs": [{"targets": ["127.0.0.1:8080"]}],
            "metric_relabel_configs": [snake(rule) for rule in wsl_relabelings()],
        }],
    }
    (work / "prometheus.yml").write_text(yaml.safe_dump(config, sort_keys=False))
    subprocess.run(["chmod", "-R", "a+rX", str(work)], check=True)
    # Only stdout holds the answer of the query: Docker writes its pull lines
    # to stderr, and they can end up on the same line in a merged output.
    container = subprocess.run(
        [
            binary, "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
            "--mount", f"type=bind,src={work},dst=/t,readonly",
            PROMETHEUS, "-c", SCRIPT,
        ],
        capture_output=True, text=True, timeout=600, check=False,
    )
    assert container.returncode == 0, (container.stdout + container.stderr)[-8000:]
    result = json.loads(container.stdout.strip().splitlines()[-1])["data"]["result"]
    kept = sorted(
        tuple(sorted((k, v) for k, v in series["metric"].items() if k not in {"instance", "job"}))
        for series in result
        if not series["metric"]["__name__"].startswith(("up", "scrape_"))
    )

    def check(action, policy):
        return (
            ("__name__", "apiserver_validating_admission_policy_check_total"),
            ("enforcement_action", action),
            ("error_type", "invalid_error"),
            ("policy", policy),
            ("policy_binding", policy),
        )

    assert kept == [
        (("__name__", "apiserver_request_total"), ("code", "200"), ("verb", "GET")),
        check("audit", NFS_POLICIES[0]),
        check("deny", POLICY),
        check("deny", NFS_POLICIES[1]),
    ], kept
