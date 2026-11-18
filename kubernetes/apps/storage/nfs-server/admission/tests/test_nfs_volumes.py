"""The admission policies of NFS volumes, on a throwaway API server (run.sh).

The API server is the version of the clusters. The test talks to it with
the client certificate of its admin, over HTTPS, with the standard library
only. It first binds both policies with Deny, to test each rule, then with
the bindings of git (Audit and Warn), to show that those refuse nothing and
warn. Each pod is a dry run, so no pod is made. No kubelet runs.
"""

import copy
import json
import ssl
import time
import unittest
import urllib.error
import urllib.request

API = "https://apiserver:6443"
ADMISSION = "/apis/admissionregistration.k8s.io/v1"
SERVER = "10.5.0.3"
NAMESPACES = ["production", "tools", "networking", "agents-main", "agents-finance", "other"]
CONTEXT = ssl.create_default_context(cafile="/pki/ca.crt")
CONTEXT.load_cert_chain("/pki/admin.crt", "/pki/admin.key")

with open("/work/policies.json", encoding="utf-8") as f:
    POLICIES = json.load(f)
with open("/work/users.json", encoding="utf-8") as f:
    USERS = json.load(f)


def call(method, path, body=None, content_type="application/json"):
    """The status, the JSON body and the warnings of one request."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(API + path, data=data, method=method)
    request.add_header("Content-Type", content_type)
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
        time.sleep(1)
    raise AssertionError(f"no {what} in {seconds} s")


def endpoint(doc):
    plural = {
        "ValidatingAdmissionPolicy": "validatingadmissionpolicies",
        "ValidatingAdmissionPolicyBinding": "validatingadmissionpolicybindings",
    }[doc["kind"]]
    return f"{ADMISSION}/{plural}"


def bind(actions):
    """Applies the policies of git, with these actions on each binding."""
    for doc in POLICIES:
        doc = copy.deepcopy(doc)
        if doc["kind"] == "ValidatingAdmissionPolicyBinding":
            doc["spec"]["validationActions"] = actions
        path = endpoint(doc)
        status, current, _ = call("GET", f"{path}/{doc['metadata']['name']}")
        if status == 404:
            status, body, _ = call("POST", path, doc)
        else:
            doc["metadata"]["resourceVersion"] = current["metadata"]["resourceVersion"]
            status, body, _ = call("PUT", f"{path}/{doc['metadata']['name']}", doc)
        assert status in (200, 201), body


def pod(namespace, volumes, name="probe"):
    """A dry run of a pod with these volumes: the status, body and warnings."""
    spec = {
        "containers": [{"name": "main", "image": "registry.k8s.io/pause:3.10"}],
        "volumes": volumes,
    }
    body = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name}, "spec": spec}
    return call("POST", f"/api/v1/namespaces/{namespace}/pods?dryRun=All", body)


def nfs(server, path, read_only=None):
    source = {"server": server, "path": path}
    if read_only is not None:
        source["readOnly"] = read_only
    return {"name": "share", "nfs": source}


def volume(name, path, read_only, claim_namespace, claim_name, server=SERVER):
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolume",
        "metadata": {"name": name},
        "spec": {
            "capacity": {"storage": "1Gi"},
            "accessModes": ["ReadOnlyMany" if read_only else "ReadWriteMany"],
            "persistentVolumeReclaimPolicy": "Retain",
            "storageClassName": "",
            "nfs": {"server": server, "path": path, "readOnly": read_only},
            "claimRef": {"namespace": claim_namespace, "name": claim_name},
        },
    }


def create_volume(body, dry_run=True):
    return call("POST", "/api/v1/persistentvolumes" + ("?dryRun=All" if dry_run else ""), body)


LISTED_VOLUMES = [
    ("nfs-vault-main-rw", "/vault-main", False, "agents-main", "vault-rw"),
    ("nfs-vault-main-ro", "/vault-main", True, "agents-main", "vault-ro"),
    ("nfs-vault-finance-rw", "/vault-finance", False, "agents-finance", "vault-rw"),
    ("nfs-vault-finance-ro", "/vault-finance", True, "agents-finance", "vault-ro"),
]


def setUpModule():
    wait_for("API server", lambda: call("GET", "/readyz")[0] == 200, 120)
    for namespace in NAMESPACES:
        call("POST", "/api/v1/namespaces", {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}})
        # No controller manager runs, so make the ServiceAccount that a pod
        # names by default.
        call(
            "POST",
            f"/api/v1/namespaces/{namespace}/serviceaccounts",
            {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "default"}},
        )


class TypeCheck(unittest.TestCase):
    """Each expression of each policy checks against the types of its kind."""

    def test_no_policy_has_a_type_warning(self):
        bind(["Audit", "Warn"])
        for doc in POLICIES:
            if doc["kind"] != "ValidatingAdmissionPolicy":
                continue
            path = f"{ADMISSION}/validatingadmissionpolicies/{doc['metadata']['name']}"

            def checked(path=path):
                current = call("GET", path)[1]
                return current.get("status", {}).get("observedGeneration") == current["metadata"]["generation"]

            wait_for(f"type check of {doc['metadata']['name']}", checked, 90)
            status = call("GET", path)[1]["status"]
            with self.subTest(policy=doc["metadata"]["name"]):
                self.assertEqual(status.get("typeChecking", {}).get("expressionWarnings", []), [])


class Deny(unittest.TestCase):
    """Each rule, with both bindings set to Deny."""

    @classmethod
    def setUpClass(cls):
        bind(["Deny"])
        wait_for("policy in force", lambda: pod("other", [nfs(SERVER, "/export")])[0] == 403, 60)

    def assertRefused(self, result, policy="nfs-pod-volumes"):
        # The policies give the reason Forbidden, so the status is 403.
        status, body, _ = result
        self.assertEqual(status, 403, body)
        self.assertIn(policy, body.get("message", ""))

    def assertAdmitted(self, result):
        status, body, _ = result
        self.assertIn(status, (200, 201), body)

    def test_each_user_of_today_is_admitted(self):
        for user in USERS:
            with self.subTest(user=user["name"]):
                body = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": user["name"]}, "spec": user["spec"]}
                self.assertAdmitted(call("POST", f"/api/v1/namespaces/{user['namespace']}/pods?dryRun=All", body))

    def test_a_pod_with_no_nfs_volume_is_admitted(self):
        self.assertAdmitted(pod("other", [{"name": "scratch", "emptyDir": {}}]))
        self.assertAdmitted(pod("other", []))

    def test_a_listed_path_in_another_namespace_is_refused(self):
        self.assertRefused(pod("other", [nfs(SERVER, "/export")]))
        self.assertRefused(pod("production", [nfs(SERVER, "/gdrive-sync")]))
        self.assertRefused(pod("agents-main", [nfs(SERVER, "/export")]))

    def test_a_variant_of_a_path_is_refused(self):
        for path in ["/", "/vault-main", "/vault-finance", "//export", "/./export", "/export/", "/export/..", "/EXPORT"]:
            with self.subTest(path=path):
                self.assertRefused(pod("production", [nfs(SERVER, path)]))

    def test_another_name_of_the_server_is_refused(self):
        servers = [
            "nfs-server.storage.svc.cluster.local",
            "nfs-server.storage",
            "10.43.255.254",
            "homelab-wsl-worker-1",
            "10.5.0.3.",
            "010.5.0.3",
            "::ffff:10.5.0.3",
        ]
        for server in servers:
            with self.subTest(server=server):
                self.assertRefused(pod("production", [nfs(server, "/export")]))

    def test_one_listed_and_one_other_nfs_volume_is_refused(self):
        volumes = [nfs(SERVER, "/export", True), {"name": "other", "nfs": {"server": SERVER, "path": "/vault-main"}}]
        self.assertRefused(pod("production", volumes))

    def test_each_listed_volume_of_a_share_is_admitted(self):
        for listed in LISTED_VOLUMES:
            with self.subTest(volume=listed[0]):
                self.assertAdmitted(create_volume(volume(*listed)))

    def test_a_volume_that_differs_from_its_entry_is_refused(self):
        name, path, read_only, claim_namespace, claim_name = LISTED_VOLUMES[1]
        cases = {
            "another name": volume("nfs-vault-main-ro2", path, read_only, claim_namespace, claim_name),
            "another path": volume(name, "/vault-finance", read_only, claim_namespace, claim_name),
            "the pseudo root": volume(name, "/", read_only, claim_namespace, claim_name),
            "read and write": volume(name, path, False, claim_namespace, claim_name),
            "another claim namespace": volume(name, path, read_only, "agents-finance", claim_name),
            "another claim": volume(name, path, read_only, claim_namespace, "vault-rw"),
            "another server": volume(name, path, read_only, claim_namespace, claim_name, server="nfs-server.storage"),
        }
        no_claim = volume(name, path, read_only, claim_namespace, claim_name)
        del no_claim["spec"]["claimRef"]
        cases["no claim"] = no_claim
        for case, body in cases.items():
            with self.subTest(case=case):
                self.assertRefused(create_volume(body), "nfs-persistent-volumes")

    def test_an_nfs_volume_that_is_not_a_share_is_refused(self):
        self.assertRefused(create_volume(volume("sites", "/export", True, "production", "sites")), "nfs-persistent-volumes")

    def test_a_volume_with_another_source_is_admitted(self):
        body = volume("scratch", "/x", False, "other", "scratch")
        del body["spec"]["nfs"]
        body["spec"]["hostPath"] = {"path": "/tmp/scratch"}
        self.assertAdmitted(create_volume(body))

    def test_a_listed_volume_can_not_move_to_another_claim(self):
        listed = volume(*LISTED_VOLUMES[3])
        self.assertAdmitted(create_volume(listed, dry_run=False))
        try:
            path = f"/api/v1/persistentvolumes/{LISTED_VOLUMES[3][0]}"
            patch = "application/merge-patch+json"
            moved = {"spec": {"claimRef": {"namespace": "production", "name": "vault-ro"}}}
            self.assertRefused(call("PATCH", path, moved, patch), "nfs-persistent-volumes")
            # The binder of the cluster adds the uid of the claim: allowed.
            bound = {"spec": {"claimRef": {"uid": "00000000-0000-0000-0000-000000000001"}}}
            self.assertAdmitted(call("PATCH", path, bound, patch))
        finally:
            # No controller removes the protection finalizer here.
            call("PATCH", path, {"metadata": {"finalizers": None}}, patch)
            call("DELETE", path)
            wait_for("delete of the volume", lambda: call("GET", path)[0] == 404, 30)


class AuditAndWarn(unittest.TestCase):
    """The bindings of git: a pod that Deny refuses is admitted, with a warning."""

    @classmethod
    def setUpClass(cls):
        bindings = [d["spec"]["validationActions"] for d in POLICIES if d["kind"] == "ValidatingAdmissionPolicyBinding"]
        assert bindings == [["Audit", "Warn"], ["Audit", "Warn"]], bindings
        bind(["Audit", "Warn"])

        def warned():
            status, _, warnings = pod("other", [nfs(SERVER, "/export")])
            if status != 201 or not any("nfs-pod-volumes" in w for w in warnings):
                return False
            status, _, warnings = create_volume(volume("sites", "/export", True, "production", "sites"))
            return status == 201 and any("nfs-persistent-volumes" in w for w in warnings)

        wait_for("bindings of git in force", warned, 60)

    def test_a_pod_off_the_list_is_admitted_with_a_warning(self):
        status, body, warnings = pod("other", [nfs(SERVER, "/vault-main")])
        self.assertEqual(status, 201, body)
        self.assertTrue(any("nfs-pod-volumes" in w for w in warnings), warnings)

    def test_a_volume_off_the_list_is_admitted_with_a_warning(self):
        status, body, warnings = create_volume(volume("sites", "/export", True, "production", "sites"))
        self.assertEqual(status, 201, body)
        self.assertTrue(any("nfs-persistent-volumes" in w for w in warnings), warnings)


if __name__ == "__main__":
    unittest.main(verbosity=2)
