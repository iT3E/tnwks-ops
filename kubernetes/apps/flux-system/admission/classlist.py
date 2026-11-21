"""The class of the Flux admission policy, from git (app/policy.yaml).

The policy flux-child-scope tells a Flux child of the class from a child
outside it. A child is in the class when its spec.path is under a path of
the "ask first" class (CLASS_PATHS below, the kubernetes/ part of the class
of Owner label). This script walks the tree of Flux Kustomizations of each
site from kubernetes/clusters/<site> the way Flux builds it, and writes the
lists of the policy:

- classKustomizations: each Kustomization of the class, with the parents
  that apply it on the sites (a parent is the Kustomization whose build
  holds it);
- classHelmReleases: each HelmRelease that a Kustomization of the class
  applies;
- classNamespaces: each namespace of a Namespace manifest of the class,
  less the namespaces where a child outside the class puts an object;
- classPaths: CLASS_PATHS;
- parentObjects: the Kustomizations of the class whose build reads a file
  outside the class (the aggregators of kubernetes/apps, the ks.yaml of the
  children outside the class), with each object of their builds but the
  Flux Kustomizations, as kind/namespace/name.

It reads no SOPS file: a file with ".sops." in its name is skipped. It
follows the resources of a kustomization.yaml and its patches that delete an
object, which is what the trees of the clusters use; a feature it does not
read stops it (UnreadKustomization). observability/tests/test_flux_admission.py
checks that the lists of the policy are the ones of git.

  python3 kubernetes/apps/flux-system/admission/classlist.py check
  python3 kubernetes/apps/flux-system/admission/classlist.py write
  python3 kubernetes/apps/flux-system/admission/classlist.py json
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
POLICY = Path(__file__).resolve().parent / "app/policy.yaml"
# The sites that this policy runs on. The third cluster folder is down and
# gets no policy.
SITES = ("wsl", "wsl-02")
# The kubernetes/ part of the "ask first" class of Owner label
# (.github/scripts/owner-label-check.py, ASK_FIRST). A folder ends in "/".
# kubernetes/apps/kustomization.yaml is a file of the class and no
# Kustomization has it as its path, so it is not here.
CLASS_PATHS = (
    "kubernetes/clusters/",
    "kubernetes/flux/",
    "kubernetes/bootstrap/",
    "kubernetes/components/",
    "kubernetes/apps/flux-system/",
    "kubernetes/apps/kube-system/",
    "kubernetes/apps/system-upgrade/",
    "kubernetes/apps/databases/",
    "kubernetes/apps/storage/",
    "kubernetes/apps/ai/",
    "kubernetes/apps/security/",
    "kubernetes/apps/auth/",
    "kubernetes/apps/networking/",
    "kubernetes/apps/cert-manager/",
    "kubernetes/apps/tools/",
    "kubernetes/apps/kopiur-system/",
    "kubernetes/apps/monitoring/",
    # The apps outside the system namespaces that need a right of the class:
    # cluster RBAC (hajimari), or a privileged pod with a host path or an
    # added capability (frigate, zwave-js-ui, qbittorrent).
    "kubernetes/apps/default/hajimari/",
    "kubernetes/apps/production/frigate/",
    "kubernetes/apps/home-automation/zwave-js-ui/",
    "kubernetes/apps/media/qbittorrent/",
)
# Namespaces of the class that no Namespace manifest of git makes: the API
# server makes the first three, the chart of Cilium the last.
EXTRA_NAMESPACES = ("kube-system", "kube-public", "kube-node-lease", "cilium-secrets")
KUSTOMIZE_FILES = ("kustomization.yaml", "kustomization.yml", "Kustomization")
# The keys of a kustomization.yaml that this script reads. Another key that
# adds or changes objects stops it.
READ_KEYS = {"apiVersion", "kind", "resources", "patches", "namespace", "labels", "commonLabels",
             "commonAnnotations", "configMapGenerator", "secretGenerator", "generatorOptions",
             "components", "images", "replicas", "configurations"}


class UnreadKustomization(Exception):
    pass


def is_class_path(path: str) -> bool:
    """A spec.path (or a file) under a path of the class."""
    p = norm(path)
    return any((p + "/").startswith(c) for c in CLASS_PATHS)


# The API groups of the two Flux kinds that the walk reads.
KUSTOMIZE_GROUP = "kustomize.toolkit.fluxcd.io"
HELM_GROUP = "helm.toolkit.fluxcd.io"


def api_group(doc: dict) -> str:
    """The API group of a document, exactly: the part of apiVersion before
    the "/", or "" for the core group ("v1")."""
    group, slash, _ = str(doc.get("apiVersion", "")).partition("/")
    return group if slash else ""


def is_kind(doc: dict, group: str, kind: str) -> bool:
    """A document of that kind in that API group, with no prefix or
    substring match."""
    return doc.get("kind") == kind and api_group(doc) == group


def norm(path: str) -> str:
    return path.removeprefix("./").rstrip("/")


@dataclass
class Build:
    docs: list[dict] = field(default_factory=list)
    files: set[str] = field(default_factory=set)


def load(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(d, dict)]


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def build(path: Path) -> Build:
    """The objects of a kustomize build of a file or a folder, and the files
    it reads. SOPS files are skipped, not read."""
    out = Build()
    path = path.resolve()
    if path.is_file():
        if ".sops." not in path.name:
            out.docs += load(path)
            out.files.add(rel(path))
        return out
    k = next((path / n for n in KUSTOMIZE_FILES if (path / n).exists()), None)
    if k is None:
        # Flux makes a kustomization.yaml of each manifest under the folder.
        for f in sorted(path.rglob("*")):
            if f.is_file() and f.suffix in (".yaml", ".yml", ".json") and ".sops." not in f.name:
                out.docs += load(f)
                out.files.add(rel(f))
        return out
    out.files.add(rel(k))
    (kz,) = load(k)[:1] or [{}]
    unread = set(kz) - READ_KEYS
    if unread:
        raise UnreadKustomization(f"{rel(k)}: {sorted(unread)}")
    for name in kz.get("resources") or []:
        sub = build(path / name)
        out.docs += sub.docs
        out.files |= sub.files
    for component in kz.get("components") or []:
        sub = build(path / component)
        out.docs += sub.docs
        out.files |= sub.files
    if kz.get("namespace"):
        for d in out.docs:
            d.setdefault("metadata", {})
            if d.get("kind") != "Namespace":
                d["metadata"]["namespace"] = kz["namespace"]
    for patch in kz.get("patches") or []:
        body = yaml.safe_load(patch["patch"]) if patch.get("patch") else None
        if patch.get("path"):
            out.files.add(rel(path / patch["path"]))
        if isinstance(body, dict) and body.get("$patch") == "delete":
            out.docs = [d for d in out.docs if not (
                d.get("kind") == body.get("kind") and d.get("metadata", {}).get("name") == body["metadata"]["name"])]
    return out


def flux_kustomizations(docs: list[dict]) -> list[dict]:
    return [d for d in docs if is_kind(d, KUSTOMIZE_GROUP, "Kustomization")]


@dataclass
class Child:
    key: str
    parent: str
    path: str
    doc: dict
    build: Build


def walk(site: str) -> dict[str, Child]:
    """Each Kustomization of a site, by namespace/name, as Flux applies it
    from the cluster entry (kubernetes/clusters/<site>)."""
    children: dict[str, Child] = {}

    def visit(path: str, parent: str) -> None:
        for d in flux_kustomizations(build(ROOT / path).docs):
            key = f"{d['metadata'].get('namespace', 'flux-system')}/{d['metadata']['name']}"
            p = norm(d["spec"]["path"])
            if key in children:
                continue
            b = build(ROOT / p)
            # The components of the Flux Kustomization, relative to its path.
            for component in d["spec"].get("components") or []:
                sub = build(ROOT / p / component)
                b.docs += sub.docs
                b.files |= sub.files
            children[key] = Child(key, parent, p, d, b)
            if d["spec"].get("sourceRef", {}).get("name") == "flux-system":
                visit(p, key)

    visit(f"kubernetes/clusters/{site}", "flux-system/flux-system")
    return children


def namespace_of(doc: dict, child: Child) -> str | None:
    return child.doc["spec"].get("targetNamespace") or doc.get("metadata", {}).get("namespace")


def lists() -> dict:
    kustomizations: dict[str, set[str]] = {}
    helmreleases: set[str] = set()
    class_ns: set[str] = set(EXTRA_NAMESPACES)
    outside_ns: set[str] = set()
    parents: dict[str, set[str]] = {}
    sites: dict[str, dict] = {}
    for site in SITES:
        children = walk(site)
        sites[site] = {"kustomizations": len(children), "class": 0, "helmreleases": 0}
        for c in children.values():
            cls = is_class_path(c.path)
            sites[site]["class"] += cls
            if cls:
                kustomizations.setdefault(c.key, set()).add(c.parent)
                if any(not is_class_path(f) for f in c.build.files):
                    # Each object of the build but the Kustomizations, which
                    # rules of their own check.
                    parents.setdefault(c.key, set()).update(
                        f"{d['kind']}/{d['metadata'].get('namespace', '')}/{d['metadata']['name']}"
                        for d in c.build.docs
                        if d.get("kind") and not is_kind(d, KUSTOMIZE_GROUP, "Kustomization"))
            for d in c.build.docs:
                if is_kind(d, HELM_GROUP, "HelmRelease"):
                    sites[site]["helmreleases"] += 1
                    key = f"{namespace_of(d, c)}/{d['metadata']['name']}"
                    if cls:
                        helmreleases.add(key)
                    for extra in ("targetNamespace", "storageNamespace"):
                        if not cls and d["spec"].get(extra):
                            outside_ns.add(d["spec"][extra])
                if d.get("kind") == "Namespace":
                    if cls:
                        class_ns.add(d["metadata"]["name"])
                    continue
                ns = namespace_of(d, c)
                if ns and not cls:
                    outside_ns.add(ns)
    return {
        "classKustomizations": {k: sorted(v) for k, v in sorted(kustomizations.items())},
        "classHelmReleases": sorted(helmreleases),
        "classNamespaces": sorted(class_ns - outside_ns),
        "classPaths": list(CLASS_PATHS),
        "parentObjects": {k: sorted(v) for k, v in sorted(parents.items())},
        "sites": sites,
    }


BEGIN = "# BEGIN classlist.py: written by classlist.py write, do not edit by hand."
END = "# END classlist.py"


def render(data: dict, indent: str) -> str:
    """The generated variables of the policy, one CEL literal each."""
    lines = [indent + BEGIN]
    for name in ("classKustomizations", "classHelmReleases", "classNamespaces", "classPaths", "parentObjects"):
        value = data[name]
        lines.append(f"{indent}- name: {name}")
        lines.append(f"{indent}  expression: >-")
        if isinstance(value, dict):
            body = ["{"]
            for n, (k, v) in enumerate(value.items()):
                end = "," if n < len(value) - 1 else ""
                if len(v) <= 2:
                    body.append(f"  '{k}': [{', '.join(repr(x) for x in v)}]{end}")
                else:
                    body.append(f"  '{k}': [")
                    body += [f"    {x!r}," for x in v[:-1]] + [f"    {v[-1]!r}", f"  ]{end}"]
            body.append("}")
        else:
            body = ["["] + [f"  '{v}'," for v in value[:-1]] + [f"  '{value[-1]}'", "]"]
        lines += [f"{indent}    {line}" for line in body]
    lines.append(indent + END)
    return "\n".join(lines)


def current() -> tuple[str, str]:
    """The policy file, and the text it should have."""
    text = POLICY.read_text(encoding="utf-8")
    m = re.search(r"^( *)" + re.escape(BEGIN) + r".*?^ *" + re.escape(END) + r"$", text, re.MULTILINE | re.DOTALL)
    if not m:
        raise SystemExit(f"{POLICY}: no generated block")
    want = text[: m.start()] + render(lists(), m.group(1)) + text[m.end():]
    return text, want


def main(argv: list[str]) -> int:
    if argv == ["json"]:
        print(json.dumps(lists(), indent=2))
        return 0
    text, want = current()
    if argv == ["write"]:
        POLICY.write_text(want, encoding="utf-8")
        return 0
    if argv == ["check"]:
        if text != want:
            print(f"{POLICY.relative_to(ROOT)} is not the class of git: run classlist.py write")
            return 1
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
