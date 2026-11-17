"""The files of the cutover of the finance agent
(docs/apps/agent-platform-runbook.md, "The cutover of the finance agent"):
the inbox pod and the restore Job match the agent pod, the restore Job
refuses each member it must refuse before it writes, the values check
refuses a placeholder, and the steps of kustomization.yaml never meet in a
wrong order.

The container tests need Docker. Without it a test is skipped, except in
CI, where it fails. They run the scripts in a small Debian image with GNU
tar, as the agent image has; RESTORE_TEST_IMAGE runs them in another one
(the agent image, for a check by hand).
"""

import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml
from test_hand_written_rules import docker, scratch

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/ai/agents/finance/agent/app"
CONFIG = ROOT / "kubernetes/apps/ai/agents/finance/agent/config/openclaw.json"
IMAGE = os.environ.get(
    "RESTORE_TEST_IMAGE",
    "docker.io/library/python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90",
)


def doc(name: str) -> dict:
    (only,) = [d for d in yaml.safe_load_all((APP / name).read_text()) if d]
    return only


def pod(name: str) -> dict:
    return doc(name)["spec"]["template"]["spec"]


def listed() -> set[str]:
    resources = yaml.safe_load((APP / "kustomization.yaml").read_text())["resources"]
    return {r.removeprefix("./") for r in resources}


AGENT = pod("deployment.yaml")


@pytest.mark.parametrize("name", ["state-inbox.yaml", "restore-job.yaml"])
def test_the_helper_pods_run_as_the_agent_on_its_node_and_volume(name):
    spec = pod(name)
    (container,) = spec["containers"]
    assert container["image"] == AGENT["containers"][0]["image"]
    assert spec["nodeSelector"] == AGENT["nodeSelector"]
    assert spec["securityContext"] == AGENT["securityContext"]
    claims = {v["persistentVolumeClaim"]["claimName"] for v in spec["volumes"] if "persistentVolumeClaim" in v}
    assert claims == {"agent-state"}
    (data,) = [m for m in container["volumeMounts"] if m["name"] in {"state", "data"}]
    assert data["mountPath"] == "/home/node/data"
    assert spec["automountServiceAccountToken"] is False


@pytest.mark.parametrize("name", ["state-inbox.yaml", "restore-job.yaml"])
def test_the_helper_pods_hold_no_secret_and_no_network(name):
    spec = pod(name)
    text = (APP / name).read_text()
    assert "secretRef" not in text and "secretKeyRef" not in text
    assert not any("secret" in v for v in spec["volumes"])
    # The policy of the agent selects app.kubernetes.io/name: agent; the
    # helpers carry another name, so the default deny holds for them.
    labels = doc(name)["spec"]["template"]["metadata"]["labels"]
    assert labels["app.kubernetes.io/name"] != "agent"
    # Flux substitutes ${...} in this directory.
    assert "${" not in text


def test_the_secret_holds_each_value_the_config_names():
    names = set(re.findall(r"\$\{([A-Z0-9_]+)\}", CONFIG.read_text()))
    secret = set(doc("secret.sops.yaml")["stringData"])
    search = set(doc("secret-search.sops.yaml")["stringData"])
    assert names <= secret | search
    assert secret == {"OPENCLAW_GATEWAY_TOKEN", "LITELLM_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOW_FROM"}
    assert json.loads(CONFIG.read_text())["memory"]["search"]["remote"]["apiKey"]["id"] in secret


def test_the_steps_never_meet_in_a_wrong_order():
    now = listed()
    replicas = doc("deployment.yaml")["spec"]["replicas"]
    if "backup.yaml" in now:
        assert "pvc.yaml" in now
    if "deployment.yaml" in now:
        # Step 4: the inbox is out, and the Secrets are in.
        assert "state-inbox.yaml" not in now
        assert {"pvc.yaml", "backup.yaml", "secret.sops.yaml", "secret-search.sops.yaml"} <= now
    if replicas:
        # Step 5: the restore Job is out before the agent starts.
        assert "deployment.yaml" in now and "restore-job.yaml" not in now
    if "restore-job.yaml" in now:
        assert replicas == 0 and "state-inbox.yaml" not in now
        assert "SET-ME" not in (APP / "restore-job.yaml").read_text()


def test_the_volume_is_never_pruned():
    annotations = doc("pvc.yaml")["metadata"]["annotations"]
    assert annotations["kustomize.toolkit.fluxcd.io/prune"] == "disabled"


# The restore Job, in a container, on a scratch copy of the volume.

STATE = "start/openclaw-state-2026.11.3-20261117T000000Z.tar.gz"
VAULT = "vault-20261117T000000Z.tar"


def member(name, kind=tarfile.REGTYPE, data=b"x", mode=0o644, target=""):
    return name, kind, data, mode, target


GOOD_STATE = [member("./", tarfile.DIRTYPE, mode=0o755), member("./workspace", tarfile.DIRTYPE, mode=0o755),
              member("./workspace/AGENTS.md"), member("./workspace/scripts", tarfile.DIRTYPE, mode=0o755),
              member("./workspace/scripts/read.py", mode=0o755)]
GOOD_VAULT = [member("./", tarfile.DIRTYPE, mode=0o755), member("./a.md"), member("./notes", tarfile.DIRTYPE, mode=0o755),
              member("./notes/b.md")]


def write_tar(path, members, gz):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz" if gz else "w", format=tarfile.GNU_FORMAT) as tar:
        for name, kind, data, mode, target in members:
            info = tarfile.TarInfo(name)
            info.type, info.mode, info.linkname = kind, mode, target
            info.size = len(data) if kind == tarfile.REGTYPE else 0
            tar.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)


def run_restore(tmp, state=GOOD_STATE, vault=GOOD_VAULT, archive=STATE, vault_name=VAULT,
                checksum=True, vault_dir_file=False):
    data = tmp / "data"
    write_tar(data / "archives" / STATE, state, gz=True)
    digest = hashlib.sha256((data / "archives" / STATE).read_bytes()).hexdigest()
    if not checksum:
        digest = "0" * 64
    (data / "archives" / (STATE + ".sha256")).write_text(f"{digest}  {Path(STATE).name}\n")
    if vault is not None:
        write_tar(data / "archives/seed" / VAULT, vault, gz=False)
    (data / "state").mkdir()
    if vault_dir_file:
        (data / "vault").mkdir()
        (data / "vault/old.md").write_text("x")
    for path in [data, *data.rglob("*")]:
        path.chmod(0o777 if path.is_dir() else 0o666)
    script = tmp / "restore.sh"
    script.write_text(pod("restore-job.yaml")["containers"][0]["args"][0])
    script.chmod(0o644)
    result = subprocess.run(
        [docker(), "run", "--rm", "--network", "none", "-u", f"{os.getuid()}:{os.getgid()}",
         "-e", f"ARCHIVE={archive}", "-e", f"VAULT={vault_name}",
         "-v", f"{data}:/home/node/data", "-v", f"{script}:/s/restore.sh:ro",
         "--entrypoint", "sh", IMAGE, "/s/restore.sh"],
        capture_output=True, text=True, timeout=300, check=False)
    lines = [line for line in result.stdout.splitlines() if line.startswith("restore result=")]
    return result.returncode, lines, data


@pytest.fixture
def tmp():
    return scratch()


def test_the_restore_job_puts_the_copy_and_the_vault_in_place(tmp):
    code, lines, data = run_restore(tmp)
    assert code == 0, lines
    assert lines and lines[-1].startswith(f"restore result=ok archive={STATE} files=2 vault-notes=2 ")
    assert (data / "state/workspace/AGENTS.md").read_text() == "x"
    assert (data / "state").stat().st_mode & 0o777 == 0o700
    script = (data / "state/workspace/scripts/read.py").stat().st_mode
    assert script & 0o777 == 0o700  # the executable bit stays, for the owner only
    assert (data / "vault/notes/b.md").exists()
    assert not (data / "archives/seed" / VAULT).exists()
    assert len(list(data.glob("state.before-restore-*"))) == 1


REFUSED = {
    "link": ([*GOOD_STATE, member("./workspace/l", tarfile.SYMTYPE, target="/etc/passwd")], None,
             "member-type archive=state kinds=link"),
    "hard-link": ([*GOOD_STATE, member("./workspace/h", tarfile.LNKTYPE, target="./workspace/AGENTS.md")], None,
                  "member-type archive=state kinds=hard-link"),
    "device": ([*GOOD_STATE, member("./workspace/d", tarfile.CHRTYPE)], None, "member-type archive=state kinds=device"),
    "pipe": ([*GOOD_STATE, member("./workspace/p", tarfile.FIFOTYPE)], None, "member-type archive=state kinds=pipe"),
    "setuid": ([*GOOD_STATE, member("./workspace/s", mode=0o4755)], None, "setid-bit archive=state"),
    "setgid": ([*GOOD_STATE, member("./workspace/g", mode=0o2755)], None, "setid-bit archive=state"),
    "absolute": ([*GOOD_STATE, member("/etc/cron.d/x")], None, "absolute-path archive=state"),
    "dot-dot": ([*GOOD_STATE, member("./workspace/../x")], None, "dot-dot archive=state"),
    "credentials": ([*GOOD_STATE, member("./credentials/x.json")], None, "outside archive=state"),
    "env": ([*GOOD_STATE, member("./.env")], None, "outside archive=state"),
    "agent database": ([*GOOD_STATE, member("./agents/main/agent/openclaw-agent.sqlite")], None,
                       "outside archive=state"),
    "no workspace": ([member("./", tarfile.DIRTYPE, mode=0o755)], None, "no-workspace"),
    "vault link": (GOOD_STATE, [*GOOD_VAULT, member("./l.md", tarfile.SYMTYPE, target="/etc/passwd")],
                   "member-type archive=vault kinds=link"),
    "vault dot-dot": (GOOD_STATE, [*GOOD_VAULT, member("./../x.md")], "dot-dot archive=vault"),
    "vault setuid": (GOOD_STATE, [*GOOD_VAULT, member("./s.md", mode=0o4755)], "setid-bit archive=vault"),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_the_restore_job_refuses_before_it_writes(case, tmp):
    state, vault, reason = REFUSED[case]
    code, lines, data = run_restore(tmp, state=state, vault=GOOD_VAULT if vault is None else vault)
    assert code != 0
    assert lines == [f"restore result=failed reason={reason}"]
    assert not list(data.glob("state.before-restore-*"))
    assert not (data / "state/workspace").exists()
    assert not (data / "vault").exists()


@pytest.mark.parametrize(("kwargs", "reason"), [
    ({"checksum": False}, "checksum"),
    ({"archive": "daily/openclaw-backup-2026-11-16.tar.gz"}, "archive-name"),
    ({"archive": "start/openclaw-state-none.tar.gz"}, "missing"),
    ({"vault_name": "notes.tar"}, "vault-name"),
    ({"vault": None}, "vault-missing"),
    ({"vault_dir_file": True}, "vault-not-empty"),
])
def test_each_other_way_out_prints_its_line(kwargs, reason, tmp):
    code, lines, data = run_restore(tmp, **kwargs)
    assert code != 0
    assert lines == [f"restore result=failed reason={reason}"]
    assert not list(data.glob("state.before-restore-*"))


# The values check of the agent pod.

VALUES = ["OPENCLAW_GATEWAY_TOKEN", "LITELLM_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOW_FROM", "BRAVE_API_KEY"]


def run_values(env):
    (check,) = [c for c in AGENT["initContainers"] if c["name"] == "values-check"]
    args = [docker(), "run", "--rm", "--network", "none"]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    result = subprocess.run([*args, "--entrypoint", "sh", IMAGE, "-c", check["command"][2]],
                            capture_output=True, text=True, timeout=120, check=False)
    return result.returncode, result.stdout.strip()


def test_the_values_check_is_the_first_step_and_reads_both_secrets():
    first = AGENT["initContainers"][0]
    assert first["name"] == "values-check"
    assert {s["secretRef"]["name"] for s in first["envFrom"]} == {"agent-secret", "agent-search-secret"}
    secret = set(doc("secret.sops.yaml")["stringData"]) | set(doc("secret-search.sops.yaml")["stringData"])
    assert set(VALUES) == secret
    assert all(key in first["command"][2] for key in VALUES)


@pytest.mark.parametrize(("value", "reason"), [("pending", "placeholder"), ("", "empty")])
def test_the_values_check_refuses_a_placeholder(value, reason):
    env = dict.fromkeys(VALUES, "a-real-looking-value")
    env["TELEGRAM_BOT_TOKEN"] = value
    code, out = run_values(env)
    assert code != 0
    assert out == f"values-check result=failed key=TELEGRAM_BOT_TOKEN reason={reason}"
    assert "a-real-looking-value" not in out


def test_the_values_check_passes_with_every_value():
    code, out = run_values(dict.fromkeys(VALUES, "a-real-looking-value"))
    assert (code, out) == (0, "values-check result=ok")
