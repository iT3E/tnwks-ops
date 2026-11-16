"""The admission policies of the run jobs, on a throwaway API server (run.sh).

Each Job and each patch of the run receiver goes through its own code
(run_receiver.config.fill, run_receiver.kube.Kube) with a token of its own
service account, as in the cluster. The admin of the throwaway API server
sets up the namespace, the Roles and the policies, and plays the kubelet
(the status of a pod) where a test needs a Job to end.
"""

import asyncio
import copy
import json
import subprocess
import time
import unittest

from run_receiver import config
from run_receiver.kube import Kube

NAMESPACE = "agents-workflows"
RECEIVER = f"system:serviceaccount:{NAMESPACE}:run-receiver"
API = "https://apiserver:6443"
CA = "/pki/ca.crt"
TOKEN = "/tmp/receiver-token"
AGENT = "renovate-reviewer"
NAMESPACE_OBJECT = {
    "apiVersion": "v1",
    "kind": "Namespace",
    "metadata": {
        "name": NAMESPACE,
        "labels": {
            "pod-security.kubernetes.io/enforce": "restricted",
            "pod-security.kubernetes.io/enforce-version": "latest",
        },
    },
}


def kubectl(*args, stdin=None, check=True):
    done = subprocess.run(["kubectl", *args], input=stdin, capture_output=True, text=True, timeout=60, check=False)
    if check and done.returncode != 0:
        raise AssertionError(f"kubectl {' '.join(args)}: {done.stderr.strip()}")
    return done


def wait_for(what, test, seconds=90):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(1)
    raise AssertionError(f"no {what} in {seconds} s")


def setUpModule():
    wait_for("API server", lambda: kubectl("get", "--raw", "/readyz", check=False).returncode == 0, 120)
    kubectl("apply", "-f", "-", stdin=json.dumps(NAMESPACE_OBJECT))
    kubectl("apply", "-n", NAMESPACE, "-f", "/work/rbac.yaml")
    kubectl("apply", "-f", "/work/admissionpolicy.yaml")
    token = kubectl("create", "token", "run-receiver", "-n", NAMESPACE, "--duration=1h").stdout.strip()
    with open(TOKEN, "w", encoding="utf-8") as f:
        f.write(token)
    # A policy and its binding take effect a moment after they are made: wait
    # until a Job with a second container is refused (a dry run, so nothing
    # is made before that).
    second = receiver_job("probe-wait")
    pod = second["spec"]["template"]["spec"]
    pod["containers"].append({"name": "extra", "image": pod["containers"][0]["image"]})
    wait_for("policy in force", lambda: create(second, dry_run=True)[0] == 422, 60)


def receiver_job(key):
    """The Job that the receiver makes for this key, from agents.yaml."""
    templates = config.load("/work/agents.yaml")
    return config.fill(
        templates[AGENT],
        name=f"run-{key}",
        namespace=NAMESPACE,
        agent=AGENT,
        key=key,
        input_json=json.dumps({"repository": "example-org/example", "number": 1}),
        fingerprint="0" * 64,
    )


def job(key, **container):
    """A receiver Job with changes to its container (None removes a field)."""
    out = receiver_job(key)
    target = out["spec"]["template"]["spec"]["containers"][0]
    for field, value in container.items():
        if value is None:
            target.pop(field, None)
        else:
            target[field] = value
    return out


async def _call(method, *args):
    kube = Kube(base_url=API, token_file=TOKEN, ca_file=CA)
    try:
        return await getattr(kube, method)(*args)
    finally:
        await kube.close()


def create(body, dry_run=False):
    """POST the Job as the receiver: (status code, text)."""
    kube = Kube(base_url=API, token_file=TOKEN, ca_file=CA)
    query = "?dryRun=All" if dry_run else ""

    async def go():
        try:
            r = await kube._call("POST", f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs{query}", json=body)
            return r.status_code, r.text
        finally:
            await kube.close()

    return asyncio.run(go())


def patch(name, body, content_type="application/merge-patch+json"):
    """PATCH the Job as the receiver: (status code, text)."""
    kube = Kube(base_url=API, token_file=TOKEN, ca_file=CA)

    async def go():
        try:
            r = await kube._call(
                "PATCH",
                f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs/{name}",
                content=json.dumps(body).encode(),
                headers_extra={"Content-Type": content_type},
            )
            return r.status_code, r.text
        finally:
            await kube.close()

    return asyncio.run(go())


def get_job(name):
    done = kubectl("get", "job", name, "-n", NAMESPACE, "-o", "json", check=False)
    return json.loads(done.stdout) if done.returncode == 0 else None


class ReceiverJobs(unittest.TestCase):
    """What the receiver makes and writes at this image is admitted."""

    def test_a_job_from_the_template_is_admitted_and_both_annotations_are_written(self):
        body = receiver_job("allow-1")
        created, _ = asyncio.run(_call("create_job", NAMESPACE, body))
        self.assertTrue(created)
        asyncio.run(_call("annotate_job", NAMESPACE, "run-allow-1", {"workflow-agents/result": '{"status":"x"}'}))
        asyncio.run(_call("annotate_job", NAMESPACE, "run-allow-1", {"workflow-agents/sandbox-record": "gone"}))
        # A second write of the kept answer passes too (the receiver can write it again).
        asyncio.run(_call("annotate_job", NAMESPACE, "run-allow-1", {"workflow-agents/result": '{"status":"y"}'}))
        annotations = get_job("run-allow-1")["metadata"]["annotations"]
        self.assertEqual(annotations["workflow-agents/result"], '{"status":"y"}')
        self.assertEqual(annotations["workflow-agents/sandbox-record"], "gone")
        self.assertEqual(annotations["workflow-agents/run-key"], "allow-1")

    def test_the_controllers_run_end_and_delete_a_receiver_job(self):
        # The receiver makes it; the TTL is short here so that the TTL
        # controller deletes it in the test (the policy does not pin the TTL).
        body = receiver_job("allow-2")
        body["spec"]["ttlSecondsAfterFinished"] = 5
        self.assertTrue(asyncio.run(_call("create_job", NAMESPACE, body))[0])
        # The Job controller makes the pod (with its finalizer on the pod).
        pod = wait_for("pod", lambda: kubectl(
            "get", "pods", "-n", NAMESPACE, "-l", "job-name=run-allow-2", "-o", "jsonpath={.items[0].metadata.name}",
            check=False).stdout)
        # The kubelet's part: the pod succeeded.
        status = {"status": {"phase": "Succeeded", "conditions": [{"type": "Ready", "status": "False"}]}}
        kubectl("patch", "pod", pod, "-n", NAMESPACE, "--subresource=status", "--type=merge", "-p", json.dumps(status))
        asyncio.run(_call("annotate_job", NAMESPACE, "run-allow-2", {"workflow-agents/result": '{"status":"x"}'}))
        # The Job controller writes the status, the TTL controller deletes the
        # Job (foreground), and the garbage collector removes the pod and the
        # finalizer of the Job.
        wait_for("Job gone", lambda: get_job("run-allow-2") is None, 120)

    def test_an_admin_and_the_garbage_collector_are_not_bound(self):
        self.assertTrue(asyncio.run(_call("create_job", NAMESPACE, receiver_job("allow-3")))[0])
        kubectl("label", "job", "run-allow-3", "-n", NAMESPACE, "note=admin")
        kubectl("delete", "job", "run-allow-3", "-n", NAMESPACE, "--cascade=foreground", "--wait=false")
        wait_for("Job gone", lambda: get_job("run-allow-3") is None, 120)


class CommandPin(unittest.TestCase):
    """A Job of the receiver runs the entrypoint with the one argument run-job."""

    CASES = {
        "a command": {"command": ["sh", "-c", "true"]},
        "a command with the same args": {"command": ["python3"], "args": ["run-job"]},
        "another argument": {"args": ["run-job", "--other"]},
        "no args": {"args": None},
        "a postStart hook": {"lifecycle": {"postStart": {"exec": {"command": ["true"]}}}},
        "a liveness probe": {"livenessProbe": {"exec": {"command": ["true"]}}},
        "a readiness probe": {"readinessProbe": {"exec": {"command": ["true"]}}},
        "a startup probe": {"startupProbe": {"exec": {"command": ["true"]}}},
    }

    def test_each_other_command_is_refused_by_the_policy(self):
        for i, (what, change) in enumerate(self.CASES.items()):
            with self.subTest(what):
                code, text = create(job(f"pin-{i}", **change))
                self.assertEqual(code, 422, text)
                self.assertIn("workflow-run-jobs", text)
                self.assertIn("the one argument run-job", text)


class OtherContainers(unittest.TestCase):
    def test_an_init_container_or_a_second_container_is_refused(self):
        extra = {"name": "extra", "image": receiver_job("x")["spec"]["template"]["spec"]["containers"][0]["image"]}
        for i, field in enumerate(("initContainers", "containers")):
            with self.subTest(field):
                body = receiver_job(f"other-{i}")
                pod = body["spec"]["template"]["spec"]
                pod[field] = [*pod.get(field, []), copy.deepcopy(extra)] if field == "containers" else [extra]
                code, text = create(body)
                self.assertEqual(code, 422, text)
                self.assertIn("no other one", text)

    def test_an_ephemeral_container_in_the_template_is_refused(self):
        body = receiver_job("other-e")
        body["spec"]["template"]["spec"]["ephemeralContainers"] = [{"name": "debug", "image": "busybox"}]
        code, text = create(body)
        # The API server refuses it before the policy: ephemeral containers
        # can't be in a pod template.
        self.assertEqual(code, 422, text)


class Updates(unittest.TestCase):
    """An update of a Job by the receiver changes the two annotations only."""

    @classmethod
    def setUpClass(cls):
        assert asyncio.run(_call("create_job", NAMESPACE, receiver_job("update-1")))[0]

    CASES = {
        "the time limit": ({"spec": {"activeDeadlineSeconds": 60}}, "spec"),
        "suspend": ({"spec": {"suspend": True}}, "spec"),
        "the TTL": ({"spec": {"ttlSecondsAfterFinished": 1}}, "spec"),
        "a new label": ({"metadata": {"labels": {"other": "x"}}}, "label"),
        "the name label": ({"metadata": {"labels": {"app.kubernetes.io/name": "other"}}}, "label"),
        "an owner reference": (
            {"metadata": {"ownerReferences": [{"apiVersion": "v1", "kind": "ConfigMap", "name": "x", "uid": "u"}]}},
            "owner reference",
        ),
        "a finalizer": ({"metadata": {"finalizers": ["example.org/hold"]}}, "finalizer"),
        "another annotation": ({"metadata": {"annotations": {"other": "x"}}}, "only the annotations"),
        "the run key": ({"metadata": {"annotations": {"workflow-agents/run-key": "other"}}}, "only the annotations"),
        "no fingerprint": ({"metadata": {"annotations": {"workflow-agents/fingerprint": None}}}, "only the annotations"),
    }

    def test_each_other_change_is_refused_by_the_policy(self):
        before = get_job("run-update-1")
        for what, (body, words) in self.CASES.items():
            with self.subTest(what):
                code, text = patch("run-update-1", body)
                self.assertEqual(code, 422, text)
                self.assertIn("workflow-run-job-updates", text)
                self.assertIn(words, text)
        after = get_job("run-update-1")
        self.assertEqual(after["spec"], before["spec"])
        self.assertEqual(after["metadata"].get("labels"), before["metadata"].get("labels"))
        self.assertEqual(after["metadata"].get("annotations"), before["metadata"].get("annotations"))

    def test_the_two_annotations_with_another_change_are_refused(self):
        code, text = patch(
            "run-update-1", {"metadata": {"annotations": {"workflow-agents/result": "{}", "other": "x"}}}
        )
        self.assertEqual(code, 422, text)
        self.assertNotIn("workflow-agents/result", get_job("run-update-1")["metadata"]["annotations"])


if __name__ == "__main__":
    unittest.main()
