"""bin/main-mcp-key: the key of the main agent at the MCP gateway and its hash
on the routes of main (docs/apps/mcp-gateway.md, "The keys").

The first tests run the editor step and the hash step on copies, with no
sops. The round trip runs the script with sops and a throwaway age key in a
copy of the files, and needs sops and age-keygen on the PATH and
MAIN_MCP_KEY_SOPS_TEST=1 (no CI job sets it; the build ran it in a container
with no credentials). It checks that the output has names and "ok" only,
that the key is 64 hex characters, that each route of main has its hash,
and that the ciphertext of the other keys does not change.
"""

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "bin/main-mcp-key"
CONFIG = ROOT / "kubernetes/apps/ai/mcp-gateway/app/config/config.yaml"
SECRET = "kubernetes/apps/ai/agents/main/agent/app/secret.sops.yaml"
GATEWAY = "kubernetes/apps/ai/mcp-gateway/app/config/config.yaml"
OTHERS = ["OPENCLAW_GATEWAY_TOKEN", "LITELLM_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOW_FROM"]


def load():
    loader = SourceFileLoader("main_mcp_key", str(SCRIPT))
    spec = importlib.util.spec_from_loader("main_mcp_key", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def main_hashes(text: str) -> dict[str, str]:
    return {r["name"]: k["keyHash"] for r in yaml.safe_load(text)["routes"]
            for k in r["policies"]["apiKey"]["keys"] if k["metadata"]["client"] == "main"}


SECRET_TEXT = """apiVersion: v1
kind: Secret
metadata:
    name: agent-secret
type: Opaque
stringData:
    OPENCLAW_GATEWAY_TOKEN: a
    LITELLM_API_KEY: b
    TELEGRAM_BOT_TOKEN: c
    TELEGRAM_ALLOW_FROM: "1"
"""


@pytest.mark.parametrize("before", [SECRET_TEXT, SECRET_TEXT + "    MCP_GATEWAY_KEY: pending\n",
                                    SECRET_TEXT.replace("    LITELLM", "    MCP_GATEWAY_KEY: pending\n    LITELLM")])
def test_the_editor_step_puts_one_new_key_in_stringdata(tmp_path, before):
    m = load()
    path = tmp_path / "clear.yaml"
    path.write_text(before)
    m.editor_step(path)
    after = yaml.safe_load(path.read_text())
    value = after["stringData"].pop("MCP_GATEWAY_KEY")
    assert re.fullmatch(r"[0-9a-f]{64}", value)
    assert after["stringData"] == yaml.safe_load(SECRET_TEXT)["stringData"]
    assert after["metadata"] == {"name": "agent-secret"}


def test_the_hash_step_sets_each_route_of_main_and_nothing_else(tmp_path, capsys, monkeypatch):
    m = load()
    copy = tmp_path / "config.yaml"
    copy.write_text(CONFIG.read_text())
    monkeypatch.setattr(m, "CONFIG", copy)
    digest = "sha256:" + hashlib.sha256(b"x" * 64).hexdigest()
    m.set_hash(digest)
    before, after = main_hashes(CONFIG.read_text()), main_hashes(copy.read_text())
    assert set(after) == set(before) and "main-vault" in after
    assert set(after.values()) == {digest}
    assert CONFIG.read_text().count("keyHash") == copy.read_text().count("keyHash")
    rest = yaml.safe_load(copy.read_text())
    for r in rest["routes"]:
        for k in r["policies"]["apiKey"]["keys"]:
            if k["metadata"]["client"] != "main":
                assert k["keyHash"] in CONFIG.read_text()
    out = capsys.readouterr().out
    assert digest.removeprefix("sha256:") not in out
    assert all(line.endswith(" ok") for line in out.splitlines())


def test_the_hash_step_refuses_the_hash_of_another_client(tmp_path, monkeypatch):
    m = load()
    copy = tmp_path / "config.yaml"
    copy.write_text(CONFIG.read_text())
    monkeypatch.setattr(m, "CONFIG", copy)
    (finance,) = [r for r in yaml.safe_load(CONFIG.read_text())["routes"] if r["name"] == "finance"]
    other = finance["policies"]["apiKey"]["keys"][0]["keyHash"]
    if other.startswith("sha256:000"):
        pytest.skip("finance has a placeholder")
    with pytest.raises(SystemExit):
        m.set_hash(other)
    assert copy.read_text() == CONFIG.read_text()


# The round trip with sops.

ROUND_TRIP = os.environ.get("MAIN_MCP_KEY_SOPS_TEST") == "1"


def enc(text: str) -> dict[str, str]:
    return {k: v for k, v in yaml.safe_load(text)["stringData"].items()}


@pytest.fixture
def tree(tmp_path, monkeypatch):
    if not (ROUND_TRIP and shutil.which("sops") and shutil.which("age-keygen")):
        pytest.skip("the round trip with sops is opt-in: MAIN_MCP_KEY_SOPS_TEST=1, sops and age-keygen")
    # Out of the repo, so its .sops.yaml does not apply.
    monkeypatch.chdir(tmp_path)
    for rel in ("bin/sops", "bin/main-mcp-key", GATEWAY):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, tmp_path / rel)
    key = tmp_path / "age.txt"
    subprocess.run(["age-keygen", "-o", str(key)], check=True, capture_output=True)
    public = re.search(r"public key: (\S+)", key.read_text())[1]
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(key))
    secret = tmp_path / SECRET
    secret.parent.mkdir(parents=True)
    secret.write_text(SECRET_TEXT)
    subprocess.run(["sops", "-e", "-i", "--age", public, "--encrypted-regex", "^(data|stringData)$", str(secret)],
                   check=True, capture_output=True)
    return tmp_path


def run(tree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(tree / "bin/main-mcp-key"), *args], capture_output=True, text=True, check=False)


def clear(tree: Path) -> dict:
    out = subprocess.run(["sops", "-d", str(tree / SECRET)], capture_output=True, text=True, check=True).stdout
    return yaml.safe_load(out)["stringData"]


def test_the_round_trip_makes_the_key_and_its_hash_and_prints_no_value(tree):
    before = enc((tree / SECRET).read_text())
    done = run(tree)
    assert done.returncode == 0, done.stdout + done.stderr
    lines = done.stdout.splitlines()
    assert lines[0] == "secret.sops.yaml key=MCP_GATEWAY_KEY made ok"
    assert {line.split()[1] for line in lines[1:]} == {f"route={r}" for r in main_hashes(CONFIG.read_text())}
    assert all(line.endswith(" set ok") for line in lines[1:]) and done.stderr == ""
    value = clear(tree)["MCP_GATEWAY_KEY"]
    assert re.fullmatch(r"[0-9a-f]{64}", value)
    digest = hashlib.sha256(value.encode()).hexdigest()
    assert value not in done.stdout and digest not in done.stdout
    assert set(main_hashes((tree / GATEWAY).read_text()).values()) == {"sha256:" + digest}
    after = enc((tree / SECRET).read_text())
    assert {k: after[k] for k in OTHERS} == before
    # Again: the key stays, and so does each hash.
    again = run(tree)
    assert again.returncode == 0
    assert again.stdout.splitlines()[0] == "secret.sops.yaml key=MCP_GATEWAY_KEY kept ok"
    assert all(line.endswith(" unchanged ok") for line in again.stdout.splitlines()[1:])
    assert clear(tree)["MCP_GATEWAY_KEY"] == value
    # --new: a new key, and its hash on each route.
    new = run(tree, "--new")
    assert new.returncode == 0 and new.stdout.splitlines()[0].endswith(" made ok")
    other = clear(tree)["MCP_GATEWAY_KEY"]
    assert other != value
    assert set(main_hashes((tree / GATEWAY).read_text()).values()) == {
        "sha256:" + hashlib.sha256(other.encode()).hexdigest()}


def test_the_round_trip_keeps_a_key_that_exists_and_its_route(tree):
    """As on main since the vault step: the key is in the Secret and its hash
    on main-vault. The script writes no byte of the Secret file and of the
    line of main-vault, and sets the hash on each other route of main."""
    secret, config = tree / SECRET, tree / GATEWAY
    subprocess.run(["sops", "--set", '["stringData"]["MCP_GATEWAY_KEY"] "' + "ab" * 32 + '"', str(secret)],
                   check=True, capture_output=True)
    digest = "sha256:" + hashlib.sha256(("ab" * 32).encode()).hexdigest()
    text = config.read_text()
    old = main_hashes(text)["main-vault"]
    vault = 'keyHash: "%s"\n            metadata: {client: main, route: main-vault}'
    assert text.count(vault % old) == 1
    config.write_text(text.replace(vault % old, vault % digest))
    before_secret, before = secret.read_bytes(), config.read_text()
    done = run(tree)
    assert done.returncode == 0, done.stdout + done.stderr
    lines = done.stdout.splitlines()
    assert lines[0] == "secret.sops.yaml key=MCP_GATEWAY_KEY kept ok"
    assert "config.yaml route=main-vault unchanged ok" in lines
    assert secret.read_bytes() == before_secret
    after = config.read_text()
    assert (vault % digest) in after
    assert set(main_hashes(after).values()) == {digest}
    # Only the key lines of the other routes of main changed.
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines(), strict=True) if a != b]
    assert changed and all("keyHash" in a and digest in b for a, b in changed)


def test_the_round_trip_refuses_a_key_of_another_form_and_a_file_it_can_not_read(tree, monkeypatch):
    secret = tree / SECRET
    subprocess.run(["sops", "--set", '["stringData"]["MCP_GATEWAY_KEY"] "short"', str(secret)],
                   check=True, capture_output=True)
    config = (tree / GATEWAY).read_text()
    done = run(tree)
    assert done.returncode == 1
    assert done.stdout == "secret.sops.yaml key=MCP_GATEWAY_KEY form=not-64-hex (--new makes a new one)\n"
    assert (tree / GATEWAY).read_text() == config
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(tree / "missing.txt"))
    done = run(tree)
    assert (done.returncode, done.stdout) == (1, "secret.sops.yaml read=failed\n")
    assert (tree / GATEWAY).read_text() == config
