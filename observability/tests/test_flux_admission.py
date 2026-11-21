"""The admission policy of the Flux children
(kubernetes/apps/flux-system/admission, docs/operations/flux-admission.md).

The lists of the policy are the class of git: classlist.py walks the Flux
trees of both sites the way Flux builds them and writes them. These tests
check that the policy holds the lists of git now, that each site lists the
policy, that only the two parents of the apps read files outside the class,
and that the binding denies. The last test runs the rules
on a throwaway API server (admission/tests/run.sh), with Docker.
"""

import ast
import importlib.util
import os
import re
import subprocess
import sys

import pytest
import yaml
from test_hand_written_rules import ROOT, docker

ADMISSION = ROOT / "kubernetes/apps/flux-system/admission"
sys.path.insert(0, str(ADMISSION))

import classlist
from classlist import flux_text as text_of
from classlist import flux_variables, substitution_off


def docs():
    return {d["kind"]: d for d in yaml.safe_load_all((ADMISSION / "app/policy.yaml").read_text()) if d}


def test_the_lists_of_the_policy_are_the_class_of_git():
    stale = [str(path.relative_to(ROOT)) for path, text, want in classlist.targets() if text != want]
    assert not stale, f"{stale}: run python3 kubernetes/apps/flux-system/admission/classlist.py write"


def test_the_walk_finds_each_child_of_each_site():
    data = classlist.lists()
    for site in classlist.SITES:
        counts = data["sites"][site]
        assert counts["kustomizations"] > 50 and 0 < counts["class"] < counts["kustomizations"], (site, counts)
    assert "flux-system/flux-admission" in data["classKustomizations"]
    assert data["classKustomizations"]["flux-system/flux-admission"] == [
        "flux-system/cluster-apps", "flux-system/cluster-apps-wsl-02"]


def test_the_walk_follows_the_patches_of_a_path():
    # The apps of the second site move two children to folders of their own
    # with a patch of spec.path; Flux builds those folders, so the walk does.
    walked = classlist.walk("wsl-02")
    assert {key: walked[key].path for key in ("flux-system/kube-prometheus-stack-addons",
                                              "flux-system/kopiur-policies")} == {
        "flux-system/kube-prometheus-stack-addons": "kubernetes/clusters/wsl-02/apps/monitoring-addons",
        "flux-system/kopiur-policies": "kubernetes/clusters/wsl-02/apps/kopiur-policies",
    }
    assert walked["flux-system/kube-prometheus-stack-addons"].build.files >= {
        "kubernetes/clusters/wsl-02/apps/monitoring-addons/kustomization.yaml"}
    assert classlist.walk("wsl")["flux-system/kopiur-policies"].path == "kubernetes/apps/kopiur-system/kopiur/policies"


PATH_KS = """
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata: {name: web, namespace: flux-system}
spec: {path: ./base, sourceRef: {kind: GitRepository, name: flux-system}}
"""
PATH_PATCH = """
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata: {name: %s, namespace: flux-system}
spec: {path: ./site}
"""


@pytest.mark.parametrize(("patch", "want"), [
    ({"patch": PATH_PATCH % "web"}, "./site"),
    ({"path": "patch.yaml"}, "./site"),
    # A patch that does not touch the path changes nothing.
    ({"patch": "apiVersion: kustomize.toolkit.fluxcd.io/v1\nkind: Kustomization\n"
               "metadata: {name: web}\nspec: {dependsOn: [{name: db}]}\n"}, "./base"),
    ({"target": {"kind": "HelmRelease"}, "patch": "- {op: add, path: /spec/install, value: {}}"}, "./base"),
    # The forms that it can not read stop it.
    ({"patch": PATH_PATCH % "other"}, None),
    ({"target": {"kind": "Kustomization", "name": "web"}, "patch": PATH_PATCH % "web"}, None),
    ({"target": {"kind": "Kustomization"}, "patch": "- {op: replace, path: /spec/path, value: ./site}"}, None),
    ({"target": {"name": "web"}, "patch": "- {op: replace, path: /spec, value: {path: ./site}}"}, None),
])
def test_the_walk_reads_one_form_of_a_patch_of_a_path(tmp_path, monkeypatch, patch, want):
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps/web.yaml").write_text(PATH_KS)
    (tmp_path / "apps/patch.yaml").write_text(PATH_PATCH % "web")
    (tmp_path / "apps/kustomization.yaml").write_text(yaml.safe_dump({"resources": ["./web.yaml"], "patches": [patch]}))
    monkeypatch.setattr(classlist, "ROOT", tmp_path)
    if want is None:
        with pytest.raises(classlist.UnreadKustomization):
            classlist.build(tmp_path / "apps")
        return
    (web,) = classlist.flux_kustomizations(classlist.build(tmp_path / "apps").docs)
    assert web["spec"]["path"] == want


SOURCE_TREE = {
    "kubernetes/clusters/lab/kustomization.yaml": "resources: [./apps.yaml]\n",
    "kubernetes/clusters/lab/apps.yaml": (
        "apiVersion: kustomize.toolkit.fluxcd.io/v1\nkind: Kustomization\n"
        "metadata: {name: cluster-apps, namespace: flux-system}\n"
        "spec: {path: ./kubernetes/clusters/lab/apps, sourceRef: {kind: GitRepository, name: flux-system}}\n"),
    "kubernetes/clusters/lab/apps/kustomization.yaml": "resources: [./web.yaml]\n",
    "kubernetes/apps/web/app/configmap.yaml": "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: web}\n",
    "kubernetes/apps/web/component/configmap.yaml": "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: extra}\n",
    # A file with a child of another source that no cluster lists.
    "kubernetes/apps/other/ks.yaml": (
        "apiVersion: kustomize.toolkit.fluxcd.io/v1\nkind: Kustomization\n"
        "metadata: {name: other, namespace: flux-system}\n"
        "spec: {path: ./vendor, sourceRef: {kind: GitRepository, name: vendor, namespace: security}}\n"),
}
OWN = {"kind": "GitRepository", "name": "flux-system"}


@pytest.mark.parametrize(("namespace", "spec", "error"), [
    ("flux-system", {"path": "./kubernetes/apps/web/app", "sourceRef": OWN}, None),
    ("flux-system", {"path": "kubernetes/apps/web/app/", "sourceRef": {**OWN, "namespace": "flux-system"}}, None),
    ("flux-system", {"path": "./kubernetes/apps/web/app", "sourceRef": OWN, "components": ["../component"]}, None),
    # Another source: its objects are not in this repository.
    ("flux-system", {"path": "./vendor", "sourceRef": {"kind": "GitRepository", "name": "vendor",
                                                      "namespace": "security"}},
     "flux-system/web: the source GitRepository security/vendor is not the GitRepository flux-system/flux-system"),
    ("flux-system", {"path": "./kubernetes/apps/web/app", "sourceRef": {**OWN, "namespace": "security"}},
     "flux-system/web: the source GitRepository security/flux-system"),
    ("flux-system", {"path": "./kubernetes/apps/web/app", "sourceRef": {"kind": "OCIRepository", "name": "flux-system"}},
     "flux-system/web: the source OCIRepository flux-system/flux-system"),
    ("flux-system", {"path": "./kubernetes/apps/web/app"}, "flux-system/web: the source None flux-system/None"),
    # The namespace of a sourceRef is the one of the Kustomization by default.
    ("security", {"path": "./kubernetes/apps/web/app", "sourceRef": OWN},
     "security/web: the source GitRepository security/flux-system"),
    # A path that is not in the repository: a build of nothing.
    ("flux-system", {"path": "./kubernetes/apps/web/missing", "sourceRef": OWN},
     "flux-system/web: the path kubernetes/apps/web/missing is not in the repository"),
    ("flux-system", {"path": "../outside", "sourceRef": OWN},
     "flux-system/web: the path ../outside is not in the repository"),
    ("flux-system", {"path": "./kubernetes/apps/web/app", "sourceRef": OWN, "components": ["../none"]},
     "flux-system/web: the path kubernetes/apps/web/app/../none is not in the repository"),
])
def test_the_walk_stops_at_a_child_that_it_can_not_read_from_this_repository(
        tmp_path, monkeypatch, namespace, spec, error):
    root = tmp_path / "repo"
    (tmp_path / "outside").mkdir()
    web = {"apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization",
           "metadata": {"name": "web", "namespace": namespace}, "spec": spec}
    for name, text in {**SOURCE_TREE, "kubernetes/clusters/lab/apps/web.yaml": yaml.safe_dump(web)}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    monkeypatch.setattr(classlist, "ROOT", root)
    if error:
        with pytest.raises(classlist.UnreadKustomization, match=re.escape(error)):
            classlist.walk("lab")
        return
    children = classlist.walk("lab")
    # The unlisted file of another source is not reached, so it does not stop the walk.
    assert sorted(children) == ["flux-system/cluster-apps", "flux-system/web"]
    want = ["web", "extra"] if spec.get("components") else ["web"]
    assert [d["metadata"]["name"] for d in children["flux-system/web"].build.docs] == want


def test_only_the_parents_of_the_apps_read_files_outside_the_class():
    # A child of the class whose build reads a file outside the class would
    # apply that file with the rights of the class. The two parents of the
    # apps read the aggregators and the ks.yaml of the children outside the
    # class; the rule parent-objects holds what they may apply.
    assert sorted(classlist.lists()["parentObjects"]) == [
        "flux-system/cluster-apps", "flux-system/cluster-apps-wsl-02"]


def test_the_class_paths_are_the_kubernetes_folders_of_owner_label():
    # Owner label holds a change in the class; the policy holds what a child
    # outside it applies. The two lists must name the same folders.
    tree = ast.parse((ROOT / ".github/scripts/owner-label-check.py").read_text())
    (ask_first,) = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    and getattr(n.targets[0], "id", None) == "ASK_FIRST"]
    folders = [p for p in ask_first if p.startswith("kubernetes/") and p.endswith("/")]
    assert sorted(folders) == sorted(classlist.CLASS_PATHS)


# The groups of the operators that the rule operator-pods keeps to the
# class (app/policy.yaml, variables.operatorPods).
OPERATOR_GROUPS = ("actions.github.com", "agents.x-k8s.io", "extensions.agents.x-k8s.io",
                   "postgresql.cnpg.io", "barmancloud.cnpg.io")


@pytest.mark.parametrize("site", classlist.SITES)
def test_no_child_outside_the_class_applies_an_object_of_an_operator_of_the_class(site):
    """D149: the database folders of the apps are in the class, so a new app
    with a database puts its db folder in the class (classlist.py and
    ASK_FIRST) before the policy denies it."""
    found = {}
    for child in classlist.walk(site).values():
        if classlist.is_class_path(child.path):
            continue
        for doc in child.build.docs:
            if classlist.api_group(doc) in OPERATOR_GROUPS:
                found.setdefault(child.key, set()).add(doc["kind"])
    assert not found, found
    policy = (ROOT / "kubernetes/apps/flux-system/admission/app/policy.yaml").read_text()
    assert all(f"'{g}'" in policy for g in OPERATOR_GROUPS)


# The Kustomization of the reduced substitution Secret, and its template on
# each site (docs/operations/flux-admission.md, "Substitution").
SUBSTITUTION_CHILD = "flux-system/flux-substitution"
SUBSTITUTION_KS = {
    "wsl": "../../../apps/flux-system/substitution/ks.yaml",
    "wsl-02": "./flux-substitution-ks.yaml",
}


def substitution_template(site):
    """The child of the reduced Secret on a site, and the Secret of its build."""
    child = classlist.walk(site)[SUBSTITUTION_CHILD]
    (secret,) = [d for d in child.build.docs
                 if d.get("kind") == "Secret" and d["metadata"]["name"] == "cluster-secrets-apps"]
    return child, secret


def secret_key_names(site):
    """The key names of the cluster-secrets of a site, from its SOPS file in
    git. SOPS encrypts the values and leaves the names in the clear; this
    reads the names only, and never a value."""
    (doc,) = [d for d in yaml.safe_load_all(
        (ROOT / f"kubernetes/clusters/{site}/cluster-secrets.sops.yaml").read_text()) if d]
    assert doc["kind"] == "Secret" and doc["metadata"]["name"] == "cluster-secrets", site
    return set(doc.get("stringData") or {}) | set(doc.get("data") or {})


@pytest.mark.parametrize("site", classlist.SITES)
def test_the_reduced_secret_builds_in_strict_mode_with_the_keys_of_the_site(site):
    """Flux substitutes in strict mode (the default of kustomize-controller
    since v1.9): a bare variable whose key is missing stops the build. So each
    variable of the template of a site is a key of its cluster-secrets."""
    child, secret = substitution_template(site)
    sources = child.doc["spec"]["postBuild"]["substituteFrom"]
    assert sources == [{"kind": "Secret", "name": "cluster-secrets"}], site
    inline = set(child.doc["spec"]["postBuild"].get("substitute") or {})
    names = secret_key_names(site)
    used = set(flux_variables(yaml.safe_dump(secret)))
    missing = sorted(used - names - inline)
    assert not missing, f"{site}: these variables of the template are not keys of its cluster-secrets: {missing}"
    # Each key is its own variable with no default, so the Secret holds the
    # values of its site and no other, and a key that the site lacks stays out
    # of its template.
    assert all(v == f"${{{k}}}" for k, v in secret["stringData"].items()), site


def substitution_of(site, child):
    """The substituteFrom of a direct child of the parent of the apps, after
    the patches of the parent, as (kind, name) pairs."""
    parent = next(d for d in yaml.safe_load_all((ROOT / f"kubernetes/clusters/{site}/apps.yaml").read_text()) if d)
    labels = child.doc["metadata"].get("labels") or {}
    sources = (child.doc["spec"].get("postBuild") or {}).get("substituteFrom")
    for patch in parent["spec"]["patches"]:
        target = patch["target"]
        assert target["labelSelector"] == "substitution.flux.home.arpa/disabled notin (true)"
        if labels.get("substitution.flux.home.arpa/disabled") == "true":
            continue
        if "name" in target and not re.fullmatch(target["name"], child.doc["metadata"]["name"]):
            continue
        sources = yaml.safe_load(patch["patch"])["spec"]["postBuild"]["substituteFrom"]
    return [(s["kind"], s["name"]) for s in sources or []]


@pytest.mark.parametrize("site", classlist.SITES)
def test_only_the_children_of_the_class_get_the_full_secret(site):
    for child in classlist.walk(site).values():
        if child.parent != classlist.APPS_PARENT[site]:
            continue
        sources = substitution_of(site, child)
        if classlist.is_class_path(child.path):
            assert ("Secret", "cluster-secrets-apps") not in sources, child.key
        else:
            assert ("Secret", "cluster-secrets") not in sources, child.key
            assert ("Secret", "cluster-secrets-apps") in sources, child.key


@pytest.mark.parametrize("site", classlist.SITES)
def test_the_reduced_secret_holds_each_key_of_the_children_outside_the_class(site):
    _, secret = substitution_template(site)
    keys = set(secret["stringData"])
    (settings,) = [d for d in yaml.safe_load_all((ROOT / f"kubernetes/clusters/{site}/cluster-settings.yaml").read_text()) if d]
    known = keys | set(settings["data"])
    missing = {}
    for child in classlist.walk(site).values():
        if classlist.is_class_path(child.path):
            continue
        inline = set((child.doc["spec"].get("postBuild") or {}).get("substitute") or {})
        for doc in child.build.docs:
            for name in flux_variables(yaml.safe_dump(doc)):
                if name not in known | inline:
                    missing.setdefault(child.key, set()).add(name)
    assert not missing, f"add these keys to the template of {site}, or a default: {missing}"


# Each Kustomization of each site that substitutes: in strict mode a
# variable with no definition stops its build on that site. CI applies
# nothing, so this test reads the sources of each site by name: the objects
# that the walk builds, and the key names of the SOPS files that its
# kustomizations list, with no value.
REQUIREMENT = re.compile(r"\s*(!?)([\w./-]+)\s*(?:(==|=|!=)\s*([\w./-]*)|\s(in|notin)\s*\(([^)]*)\))?\s*")
# Each form of a variable of Flux is read by classlist.flux_variables, the
# one reader that the names-only check of the encrypted files uses too.


def selects(selector, labels):
    """A label selector of a Flux patch target against the labels of an object."""
    for requirement in re.split(r",(?![^(]*\))", selector):
        m = REQUIREMENT.fullmatch(requirement)
        assert m, f"a label selector that this test can not read: {selector!r}"
        bang, key, op, value, set_op, values = m.groups()
        have = labels.get(key)
        if set_op:
            inside = have is not None and str(have) in {v.strip() for v in values.split(",")}
            ok = inside if set_op == "in" else not inside
        elif op:
            ok = (str(have) == value) if op in ("=", "==") else (str(have) != value)
        else:
            ok = (have is None) if bang else (have is not None)
        if not ok:
            return False
    return True


def targets(target, doc):
    meta = doc.get("metadata") or {}
    assert set(target) <= {"group", "version", "kind", "name", "namespace", "labelSelector"}, target
    version = str(doc.get("apiVersion", "")).rpartition("/")[2]
    return all((
        target.get("group", classlist.api_group(doc)) == classlist.api_group(doc),
        target.get("version", version) == version,
        target.get("kind", doc.get("kind")) == doc.get("kind"),
        re.fullmatch(target.get("name", ".*"), meta.get("name", "")),
        re.fullmatch(target.get("namespace", ".*"), meta.get("namespace", "flux-system")),
        selects(target["labelSelector"], meta.get("labels") or {}) if "labelSelector" in target else True,
    ))


def target_of(patch, body, where):
    """The target of a patch. A patch with no target applies only to the
    object that it names, by kind and name."""
    if patch.get("target"):
        return patch["target"]
    meta = (body.get("metadata") or {}) if isinstance(body, dict) else {}
    assert body and isinstance(body, dict) and body.get("kind") and meta.get("name"), (
        f"{where}: a patch with no target that names no object")
    out = {"group": classlist.api_group(body), "kind": body["kind"], "name": re.escape(meta["name"])}
    if meta.get("namespace"):
        out["namespace"] = re.escape(meta["namespace"])
    return out


def post_build(child, children):
    """The postBuild of a child as Flux applies it: its own, with the patches
    of its parent that target it merged in."""
    out = dict(child.doc["spec"].get("postBuild") or {})
    parent = children.get(child.parent)
    for patch in (parent.doc["spec"].get("patches") or []) if parent else []:
        body = yaml.safe_load(patch["patch"])
        if not targets(target_of(patch, body, parent.key), child.doc):
            continue
        assert isinstance(body, dict), f"{parent.key}: a patch that this test can not read"
        new = (body.get("spec") or {}).get("postBuild") or {}
        inline = {**(out.get("substitute") or {}), **(new.get("substitute") or {})}
        out.update(new)
        if inline:
            out["substitute"] = inline
    return out


def kustomizations(files):
    """Each kustomization.yaml of a set of files of a build, loaded."""
    for f in sorted(files):
        path = ROOT / f
        if path.name in classlist.KUSTOMIZE_FILES:
            (kz,) = classlist.load(path)[:1] or [{}]
            yield path, kz


def patch_bodies(path, kz):
    """The patches of a kustomization.yaml, as kustomize reads them: the
    inline text or the file, with no comment."""
    for patch in kz.get("patches") or []:
        text = patch.get("patch") or ((path.parent / patch["path"]).read_text() if patch.get("path") else "")
        for body in yaml.safe_load_all(text):
            if body is not None:
                yield patch, body


def sets_post_build(patch, body):
    """A patch that changes the postBuild of a Flux Kustomization, which the
    walk does not apply."""
    if isinstance(body, list):
        target = patch.get("target") or {}
        return target.get("kind") in (None, "Kustomization") and any(
            str(op.get("path", "")).startswith("/spec/postBuild") for op in body if isinstance(op, dict))
    return (classlist.is_kind(body, classlist.KUSTOMIZE_GROUP, "Kustomization")
            and "postBuild" in (body.get("spec") or {}))


def sops_key_names(path):
    """The key names of each Secret of a SOPS file. SOPS encrypts the values
    and leaves the names in the clear; this reads the names only."""
    return [(d, set(d.get("stringData") or {}) | set(d.get("data") or {}))
            for d in yaml.safe_load_all(path.read_text()) if d]


def site_sources(site, children):
    """The key names of each ConfigMap and Secret of flux-system on a site,
    by (kind, name), from git: the objects of the walk, and the SOPS files
    that a kustomization of the walk lists."""
    entry = classlist.build(ROOT / f"kubernetes/clusters/{site}")
    builds = [(entry, None)] + [(child.build, child) for child in children.values()]
    named = []
    for build, child in builds:
        docs = [(d, set(d.get("data") or {}) | set(d.get("stringData") or {})) for d in build.docs]
        for path, kz in kustomizations(build.files):
            for name in kz.get("resources") or []:
                if ".sops." in name:
                    for d, keys in sops_key_names(path.parent / name):
                        if kz.get("namespace"):
                            d.setdefault("metadata", {})["namespace"] = kz["namespace"]
                        docs.append((d, keys))
        for d, keys in docs:
            namespace = classlist.namespace_of(d, child) if child else (d.get("metadata") or {}).get("namespace")
            if d.get("kind") in ("ConfigMap", "Secret") and namespace in (None, "flux-system"):
                named.append((d, keys))
    out = {}
    for d, keys in named:
        out.setdefault((d["kind"], d["metadata"]["name"]), set()).update(keys)
    return out


def undefined_variables(site):
    """Each variable of a child of a site that no source of that child
    defines, by child."""
    children = classlist.walk(site)
    sources = site_sources(site, children)
    missing = {}
    for child in children.values():
        spec = post_build(child, children)
        if not spec.get("substitute") and not spec.get("substituteFrom"):
            continue
        found = missing.setdefault(child.key, set())
        known = set(spec.get("substitute") or {})
        for source in spec.get("substituteFrom") or []:
            keys = sources.get((source["kind"], source["name"]))
            if keys is None and not source.get("optional"):
                found.add(f"<no {source['kind']} {source['name']} that this test can read: it reads the objects "
                          "of the walk and the SOPS files that a kustomization lists, and no generator>")
            known |= keys or set()
        texts = [text_of(doc) for doc in child.build.docs if not substitution_off(doc)]
        # Flux applies the patches of each kustomization.yaml of the build,
        # then substitutes the result.
        for path, kz in kustomizations(child.build.files):
            for patch, body in patch_bodies(path, kz):
                if sets_post_build(patch, body):
                    found.add(f"<a patch of {path.relative_to(ROOT)} that sets postBuild, which this test "
                              "does not apply>")
                if not (isinstance(body, dict) and substitution_off(body)):
                    texts.append(text_of(body))
        found.update(name for text in texts for name in flux_variables(text) if name not in known)
    return {k: sorted(v) for k, v in sorted(missing.items()) if v}


@pytest.mark.parametrize("site", classlist.SITES)
def test_each_variable_of_each_site_has_a_definition_in_that_site(site):
    """A change that adds a variable to a manifest, or a key to a template,
    that one site does not define fails here, not in the build of that site."""
    missing = undefined_variables(site)
    assert not missing, f"{site}: add these keys to the sources of {site}, or a default: {missing}"


def test_the_forms_of_a_variable():
    # The lab of fluxcd/pkg/envsubst v1.9.0 in strict mode, with X unset.
    stops = ["${X}", "${X/./-}", "${X//./-}", "${X/#a/b}", "${X/%a/b}", "${X:1}", "${X:1:2}", "${#X}",
             "${X^}", "${X^^}", "${X,}", "${X,,}", "${X#a}", "${X##a}", "${X%a}", "${X%%a}",
             "$$${X}"]
    passes = ["${X:=d}", "${X:-d}", "${X=d}", "${X:+d}", "${X:?d}", "${X:-$${Y}}", "$${X}", "$$$${X}", "$X"]
    for text in stops:
        assert list(flux_variables(text)) == ["X"], text
    for text in passes:
        assert list(flux_variables(text)) == [], text
    assert list(flux_variables("${X:-${Y}}")) == ["Y"]
    for text in ("${X-d}", "${X+d}", "${X?d}", "${}", "${ X}", "${X"):
        (name,) = flux_variables(text)
        assert name.startswith("<a form that Flux can not read"), text


def test_the_selector_and_the_patch_of_the_parent():
    off = "substitution.flux.home.arpa/disabled"
    assert selects(f"{off} notin (true)", {})
    assert not selects(f"{off} notin (true)", {off: "true"})
    assert selects(f"{off} in (true, yes)", {off: "yes"})
    assert selects(f"a=b,!{off}", {"a": "b"}) and not selects("a!=b", {"a": "b"})
    with pytest.raises(AssertionError):
        selects("a notin true", {})
    ks = {"apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization",
          "metadata": {"name": "x", "namespace": "flux-system", "labels": {off: "true"}},
          "spec": {"postBuild": {"substitute": {"A": "1"}}}}
    patch = {"target": {"group": "kustomize.toolkit.fluxcd.io", "kind": "Kustomization",
                        "labelSelector": f"{off} notin (true)"},
             "patch": "spec:\n  postBuild:\n    substitute: {B: '2'}\n    substituteFrom: [{kind: Secret, name: s}]\n"}
    parent = classlist.Child("p", "", "p", {"spec": {"patches": [patch]}}, classlist.Build())
    child = classlist.Child("c", "p", "c", ks, classlist.Build())
    assert post_build(child, {"p": parent}) == {"substitute": {"A": "1"}}
    del ks["metadata"]["labels"]
    assert post_build(child, {"p": parent}) == {"substitute": {"A": "1", "B": "2"},
                                                "substituteFrom": [{"kind": "Secret", "name": "s"}]}


# A small tree of one site, for the cases that the tree of git does not
# have today. The value of the SOPS file is a fake, and the test reads only
# its key names.
LAB = {
    "kubernetes/clusters/lab/kustomization.yaml": """
        resources: [./cluster-settings.yaml, ./cluster-secrets.sops.yaml, ./apps.yaml]
        """,
    "kubernetes/clusters/lab/cluster-settings.yaml": """
        apiVersion: v1
        kind: ConfigMap
        metadata: {name: cluster-settings, namespace: flux-system}
        data: {CLUSTER_NAME: lab}
        """,
    "kubernetes/clusters/lab/cluster-secrets.sops.yaml": """
        apiVersion: v1
        kind: Secret
        metadata: {name: cluster-secrets, namespace: flux-system}
        stringData: {SECRET_DOMAIN: "ENC[AES256_GCM,data:ZmFrZQ==,type:str]"}
        sops: {version: 3.9.0}
        """,
    "kubernetes/clusters/lab/apps.yaml": """
        apiVersion: kustomize.toolkit.fluxcd.io/v1
        kind: Kustomization
        metadata: {name: cluster-apps, namespace: flux-system}
        spec:
          path: ./kubernetes/clusters/lab/apps
          sourceRef: {kind: GitRepository, name: flux-system}
          postBuild:
            substituteFrom: [{kind: ConfigMap, name: cluster-settings}, {kind: Secret, name: cluster-secrets}]
          patches:
            - target:
                group: kustomize.toolkit.fluxcd.io
                kind: Kustomization
                labelSelector: substitution.flux.home.arpa/disabled notin (true)
              patch: |-
                apiVersion: kustomize.toolkit.fluxcd.io/v1
                kind: Kustomization
                metadata: {name: not-used}
                spec:
                  postBuild:
                    substituteFrom: [{kind: ConfigMap, name: cluster-settings}, {kind: Secret, name: cluster-secrets}]
        """,
    "kubernetes/clusters/lab/apps/kustomization.yaml": """
        resources: [./web.yaml]
        """,
    "kubernetes/clusters/lab/apps/web.yaml": """
        apiVersion: kustomize.toolkit.fluxcd.io/v1
        kind: Kustomization
        metadata: {name: web, namespace: flux-system}
        spec:
          path: ./kubernetes/apps/web
          sourceRef: {kind: GitRepository, name: flux-system}
        """,
    "kubernetes/apps/web/kustomization.yaml": """
        resources: [./configmap.yaml]
        """,
    "kubernetes/apps/web/configmap.yaml": """
        apiVersion: v1
        kind: ConfigMap
        metadata: {name: web, namespace: default}
        data: {host: "${SECRET_DOMAIN/./-}", site: "${CLUSTER_NAME}"}
        """,
}
# A patch of the apps of the site, on the child web, as the second site has.
APPS_PATCH = """
    resources: [./web.yaml]
    patches:
      - patch: |-
          apiVersion: kustomize.toolkit.fluxcd.io/v1
          kind: Kustomization
          metadata: {name: web, namespace: flux-system}
          spec:
            patches:
              - target: {kind: ConfigMap, name: web}
                patch: |-
                  - {op: add, path: /data/peer, value: "%s"}
    """
# A SOPS Secret of flux-system in the folder of an app, which web names.
APP_SOPS = {
    "kubernetes/apps/web/kustomization.yaml": """
        resources: [./configmap.yaml, ./web-secrets.sops.yaml]
        """,
    "kubernetes/apps/web/web-secrets.sops.yaml": """
        apiVersion: v1
        kind: Secret
        metadata: {name: web-secrets, namespace: flux-system}
        stringData: {WEB_TOKEN: "ENC[AES256_GCM,data:ZmFrZQ==,type:str]"}
        sops: {version: 3.9.0}
        """,
    "kubernetes/clusters/lab/apps/web.yaml": """
        apiVersion: kustomize.toolkit.fluxcd.io/v1
        kind: Kustomization
        metadata:
          name: web
          namespace: flux-system
          labels: {substitution.flux.home.arpa/disabled: "true"}
        spec:
          path: ./kubernetes/apps/web
          sourceRef: {kind: GitRepository, name: flux-system}
          postBuild:
            substituteFrom: [{kind: Secret, name: web-secrets}, {kind: ConfigMap, name: %s}]
        """,
    "kubernetes/apps/web/configmap.yaml": """
        apiVersion: v1
        kind: ConfigMap
        metadata: {name: web, namespace: default}
        data: {token: "${WEB_TOKEN}"}
        """,
}
# A patch of the parent with no target, which names a child other than web.
NO_TARGET = LAB["kubernetes/clusters/lab/apps.yaml"] + """
            - patch: |-
                apiVersion: kustomize.toolkit.fluxcd.io/v1
                kind: Kustomization
                metadata: {name: other, namespace: flux-system}
                spec:
                  postBuild:
                    substitute: {NEW_KEY: x}
        """
WEB = "kubernetes/apps/web/configmap.yaml"
CONFIGMAP = LAB[WEB].replace('site: "${CLUSTER_NAME}"', 'site: "%s"')
CANT_READ = ("<no ConfigMap web-generated that this test can read: it reads the objects of the walk and the SOPS "
             "files that a kustomization lists, and no generator>")
# A variable in a YAML comment does not stop the build: the controller
# substitutes each object of the build as res.AsYAML() gives it, which goes
# through JSON, so no comment is left (the lab of kustomize api v0.21.1 and
# fluxcd/pkg/envsubst v1.9.0 in strict mode, at kustomize-controller
# v1.10.1). A line of a block string that starts with "#" is text, and does.
COMMENTED = """
    # ${NEW_KEY}
    apiVersion: v1
    kind: ConfigMap
    metadata: {name: web, namespace: default}  # ${NEW_KEY}
    data:
      # ${NEW_KEY}
      site: "${CLUSTER_NAME}"  # ${NEW_KEY}
    """
BLOCK_STRING = """
    apiVersion: v1
    kind: ConfigMap
    metadata: {name: web, namespace: default}
    data:
      conf: |
        # ${NEW_KEY}
        site=${CLUSTER_NAME}
    """


@pytest.mark.parametrize(("change", "want"), [
    ({}, {}),
    # m3: a new bare variable in a manifest of a child that substitutes.
    ({WEB: CONFIGMAP % "${NEW_KEY}"}, {"flux-system/web": ["NEW_KEY"]}),
    # m2: the same name in a form that reads the value.
    ({WEB: CONFIGMAP % "${NEW_KEY/./-}"}, {"flux-system/web": ["NEW_KEY"]}),
    ({WEB: CONFIGMAP % "${NEW_KEY:=lab}"}, {}),
    ({WEB: CONFIGMAP % "${NEW_KEY-lab}"}, {"flux-system/web": ["<a form that Flux can not read: ${NEW_KEY-...}>"]}),
    # m1b: a new variable in a patch of the kustomization.yaml of the apps,
    # which the parent substitutes.
    ({"kubernetes/clusters/lab/apps/kustomization.yaml": APPS_PATCH % "${NEW_KEY}"},
     {"flux-system/cluster-apps": ["NEW_KEY"]}),
    ({"kubernetes/clusters/lab/apps/kustomization.yaml": APPS_PATCH % "${CLUSTER_NAME}"}, {}),
    # A patch there that sets postBuild fails, as the walk does not apply it.
    ({"kubernetes/clusters/lab/apps/kustomization.yaml": APPS_PATCH.replace(
        "spec:\n", "spec:\n            postBuild: {substitute: {A: b}}\n", 1) % "x"},
     {"flux-system/cluster-apps": [("<a patch of kubernetes/clusters/lab/apps/kustomization.yaml that sets "
                                    "postBuild, which this test does not apply>")]}),
    # F3: a SOPS Secret in the folder of an app is a source; a generated
    # ConfigMap is a source that the test can not read, and says so.
    ({**APP_SOPS, "kubernetes/clusters/lab/apps/web.yaml": APP_SOPS["kubernetes/clusters/lab/apps/web.yaml"]
      % "cluster-settings"}, {}),
    ({**APP_SOPS, "kubernetes/clusters/lab/apps/web.yaml": APP_SOPS["kubernetes/clusters/lab/apps/web.yaml"]
      % "web-generated"}, {"flux-system/web": [CANT_READ]}),
    # F4: a patch of the parent with no target reaches only the child that
    # it names, so web does not get NEW_KEY from it.
    ({"kubernetes/clusters/lab/apps.yaml": NO_TARGET, WEB: CONFIGMAP % "${NEW_KEY}"},
     {"flux-system/web": ["NEW_KEY"]}),
    # A variable in a comment passes, and one in a block string fails.
    ({WEB: COMMENTED}, {}),
    ({WEB: BLOCK_STRING}, {"flux-system/web": ["NEW_KEY"]}),
])
def test_the_check_of_the_variables_on_a_small_tree(tmp_path, monkeypatch, change, want):
    import textwrap
    for name, text in {**LAB, **change}.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(textwrap.dedent(text))
    monkeypatch.setattr(classlist, "ROOT", tmp_path)
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    assert undefined_variables("lab") == want


def test_both_sites_list_the_policy():
    for site in classlist.SITES:
        text = (ROOT / f"kubernetes/clusters/{site}/apps/kustomization.yaml").read_text()
        assert "- ../../../apps/flux-system/admission/ks.yaml" in text, site
        assert f"- {SUBSTITUTION_KS[site]}" in text, site
        others = set(SUBSTITUTION_KS.values()) - {SUBSTITUTION_KS[site]}
        assert not any(f"- {o}" in text for o in others), site


def objects_check():
    path = ADMISSION / "app/scripts/admission_objects.py"
    spec = importlib.util.spec_from_file_location("admission_objects", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_objects_check_reads_the_class_of_the_policy():
    check = objects_check()
    kustomizations, helmreleases = check.class_lists(docs()["ValidatingAdmissionPolicy"])
    data = classlist.lists()
    assert kustomizations == set(data["classKustomizations"])
    assert helmreleases == set(data["classHelmReleases"])


def test_the_objects_check_finds_an_admission_object_owned_outside_the_class():
    check = objects_check()
    k, h = {"flux-system/flux-admission"}, {"cert-manager/cert-manager"}

    def hook(name, labels):
        return {"kind": "MutatingWebhookConfiguration", "metadata": {"name": name, "labels": labels}}

    objects = [
        hook("policy", {"kustomize.toolkit.fluxcd.io/namespace": "flux-system",
                        "kustomize.toolkit.fluxcd.io/name": "flux-admission"}),
        hook("cert-manager-webhook", {"helm.toolkit.fluxcd.io/namespace": "cert-manager",
                                      "helm.toolkit.fluxcd.io/name": "cert-manager"}),
        hook("by-hand", {}),
        hook("outside-k", {"kustomize.toolkit.fluxcd.io/namespace": "flux-system",
                           "kustomize.toolkit.fluxcd.io/name": "mealie"}),
        hook("outside-h", {"helm.toolkit.fluxcd.io/namespace": "production",
                           "helm.toolkit.fluxcd.io/name": "mealie"}),
    ]
    lines, unowned = check.findings(objects, k, h)
    assert unowned == 1
    assert [line.split()[2] for line in lines] == ["name=outside-k", "name=outside-h"]
    assert check.findings(objects[:3], k, h) == ([], 1)


def test_the_objects_check_may_only_read():
    rules = [d for d in yaml.safe_load_all((ADMISSION / "app/objects-check.yaml").read_text())
             if d and d["kind"] == "ClusterRole"]
    assert [r["verbs"] for role in rules for r in role["rules"]] == [["get", "list"]]


def test_the_policy_matches_the_two_controllers_and_fails_closed():
    policy = docs()["ValidatingAdmissionPolicy"]
    assert policy["spec"]["failurePolicy"] == "Fail"
    (condition,) = policy["spec"]["matchConditions"]
    for account in ("kustomize-controller", "helm-controller"):
        assert f"system:serviceaccount:flux-system:{account}" in condition["expression"]


def test_the_binding_denies_and_audits():
    # It was Audit and Warn until a read of the audit counts of both sites
    # (docs/operations/flux-admission.md, "Roll-out"). Audit keeps the
    # annotation with the name of a refused object in the audit stream.
    assert docs()["ValidatingAdmissionPolicyBinding"]["spec"]["validationActions"] == ["Deny", "Audit"]


def test_the_rules_on_a_throwaway_api_server():
    docker()
    env = dict(os.environ, PYTHON=sys.executable)
    env.pop("KUBECONFIG", None)
    result = subprocess.run(["bash", str(ADMISSION / "tests/run.sh")], capture_output=True, text=True,
                            timeout=900, check=False, env=env)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output[-12000:]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# A remote source of kustomize: a URL, a git address, a query of a ref, or a
# host name before the first slash.
REMOTE = re.compile(r"://|^git@|^git::|[?&](?:ref|version)=|^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?::[0-9]+)?/")


@pytest.mark.parametrize("site", classlist.SITES)
def test_kustomize_controller_refuses_remote_bases(site):
    # A URL in a kustomization outside the class would bring in any
    # manifest, an admission object among them, which the policy can not see.
    patches = yaml.safe_load((ROOT / f"kubernetes/clusters/{site}/flux-system/kustomization.yaml").read_text())["patches"]
    args = [op["value"] for p in patches if (p.get("target") or {}).get("name") == "kustomize-controller"
            for op in yaml.safe_load(p["patch"])]
    assert args == ["--no-remote-bases=true"]


def test_no_build_of_flux_uses_a_remote_source():
    # With the flag, a build with one fails. Only the bootstrap of Flux, which
    # no Kustomization builds, has one.
    built = {child.path for site in classlist.SITES for child in classlist.walk(site).values()}
    found = []
    for path in ROOT.glob("kubernetes/**/kustomization.yaml"):
        for doc in yaml.safe_load_all(path.read_text()):
            for key in ("resources", "components", "bases", "crds"):
                found += [(str(path.relative_to(ROOT)), entry) for entry in (doc or {}).get(key) or []
                          if isinstance(entry, str) and REMOTE.search(entry)]
    assert {path for path, _ in found} == {"kubernetes/bootstrap/kustomization.yaml"}, found
    assert not any(p == "kubernetes/bootstrap" or p.startswith("kubernetes/bootstrap/") for p in built)


@pytest.mark.parametrize(
    ("api_version", "group", "kustomization", "helmrelease"),
    [
        ("kustomize.toolkit.fluxcd.io/v1", "kustomize.toolkit.fluxcd.io", True, False),
        ("helm.toolkit.fluxcd.io/v2", "helm.toolkit.fluxcd.io", False, True),
        ("v1", "", False, False),
        ("", "", False, False),
        # Groups that a substring or prefix match takes: "fluxcd.io" in the
        # version, or a group that starts with a Flux group.
        ("evil.fluxcd.io/v1", "evil.fluxcd.io", False, False),
        ("kustomize.toolkit.fluxcd.io.example.com/v1", "kustomize.toolkit.fluxcd.io.example.com", False, False),
        ("helm.toolkit.fluxcd.io.example.com/v2", "helm.toolkit.fluxcd.io.example.com", False, False),
        ("example.com/kustomize.toolkit.fluxcd.io/v1", "example.com", False, False),
        ("xkustomize.toolkit.fluxcd.io/v1", "xkustomize.toolkit.fluxcd.io", False, False),
    ],
)
def test_the_walk_takes_the_api_group_of_a_document_exactly(api_version, group, kustomization, helmrelease):
    for kind, expect, flux_group in (
        ("Kustomization", kustomization, classlist.KUSTOMIZE_GROUP),
        ("HelmRelease", helmrelease, classlist.HELM_GROUP),
    ):
        doc = {"apiVersion": api_version, "kind": kind, "metadata": {"name": "x"}}
        assert classlist.api_group(doc) == group
        assert classlist.is_kind(doc, flux_group, kind) is expect
        if kind == "Kustomization":
            assert (classlist.flux_kustomizations([doc]) == [doc]) is expect
