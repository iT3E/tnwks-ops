"""The generated rule tests that span a week (observability/generated/rule-tests-week.yaml).

They have one evaluation each 30 minutes, so they are in their own file; the CI
step of the rule tests runs only rule-tests.yaml. promtool runs in the image of
Prometheus, as for the hand-written cases. Without Docker the test is skipped,
except in CI, where it fails.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "observability/generated"
sys.path.insert(0, str(ROOT / "observability"))
from versions import PROMETHEUS


def test_week_file_has_tests_and_reads_the_test_rules():
    spec = yaml.safe_load((GENERATED / "rule-tests-week.yaml").read_text())
    assert spec["tests"], "rule-tests-week.yaml has no test"
    assert spec["rule_files"] == ["rules-for-test.yaml"]


def test_week_rule_tests_pass():
    if not shutil.which("docker"):
        if os.environ.get("CI"):
            pytest.fail("Docker is needed to run the rule tests of a week")
        pytest.skip("docker not installed")
    result = subprocess.run([
        "docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/promtool",
        "--mount", f"type=bind,src={GENERATED},dst=/rules,readonly", "--workdir", "/rules",
        PROMETHEUS, "test", "rules", "rule-tests-week.yaml",
    ], capture_output=True, text=True, timeout=600, check=False)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and "SUCCESS" in output, output[-8000:]
