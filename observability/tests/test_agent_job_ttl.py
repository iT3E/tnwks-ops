"""The time to live of the finished Jobs of the agent platform.

Each CronJob in a trust domain (agents-*) and in ai deletes a finished Job
after 26 hours, also a failed one. AgentPlatformCronJobFailed and the state
of a schedule on the AI board (homelab:ai_schedule_state) read the newest
failed Job of a CronJob, so the failed Job must stay until a newer run has
ended: a daily run comes 24 hours after the one before. The one-off Jobs
that Flux applies get no time to live: Flux makes a deleted Job again at
its next reconcile, and the Job runs again (a restore, a seed).
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
AI = ROOT / "kubernetes/apps/ai"
TTL = 26 * 3600
# The CronJob of the heartbeat check is in monitoring, not in a trust domain.
OUTSIDE = {"kubernetes/apps/ai/agents/monitoring/app/heartbeat-check.yaml"}


def docs(kind):
    for path in sorted(AI.rglob("*.yaml")):
        text = path.read_text()
        if f"kind: {kind}\n" not in text:
            continue
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict) and doc.get("kind") == kind:
                yield str(path.relative_to(ROOT)), doc


def test_each_cronjob_of_the_trust_domains_and_ai_deletes_a_finished_job_after_26_hours():
    seen = []
    for path, cron in docs("CronJob"):
        if path in OUTSIDE:
            continue
        spec = cron["spec"]
        seen.append(cron["metadata"]["name"])
        assert spec["jobTemplate"]["spec"].get("ttlSecondsAfterFinished") == TTL, (path, cron["metadata"]["name"])
        # The time to live is the limit; the history limits stay as before.
        assert spec["failedJobsHistoryLimit"] == 3 and spec["successfulJobsHistoryLimit"] == 1, path
    assert len(seen) == 10, seen


def test_a_failed_job_stays_until_the_next_run_has_ended():
    # The longest period is a day, and a run ends within the deadline of its
    # Job (all its tries). A failed Job must outlive the start of the next
    # run plus that time, so the alert stays until a newer run decides it.
    for path, cron in docs("CronJob"):
        if path in OUTSIDE:
            continue
        job = cron["spec"]["jobTemplate"]["spec"]
        assert job["ttlSecondsAfterFinished"] >= 24 * 3600 + job["activeDeadlineSeconds"], path


def test_no_one_off_job_of_flux_has_a_time_to_live():
    jobs = list(docs("Job"))
    assert jobs
    for path, job in jobs:
        assert "ttlSecondsAfterFinished" not in job["spec"], (path, job["metadata"]["name"])
