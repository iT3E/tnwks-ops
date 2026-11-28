"""The pilot has no chat channel since 2026-11-19 (docs/apps/agent-platform.md,
"The pilot agent"): the owner moved its test bot to the main agent, for the
test run of main beside the host. Telegram gives the updates of a bot to one
poller, so the channel of the pilot is off in the same change as the token in
the Secret of main.

The static test holds the config. The container test starts the gateway of
the agent image with the config of the pilot, with no network and a fake
token in the env, and checks that it does not load the Telegram plugin and
starts no account. Without Docker it is skipped, except in CI, where it
fails.
"""

import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import yaml
from test_hand_written_rules import docker, scratch

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "kubernetes/apps/ai/agents"
PILOT = AGENTS / "pilot/agent"


def config(agent: str) -> dict:
    return json.loads((AGENTS / agent / "agent/config/openclaw.json").read_text())


def image() -> str:
    for d in yaml.safe_load_all((PILOT / "app/deployment.yaml").read_text()):
        if d and d["kind"] == "Deployment":
            return d["spec"]["template"]["spec"]["containers"][0]["image"]
    raise AssertionError("no Deployment")


def test_the_channel_of_the_pilot_is_off_and_the_one_of_main_is_on():
    pilot = config("pilot")["channels"]["telegram"]
    assert pilot["enabled"] is False
    # The rest of the block stays, so the way back is one revert.
    assert pilot["dmPolicy"] == "allowlist" and pilot["groupPolicy"] == "disabled"
    assert config("main")["channels"]["telegram"].get("enabled", True) is not False


def test_the_pilot_still_runs_its_scheduled_turn_with_no_channel():
    # OpenClaw 2026.11.10 has no heartbeat in the config: the doctor step
    # puts it in its own copy, and doctor makes the job of it, with no
    # delivery (target none).
    assert "heartbeat" not in config("pilot")["agents"]["defaults"]
    (doc,) = [d for d in yaml.safe_load_all((PILOT / "app/deployment.yaml").read_text()) if d]
    (doctor,) = [c for c in doc["spec"]["template"]["spec"]["initContainers"] if c["name"] == "doctor"]
    command = " ".join(doctor["command"])
    assert 'c.agents.defaults.heartbeat = {every: "24h", target: "none"' in command


def log_of(tool: str, name: str) -> str:
    out = subprocess.run([tool, "logs", name], capture_output=True, text=True, timeout=30, check=False)
    return out.stdout + out.stderr


def test_the_gateway_of_the_pilot_starts_no_telegram_account():
    tool = docker()
    base = scratch()
    (base / "state").mkdir()
    (base / "cfg").mkdir()
    (base / "cfg/openclaw.json").write_text((PILOT / "config/openclaw.json").read_text())
    # The state root has the mode of the claim: 1777 (OpenClaw 2026.11.10
    # refuses a world writable one with no sticky bit).
    (base / "state").chmod(0o1777)
    (base / "cfg").chmod(0o777)
    (base / "cfg/openclaw.json").chmod(0o644)
    name = f"pilot-channel-{uuid.uuid4().hex[:8]}"
    env = {
        "OPENCLAW_STATE_DIR": "/state", "OPENCLAW_CONFIG_PATH": "/cfg/openclaw.json",
        "OPENCLAW_CONFIG_READONLY": "1", "XDG_CACHE_HOME": "/state/.cache", "OPENCLAW_NO_AUTO_UPDATE": "1",
        "DO_NOT_TRACK": "1", "OPENCLAW_OFFLINE": "1", "OPENCLAW_GATEWAY_TOKEN": "lab-gateway-token-0123456789",
        "LITELLM_API_KEY": "lab", "BRAVE_API_KEY": "lab",
        # A token in the form of a bot token, not a real one: the env of the
        # pod still has the token of the pilot's Secret.
        "TELEGRAM_BOT_TOKEN": "123456:lab-not-a-real-token", "TELEGRAM_ALLOW_FROM": "123456",
    }
    args = [tool, "run", "-d", "--name", name, "--network", "none", "--read-only", "--tmpfs", "/tmp",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "-u", "1000:1000",
            "-v", f"{base / 'cfg'}:/cfg:ro", "-v", f"{base / 'state'}:/state"]
    for k, v in env.items():
        args += ["-e", f"{k}={v}"]
    subprocess.run([*args, image()], check=True, capture_output=True, timeout=120)
    try:
        logs = ""
        for _ in range(60):
            logs = log_of(tool, name)
            if "[gateway] ready" in logs:
                break
            time.sleep(2)
        assert "[gateway] ready" in logs, logs[-3000:]
        # Some seconds more, for a channel that starts after ready.
        time.sleep(8)
        logs = log_of(tool, name)
        (listening,) = [line for line in logs.splitlines() if "http server listening" in line]
        assert "telegram" not in listening
        assert "[telegram]" not in logs
        assert "ambient channel" not in logs
    finally:
        subprocess.run([tool, "rm", "-f", name], capture_output=True, timeout=60, check=False)
        shutil.rmtree(base, ignore_errors=True)
