"""Tests for bin/ci-changes and .github/ci/domains.toml.

    python3 -m unittest discover -s .github/scripts -p 'test_ci_changes.py' -v
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader("ci_changes", str(ROOT / "bin/ci-changes"))
spec = importlib.util.spec_from_loader("ci_changes", loader)
ci_changes = importlib.util.module_from_spec(spec)
loader.exec_module(ci_changes)

CONFIG = tomllib.loads((ROOT / ".github/ci/domains.toml").read_text())


def on(files):
    out = ci_changes.evaluate(CONFIG, files)
    return {k for k, v in out.items() if v is True}, {k: [m["name"] for m in v] for k, v in out.items() if isinstance(v, list)}


class Glob(unittest.TestCase):
    def match(self, pattern, path):
        return bool(ci_changes.glob_regex(pattern).match(path))

    def test_double_star_crosses_segments(self):
        self.assertTrue(self.match("kubernetes/**", "kubernetes/apps/a/b.yaml"))
        self.assertTrue(self.match(".github/**", ".github/x"))
        self.assertFalse(self.match("kubernetes/**", "kubernetesx/a"))

    def test_single_star_stays_in_segment(self):
        self.assertTrue(self.match("infrastructure/ansible/scripts/*monitoring*.py", "infrastructure/ansible/scripts/test-monitoring-check-mode.py"))
        self.assertFalse(self.match("infrastructure/ansible/scripts/*monitoring*.py", "infrastructure/ansible/scripts/sub/monitoring.py"))
        self.assertTrue(self.match(".github/scripts/dependency-compatibility*", ".github/scripts/dependency-compatibility.py"))

    def test_leading_double_star_dir(self):
        self.assertTrue(self.match("**/*.py", "a.py"))
        self.assertTrue(self.match("**/*.py", "a/b/c.py"))

    def test_literal_dots_and_exact(self):
        self.assertTrue(self.match("bin/ci-changes", "bin/ci-changes"))
        self.assertFalse(self.match("bin/ci-changes", "bin/ci-changes.bak"))
        self.assertFalse(self.match(".github/zizmor.yml", ".github/zizmorXyml"))


class Domains(unittest.TestCase):
    def test_media_bump_is_kubernetes_and_observability_contracts_only(self):
        domains, groups = on(["kubernetes/apps/media/sonarr/app/helmrelease.yaml"])
        self.assertEqual(domains, {"kubernetes", "observability"})
        self.assertEqual(groups["terraform"], [])

    def test_monitoring_change_runs_rehearsals(self):
        domains, _ = on(["kubernetes/apps/monitoring/grafana/app/helmrelease.yaml"])
        self.assertIn("observability_rehearsals", domains)

    def test_home_assistant(self):
        domains, _ = on(["kubernetes/apps/home-automation/home-assistant/app/config/automations.yaml"])
        self.assertEqual(domains, {"kubernetes", "observability", "home_assistant"})

    def test_docs_only_runs_nothing(self):
        domains, groups = on(["docs/operations/ci.md", "README.md", ".changes/unreleased/x.yaml"])
        self.assertEqual(domains, set())
        self.assertTrue(all(v == [] for v in groups.values()))

    def test_terraform_group_lists_changed_roots(self):
        _, groups = on([
            "infrastructure/terraform/modules/aws/cognito.tf",
            "infrastructure/terraform/environments/prod/unifi/main.tf",
        ])
        self.assertEqual(groups["terraform"], ["aws", "unifi"])

    def test_shared_action_plans_every_root_using_it(self):
        _, groups = on([".github/actions/terraform/action.yml"])
        self.assertEqual(groups["terraform"], ["cloudflare", "healthchecks", "mikrotik", "unifi"])

    def test_terraform_workflow_plans_every_root(self):
        _, groups = on([".github/workflows/terraform.yaml"])
        self.assertEqual(groups["terraform"], sorted(CONFIG["groups"]["terraform"]))

    def test_group_members_carry_their_fields(self):
        out = ci_changes.evaluate(CONFIG, ["infrastructure/terraform/modules/mikrotik/main.tf"])
        (member,) = out["terraform"]
        self.assertEqual(member["name"], "mikrotik")
        self.assertEqual(member["runner"], "tnwks-ops-runner")
        self.assertEqual(member["lock"], "mikrotik-terraform")
        self.assertNotIn("paths", member)
        for name, spec in CONFIG["groups"]["terraform"].items():
            for field in ("title", "root", "runner", "backend"):
                self.assertIn(field, spec, f"{name} has no {field}")
            self.assertIn(spec["backend"], ("local", "tfc-remote"), name)

    def test_ignore_always_for_deploy(self):
        out = ci_changes.evaluate(CONFIG, [".github/workflows/ci.yaml"], ignore_always=True)
        self.assertEqual(out["terraform"], [])
        self.assertFalse(out["kubernetes"])

    def test_ci_change_turns_everything_on(self):
        for path in [".github/ci/domains.toml", ".github/workflows/ci.yaml", "bin/ci-changes"]:
            domains, groups = on([path])
            self.assertEqual(domains, set(CONFIG["domains"]), path)
            self.assertEqual(groups["terraform"], sorted(CONFIG["groups"]["terraform"]), path)

    def test_no_file_list_means_everything(self):
        domains, groups = on(None)
        self.assertEqual(domains, set(CONFIG["domains"]))
        self.assertEqual(len(groups["terraform"]), len(CONFIG["groups"]["terraform"]))


class Output(unittest.TestCase):
    def test_render_is_github_output_lines(self):
        out = ci_changes.evaluate(CONFIG, ["infrastructure/ansible/site.yml"])
        lines = ci_changes.render(out, ["infrastructure/ansible/site.yml"])
        self.assertIn("files=1", lines)
        self.assertIn("ansible=true", lines)
        self.assertIn("kubernetes=false", lines)
        self.assertIn('terraform=[]', lines)
        for line in lines:
            key, _, value = line.partition("=")
            self.assertRegex(key, r"^[a-z_]+$")
            self.assertNotIn("\n", value)

    def test_missing_base_means_everything(self):
        self.assertIsNone(ci_changes.changed_files("0" * 40, "HEAD"))
        self.assertIsNone(ci_changes.changed_files(None, "HEAD"))
        self.assertIsNone(ci_changes.changed_files("not-a-commit", "HEAD"))


if __name__ == "__main__":
    unittest.main()
