"""The daily list of the admission objects that a Flux child outside the
class owns (docs/operations/flux-admission.md, "Admission objects").

The API server runs no admission policy on a webhook configuration, an
admission policy or a binding, so flux-child-scope can not refuse one. This
check lists each of them, reads the owner labels that Flux sets, and fails
when one names a Kustomization or a HelmRelease outside the class. It takes
the class from the live policy flux-child-scope (its variables
classKustomizations and classHelmReleases), so it needs no list of its own.
It only reads: get and list on the admission objects.

Each line of the log:

    flux-admission-objects kind=<kind> name=<name> owner=<kind>/<namespace>/<name>
    flux-admission-objects result=pass|fail objects=<n> outside=<n> unowned=<n>

An object with no owner label of Flux (made by an operator or by hand)
counts as unowned and passes. A failed run fails its Job (KubeJobFailed).
Stdlib only.
"""

from __future__ import annotations

import ast
import json
import os
import ssl
import sys
import urllib.request

API = os.environ.get("KUBERNETES_API", "https://kubernetes.default.svc")
ACCOUNT = "/var/run/secrets/kubernetes.io/serviceaccount"
GROUP = "admissionregistration.k8s.io"
POLICY = "flux-child-scope"
KUSTOMIZE = ("kustomize.toolkit.fluxcd.io/namespace", "kustomize.toolkit.fluxcd.io/name")
HELM = ("helm.toolkit.fluxcd.io/namespace", "helm.toolkit.fluxcd.io/name")


def class_lists(policy: dict) -> tuple[set[str], set[str]]:
    """The keys of the Kustomizations and HelmReleases of the class, from the
    CEL literals of the policy (classlist.py writes them; a CEL map or list of
    strings is also a Python literal)."""
    found = {v["name"]: v["expression"] for v in policy["spec"].get("variables", [])}
    kustomizations = ast.literal_eval(found["classKustomizations"])
    helmreleases = ast.literal_eval(found["classHelmReleases"])
    if not isinstance(kustomizations, dict) or not isinstance(helmreleases, list):
        raise TypeError("the class lists of the policy are not a map and a list")
    return set(kustomizations), set(helmreleases)


def owner(labels: dict) -> tuple[str, str] | None:
    """The Flux owner of an object, as (kind, namespace/name), or None."""
    for kind, (ns, name) in (("Kustomization", KUSTOMIZE), ("HelmRelease", HELM)):
        if labels.get(name):
            return kind, f"{labels.get(ns, '')}/{labels[name]}"
    return None


def findings(objects: list[dict], kustomizations: set[str], helmreleases: set[str]) -> tuple[list[str], int]:
    """The lines of the objects owned outside the class, and the count of
    objects with no Flux owner."""
    lines, unowned = [], 0
    for o in objects:
        who = owner((o.get("metadata") or {}).get("labels") or {})
        if who is None:
            unowned += 1
            continue
        kind, key = who
        if key not in (kustomizations if kind == "Kustomization" else helmreleases):
            lines.append(f"flux-admission-objects kind={o.get('kind')} name={o['metadata'].get('name')} "
                         f"owner={kind}/{key}")
    return lines, unowned


class Client:
    def __init__(self) -> None:
        with open(f"{ACCOUNT}/token", encoding="utf-8") as f:
            self.token = f.read().strip()
        self.context = ssl.create_default_context(cafile=f"{ACCOUNT}/ca.crt")

    def get(self, path: str) -> dict:
        request = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(request, context=self.context, timeout=30) as response:
            return json.load(response)


def admission_objects(client: Client) -> list[dict]:
    """Each object of each kind of the group, in its preferred version, and
    the kinds of the other versions that the preferred one does not serve."""
    group = client.get(f"/apis/{GROUP}")
    versions = [group["preferredVersion"]["groupVersion"]] + [
        v["groupVersion"] for v in group["versions"] if v["groupVersion"] != group["preferredVersion"]["groupVersion"]]
    seen, objects = set(), []
    for version in versions:
        for resource in client.get(f"/apis/{version}")["resources"]:
            if "/" in resource["name"] or "list" not in resource.get("verbs", []) or resource["kind"] in seen:
                continue
            seen.add(resource["kind"])
            path, items = f"/apis/{version}/{resource['name']}?limit=500", []
            while path:
                page = client.get(path)
                items += [dict(i, kind=resource["kind"]) for i in page.get("items", [])]
                more = (page.get("metadata") or {}).get("continue")
                path = f"/apis/{version}/{resource['name']}?limit=500&continue={more}" if more else ""
            objects += items
    return objects


def main() -> int:
    client = Client()
    kustomizations, helmreleases = class_lists(
        client.get(f"/apis/{GROUP}/v1/validatingadmissionpolicies/{POLICY}"))
    objects = admission_objects(client)
    lines, unowned = findings(objects, kustomizations, helmreleases)
    for line in lines:
        print(line, flush=True)
    result = "fail" if lines else "pass"
    print(f"flux-admission-objects result={result} objects={len(objects)} outside={len(lines)} unowned={unowned}",
          flush=True)
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
