"""The Flux lockdown, from git (docs/operations/flux-lockdown.md).

What each Flux Kustomization and HelmRelease of a site runs as, and with
which Secret it decrypts, as Flux applies it: the spec of the object with
the patches of its parent, and for the root (the Kustomization flux-system)
the patches of kubernetes/clusters/<site>/flux-system/kustomization.yaml.
It also reads the arguments of the Flux controllers of each site, and holds
KEYS, the age public key of each decryption Secret of each site.

The tests of observability/tests/test_flux_lockdown.py read these, and
tests/render.py gives the controller of the throwaway API server the
arguments of git. It reads no SOPS value: of an encrypted file it reads the
public recipients of the sops block only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "admission"))

import classlist

ROOT = classlist.ROOT

# The age public key of each decryption Secret of Flux, by site
# (kubernetes/clusters/<site>), for the map test: each encrypted file that a
# Kustomization of a site builds holds the key of the Secret that the
# Kustomization names. A Secret holds the private key; git holds only the
# public one, which is also a recipient in .sops.yaml. Phase 1 of the
# lockdown added the class key (sops-age-class) and the apps key
# (sops-age-apps) of each site; phase 3 removes sops-age.
KEYS = {
    "wsl": {
        # The key of homelab-wsl: the public key of its key file, which the
        # owner read with age-keygen -y (step 0 of phase 1).
        "sops-age": "age1hmmuna95ktwl9sf9j56wjjukyf22ukdyjd8c47nlf5fjgyeyk5rsmhtyjx",
        "sops-age-class": "age1lpt37nvkct4l296q7wtueg79msn5x2ul0pcwaclzx2slyv2pnecs4nm6g6",
        "sops-age-apps": "age184eetlfw0vdmxy9ydz532rnar54qt9f39l8xy0jx6w7pp4qksecqqexgpm",
    },
    "wsl-02": {
        # ~/.config/sops/age/homelab-wsl-02.txt (the rule of
        # kubernetes/clusters/wsl-02 in .sops.yaml).
        "sops-age": "age1p6nksz7jrqlq8jm0qcvxwgaw8dup334tjp7y59w5pydkp8mnw9ssvtay53",
        "sops-age-class": "age1g7c0eg586wlen6s0dgprcvtahx0cqq3zl35xdg64vdr8wpg0n3cqcgp967",
        "sops-age-apps": "age1whse4sy99860h8kne626effqnrmsjny7vshpql60ealvwh7mh5vqv7ukhx",
    },
}
AGE_PUBLIC_KEY = re.compile(r"^age1[02-9ac-hj-np-z]{58}$")

# The two accounts of Flux that apply, in flux-system. The root and each
# Kustomization of the class run as kustomize-controller (phase 2b).
CONTROLLERS = ("kustomize-controller", "helm-controller")
FLAGS = ("--default-service-account", "--sops-age-secret", "--no-cross-namespace-refs",
         "--default-decryption-service-account")


def flux_system(site: str) -> Path:
    return ROOT / f"kubernetes/clusters/{site}/flux-system"


def install_patches(site: str) -> list[tuple[dict, object]]:
    """The patches of the Flux install of a site, as (target, body)."""
    (kz,) = classlist.load(flux_system(site) / "kustomization.yaml")[:1]
    return [(p.get("target") or {}, yaml.safe_load(p["patch"])) for p in kz.get("patches") or []]


def json_patch(doc: dict, ops: list) -> dict:
    """The add and replace operations of a JSON patch on a document: enough
    for the patches of the Flux install. Another operation stops it."""
    for op in ops:
        if op.get("op") not in ("add", "replace"):
            raise ValueError(f"a JSON patch operation that this module does not read: {op}")
        parts = [p.replace("~1", "/").replace("~0", "~") for p in op["path"].split("/")[1:]]
        where = doc
        for part in parts[:-1]:
            where = where[int(part)] if isinstance(where, list) else where.setdefault(part, {})
        last = parts[-1]
        if isinstance(where, list):
            if last == "-":
                where.append(op["value"])
            else:
                where.insert(int(last), op["value"]) if op["op"] == "add" else where.__setitem__(int(last), op["value"])
        else:
            where[last] = op["value"]
    return doc


def targets_install(target: dict, doc: dict) -> bool:
    meta = doc.get("metadata") or {}
    return all((
        target.get("group", classlist.api_group(doc)) == classlist.api_group(doc),
        target.get("kind", doc.get("kind")) == doc.get("kind"),
        re.fullmatch(target.get("name", ".*"), meta.get("name", "")),
    ))


def install(site: str, file: str) -> list[dict]:
    """The objects of a file of the Flux install of a site, with the
    patches of its kustomization.yaml."""
    docs = classlist.load(flux_system(site) / file)
    for target, body in install_patches(site):
        for d in docs:
            if isinstance(body, list) and target and targets_install(target, d):
                json_patch(d, body)
    return docs


def root(site: str) -> dict:
    """The root Kustomization flux-system of a site, as the install applies it."""
    (doc,) = [d for d in install(site, "gotk-sync.yaml") if classlist.is_kind(d, classlist.KUSTOMIZE_GROUP, "Kustomization")]
    return doc


def controller_args(site: str, name: str) -> list[str]:
    """The arguments of the container of a Flux controller of a site."""
    (doc,) = [d for d in install(site, "gotk-components.yaml")
              if d.get("kind") == "Deployment" and d["metadata"]["name"] == name]
    return list(doc["spec"]["template"]["spec"]["containers"][0].get("args") or [])


def flag(args: list[str], name: str) -> str | None:
    """The value of a flag in a list of arguments, "" for a flag with no
    value, or None."""
    for a in args:
        if a == name:
            return ""
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def parent_patch_targets(target: dict, doc: dict) -> bool:
    """A target of a patch of a Flux Kustomization against a child."""
    meta = doc.get("metadata") or {}
    labels = meta.get("labels") or {}
    selector = target.get("labelSelector")
    if selector is not None:
        for req in selector.split(","):
            m = re.fullmatch(r"\s*([\w./-]+)\s+notin\s+\(([^)]*)\)\s*", req)
            if not m:
                raise ValueError(f"a label selector that this module does not read: {selector!r}")
            if str(labels.get(m.group(1))) in {v.strip() for v in m.group(2).split(",")}:
                return False
    version = str(doc.get("apiVersion", "")).rpartition("/")[2]
    return all((
        target.get("group", classlist.api_group(doc)) == classlist.api_group(doc),
        target.get("version", version) == version,
        target.get("kind", doc.get("kind")) == doc.get("kind"),
        re.fullmatch(target.get("name", ".*"), meta.get("name", "")),
        re.fullmatch(target.get("namespace", ".*"), meta.get("namespace", "flux-system")),
    ))


def spec_of(child: classlist.Child, children: dict[str, classlist.Child]) -> dict:
    """The spec of a child as Flux applies it: its own, with the strategic
    merge patches of its parent that target it. A JSON patch of a parent on
    a Kustomization stops it."""
    spec = dict(child.doc["spec"])
    parent = children.get(child.parent)
    for patch in (parent.doc["spec"].get("patches") or []) if parent else []:
        body = yaml.safe_load(patch["patch"])
        target = patch.get("target") or {"kind": body.get("kind"), "name": re.escape(body["metadata"]["name"])}
        if not parent_patch_targets(target, child.doc):
            continue
        if not isinstance(body, dict):
            raise TypeError(f"{parent.key}: a JSON patch of a Kustomization, which this module does not read")
        for key, value in (body.get("spec") or {}).items():
            spec[key] = value
    return spec


def encrypted_of(child: classlist.Child) -> set[str]:
    """The SOPS files of the build of a child, by path, with its components."""
    out = set(child.build.encrypted)
    for component in child.doc["spec"].get("components") or []:
        out |= classlist.build(ROOT / child.path / component).encrypted
    return out


def kustomizations(site: str) -> list[tuple[str, dict, set[str], bool]]:
    """Each Kustomization of a site, the root first: its key, its spec as
    Flux applies it, its SOPS files, and whether it is in the class."""
    entry = classlist.build(ROOT / f"kubernetes/clusters/{site}")
    out = [("flux-system/flux-system", root(site)["spec"], set(entry.encrypted), True)]
    children = classlist.walk(site)
    for c in children.values():
        if c.key == out[0][0]:
            # The root is in its own build (gotk-sync.yaml): the install
            # patches it, so the entry above holds it.
            continue
        out.append((c.key, spec_of(c, children), encrypted_of(c), classlist.is_class_path(c.path)))
    return out


def helmreleases(site: str) -> list[tuple[str, dict, bool]]:
    """Each HelmRelease of a site: its namespace/name, its spec, and whether
    a Kustomization of the class applies it."""
    out = []
    for c in classlist.walk(site).values():
        for d in c.build.docs:
            if classlist.is_kind(d, classlist.HELM_GROUP, "HelmRelease"):
                out.append((f"{classlist.namespace_of(d, c)}/{d['metadata']['name']}", d["spec"],
                            classlist.is_class_path(c.path)))
    return out


def recipients(path: str) -> set[str]:
    """The age public keys of an encrypted file: the recipients of the sops
    block of each document, and no value."""
    return {r["recipient"] for d in classlist.load(ROOT / path)
            for r in ((d.get("sops") or {}).get("age") or [])}
