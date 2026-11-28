"""The daily backup of the main agent with its config in a projected volume
(docs/apps/agent-platform.md, "State, archives and backups").

The config of main includes the group file of the owner. The kubelet makes
each file of a projected volume a link (`openclaw.json -> ..data/...`), and
the backup command of the product refuses an include file that is a link
(src/infra/backup-config-capture.ts at 2026.11.3), so each backup failed on
the cluster. The backup container runs on a copy of the config with regular
files.

The container test builds the config directory in the form of the kubelet,
starts the gateway of the agent image with it, and runs the backup container
of the Deployment beside it (one PID namespace and one loopback, as in the
pod), with no network and fake values. Without Docker it is skipped, except
in CI, where it fails.
"""

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import yaml
from test_hand_written_rules import docker, scratch

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "kubernetes/apps/ai/agents"
APP = AGENTS / "main/agent/app"
CONFIG = AGENTS / "main/agent/config/openclaw.json"
SCRIPTS = AGENTS / "pilot/agent/config/scripts"
GROUP = '{"-1000000000001": {"requireMention": false}}'
FAKE = {
    "OPENCLAW_GATEWAY_PASSWORD": "lab", "LITELLM_API_KEY": "lab", "BRAVE_API_KEY": "lab",
    "OPENCLAW_UI_ORIGIN": "https://openclaw.example.invalid", "OPENCLAW_INGRESS_CIDR": "192.0.2.0/24",
    "OPENCLAW_UI_ALLOW_USER": "operator@example.invalid",
    "TELEGRAM_BOT_TOKEN": "123456:lab-not-a-real-token", "TELEGRAM_ALLOW_FROM": "123456",
}


def pod() -> dict:
    for d in yaml.safe_load_all((APP / "deployment.yaml").read_text()):
        if d and d["kind"] == "Deployment":
            return d["spec"]["template"]["spec"]
    raise AssertionError("no Deployment")


def container(name: str) -> dict:
    p = pod()
    (only,) = [c for c in p["initContainers"] + p["containers"] if c["name"] == name]
    return only


def includes(node) -> set[str]:
    """Each file that the config includes, by its name."""
    found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$include":
                found |= {Path(v).name for v in ([value] if isinstance(value, str) else value)}
            else:
                found |= includes(value)
    elif isinstance(node, list):
        for value in node:
            found |= includes(value)
    return found


def test_the_backup_runs_on_a_copy_of_each_file_of_the_config():
    backup = container("backup")
    assert backup["command"][:2] == ["sh", "-c"]
    script = backup["command"][2]
    (copy,) = [line for line in script.splitlines() if line.strip().startswith("cp ")]
    copied = {Path(word).name for word in copy.split()[1:-1]}
    # The root file and each include, so a new include breaks this test
    # before it breaks the backup.
    assert copied == {"openclaw.json"} | includes(json.loads(CONFIG.read_text()))
    assert 'OPENCLAW_CONFIG_PATH="$d/openclaw.json" exec sh /scripts/daily-backup.sh' in script
    assert "${" not in script
    mounts = {m["name"]: m for m in backup["volumeMounts"]}
    assert mounts["config"]["readOnly"] is True and mounts["tmp"]["mountPath"] == "/tmp"


def projected(base: Path) -> Path:
    """A config directory in the form that the kubelet makes for a projected
    volume: a dated directory with the files, a link ..data to it, and a
    link for each file."""
    config = base / "config"
    dated = config / "..2026_11_19_07_00_00.000000001"
    dated.mkdir(parents=True)
    (dated / "openclaw.json").write_text(CONFIG.read_text())
    (dated / "telegram-groups.json").write_text(GROUP)
    (config / "..data").symlink_to(dated.name)
    for name in ("openclaw.json", "telegram-groups.json"):
        (config / name).symlink_to(f"..data/{name}")
        (dated / name).chmod(0o644)
    for path in (config, dated):
        path.chmod(0o755)
    return config


def logs(tool: str, name: str) -> str:
    out = subprocess.run([tool, "logs", name], capture_output=True, text=True, timeout=30, check=False)
    return out.stdout + out.stderr


def wait_for(tool: str, name: str, pattern: str, seconds: int) -> str:
    text = ""
    for _ in range(seconds // 2):
        text = logs(tool, name)
        if re.search(pattern, text):
            return text
        time.sleep(2)
    raise AssertionError(f"{pattern} not in the log of {name}:\n{text[-3000:]}")


def run(tool: str, name: str, extra: list[str], env: dict, image: str, command: list[str] | None, mounts):
    args = [tool, "run", "-d", "--name", name, "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "-u", "1000:1000", *extra]
    for k, v in env.items():
        args += ["-e", f"{k}={v}"]
    for src, dst, ro in mounts:
        args += ["-v", f"{src}:{dst}{':ro' if ro else ''}"]
    if command:
        args += ["--entrypoint", command[0], image, *command[1:]]
    else:
        args += [image]
    subprocess.run(args, check=True, capture_output=True, timeout=120)


def test_the_backup_passes_with_the_config_in_a_projected_volume():
    tool = docker()
    base = scratch()
    config = projected(base)
    data, tmp, scripts = base / "data", base / "tmp", base / "scripts"
    for path in (data, tmp, scripts):
        path.mkdir()
        # The mode of the claim root: OpenClaw 2026.11.10 refuses a world
        # writable ancestor with no sticky bit.
        path.chmod(0o1777)
    shutil.copy(SCRIPTS / "daily-backup.sh", scripts / "daily-backup.sh")
    (scripts / "daily-backup.sh").chmod(0o644)
    agent, backup = container("agent"), container("backup")
    image = os.environ.get("RESTORE_TEST_IMAGE", agent["image"])
    env = {e["name"]: e["value"] for e in agent["env"] if "value" in e} | FAKE
    gateway, sidecar = f"main-gw-{uuid.uuid4().hex[:8]}", f"main-backup-{uuid.uuid4().hex[:8]}"
    mounts = [(config, "/home/node/config", True), (data, "/home/node/data", False), (tmp, "/tmp", False)]
    try:
        run(tool, gateway, ["--network", "none"], env, image, None, mounts)
        wait_for(tool, gateway, r"\[gateway\] ready", 180)
        benv = {e["name"]: e["value"] for e in backup["env"] if "value" in e} | FAKE
        run(tool, sidecar, ["--pid", f"container:{gateway}", "--network", f"container:{gateway}"], benv, image,
            backup["command"], [*mounts, (scripts, "/scripts", True)])
        out = wait_for(tool, sidecar, r"daily-backup result=", 300)
        assert "daily-backup result=ok" in out, out[-3000:]
        assert "Cannot capture required config file" not in out
        # The archive directory is 0700 of the user of the container, so the
        # test reads the list of the archive in a container too.
        listing = subprocess.run(
            [tool, "run", "--rm", "--network", "none", "-u", "1000:1000", "-v", f"{data}:/home/node/data:ro",
             "--entrypoint", "sh", image, "-c",
             "tar -tvzf /home/node/data/archives/daily/openclaw-backup-*.tar.gz"],
            capture_output=True, text=True, timeout=120, check=False)
        assert listing.returncode == 0, listing.stderr[-2000:]
        copied = [line for line in listing.stdout.splitlines()
                  if "/payload/posix/tmp/backup-config/" in line and not line.rstrip().endswith("/")]
        assert {line.rsplit("/", 1)[1] for line in copied} == {"openclaw.json", "telegram-groups.json"}
        assert all(line.startswith("-") for line in copied), copied
    finally:
        for name in (sidecar, gateway):
            subprocess.run([tool, "rm", "-f", name], capture_output=True, timeout=60, check=False)
        shutil.rmtree(base, ignore_errors=True)
