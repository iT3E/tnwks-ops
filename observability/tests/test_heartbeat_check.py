"""The heartbeat check asks Tempo again before a missing run or an error counts.

kubernetes/apps/ai/agents/monitoring/app/scripts/heartbeat_check.py runs once
a day; one short break of Tempo at that time must not fail the day's check.
"""

import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "kubernetes/apps/ai/agents/monitoring/app/scripts/heartbeat_check.py"
NOW = 1_800_000_000
LAST = NOW - 5 * 3600


@pytest.fixture
def check():
    spec = importlib.util.spec_from_file_location("heartbeat_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def answers(*replies):
    """An ask() that gives each reply in turn; an exception is raised."""
    calls = iter(replies)

    def ask(service, now):
        reply = next(calls)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return ask


def test_an_error_then_a_run_is_ok_after_one_wait(check):
    waits = []
    line = check.check("svc", NOW, ask=answers(urllib.error.URLError("down"), LAST), wait=waits.append)
    assert line.startswith("heartbeat-check result=ok service=svc last=")
    assert line.endswith(" attempt=2")
    assert waits == [check.WAIT_SECONDS]


def test_a_run_on_the_first_try_does_not_wait(check):
    waits = []
    line = check.check("svc", NOW, ask=answers(LAST), wait=waits.append)
    assert " result=ok " in line
    assert waits == []


def test_missing_on_each_try_is_missing(check):
    waits = []
    line = check.check("svc", NOW, ask=answers(None, None, None), wait=waits.append)
    assert line == f"heartbeat-check result=missing service=svc window_hours={check.WINDOW_HOURS} attempts=3"
    assert waits == [check.WAIT_SECONDS] * 2


def test_a_missing_run_that_comes_later_is_ok(check):
    line = check.check("svc", NOW, ask=answers(None, None, LAST), wait=lambda _: None)
    assert line.endswith(" attempt=3")
    assert " result=ok " in line


def test_an_error_on_each_try_is_an_error(check):
    line = check.check("svc", NOW, ask=answers(TimeoutError(), ValueError(), urllib.error.URLError("x")),
                       wait=lambda _: None)
    assert line == "heartbeat-check result=error service=svc error=URLError attempts=3"


def test_main_fails_when_one_service_is_missing(check, monkeypatch, capsys):
    monkeypatch.setattr(check, "SERVICES", ["a", "b"])
    monkeypatch.setattr(check, "newest_run", answers(LAST, None, None, None))
    monkeypatch.setattr(check.time, "sleep", lambda _: None)
    assert check.main() == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("heartbeat-check result=ok service=a ")
    assert out[-1].startswith("heartbeat-check result=missing service=b ")


def test_services_are_the_running_agents_with_a_heartbeat():
    """SERVICES names each personal agent whose pod runs (replicas 1) and whose
    config has a heartbeat, by its service name, and nothing else. The list
    grows after the first heartbeat of a new agent is in Tempo (the comment
    of heartbeat-check.yaml); this test then holds it."""
    agents = ROOT / "kubernetes/apps/ai/agents"
    running = set()
    for config in sorted(agents.glob("*/agent/config/openclaw.json")):
        deployment = config.parents[1] / "app/deployment.yaml"
        docs = [d for d in yaml.safe_load_all(deployment.read_text()) if d and d["kind"] == "Deployment"]
        c = json.loads(config.read_text())
        if docs[0]["spec"]["replicas"] >= 1 and c["agents"]["defaults"].get("heartbeat", {}).get("every"):
            running.add(c["diagnostics"]["otel"]["serviceName"])
    cron = yaml.safe_load((ROOT / "kubernetes/apps/ai/agents/monitoring/app/heartbeat-check.yaml").read_text())
    (job,) = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    services = {e["name"]: e["value"] for e in job["env"]}["SERVICES"].split(",")
    assert len(services) == len(set(services))
    assert set(services) == running == {"openclaw-agents-pilot", "openclaw-agents-finance", "openclaw-agents-main"}
