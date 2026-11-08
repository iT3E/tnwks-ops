"""Tests for .github/scripts/ci-stats.py.

    python3 -m unittest discover -s .github/scripts -p 'test_ci_stats.py' -v
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("ci_stats", Path(__file__).with_name("ci-stats.py"))
ci_stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_stats)

NOW = dt.datetime(2026, 11, 8, 12, tzinfo=dt.timezone.utc)
N = iter(range(1, 10**6))


def run(name="CI", event="pull_request", conclusion="success", status="completed",
        attempt=1, hours_ago=2, minutes=3, actor="iT3E"):
    created = NOW - dt.timedelta(hours=hours_ago)
    n = next(N)
    return {
        "id": n, "run_number": n, "name": name, "event": event, "status": status,
        "conclusion": conclusion if status == "completed" else None, "run_attempt": attempt,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "updated_at": (created + dt.timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
        "actor": {"login": actor}, "html_url": f"https://example.invalid/{n}",
    }


SMALL_CACHE = {"active_caches_size_in_bytes": 10**9, "active_caches_count": 10}


class Summary(unittest.TestCase):
    def test_healthy_window_has_no_alerts(self):
        runs = [run() for _ in range(60)] + [run(name="PR #12"), run(name="Push on main")]
        report, alerts = ci_stats.summarize(runs, SMALL_CACHE, NOW, 7)
        self.assertEqual(alerts, [])
        self.assertIn("| CodeQL (default setup) |", report)
        self.assertIn("Flake rate (passed only on a re-run): **0.0%**", report)

    def test_rerun_passes_count_as_flakes(self):
        runs = [run() for _ in range(90)] + [run(attempt=2) for _ in range(10)]
        _, alerts = ci_stats.summarize(runs, SMALL_CACHE, NOW, 7)
        self.assertEqual(len(alerts), 1)
        self.assertIn("flake rate 10.0%", alerts[0])

    def test_skipped_and_cancelled_ci_runs_are_not_counted(self):
        runs = [run(attempt=2)] + [run(conclusion="cancelled") for _ in range(100)]
        report, _ = ci_stats.summarize(runs, SMALL_CACHE, NOW, 7)
        self.assertIn("(1 of 1)", report)

    def test_runs_outside_the_window_are_ignored(self):
        runs = [run(), run(hours_ago=24 * 8, attempt=2)]
        report, alerts = ci_stats.summarize(runs, SMALL_CACHE, NOW, 7)
        self.assertEqual(alerts, [])
        self.assertIn("1 runs, ", report)

    def test_big_cache_alerts(self):
        _, alerts = ci_stats.summarize([run()], {"active_caches_size_in_bytes": 10 * 10**9}, NOW, 7)
        self.assertEqual(alerts, ["Actions cache 10.0 GB is over 9 GB"])

    def test_waiting_over_an_hour_alerts(self):
        runs = [run(name="UniFi Plan", status="waiting", hours_ago=3), run(status="queued", hours_ago=0.5)]
        report, alerts = ci_stats.summarize(runs, SMALL_CACHE, NOW, 7)
        self.assertEqual(len(alerts), 1)
        self.assertIn("UniFi Plan", alerts[0])
        self.assertIn("waiting since", report)

    def test_time_to_result_per_event(self):
        runs = [run(event="push", minutes=m) for m in (1, 2, 3, 4, 10)]
        report, _ = ci_stats.summarize(runs, SMALL_CACHE, NOW, 7)
        self.assertIn("| push | 5 | 0 | 180 s | 600 s |", report)


if __name__ == "__main__":
    unittest.main()
