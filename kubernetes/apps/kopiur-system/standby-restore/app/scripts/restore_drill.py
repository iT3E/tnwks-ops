"""Weekly standby restore: the peer site's newest kopiur snapshot of each
listed PVC, restored into this site's copy of the PVC, wherever the app is
off here (docs/wsl/multi-site.md, docs/operations/backups.md).

It keeps the standby's PVCs current and is a standing restore drill: a failed
or empty restore fails the Job (KubeJobFailed), and StandbyRestoreDrillStale
fires when there has been no successful run for 8 days.

Skipped for an app that runs on this site (its Deployment or StatefulSet has
replicas) or that has site-overrides here (a switchover is under way). A
StatefulSet's PVC doesn't exist on the standby until its pod has run; an entry
with `create` has the restore create it (as bin/site-failover does), and the
StatefulSet adopts it when it scales up.
"""
import datetime
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
RESTORE_API = "/apis/kopiur.home-operations.com/v1alpha1"
LABEL = "app.kubernetes.io/name=standby-restore"
KEEP = 2


def log(msg):
    print(f"{datetime.datetime.now(datetime.timezone.utc):%H:%M:%S} {msg}", flush=True)


def api(method, path, body=None):
    with open(f"{SA}/token") as f:
        token = f.read()
    url = f"https://{os.environ['KUBERNETES_SERVICE_HOST']}:{os.environ['KUBERNETES_SERVICE_PORT']}{path}"
    req = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    ctx = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"{method} {path}: {e.code} {e.read().decode()[:300]}") from e


def drill(entry, repository):
    ns, pvc, prefix = entry["namespace"], entry["pvc"], entry["prefix"]
    kind, workload = entry.get("workload", f"deployment/{entry.get('deployment')}").split("/", 1)
    dep = api("GET", f"/apis/apps/v1/namespaces/{ns}/{kind}s/{workload}")
    if dep is None:
        log(f"{ns}/{pvc}: no {kind} {workload} here; skipped")
        return True
    if dep["spec"].get("replicas", 1) > 0:
        log(f"{ns}/{pvc}: {workload} runs on this site; skipped")
        return True
    ov = (api("GET", "/api/v1/namespaces/flux-system/configmaps/site-overrides") or {}).get("data") or {}
    if any(k.startswith(prefix + "_") for k in ov):
        log(f"{ns}/{pvc}: site-overrides for {prefix}_* (a switchover); skipped")
        return True
    target = {"pvcRef": {"name": pvc}}
    if api("GET", f"/api/v1/namespaces/{ns}/persistentvolumeclaims/{pvc}") is None:
        if not entry.get("create"):
            log(f"{ns}/{pvc}: PVC missing")
            return False
        c = entry["create"]
        log(f"{ns}/{pvc}: no PVC here yet; the restore creates it ({c['capacity']}, {c['storageClass']})")
        target = {"pvc": {"name": pvc, "capacity": c["capacity"], "storageClassName": c["storageClass"],
                          "accessModes": ["ReadWriteOnce"]}}

    name = f"{pvc}-drill-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d%H%M}"
    log(f"{ns}/{pvc}: Restore {name} from {repository} ({ns}/{ns}-{entry['policy']}, newest)")
    api("POST", f"{RESTORE_API}/namespaces/{ns}/restores", {
        "apiVersion": "kopiur.home-operations.com/v1alpha1",
        "kind": "Restore",
        "metadata": {"name": name, "namespace": ns, "labels": {"app.kubernetes.io/name": "standby-restore", "tnwks.us/pvc": pvc}},
        "spec": {
            "repository": {"kind": "ClusterRepository", "name": repository},
            "source": {"identity": {"hostname": ns, "username": f"{ns}-{entry['policy']}", "sourcePath": f"/pvc/{pvc}"}},
            "target": target,
            "credentialProjection": {"enabled": True},
            "options": {"enableFileDeletion": True},
            "mover": {"securityContext": {"runAsUser": entry["uid"], "runAsGroup": entry["gid"]}},
        },
    })
    deadline = time.time() + 1800
    while time.time() < deadline:
        o = api("GET", f"{RESTORE_API}/namespaces/{ns}/restores/{name}") or {}
        st = o.get("status", {})
        phase = st.get("phase")
        if phase in ("Completed", "Succeeded"):
            sid = (st.get("resolved") or {}).get("kopiaSnapshotID")
            log(f"{ns}/{pvc}: restored snapshot {sid} {json.dumps(st.get('progress') or {})} {json.dumps(st.get('timing') or {})}")
            ok = bool(sid)
            break
        if phase == "Failed":
            log(f"{ns}/{pvc}: FAILED {json.dumps(st.get('failure') or {})[:300]} {(st.get('logTail') or '')[-300:]}")
            ok = False
            break
        time.sleep(15)
    else:
        log(f"{ns}/{pvc}: timed out (phase {phase})")
        ok = False

    olds = api("GET", f"{RESTORE_API}/namespaces/{ns}/restores?labelSelector={LABEL},tnwks.us/pvc={pvc}") or {}
    items = sorted(olds.get("items", []), key=lambda i: i["metadata"]["creationTimestamp"])
    for i in items[:-KEEP]:
        log(f"{ns}/{pvc}: deleting old drill {i['metadata']['name']}")
        api("DELETE", f"{RESTORE_API}/namespaces/{ns}/restores/{i['metadata']['name']}")
    return ok


def main():
    repository = os.environ["PEER_REPOSITORY"]
    entries = json.loads(os.environ["ENTRIES"])
    results = [drill(e, repository) for e in entries]
    if not all(results):
        sys.exit(1)
    log("all standby restores OK")


if __name__ == "__main__":
    main()
