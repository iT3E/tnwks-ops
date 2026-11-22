"""Writes the inputs of test_flux_admission.py as JSON (run.sh calls it).

The test runs in a plain Python image with no YAML parser, so this step, in
the Python of the repository (observability), writes:

- policy.json: the policy and its binding (../app/policy.yaml);
- crds.json: the CRDs of the Flux objects that the test makes, from the
  Flux install of the clusters;
- kustomizations.json and helmreleases.json: each Flux Kustomization and
  HelmRelease of both sites in git (classlist.py walks the trees), with the
  Kustomization that applies it, after the substitution of the settings of
  the site;
- names.json: the names of the children that the test cases use.

  python3 render.py <output directory>
"""

import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import classlist

ROOT = classlist.ROOT
CRDS = (
    "kustomizations.kustomize.toolkit.fluxcd.io",
    "helmreleases.helm.toolkit.fluxcd.io",
    "helmcharts.source.toolkit.fluxcd.io",
    "imageupdateautomations.image.toolkit.fluxcd.io",
)


# The kinds of the operators that make a pod from a template of their own
# (the rule operator-pods): CRDs that take any spec, enough for the test.
OPERATOR_KINDS = (
    ("actions.github.com", "v1alpha1", "AutoscalingRunnerSet", "autoscalingrunnersets"),
    ("actions.github.com", "v1alpha1", "EphemeralRunner", "ephemeralrunners"),
    ("agents.x-k8s.io", "v1beta1", "Sandbox", "sandboxes"),
    ("extensions.agents.x-k8s.io", "v1beta1", "SandboxTemplate", "sandboxtemplates"),
    ("postgresql.cnpg.io", "v1", "Cluster", "clusters"),
    ("postgresql.cnpg.io", "v1", "Pooler", "poolers"),
)


def operator_crd(group: str, version: str, kind: str, plural: str) -> dict:
    return {
        "apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition",
        "metadata": {"name": f"{plural}.{group}"},
        "spec": {"group": group, "scope": "Namespaced",
                 "names": {"kind": kind, "plural": plural, "singular": kind.lower(), "listKind": kind + "List"},
                 "versions": [{"name": version, "served": True, "storage": True, "schema": {"openAPIV3Schema": {
                     "type": "object", "x-kubernetes-preserve-unknown-fields": True}}}]},
    }


VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::=([^}]*))?\}")


def substitute(doc: dict, settings: dict) -> dict:
    """The substitution of Flux with the settings of the site and the
    defaults of the manifests ("${NAME:=default}"); a name with neither
    stays as it is. The cluster Secret is not read."""
    text = yaml.safe_dump(doc)
    text = VAR.sub(lambda m: settings.get(m.group(1), m.group(2) if m.group(2) is not None else m.group(0)), text)
    return yaml.safe_load(text)


def settings_of(site: str) -> dict:
    (doc,) = [d for d in yaml.safe_load_all((ROOT / f"kubernetes/clusters/{site}/cluster-settings.yaml").read_text()) if d]
    return {k: str(v) for k, v in (doc.get("data") or {}).items()}


def main(out: Path) -> None:
    policy = [d for d in yaml.safe_load_all((HERE.parent / "app/policy.yaml").read_text()) if d]
    components = ROOT / "kubernetes/clusters/wsl/flux-system/gotk-components.yaml"
    crds = [d for d in yaml.safe_load_all(components.read_text())
            if d and d.get("kind") == "CustomResourceDefinition" and d["metadata"]["name"] in CRDS]
    assert len(crds) == len(CRDS), [d["metadata"]["name"] for d in crds]
    crds += [operator_crd(*k) for k in OPERATOR_KINDS]
    data = classlist.lists()
    kustomizations, helmreleases = [], []
    for site in classlist.SITES:
        settings = settings_of(site)
        for child in classlist.walk(site).values():
            kustomizations.append({"site": site, "parent": child.parent, "doc": substitute(child.doc, settings)})
            for d in child.build.docs:
                if classlist.is_kind(d, classlist.HELM_GROUP, "HelmRelease"):
                    d = substitute(d, settings)
                    d["metadata"]["namespace"] = classlist.namespace_of(d, child)
                    helmreleases.append({"site": site, "applier": child.key, "doc": d})
    class_k = data["classKustomizations"]
    names = {
        "outsideK": "flux-system/mealie",
        "outsideHR": "production/mealie",
        "classK": "flux-system/cilium",
        "classHR": "kube-system/cilium",
        "parent": "flux-system/cluster-apps",
        # A Kustomization of the class on the second site only: on the first,
        # its name with the parent of the first is not in the class.
        "secondSiteOnly": next(k for k, v in class_k.items() if v == ["flux-system/cluster-apps-wsl-02"]),
        # A HelmRelease of the class in a namespace that children outside
        # the class share.
        "sharedClassHR": next(h for h in data["classHelmReleases"]
                              if h.split("/")[0] not in data["classNamespaces"]),
        "classNamespaces": data["classNamespaces"],
    }
    assert names["outsideK"] not in class_k and names["classK"] in class_k
    assert names["outsideHR"] not in data["classHelmReleases"] and names["classHR"] in data["classHelmReleases"]
    (out / "policy.json").write_text(json.dumps(policy))
    (out / "crds.json").write_text(json.dumps(crds))
    (out / "kustomizations.json").write_text(json.dumps(kustomizations))
    (out / "helmreleases.json").write_text(json.dumps(helmreleases))
    (out / "names.json").write_text(json.dumps(names))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
