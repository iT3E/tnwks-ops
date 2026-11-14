"""Daily check of the scheduled turn (heartbeat) of each personal agent.

The heartbeat of the product runs once a day in its own session and writes
a trace to Tempo through the collector. For each service in SERVICES, this
asks Tempo for a completed heartbeat run (a span `openclaw.run` with
`openclaw.trigger=heartbeat` and `openclaw.outcome=completed`) in the last
WINDOW_HOURS. It prints one line for each service:

    heartbeat-check result=ok service=<name> last=<time of the newest run>
    heartbeat-check result=missing service=<name>
    heartbeat-check result=error service=<name> error=<what failed>

and exits 1 when a service is missing or Tempo could not answer, which fails
its Job. A missing run or an error is asked again ATTEMPTS times in all,
WAIT_SECONDS apart, before it counts: one short break of Tempo must not fail
the day's check. It reads span names and two attributes only, never a span's
text.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

TEMPO = os.environ.get("TEMPO_URL", "http://tempo.monitoring.svc.cluster.local:3200")
SERVICES = [s.strip() for s in os.environ.get("SERVICES", "").split(",") if s.strip()]
WINDOW_HOURS = int(os.environ.get("WINDOW_HOURS", "26"))
ATTEMPTS = int(os.environ.get("ATTEMPTS", "3"))
WAIT_SECONDS = int(os.environ.get("WAIT_SECONDS", "60"))
TIMEOUT_SECONDS = 30
ERRORS = (urllib.error.URLError, TimeoutError, ValueError, KeyError)


def query(service: str) -> str:
    return (
        f'{{resource.service.name="{service}" && name="openclaw.run"'
        ' && span.openclaw.trigger="heartbeat" && span.openclaw.outcome="completed"}'
    )


def newest_run(service: str, now: int) -> int | None:
    """The start (Unix seconds) of the newest completed heartbeat, or None."""
    params = urllib.parse.urlencode({
        "q": query(service),
        "start": now - WINDOW_HOURS * 3600,
        "end": now,
        "limit": 50,
    })
    with urllib.request.urlopen(f"{TEMPO}/api/search?{params}", timeout=TIMEOUT_SECONDS) as answer:
        body = json.load(answer)
    starts = [int(t["startTimeUnixNano"]) // 1_000_000_000 for t in body.get("traces") or []]
    return max(starts) if starts else None


def check(service: str, now: int, ask=None, wait=None) -> str:
    """The result line of one service, after up to ATTEMPTS tries."""
    ask = ask or newest_run
    wait = wait or time.sleep
    line = ""
    for attempt in range(1, ATTEMPTS + 1):
        if attempt > 1:
            wait(WAIT_SECONDS)
        try:
            last = ask(service, now)
        except ERRORS as error:
            line = f"heartbeat-check result=error service={service} error={type(error).__name__}"
        else:
            if last is not None:
                when = datetime.fromtimestamp(last, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                return f"heartbeat-check result=ok service={service} last={when} attempt={attempt}"
            line = f"heartbeat-check result=missing service={service} window_hours={WINDOW_HOURS}"
        if attempt < ATTEMPTS:
            print(f"heartbeat-check retry service={service} attempt={attempt}", flush=True)
    return f"{line} attempts={ATTEMPTS}"


def main() -> int:
    if not SERVICES:
        print("heartbeat-check result=error service=none error=SERVICES is empty")
        return 1
    now = int(time.time())
    failed = False
    for service in SERVICES:
        line = check(service, now)
        print(line)
        failed = failed or " result=ok " not in line
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
