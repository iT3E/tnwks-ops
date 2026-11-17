"""One merge gives one rollout of an agent pod, and the official plugins step
says why it failed (docs/apps/agent-platform.md, "A start of the agent
pod"), for the pilot and the finance agent.

The config and the scripts of the pod are ConfigMaps with a hash of their
content in the name, made in the same Flux Kustomization as the Deployment:
a change of a file is a change of the pod template, and Reloader watches the
Secrets only. With the config in a Kustomization of its own and Reloader on
the ConfigMaps, one merge gave two rollouts on 2026-11-17.

The render test needs kustomize (or kubectl), and the step test Docker:
without them a test is skipped, except in CI, where it fails.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from test_hand_written_rules import docker

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "kubernetes/apps/ai/agents"
NAMES = ["pilot", "finance"]
NO_SUBSTITUTION = {"kustomize.toolkit.fluxcd.io/substitute": "disabled"}


def deployment(agent: str) -> dict:
    (doc,) = [d for d in yaml.safe_load_all((AGENTS / agent / "agent/app/deployment.yaml").read_text()) if d]
    return doc


def kustomization(agent: str) -> dict:
    return yaml.safe_load((AGENTS / agent / "agent/app/kustomization.yaml").read_text())


def mounted(pod: dict) -> set[str]:
    return {v["configMap"]["name"] for v in pod["volumes"] if "configMap" in v}


def init(pod: dict, name: str) -> dict:
    (only,) = [c for c in pod["initContainers"] if c["name"] == name]
    return only


@pytest.mark.parametrize("agent", NAMES)
def test_one_flux_kustomization_applies_the_pod_and_its_config(agent):
    docs = [d for d in yaml.safe_load_all((AGENTS / agent / "agent/ks.yaml").read_text()) if d]
    assert [d["metadata"]["name"] for d in docs] == [f"agents-{agent}-agent"]
    (ks,) = docs
    assert ks["spec"]["path"] == f"./kubernetes/apps/ai/agents/{agent}/agent/app"
    assert ks["spec"]["prune"] is True
    # The directory of the config is files only: no Kustomization builds it
    # alone, so no second apply can change a ConfigMap of the pod.
    assert not (AGENTS / agent / "agent/config/kustomization.yaml").exists()


@pytest.mark.parametrize("agent", NAMES)
def test_each_config_map_of_the_pod_has_a_hash_in_its_name(agent):
    k = kustomization(agent)
    assert "disableNameSuffixHash" not in k.get("generatorOptions", {})
    generated = {g["name"]: g for g in k["configMapGenerator"]}
    assert mounted(deployment(agent)["spec"]["template"]["spec"]) == set(generated)
    for g in generated.values():
        assert "disableNameSuffixHash" not in g.get("options", {})
    # The config and the scripts hold ${NAME} for the product and the shell:
    # Flux must not substitute them.
    for name in ("agent-config", "agent-scripts"):
        assert generated[name]["options"]["annotations"] == NO_SUBSTITUTION


@pytest.mark.parametrize("agent", NAMES)
def test_reloader_watches_the_secrets_only_and_the_pod_is_recreated(agent):
    d = deployment(agent)
    reloader = {k: v for k, v in d["metadata"]["annotations"].items() if "reloader.stakater.com" in k}
    assert reloader == {"secret.reloader.stakater.com/auto": "true"}
    assert not [k for k in d["spec"]["template"]["metadata"].get("annotations", {}) if "reloader" in k]
    # One process owns the state on one volume: the old pod stops first.
    assert d["spec"]["strategy"] == {"type": "Recreate"}
    assert d["spec"]["replicas"] <= 1


def kustomize() -> list[str]:
    if shutil.which("kustomize"):
        return ["kustomize", "build"]
    if shutil.which("kubectl"):
        return ["kubectl", "kustomize"]
    if os.environ.get("CI"):
        pytest.fail("kustomize or kubectl is needed to render the agent pods")
    pytest.skip("kustomize not installed")


@pytest.mark.parametrize("agent", NAMES)
def test_the_render_puts_the_names_with_the_hash_in_the_pod(agent):
    command = kustomize()
    result = subprocess.run([*command, "--load-restrictor=LoadRestrictionsNone",
                             str(AGENTS / agent / "agent/app")],
                            capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr[-4000:]
    objects = [o for o in yaml.safe_load_all(result.stdout) if o]
    maps = {o["metadata"]["name"]: o for o in objects if o["kind"] == "ConfigMap"}
    (pod,) = [o["spec"]["template"]["spec"] for o in objects if o["kind"] == "Deployment"]
    names = mounted(pod)
    assert names == set(maps)
    for name in names:
        assert re.fullmatch(r"agent-[a-z-]+-[a-z0-9]{10}", name), name
    for name in names:
        if name.startswith(("agent-config-", "agent-scripts-")):
            assert maps[name]["metadata"]["annotations"] == NO_SUBSTITUTION
    config = next(m for n, m in maps.items() if n.startswith("agent-config-"))
    assert "${TELEGRAM_ALLOW_FROM}" in config["data"]["openclaw.json"]


@pytest.mark.parametrize("agent", NAMES)
def test_the_official_plugins_step_runs_with_the_debug_flag_and_no_secret(agent):
    pod = deployment(agent)["spec"]["template"]["spec"]
    step = init(pod, "official-plugins")
    env = {e["name"]: e for e in step["env"]}
    assert env.pop("OPENCLAW_DEBUG") == {"name": "OPENCLAW_DEBUG", "value": "1"}
    # Otherwise the env of the gateway, as the archive step has it.
    assert list(env.values()) == init(pod, "archive")["env"]
    assert "envFrom" not in step
    assert all("valueFrom" not in e for e in step["env"])
    names = [c["name"] for c in pod["initContainers"]]
    assert names.index("official-plugins") < names.index("doctor")


def test_the_debug_flag_makes_the_cli_print_the_reason():
    """A failed install in the agent image prints only "The CLI command
    failed." With the env of the step it prints the reason too. The state
    directory is a file here, so the CLI fails at once, with no network."""
    tool = docker()
    pod = deployment("finance")["spec"]["template"]["spec"]
    step = init(pod, "official-plugins")
    image = step["image"]
    env = {e["name"]: e["value"] for e in step["env"]}
    env |= {"OPENCLAW_STATE_DIR": "/tmp/state", "OPENCLAW_CONFIG_PATH": "/tmp/openclaw.json",
            "XDG_CACHE_HOME": "/tmp/cache"}
    del env["OPENCLAW_CONFIG_READONLY"]
    script = ("echo '{}' > /tmp/openclaw.json && echo x > /tmp/state && "
              "node /app/openclaw.mjs plugins install @openclaw/brave-plugin --pin --no-enable --force")
    outputs = {}
    for debug in (False, True):
        values = dict(env) if debug else {k: v for k, v in env.items() if k != "OPENCLAW_DEBUG"}
        args = [tool, "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--tmpfs", "/tmp:uid=1000,gid=1000", "--user", "1000:1000"]
        for key, value in values.items():
            args += ["-e", f"{key}={value}"]
        result = subprocess.run([*args, "--entrypoint", "sh", image, "-c", script],
                                capture_output=True, text=True, timeout=300, check=False)
        assert result.returncode != 0
        outputs[debug] = (result.stdout + result.stderr).splitlines()
    assert "[openclaw] The CLI command failed." in outputs[False]
    assert not [line for line in outputs[False] if line.startswith("[openclaw] Reason:")]
    assert "[openclaw] The CLI command failed." in outputs[True]
    assert [line for line in outputs[True] if line.startswith("[openclaw] Reason: ")]
