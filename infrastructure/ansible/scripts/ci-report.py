#!/usr/bin/env python3
"""Summarize an ansible-playbook run for CI (.github/workflows/ansible-hosts.yaml).

The repo is public and so are its Actions logs and PR comments, so this prints
only what the repo itself already says: per host the PLAY RECAP counts, and
the names of the tasks that changed or failed. Diffs, item labels and error
messages stay in the full log, which the workflow keeps age-encrypted.

    ci-report.py --log run.log --site site_wsl --hosts A,B --mode check \
        [--rc N] [--duration S] [--summary out.md] [--prom out.prom]

--prom writes the drift-check metrics for windows_exporter's textfile
collector (the WindowsHostConfigDrift alerts read them).
"""
import argparse
import re
import time

RECAP = re.compile(
    r"^(?P<host>\S+)\s+:\s+ok=(?P<ok>\d+)\s+changed=(?P<changed>\d+)\s+"
    r"unreachable=(?P<unreachable>\d+)\s+failed=(?P<failed>\d+)\s+skipped=(?P<skipped>\d+)"
)
HEADER = re.compile(r"^(?:TASK|RUNNING HANDLER) \[(?P<name>.+?)\] \*+$")
RESULT = re.compile(r"^(?P<status>changed|fatal|failed): \[(?P<host>[^\]]+)\]")
COUNTS = ("ok", "changed", "unreachable", "failed", "skipped")
PROM = "tnwks_windows_drift_check"


def parse(lines):
    totals, events, task = {}, [], None
    for line in lines:
        line = line.rstrip("\n")
        if m := HEADER.match(line):
            task = m["name"]
        elif m := RESULT.match(line):
            host = m["host"].split(" -> ")[0]
            status = "changed" if m["status"] == "changed" else "failed"
            if task and (host, status, task) not in events:
                events.append((host, status, task))
        elif m := RECAP.match(line):
            # One ansible-playbook run with several playbooks prints a recap
            # after each, cumulative: a host's last line is its total.
            totals[m["host"]] = {k: int(m[k]) for k in COUNTS}
    return totals, events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--site", required=True)
    ap.add_argument("--hosts", required=True, help="comma-separated hosts the run was for")
    ap.add_argument("--mode", required=True)
    ap.add_argument("--rc", type=int, default=0)
    ap.add_argument("--duration", type=int, default=0)
    ap.add_argument("--summary")
    ap.add_argument("--prom")
    a = ap.parse_args()

    with open(a.log, encoding="utf-8", errors="replace") as f:
        totals, events = parse(f)
    hosts = [h for h in a.hosts.split(",") if h]
    # A host without a recap line never ran: count it as failed.
    for h in hosts:
        totals.setdefault(h, dict(dict.fromkeys(COUNTS, 0), failed=1))

    rows = [h for h in hosts] + sorted(h for h in totals if h not in hosts)
    out = [f"#### Ansible {a.mode}: `{a.site}` (exit {a.rc})", "",
           "| Host | ok | changed | failed | unreachable | skipped |",
           "| --- | --: | --: | --: | --: | --: |"]
    for h in rows:
        t = totals[h]
        out.append(f"| `{h}` | {t['ok']} | {t['changed']} | {t['failed']} | {t['unreachable']} | {t['skipped']} |")
    for status in ("failed", "changed"):
        hits = [(h, n) for h, s, n in events if s == status]
        if hits:
            out += ["", f"**{status.capitalize()}:**", ""]
            out += [f"- `{h}`: {n}" for h, n in hits]
    if not events:
        out += ["", "No changes."]
    summary = "\n".join(out) + "\n"
    print(summary)
    if a.summary:
        with open(a.summary, "w", encoding="utf-8") as f:
            f.write(summary)

    if a.prom:
        lines = [
            f"# HELP {PROM}_timestamp_seconds When the nightly Ansible --check of this site finished.",
            f"# TYPE {PROM}_timestamp_seconds gauge",
            f"{PROM}_timestamp_seconds {int(time.time())}",
            f"# HELP {PROM}_exit_code Exit code of the ansible-playbook --check run (0 = clean).",
            f"# TYPE {PROM}_exit_code gauge",
            f"{PROM}_exit_code {a.rc}",
            f"# HELP {PROM}_duration_seconds How long the check took.",
            f"# TYPE {PROM}_duration_seconds gauge",
            f"{PROM}_duration_seconds {a.duration}",
        ]
        for k, help_ in (("changed", "Tasks the check would change (drift)."),
                         ("failed", "Failed tasks, including the check-only assertions."),
                         ("unreachable", "1 when Ansible could not reach the host.")):
            lines += [f"# HELP {PROM}_{k} {help_}", f"# TYPE {PROM}_{k} gauge"]
            lines += [f'{PROM}_{k}{{host="{h}"}} {min(totals[h][k], 1) if k == "unreachable" else totals[h][k]}'
                      for h in hosts]
        with open(a.prom, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
