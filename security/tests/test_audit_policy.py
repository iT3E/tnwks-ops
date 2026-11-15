"""The audit policy of each cluster (talos/<cluster>/patches/audit-policy*.yaml) keeps
what talos/README.md says it keeps. homelab-wsl has two files: the v1alpha1 form for its
live node, and the multi-document form for a new cluster. Each file has the same rules.

The API server takes the first rule that matches a request. `level_of` below does
the same for the fields that the policy uses, so a sample request gets the level
that the API server gives it."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FILES = sorted(ROOT.glob("talos/*/patches/audit-policy*.yaml"))

LEVELS = {"None", "Metadata", "Request", "RequestResponse"}
STAGES = {"RequestReceived", "ResponseStarted", "ResponseComplete", "Panic"}
RULE_KEYS = {"level", "users", "userGroups", "verbs", "resources", "namespaces", "nonResourceURLs"}
# Keys that level_of does not read. A policy that starts to use one needs it there first.
NOT_READ = {"namespaces"}

KUBELET = ("system:node:cp-1", ["system:nodes", "system:authenticated"])
ACCOUNT = ("system:serviceaccount:flux-system:kustomize-controller",
           ["system:serviceaccounts", "system:serviceaccounts:flux-system", "system:authenticated"])
CONTROLLERS = ("system:kube-controller-manager", ["system:authenticated"])
SCHEDULER = ("system:kube-scheduler", ["system:authenticated"])
API_SERVER = ("system:apiserver", ["system:masters"])
PERSON = ("admin", ["system:masters", "system:authenticated"])
NOBODY = ("system:anonymous", ["system:unauthenticated"])


def policy_of(path):
    """The policy of a patch in either form of Talos: the document
    KubeAuditPolicyConfig, or cluster.apiServer.auditPolicy of the v1alpha1 config."""
    found = []
    for document in yaml.safe_load_all(path.read_text()):
        if not isinstance(document, dict):
            continue
        if document.get("kind") == "KubeAuditPolicyConfig":
            found.append(document["configuration"])
        elif "auditPolicy" in document.get("cluster", {}).get("apiServer", {}):
            found.append(document["cluster"]["apiServer"]["auditPolicy"])
    assert len(found) == 1, f"{path}: one audit policy, found {len(found)}"
    return found[0]


def resource(user, verb, group, name, sub=""):
    return {"user": user, "verb": verb, "group": group, "resource": name, "sub": sub}


def url(user, path):
    return {"user": user, "verb": "get", "path": path}


def matches_resource(rule, request):
    if "path" in request:
        return False
    if not rule.get("resources"):
        return True
    full = request["resource"] + ("/" + request["sub"] if request["sub"] else "")
    for entry in rule["resources"]:
        if entry.get("group", "") != request["group"]:
            continue
        names = entry.get("resources", [])
        if not names:
            return True
        for name in names:
            if name in ("*", full):
                return True
            if name.startswith("*/") and request["sub"] == name[2:]:
                return True
            if name.endswith("/*") and request["resource"] == name[:-2] and request["sub"]:
                return True
    return False


def matches_url(rule, request):
    if "path" not in request:
        return False
    for pattern in rule["nonResourceURLs"]:
        if pattern in ("*", request["path"]):
            return True
        if pattern.endswith("*") and request["path"].startswith(pattern[:-1]):
            return True
    return False


def level_of(policy, request):
    name, groups = request["user"]
    for rule in policy["rules"]:
        if rule.get("users") and name not in rule["users"]:
            continue
        if rule.get("userGroups") and not set(groups) & set(rule["userGroups"]):
            continue
        if rule.get("verbs") and request["verb"] not in rule["verbs"]:
            continue
        if rule.get("resources"):
            if matches_resource(rule, request):
                return rule["level"]
            continue
        if rule.get("nonResourceURLs"):
            if matches_url(rule, request):
                return rule["level"]
            continue
        return rule["level"]
    return "None"


KEPT = [
    ("a kubelet reads a Secret", resource(KUBELET, "get", "", "secrets")),
    ("a kubelet watches a Secret", resource(KUBELET, "watch", "", "secrets")),
    ("a service account lists Secrets", resource(ACCOUNT, "list", "", "secrets")),
    ("the controller manager reads a Secret", resource(CONTROLLERS, "get", "", "secrets")),
    ("the scheduler reads a Secret", resource(SCHEDULER, "get", "", "secrets")),
    ("the API server reads a Secret", resource(API_SERVER, "get", "", "secrets")),
    ("a person reads a Secret", resource(PERSON, "get", "", "secrets")),
    ("a service account writes a Secret", resource(ACCOUNT, "patch", "", "secrets")),
    ("a service account opens a command in a pod", resource(ACCOUNT, "get", "", "pods", "exec")),
    ("a person opens a command in a pod", resource(PERSON, "create", "", "pods", "exec")),
    ("a service account attaches to a pod", resource(ACCOUNT, "get", "", "pods", "attach")),
    ("a service account forwards a port", resource(ACCOUNT, "get", "", "pods", "portforward")),
    ("a service account reads through a node", resource(ACCOUNT, "get", "", "nodes", "proxy")),
    ("a service account reads through a Service", resource(ACCOUNT, "get", "", "services", "proxy")),
    ("a person reads a pod", resource(PERSON, "get", "", "pods")),
    ("a person lists nodes", resource(PERSON, "list", "", "nodes")),
    ("a person reads events", resource(PERSON, "list", "", "events")),
    ("a person deletes a lease", resource(PERSON, "delete", "coordination.k8s.io", "leases")),
    ("an anonymous read of a pod", resource(NOBODY, "get", "", "pods")),
    ("an anonymous read of an API path", url(NOBODY, "/api/v1")),
    ("a service account creates a pod", resource(ACCOUNT, "create", "", "pods")),
    ("a service account patches a deployment", resource(ACCOUNT, "patch", "apps", "deployments")),
    ("a service account deletes a config map", resource(ACCOUNT, "delete", "", "configmaps")),
    ("a kubelet patches the status of a pod", resource(KUBELET, "patch", "", "pods", "status")),
    ("a kubelet asks for a token", resource(KUBELET, "create", "", "serviceaccounts", "token")),
    ("the controller manager updates a role binding",
     resource(CONTROLLERS, "update", "rbac.authorization.k8s.io", "clusterrolebindings")),
    ("a service account asks for an access review",
     resource(ACCOUNT, "create", "authorization.k8s.io", "subjectaccessreviews")),
]

NOT_KEPT = [
    ("a kubelet renews its lease", resource(KUBELET, "update", "coordination.k8s.io", "leases")),
    ("a controller renews its leader lease", resource(ACCOUNT, "update", "coordination.k8s.io", "leases")),
    ("the scheduler patches a lease", resource(SCHEDULER, "patch", "coordination.k8s.io", "leases")),
    ("a kubelet writes an event", resource(KUBELET, "create", "", "events")),
    ("a controller patches an event", resource(ACCOUNT, "patch", "events.k8s.io", "events")),
    ("a probe of the health URL", url(NOBODY, "/readyz")),
    ("a probe of one health check", url(NOBODY, "/livez/ping")),
    ("a read of the version", url(ACCOUNT, "/version")),
    ("a kubelet reads a pod", resource(KUBELET, "get", "", "pods")),
    ("a kubelet watches a config map", resource(KUBELET, "watch", "", "configmaps")),
    ("a service account lists deployments", resource(ACCOUNT, "list", "apps", "deployments")),
    ("a service account reads the metrics URL", url(ACCOUNT, "/metrics")),
    ("the controller manager watches pods", resource(CONTROLLERS, "watch", "", "pods")),
    ("the scheduler lists nodes", resource(SCHEDULER, "list", "", "nodes")),
    ("the API server reads a namespace", resource(API_SERVER, "get", "", "namespaces")),
]


# The directory under talos/ of each cluster that has the audit stream.
CLUSTERS = {"wsl", "wsl-02"}


def test_each_cluster_has_an_audit_policy():
    assert {path.parts[-3] for path in FILES} == CLUSTERS


def test_homelab_wsl_has_the_policy_in_both_forms():
    forms = {}
    for path in FILES:
        if path.parts[-3] == "wsl":
            documents = [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
            forms[path.name] = "document" if any(d.get("kind") == "KubeAuditPolicyConfig" for d in documents) else "v1alpha1"
    assert forms == {"audit-policy.yaml": "v1alpha1", "audit-policy-multidoc.yaml": "document"}


def test_each_cluster_has_the_same_policy():
    policies = [policy_of(path) for path in FILES]
    assert all(policy == policies[0] for policy in policies)


@pytest.mark.parametrize("path", FILES, ids=lambda path: f"{path.parts[-3]}/{path.name}")
def test_the_policy_has_the_form_of_the_api_server(path):
    policy = policy_of(path)
    assert policy["apiVersion"] == "audit.k8s.io/v1"
    assert policy["kind"] == "Policy"
    assert set(policy) <= {"apiVersion", "kind", "omitStages", "rules"}
    assert set(policy.get("omitStages", [])) <= STAGES
    assert policy["rules"]
    for rule in policy["rules"]:
        assert rule["level"] in LEVELS
        assert set(rule) <= RULE_KEYS - NOT_READ
        for entry in rule.get("resources", []):
            assert set(entry) <= {"group", "resources"}
            assert isinstance(entry.get("group", ""), str)


@pytest.mark.parametrize("path", FILES, ids=lambda path: f"{path.parts[-3]}/{path.name}")
def test_the_policy_writes_no_body_of_a_request(path):
    """Metadata has no body. A higher level would put the data of a Secret in the log."""
    assert {rule["level"] for rule in policy_of(path)["rules"]} <= {"None", "Metadata"}


@pytest.mark.parametrize("path", FILES, ids=lambda path: f"{path.parts[-3]}/{path.name}")
@pytest.mark.parametrize("name,request_", KEPT, ids=[name for name, _ in KEPT])
def test_the_policy_keeps(path, name, request_):
    assert level_of(policy_of(path), request_) == "Metadata", name


@pytest.mark.parametrize("path", FILES, ids=lambda path: f"{path.parts[-3]}/{path.name}")
@pytest.mark.parametrize("name,request_", NOT_KEPT, ids=[name for name, _ in NOT_KEPT])
def test_the_policy_does_not_keep(path, name, request_):
    assert level_of(policy_of(path), request_) == "None", name


@pytest.mark.parametrize("path", FILES, ids=lambda path: f"{path.parts[-3]}/{path.name}")
def test_each_request_on_a_secret_is_kept_whoever_asks(path):
    policy = policy_of(path)
    for user in (KUBELET, ACCOUNT, CONTROLLERS, SCHEDULER, API_SERVER, PERSON, NOBODY):
        for verb in ("get", "list", "watch", "create", "update", "patch", "delete", "deletecollection"):
            assert level_of(policy, resource(user, verb, "", "secrets")) == "Metadata", (user[0], verb)
