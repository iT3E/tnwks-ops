"""The proofs of the Flux lockdown, with the controller of git on a
throwaway API server (run.sh).

The test talks to the API server as its admin, over HTTPS, with the
standard library only. kustomize-controller runs with the image and the
arguments of git, as the account flux-system/kustomize-controller, which is
bound to cluster-admin as on the clusters. A probe policy refuses each
ConfigMap in the namespace probe with the name of the user of the request,
so the status of a Kustomization that applies one shows the user that the
controller applies as.

python3 test_flux_lockdown.py Setup | NoFlag | Flag

- Setup: the CRDs of Flux, the namespaces, the accounts, two decryption
  Secrets that hold a placeholder (no key), the GitRepository flux-system
  with the artifact of the test tree, and the probe policy.
- NoFlag, with the arguments of git:
  1. A Kustomization with no account applies as kustomize-controller, and
     one that names kustomize-controller (step 2b) applies as that same
     user, with its rights: so the admission policy, which matches that
     user, sees it as before.
  2. One that names an account of its own applies as that account.
  3. The controller reads the decryption Secret as itself: a child account
     with no right on Secrets of flux-system builds with the Secret of the
     apps key and with the Secret of the class key, and a missing Secret
     reads "not found", not "forbidden".
  4. A Kustomization that comes to name a missing account fails its apply
     and prunes nothing; a delete of it then removes no object.
  6. A child whose account has only the role flux-child of git (step 2a)
     applies an object of each kind of that role, and fails on a Role and
     on a Pod, which the role does not grant.
  7. The same account with wait: true is Ready on a Deployment and a
     StatefulSet: the controller reads their ReplicaSets and Pods as the
     account when it waits. With the role less those reads, the wait fails
     "forbidden".
- Flag, after a restart with --default-service-account=default (step 2e):
  1. A Kustomization with no account applies as flux-system/default (which
     has no right in the namespace of the probe, so RBAC names it).
  2. One that applied before the flag and names no account fails and
     prunes nothing.
  3. The ones that name an account apply as before.
"""

import base64
import json
import ssl
import sys
import time
import unittest
import urllib.error
import urllib.request

API = "https://apiserver:6443"
CONTEXT = ssl.create_default_context(cafile="/pki/ca.crt")
CONTEXT.load_cert_chain("/pki/admin.crt", "/pki/admin.key")
KUSTOMIZATIONS = "/apis/kustomize.toolkit.fluxcd.io/v1/namespaces/flux-system/kustomizations"
GITREPOSITORY = "/apis/source.toolkit.fluxcd.io/v1/namespaces/flux-system/gitrepositories/flux-system"
ADMISSION = "/apis/admissionregistration.k8s.io/v1"
CONTROLLER = "system:serviceaccount:flux-system:kustomize-controller"
CHILD_ACCOUNTS = ("child-own", "child-dec")
# The account of a child with the role flux-child of git only, and one with
# that role less its reads of ReplicaSets and Pods (role-noread.json).
ROLE_ACCOUNT = "flux-child-census"
NOREAD_ACCOUNT = "flux-child-noread"


def read(name):
    with open(f"/work/{name}", encoding="utf-8") as f:
        return f.read()


def call(method, path, body=None, content_type="application/json"):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(API + path, data=data, method=method)
    request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, context=CONTEXT, timeout=30) as response:
            return response.status, parse(response.read())
    except urllib.error.HTTPError as error:
        return error.code, parse(error.read())


def parse(raw):
    try:
        return json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {"message": raw.decode(errors="replace")}


def wait_for(what, test, seconds=120):
    end = time.monotonic() + seconds
    last = None
    while time.monotonic() < end:
        try:
            last = test()
            if last:
                return last
        except OSError:
            pass
        time.sleep(1)
    raise AssertionError(f"no {what} in {seconds} s")


def make(path, doc):
    status, body = call("POST", path, doc)
    assert status in (201, 409), (path, status, body.get("message"))


def obj(api, kind, name, namespace=None, **fields):
    doc = {"apiVersion": api, "kind": kind, "metadata": {"name": name}}
    if namespace:
        doc["metadata"]["namespace"] = namespace
    doc.update(fields)
    return doc


def kustomization(name, path, **spec):
    body = {"interval": "1m", "retryInterval": "5s", "timeout": "30s", "path": path, "prune": True,
            "wait": False, "sourceRef": {"kind": "GitRepository", "name": "flux-system"}}
    body.update(spec)
    make(KUSTOMIZATIONS, obj("kustomize.toolkit.fluxcd.io/v1", "Kustomization", name, "flux-system", spec=body))


def ks(name):
    status, body = call("GET", f"{KUSTOMIZATIONS}/{name}")
    assert status == 200, (name, status)
    return body


def ready(body):
    """The Ready condition of a Kustomization, once the controller has
    handled its generation: (status, message), or None."""
    for c in (body.get("status") or {}).get("conditions") or []:
        if c["type"] == "Ready" and c["status"] in ("True", "False") and \
                c.get("observedGeneration") == body["metadata"]["generation"]:
            return c["status"], c["message"]
    return None


def wait_ready(name, want, contains=None, seconds=120):
    def test():
        got = ready(ks(name))
        if got and got[0] == want and (contains is None or contains in got[1]):
            return got
        return None
    try:
        return wait_for(f"Ready {want} of {name}" + (f" with {contains!r}" if contains else ""), test, seconds)
    except AssertionError:
        print(f"{name}:", json.dumps((ks(name).get("status") or {}).get("conditions")))
        raise


def reconcile(name):
    """A reconcile on request, as `flux reconcile` asks for one; the Ready
    condition of that reconcile."""
    stamp = str(time.time_ns())
    status, _ = call("PATCH", f"{KUSTOMIZATIONS}/{name}",
                     {"metadata": {"annotations": {"reconcile.fluxcd.io/requestedAt": stamp}}},
                     "application/merge-patch+json")
    assert status == 200, status
    def handled():
        body = ks(name)
        if (body.get("status") or {}).get("lastHandledReconcileAt") == stamp and ready(body):
            return body
        return None
    return ready(wait_for(f"a reconcile of {name}", handled))


def exists(namespace, name):
    return call("GET", f"/api/v1/namespaces/{namespace}/configmaps/{name}")[0] == 200


def inventory(name):
    return [e["id"] for e in ((ks(name).get("status") or {}).get("inventory") or {}).get("entries") or []]


def user(name):
    return f"identity-probe user=system:serviceaccount:flux-system:{name} end"


class Setup(unittest.TestCase):
    def test_setup(self):
        wait_for("API server", lambda: call("GET", "/readyz")[0] == 200)
        crds = json.loads(read("crds.json"))
        for crd in crds:
            make("/apis/apiextensions.k8s.io/v1/customresourcedefinitions", crd)
        for crd in crds:
            wait_for(f"CRD {crd['metadata']['name']}", lambda c=crd: any(
                x["type"] == "Established" and x["status"] == "True"
                for x in call("GET", f"/apis/apiextensions.k8s.io/v1/customresourcedefinitions/{c['metadata']['name']}")[1]
                .get("status", {}).get("conditions", [])))
        for namespace, labels in (("flux-system", {}), ("tenant", {}), ("probe", {"lockdown-probe": "identity"})):
            make("/api/v1/namespaces", obj("v1", "Namespace", namespace, metadata={"name": namespace, "labels": labels}))
        wait_for("the aggregated role admin", lambda: call(
            "GET", "/apis/rbac.authorization.k8s.io/v1/clusterroles/admin")[1].get("rules"))
        # The accounts of flux-system: the controller (cluster-admin, as in
        # the install of git), default, and two of children outside the
        # class with admin in the namespaces of the test only.
        for name in ("kustomize-controller", "default", *CHILD_ACCOUNTS, ROLE_ACCOUNT, NOREAD_ACCOUNT):
            make("/api/v1/namespaces/flux-system/serviceaccounts", obj("v1", "ServiceAccount", name, "flux-system"))
        make("/apis/rbac.authorization.k8s.io/v1/clusterrolebindings", obj(
            "rbac.authorization.k8s.io/v1", "ClusterRoleBinding", "cluster-reconciler-flux-system",
            roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "cluster-admin"},
            subjects=[{"kind": "ServiceAccount", "name": "kustomize-controller", "namespace": "flux-system"}]))
        for namespace in ("tenant", "probe"):
            make(f"/apis/rbac.authorization.k8s.io/v1/namespaces/{namespace}/rolebindings", obj(
                "rbac.authorization.k8s.io/v1", "RoleBinding", "children-admin", namespace,
                roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "admin"},
                subjects=[{"kind": "ServiceAccount", "name": n, "namespace": "flux-system"} for n in CHILD_ACCOUNTS]))
        # The role of the children of git, bound in tenant as accounts.py
        # binds it; and the CRDs of its kinds of the Prometheus operator.
        for account, role in ((ROLE_ACCOUNT, "role.json"), (NOREAD_ACCOUNT, "role-noread.json")):
            role = json.loads(read(role))
            make("/apis/rbac.authorization.k8s.io/v1/clusterroles", role)
            make("/apis/rbac.authorization.k8s.io/v1/namespaces/tenant/rolebindings", obj(
                "rbac.authorization.k8s.io/v1", "RoleBinding", account, "tenant",
                roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole",
                         "name": role["metadata"]["name"]},
                subjects=[{"kind": "ServiceAccount", "name": account, "namespace": "flux-system"}]))
        for kind, plural in (("PrometheusRule", "prometheusrules"), ("ServiceMonitor", "servicemonitors")):
            crd = obj("apiextensions.k8s.io/v1", "CustomResourceDefinition", f"{plural}.monitoring.coreos.com", spec={
                "group": "monitoring.coreos.com", "scope": "Namespaced",
                "names": {"kind": kind, "plural": plural, "singular": kind.lower(), "listKind": kind + "List"},
                "versions": [{"name": "v1", "served": True, "storage": True, "schema": {"openAPIV3Schema": {
                    "type": "object", "x-kubernetes-preserve-unknown-fields": True}}}]})
            make("/apis/apiextensions.k8s.io/v1/customresourcedefinitions", crd)
            wait_for(f"CRD {plural}", lambda n=crd["metadata"]["name"]: any(
                x["type"] == "Established" and x["status"] == "True"
                for x in (call("GET", f"/apis/apiextensions.k8s.io/v1/customresourcedefinitions/{n}")[1]
                          .get("status") or {}).get("conditions") or []))
        # The decryption Secrets hold a placeholder under a name that the
        # controller does not read as a key: the tree has no encrypted file.
        placeholder = base64.b64encode(b"a placeholder of the test, not a key").decode()
        for name in ("sops-age-apps", "sops-age-class"):
            make("/api/v1/namespaces/flux-system/secrets",
                 obj("v1", "Secret", name, "flux-system", data={"README": placeholder}))
        make(GITREPOSITORY.rsplit("/", 1)[0], obj(
            "source.toolkit.fluxcd.io/v1", "GitRepository", "flux-system", "flux-system",
            spec={"interval": "1h", "url": "https://source.invalid/tree", "ref": {"branch": "main"}}))
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        status, body = call("PATCH", GITREPOSITORY + "/status", {"status": {
            "observedGeneration": 1,
            "conditions": [{"type": "Ready", "status": "True", "reason": "Succeeded", "message": "test tree",
                            "lastTransitionTime": now, "observedGeneration": 1}],
            "artifact": {"path": "gitrepository/flux-system/flux-system/source.tar.gz",
                         "url": "http://source:8080/source.tar.gz",
                         "revision": "main@sha1:" + "0" * 40, "digest": read("digest.txt").strip(),
                         "lastUpdateTime": now}}}, "application/merge-patch+json")
        self.assertEqual(status, 200, body.get("message"))
        make(f"{ADMISSION}/validatingadmissionpolicies", obj(
            "admissionregistration.k8s.io/v1", "ValidatingAdmissionPolicy", "lockdown-identity-probe", spec={
                "failurePolicy": "Fail",
                "matchConstraints": {
                    "namespaceSelector": {"matchLabels": {"lockdown-probe": "identity"}},
                    "resourceRules": [{"apiGroups": [""], "apiVersions": ["v1"], "operations": ["CREATE", "UPDATE"],
                                       "resources": ["configmaps"]}]},
                "validations": [{"expression": "false",
                                 "messageExpression": "'identity-probe user=' + request.userInfo.username + ' end'"}]}))
        make(f"{ADMISSION}/validatingadmissionpolicybindings", obj(
            "admissionregistration.k8s.io/v1", "ValidatingAdmissionPolicyBinding", "lockdown-identity-probe",
            spec={"policyName": "lockdown-identity-probe", "validationActions": ["Deny"]}))
        probe = obj("v1", "ConfigMap", "x", "probe", data={})
        wait_for("the probe policy in force", lambda: "identity-probe user=admin end" in call(
            "POST", "/api/v1/namespaces/probe/configmaps?dryRun=All", probe)[1].get("message", ""))


class NoFlag(unittest.TestCase):
    def test_1_the_controller_and_the_class_apply_as_the_controller(self):
        kustomization("who-controller", "./probe")
        kustomization("who-class", "./probe", serviceAccountName="kustomize-controller")
        wait_ready("who-controller", "False", "identity-probe user=" + CONTROLLER + " end")
        wait_ready("who-class", "False", "identity-probe user=" + CONTROLLER + " end")
        # With its rights: a cluster object.
        kustomization("class", "./class", serviceAccountName="kustomize-controller")
        wait_ready("class", "True")
        self.assertEqual(call("GET", "/apis/rbac.authorization.k8s.io/v1/clusterroles/lockdown-class")[0], 200)

    def test_2_a_child_applies_as_its_own_account(self):
        kustomization("who-own", "./probe", serviceAccountName="child-own")
        wait_ready("who-own", "False", user("child-own"))

    def test_3_the_controller_reads_the_decryption_secret_as_itself(self):
        # child-dec may not read a Secret of flux-system.
        status, review = call("POST", "/apis/authorization.k8s.io/v1/subjectaccessreviews", {
            "apiVersion": "authorization.k8s.io/v1", "kind": "SubjectAccessReview", "spec": {
                "user": "system:serviceaccount:flux-system:child-dec",
                "groups": ["system:serviceaccounts", "system:serviceaccounts:flux-system", "system:authenticated"],
                "resourceAttributes": {"namespace": "flux-system", "verb": "get", "resource": "secrets",
                                       "name": "sops-age-class"}}})
        self.assertEqual(status, 201)
        self.assertFalse(review["status"].get("allowed"), review["status"])
        for name, path, secret in (("dec-apps", "./tenant", "sops-age-apps"),
                                   ("dec-class", "./tenant-class", "sops-age-class")):
            kustomization(name, path, serviceAccountName="child-dec",
                          decryption={"provider": "sops", "secretRef": {"name": secret}})
            wait_ready(name, "True")
        self.assertTrue(exists("tenant", "app") and exists("tenant", "app-class"))
        # The controller reads a missing Secret as a user that may read
        # it: "not found". As child-dec the read would be "forbidden".
        kustomization("dec-none", "./tenant-none", serviceAccountName="child-dec",
                      decryption={"provider": "sops", "secretRef": {"name": "sops-age-none"}})
        _, message = wait_ready("dec-none", "False", "sops-age-none")
        self.assertIn("not found", message)
        self.assertNotIn("forbidden", message)
        self.assertFalse(exists("tenant", "app-none"))

    def test_4_a_missing_account_fails_and_prunes_nothing(self):
        kustomization("gone", "./kept")
        wait_ready("gone", "True")
        self.assertTrue(exists("tenant", "kept"))
        before = inventory("gone")
        self.assertEqual(before, ["tenant_kept__ConfigMap"])
        status, _ = call("PATCH", f"{KUSTOMIZATIONS}/gone",
                         {"spec": {"serviceAccountName": "missing-account", "path": "./other"}},
                         "application/merge-patch+json")
        self.assertEqual(status, 200)
        _, message = wait_ready("gone", "False", "missing-account")
        self.assertIn("forbidden", message)
        self.assertEqual(reconcile("gone")[0], "False")
        self.assertTrue(exists("tenant", "kept"))
        self.assertFalse(exists("tenant", "other"))
        self.assertEqual(inventory("gone"), before)
        # A delete of a Kustomization whose account is missing removes no
        # object either: the controller can not act as that account, so it
        # leaves the objects of the inventory in place.
        self.assertEqual(call("DELETE", f"{KUSTOMIZATIONS}/gone")[0], 200)
        wait_for("the delete of gone", lambda: call("GET", f"{KUSTOMIZATIONS}/gone")[0] == 404)
        self.assertTrue(exists("tenant", "kept"))

    def test_5_a_child_with_no_account_before_the_flag(self):
        kustomization("flagged", "./kept2")
        wait_ready("flagged", "True")
        self.assertTrue(exists("tenant", "kept2"))


    def test_6_the_role_of_the_children_applies_their_kinds(self):
        kustomization("census", "./census", serviceAccountName=ROLE_ACCOUNT)
        wait_ready("census", "True")
        self.assertEqual(len(inventory("census")), 12, inventory("census"))
        for name in ("census-role", "census-pod"):
            kustomization(name, f"./{name}", serviceAccountName=ROLE_ACCOUNT)
            wait_ready(name, "False", f"User \"system:serviceaccount:flux-system:{ROLE_ACCOUNT}\" cannot")

    def test_7_the_role_reads_what_the_wait_reads(self):
        kustomization("census-wait", "./census-wait", serviceAccountName=ROLE_ACCOUNT, wait=True)
        _, message = wait_ready("census-wait", "True")
        self.assertNotIn("forbidden", message)
        self.assertEqual(len(inventory("census-wait")), 2, inventory("census-wait"))
        kustomization("census-noread", "./census-noread", serviceAccountName=NOREAD_ACCOUNT, wait=True)
        wait_ready("census-noread", "False", "replicasets.apps is forbidden")


class Flag(unittest.TestCase):
    def test_1_no_account_applies_as_the_default_account(self):
        # The default account has no right in probe, so RBAC refuses it
        # first, with its name.
        kustomization("who-default", "./probe")
        wait_ready("who-default", "False", 'User "system:serviceaccount:flux-system:default" cannot patch')

    def test_2_a_child_with_no_account_fails_and_prunes_nothing(self):
        before = inventory("flagged")
        status, message = reconcile("flagged")
        self.assertEqual(status, "False", message)
        self.assertIn("system:serviceaccount:flux-system:default", message)
        self.assertTrue(exists("tenant", "kept2"))
        self.assertEqual(inventory("flagged"), before)

    def test_3_the_ones_that_name_an_account_apply_as_before(self):
        self.assertEqual(reconcile("class")[0], "True")
        self.assertEqual(reconcile("dec-apps")[0], "True")
        self.assertEqual(reconcile("dec-class")[0], "True")
        _, message = reconcile("who-class")
        self.assertIn("identity-probe user=" + CONTROLLER + " end", message)
        _, message = reconcile("who-own")
        self.assertIn(user("child-own"), message)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], *sys.argv[1:]], verbosity=2)
