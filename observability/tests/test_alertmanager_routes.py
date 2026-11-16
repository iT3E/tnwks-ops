"""The routes of kubernetes/apps/monitoring/alertmanager/app/config/alertmanager.yml.

Each case asks `amtool config routes test`, from the Alertmanager image of the
HelmRelease, which receiver an alert with these labels reaches. Both WSL
clusters read this file.

Each test needs Docker. Without it a test is skipped, except in CI, where it
fails.
"""

import subprocess

import pytest
import yaml
from test_hand_written_rules import ROOT, docker

APP = ROOT / "kubernetes/apps/monitoring/alertmanager/app"
CONFIG = APP / "config/alertmanager.yml"


def alertmanager_image():
    release = yaml.safe_load((APP / "helmrelease.yaml").read_text())
    found = []

    def walk(node):
        if isinstance(node, dict):
            image = node.get("image")
            if isinstance(image, dict) and "alertmanager" in str(image.get("repository", "")):
                found.append(f"{image['repository']}:{image['tag']}")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(release)
    assert len(found) == 1, found
    return found[0]


# (labels, receiver). KubeJobFailed of the agent platform's own Jobs goes
# nowhere: AgentPlatformCronJobFailed and AgentWorkflowRunFailed cover them.
# Every other failed Job still reaches the channel.
CASES = [
    ({"alertname": "KubeJobFailed", "namespace": "agents-workflows", "job_name": "run-0123456789abcdef0123"}, "null"),
    ({"alertname": "KubeJobFailed", "namespace": "agents-workflows", "job_name": "workflow-providers-29900000"}, "null"),
    ({"alertname": "KubeJobFailed", "namespace": "agents-workflows", "job_name": "workflow-sweep-29900000"}, "null"),
    ({"alertname": "KubeJobFailed", "namespace": "agents-finance", "job_name": "platform-check-probe-29900000"}, "null"),
    ({"alertname": "KubeJobFailed", "namespace": "agents-main", "job_name": "platform-check-isolation-29900000"}, "null"),
    # A backup mover of a trust domain, a Job made by hand, a new CronJob.
    ({"alertname": "KubeJobFailed", "namespace": "agents-pilot", "job_name": "agent-state-20260101103000"}, "telegram"),
    ({"alertname": "KubeJobFailed", "namespace": "agents-workflows", "job_name": "providers-now"}, "telegram"),
    ({"alertname": "KubeJobFailed", "namespace": "agents-pilot", "job_name": "platform-check-other-29900000"}, "telegram"),
    # Names that only look like a run job (run-<20 hex>, agents-workflows
    # only): the matcher must match the whole name.
    ({"alertname": "KubeJobFailed", "namespace": "agents-pilot", "job_name": "run-by-hand"}, "telegram"),
    ({"alertname": "KubeJobFailed", "namespace": "agents-workflows", "job_name": "run-by-hand"}, "telegram"),
    ({"alertname": "KubeJobFailed", "namespace": "agents-pilot", "job_name": "run-0123456789abcdef0123"}, "telegram"),
    ({"alertname": "KubeJobFailed", "namespace": "agents-workflows", "job_name": "run-0123456789abcdef0123-x"}, "telegram"),
    ({"alertname": "KubeJobFailed", "namespace": "agents-workflows", "job_name": "run-0123456789ABCDEF0123"}, "telegram"),
    ({"alertname": "KubeJobFailed", "namespace": "agents-workflows", "job_name": "my-run-0123456789abcdef0123"}, "telegram"),
    # The CronJobs of agents-workflows in another trust domain.
    ({"alertname": "KubeJobFailed", "namespace": "agents-pilot", "job_name": "workflow-providers-29900000"}, "telegram"),
    # The same names outside a trust domain, and the household apps.
    ({"alertname": "KubeJobFailed", "namespace": "default", "job_name": "run-0123456789abcdef0123"}, "telegram"),
    ({"alertname": "KubeJobFailed", "namespace": "production", "job_name": "finance-sync-29900000"}, "telegram"),
    ({"alertname": "KubeJobFailed", "namespace": "kopiur-system", "job_name": "aws-backup-credentials-agents-29900000"}, "telegram"),
    # The platform's own alerts still reach the channel.
    ({"alertname": "AgentPlatformCronJobFailed", "namespace": "agents-finance", "cronjob": "platform-check-probe"}, "telegram"),
    ({"alertname": "AgentWorkflowRunFailed", "namespace": "agents-workflows", "job_name": "run-0123456789abcdef0123"}, "telegram"),
]


@pytest.mark.parametrize(("labels", "receiver"), CASES,
                         ids=[f"{c[0]['alertname']}-{c[0]['namespace']}-{c[0].get('job_name', c[0].get('cronjob'))}" for c in CASES])
def test_route(labels, receiver):
    binary = docker()
    result = subprocess.run([
        binary, "run", "--rm", "--network", "none",
        "--mount", f"type=bind,src={CONFIG},dst=/t/alertmanager.yml,readonly",
        "--entrypoint", "amtool", alertmanager_image(),
        "config", "routes", "test", "--config.file=/t/alertmanager.yml",
        f"--verify.receivers={receiver}",
        *[f"{key}={value}" for key, value in labels.items()],
    ], capture_output=True, text=True, timeout=300, check=False)
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
