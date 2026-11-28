"""The state of an agent pod with OpenClaw 2026.11.10 (docs/apps/agent-platform.md,
"A start of the agent pod" and "The pilot agent"): the sticky bit on the root
of the state volume, and the lock step, which recovers the state lock of an
earlier pod that did not stop cleanly.

The lock test starts the product of the agent image (deployment.yaml, by
digest) with the config of the pilot in docker "pods": a container per pod
that holds its PID namespace and its host name, joined by each container of
that pod, as with shareProcessNamespace. It needs Docker: without it the
test is skipped, except in CI, where it fails.
"""

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml
from test_hand_written_rules import docker, scratch

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "kubernetes/apps/ai/agents"
NAMES = ["pilot", "finance", "main"]
SCRIPT = AGENTS / "pilot/agent/config/scripts/recover-lock.sh"
PROVISIONER = ROOT / "kubernetes/apps/kube-system/local-path-provisioner"


def deployment(agent: str) -> dict:
    (doc,) = [d for d in yaml.safe_load_all((AGENTS / agent / "agent/app/deployment.yaml").read_text()) if d]
    return doc


def pod(agent: str) -> dict:
    return deployment(agent)["spec"]["template"]["spec"]


def init(spec: dict, name: str) -> dict:
    (only,) = [c for c in spec["initContainers"] if c["name"] == name]
    return only


@pytest.mark.parametrize("agent", NAMES)
def test_the_lock_step_runs_before_doctor_with_no_secret(agent):
    spec = pod(agent)
    names = [c["name"] for c in spec["initContainers"]]
    assert names.index("archive") < names.index("recover-lock") < names.index("doctor")
    step = init(spec, "recover-lock")
    assert step["command"] == ["sh", "/scripts/recover-lock.sh"]
    assert step["image"] == init(spec, "doctor")["image"]
    assert "envFrom" not in step
    assert all("valueFrom" not in e for e in step["env"])
    env = {e["name"]: e["value"] for e in step["env"]}
    assert env["OPENCLAW_STATE_DIR"] == "/home/node/data/state"
    mounts = {m["name"]: m for m in step["volumeMounts"]}
    # The volume at its root, as the gateway has it (no subPath on local-path).
    assert mounts["state"] == {"name": "state", "mountPath": "/home/node/data"}
    assert mounts["scripts"]["readOnly"] and mounts["config"]["readOnly"]


@pytest.mark.parametrize("agent", NAMES)
def test_the_pod_lets_the_gateway_give_its_lock_back(agent):
    doc = deployment(agent)
    spec = doc["spec"]["template"]["spec"]
    # The gateway drains work for up to 330 s after SIGTERM, then has a
    # margin of 5 s to exit; a SIGKILL before that leaves the lock.
    assert spec["terminationGracePeriodSeconds"] >= 335
    # One PID namespace: the product sees the end of a gateway of this pod.
    assert spec["shareProcessNamespace"] is True
    assert doc["spec"]["strategy"]["type"] == "Recreate"
    assert spec["nodeSelector"] == sticky_job()["spec"]["template"]["spec"]["nodeSelector"]


@pytest.mark.parametrize("agent", NAMES)
def test_the_scripts_have_the_lock_step_and_the_pod_waits_for_the_sticky_bit(agent):
    files = next(g for g in yaml.safe_load((AGENTS / agent / "agent/app/kustomization.yaml").read_text())
                 ["configMapGenerator"] if g["name"] == "agent-scripts")["files"]
    (entry,) = [f for f in files if f.startswith("recover-lock.sh=")]
    assert (AGENTS / agent / "agent/app" / entry.split("=", 1)[1]).resolve() == SCRIPT
    (ks,) = [d for d in yaml.safe_load_all((AGENTS / agent / "agent/ks.yaml").read_text()) if d]
    assert {"name": "local-path-agent-state"} in ks["spec"]["dependsOn"]


def sticky_job() -> dict:
    (doc,) = [d for d in yaml.safe_load_all((PROVISIONER / "agent-state/job.yaml").read_text()) if d]
    return doc


def test_the_sticky_bit_reaches_each_agent_state_volume():
    job = sticky_job()
    spec = job["spec"]["template"]["spec"]
    (volume,) = spec["volumes"]
    root = volume["hostPath"]["path"]
    # The root of the default class on that node.
    (release,) = [d for d in yaml.safe_load_all((PROVISIONER / "app/helmrelease.yaml").read_text()) if d]
    values = release["spec"]["values"]
    paths = {e["node"]: e["paths"] for e in values["storageClassConfigs"]["local-path"]["nodePathMap"]}
    assert paths[spec["nodeSelector"]["kubernetes.io/hostname"]] == [root]
    (container,) = spec["containers"]
    script = container["command"][-1]
    assert "for d in /storage/pvc-*_agents-*_agent-state; do" in script and 'chmod +t "$d"' in script
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert spec["automountServiceAccountToken"] is False
    # New volumes: the setup script of the provisioner.
    setup = values["configmap"]["setup"]
    assert '*/pvc-*_agents-*_agent-state) chmod 1777 "${absolutePath}" ;;' in setup
    # Flux recreates the Job on a change (it is immutable), and waits for it.
    docs = [d for d in yaml.safe_load_all((PROVISIONER / "ks.yaml").read_text()) if d]
    (ks,) = [d for d in docs if d["metadata"]["name"] == "local-path-agent-state"]
    assert ks["spec"]["force"] is True and ks["spec"]["wait"] is True


def test_the_volume_names_match_the_glob():
    """The provisioner names a directory pvc-<uid>_<namespace>_<claim>."""
    import fnmatch

    assert fnmatch.fnmatch("pvc-ccc14215-9707-4514-9bfe-d882cfc44942_agents-pilot_agent-state",
                           "pvc-*_agents-*_agent-state")
    assert not fnmatch.fnmatch("pvc-1f33eb71-00a3-4274-abcb-6b4c761f73d9_agents-finance_openshell-data-openshell-0",
                               "pvc-*_agents-*_agent-state")


# The lock test

ENV = {
    "OPENCLAW_STATE_DIR": "/home/node/data/state", "OPENCLAW_CONFIG_PATH": "/home/node/config/openclaw.json",
    "OPENCLAW_CONFIG_READONLY": "1", "XDG_CACHE_HOME": "/home/node/data/state/.cache",
    "OPENCLAW_NO_AUTO_UPDATE": "1", "DO_NOT_TRACK": "1", "OPENCLAW_OFFLINE": "1",
    "OPENCLAW_GATEWAY_TOKEN": "lab-gateway-token-0123456789", "LITELLM_API_KEY": "lab", "BRAVE_API_KEY": "lab",
    "TELEGRAM_BOT_TOKEN": "123456:lab", "TELEGRAM_ALLOW_FROM": "1",
}


class Lab:
    def __init__(self, tool: str, base: Path, image: str):
        self.tool, self.base, self.image = tool, base, image
        self.names: list[str] = []
        self.env = [a for k, v in ENV.items() for a in ("-e", f"{k}={v}")]
        self.mounts = ["-v", f"{base}/data:/home/node/data", "-v", f"{base}/cfg:/home/node/config:ro",
                       "-v", f"{base}/scripts:/scripts:ro", "--tmpfs", "/tmp:uid=1000,gid=1000,mode=2700",
                       "--tmpfs", "/home/node/.config:uid=1000,gid=1000"]

    def docker(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run([self.tool, *args], capture_output=True, text=True, timeout=600, check=False)
        if check:
            assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]
        return result

    def pod(self, name: str) -> str:
        self.names.append(name)
        self.docker("run", "-d", "--quiet", "--name", name, "--hostname", name, "--network", "none",
                    "--entrypoint", "sleep", self.image, "infinity")
        return name

    def in_pod(self, pod: str) -> list[str]:
        return ["--pid", f"container:{pod}", "--hostname", pod, "--network", "none", "--cap-drop", "ALL"]

    def gateway(self, pod: str) -> str:
        name = f"{pod}-gateway"
        self.names.append(name)
        self.docker("run", "-d", "--quiet", "--name", name, *self.in_pod(pod), *self.env, *self.mounts, self.image)
        probe = ("require('http').get('http://127.0.0.1:18789/healthz', r => process.exit(r.statusCode < 400 ? 0 : 1))"
                 ".on('error', () => process.exit(1))")
        deadline = time.monotonic() + 240
        while self.docker("exec", name, "node", "-e", probe, check=False).returncode != 0:
            assert time.monotonic() < deadline, self.docker("logs", name, check=False).stderr[-3000:]
            time.sleep(3)
        return name

    def kill(self, name: str) -> None:
        self.docker("kill", "--signal", "KILL", name)
        self.docker("rm", name)

    def step(self, pod: str) -> list[str]:
        result = self.docker("run", "--rm", *self.in_pod(pod), *self.env, *self.mounts,
                             "--entrypoint", "sh", self.image, "/scripts/recover-lock.sh", check=False)
        return [*(result.stdout + result.stderr).splitlines(), f"exit={result.returncode}"]

    def clean(self) -> None:
        for name in reversed(self.names):
            self.docker("rm", "-f", name, check=False)


@pytest.fixture
def lab():
    tool = docker()
    base = scratch()
    for sub, mode in (("data", 0o1777), ("cfg", 0o755), ("scripts", 0o755)):
        (base / sub).mkdir()
        (base / sub).chmod(mode)
    shutil.copy(AGENTS / "pilot/agent/config/openclaw.json", base / "cfg/openclaw.json")
    shutil.copy(SCRIPT, base / "scripts/recover-lock.sh")
    for path in (base / "cfg/openclaw.json", base / "scripts/recover-lock.sh"):
        path.chmod(0o644)
    lab = Lab(tool, base, init(pod("pilot"), "recover-lock")["image"])
    try:
        yield lab
    finally:
        lab.clean()


def test_the_lock_step_recovers_the_lock_of_a_dead_pod_only(lab):
    tag = uuid.uuid4().hex[:8]
    first = lab.pod(f"lock-a-{tag}")
    # A clean volume: nothing to do.
    assert lab.step(first)[-2:] == ["recover-lock result=none holders=0", "exit=0"]
    # A live gateway of another pod: it renews its lock, so the step waits
    # once and then refuses, and the gateway keeps running.
    gateway = lab.gateway(first)
    other = lab.pod(f"lock-b-{tag}")
    out = lab.step(other)
    assert out[-1] == "exit=1" and out[-2].startswith("recover-lock result=failed reason=renewed-"), out
    assert lab.docker("inspect", "-f", "{{.State.Running}}", gateway).stdout.strip() == "true"
    # The gateway of the first pod is killed: in the same pod, the step
    # leaves the lock to the product, which starts again.
    lab.kill(gateway)
    assert lab.step(first)[-2:] == ["recover-lock result=same-pod holders=3", "exit=0"]
    lab.kill(lab.gateway(first))
    # The pod is gone: the step of the next pod recovers the lock (after it
    # is 60 s old), and its gateway starts.
    lab.docker("rm", "-f", first)
    third = lab.pod(f"lock-c-{tag}")
    out = lab.step(third)
    assert out[-2:] == ["recover-lock result=recovered holders=3", "exit=0"], out
    lab.gateway(third)
