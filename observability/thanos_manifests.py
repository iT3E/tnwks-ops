"""Bounded Thanos workloads. Activation remains a separate post-migration change."""

from pathlib import Path

import yaml

from versions import THANOS

ROOT = Path(__file__).resolve().parents[1]


def workload(name, command, memory, request, cpu, args, persistent=None, server=False):
    image, digest = THANOS.split("@")
    repository, tag = image.rsplit(":", 1)
    heap = str(int(memory[:-2]) * {"Mi": 2**20, "Gi": 2**30}[memory[-2:]] * 4 // 5)
    values = {
        "controllers": {"app": {"type": "statefulset" if persistent else "deployment",
            "annotations": {"reloader.stakater.com/auto": "true"},
            "pod": {"automountServiceAccountToken": False, "labels": {"k8s_cluster": "${CLUSTER_NAME}"}, "securityContext": {
                "runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000,
                "fsGroup": 1000, "fsGroupChangePolicy": "OnRootMismatch",
                "seccompProfile": {"type": "RuntimeDefault"}}},
            "containers": {"app": {"image": {"repository": repository, "tag": f"{tag}@{digest}"},
                "args": [command, "--http-address=0.0.0.0:10902", *args],
                "env": {"GOMEMLIMIT": heap},
                "securityContext": {"readOnlyRootFilesystem": True,
                    "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                "resources": {"requests": {"cpu": cpu, "memory": request},
                              "limits": {"memory": memory}},
                "probes": {kind: {"enabled": True, "custom": True, "spec": {
                    "httpGet": {"path": "/-/healthy" if kind == "liveness" else "/-/ready", "port": 10902},
                    "timeoutSeconds": 5, "failureThreshold": 6}}
                    for kind in ["liveness", "readiness"]}}}}},
        "service": {"app": {"controller": "app", "ports": {"http": {"port": 10902}}}},
        "persistence": {"tls": {"type": "secret",
            "name": "observability-store-server" if server else "observability-store-client",
            "globalMounts": [{"path": "/tls", "readOnly": True}]}},
        "serviceMonitor": {"app": {"enabled": True, "serviceName": name,
            "endpoints": [{"port": "http", "interval": "1m", "relabelings": [
                {"targetLabel": "k8s_cluster", "replacement": "${CLUSTER_NAME}"}]}]}}
    }
    if command != "query":
        values["persistence"]["objstore"] = {"type": "secret", "name": "observability-thanos-objstore",
            "globalMounts": [{"path": "/objstore", "readOnly": True}]}
    if server:
        values["service"]["app"]["ports"]["grpc"] = {"port": 10901}
    if persistent:
        values["persistence"]["data"] = {"type": "persistentVolumeClaim",
            "storageClass": "${OBSERVABILITY_STORAGE_CLASS:=local-path-observability}", "accessMode": "ReadWriteOnce",
            "size": persistent, "globalMounts": [{"path": "/data"}]}
    if command == "compact":
        values["controllers"]["app"]["pod"]["nodeSelector"] = {"kubernetes.io/hostname": "${OBSERVABILITY_ARCHIVE_NODE:=homelab-wsl-02-worker-1}"}
    return {"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease",
        "metadata": {"name": name, "namespace": "monitoring"}, "spec": {
            "interval": "30m", "chart": {"spec": {"chart": "app-template", "version": "3.7.3",
                "sourceRef": {"kind": "HelmRepository", "name": "bjw-s", "namespace": "flux-system"}}},
            "values": values}}


def generated_platform():
    server_tls = ["--grpc-address=0.0.0.0:10901", "--grpc-server-tls-cert=/tls/tls.crt",
                  "--grpc-server-tls-key=/tls/tls.key", "--grpc-server-tls-client-ca=/tls/ca.crt"]
    query = workload("thanos-query", "query", "768Mi", "128Mi", "50m", [
        "--grpc-address=127.0.0.1:10901", "--grpc-client-tls-secure",
        "--grpc-client-tls-ca=/tls/ca.crt", "--grpc-client-tls-cert=/tls/tls.crt",
        "--grpc-client-tls-key=/tls/tls.key",
        "--grpc-client-server-name=observability-store.monitoring.svc.cluster.local",
        "--endpoint-strict=thanos-sidecar-${CLUSTER_NAME}.monitoring.svc.cluster.local:10901",
        "--endpoint-strict=thanos-sidecar-${PEER_CLUSTER_NAME}.monitoring.svc.cluster.local:10901",
        "--endpoint-strict=thanos-store-${CLUSTER_NAME}.monitoring.svc.cluster.local:10901",
        "--query.replica-label=__replica__", "--query.timeout=30s", "--query.max-concurrent=2",
        "--query.max-concurrent-select=2", "--store.response-timeout=10s", "--no-query.partial-response"])
    store = workload("thanos-store", "store", "1Gi", "256Mi", "50m", [
        *server_tls, "--objstore.config-file=/objstore/objstore.yaml", "--data-dir=/data",
        "--index-cache-size=128MB", "--chunk-pool-size=256MB", "--store.grpc.series-max-concurrency=4",
        "--store.limits.request-series=50000", "--store.limits.request-samples=5000000",
        "--block-sync-concurrency=4"], "4Gi", True)
    compactor = workload("thanos-compactor", "compact", "4Gi", "512Mi", "100m", [
        "--objstore.config-file=/objstore/objstore.yaml", "--data-dir=/data", "--wait",
        "--compact.concurrency=1", "--compact.blocks-fetch-concurrency=1", "--downsample.concurrency=1",
        "--retention.resolution-raw=35d", "--retention.resolution-5m=35d", "--retention.resolution-1h=35d",
        "--downsampling.disable"], "64Gi")
    result = {}
    for part, resources in [("app", [query, store]), ("compactor", [compactor])]:
        base = ROOT / "kubernetes/apps/monitoring/thanos-metrics" / part
        result[base / "helmrelease.yaml"] = "".join("---\n" + yaml.safe_dump(r, sort_keys=False, width=110) for r in resources)
        result[base / "kustomization.yaml"] = "---\n" + yaml.safe_dump({
            "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
            "namespace": "monitoring", "resources": ["helmrelease.yaml"]}, sort_keys=False)
    services = []
    for cluster in ["${CLUSTER_NAME}", "${PEER_CLUSTER_NAME}"]:
        for name, port in [("thanos-sidecar", 10901), ("prometheus", 9090)]:
            services.append({"apiVersion": "v1", "kind": "Service", "metadata": {
                "name": f"{name}-{cluster}", "annotations": {"service.cilium.io/global": "true"}},
                "spec": {"ports": [{"name": "grpc" if port == 10901 else "http", "port": port,
                                    "targetPort": port}], "selector": {
                    "app.kubernetes.io/name": "prometheus", "prometheus": "kube-prometheus-stack-prometheus",
                    "k8s_cluster": cluster}}})
    services.append({"apiVersion": "v1", "kind": "Service", "metadata": {
        "name": "thanos-store-${CLUSTER_NAME}"}, "spec": {"ports": [{"name": "grpc", "port": 10901}],
        "selector": {"app.kubernetes.io/name": "thanos-store", "app.kubernetes.io/instance": "thanos-store",
                     "app.kubernetes.io/component": "app", "k8s_cluster": "${CLUSTER_NAME}"}}})
    base = ROOT / "kubernetes/apps/monitoring/thanos-metrics/app"
    result[base / "services.yaml"] = "".join("---\n" + yaml.safe_dump(s, sort_keys=False) for s in services)
    kust = yaml.safe_load(result[base / "kustomization.yaml"])
    kust["resources"].append("services.yaml")
    result[base / "kustomization.yaml"] = "---\n" + yaml.safe_dump(kust, sort_keys=False)
    return result
