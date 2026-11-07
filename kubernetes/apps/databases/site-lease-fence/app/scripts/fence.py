"""Database fence (docs/wsl/multi-site.md, "Fencing"): every minute, for each
warm app, read its site lease (<app>.lease.tnwks.local, TXT, from the
router) and, if it names this site while this site's <app>-pg still believes
it's the primary, fence that Cluster (cnpg.io/fencedInstances: ["*"]), so
nothing can write to it after the other site was promoted.

A lease that can't be read changes nothing: deciding to fence needs a lease
that says so. A newly fenced Cluster fails the Job (KubeJobFailed); one that's
already fenced is only logged. Unfencing is manual, after a rebuild.

Each app whose lease was read, and whose Clusters here are in the state that
lease requires, is attested in the site-lease-fence-status ConfigMap: the
lease, the result (clear: the lease doesn't name this site; absent, replica,
fenced, or fencing: it does, and nothing here can write) and the time. The
other site's evidence collector reads that as proof this site's fence is live
for the app. An app with an unreadable lease or an API error isn't attested.
"""
import json
import os
import random
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
FENCE = "cnpg.io/fencedInstances"
STATUS = "/api/v1/namespaces/databases/configmaps/site-lease-fence-status"
# Worst first: an app with several Clusters is attested with its worst result.
RESULTS = ["fencing", "fenced", "replica", "absent"]


def log(msg):
    print(msg, flush=True)


def nameserver():
    with open("/etc/resolv.conf") as f:
        for line in f:
            if line.startswith("nameserver"):
                return line.split()[1]
    raise RuntimeError("no nameserver in /etc/resolv.conf")


def txt(name, timeout=3.0):
    """The first TXT string of name (absolute), or None if it can't be read."""
    qid = random.randint(0, 0xFFFF)
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    for label in name.rstrip(".").split("."):
        q += bytes([len(label)]) + label.encode()
    q += b"\x00" + struct.pack(">HH", 16, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(q, (nameserver(), 53))
        resp, _ = s.recvfrom(4096)
    except OSError as e:
        log(f"{name}: no answer ({e})")
        return None
    finally:
        s.close()
    rid, flags, qd, an, _, _ = struct.unpack(">HHHHHH", resp[:12])
    if rid != qid or flags & 0x000F:
        log(f"{name}: rcode {flags & 0x000F}")
        return None
    off = 12
    for _ in range(qd):
        while resp[off]:
            off += resp[off] + 1
        off += 5
    for _ in range(an):
        if resp[off] & 0xC0 == 0xC0:
            off += 2
        else:
            while resp[off]:
                off += resp[off] + 1
            off += 1
        rtype, _, _, rdlen = struct.unpack(">HHIH", resp[off:off + 10])
        off += 10
        if rtype == 16 and rdlen:
            return resp[off + 1:off + 1 + resp[off]].decode()
        off += rdlen
    log(f"{name}: no TXT record")
    return None


def api(method, path, body=None, content_type="application/json"):
    with open(f"{SA}/token") as f:
        token = f.read()
    url = f"https://{os.environ['KUBERNETES_SERVICE_HOST']}:{os.environ['KUBERNETES_SERVICE_PORT']}{path}"
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": content_type,
                                          "Accept": "application/json"})
    ctx = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"{method} {path}: {e.code} {e.read().decode()[:300]}") from e


def fence(name, cluster, site):
    """Make sure this site's cluster can't write, its lease naming this site.
    Returns how: absent, replica, fenced (already) or fencing (just now)."""
    path = f"/apis/postgresql.cnpg.io/v1/namespaces/databases/clusters/{cluster}"
    c = api("GET", path)
    if c is None:
        log(f"{name}: fenced, and there's no {cluster} here")
        return "absent"
    primary = (c["spec"].get("replica") or {}).get("primary") or site
    if primary != site:
        log(f"{name}: fenced; {cluster} follows {primary} (a replica): fine")
        return "replica"
    if FENCE in (c["metadata"].get("annotations") or {}):
        log(f"{name}: fenced; {cluster} still claims primary and is already fenced. Rebuild it as a replica")
        return "fenced"
    log(f"{name}: FENCED by its site lease, and {cluster} still claims primary here: fencing it")
    api("PATCH", path, {"metadata": {"annotations": {FENCE: '["*"]'}}}, "application/merge-patch+json")
    return "fencing"


def main():
    site = os.environ["SITE"]
    clusters = {}
    for app in json.loads(os.environ["APPS"]):
        clusters.setdefault(app["app"], []).append(app["cluster"])
    newly, failed, attested = [], [], {}
    for name, names in clusters.items():
        lease = txt(f"{name}.lease.tnwks.local.")
        if lease is None:
            log(f"{name}: lease unreadable; nothing changed")
            continue
        if lease != site:
            log(f"{name}: lease {lease!r}; this site isn't fenced")
            attested[name] = {"lease": lease, "result": "clear"}
            continue
        try:
            results = [fence(name, cluster, site) for cluster in names]
        except RuntimeError as e:
            log(f"{name}: {e}")
            failed.append(name)
            continue
        newly += [cluster for cluster, result in zip(names, results) if result == "fencing"]
        attested[name] = {"lease": lease, "result": min(results, key=RESULTS.index)}
    if attested:
        checked = int(time.time())
        data = {name: json.dumps(dict(value, checked=checked), sort_keys=True) for name, value in attested.items()}
        try:
            if api("PATCH", STATUS, {"data": data}, "application/merge-patch+json") is None:
                log("site-lease-fence-status is missing (Flux creates it); nothing attested")
        except RuntimeError as e:
            log(f"attestation not written: {e}")
            failed.append("attestation")
    if newly:
        log(f"fenced {', '.join(newly)}: see docs/wsl/multi-site.md (Fencing) for the rebuild")
    if newly or failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
