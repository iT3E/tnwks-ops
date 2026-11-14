"""Cases for hand-written alert rules and Vector transforms, run in containers.

The generated rules have their fixtures in observability/generated. A rule or a
Vector transform written by hand in kubernetes/ gets its cases here:

- observability/rule-tests/*.yaml: a promtool test file whose rule_files name
  PrometheusRule manifests by their path in the repo. Each rule loses its
  annotations (they hold Flux variables and Go templates), and promtool runs
  the file as it is written.
- observability/vector-tests/*.yaml: a `vector test` file with one more key,
  `config`, the path of a Vector config in the repo. The test runs with the
  image of the HelmRelease next to that config, so it checks the version that
  runs. A Flux default (${VAR:=value}) becomes its value, and any other Flux
  variable becomes "test".

Each test needs Docker. Without it a test is skipped, except in CI, where it
fails.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "observability"))
from versions import PROMETHEUS

RULE_TESTS = sorted((ROOT / "observability/rule-tests").glob("*.yaml"))
VECTOR_TESTS = sorted((ROOT / "observability/vector-tests").glob("*.yaml"))
FLUX_DEFAULT = re.compile(r"\$\{[A-Z0-9_]+:=([^}]*)\}")
FLUX_VARIABLE = re.compile(r"\$\{[A-Z0-9_]+\}")


def docker():
    if shutil.which("docker"):
        return "docker"
    if os.environ.get("CI"):
        pytest.fail("Docker is needed to run the hand-written rule and Vector cases")
    pytest.skip("docker not installed")


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=600, check=False)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output[-8000:]
    return output


def scratch():
    path = Path(tempfile.mkdtemp(prefix="rule-tests-"))
    path.chmod(0o755)  # promtool runs as nobody in its image
    return path


def plain_rules(manifest):
    """The groups of each PrometheusRule in a manifest, with no annotations."""
    groups = []
    for doc in yaml.safe_load_all(manifest.read_text()):
        if isinstance(doc, dict) and doc.get("kind") == "PrometheusRule":
            for group in doc["spec"]["groups"]:
                for rule in group["rules"]:
                    rule.pop("annotations", None)
                groups.append(group)
    assert groups, f"{manifest} holds no PrometheusRule"
    return {"groups": groups}


def vector_image(config):
    """The image of the HelmRelease next to a Vector config directory."""
    release = yaml.safe_load((config.parent.parent / "helmrelease.yaml").read_text())
    found = []

    def walk(node):
        if isinstance(node, dict):
            if {"repository", "tag"} <= node.keys() and "vector" in str(node["repository"]):
                found.append(f'{node["repository"]}:{node["tag"]}')
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(release)
    assert len(set(found)) == 1, (config, found)
    return found[0]


@pytest.mark.parametrize("fixture", RULE_TESTS, ids=[p.name for p in RULE_TESTS])
def test_rule_fixture_covers_each_alert_of_its_rules(fixture):
    spec = yaml.safe_load(fixture.read_text())
    tested = {
        case["alertname"]
        for test in spec["tests"]
        for case in test.get("alert_rule_test", [])
    }
    for name in spec["rule_files"]:
        assert (ROOT / name).is_file(), f"{fixture.name}: {name} is not in the repo"
        for group in plain_rules(ROOT / name)["groups"]:
            for rule in group["rules"]:
                if "alert" in rule:
                    assert rule["alert"] in tested, f"{fixture.name}: no case for {rule['alert']}"


@pytest.mark.parametrize("fixture", RULE_TESTS, ids=[p.name for p in RULE_TESTS])
def test_promtool_cases_pass(fixture):
    binary = docker()
    work = scratch()
    try:
        spec = yaml.safe_load(fixture.read_text())
        for name in spec["rule_files"]:
            target = work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(yaml.safe_dump(plain_rules(ROOT / name), sort_keys=False))
        # The file as written: a reload would read 0x60 as a hex number.
        (work / "tests.yaml").write_text(fixture.read_text())
        subprocess.run(["chmod", "-R", "a+rX", str(work)], check=True)
        output = run([
            binary, "run", "--rm", "--network", "none", "--entrypoint", "/bin/promtool",
            "--mount", f"type=bind,src={work},dst=/t,readonly", "--workdir", "/t",
            PROMETHEUS, "test", "rules", "tests.yaml",
        ])
        assert "SUCCESS" in output, output[-8000:]
    finally:
        shutil.rmtree(work, ignore_errors=True)


@pytest.mark.parametrize("fixture", VECTOR_TESTS, ids=[p.name for p in VECTOR_TESTS])
def test_vector_cases_pass(fixture):
    binary = docker()
    text = fixture.read_text()
    config = ROOT / yaml.safe_load(text)["config"]
    assert config.is_file(), f"{fixture.name}: {config} is not in the repo"
    work = scratch()
    try:
        rendered = FLUX_DEFAULT.sub(r"\1", config.read_text())
        rendered = FLUX_VARIABLE.sub("test", rendered)
        (work / "config.yaml").write_text(rendered)
        # The file as written, but its config key, which Vector does not know.
        tests = re.sub(r"(?m)^config: .*\n", "", text, count=1)
        (work / "tests.yaml").write_text(tests)
        subprocess.run(["chmod", "-R", "a+rX", str(work)], check=True)
        output = run([
            binary, "run", "--rm", "--network", "none", "--entrypoint", "vector",
            "--mount", f"type=bind,src={work},dst=/t,readonly",
            vector_image(config), "test", "/t/config.yaml", "/t/tests.yaml",
        ])
        cases = len(yaml.safe_load(tests)["tests"])
        assert output.count(" ... passed") == cases, output[-8000:]
    finally:
        shutil.rmtree(work, ignore_errors=True)
