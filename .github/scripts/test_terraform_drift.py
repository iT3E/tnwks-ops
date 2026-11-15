"""Tests for .github/scripts/terraform-drift.py.

    python3 -m unittest discover -s .github/scripts -p 'test_terraform_drift.py' -v
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("terraform_drift", HERE / "terraform-drift.py")
drift = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drift)

# What actions/terraform (github-workflows v1.9.2) puts in its summary output.
SUMMARY = """**Plan:** 1 to add, 1 to change, 1 to destroy, 1 to move

- `create` module.github.github_repository.this["private4"]
- `update` module.github.github_repository_ruleset.this["tnwks-ops/main"]
- `delete/create` module.github.github_actions_variable.this["x"]
- `move` module.a -> module.b"""
NO_CHANGES = "**Plan:** 0 to add, 0 to change, 0 to destroy, 0 to move\n"


def metrics(text: str) -> dict[str, str]:
    return dict(line.split(" ", 1) for line in text.splitlines() if line and not line.startswith("#"))


class Evaluate(unittest.TestCase):
    def test_no_drift(self):
        r = drift.evaluate("success", "false", NO_CHANGES)
        self.assertEqual((r.failed, r.drifted, r.changed), (False, False, 0))

    def test_drift_counts_addresses(self):
        r = drift.evaluate("success", "true", SUMMARY)
        self.assertEqual((r.failed, r.drifted, r.changed), (False, True, 4))
        self.assertEqual(r.counts, (1, 1, 1, 1))

    def test_changes_without_addresses_is_still_drift(self):
        """Output-only or import-only plans: exit code 2, no address line."""
        r = drift.evaluate("success", "true", NO_CHANGES)
        self.assertEqual((r.failed, r.drifted, r.changed), (False, True, 0))

    def test_failed_or_skipped_plan_is_a_failed_check(self):
        for outcome in ("failure", "skipped", ""):
            r = drift.evaluate(outcome, "", "")
            self.assertEqual((r.failed, r.drifted, r.changed), (True, False, 0), outcome)

    def test_success_without_changes_output_is_a_failed_check(self):
        r = drift.evaluate("success", "", SUMMARY)
        self.assertTrue(r.failed)
        self.assertFalse(r.drifted)


class Render(unittest.TestCase):
    def test_four_gauges_without_labels(self):
        text = drift.render(drift.evaluate("success", "true", SUMMARY), 1794600000.4)
        self.assertEqual(metrics(text), {
            "tnwks_terraform_drift_check_timestamp_seconds": "1794600000",
            "tnwks_terraform_drift_check_drifted": "1",
            "tnwks_terraform_drift_check_changed": "4",
            "tnwks_terraform_drift_check_failed": "0",
        })
        self.assertNotIn("{", text)
        self.assertEqual(text.count("# TYPE "), 4)
        self.assertTrue(text.endswith("\n"))


class Main(unittest.TestCase):
    def run_main(self, *args, summary=""):
        with tempfile.TemporaryDirectory() as tmp:
            prom = Path(tmp) / "drift.prom"
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"TF_SUMMARY": summary}), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = drift.main([*args, "--prom", str(prom), "--now", "100"])
            return rc, out.getvalue(), err.getvalue(), (prom.read_text() if prom.exists() else None)

    def test_drift_prints_counts_never_addresses(self):
        rc, out, _, prom = self.run_main("--root", "github", "--outcome", "success", "--changes", "true", summary=SUMMARY)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "::warning title=Terraform drift::github: drift, 4 address(es) would change "
                              "(1 to add, 1 to change, 1 to destroy, 1 to move)\n")
        self.assertNotIn("private4", out)
        self.assertNotIn("module.", out)
        self.assertEqual(metrics(prom)["tnwks_terraform_drift_check_drifted"], "1")

    def test_no_drift(self):
        rc, out, _, prom = self.run_main("--root", "github", "--outcome", "success", "--changes", "false", summary=NO_CHANGES)
        self.assertEqual((rc, out), (0, "github: no drift\n"))
        self.assertEqual(metrics(prom)["tnwks_terraform_drift_check_failed"], "0")

    def test_failure_writes_failed_and_exits_0(self):
        rc, out, _, prom = self.run_main("--root", "github", "--outcome", "failure")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "github: check failed (the plan step's outcome is failure)\n")
        self.assertEqual(metrics(prom)["tnwks_terraform_drift_check_failed"], "1")
        self.assertEqual(metrics(prom)["tnwks_terraform_drift_check_timestamp_seconds"], "100")

    def test_bad_root_name_writes_nothing(self):
        for root in ("../x", "a/b", "", "Github"):
            rc, out, err, prom = self.run_main("--root", root, "--outcome", "success", "--changes", "false")
            self.assertEqual(rc, 2, root)
            self.assertIsNone(prom, root)
            self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
