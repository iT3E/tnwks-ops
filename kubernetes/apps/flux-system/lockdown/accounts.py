"""The accounts of the Flux children outside the class, from git (step 2a of
docs/operations/flux-lockdown.md).

For each site in SITES, it writes kubernetes/apps/flux-system/accounts/<site>/accounts.yaml:

- for each Kustomization outside the class, the account flux-child-<name> in
  flux-system, and a RoleBinding to the role flux-child in each namespace
  where its build applies objects, its SOPS files too (encrypted_namespaces);
- for each HelmRelease outside the class, the account flux-release-<name> in
  its namespace, and a RoleBinding to flux-child there.

The account default of flux-system gets no binding. No child names an
account until step 2c.

  python3 kubernetes/apps/flux-system/lockdown/accounts.py write | check

observability/tests/test_flux_lockdown.py fails until a change of the
children is written.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import lockdown

classlist = lockdown.classlist
ROOT = lockdown.ROOT

# The sites whose accounts are in git: site 2 first (step 2a goes site by
# site).
SITES = ("wsl-02",)
ROLE = "flux-child"
HEADER = """---
# The accounts of the Flux children outside the class of {site}
# (docs/operations/flux-lockdown.md, step 2a). Written by
# kubernetes/apps/flux-system/lockdown/accounts.py write: do not edit by hand.
"""


def path_of(site: str) -> Path:
    return ROOT / f"kubernetes/apps/flux-system/accounts/{site}/accounts.yaml"


def encrypted_namespaces(path: Path, outer: str | None = None) -> set[str]:
    """The namespaces of the documents of the SOPS files of a kustomize build
    of a path, as kustomize sets them: the namespace of the outermost
    kustomization.yaml that sets one, else the namespace of the metadata of
    the document. SOPS leaves the metadata in the clear (the encrypted_regex
    of .sops.yaml covers data and stringData only), so this reads no value."""
    path = path.resolve()
    if path.is_file():
        if ".sops." not in path.name:
            return set()
        return {outer or (d.get("metadata") or {}).get("namespace") for d in classlist.load(path)} - {None, ""}
    k = next((path / n for n in classlist.KUSTOMIZE_FILES if (path / n).exists()), None)
    if k is None:
        # Flux makes a kustomization.yaml of each manifest under the folder.
        return {n for f in sorted(path.rglob("*.sops.*")) if f.suffix in (".yaml", ".yml", ".json")
                for n in encrypted_namespaces(f, outer)}
    (kz,) = classlist.load(k)[:1] or [{}]
    inner = outer or kz.get("namespace")
    out = set()
    for name in (kz.get("resources") or []) + (kz.get("components") or []):
        out |= encrypted_namespaces(path / name, inner)
    return out


def namespaces_of(child: classlist.Child) -> list[str]:
    """Each namespace where a child applies objects: of its build, and of
    its SOPS files."""
    target = child.doc["spec"].get("targetNamespace")
    found = {classlist.namespace_of(d, child) for d in child.build.docs}
    for path in [child.path, *(f"{child.path}/{c}" for c in child.doc["spec"].get("components") or [])]:
        found |= {target} if target and child.build.encrypted else encrypted_namespaces(ROOT / path)
    return sorted(n for n in found if n)


def children(site: str) -> tuple[dict[str, list[str]], list[str]]:
    """The Kustomizations outside the class of a site, by name, with the
    namespaces of their objects; and the HelmReleases outside the class, by
    namespace/name."""
    kustomizations = {}
    for child in classlist.walk(site).values():
        if classlist.is_class_path(child.path):
            continue
        namespace, name = child.key.split("/")
        if namespace != "flux-system":
            raise ValueError(f"{child.key}: a Kustomization outside flux-system, which this module does not place")
        kustomizations[name] = namespaces_of(child)
    helmreleases = sorted(key for key, _, in_class in lockdown.helmreleases(site) if not in_class)
    return kustomizations, helmreleases


def binding(name: str, namespace: str, account: str, account_namespace: str) -> dict:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
        "metadata": {"name": name, "namespace": namespace},
        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": ROLE},
        "subjects": [{"kind": "ServiceAccount", "name": account, "namespace": account_namespace}],
    }


def account(name: str, namespace: str) -> dict:
    # No token is mounted from this account: no pod runs as it (the policy
    # flux-child-pods), and Flux acts as it by impersonation.
    return {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": name, "namespace": namespace},
            "automountServiceAccountToken": False}


def manifests(site: str) -> list[dict]:
    kustomizations, helmreleases = children(site)
    docs = []
    for name, namespaces in sorted(kustomizations.items()):
        sa = f"flux-child-{name}"
        docs.append(account(sa, "flux-system"))
        docs += [binding(sa, n, sa, "flux-system") for n in namespaces]
    for key in helmreleases:
        namespace, name = key.split("/")
        sa = f"flux-release-{name}"
        docs.append(account(sa, namespace))
        docs.append(binding(sa, namespace, sa, namespace))
    return docs


def render(site: str) -> str:
    return HEADER.format(site=site) + "---\n".join(
        yaml.safe_dump(d, sort_keys=False, default_flow_style=False) for d in manifests(site))


def main(argv: list[str]) -> int:
    if argv[1:] not in (["write"], ["check"]):
        print(__doc__)
        return 2
    stale = []
    for site in SITES:
        text = render(site)
        path = path_of(site)
        if argv[1] == "write":
            path.write_text(text, encoding="utf-8")
        elif not path.exists() or path.read_text(encoding="utf-8") != text:
            stale.append(str(path.relative_to(ROOT)))
    if stale:
        print("stale, run accounts.py write:", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
