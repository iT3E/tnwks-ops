#!/usr/bin/python3
"""The host check of the security platform in a WSL distro.

Managed by tnwks-ops (role wsl_host_check). The timer tnwks-host-check runs
this script every 6 hours. It reads, and it changes no setting of the
distro: apt-get only simulates.

It writes a .prom file into a directory of the Windows host's
windows_exporter (role windows_host_check makes the directory and lets the
distro write there). The file has the two series of the security registry
(security/README.md, "The series") for the source host-check, and a few
series of its own that say what it read. The check gives the number of items
that fail it, and 0 is a pass:

  updates-pending  each package with an upgrade that has waited more than
                   --updates-max-age-days since this check first saw it

A package list says nothing of when an upgrade came out, so the check keeps
the time at which it first saw each upgrade in its state file. The clock of
a package starts at the first run that sees its upgrade.

When one query fails, the file has no result and no time of the last result,
so the rules of the registry read the source as late for this distro
(SecurityResultLate), and never as a pass. Package lists older than
--lists-max-age-hours are a failed query too: a simulation on old lists
can not see a new upgrade. The file goes to its place in one step, so
windows_exporter never reads half a file.

The text of each series that the Windows host check also writes is the
same as there: a series with two texts would stop the exporter from serving
it. security/tests/test_host_check_distro.py checks that, and tests this
file with fake answers.
"""

from __future__ import annotations

import argparse
import email.utils
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SOURCE = "host-check"

HELP = {
    "homelab_security_check_result": (
        "The number of items that fail one check of the security registry. 0 is a pass."
    ),
    "homelab_security_last_result_timestamp_seconds": (
        "The Unix time of the newest result of a source of the security registry."
    ),
    "homelab_security_host_check_failed_queries": (
        "The number of queries of the host check that failed in its last run."
        " Above 0, the run gives no result."
    ),
    "homelab_security_host_updates_pending": (
        "The number of updates that the host can install and has not installed."
    ),
    "homelab_security_host_package_lists_timestamp_seconds": (
        "The Unix time of the newest package list of the host, from the Date of its Release files."
    ),
    "homelab_security_host_reboot_pending": (
        "Whether the host waits for a restart to finish an update: 1 yes, 0 no."
    ),
}

# "Inst <package> [<installed version>] (<new version> ...)": an upgrade. A
# line with no installed version is a new package that an upgrade pulls in.
INST = re.compile(r"^Inst (\S+) \[")
DATE = re.compile(r"^Date: (.+)$", re.MULTILINE)


class QueryError(Exception):
    """A query gave no answer that the check can trust."""


def pending_packages(run=subprocess.run) -> list[str]:
    """The packages that apt can upgrade now. apt-get only simulates."""
    try:
        proc = run(
            ["apt-get", "-s", "-o", "Debug::NoLocking=1", "dist-upgrade"],
            env={**os.environ, "LC_ALL": "C"},
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise QueryError(f"apt-get did not run: {error}") from error
    if proc.returncode != 0:
        raise QueryError(f"apt-get exited {proc.returncode}")
    return sorted({m.group(1) for line in proc.stdout.splitlines() if (m := INST.match(line))})


def lists_time(lists_dir: Path) -> float:
    """The newest Date of the Release files of the package lists."""
    newest = None
    for path in sorted([*lists_dir.glob("*_InRelease"), *lists_dir.glob("*_Release")]):
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                head = handle.read(8192)
        except OSError:
            continue
        match = DATE.search(head)
        if not match:
            continue
        try:
            moment = email.utils.parsedate_to_datetime(match.group(1).strip()).timestamp()
        except (TypeError, ValueError):
            continue
        newest = moment if newest is None else max(newest, moment)
    if newest is None:
        raise QueryError(f"no Release file with a Date in {lists_dir}")
    return newest


def read_state(path: Path) -> dict[str, float]:
    """The time at which the check first saw each pending upgrade."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        first = data["first_pending"]
        if data.get("version") != 1 or not isinstance(first, dict):
            raise ValueError("not version 1")
        return {str(name): float(seen) for name, seen in first.items()}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise QueryError(f"the state file {path} can not be read: {error}") from error


def write_atomic(path: Path, text: str) -> None:
    """Write next to the place, then put the file in place in one step."""
    fd, partial = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".partial")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(partial, path)
    except BaseException:
        Path(partial).unlink(missing_ok=True)
        raise


def measure(
    now: float,
    updates_max_age_days: int,
    lists_max_age_hours: int,
    state: dict[str, float] | None,
    packages=pending_packages,
    lists=lists_time,
    reboot=lambda: False,
) -> dict:
    """Run each query. state is None when the state file could not be read."""
    results: dict[str, int] = {}
    details: list[tuple[str, dict[str, str], float]] = []
    failed: list[str] = []
    new_state = dict(state or {})

    if state is None:
        failed.append("state")
        state = {}

    try:
        newest = lists()
        details.append(("homelab_security_host_package_lists_timestamp_seconds", {}, newest))
        if now - newest > lists_max_age_hours * 3600:
            failed.append("package-lists")
    except QueryError:
        failed.append("package-lists")

    try:
        pending = packages()
        new_state = {name: state.get(name, now) for name in pending}
        late = [name for name, seen in new_state.items() if now - seen > updates_max_age_days * 86400]
        results["updates-pending"] = len(late)
        details.append(("homelab_security_host_updates_pending", {}, len(pending)))
    except QueryError:
        failed.append("updates")

    # A fact that no check reads: a fault here leaves out its series, and
    # costs no result.
    try:
        details.append(("homelab_security_host_reboot_pending", {}, int(bool(reboot()))))
    except OSError:
        pass

    return {"results": results, "details": details, "failed": failed, "state": new_state}


def label_value(value: str) -> str:
    """The escapes of the text format of Prometheus."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def series(name: str, labels: dict[str, str], value: float) -> str:
    pairs = ",".join(f'{key}="{label_value(str(val))}"' for key, val in labels.items())
    return f"{name}{{{pairs}}} {int(value) if float(value).is_integer() else value}"


def render(system: str, measured: dict, now: float) -> str:
    """The text of the .prom file: each family once, with its HELP and TYPE."""
    families: dict[str, list[str]] = {}

    def add(name: str, labels: dict[str, str], value: float) -> None:
        families.setdefault(name, []).append(series(name, labels, value))

    if not measured["failed"]:
        for check, value in measured["results"].items():
            add("homelab_security_check_result", {"source": SOURCE, "check": check, "system": system}, value)
        add("homelab_security_last_result_timestamp_seconds", {"source": SOURCE, "system": system}, int(now))
    add("homelab_security_host_check_failed_queries", {"system": system}, len(measured["failed"]))
    for name, labels, value in measured["details"]:
        add(name, {"system": system, **labels}, value)
    lines = []
    for name, rows in families.items():
        lines += [f"# HELP {name} {HELP[name]}", f"# TYPE {name} gauge", *rows]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--system", required=True, help="the name of this distro in security/registry/systems.yaml")
    parser.add_argument("--textfile-dir", type=Path, required=True)
    parser.add_argument("--file-name", default="tnwks_security_host_check_distro.prom")
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--updates-max-age-days", type=int, default=14)
    parser.add_argument("--lists-max-age-hours", type=int, default=72)
    parser.add_argument("--lists-dir", type=Path, default=Path("/var/lib/apt/lists"))
    parser.add_argument("--reboot-file", type=Path, default=Path("/var/run/reboot-required"))
    args = parser.parse_args(argv)

    now = time.time()
    try:
        state = read_state(args.state_file)
    except QueryError:
        state = None
    measured = measure(
        now,
        args.updates_max_age_days,
        args.lists_max_age_hours,
        state,
        lists=lambda: lists_time(args.lists_dir),
        reboot=args.reboot_file.exists,
    )
    write_atomic(args.textfile_dir / args.file_name, render(args.system, measured, now))
    write_atomic(args.state_file, json.dumps({"version": 1, "first_pending": measured["state"]}, indent=1) + "\n")
    if measured["failed"]:
        print(f"host check of {args.system}: no result, these queries failed: {', '.join(measured['failed'])}")
        return 1
    print(f"host check of {args.system}: {len(measured['results'])} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
