"""The seed of the finance share, step F of task 3.36
(docs/apps/agent-platform.md, "The seed of the finance share"): a one-time
Job that scans the copy of the vault on the state volume of the finance
agent with the secret scanner of CI, and copies it to the share only when
the owner decided each finding.

The static tests hold the form of the Job: the scanner and its config are
the ones of CI, the source is read only, the pod has no network and no
secret, and it runs on the node of the state volume. The container tests
run the three steps of the Job (the scan image, then the script in the agent
image) on a fake vault with a fake token, a config folder of the notes app,
a trash, a .git folder, links and a pipe. The token is made at each run, so
no secret is in git. Without Docker they are skipped, except in CI, where
they fail. The render test needs kustomize (or kubectl).
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import string
import subprocess
from pathlib import Path

import pytest
import yaml
from test_hand_written_rules import docker, scratch

ROOT = Path(__file__).resolve().parents[2]
FINANCE = ROOT / "kubernetes/apps/ai/agents/finance"
SEED = FINANCE / "vault-seed"
APP = SEED / "app"
ID = re.compile(r"^[0-9a-f]{16}$")


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def job() -> dict:
    (only,) = docs(APP / "job.yaml")
    return only


def pod() -> dict:
    return job()["spec"]["template"]["spec"]


def container(name: str) -> dict:
    p = pod()
    (only,) = [c for c in p.get("initContainers", []) + p["containers"] if c["name"] == name]
    return only


def agent_pod() -> dict:
    (d,) = docs(FINANCE / "agent/app/deployment.yaml")
    return d["spec"]["template"]["spec"]


def test_the_seed_is_its_own_kustomization_after_the_share_and_the_agent():
    # Listed for the run, and out of the list after it (step 3 of the
    # runbook): both are right, and the files stay in git.
    resources = yaml.safe_load((FINANCE / "kustomization.yaml").read_text())["resources"]
    assert {r for r in resources if "seed" in r} <= {"./vault-seed/ks.yaml"}
    (ks,) = docs(SEED / "ks.yaml")
    assert ks["metadata"]["name"] == "agents-finance-vault-seed"
    assert ks["spec"]["targetNamespace"] == "agents-finance"
    assert ks["spec"]["path"] == "./kubernetes/apps/ai/agents/finance/vault-seed/app"
    assert ks["spec"]["prune"] is True
    assert {d["name"] for d in ks["spec"]["dependsOn"]} == {"agents-finance-vault-share", "agents-finance-agent"}
    # Flux substitutes ${...} in this tree: the Job has none, and the
    # ConfigMap of the script (which has some, for node) is out of it.
    assert "${" not in (APP / "job.yaml").read_text()
    k = yaml.safe_load((APP / "kustomization.yaml").read_text())
    assert k["resources"] == ["./job.yaml"]
    (generated,) = k["configMapGenerator"]
    assert generated["options"]["annotations"] == {"kustomize.toolkit.fluxcd.io/substitute": "disabled"}
    files = dict(f.split("=", 1) for f in generated["files"])
    assert set(files) == {"seed.mjs", "betterleaks.toml"}
    # The config of the scan is the one of CI, not a copy.
    assert (APP / files["betterleaks.toml"]).resolve() == ROOT / ".betterleaks.toml"


def test_the_job_runs_once_with_no_retry_and_a_name_for_each_run():
    j = job()
    assert re.fullmatch(r"vault-seed(-[0-9]+)?", j["metadata"]["name"])
    assert j["spec"]["backoffLimit"] == 0
    assert j["spec"]["activeDeadlineSeconds"] <= 3600
    assert pod()["restartPolicy"] == "Never"


def test_the_pod_has_no_secret_no_token_and_no_network():
    p = pod()
    text = json.dumps(job())
    assert "secretRef" not in text and "secretKeyRef" not in text and '"secret"' not in text
    assert p["automountServiceAccountToken"] is False
    assert p["securityContext"] == {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000,
                                    "seccompProfile": {"type": "RuntimeDefault"}}
    for c in p["initContainers"] + p["containers"]:
        assert c["securityContext"] == {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]}}, c["name"]
        assert "envFrom" not in c
        assert re.search(r"@sha256:[0-9a-f]{64}$", c["image"]), c["name"]
    # The only policy of the namespace that selects the pod is the default
    # deny, which allows nothing: the pod has no network, not even DNS.
    labels = job()["spec"]["template"]["metadata"]["labels"]
    assert labels["agents.home.arpa/dns"] == "none"
    selecting = []
    for path in FINANCE.rglob("*.yaml"):
        for d in docs(path):
            if d.get("kind") == "CiliumNetworkPolicy":
                selector = d["spec"]["endpointSelector"]
            elif d.get("kind") == "NetworkPolicy":
                selector = d["spec"]["podSelector"]
            else:
                continue
            if selects(selector, labels):
                selecting.append(d)
    assert [d["metadata"]["name"] for d in selecting] == ["default-deny"]
    (deny,) = selecting
    assert set(deny["spec"]["policyTypes"]) == {"Ingress", "Egress"}
    assert "ingress" not in deny["spec"] and "egress" not in deny["spec"]


def selects(selector: dict, labels: dict) -> bool:
    if any(labels.get(k) != v for k, v in (selector.get("matchLabels") or {}).items()):
        return False
    for e in selector.get("matchExpressions") or []:
        key, op, values = e["key"], e["operator"], e.get("values", [])
        ok = {"Exists": key in labels, "DoesNotExist": key not in labels,
              "In": labels.get(key) in values, "NotIn": labels.get(key) not in values}[op]
        if not ok:
            return False
    return True


def test_the_selector_rule_of_the_network_test_can_fail():
    assert selects({}, {"a": "b"})
    assert not selects({"matchLabels": {"app.kubernetes.io/name": "agent"}}, {"app.kubernetes.io/name": "vault-seed"})
    assert not selects({"matchExpressions": [{"key": "agents.home.arpa/dns", "operator": "DoesNotExist"}]},
                       {"agents.home.arpa/dns": "none"})


def test_the_job_runs_on_the_node_of_the_state_volume_with_the_agent_image():
    assert pod()["nodeSelector"] == agent_pod()["nodeSelector"]
    agent_image = agent_pod()["containers"][0]["image"]
    assert container("report")["image"] == container("copy")["image"] == agent_image


def test_the_source_is_read_only_and_only_the_copy_step_has_the_share():
    volumes = {v["name"]: v for v in pod()["volumes"]}
    assert volumes["source"] == {"name": "source",
                                 "persistentVolumeClaim": {"claimName": "agent-state", "readOnly": True}}
    assert volumes["share"] == {"name": "share", "persistentVolumeClaim": {"claimName": "vault-rw"}}
    assert volumes["work"]["emptyDir"]["medium"] == "Memory"
    assert set(volumes) == {"source", "share", "work", "seed"}
    for c in pod()["initContainers"] + pod()["containers"]:
        mounts = {m["name"]: m for m in c["volumeMounts"]}
        # At the root of the volume: a subPath of a local-path volume is an
        # empty directory on this cluster (talos/wsl/README.md).
        assert mounts["source"] == {"name": "source", "mountPath": "/state", "readOnly": True}
        assert ("share" in mounts) == (c["name"] == "copy"), c["name"]
    assert [c["name"] for c in pod()["initContainers"]] == ["scan", "report"]
    assert [c["name"] for c in pod()["containers"]] == ["copy"]


def test_no_mount_of_a_claim_has_a_sub_path():
    # On this cluster the kubelet can't reach a local-path directory, so a
    # subPath of a claim would be an empty directory that it makes as root
    # (talos/wsl/README.md): the source would look empty.
    claims = {v["name"] for v in pod()["volumes"] if "persistentVolumeClaim" in v}
    assert claims == {"source", "share"}
    for c in pod()["initContainers"] + pod()["containers"]:
        for m in c["volumeMounts"]:
            if m["name"] in claims:
                assert "subPath" not in m and "subPathExpr" not in m, (c["name"], m["name"])


def test_the_scan_is_the_scanner_of_ci_with_no_value_in_its_output():
    scan = container("scan")
    rev = None
    for repo in yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"]:
        if repo["repo"].endswith("/betterleaks/betterleaks"):
            rev = repo["rev"]
    assert rev and scan["image"].startswith(f"ghcr.io/betterleaks/betterleaks:{rev}@sha256:")
    args = scan["args"]
    # The scan reads the folder of the vault only, the same folder as the
    # other two steps, and not the rest of the state volume.
    (source,) = {e["value"] for e in container("report")["env"] if e["name"] == "SOURCE"}
    assert source == "/state/vault"
    assert args[:2] == ["dir", source]
    assert sum(not a.startswith("-") and a.startswith("/") and a not in
               (args[args.index("--config") + 1], args[args.index("--report-path") + 1]) for a in args) == 1
    assert args[args.index("--config") + 1] == "/seed/betterleaks.toml"
    assert args[args.index("--report-path") + 1] == "/work/report.json"
    assert "--redact" in args
    for flag in ("-v", "--verbose", "--validation", "--follow-symlinks", "--unsafe-auto-load-config"):
        assert flag not in args, flag


def test_the_decisions_are_ids_and_the_same_in_both_steps():
    report, copy = container("report")["env"], container("copy")["env"]
    assert report == copy
    assert [e["name"] for e in report] == ["SOURCE", "SKIP", "TAKE"]
    for e in report[1:]:
        ids = e["value"].split()
        assert all(ID.match(i) for i in ids), e["name"]


def kustomize() -> list[str]:
    if shutil.which("kustomize"):
        return ["kustomize", "build"]
    if shutil.which("kubectl"):
        return ["kubectl", "kustomize"]
    if os.environ.get("CI"):
        pytest.fail("kustomize or kubectl is needed to render the seed")
    pytest.skip("kustomize not installed")


def test_the_render_holds_the_config_of_ci_and_the_script():
    result = subprocess.run([*kustomize(), "--load-restrictor=LoadRestrictionsNone", str(APP)],
                            capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr[-4000:]
    objects = [o for o in yaml.safe_load_all(result.stdout) if o]
    (cm,) = [o for o in objects if o["kind"] == "ConfigMap"]
    assert cm["data"]["betterleaks.toml"] == (ROOT / ".betterleaks.toml").read_text()
    assert cm["data"]["seed.mjs"] == (APP / "seed.mjs").read_text()
    (j,) = [o for o in objects if o["kind"] == "Job"]
    assert j["spec"]["template"]["spec"]["volumes"][-1]["configMap"]["name"] == cm["metadata"]["name"]


# The container tests: the steps of the Job on a fake vault.

def token() -> str:
    letters = string.ascii_letters + string.digits
    return "ghp_" + "".join(secrets.choice(letters) for _ in range(36))


def fake_vault(base: Path) -> tuple[Path, str]:
    """A vault with three notes, an image, an empty folder, a note with a
    token, a config folder of the notes app with a token, a trash, a .git
    folder, a link out and a link in, and a pipe; in the folder vault/ of a
    fake state volume, beside a workspace with a token of its own."""
    src = base / "state/vault"
    value = token()
    (base / "state/workspace").mkdir(parents=True)
    (base / "state/workspace/other.md").write_text(f"# Not the vault\n\nkey {token()}\n")
    (base / "state/workspace/other.md").chmod(0o644)
    (base / "state/workspace").chmod(0o755)
    (src / "Notes/sub").mkdir(parents=True)
    (src / "Empty").mkdir()
    (src / "Notes/a.md").write_text("# A\n\nA plain note.\n")
    (src / "Notes/sub/b.md").write_text("# B\n\nAnother note.\n")
    (src / "Notes/img.png").write_bytes(bytes(range(256)) * 4)
    (src / "Notes/token.md").write_text(f"# Keys\n\nthe github token {value}\n")
    (src / ".obsidian/plugins/x").mkdir(parents=True)
    (src / ".obsidian/plugins/x/data.json").write_text(json.dumps({"token": token()}) + "\n")
    (src / ".trash").mkdir()
    (src / ".trash/old.md").write_text("# Old\n")
    (src / ".git").mkdir()
    (src / ".git/config").write_text("[core]\n")
    (src / "link-out").symlink_to("/etc/passwd")
    (src / "Notes/link.md").symlink_to("a.md")
    os.mkfifo(src / "pipe")
    os.utime(src / "Notes/a.md", (1_700_000_000, 1_700_000_000))
    for path in [src, *src.rglob("*")]:
        if not path.is_symlink() and not stat_is_fifo(path):
            path.chmod(0o755 if path.is_dir() else 0o644)
    return src, value


def stat_is_fifo(path: Path) -> bool:
    import stat
    return stat.S_ISFIFO(os.lstat(path).st_mode)


def tree(root: Path) -> dict[str, str]:
    """Each entry under a folder: its kind and, for a file, its hash."""
    out = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out[rel] = "link:" + os.readlink(path)
        elif stat_is_fifo(path):
            out[rel] = "fifo"
        elif path.is_dir():
            out[rel] = "dir"
        else:
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class Run:
    def __init__(self, code: int, report: str, copy: str, scan: str):
        self.code, self.report, self.copy, self.scan = code, report, copy, scan

    def findings(self) -> list[dict]:
        out = []
        for line in self.report.splitlines():
            if m := re.fullmatch(r"vault-seed finding id=(\S+) rule=(\S+) line=(\d+) decision=(\S+) file=(.*)", line):
                out.append({"id": m[1], "rule": m[2], "line": int(m[3]), "decision": m[4], "file": json.loads(m[5])})
        return out

    def result(self) -> dict[str, str]:
        (line,) = self.copy.splitlines()
        assert line.startswith("vault-seed result=")
        return dict(part.split("=", 1) for part in line.split()[1:])


def run_job(base: Path, skip: str = "", take: str = "") -> Run:
    """The three steps of the Job, as its pod runs them: the scan image, then
    the script in the agent image, each with a read-only root, no network
    and no capability, the source read only."""
    tool = docker()
    seed = base / "seed"
    seed.mkdir(exist_ok=True)
    shutil.copy(APP / "seed.mjs", seed / "seed.mjs")
    shutil.copy(ROOT / ".betterleaks.toml", seed / "betterleaks.toml")
    work = base / "work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    share = base / "share"
    share.mkdir(exist_ok=True)
    for path in (seed, work, share):
        path.chmod(0o777)
    common = [tool, "run", "--rm", "--quiet", "--network", "none", "--read-only", "--cap-drop", "ALL",
              "--security-opt", "no-new-privileges", "-u", f"{os.getuid()}:{os.getgid()}",
              "-v", f"{base / 'state'}:/state:ro", "-v", f"{work}:/work", "-v", f"{seed}:/seed:ro"]
    decisions = ["-e", f"SKIP={skip}", "-e", f"TAKE={take}"]
    scan = container("scan")
    s = subprocess.run([*common, "-e", "HOME=/work", scan["image"], *scan["args"]],
                       capture_output=True, text=True, timeout=300, check=False)
    assert s.returncode == 0, s.stdout + s.stderr
    image = container("copy")["image"]
    r = subprocess.run([*common, *decisions, "--entrypoint", "node", image, "/seed/seed.mjs", "report"],
                       capture_output=True, text=True, timeout=300, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    c = subprocess.run([*common, *decisions, "-v", f"{share}:/share", "--entrypoint", "node", image,
                        "/seed/seed.mjs", "copy"], capture_output=True, text=True, timeout=300, check=False)
    return Run(c.returncode, r.stdout + r.stderr, c.stdout + c.stderr, s.stdout + s.stderr)


@pytest.fixture
def vault():
    base = scratch()
    src, value = fake_vault(base)
    yield base, src, value
    shutil.rmtree(base, ignore_errors=True)


def the_finding(run: Run) -> dict:
    (f,) = [f for f in run.findings() if f["file"] == "Notes/token.md"]
    return f


def test_a_finding_with_no_decision_stops_the_copy(vault):
    base, src, value = vault
    before = tree(src)
    run = run_job(base)
    assert run.code != 0
    assert run.copy.strip() == "vault-seed result=failed reason=findings undecided=1"
    finding = the_finding(run)
    assert ID.match(finding["id"]) and finding["decision"] == "none" and finding["rule"] == "github-pat"
    # The token of the config folder is found, and needs no decision.
    (config,) = [f for f in run.findings() if f["file"].startswith(".obsidian/")]
    assert config["decision"] == "left-out-config"
    assert "vault-seed report=ok findings=2 undecided=1" in run.report
    # The rest of the state volume is not scanned and not named.
    assert "workspace" not in run.report + run.copy + run.scan
    assert list((base / "share").iterdir()) == []
    assert tree(src) == before
    for out in (run.scan, run.report, run.copy):
        assert value not in out and "ghp_" not in out


def test_the_owner_leaves_the_file_out_and_the_rest_moves(vault):
    base, src, value = vault
    before = tree(src)
    first = the_finding(run_job(base))
    run = run_job(base, skip=first["id"])
    # The id is the same in a second run of the scan.
    assert the_finding(run)["id"] == first["id"] and the_finding(run)["decision"] == "skip"
    assert run.code == 0, run.copy
    assert run.result() == {
        "result": "ok", "source-files": "7", "source-notes": "4", "files": "3", "notes": "2",
        "bytes": str(len("# A\n\nA plain note.\n") + len("# B\n\nAnother note.\n") + 1024),
        "left-out-config": "1", "left-out-trash": "1", "left-out-git": "1", "left-out-findings": "1",
        "taken-findings": "0", "links": "2", "other": "1",
    }
    share = base / "share"
    got = tree(share)
    assert got == {"Empty": "dir", "Notes": "dir", "Notes/a.md": before["Notes/a.md"],
                   "Notes/img.png": before["Notes/img.png"], "Notes/sub": "dir",
                   "Notes/sub/b.md": before["Notes/sub/b.md"]}
    for path in share.rglob("*"):
        assert (path.stat().st_mode & 0o777) == (0o700 if path.is_dir() else 0o600), path
    assert (share / "Notes/a.md").stat().st_mtime == 1_700_000_000
    assert tree(src) == before
    assert value not in run.copy + run.report


def test_the_owner_takes_the_file_as_it_is(vault):
    base, src, _ = vault
    first = the_finding(run_job(base))
    run = run_job(base, take=first["id"])
    assert run.code == 0, run.copy
    result = run.result()
    assert result["files"] == "4" and result["taken-findings"] == "1" and result["left-out-findings"] == "0"
    assert (base / "share/Notes/token.md").read_bytes() == (src / "Notes/token.md").read_bytes()


def test_a_second_run_on_a_share_with_files_copies_nothing(vault):
    base, _, _ = vault
    first = the_finding(run_job(base))
    assert run_job(base, skip=first["id"]).code == 0
    before = tree(base / "share")
    run = run_job(base, skip=first["id"])
    assert run.code != 0
    assert run.copy.strip() == "vault-seed result=failed reason=share-not-empty"
    assert tree(base / "share") == before


def test_a_folder_of_a_stopped_run_is_removed_first(vault):
    base, _, _ = vault
    first = the_finding(run_job(base))
    left = base / "share/.seed-incoming-20261119T000000Z/Notes"
    left.mkdir(parents=True)
    (left / "half.md").write_text("half\n")
    run = run_job(base, skip=first["id"])
    assert run.code == 0, run.copy
    assert not [p for p in (base / "share").iterdir() if p.name.startswith(".seed-incoming-")]
    assert not (base / "share/Notes/half.md").exists()


@pytest.mark.parametrize(("skip", "take", "reason"), [
    ("0123456789abcdef", "", "stale-decision count=1"),
    ("not-an-id", "", "decision-form"),
])
def test_a_wrong_decision_list_copies_nothing(vault, skip, take, reason):
    base, _, _ = vault
    run = run_job(base, skip=skip, take=take)
    assert run.code != 0
    assert run.copy.strip() == f"vault-seed result=failed reason={reason}"
    assert list((base / "share").iterdir()) == []


def test_one_id_in_both_lists_copies_nothing(vault):
    base, _, _ = vault
    first = the_finding(run_job(base))
    run = run_job(base, skip=first["id"], take=first["id"])
    assert run.code != 0
    assert run.copy.strip() == "vault-seed result=failed reason=decision-twice count=1"
    assert list((base / "share").iterdir()) == []


def test_a_decision_on_a_left_out_file_is_stale(vault):
    base, _, _ = vault
    config = next(f for f in run_job(base).findings() if f["decision"] == "left-out-config")
    run = run_job(base, take=config["id"])
    assert run.copy.strip() == "vault-seed result=failed reason=stale-decision count=1"


@pytest.mark.parametrize(("make", "reason"), [
    ("missing", "source-missing"),
    ("empty", "source-empty"),
    ("only-folders", "source-empty"),
    ("only-config", "nothing-to-copy"),
])
def test_a_source_with_nothing_to_copy_is_a_failure(make, reason):
    base = scratch()
    try:
        state = base / "state"
        (state / "workspace").mkdir(parents=True)
        (state / "workspace/a.md").write_text("# A note of the workspace\n")
        vault = state / "vault"
        if make != "missing":
            vault.mkdir()
        if make == "only-folders":
            (vault / "a/b").mkdir(parents=True)
        if make == "only-config":
            (vault / ".obsidian").mkdir()
            (vault / ".obsidian/app.json").write_text("{}\n")
        for path in [state, *state.rglob("*")]:
            path.chmod(0o755 if path.is_dir() else 0o644)
        run = run_job(base)
        assert run.code != 0
        assert run.copy.strip() == f"vault-seed result=failed reason={reason}"
        assert list((base / "share").iterdir()) == []
    finally:
        shutil.rmtree(base, ignore_errors=True)
