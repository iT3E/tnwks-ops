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
so the rules of the registry read the check as "no evidence" for this
distro, and never as a pass. Old package lists are a failed query too: a
simulation on old lists can not see a new upgrade. The age that counts is
the one of the oldest list of the updates and the security fixes of the
distribution (a Release file with the Origin Ubuntu or Debian and a Suite
that ends in -updates or -security), so a fresh list of another source does
not hide an old one. With no such list, the run has no result. A state file
that is there and can not be read is a failed query, and the run then
leaves it as it is, so it does not start the clock of each upgrade again.
The file goes to its place in one step, so windows_exporter never reads
half a file.

The write test comes first in each run (Design Doc 0004, section 5.9). A try
makes a file in the textfile directory of the host, above the directory of
the distro, with a name that does not end in .prom, so the exporter never
reads it. Only a refusal by Windows is the pass. Linux checks the mode of the
directory first, as /mnt/c shows it, and a refusal there never reaches
Windows: a try asks the kernel first (access() with the effective ids), and
such a try is no evidence. Root passes that check, so the try as root is the
one that reaches Windows: the service runs it before the check, with full
privileges, and writes its answer to --write-test-result. The check reads
that answer, and also tries as its own user. The run counts the failed query
textfile-write when the answer of root is not a refusal by Windows (or is
missing or old), or when a try succeeds, or when the directory is gone or can
not be listed. A try removes the file that it made, and nothing else. Each
try is a series homelab_security_host_write_test_result with the uid of its
user: 0 refused by Windows, 1 the write succeeded, 2 refused by Linux before
Windows, 3 not done. With --write-test-only, the script runs one try alone,
prints its answer with its uid, and exits with that code.

The result carries the label severity: high, as Design Doc 0004, section
4.1.4, alerts for updates pending for more than 14 days.

The text of each series that the Windows host check also writes is the
same as there: a series with two texts would stop the exporter from serving
it. security/tests/test_host_check_distro.py checks that, and tests this
file with fake answers.
"""

from __future__ import annotations

import argparse
import email.utils
import errno
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SOURCE = "host-check"
SEVERITY = {"updates-pending": "high"}
# The lists whose age counts: the updates and the security fixes of the distribution.
ORIGINS = frozenset({"Ubuntu", "Debian"})
SUITES = ("-updates", "-security")

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
        "The Unix time of the oldest package list of the updates and the security fixes of the host,"
        " from the Date of its Release file."
    ),
    "homelab_security_host_reboot_pending": (
        "Whether the host waits for a restart to finish an update: 1 yes, 0 no."
    ),
    "homelab_security_host_write_test_result": (
        "The write test of a WSL distro to the textfile directory of its host, by the uid that tried:"
        " 0 refused by Windows, 1 the write succeeded, 2 refused by Linux before Windows, 3 not done."
    ),
}

# The answers of a try of the write test.
REFUSED, WRITTEN, LINUX_REFUSED, NOT_DONE = 0, 1, 2, 3
# The answer of the try as root counts when it is this new, in seconds.
ROOT_RESULT_MAX_AGE = 900
# The owner that the file of that answer must have: root wrote it. The distro user
# owns the runtime directory, so it could put a file of its own in that place.
ROOT_UID = 0
# The mode of that file: the check reads it as the distro user, with no capability.
ROOT_RESULT_MODE = 0o644

# "Inst <package> [<installed version>] (<new version> ...)": an upgrade. A
# line with no installed version is a new package that an upgrade pulls in.
INST = re.compile(r"^Inst (\S+) \[")
FIELD = re.compile(r"^(Origin|Suite|Date): (.+)$", re.MULTILINE)


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
    """The oldest Date of the Release files of the updates and the security fixes of the
    distribution: Origin Ubuntu or Debian, and a Suite that ends in -updates or -security.
    A list of another source, or of the base suite, does not count."""
    oldest = None
    for path in sorted([*lists_dir.glob("*_InRelease"), *lists_dir.glob("*_Release")]):
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                head = handle.read(8192)
        except OSError:
            continue
        fields = {}
        for name, value in FIELD.findall(head):
            fields.setdefault(name, value.strip())
        if fields.get("Origin") not in ORIGINS or not fields.get("Suite", "").endswith(SUITES):
            continue
        try:
            moment = email.utils.parsedate_to_datetime(fields.get("Date", "")).timestamp()
        except (TypeError, ValueError):
            raise QueryError(f"{path.name} has no Date that can be read") from None
        oldest = moment if oldest is None else min(oldest, moment)
    if oldest is None:
        raise QueryError(f"no Release file of the updates or the security fixes in {lists_dir}")
    return oldest


def mount_of(path: Path, mountinfo: Path = Path("/proc/self/mountinfo")) -> str:
    """The type and the options of the mount that holds path, for the message."""
    best, found = "", "unknown"
    try:
        lines = mountinfo.read_text().splitlines()
    except OSError:
        return found
    text = str(path)
    for line in lines:
        fields = line.split()
        if " - " not in line or len(fields) < 6:
            continue
        point = fields[4].replace("\\040", " ")
        if (text == point or text.startswith(point.rstrip("/") + "/")) and len(point) >= len(best):
            after = line.split(" - ", 1)[1].split()
            best, found = point, f"{after[0]} {fields[5]},{after[2] if len(after) > 2 else ''}".rstrip(",")
    return found


def write_test(directory: Path) -> tuple[int, int, str]:
    """One try to make a file in the textfile directory of the host, as this process:
    (uid, answer, message). Only a refusal by Windows is REFUSED. The kernel answers
    first by the mode that /mnt/c shows (access() with the effective ids asks the same
    check as open()), and a try that Linux refuses never reaches Windows. A try that
    made a file removes it, and touches no other file."""
    uid = os.geteuid()
    user = f"uid {uid}"
    try:
        os.listdir(directory)
        mode = os.stat(directory)
    except OSError as error:
        return uid, NOT_DONE, f"{user}: the textfile directory {directory} can not be listed ({error.strerror})"
    shown = f"{stat.filemode(mode.st_mode)} {mode.st_uid}:{mode.st_gid}, mount {mount_of(directory)}"
    if not os.access(directory, os.W_OK | os.X_OK, effective_ids=True):
        return uid, LINUX_REFUSED, f"{user}: Linux refuses the write by the mode of the directory ({shown}), so the try does not reach Windows"
    # Not .prom: windows_exporter reads .prom files only, so it never reads this one.
    path = directory / f".tnwks-write-test-{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except PermissionError as error:
        # EACCES or EPERM: Python raises PermissionError for both.
        return uid, REFUSED, (f"{user}: Windows refused the write"
                              f" (errno {error.errno} {errno.errorcode.get(error.errno, '?')}; {shown})")
    except OSError as error:
        # The errno, by number and name: the first live answer says how drvfs maps a refusal.
        return uid, NOT_DONE, (f"{user}: a write gave neither a file nor a refusal"
                               f" (errno {error.errno} {errno.errorcode.get(error.errno, '?')}: {error.strerror})")
    os.close(fd)
    try:
        path.unlink()
    except OSError as error:
        return uid, WRITTEN, f"{user}: the write succeeded, and {path.name} could not be removed ({error.strerror})"
    return uid, WRITTEN, f"{user}: the write succeeded, and the file is removed"


def read_root_result(path: Path, now: float) -> tuple[int, int, str]:
    """The answer of the try as root, from the step before the check. A missing, old or
    odd answer is NOT_DONE, and so is a file that root does not own, or a link."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as handle:
            owner = os.fstat(handle.fileno()).st_uid
            if owner != ROOT_UID:
                raise ValueError(f"the file is owned by uid {owner}, not by root")
            data = json.loads(handle.read(65536))
        uid, answer, moment, message = data["uid"], data["answer"], data["time"], data["message"]
        if data.get("version") != 1 or uid != 0 or answer not in (REFUSED, WRITTEN, LINUX_REFUSED, NOT_DONE):
            raise ValueError("not an answer of root")
        if not isinstance(moment, (int, float)) or not now - ROOT_RESULT_MAX_AGE <= moment <= now + 60:
            raise ValueError("not an answer of this run")
        return 0, answer, str(message)[:300]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return 0, NOT_DONE, f"uid 0: the try as root left no answer ({type(error).__name__}: {error})"


def write_failed(tries: list[tuple[int, int, str]]) -> bool:
    """The failed query textfile-write: root has no refusal by Windows, or a try wrote,
    or a try could not be done. A refusal by Linux for another user is no evidence, and
    not a fail."""
    if REFUSED not in [answer for uid, answer, _ in tries if uid == 0]:
        return True
    return any(answer in (WRITTEN, NOT_DONE) for _, answer, _ in tries)


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


def write_atomic(path: Path, text: str, mode: int | None = None) -> None:
    """Write next to the place, then put the file in place in one step. mkstemp makes
    the file 0600; mode, when given, is set before the file is in place."""
    fd, partial = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".partial")
    try:
        if mode is not None:
            os.fchmod(fd, mode)
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
    writes=lambda: [(0, REFUSED, "uid 0: not tried")],
) -> dict:
    """Run each query. state is None when the state file could not be read. writes is the
    write test, and it runs first."""
    results: dict[str, int] = {}
    details: list[tuple[str, dict[str, str], float]] = []
    failed: list[str] = []
    new_state = dict(state or {})

    tries = writes()
    for uid, answer, message in tries:
        details.append(("homelab_security_host_write_test_result", {"uid": str(uid)}, answer))
        print(f"textfile-write: {message}", file=sys.stderr)
    if write_failed(tries):
        failed.append("textfile-write")

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
            labels = {"source": SOURCE, "check": check, "system": system, "severity": SEVERITY[check]}
            add("homelab_security_check_result", labels, value)
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
    parser.add_argument("--textfile-dir", type=Path, required=True, help="the directory of the .prom file")
    parser.add_argument("--write-test-dir", type=Path, required=True,
                        help="the textfile directory of the host, which the distro must not be able to change")
    parser.add_argument("--write-test-only", action="store_true",
                        help="run one try of the write test alone, and exit with its answer (0 refused by Windows)")
    parser.add_argument("--write-test-result", type=Path,
                        help="with --write-test-only: write the answer there; else: read the answer of root there")
    parser.add_argument("--file-name", default="tnwks_security_host_check_distro.prom")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--updates-max-age-days", type=int, default=14)
    parser.add_argument("--lists-max-age-hours", type=int, default=72)
    parser.add_argument("--lists-dir", type=Path, default=Path("/var/lib/apt/lists"))
    parser.add_argument("--reboot-file", type=Path, default=Path("/var/run/reboot-required"))
    args = parser.parse_args(argv)

    if args.write_test_only:
        uid, answer, message = write_test(args.write_test_dir)
        if args.write_test_result:
            # 0644: the check reads it as the distro user.
            write_atomic(args.write_test_result, json.dumps(
                {"version": 1, "uid": uid, "answer": answer, "time": time.time(), "message": message}) + "\n",
                ROOT_RESULT_MODE)
        print(f"write test of {args.system}: {message}")
        return answer
    if args.state_file is None:
        parser.error("--state-file is required")

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
        writes=lambda: [read_root_result(args.write_test_result, now) if args.write_test_result
                        else (0, NOT_DONE, "uid 0: no answer of root to read"),
                        write_test(args.write_test_dir)],
    )
    write_atomic(args.textfile_dir / args.file_name, render(args.system, measured, now))
    # A state file that is there and can not be read stays as it is: a new one would
    # start the clock of each pending upgrade again. A missing one (the first run) is
    # written.
    if state is not None:
        write_atomic(args.state_file, json.dumps({"version": 1, "first_pending": measured["state"]}, indent=1) + "\n")
    if measured["failed"]:
        print(f"host check of {args.system}: no result, these queries failed: {', '.join(measured['failed'])}")
        return 1
    print(f"host check of {args.system}: {len(measured['results'])} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
