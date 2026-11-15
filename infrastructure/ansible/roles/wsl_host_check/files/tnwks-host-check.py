#!/usr/bin/python3
"""The host check of the security platform in a WSL distro.

Managed by tnwks-ops (role wsl_host_check). The timer tnwks-host-check runs
this script every 6 hours. It reads, and it changes no setting of the
distro: apt-get only simulates.

It writes a JSON document into the inbox of its Windows host,
C:\\ProgramData\\tnwks-host-check-distro (Design Doc 0004, section 5.9). Role
windows_host_check makes the inbox, where only SYSTEM, the Administrators and
the account of the distro may change files, and runs a relay as SYSTEM each
hour. The relay checks the document, and writes the .prom file of the distro
for windows_exporter, with the name of the distro from the inventory. The
distro can not write the textfile directory itself. The document has a
version, the name of the distro, the time of the run, the result of each
check, the names of the failed queries and the detail values. Each check
gives the number of items that fail it, and 0 is a pass:

  updates-pending  each package with an upgrade that apt offers now, and
                   whose clock is older than --updates-max-age-days
  updates-held     each held package with a newer candidate, and whose
                   clock is older than --updates-max-age-days

A package list says nothing of when an upgrade came out, so the check keeps
a clock for each package in its state file (Design Doc 0004, sections 5.6
and 5.7). A second simulation, with --ignore-hold and with each phased
update, lists each upgrade that the sources offer: each package of that list
has one clock, whether it waits, is held or is phased, and keeps it when it
moves from one to another. updates-pending counts the upgrades of the plain
simulation, and updates-held the held packages of the second one.

The clock of an upgrade that a run sees first starts at the Date of its
package list at the last run with a result: the upgrade came out after that
Date. The simulation names the list of each upgrade (its Label, or Origin,
and its Suite). With no Date of its list in the state (no state file, a
state file of the old form, or a list that the last run did not have), the
clock starts at the time 0, so the age that is not known counts as old. Only
a run with a result, one in which no query failed, writes the state file.
A run that fails leaves it as it was, so a stop never moves a clock late.

When one query fails, the document has no result, only the names of the
failed queries, so the relay writes no result and no time of the last
result, and the rules of the registry read the check as "no evidence" for
this distro, and never as a pass. Old package lists are a failed query too: a
simulation on old lists can not see a new upgrade. The age that counts is
the one of the oldest list of the updates and the security fixes of the
distribution (a Release file with the Origin Ubuntu or Debian and a Suite
that ends in -updates or -security), so a fresh list of another source does
not hide an old one. With no such list, the run has no result. A state file
that is there and can not be read is a failed query, and the run then
leaves it as it is, so it does not start the clock of each upgrade again.
The document goes to its place in one step, so the relay never reads half a
file.

The write test comes first in each run (Design Doc 0004, section 5.9). A try
makes a file in the textfile directory of the host, with a name that does
not end in .prom, so the exporter never reads it. Only a refusal by Windows
is the pass. Linux checks the mode of the
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
Windows, 3 not done. The series homelab_security_host_write_test_errno gives
the errno of each try, or 0 when the try had none, so a read of the metrics
shows how drvfs maps a refusal. With --write-test-only, the script runs one try alone,
prints its answer with its uid, and exits with that code.

The relay gives each result the label severity: high (Design Doc 0004,
sections 4.1.4 and 5.7). security/tests/test_host_check_distro.py tests this
file with fake answers, and security/tests/test_host_check_windows.py tests
the relay with the documents of this file.
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

# The checks of a distro: the relay takes these only.
CHECKS = ("updates-pending", "updates-held")
# The lists whose age counts: the updates and the security fixes of the distribution.
ORIGINS = frozenset({"Ubuntu", "Debian"})
SUITES = ("-updates", "-security")

# The answers of a try of the write test.
REFUSED, WRITTEN, LINUX_REFUSED, NOT_DONE = 0, 1, 2, 3
# The answer of the try as root counts when it is this new, in seconds.
ROOT_RESULT_MAX_AGE = 900
# The owner that the file of that answer must have: root wrote it. The distro user
# owns the runtime directory, so it could put a file of its own in that place.
ROOT_UID = 0
# The mode of that file: the check reads it as the distro user, with no capability.
ROOT_RESULT_MODE = 0o644

# "Inst <package> [<installed version>] (<new version> <lists> [<arch>])": an
# upgrade. A line with no installed version is a new package that an upgrade
# pulls in. <lists> is "<Label>:<Version>/<Suite>" for each list of the new
# version, joined by ", " (apt names a list by its Label, or by its Origin when
# it has no Label).
UPGRADE = re.compile(r"^Inst \S+ \[")
INST = re.compile(r"^Inst (\S+) \[[^\]]*\] \((\S+)(?: ([^()]*?))?(?: \[[^\[\]]*\])?\)")
FIELD = re.compile(r"^(Origin|Label|Suite|Date): (.+)$", re.MULTILINE)
# The second simulation: each upgrade that the sources offer, held or phased.
EVERY_UPGRADE = ["-o", "APT::Get::Always-Include-Phased-Updates=true", "--ignore-hold"]


class QueryError(Exception):
    """A query gave no answer that the check can trust."""


def list_key(label: str, suite: str) -> str:
    """The name of a package list in the state file."""
    return f"{label}/{suite}"


def lists_of(text: str) -> tuple[str, ...]:
    """The lists of a new version, from the text that apt prints after it."""
    keys = []
    for item in text.split(", "):
        label, _, rest = item.rpartition(":") if ":" in item else ("", "", item)
        keys.append(list_key(label, rest.rpartition("/")[2]))
    return tuple(sorted(set(keys)))


def simulate(extra: list[str], run=subprocess.run) -> dict[str, tuple[str, ...]]:
    """The packages of a simulation of dist-upgrade, each with the lists of its new
    version. apt-get only simulates."""
    try:
        proc = run(
            ["apt-get", "-s", "-o", "Debug::NoLocking=1", *extra, "dist-upgrade"],
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
    found = {}
    for line in proc.stdout.splitlines():
        if UPGRADE.match(line):
            match = INST.match(line)
            if not match:
                raise QueryError(f"apt-get printed an upgrade that can not be read: {line[:200]}")
            found[match.group(1)] = lists_of(match.group(3)) if match.group(3) else ()
    return found


def pending_packages(run=subprocess.run) -> dict[str, tuple[str, ...]]:
    """The upgrades that apt offers now."""
    return simulate([], run)


def every_upgrade(run=subprocess.run) -> dict[str, tuple[str, ...]]:
    """Each upgrade that the sources offer: also a held package, and a phased update
    that the host is not yet eligible for."""
    return simulate(EVERY_UPGRADE, run)


def held_packages(run=subprocess.run) -> set[str]:
    """The packages that the host holds (apt-mark only reads)."""
    try:
        proc = run(["apt-mark", "showhold"], env={**os.environ, "LC_ALL": "C"},
                   capture_output=True, text=True, check=False, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        raise QueryError(f"apt-mark did not run: {error}") from error
    if proc.returncode != 0:
        raise QueryError(f"apt-mark exited {proc.returncode}")
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def release_files(lists_dir: Path):
    """The fields of each Release file of the package lists: (file name, fields)."""
    for path in sorted([*lists_dir.glob("*_InRelease"), *lists_dir.glob("*_Release")]):
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                head = handle.read(8192)
        except OSError:
            continue
        fields = {}
        for name, value in FIELD.findall(head):
            fields.setdefault(name, value.strip())
        yield path.name, fields


def date_of(fields: dict[str, str]) -> float:
    return email.utils.parsedate_to_datetime(fields.get("Date", "")).timestamp()


def lists_time(lists_dir: Path) -> float:
    """The oldest Date of the Release files of the updates and the security fixes of the
    distribution: Origin Ubuntu or Debian, and a Suite that ends in -updates or -security.
    A list of another source, or of the base suite, does not count."""
    oldest = None
    for name, fields in release_files(lists_dir):
        if fields.get("Origin") not in ORIGINS or not fields.get("Suite", "").endswith(SUITES):
            continue
        try:
            moment = date_of(fields)
        except (TypeError, ValueError):
            raise QueryError(f"{name} has no Date that can be read") from None
        oldest = moment if oldest is None else min(oldest, moment)
    if oldest is None:
        raise QueryError(f"no Release file of the updates or the security fixes in {lists_dir}")
    return oldest


def list_dates(lists_dir: Path) -> dict[str, float]:
    """The Date of each package list, by the name that the simulation gives it. A list
    with no Date that can be read is left out, so an upgrade from it counts as old."""
    dates: dict[str, float] = {}
    for _, fields in release_files(lists_dir):
        try:
            moment = date_of(fields)
        except (TypeError, ValueError):
            continue
        key = list_key(fields.get("Label") or fields.get("Origin", ""), fields.get("Suite", ""))
        dates[key] = min(moment, dates.get(key, moment))
    return dates


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
    odd answer is NOT_DONE, and so is a file that root does not own, a link, or a file
    that is not a regular file. O_NONBLOCK: a FIFO in that place opens at once, and does
    not hold the run until the service times out."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("not a regular file")
            if info.st_uid != ROOT_UID:
                raise ValueError(f"the file is owned by uid {info.st_uid}, not by root")
            handle = os.fdopen(fd, encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise
        with handle:
            data = json.loads(handle.read(65536))
        uid, answer, moment, message = data["uid"], data["answer"], data["time"], data["message"]
        if data.get("version") != 1 or uid != 0 or answer not in (REFUSED, WRITTEN, LINUX_REFUSED, NOT_DONE):
            raise ValueError("not an answer of root")
        if not isinstance(moment, (int, float)) or not now - ROOT_RESULT_MAX_AGE <= moment <= now + 60:
            raise ValueError("not an answer of this run")
        return 0, answer, str(message)[:300]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return 0, NOT_DONE, f"uid 0: the try as root left no answer ({type(error).__name__}: {error})"


ERRNO = re.compile(r"\(errno (\d+) ")


def errno_of(message: str) -> int:
    """The errno that the message of a try names, or 0. The message carries it, also
    the message of root through its answer file."""
    found = ERRNO.search(message)
    return int(found[1]) if found else 0


def write_failed(tries: list[tuple[int, int, str]]) -> bool:
    """The failed query textfile-write: root has no refusal by Windows, or a try wrote,
    or a try could not be done. A refusal by Linux for another user is no evidence, and
    not a fail."""
    if REFUSED not in [answer for uid, answer, _ in tries if uid == 0]:
        return True
    return any(answer in (WRITTEN, NOT_DONE) for _, answer, _ in tries)


def empty_state() -> dict:
    return {"first_seen": {}, "lists": {}}


def read_state(path: Path) -> dict:
    """The clock of each upgrade (first_seen), and the Date of each package list at the
    last run with a result (lists). The old form (version 1) has the clocks only."""
    if not path.exists():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") == 1:
            first, lists = data["first_pending"], {}
        elif data.get("version") == 2:
            first, lists = data["first_seen"], data["lists"]
        else:
            raise ValueError("not version 1 or 2")
        if not isinstance(first, dict) or not isinstance(lists, dict):
            raise TypeError("not a map")
        return {"first_seen": {str(name): float(seen) for name, seen in first.items()},
                "lists": {str(name): float(date) for name, date in lists.items()}}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise QueryError(f"the state file {path} can not be read: {error}") from error


def state_text(state: dict) -> str:
    return json.dumps({"version": 2, **state}, indent=1, sort_keys=True) + "\n"


def first_time(lists: tuple[str, ...], dates: dict[str, float]) -> float:
    """The clock of an upgrade that a run sees first: the oldest Date of its lists at the
    last run with a result, as it came out after that. With none, the time 0."""
    known = [dates[key] for key in lists if key in dates]
    return min(known) if known else 0.0


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
    state: dict | None,
    packages=pending_packages,
    lists=lists_time,
    reboot=lambda: False,
    writes=lambda: [(0, REFUSED, "uid 0: not tried")],
    upgrades=every_upgrade,
    holds=held_packages,
    dates=dict,
) -> dict:
    """Run each query. state is None when the state file could not be read. writes is the
    write test, and it runs first. The state that comes back is the one to write when no
    query failed."""
    results: dict[str, int] = {}
    details: list[tuple[str, dict[str, str], float]] = []
    failed: list[str] = []
    new_state = state or empty_state()

    tries = writes()
    for uid, answer, message in tries:
        details.append(("homelab_security_host_write_test_result", {"uid": str(uid)}, answer))
        details.append(("homelab_security_host_write_test_errno", {"uid": str(uid)}, errno_of(message)))
        print(f"textfile-write: {message}", file=sys.stderr)
    if write_failed(tries):
        failed.append("textfile-write")

    if state is None:
        failed.append("state")
        state = empty_state()

    try:
        newest = lists()
        details.append(("homelab_security_host_package_lists_timestamp_seconds", {}, newest))
        if now - newest > lists_max_age_hours * 3600:
            failed.append("package-lists")
    except QueryError:
        failed.append("package-lists")

    try:
        pending = packages()
        offered = {**upgrades(), **pending}
        held = holds()
        seen, known = state["first_seen"], state["lists"]
        first = {name: seen[name] if name in seen else first_time(keys, known) for name, keys in offered.items()}

        def late(name: str) -> bool:
            return now - first[name] > updates_max_age_days * 86400

        held_now = [name for name in offered if name in held]
        results["updates-pending"] = sum(1 for name in pending if late(name))
        results["updates-held"] = sum(1 for name in held_now if late(name))
        details.append(("homelab_security_host_updates_pending", {}, len(pending)))
        details.append(("homelab_security_host_updates_held", {}, len(held_now)))
        new_state = {"first_seen": first, "lists": dict(dates())}
    except QueryError:
        failed.append("updates")

    # A fact that no check reads: a fault here leaves out its series, and
    # costs no result.
    try:
        details.append(("homelab_security_host_reboot_pending", {}, int(bool(reboot()))))
    except OSError:
        pass

    return {"results": results, "details": details, "failed": failed, "state": new_state}


def document(system: str, measured: dict, now: float) -> dict:
    """The JSON document for the inbox of the host: the results only when no query
    failed, the names of the failed queries, and each detail value."""
    return {
        "version": 1,
        "system": system,
        "time": int(now),
        "results": {} if measured["failed"] else dict(measured["results"]),
        "failed": list(measured["failed"]),
        "details": [{"name": name, "labels": dict(labels), "value": value}
                    for name, labels, value in measured["details"]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--system", required=True, help="the name of this distro in security/registry/systems.yaml")
    parser.add_argument("--inbox-dir", type=Path, help="the inbox of the host for the document of the run")
    parser.add_argument("--write-test-dir", type=Path, required=True,
                        help="the textfile directory of the host, which the distro must not be able to change")
    parser.add_argument("--write-test-only", action="store_true",
                        help="run one try of the write test alone, and exit with its answer (0 refused by Windows)")
    parser.add_argument("--write-test-result", type=Path,
                        help="with --write-test-only: write the answer there; else: read the answer of root there")
    parser.add_argument("--file-name", default="tnwks_security_host_check_distro.json")
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
    if args.state_file is None or args.inbox_dir is None:
        parser.error("--state-file and --inbox-dir are required")

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
        dates=lambda: list_dates(args.lists_dir),
        reboot=args.reboot_file.exists,
        writes=lambda: [read_root_result(args.write_test_result, now) if args.write_test_result
                        else (0, NOT_DONE, "uid 0: no answer of root to read"),
                        write_test(args.write_test_dir)],
    )
    write_atomic(args.inbox_dir / args.file_name, json.dumps(document(args.system, measured, now)) + "\n")
    # Only a run with a result writes the state (Design Doc 0004, section 5.6). A run
    # that fails, on old lists or a state file that can not be read, leaves it as it
    # is: its clocks and the Dates of its lists stay those of the last run with a result.
    if not measured["failed"]:
        write_atomic(args.state_file, state_text(measured["state"]))
    if measured["failed"]:
        print(f"host check of {args.system}: no result, these queries failed: {', '.join(measured['failed'])}")
        return 1
    print(f"host check of {args.system}: {len(measured['results'])} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
