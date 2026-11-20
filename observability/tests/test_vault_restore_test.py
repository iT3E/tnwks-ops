"""The restore test of the finance share
(kubernetes/apps/ai/agents/finance/vault-backup/restore-test/;
docs/apps/agent-platform-runbook.md, "The restore test of the finance
share"): a Restore of one snapshot of the policy vault into a scratch
volume, then a check Job that compares it with the live share.

The static tests hold the form: the Restore writes only the scratch claim,
the check mounts the share read only and has no secret, no token and no
network, the two steps run in order, and the test is listed only with the
name of a real snapshot. The container tests run the script of the check
Job in the agent image, as the pod runs it, on a fake share and a fake
restore: the same files pass, and each kind of difference fails with its
reason and never prints a name. Without Docker they are skipped, except in
CI, where they fail.
"""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from test_hand_written_rules import docker, scratch

ROOT = Path(__file__).resolve().parents[2]
FINANCE = ROOT / "kubernetes/apps/ai/agents/finance"
TEST = FINANCE / "vault-backup/restore-test"
SNAPSHOT = re.compile(r"vault-\d{14}")
PLACEHOLDER = "vault-SET-ME"


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def one(path: Path) -> dict:
    (only,) = docs(path)
    return only


def restore() -> dict:
    return one(TEST / "restore/restore.yaml")


def job() -> dict:
    return one(TEST / "check/job.yaml")


def pod() -> dict:
    return job()["spec"]["template"]["spec"]


def check() -> dict:
    (only,) = pod()["containers"]
    return only


def env() -> dict[str, str]:
    return {e["name"]: e["value"] for e in check()["env"]}


def flux() -> dict[str, dict]:
    return {d["metadata"]["name"]: d["spec"] for d in docs(TEST / "ks.yaml")}


def listed() -> bool:
    resources = yaml.safe_load((FINANCE / "kustomization.yaml").read_text())["resources"]
    return "./vault-backup/restore-test/ks.yaml" in resources


def test_the_restore_writes_only_the_scratch_claim():
    spec = restore()["spec"]
    claim = one(TEST / "restore/pvc.yaml")
    assert spec["target"] == {"pvcRef": {"name": claim["metadata"]["name"]}}
    assert claim["metadata"]["name"] not in {"vault-rw", "vault-ro"}
    assert claim["spec"]["storageClassName"] == "${STORAGE_CLASS_DEFAULT}"
    assert set(spec["source"]) == {"snapshotRef"}


def test_the_restore_and_the_check_name_the_same_snapshot():
    name = restore()["spec"]["source"]["snapshotRef"]["name"]
    assert env()["SNAPSHOT"] == name
    assert SNAPSHOT.fullmatch(name) or name == PLACEHOLDER, name


def test_the_test_is_never_listed_with_the_placeholder_snapshot():
    if listed():
        assert SNAPSHOT.fullmatch(restore()["spec"]["source"]["snapshotRef"]["name"])


def test_the_restore_mover_is_the_mover_of_the_backup():
    (backup,) = [d for d in docs(FINANCE / "vault-backup/app/backup.yaml") if d["kind"] == "SnapshotPolicy"]
    mover = restore()["spec"]["mover"]
    assert mover["securityContext"] == backup["spec"]["mover"]["securityContext"]
    for key, value in backup["spec"]["mover"]["podSecurityContext"].items():
        assert mover["podSecurityContext"][key] == value


def test_the_check_runs_after_the_restore_and_the_restore_after_the_backup():
    spec = flux()
    test = spec["agents-finance-vault-restore-test"]
    check_ks = spec["agents-finance-vault-restore-check"]
    assert {"name": "agents-finance-vault-backup"} in test["dependsOn"]
    assert test["wait"] is True
    (expr,) = test["healthCheckExprs"]
    assert expr["kind"] == "Restore"
    assert {"name": "agents-finance-vault-restore-test"} in check_ks["dependsOn"]
    assert test["timeout"] == "45m"
    assert restore()["spec"]["failurePolicy"]["activeDeadlineSeconds"] < 45 * 60
    for s in spec.values():
        assert s["prune"] is True
        assert s["targetNamespace"] == "agents-finance"
        assert (ROOT / s["path"]).is_dir()


def test_the_check_runs_once_as_the_agent_with_no_secret_no_token_and_no_network():
    j = job()["spec"]
    p = pod()
    assert j["backoffLimit"] == 0
    assert p["restartPolicy"] == "Never"
    assert p["automountServiceAccountToken"] is False
    assert p["enableServiceLinks"] is False
    assert "serviceAccountName" not in p
    assert "hostNetwork" not in p
    assert set(env()) == {"SNAPSHOT"}
    assert "envFrom" not in check()
    sc = check()["securityContext"]
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["allowPrivilegeEscalation"] is False
    assert p["securityContext"]["runAsUser"] == 1000
    agent = one(FINANCE / "agent/app/deployment.yaml")["spec"]["template"]["spec"]
    assert check()["image"] == agent["containers"][0]["image"]


def test_both_claims_are_read_only_at_their_root_and_the_lists_stay_in_memory():
    volumes = {v["name"]: v for v in pod()["volumes"]}
    assert volumes["restored"]["persistentVolumeClaim"] == {"claimName": "vault-restore-test", "readOnly": True}
    assert volumes["live"]["persistentVolumeClaim"] == {"claimName": "vault-ro", "readOnly": True}
    assert volumes["work"]["emptyDir"]["medium"] == "Memory"
    assert not any("secret" in v or "projected" in v for v in volumes.values())
    mounts = {m["name"]: m for m in check()["volumeMounts"]}
    assert mounts["restored"] == {"name": "restored", "mountPath": "/restored", "readOnly": True}
    assert mounts["live"] == {"name": "live", "mountPath": "/live", "readOnly": True}
    assert not any("subPath" in m for m in mounts.values())


def test_no_count_of_the_vault_is_in_git():
    """The count of the share is read at run time, never written down."""
    for path in TEST.rglob("*.yaml"):
        text = path.read_text()
        assert not re.search(r"\b(files|notes|bytes)=\d", text), path


# The container tests.

OLD = time.mktime(time.strptime("2026-11-01", "%Y-%m-%d"))
NAMES = ["Budget Plan.md", "Taxes/Return Draft.md", "Taxes/receipt.pdf", "-dash.md", "Deep/a/b/Line.md"]


def make(root: Path, names=NAMES) -> None:
    root.mkdir(parents=True)
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"text of {name}\n")
    (root / "Empty Folder").mkdir()
    (root / "Link To Plan.md").symlink_to("Budget Plan.md")
    for path in root.rglob("*"):
        os.utime(path, (OLD, OLD), follow_symlinks=False)


def run_check(base: Path, snapshot: str = "vault-20261119103300") -> tuple[int, str]:
    tool = docker()
    for path in (base, base / "restored", base / "live"):
        path.chmod(0o755)
    result = subprocess.run(
        [tool, "run", "--rm", "--quiet", "--network", "none", "--read-only", "--cap-drop", "ALL",
         "--security-opt", "no-new-privileges", "-u", f"{os.getuid()}:{os.getgid()}",
         "-v", f"{base / 'restored'}:/restored:ro", "-v", f"{base / 'live'}:/live:ro",
         "--tmpfs", "/work", "-e", f"SNAPSHOT={snapshot}",
         "--entrypoint", "sh", check()["image"], "-c", check()["args"][0]],
        capture_output=True, text=True, timeout=300, check=False)
    out = result.stdout + result.stderr
    for name in [*NAMES, "Empty Folder", "Link To Plan", "Taxes", "Deep", "New Note", "text of"]:
        assert name not in out, out
    return result.returncode, out


def result(out: str) -> dict[str, str]:
    (line,) = [x for x in out.splitlines() if " result=" in x]
    return dict(part.split("=", 1) for part in line.split()[1:])


def reasons(out: str) -> set[str]:
    return set(result(out)["reason"].split(","))


@pytest.fixture
def base():
    path = scratch()
    make(path / "restored")
    make(path / "live")
    yield path
    for p in path.rglob("*"):
        if not p.is_symlink():
            p.chmod(0o755)
    shutil.rmtree(path, ignore_errors=True)


def test_the_same_files_pass_with_counts_only(base):
    code, out = run_check(base)
    assert code == 0, out
    assert result(out) == {"result": "ok", "files": "5", "notes": "4", "bytes": str(sum(
        len(f"text of {n}\n") for n in NAMES))}
    assert "vault-restore-check compare only-live=0 only-restored=0 same-dirs=yes same-links=yes live-newer=0" in out
    assert "snapshot=vault-20261119103300 at=2026-11-19T10:33:00Z" in out


def test_a_note_missing_from_the_restore_fails(base):
    (base / "restored/Taxes/Return Draft.md").unlink()
    code, out = run_check(base)
    assert code == 1
    assert reasons(out) == {"files", "notes", "bytes", "content"}


def test_a_note_with_other_words_of_the_same_size_fails(base):
    path = base / "restored/Budget Plan.md"
    path.write_text(path.read_text().upper())
    os.utime(path, (OLD, OLD))
    code, out = run_check(base)
    assert code == 1
    assert reasons(out) == {"content"}
    assert "only-live=1 only-restored=1" in out


def test_a_moved_note_fails_on_its_path(base):
    (base / "restored/Deep/a/b/Line.md").rename(base / "restored/Deep/a/Line.md")
    code, out = run_check(base)
    assert code == 1
    assert reasons(out) == {"content"}


def test_a_lost_folder_and_a_lost_link_fail(base):
    (base / "restored/Empty Folder").rmdir()
    (base / "restored/Link To Plan.md").unlink()
    code, out = run_check(base)
    assert code == 1
    assert reasons(out) == {"dirs", "links"}


def test_a_restored_file_that_cannot_be_read_fails(base):
    (base / "restored/Taxes/receipt.pdf").chmod(0)
    code, out = run_check(base)
    assert code == 1
    assert {"unreadable", "content"} <= reasons(out)


def test_an_empty_restore_fails(base):
    shutil.rmtree(base / "restored")
    (base / "restored").mkdir()
    code, out = run_check(base)
    assert code == 1
    assert {"empty", "no-notes", "files", "notes", "bytes", "content", "dirs", "links"} == reasons(out)


def test_a_write_to_the_share_after_the_snapshot_says_so(base):
    (base / "live/New Note.md").write_text("new\n")
    code, out = run_check(base)
    assert code == 1
    assert reasons(out) == {"files", "notes", "bytes", "content", "share-changed"}
    assert "live-newer=1" in out


@pytest.mark.parametrize("snapshot", ["vault-SET-ME", "vault-2026111910330", "vault-20261399103300", ""])
def test_a_wrong_snapshot_name_stops_first(base, snapshot):
    code, out = run_check(base, snapshot)
    assert code == 1
    assert out.strip() == "vault-restore-check result=failed reason=snapshot-name"
