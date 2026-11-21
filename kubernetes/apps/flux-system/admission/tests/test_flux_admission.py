"""The admission policy of the Flux children, on a throwaway API server (run.sh).

The API server is the version of the clusters. The test talks to it with
the client certificate of its admin, over HTTPS, with the standard library
only. A request of a Flux controller is a request of the admin that
impersonates the service account of that controller, with the owner labels
that the controller sets. Each request that a case makes is a dry run, so no
object is made, except the few that the cases of an update need.

1. The policy of git with a Deny binding: each case of each rule, a child
   outside the class refused, and the same object from a child of the class
   or from a person let through.
2. Each Flux Kustomization and HelmRelease of both sites in git, from the
   Kustomization that applies it: none refused.
3. The binding of git (Audit and Warn): a refused case passes with a warning.
4. Each rule switched off in turn: at least one of its own cases passes, so
   each case can fail.
"""

import copy
import json
import re
import ssl
import time
import unittest
import urllib.error
import urllib.request

API = "https://apiserver:6443"
ADMISSION = "/apis/admissionregistration.k8s.io/v1"
KUSTOMIZE = "system:serviceaccount:flux-system:kustomize-controller"
HELM = "system:serviceaccount:flux-system:helm-controller"
CONTEXT = ssl.create_default_context(cafile="/pki/ca.crt")
CONTEXT.load_cert_chain("/pki/admin.crt", "/pki/admin.key")


def read(name):
    with open(f"/work/{name}.json", encoding="utf-8") as f:
        return json.load(f)


POLICY = read("policy")
CRDS = read("crds")
GIT_KUSTOMIZATIONS = read("kustomizations")
GIT_HELMRELEASES = read("helmreleases")
NAMES = read("names")

# kind: (plural, namespaced)
KINDS = {
    "ConfigMap": ("configmaps", True),
    "Secret": ("secrets", True),
    "Namespace": ("namespaces", False),
    "PersistentVolume": ("persistentvolumes", False),
    "Pod": ("pods", True),
    "Event": ("events", True),
    "ClusterRole": ("clusterroles", False),
    "ClusterRoleBinding": ("clusterrolebindings", False),
    "Role": ("roles", True),
    "RoleBinding": ("rolebindings", True),
    "CustomResourceDefinition": ("customresourcedefinitions", False),
    "Deployment": ("deployments", True),
    "StatefulSet": ("statefulsets", True),
    "DaemonSet": ("daemonsets", True),
    "ReplicaSet": ("replicasets", True),
    "Job": ("jobs", True),
    "CronJob": ("cronjobs", True),
    "NetworkPolicy": ("networkpolicies", True),
    "Lease": ("leases", True),
    "Kustomization": ("kustomizations", True),
    "HelmRelease": ("helmreleases", True),
    "HelmChart": ("helmcharts", True),
    "ImageUpdateAutomation": ("imageupdateautomations", True),
    "ValidatingAdmissionPolicy": ("validatingadmissionpolicies", False),
    "ValidatingAdmissionPolicyBinding": ("validatingadmissionpolicybindings", False),
}
VERSIONS = {
    "ConfigMap": "v1", "Secret": "v1", "Namespace": "v1", "PersistentVolume": "v1", "Pod": "v1",
    "Event": "v1", "ClusterRole": "rbac.authorization.k8s.io/v1",
    "ClusterRoleBinding": "rbac.authorization.k8s.io/v1", "Role": "rbac.authorization.k8s.io/v1",
    "RoleBinding": "rbac.authorization.k8s.io/v1", "CustomResourceDefinition": "apiextensions.k8s.io/v1",
    "Deployment": "apps/v1", "StatefulSet": "apps/v1", "DaemonSet": "apps/v1", "ReplicaSet": "apps/v1",
    "Job": "batch/v1", "CronJob": "batch/v1", "NetworkPolicy": "networking.k8s.io/v1",
    "Lease": "coordination.k8s.io/v1", "ValidatingAdmissionPolicy": "admissionregistration.k8s.io/v1",
}
for crd in CRDS:
    served = [v["name"] for v in crd["spec"]["versions"] if v.get("storage")]
    VERSIONS[crd["spec"]["names"]["kind"]] = f"{crd['spec']['group']}/{served[0]}"


def call(method, path, body=None, user=None, content_type="application/json"):
    """The status, the JSON body and the warnings of one request."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(API + path, data=data, method=method)
    request.add_header("Content-Type", content_type)
    if user:
        request.add_header("Impersonate-User", user)
    try:
        with urllib.request.urlopen(request, context=CONTEXT, timeout=30) as response:
            return response.status, parse(response.read()), response.headers.get_all("Warning") or []
    except urllib.error.HTTPError as error:
        return error.code, parse(error.read()), error.headers.get_all("Warning") or []


def parse(raw):
    try:
        return json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {"text": raw.decode(errors="replace")}


def wait_for(what, test, seconds=90):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if test():
                return
        except OSError:
            pass
        time.sleep(0.5)
    raise AssertionError(f"no {what} in {seconds} s")


def collection(doc):
    version = doc["apiVersion"]
    base = "/api/v1" if version == "v1" else f"/apis/{version}"
    plural, namespaced = KINDS[doc["kind"]]
    if namespaced:
        return f"{base}/namespaces/{doc['metadata']['namespace']}/{plural}"
    return f"{base}/{plural}"


def create(doc, user=None, dry=True):
    return call("POST", collection(doc) + ("?dryRun=All" if dry else ""), doc, user)


def replace(doc, user=None, dry=True):
    path = f"{collection(doc)}/{doc['metadata']['name']}"
    return call("PUT", path + ("?dryRun=All" if dry else ""), doc, user)


def k_labels(key):
    namespace, name = key.split("/")
    return {"kustomize.toolkit.fluxcd.io/name": name, "kustomize.toolkit.fluxcd.io/namespace": namespace}


def h_labels(key):
    namespace, name = key.split("/")
    return {"helm.toolkit.fluxcd.io/name": name, "helm.toolkit.fluxcd.io/namespace": namespace}


def obj(kind, name, namespace=None, labels=None, **fields):
    doc = {"apiVersion": VERSIONS[kind], "kind": kind, "metadata": {"name": name}}
    if namespace:
        doc["metadata"]["namespace"] = namespace
    if labels:
        doc["metadata"]["labels"] = dict(labels)
    doc.update(fields)
    return doc


def container(**security):
    c = {"name": "app", "image": "registry.invalid/app:1"}
    if security:
        c["securityContext"] = security
    return c


def pod_spec(*containers, **fields):
    spec = {"containers": list(containers) or [container()]}
    spec.update(fields)
    return spec


def workload(kind, name, namespace, labels, spec):
    template = {"metadata": {"labels": {"app": name}}, "spec": spec}
    selector = {"matchLabels": {"app": name}}
    if kind == "Pod":
        return obj(kind, name, namespace, labels, spec=spec)
    if kind == "CronJob":
        spec = dict(spec, restartPolicy="Never")
        return obj(kind, name, namespace, labels, spec={
            "schedule": "0 0 * * *", "jobTemplate": {"spec": {"template": dict(template, spec=spec)}}})
    if kind == "Job":
        return obj(kind, name, namespace, labels, spec={"template": dict(template, spec=dict(spec, restartPolicy="Never"))})
    body = {"selector": selector, "template": template}
    if kind == "StatefulSet":
        body["serviceName"] = name
    return obj(kind, name, namespace, labels, spec=body)


def kustomization(name, path, labels, **spec):
    body = {"interval": "30m", "path": path, "prune": True,
            "sourceRef": {"kind": "GitRepository", "name": "flux-system"}}
    body.update(spec)
    return obj("Kustomization", name, "flux-system", labels, spec=body)


def helmrelease(key, labels, **spec):
    namespace, name = key.split("/")
    body = {"interval": "30m", "chart": {"spec": {"chart": "app-template", "version": "4.0.0",
            "sourceRef": {"kind": "HelmRepository", "name": "bjw-s", "namespace": "flux-system"}}}}
    body.update(spec)
    return obj("HelmRelease", name, namespace, labels, spec=body)


OUT_K = k_labels(NAMES["outsideK"])
CLASS_K = k_labels(NAMES["classK"])
PARENT = k_labels(NAMES["parent"])
OUT_H = h_labels(NAMES["outsideHR"])
CLASS_H = h_labels(NAMES["classHR"])
SUBST = {"substituteFrom": [{"kind": "ConfigMap", "name": "cluster-settings"},
                            {"kind": "Secret", "name": "cluster-secrets"}]}
APP_PATH = "./kubernetes/apps/production/new-app/app"


def privileged_cases():
    """A pod spec with each field of the rule, in each kind of workload, in
    the three namespaces with the privileged level and more."""
    variants = [
        ("privileged", "Deployment", pod_spec(container(privileged=True))),
        ("host-path", "StatefulSet", pod_spec(volumes=[{"name": "dev", "hostPath": {"path": "/dev"}}])),
        ("host-network", "DaemonSet", pod_spec(hostNetwork=True)),
        ("host-pid", "Job", pod_spec(hostPID=True)),
        ("host-ipc", "CronJob", pod_spec(hostIPC=True)),
        ("host-port", "Pod", pod_spec({"name": "app", "image": "registry.invalid/app:1",
                                       "ports": [{"containerPort": 80, "hostPort": 80}]})),
        ("added-capability", "ReplicaSet", pod_spec(
            container(), initContainers=[dict(container(capabilities={"add": ["NET_ADMIN"]}), name="init")])),
    ]
    cases = []
    for namespace in ("home-automation", "media", "production"):
        cases.append(("privileged-pod", f"a privileged Deployment in {namespace} (test object)", KUSTOMIZE, "deny",
                      workload("Deployment", "priv", namespace, OUT_K, pod_spec(container(privileged=True)))))
    for what, kind, spec in variants:
        cases.append(("privileged-pod", f"{what} in a {kind}", KUSTOMIZE, "deny",
                      workload(kind, f"p-{what}", "selfhosted", OUT_K, spec)))
    cases.append(("privileged-pod", "a privileged Deployment of a HelmRelease outside the class", HELM, "deny",
                  workload("Deployment", "priv-helm", "production", OUT_H, pod_spec(container(privileged=True)))))
    cases.append(("privileged-pod", "a pod spec that drops each capability", KUSTOMIZE, "allow",
                  workload("Deployment", "plain", "production", OUT_K, pod_spec(
                      container(capabilities={"drop": ["ALL"]}, allowPrivilegeEscalation=False)))))
    cases.append(("privileged-pod", "a privileged DaemonSet of a HelmRelease of the class", HELM, "allow",
                  workload("DaemonSet", "agent", "kube-system", CLASS_H, pod_spec(
                      container(privileged=True), hostNetwork=True))))
    return cases


def cases():
    second = NAMES["secondSiteOnly"]
    shared = NAMES["sharedClassHR"]
    return [
        # cluster-object
        ("cluster-object", "cluster RBAC (test object)", KUSTOMIZE, "deny",
         obj("ClusterRoleBinding", "grant", None, OUT_K,
             roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "cluster-admin"},
             subjects=[{"kind": "ServiceAccount", "name": "default", "namespace": "production"}])),
        ("cluster-object", "a CRD", KUSTOMIZE, "deny",
         obj("CustomResourceDefinition", "things.example.com", None, OUT_K, spec={
             "group": "example.com", "scope": "Namespaced",
             "names": {"plural": "things", "singular": "thing", "kind": "Thing"},
             "versions": [{"name": "v1", "served": True, "storage": True,
                           "schema": {"openAPIV3Schema": {"type": "object"}}}]})),
        ("cluster-object", "a namespace", KUSTOMIZE, "deny", obj("Namespace", "new-ns", None, OUT_K)),
        ("cluster-object", "a volume with a host path", KUSTOMIZE, "deny",
         obj("PersistentVolume", "host", None, OUT_K, spec={
             "capacity": {"storage": "1Gi"}, "accessModes": ["ReadWriteOnce"], "hostPath": {"path": "/"}})),
        ("cluster-object", "a ClusterRole of a HelmRelease outside the class", HELM, "deny",
         obj("ClusterRole", "chart-role", None, OUT_H, rules=[])),
        ("cluster-object", "a ClusterRole of a child of the class", KUSTOMIZE, "allow",
         obj("ClusterRole", "class-role", None, CLASS_K, rules=[])),
        ("cluster-object", "a ClusterRole from a person", None, "allow",
         obj("ClusterRole", "person-role", None, OUT_K, rules=[])),
        # class-namespace
        ("class-namespace", "a ConfigMap in ai (test object)", KUSTOMIZE, "deny",
         obj("ConfigMap", "x", "ai", OUT_K, data={"a": "b"})),
        ("class-namespace", "a Secret in flux-system", KUSTOMIZE, "deny",
         obj("Secret", "x", "flux-system", OUT_K, stringData={"a": "b"})),
        ("class-namespace", "a ConfigMap of a HelmRelease outside the class in kube-system", HELM, "deny",
         obj("ConfigMap", "x", "kube-system", OUT_H, data={"a": "b"})),
        ("class-namespace", "a ConfigMap in its own namespace", KUSTOMIZE, "allow",
         obj("ConfigMap", "x", "production", OUT_K, data={"a": "b"})),
        ("class-namespace", "a ConfigMap of a child of the class in ai", KUSTOMIZE, "allow",
         obj("ConfigMap", "x", "ai", CLASS_K, data={"a": "b"})),
        ("class-namespace", "the HelmChart of a HelmRelease outside the class, in flux-system", HELM, "allow",
         obj("HelmChart", "production-mealie", "flux-system", OUT_H, spec={
             "chart": "app-template", "interval": "30m",
             "sourceRef": {"kind": "HelmRepository", "name": "bjw-s"}})),
        # rbac
        ("rbac", "a Role", KUSTOMIZE, "deny",
         obj("Role", "r", "production", OUT_K, rules=[{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"]}])),
        ("rbac", "a RoleBinding", KUSTOMIZE, "deny",
         obj("RoleBinding", "rb", "production", OUT_K,
             roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "admin"},
             subjects=[{"kind": "ServiceAccount", "name": "default", "namespace": "production"}])),
        ("rbac", "a Role of a child of the class", KUSTOMIZE, "allow", obj("Role", "r", "production", CLASS_K, rules=[])),
        # flux-kinds
        ("flux-kinds", "a Kustomization of a child outside the class", KUSTOMIZE, "deny",
         kustomization("nested", APP_PATH, OUT_K)),
        ("flux-kinds", "an ImageUpdateAutomation", KUSTOMIZE, "deny",
         obj("ImageUpdateAutomation", "push", "production", OUT_K, spec={
             "interval": "30m", "sourceRef": {"kind": "GitRepository", "name": "flux-system", "namespace": "flux-system"},
             "git": {"commit": {"author": {"email": "a@example.invalid", "name": "a"}}}})),
        # helmrelease-spec
        ("helmrelease-spec", "a service account", KUSTOMIZE, "deny",
         helmrelease(NAMES["outsideHR"], OUT_K, serviceAccountName="helm-controller")),
        ("helmrelease-spec", "a kubeconfig", KUSTOMIZE, "deny",
         helmrelease(NAMES["outsideHR"], OUT_K, kubeConfig={"secretRef": {"name": "other"}})),
        ("helmrelease-spec", "a target namespace of the class", KUSTOMIZE, "deny",
         helmrelease(NAMES["outsideHR"], OUT_K, targetNamespace="ai")),
        ("helmrelease-spec", "a storage namespace of the class", KUSTOMIZE, "deny",
         helmrelease(NAMES["outsideHR"], OUT_K, storageNamespace="kube-system")),
        ("helmrelease-spec", "hooks with no owner labels", KUSTOMIZE, "deny",
         helmrelease(NAMES["outsideHR"], OUT_K, postRenderStrategy="nohooks")),
        ("helmrelease-spec", "a plain HelmRelease", KUSTOMIZE, "allow", helmrelease(NAMES["outsideHR"], OUT_K)),
        # kustomization-spec: from the parent of the class, which also builds
        # the ks.yaml of the children outside the class.
        ("kustomization-spec", "a Kustomization with a class path (test object)", KUSTOMIZE, "deny",
         kustomization("new-app", "./kubernetes/apps/ai/agents", PARENT, postBuild=SUBST)),
        ("kustomization-spec", "a path above the class", KUSTOMIZE, "deny",
         kustomization("new-app", "./kubernetes", PARENT)),
        ("kustomization-spec", "the root", KUSTOMIZE, "deny", kustomization("new-app", "./", PARENT)),
        ("kustomization-spec", "a path that goes up", KUSTOMIZE, "deny",
         kustomization("new-app", "./kubernetes/apps/production/../ai", PARENT)),
        ("kustomization-spec", "a trailing slash above the class", KUSTOMIZE, "deny",
         kustomization("new-app", "kubernetes/apps/", PARENT)),
        ("kustomization-spec", "a service account", KUSTOMIZE, "deny",
         kustomization("new-app", APP_PATH, PARENT, serviceAccountName="helm-controller")),
        ("kustomization-spec", "a kubeconfig", KUSTOMIZE, "deny",
         kustomization("new-app", APP_PATH, PARENT, kubeConfig={"secretRef": {"name": "other"}})),
        ("kustomization-spec", "the owner label of the class in commonMetadata", KUSTOMIZE, "deny",
         kustomization("new-app", APP_PATH, PARENT, commonMetadata={
             "labels": {"kustomize.toolkit.fluxcd.io/name": NAMES["classK"].split("/")[1]}})),
        ("kustomization-spec", "substitution from another Secret of flux-system", KUSTOMIZE, "deny",
         kustomization("new-app", APP_PATH, PARENT, postBuild={"substituteFrom": [{"kind": "Secret", "name": "flux-system"}]})),
        ("kustomization-spec", "a Kustomization of an app", KUSTOMIZE, "allow",
         kustomization("new-app", APP_PATH, PARENT, postBuild=SUBST,
                       decryption={"provider": "sops", "secretRef": {"name": "sops-age"}})),
        # kustomization-class
        ("kustomization-class", "a name of the class on the other site, with a path of the class", KUSTOMIZE, "deny",
         kustomization(second.split("/")[1], "./kubernetes/apps/security/new", PARENT)),
        ("kustomization-class", "a name of the class with a path outside it", KUSTOMIZE, "deny",
         kustomization(NAMES["classK"].split("/")[1], APP_PATH, PARENT)),
        ("kustomization-class", "a name of the class from the right parent", KUSTOMIZE, "allow",
         kustomization(second.split("/")[1], "./kubernetes/apps/security/new", k_labels("flux-system/cluster-apps-wsl-02"))),
        # helmrelease-class
        ("helmrelease-class", "a name of the class in a shared namespace, from a child outside the class", KUSTOMIZE, "deny",
         helmrelease(shared, OUT_K)),
        ("helmrelease-class", "the same from a child of the class", KUSTOMIZE, "allow", helmrelease(shared, CLASS_K)),
        # parent-objects
        ("parent-objects", "a new object from the parent that reads the aggregators", KUSTOMIZE, "deny",
         obj("NetworkPolicy", "allow-all", "agents-main", PARENT, spec={
             "podSelector": {}, "ingress": [{}], "policyTypes": ["Ingress"]})),
        ("parent-objects", "a ClusterRoleBinding from that parent", KUSTOMIZE, "deny",
         obj("ClusterRoleBinding", "grant", None, PARENT,
             roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "cluster-admin"},
             subjects=[{"kind": "ServiceAccount", "name": "default", "namespace": "production"}])),
        ("parent-objects", "a namespace of its build", KUSTOMIZE, "allow",
         {"update": obj("Namespace", "production", None, PARENT)}),
        # takeover
        ("takeover", "a change of an object of a child of the class", KUSTOMIZE, "deny",
         {"update": dict(TAKEN, metadata=dict(TAKEN["metadata"], labels=OUT_K))}),
        ("takeover", "a change of it by its own child", KUSTOMIZE, "allow",
         {"update": dict(TAKEN, data={"a": "c"})}),
        # the work of the controllers
        ("work", "an event of the helm controller in kube-system", HELM, "allow",
         obj("Event", "e.1", "kube-system", None, involvedObject={"kind": "HelmRelease", "name": "x", "namespace": "kube-system"},
             reason="Test", message="m", type="Normal")),
        ("work", "a lease of the kustomize controller in flux-system", KUSTOMIZE, "allow",
         obj("Lease", "kustomize-controller-leader-election", "flux-system", None, spec={})),
        ("work", "the release storage of the helm controller in kube-system", HELM, "allow",
         obj("Secret", "sh.helm.release.v1.cilium.v2", "kube-system", {"owner": "helm", "name": "cilium"},
             type="helm.sh/release.v1", stringData={"release": "x"})),
    ] + privileged_cases()


def apply_policy(actions, drop=None):
    """Applies the policy of git, less the validation of the rule drop, and
    its binding with these actions."""
    for doc in POLICY:
        doc = copy.deepcopy(doc)
        if doc["kind"] == "ValidatingAdmissionPolicy" and drop:
            kept = [v for v in doc["spec"]["validations"] if f"flux-child-scope/{drop}:" not in v["messageExpression"]]
            assert len(kept) == len(doc["spec"]["validations"]) - 1, drop
            doc["spec"]["validations"] = kept
        if doc["kind"] == "ValidatingAdmissionPolicyBinding":
            doc["spec"]["validationActions"] = actions
        status, _, _ = call("PATCH", f"{ADMISSION}/{KINDS[doc['kind']][0]}/{doc['metadata']['name']}?fieldManager=test&force=true",
                            doc, content_type="application/apply-patch+yaml")
        assert status in (200, 201), (doc["kind"], status)


# The object that the cases of the rule takeover change: a child of the
# class applies it (setUpClass makes it).
TAKEN = obj("ConfigMap", "taken", "production", CLASS_K, data={"a": "b"})


def send(case):
    """A case is a dry run of a create, or of an update of an object that
    setUpClass makes."""
    _, _, user, _, doc = case
    if "update" in doc:
        return replace(doc["update"], user)
    return create(doc, user)


def refused(result):
    status, body, _ = result
    return status == 403 and "flux-child-scope/" in body.get("message", "")


def outcome(case):
    result = send(case)
    status, body, warnings = result
    if refused(result):
        return "deny", body.get("message", "")
    if status in (200, 201):
        return "allow", warnings
    return f"error {status}", body.get("message", body)


class FluxAdmission(unittest.TestCase):
    maxDiff = None
    @classmethod
    def setUpClass(cls):
        wait_for("API server", lambda: call("GET", "/readyz")[0] == 200)
        for crd in CRDS:
            status, _, _ = create(crd, dry=False)
            assert status in (201, 409), (crd["metadata"]["name"], status)
        for crd in CRDS:
            wait_for(f"CRD {crd['metadata']['name']}", lambda c=crd: any(
                x["type"] == "Established" and x["status"] == "True"
                for x in call("GET", f"/apis/apiextensions.k8s.io/v1/customresourcedefinitions/{c['metadata']['name']}")[1]
                .get("status", {}).get("conditions", [])))
        namespaces = {"ai", "flux-system", "production", "home-automation", "media", "selfhosted", "kube-system",
                      "agents-main", "databases"}
        namespaces |= {e["doc"]["metadata"]["namespace"] for e in GIT_HELMRELEASES}
        namespaces |= {e["doc"]["metadata"].get("namespace", "flux-system") for e in GIT_KUSTOMIZATIONS}
        for namespace in sorted(namespaces):
            status, _, _ = create(obj("Namespace", namespace), dry=False)
            assert status in (201, 409), (namespace, status)
        # As in the clusters: both controllers are bound to cluster-admin.
        for user in (KUSTOMIZE, HELM):
            name = user.rsplit(":", 1)[1]
            status, _, _ = create(obj("ClusterRoleBinding", f"{name}-admin",
                roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "cluster-admin"},
                subjects=[{"kind": "ServiceAccount", "name": name, "namespace": "flux-system"}]), dry=False)
            assert status in (201, 409), (name, status)
        status, _, _ = create(TAKEN, dry=False)
        assert status in (201, 409), status
        apply_policy(["Deny"])
        probe = ("cluster-object", "probe", KUSTOMIZE, "deny", obj("ClusterRole", "probe", None, OUT_K, rules=[]))
        try:
            wait_for("the policy in force", lambda: refused(send(probe)))
        except AssertionError:
            print("probe:", outcome(probe))
            print("policy:", json.dumps(call("GET", f"{ADMISSION}/validatingadmissionpolicies/flux-child-scope")[1].get("status")))
            raise

    def test_1_type_check(self):
        status, body, _ = call("GET", f"{ADMISSION}/validatingadmissionpolicies/flux-child-scope")
        self.assertEqual(status, 200)
        wait_for("a type check", lambda: "typeChecking" in call(
            "GET", f"{ADMISSION}/validatingadmissionpolicies/flux-child-scope")[1].get("status", {}))
        status, body, _ = call("GET", f"{ADMISSION}/validatingadmissionpolicies/flux-child-scope")
        self.assertEqual(body["status"]["typeChecking"].get("expressionWarnings", []), [])

    def test_2_each_case(self):
        failures = []
        for case in cases():
            rule, what, _, want, _ = case
            got, detail = outcome(case)
            if got != want:
                failures.append(f"{rule}: {what}: want {want}, got {got}: {detail}")
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_3_a_finalizer(self):
        # A finalizer of the controller on a Kustomization of the class that
        # a person made, with no owner labels (a bootstrap).
        root = kustomization("flux-system", "./kubernetes/clusters/wsl", None)
        status, _, _ = create(root, dry=False)
        self.assertIn(status, (201, 409))
        current = call("GET", "/apis/" + VERSIONS["Kustomization"] + "/namespaces/flux-system/kustomizations/flux-system")[1]
        current["metadata"]["finalizers"] = ["finalizers.fluxcd.io"]
        self.assertEqual(replace(current, KUSTOMIZE)[0], 200)
        # The same request with a new path is no finalizer.
        current["spec"]["path"] = APP_PATH
        self.assertTrue(refused(replace(current, KUSTOMIZE)), "a new spec with no owner label")
        self.assertEqual(call("DELETE", "/apis/" + VERSIONS["Kustomization"]
                              + "/namespaces/flux-system/kustomizations/flux-system")[0], 200)

    def test_4_git(self):
        failures = []
        for entry in GIT_KUSTOMIZATIONS:
            doc = copy.deepcopy(entry["doc"])
            doc["metadata"].setdefault("namespace", "flux-system")
            doc["metadata"]["labels"] = dict(doc["metadata"].get("labels", {}), **k_labels(entry["parent"]))
            status, body, _ = create(doc, KUSTOMIZE)
            if status != 201:
                failures.append(f"{entry['site']} Kustomization {doc['metadata']['name']}: {status} {body.get('message')}")
        for entry in GIT_HELMRELEASES:
            doc = copy.deepcopy(entry["doc"])
            doc["metadata"]["labels"] = dict(doc["metadata"].get("labels", {}), **k_labels(entry["applier"]))
            status, body, _ = create(doc, KUSTOMIZE)
            if status != 201:
                failures.append(f"{entry['site']} HelmRelease {doc['metadata']['name']}: {status} {body.get('message')}")
        self.assertEqual(failures, [], "\n" + "\n".join(failures))
        self.assertGreater(len(GIT_KUSTOMIZATIONS), 200)
        self.assertGreater(len(GIT_HELMRELEASES), 100)

    def test_5_admission_objects_are_out_of_reach(self):
        # The API server runs no admission policy on an admission object, so
        # this policy can not refuse one (docs/operations/flux-admission.md).
        # If a later version starts to, this case fails and the docs change.
        status, _, _ = create(obj("ValidatingAdmissionPolicy", "other", None, OUT_K, spec={
            "failurePolicy": "Fail",
            "matchConstraints": {"resourceRules": [{"apiGroups": [""], "apiVersions": ["v1"],
                                                    "operations": ["CREATE"], "resources": ["pods"]}]},
            "validations": [{"expression": "true"}]}), KUSTOMIZE)
        self.assertEqual(status, 201)

    def test_6_audit_and_warn(self):
        actions = next(d for d in POLICY if d["kind"] == "ValidatingAdmissionPolicyBinding")["spec"]["validationActions"]
        self.assertEqual(sorted(actions), ["Audit", "Warn"])
        try:
            apply_policy(actions)
            deny = next(c for c in cases() if c[3] == "deny")
            wait_for("the binding of git", lambda: send(deny)[0] in (200, 201))
            for case in cases():
                status, _, warnings = send(case)
                warned = any("flux-child-scope/" in w for w in warnings)
                self.assertEqual((status in (200, 201), warned), (True, case[3] == "deny"), case[:2])
        finally:
            apply_policy(["Deny"])

    def test_7_each_rule_can_fail(self):
        rules = sorted({c[0] for c in cases() if c[3] == "deny"})
        self.assertEqual(len(rules), 11)
        try:
            for rule in rules:
                apply_policy(["Deny"])
                denied = [c for c in cases() if c[0] == rule and c[3] == "deny"]
                wait_for(f"{rule} in force", lambda d=denied: refused(send(d[0])), 30)
                apply_policy(["Deny"], drop=rule)
                # With the rule off, at least one of its cases passes: so its
                # test fails, and the rule is what refuses it.
                wait_for(f"{rule} off", lambda d=denied: any(not refused(send(c)) for c in d), 30)
        finally:
            apply_policy(["Deny"])

    def test_8_the_count_has_refusals_only(self):
        # apiserver_validating_admission_policy_check_total, the count that
        # the clusters keep for this policy, adds one for each action of a
        # validation that refuses (one request can fail two rules). A check
        # that allows adds nothing, from either controller. So the read
        # before Deny needs refusals on purpose: the live check
        # (bin/flux-admission-check) sends test objects as both controllers,
        # and each adds at least one to "audit" on that site.
        def counts():
            text = call("GET", "/metrics")[1]["text"]
            out = {}
            for m in re.finditer(r'^apiserver_validating_admission_policy_check_total\{([^}]*)\} ([0-9.e+]+)$',
                                 text, re.MULTILINE):
                labels = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
                if labels.get("policy") == "flux-child-scope":
                    action = labels["enforcement_action"]
                    out[action] = out.get(action, 0) + float(m.group(2))
            return out

        allowed = [c for c in cases() if c[3] == "allow"]
        denied = [next(c for c in cases() if c[3] == "deny" and c[2] == user) for user in (KUSTOMIZE, HELM)]
        self.assertEqual({c[2] for c in allowed} >= {KUSTOMIZE, HELM}, True)
        try:
            apply_policy(["Audit", "Warn"])
            wait_for("audit and warn", lambda: send(denied[0])[0] in (200, 201))
            before = counts()
            for case in allowed:
                send(case)
            self.assertEqual(counts(), before, "an allowed check added to the count")
            for case in denied:
                send(case)
            after = counts()
            rise = {k: after[k] - before.get(k, 0) for k in after if after[k] != before.get(k, 0)}
            self.assertEqual(set(rise), {"audit", "warn"}, rise)
            self.assertTrue(rise["audit"] == rise["warn"] >= 2, rise)
            apply_policy(["Deny", "Audit"])
            wait_for("deny", lambda: refused(send(denied[0])))
            before = counts()
            send(denied[1])
            after = counts()
            rise = {k: after[k] - before.get(k, 0) for k in after if after[k] != before.get(k, 0)}
            self.assertEqual(set(rise), {"deny", "audit"}, rise)
            self.assertTrue(rise["deny"] == rise["audit"] >= 1, rise)
        finally:
            apply_policy(["Deny"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
