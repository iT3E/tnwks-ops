"""The admission policy of the Flux children, on a throwaway API server (run.sh).

The API server is the version of the clusters. The test talks to it with
the client certificate of its admin, over HTTPS, with the standard library
only. A request of a Flux controller is a request of the admin that
impersonates the service account of that controller, with the owner labels
that the controller sets. Each request that a case makes is a dry run, so no
object is made, except the few that the cases of an update need.

1. The policy of git with its binding (Deny and Audit): each case of each rule, a child
   outside the class refused, and the same object from a child of the class
   or from a person let through.
2. Each Flux Kustomization and HelmRelease of both sites in git, from the
   Kustomization that applies it: none refused.
3. The binding of git (Deny and Audit) refuses each deny case; Audit and
   Warn, the action of the break-glass path, lets it pass with a warning.
4. Each rule switched off in turn: at least one of its own cases passes, so
   each case can fail.

The accounts of the children outside the class (flux-child-<name> in
flux-system, flux-release-<name> in the namespace of a HelmRelease) are
bound to cluster-admin here, so that the policy, not RBAC, is what refuses
their requests. The policy flux-child-pods (../app/pods.yaml) is applied
with flux-child-scope.
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
# The actions of the binding of git: Deny, and Audit, which keeps the
# annotation with the name of the object in the audit stream.
GIT_ACTIONS = ["Deny", "Audit"]
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
    "AutoscalingRunnerSet": ("autoscalingrunnersets", True),
    "EphemeralRunner": ("ephemeralrunners", True),
    "Sandbox": ("sandboxes", True),
    "SandboxTemplate": ("sandboxtemplates", True),
    "Cluster": ("clusters", True),
    "Pooler": ("poolers", True),
    "ScheduledBackup": ("scheduledbackups", True),
    "ObjectStore": ("objectstores", True),
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
                            {"kind": "Secret", "name": "cluster-secrets-apps"}]}
APP_PATH = "./kubernetes/apps/production/new-app/app"
CLASS_KEY = {"provider": "sops", "secretRef": {"name": "sops-age-class"}}
APPS_KEY = {"provider": "sops", "secretRef": {"name": "sops-age-apps"}}


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
        ("helmrelease-spec", "a release name", KUSTOMIZE, "deny",
         helmrelease(NAMES["outsideHR"], OUT_K, releaseName="frigate")),
        ("helmrelease-spec", "a target namespace outside the class", KUSTOMIZE, "deny",
         helmrelease(NAMES["outsideHR"], OUT_K, targetNamespace="production")),
        ("helmrelease-spec", "a storage namespace outside the class", KUSTOMIZE, "deny",
         helmrelease(NAMES["outsideHR"], OUT_K, storageNamespace="production")),
        ("helmrelease-spec", "a release name from a HelmRelease outside the class", HELM, "deny",
         helmrelease(NAMES["outsideHR"], OUT_H, releaseName="frigate")),
        ("helmrelease-spec", "a release name of a child of the class", KUSTOMIZE, "allow",
         helmrelease(NAMES["classHR"], CLASS_K, releaseName="frigate")),
        ("helmrelease-spec", "release fields from a person", None, "allow",
         helmrelease(NAMES["outsideHR"], OUT_K, releaseName="frigate", targetNamespace="production",
                     storageNamespace="production")),
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
        ("kustomization-spec", "substitution from the full cluster-secrets", KUSTOMIZE, "deny",
         kustomization("new-app", APP_PATH, PARENT, postBuild={"substituteFrom": [{"kind": "Secret", "name": "cluster-secrets"}]})),
        # The lockdown (docs/operations/flux-lockdown.md): a child outside
        # the class may not run as the controller, or decrypt with the class
        # key, which the controller reads as itself whatever the account of
        # the child.
        ("kustomization-spec", "the account of the controller", KUSTOMIZE, "deny",
         kustomization("new-app", APP_PATH, PARENT, postBuild=SUBST, serviceAccountName="kustomize-controller")),
        ("kustomization-spec", "the Secret of the class key", KUSTOMIZE, "deny",
         kustomization("new-app", APP_PATH, PARENT, postBuild=SUBST, decryption=CLASS_KEY)),
        ("kustomization-spec", "the Secret of the apps key", KUSTOMIZE, "allow",
         kustomization("new-app", APP_PATH, PARENT, postBuild=SUBST, decryption=APPS_KEY)),
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
        ("kustomization-class", "a name of the class, as the controller, with the Secret of the class key", KUSTOMIZE,
         "allow", kustomization(second.split("/")[1], "./kubernetes/apps/security/new",
                                k_labels("flux-system/cluster-apps-wsl-02"), serviceAccountName="kustomize-controller",
                                decryption=CLASS_KEY)),
        ("kustomization-class", "the parent of the apps from the root, as the controller, with the class key", KUSTOMIZE,
         "allow", kustomization("cluster-apps", "./kubernetes/clusters/wsl/apps", k_labels("flux-system/flux-system"),
                                serviceAccountName="kustomize-controller", decryption=CLASS_KEY)),
        # helmrelease-class
        ("helmrelease-class", "a name of the class in a shared namespace, from a child outside the class", KUSTOMIZE, "deny",
         helmrelease(shared, OUT_K)),
        ("helmrelease-class", "the same from a child of the class", KUSTOMIZE, "allow", helmrelease(shared, CLASS_K)),
        ("helmrelease-class", "a HelmRelease of the class with an account of its namespace", KUSTOMIZE, "allow",
         helmrelease(NAMES["classHR"], CLASS_K, serviceAccountName="flux-apply")),
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
        # secret-types: the Secrets that Helm and service accounts use
        ("secret-types", "the storage of a Helm release", KUSTOMIZE, "deny",
         obj("Secret", "sh.helm.release.v1.frigate.v9", "production", OUT_K,
             type="helm.sh/release.v1", stringData={"release": "x"})),
        ("secret-types", "the storage of a Helm release from a chart outside the class", HELM, "deny",
         obj("Secret", "sh.helm.release.v1.other.v1", "production", OUT_H,
             type="helm.sh/release.v1", stringData={"release": "x"})),
        ("secret-types", "a token of an account", KUSTOMIZE, "deny",
         obj("Secret", "token", "production", OUT_K, type="kubernetes.io/service-account-token",
             metadata={"name": "token", "namespace": "production", "labels": OUT_K,
                       "annotations": {"kubernetes.io/service-account.name": "default"}})),
        ("secret-types", "a token of an account from a chart outside the class", HELM, "deny",
         obj("Secret", "token", "production", OUT_H, type="kubernetes.io/service-account-token",
             metadata={"name": "token", "namespace": "production", "labels": OUT_H,
                       "annotations": {"kubernetes.io/service-account.name": "default"}})),
        ("secret-types", "a plain Secret in its own namespace", KUSTOMIZE, "allow",
         obj("Secret", "plain", "production", OUT_K, type="Opaque", stringData={"a": "b"})),
        ("secret-types", "a token of an account from a child of the class", KUSTOMIZE, "allow",
         obj("Secret", "token", "production", CLASS_K, type="kubernetes.io/service-account-token",
             metadata={"name": "token", "namespace": "production", "labels": CLASS_K,
                       "annotations": {"kubernetes.io/service-account.name": "default"}})),
        ("secret-types", "the storage of a Helm release from a person", None, "allow",
         obj("Secret", "sh.helm.release.v1.person.v1", "production", OUT_K,
             type="helm.sh/release.v1", stringData={"release": "x"})),
        # the work of the controllers
        ("work", "an event of the helm controller in kube-system", HELM, "allow",
         obj("Event", "e.1", "kube-system", None, involvedObject={"kind": "HelmRelease", "name": "x", "namespace": "kube-system"},
             reason="Test", message="m", type="Normal")),
        ("work", "a lease of the kustomize controller in flux-system", KUSTOMIZE, "allow",
         obj("Lease", "kustomize-controller-leader-election", "flux-system", None, spec={})),
        ("work", "the release storage of a HelmRelease outside the class, by the helm controller", HELM, "allow",
         obj("Secret", "sh.helm.release.v1.mealie.v3", "production", {"owner": "helm", "name": "mealie"},
             type="helm.sh/release.v1", stringData={"release": "x"})),
        ("work", "the release storage of the helm controller in kube-system", HELM, "allow",
         obj("Secret", "sh.helm.release.v1.cilium.v2", "kube-system", {"owner": "helm", "name": "cilium"},
             type="helm.sh/release.v1", stringData={"release": "x"})),
    ] + privileged_cases() + operator_cases() + account_cases()


# The accounts of the children outside the class, as the API server names
# them: the Kustomization flux-system/mealie and the HelmRelease
# production/mealie, another Kustomization, and an account with the name of
# a Kustomization of the class (no such account is made: it is still
# outside the class).
CHILD = "system:serviceaccount:flux-system:flux-child-" + NAMES["outsideK"].split("/")[1]
RELEASE = "system:serviceaccount:{}:flux-release-{}".format(*NAMES["outsideHR"].split("/"))
CLASS_NAMED = "system:serviceaccount:flux-system:flux-child-" + NAMES["classK"].split("/")[1]
ACCOUNTS = (CHILD, RELEASE, CLASS_NAMED)


def storage(name, namespace, release):
    """A Secret of the storage of a Helm release, as Helm writes it."""
    return obj("Secret", f"sh.helm.release.v1.{name}.v1", namespace,
               {"owner": "helm", "name": release, "status": "deployed", "version": "1"},
               type="helm.sh/release.v1", stringData={"release": "x"})


def account_cases():
    """The requests of the accounts of the children: each is a request of
    a child outside the class, whatever the labels of its object."""
    own_hr = NAMES["outsideHR"].split("/")[1]
    namespace = NAMES["outsideHR"].split("/")[0]
    shared_ns, shared_name = NAMES["sharedClassHR"].split("/")
    plain = obj("ConfigMap", "plain", namespace, None, data={"a": "b"})
    token = obj("Secret", "token", namespace, None, type="kubernetes.io/service-account-token",
                metadata={"name": "token", "namespace": namespace,
                          "annotations": {"kubernetes.io/service-account.name": "default"}})
    cases = []
    for user, by in ((CHILD, "the account of a Kustomization"), (RELEASE, "the account of a HelmRelease")):
        cases += [
            ("cluster-object", f"a ClusterRole from {by}", user, "deny", obj("ClusterRole", "r", None, None, rules=[])),
            ("class-namespace", f"an object in ai from {by}", user, "deny",
             obj("ConfigMap", "c", "ai", None, data={"a": "b"})),
            ("rbac", f"a Role from {by}", user, "deny", obj("Role", "r", namespace, None, rules=[])),
            ("flux-kinds", f"a Kustomization from {by}", user, "deny", kustomization("nested", APP_PATH, None)),
            ("privileged-pod", f"a privileged pod from {by}", user, "deny",
             workload("Deployment", "p", namespace, None, pod_spec(container(privileged=True)))),
            ("operator-pods", f"a Sandbox from {by}", user, "deny",
             obj("Sandbox", "s", namespace, None, spec={"podTemplate": {"spec": pod_spec()}})),
            ("takeover", f"a change of an object of a child of the class from {by}", user, "deny",
             {"update": dict(TAKEN, data={"a": "d"})}),
            ("secret-types", f"a token of an account from {by}", user, "deny", token),
            ("secret-types", f"a plain object from {by}", user, "allow", plain),
            ("privileged-pod", f"a plain pod from {by}", user, "allow",
             workload("Deployment", "p", namespace, None, pod_spec())),
        ]
    cases += [
        # The labels of an object do not make the request of an account one of the class.
        ("class-namespace", "an object in ai with the labels of the class, from an account with a class name",
         CLASS_NAMED, "deny", obj("ConfigMap", "c", "ai", CLASS_K, data={"a": "b"})),
        ("helmrelease-class", "a HelmRelease of the class from an account with a class name", CLASS_NAMED, "deny",
         helmrelease(NAMES["sharedClassHR"], CLASS_K)),
        ("helmrelease-class", "a HelmRelease of the class from kustomize-controller", KUSTOMIZE, "allow",
         helmrelease(NAMES["sharedClassHR"], CLASS_K)),
        # The storage of a Helm release: only the account of a HelmRelease,
        # for its own release, in its own namespace.
        ("secret-types", "the storage of its own release from the account of a HelmRelease", RELEASE, "allow",
         storage(own_hr, namespace, own_hr)),
        ("secret-types", "the storage of another release from the account of a HelmRelease", RELEASE, "deny",
         storage("other", namespace, "other")),
        ("secret-types", "the storage of a release of the class from the account of a HelmRelease", RELEASE, "deny",
         storage(shared_name, shared_ns, shared_name)),
        ("secret-types", "the storage of its release in another namespace from its account", RELEASE, "deny",
         storage(own_hr, "media", own_hr)),
        ("secret-types", "the storage of its release with the name of another one, from its account", RELEASE,
         "deny", dict(storage(own_hr, namespace, own_hr), metadata=dict(
             storage(own_hr, namespace, own_hr)["metadata"], name="sh.helm.release.v1.other.v1"))),
        ("secret-types", "the storage of a release with a dot after its name, from its account", RELEASE,
         "deny", dict(storage(own_hr, namespace, own_hr), metadata=dict(
             storage(own_hr, namespace, own_hr)["metadata"], name=f"sh.helm.release.v1.{own_hr}.vbar.v1"))),
        ("secret-types", "the storage of its release with no version, from its account", RELEASE,
         "deny", dict(storage(own_hr, namespace, own_hr), metadata=dict(
             storage(own_hr, namespace, own_hr)["metadata"], name=f"sh.helm.release.v1.{own_hr}.v"))),
        ("secret-types", "the storage of its release at version 12, from its account", RELEASE,
         "allow", dict(storage(own_hr, namespace, own_hr), metadata=dict(
             storage(own_hr, namespace, own_hr)["metadata"], name=f"sh.helm.release.v1.{own_hr}.v12"))),
        ("secret-types", "the storage of a release from the account of a Kustomization", CHILD, "deny",
         storage(own_hr, namespace, own_hr)),
        # The own account of a child, and no other.
        ("helmrelease-spec", "its own account, from the account of a Kustomization", CHILD, "allow",
         helmrelease(NAMES["outsideHR"], None, serviceAccountName=f"flux-release-{own_hr}")),
        ("helmrelease-spec", "its own account, from kustomize-controller", KUSTOMIZE, "allow",
         helmrelease(NAMES["outsideHR"], OUT_K, serviceAccountName=f"flux-release-{own_hr}")),
        ("helmrelease-spec", "the account of another HelmRelease", KUSTOMIZE, "deny",
         helmrelease(NAMES["outsideHR"], OUT_K, serviceAccountName="flux-release-other")),
        ("helmrelease-spec", "the account of another HelmRelease, from the account of a Kustomization", CHILD,
         "deny", helmrelease(NAMES["outsideHR"], None, serviceAccountName="flux-release-other")),
        ("kustomization-spec", "its own account", KUSTOMIZE, "allow",
         kustomization("new-app", APP_PATH, PARENT, serviceAccountName="flux-child-new-app")),
        ("kustomization-spec", "the account of another child", KUSTOMIZE, "deny",
         kustomization("new-app", APP_PATH, PARENT, serviceAccountName=CHILD.rsplit(":", 1)[1])),
        ("kustomization-spec", "the account of a HelmRelease", KUSTOMIZE, "deny",
         kustomization("new-app", APP_PATH, PARENT, serviceAccountName="flux-release-new-app")),
        # No pod of any user runs as such an account (flux-child-pods).
        ("pod-account", "a pod of a person as the account of a Kustomization", None, "deny",
         obj("Pod", "p", namespace, None, spec=pod_spec(serviceAccountName=CHILD.rsplit(":", 1)[1]))),
        ("pod-account", "a pod of the class as the account of a HelmRelease", KUSTOMIZE, "deny",
         obj("Pod", "p", namespace, CLASS_K, spec=pod_spec(serviceAccountName=RELEASE.rsplit(":", 1)[1]))),
        ("pod-account", "a pod with the old field of the account", None, "deny",
         obj("Pod", "p", namespace, None, spec=pod_spec(serviceAccount=RELEASE.rsplit(":", 1)[1]))),
        ("pod-account", "a pod of a person as another account", None, "allow",
         obj("Pod", "p", namespace, None, spec=pod_spec(serviceAccountName="app"))),
    ]
    return cases


def operator_cases():
    """The kinds from which an operator makes a pod of its own template: none
    from outside the class, in a namespace outside it; each from the class.
    The whole group of the database operator and of its backup plugin is of
    the class (D149): the database folders of the apps are in the class, and
    databases is a namespace of the class."""
    template = {"spec": {"containers": [{"name": "app", "image": "registry.invalid/app:1"}]}}
    cluster = {"instances": 1, "storage": {"size": "1Gi"}}
    out = [
        ("AutoscalingRunnerSet", {"template": template}),
        ("EphemeralRunner", {"spec": template["spec"]}),
        ("Sandbox", {"podTemplate": template}),
        ("SandboxTemplate", {"podTemplate": template}),
        ("Pooler", {"cluster": {"name": "app-pg"}, "instances": 1, "type": "rw", "template": template}),
        ("Cluster", cluster),
        ("ScheduledBackup", {"schedule": "0 0 0 * * *", "cluster": {"name": "app-pg"}}),
        ("ObjectStore", {"configuration": {"destinationPath": "s3://bucket.invalid/app"}}),
    ]
    cases = []
    for kind, spec in out:
        cases.append(("operator-pods", f"a {kind} outside the class", KUSTOMIZE, "deny",
                      obj(kind, "new-app", "production", OUT_K, spec=spec)))
    namespace = {"AutoscalingRunnerSet": "tools", "EphemeralRunner": "tools", "Pooler": "databases",
                 "Cluster": "databases", "ScheduledBackup": "databases", "ObjectStore": "databases"}
    for kind, spec in out:
        cases.append(("operator-pods", f"a {kind} of the class", KUSTOMIZE, "allow",
                      obj(kind, "new-app", namespace.get(kind, "agents-main"), CLASS_K, spec=spec)))
    # The database clusters of the apps: their namespace is of the class now.
    cases.append(("class-namespace", "a database cluster outside the class in databases", KUSTOMIZE, "deny",
                  obj("Cluster", "new-app-pg", "databases", OUT_K, spec=cluster)))
    return cases


def apply_policy(actions, drop=None, mutate=None, match=None):
    """Applies the policy of git, less the validation of the rule drop, and
    its binding with these actions. A mutant (old, new) changes the text of
    one expression; match replaces the expression of the match of
    flux-child-scope."""
    docs = copy.deepcopy(POLICY)
    policies = [d for d in docs if d["kind"] == "ValidatingAdmissionPolicy"]
    if drop:
        for doc in policies:
            doc["spec"]["validations"] = [v for v in doc["spec"]["validations"]
                                          if f"/{drop}: " not in v["messageExpression"]]
        dropped = sum(len(d["spec"]["validations"]) for d in POLICY if d["kind"] == "ValidatingAdmissionPolicy") - \
            sum(len(d["spec"]["validations"]) for d in policies)
        assert dropped == 1, drop
        for doc in policies:
            # A policy needs one validation: one that holds for each request.
            doc["spec"]["validations"] = doc["spec"]["validations"] or [{"expression": "true"}]
    if mutate:
        hits = [v for d in policies for v in d["spec"]["validations"] if mutate[0] in v["expression"]]
        assert len(hits) == 1, mutate
        hits[0]["expression"] = hits[0]["expression"].replace(mutate[0], mutate[1])
    if match:
        (scope,) = [d for d in policies if d["metadata"]["name"] == "flux-child-scope"]
        (condition,) = scope["spec"]["matchConditions"]
        condition["expression"] = match
    for doc in docs:
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


# The prefixes of the messages of the two policies.
MARKERS = ("flux-child-scope/", "flux-child-pods/")


def refused(result):
    status, body, _ = result
    return status == 403 and any(m in body.get("message", "") for m in MARKERS)


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
                      "agents-main", "databases", "tools"}
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
        # The accounts of the children: bound to cluster-admin here, so the
        # policy is what refuses their requests (on the clusters, the role
        # flux-child limits them first).
        for user in ACCOUNTS:
            name = "account-" + user.rsplit(":", 1)[1]
            status, _, _ = create(obj("ClusterRoleBinding", name,
                roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "cluster-admin"},
                subjects=[{"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": user}]), dry=False)
            assert status in (201, 409), (name, status)
        status, _, _ = create(TAKEN, dry=False)
        assert status in (201, 409), status
        apply_policy(GIT_ACTIONS)
        probe = ("cluster-object", "probe", KUSTOMIZE, "deny", obj("ClusterRole", "probe", None, OUT_K, rules=[]))
        try:
            wait_for("the policy in force", lambda: refused(send(probe)))
        except AssertionError:
            print("probe:", outcome(probe))
            print("policy:", json.dumps(call("GET", f"{ADMISSION}/validatingadmissionpolicies/flux-child-scope")[1].get("status")))
            raise

    def test_1_type_check(self):
        for name in ("flux-child-scope", "flux-child-pods"):
            status, body, _ = call("GET", f"{ADMISSION}/validatingadmissionpolicies/{name}")
            self.assertEqual(status, 200)
            wait_for(f"a type check of {name}", lambda n=name: "typeChecking" in call(
                "GET", f"{ADMISSION}/validatingadmissionpolicies/{n}")[1].get("status", {}))
            status, body, _ = call("GET", f"{ADMISSION}/validatingadmissionpolicies/{name}")
            self.assertEqual(body["status"]["typeChecking"].get("expressionWarnings", []), [], name)

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

    def test_6_the_binding_of_git(self):
        actions = next(d for d in POLICY if d["kind"] == "ValidatingAdmissionPolicyBinding")["spec"]["validationActions"]
        self.assertEqual(actions, GIT_ACTIONS)
        apply_policy(actions)
        for case in cases():
            self.assertEqual(refused(send(case)), case[3] == "deny", case[:2])
        # Audit and Warn, the action of the roll-out and of the break-glass
        # path, lets each case through and warns on the ones it would refuse.
        try:
            apply_policy(["Audit", "Warn"])
            deny = next(c for c in cases() if c[3] == "deny")
            wait_for("audit and warn", lambda: send(deny)[0] in (200, 201))
            for case in cases():
                status, _, warnings = send(case)
                warned = any(m in w for w in warnings for m in MARKERS)
                self.assertEqual((status in (200, 201), warned), (True, case[3] == "deny"), case[:2])
        finally:
            apply_policy(GIT_ACTIONS)

    def test_7_each_rule_can_fail(self):
        rules = sorted({c[0] for c in cases() if c[3] == "deny"})
        self.assertEqual(len(rules), 14)
        try:
            for rule in rules:
                apply_policy(GIT_ACTIONS)
                denied = [c for c in cases() if c[0] == rule and c[3] == "deny"]
                wait_for(f"{rule} in force", lambda d=denied: refused(send(d[0])), 30)
                apply_policy(GIT_ACTIONS, drop=rule)
                # With the rule off, at least one of its cases passes: so its
                # test fails, and the rule is what refuses it.
                wait_for(f"{rule} off", lambda d=denied: any(not refused(send(c)) for c in d), 30)
        finally:
            apply_policy(GIT_ACTIONS)

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

    def test_9_each_release_field_can_fail(self):
        # A mutant of helmrelease-spec without the check of one release
        # field lets the case of that field pass, so each check is what
        # refuses its case.
        fields = {"releaseName": "a release name", "targetNamespace": "a target namespace outside the class",
                  "storageNamespace": "a storage namespace outside the class"}
        try:
            for field, what in fields.items():
                case = next(c for c in cases() if c[0] == "helmrelease-spec" and c[1] == what)
                apply_policy(GIT_ACTIONS)
                wait_for(f"{field} in force", lambda c=case: refused(send(c)), 30)
                apply_policy(GIT_ACTIONS, mutate=(f"!has(object.spec.{field})", "true"))
                wait_for(f"{field} off", lambda c=case: not refused(send(c)), 30)
        finally:
            apply_policy(GIT_ACTIONS)

    def test_10_the_accounts_of_the_children(self):
        # A mutant whose match has the two controllers only: each refusal of
        # a request of an account goes, so the match is what refuses them.
        # A mutant with no own release storage: that case is refused, so the
        # exception is what lets helm-controller write it as the account.
        controllers = ("request.userInfo.username in ['system:serviceaccount:flux-system:kustomize-controller', "
                       "'system:serviceaccount:flux-system:helm-controller']")
        accounts = [c for c in cases() if c[2] in ACCOUNTS and c[3] == "deny" and c[0] != "pod-account"]
        self.assertGreater(len(accounts), 20)
        own = next(c for c in cases() if c[1].startswith("the storage of its own release"))
        try:
            apply_policy(GIT_ACTIONS, match=controllers)
            wait_for("the match of the controllers only", lambda: not refused(send(accounts[0])), 30)
            self.assertEqual([c[:2] for c in accounts if refused(send(c))], [])
            apply_policy(GIT_ACTIONS, mutate=("variables.releaseAccount && object.?type", "false && object.?type"))
            wait_for("no own release storage", lambda: refused(send(own)), 30)
            apply_policy(GIT_ACTIONS)
            wait_for("the policy of git", lambda: not refused(send(own)) and refused(send(accounts[0])), 30)
        finally:
            apply_policy(GIT_ACTIONS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
