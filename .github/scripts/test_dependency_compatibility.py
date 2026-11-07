"""Behavioral checks for compatibility changes, failed evidence, and issue lifecycle."""

import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("compatibility", Path(__file__).with_name("dependency-compatibility.py"))
compatibility = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compatibility)


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.config = {"watches": [{
            "id": "test", "title": "Review test compatibility", "action": "Check vendor support.",
            "review_after": "2027-01-01", "sources": [{"name": "Vendor support", "sha256": "known"}]
        }]}
        self.today = dt.date(2026, 11, 7)

    def report(self, digest="known"):
        return compatibility.inspect(self.config, today=self.today, observe=lambda _: digest)

    def test_unchanged_evidence_stays_quiet(self):
        self.assertFalse(self.report()[0]["needs_review"])

    def test_changed_support_requests_review_without_claiming_compatibility(self):
        result = self.report("new")[0]
        self.assertTrue(result["needs_review"])
        self.assertIn("evidence changed", result["body"])
        self.assertIn("do not prove", result["body"])

    def test_expired_review_catches_abandoned_upstream(self):
        self.config["watches"][0]["review_after"] = self.today.isoformat()
        self.assertIn("periodic compatibility review is due", self.report()[0]["body"])

    def test_source_errors_request_review_without_publishing_error_payload(self):
        def fail(_):
            raise ValueError("untrusted-response-content")

        result = compatibility.inspect(self.config, today=self.today, observe=fail)[0]
        self.assertTrue(result["needs_review"])
        self.assertIn("could not be checked", result["body"])
        self.assertNotIn("untrusted-response-content", result["body"])

    def test_unifi_support_changes_are_detected_but_whitespace_is_ignored(self):
        source = {"kind": "unifi-support", "url": "https://example.invalid"}
        text = "### Setting Up Your External Database\n\nMongoDB 8.0 is supported.\n\nMore instructions."
        old = compatibility.evidence(source, fetch=lambda _: text)
        self.assertEqual(old, compatibility.evidence(source, fetch=lambda _: text.replace("8.0 is", "8.0  is")))
        self.assertNotEqual(old, compatibility.evidence(source, fetch=lambda _: text.replace("8.0", "9.0")))
        with self.assertRaises(IndexError):
            compatibility.evidence(source, fetch=lambda _: "Upstream moved the support section.")

    def test_new_windows_wheel_on_an_existing_release_is_detected(self):
        source = {"kind": "coral-releases", "url": "https://example.invalid"}
        releases = [{"tag_name": "v2.0.0", "prerelease": False, "draft": False,
                     "assets": [{"name": "runtime-cp39-win_amd64.whl", "updated_at": "old"}]}]
        old = compatibility.evidence(source, fetch=lambda _: json.dumps(releases))
        releases[0]["assets"].append({"name": "runtime-cp313-win_amd64.whl", "updated_at": "new"})
        self.assertNotEqual(old, compatibility.evidence(source, fetch=lambda _: json.dumps(releases)))

    def test_local_unifi_upgrade_is_a_compatibility_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = {"kind": "unifi-image", "path": "release.yaml"}
            text = "repository: ghcr.io/linuxserver/unifi-network-application\n  tag: 10.6.109\n"
            (root / "release.yaml").write_text(text)
            old = compatibility.evidence(source, root=root)
            (root / "release.yaml").write_text(text.replace("10.6.109", "11.0.0"))
            self.assertNotEqual(old, compatibility.evidence(source, root=root))

    def test_host_backend_override_is_a_compatibility_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "defaults.yml").write_text("coral_detector_python_version: 3.9.25\ncoral_detector_backend: edgetpu\n")
            (root / "requirements.in").write_text("tflite-runtime @ https://example.invalid/runtime.whl\n")
            host = root / "inventory/host1"
            host.mkdir(parents=True)
            (host / "main.yml").write_text("coral_detector_devices: []\n")
            source = {"kind": "coral-runtime", "defaults": "defaults.yml",
                      "requirements": "requirements.in", "inventory": "inventory"}
            old = compatibility.evidence(source, root=root)
            (host / "main.yml").write_text("coral_detector_devices: []\ncoral_detector_backend: alternative\n")
            self.assertNotEqual(old, compatibility.evidence(source, root=root))

    def existing(self, report, state="OPEN"):
        return [{"number": 1, "title": report[0]["title"], "body": report[0]["body"], "state": state,
                 "author": {"login": "github-actions"}}]

    def test_unchanged_open_issue_is_not_updated_every_week(self):
        report = self.report("new")
        with patch.object(compatibility, "gh", return_value=json.dumps(self.existing(report))) as gh, \
             patch.object(compatibility.subprocess, "run"):
            compatibility.publish(report, "example/repo")
        self.assertEqual(gh.call_count, 1)

    def test_closed_unreviewed_change_is_reopened(self):
        report = self.report("new")
        with patch.object(compatibility, "gh", side_effect=[json.dumps(self.existing(report, "CLOSED")), ""]) as gh, \
             patch.object(compatibility.subprocess, "run"):
            compatibility.publish(report, "example/repo")
        self.assertEqual(gh.call_args.args, ("example/repo", "issue", "reopen", "1"))

    def test_an_additional_upstream_change_updates_and_notifies_once(self):
        first = self.report("first")
        second = self.report("second")
        with patch.object(compatibility, "gh", side_effect=[json.dumps(self.existing(first)), "", ""]) as gh, \
             patch.object(compatibility.subprocess, "run"):
            compatibility.publish(second, "example/repo")
        self.assertEqual(gh.call_count, 3)
        self.assertEqual(gh.call_args.args[:4], ("example/repo", "issue", "comment", "1"))

    def test_accepted_baseline_closes_the_review_issue(self):
        old = self.report("new")
        report = self.report()
        with patch.object(compatibility, "gh", side_effect=[json.dumps(self.existing(old)), ""]) as gh, \
             patch.object(compatibility.subprocess, "run"):
            compatibility.publish(report, "example/repo")
        self.assertEqual(gh.call_args.args, ("example/repo", "issue", "close", "1"))

    def test_privacy_failure_prevents_all_github_writes(self):
        with patch.object(compatibility, "gh") as gh, \
             patch.object(compatibility.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "privacy-check")):
            with self.assertRaises(subprocess.CalledProcessError):
                compatibility.publish(self.report("new"), "example/repo")
        gh.assert_not_called()

    def test_a_forged_marker_does_not_redirect_the_review_issue(self):
        report = self.report("new")
        forged = self.existing(report)
        forged[0]["author"]["login"] = "contributor"
        with patch.object(compatibility, "gh", side_effect=[json.dumps(forged), ""]) as gh, \
             patch.object(compatibility.subprocess, "run"):
            compatibility.publish(report, "example/repo")
        self.assertEqual(gh.call_args.args[:3], ("example/repo", "issue", "create"))


if __name__ == "__main__":
    unittest.main()
