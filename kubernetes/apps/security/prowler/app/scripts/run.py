"""Run Prowler for one system of the security registry, and leave one OCSF file for the converter.

One CronJob of ../ runs this script with the Python of the Prowler image, for one
provider and one system. The script:

1. Writes the mutelist of the run from the list of accepted findings (the Secret
   security-accepted-findings, optional): each entry of `prowler` for this
   system whose end date has not passed. It is the only exception list of the
   run, so the default mutelist of Prowler is off too. An entry that does not
   fit is skipped and counted; a list that can not be read stops the run.
2. Runs Prowler with the checks of the registry for the provider, the output
   format json-ocsf and no report to the vendor, in parts: one process of
   Prowler for each service of the scan list, one after the other. Prowler
   keeps each service it has read, and each finding, until its process ends,
   so a process for the whole list holds them all at once (../README.md,
   "Memory").
3. Joins the records of the parts into one JSON array in
   `prowler/<provider>/tmp/` on the volume of results, and moves it to
   `prowler/<provider>/<system>.<time>.ocsf.json`, so the converter never
   reads half a file (security/converter/README.md, "The files of results").
   A run in which a part fails, or a part leaves an empty file or a file that
   is not one array, or no part leaves a record, moves no file.
4. Deletes the files of the system that are older than 14 days.
5. Prints a line when each part starts and one when it ends, with the peak of
   its process, and the peak of memory of the container at the end.

It prints no finding and no credential. Tests: security/tests/test_prowler.py.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

UTC = datetime.UTC
PROWLER = "/home/prowler/.venv/bin/prowler"
STAMP = "%Y%m%dT%H%M%SZ"
KEEP_DAYS = 14
MEMORY_PEAK = Path("/sys/fs/cgroup/memory.peak")


FIELDS = frozenset({"source", "check", "system", "reason", "until"})
# The form of an end date. date.fromisoformat also takes 20271231 and 2027-W05-1
# (since Python 3.11): the form is checked first, so both readers take one form.
# ASCII digits only (\d also takes other digits), and the whole value (fullmatch).
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# The form of a check ID, as the converter reads it (fullmatch). Prowler reads a key
# of its mutelist as a regex, so a check of "*", "." or "s3" would mute more than
# one check: such an entry is skipped.
CHECK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,199}")


class UnreadableList(Exception):
    """The list of accepted findings can not be read at all."""


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    """The object_pairs_hook of the list: a key twice in one object refuses the file,
    as the two readers could keep different values."""
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise UnreadableList("a key is twice in one object")
    return dict(pairs)


def _why_skipped(entry: object) -> tuple[str | None, datetime.date | None]:
    """Why an item of the list does not fit, or None, and its end date."""
    if not isinstance(entry, dict):
        return "not an object", None
    if set(entry) != FIELDS:
        return "a field is missing, or a key is not a field", None
    if not all(isinstance(entry[field], str) for field in FIELDS):
        return "a value is not a string", None
    if not all(entry[field].strip() for field in FIELDS):
        return "a field is empty", None
    if not CHECK.fullmatch(entry["check"]):
        return "the check is not a check ID", None
    if not DATE.fullmatch(entry["until"]):
        return "the end is not a date YYYY-MM-DD", None
    try:
        return None, datetime.date.fromisoformat(entry["until"])
    except ValueError:
        return "the end is not a date YYYY-MM-DD", None


def accepted_checks(path: Path, system: str, today: datetime.date) -> tuple[dict[str, str], int]:
    """The checks of `prowler` that the list accepts for the system today, with their end
    date, and the number of entries that are skipped because they do not fit.

    The list is the key accepted.json of the Secret: one JSON array of objects, each
    with exactly the string fields source, check, system, reason and until
    (security/README.md, "Accepted findings"). It is read with json.loads, never
    with a YAML parser. An entry is accepted until the end of its day, UTC. The
    reason stays in the Secret.

    The converter reads the same list the same way: an item that does not fit
    (not an object, a field missing, a key that is not a field, a value that is not
    a string, a field empty, the reason too, as each accepted finding has a reason
    (Design Doc 4.1.2), a check that is not a check ID, an end that is not
    YYYY-MM-DD or that date.fromisoformat refuses) is skipped and counted, and the
    log has its number and why, never a value of the list. One bad
    entry then does not stop each run until the list is fixed. A list that can not
    be read at all (not UTF-8, not JSON, a number too long or a nesting too deep for
    the parser, a key twice in one object, not an array)
    raises UnreadableList: the run then fails and mutes nothing, as it can not know
    what to mute."""
    if not path.is_file():
        return {}, 0
    try:
        entries = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    # ValueError also covers a number of more than 4300 digits (the int limit).
    except (OSError, ValueError, RecursionError) as error:
        # The type only: a parse error can quote the text, and the text can hold a reason.
        raise UnreadableList(type(error).__name__) from None
    if not isinstance(entries, list):
        raise UnreadableList("not a JSON array")
    checks: dict[str, str] = {}
    skipped = 0
    for index, entry in enumerate(entries, 1):
        why, until = _why_skipped(entry)
        if why:
            print(f"entry {index} of the list of accepted findings is skipped: {why}", flush=True)
            skipped += 1
            continue
        if entry["source"] != "prowler" or entry["system"] != system:
            continue
        if until >= today:
            check = entry["check"]
            checks[check] = max(until.isoformat(), checks.get(check, ""))
    return checks, skipped


def mutelist_key(check: str) -> str:
    """The key of one check in the mutelist, which matches that check only.

    Prowler 5.49.0 (prowler/lib/mutelist/mutelist.py, is_muted_in_check and
    is_item_matched) mutes a check when a key is "*", equals the check, or
    re.search(key, check) finds it, after each "*" of the key becomes ".*". So a
    bare check ID also mutes each longer check that holds it
    (cloudtrail_multi_region_enabled and
    cloudtrail_multi_region_enabled_logging_management_events). The key is the
    escaped check between ^ and $: a check ID (CHECK) has no "*", and re.escape
    adds none, so the key is a regex that matches the whole check and nothing
    longer. The rename of a key that starts with "lambda" does not apply: the
    key starts with "^"."""
    return "^" + re.escape(check) + "$"


def mutelist(checks: dict[str, str]) -> dict:
    """The mutelist of Prowler that mutes each accepted check on each resource of the run.

    An entry names a check and a system, and nothing narrower. A run reads one
    system (one account, or one Cloudflare account), so the account, the region
    and the resource are `*`, and no tag is named (Prowler then takes `*`)."""
    return {"Mutelist": {"Accounts": {"*": {"Checks": {
        mutelist_key(check): {"Regions": ["*"], "Resources": ["*"], "Description": f"Accepted until {until}"}
        for check, until in sorted(checks.items())}}} if checks else {}}}


def prowler_command(provider: str, checks_file: Path, mutelist_file: Path, output: Path,
                    name: str, extra: list[str]) -> list[str]:
    """The command of one run. No --push-to-cloud and no -v: nothing goes to the vendor."""
    return [PROWLER, provider,
            "--checks-file", str(checks_file),
            "--mutelist-file", str(mutelist_file),
            "--output-formats", "json-ocsf",
            "--output-directory", str(output),
            "--output-filename", name,
            "--no-banner",
            "--log-level", "ERROR",
            # Exit code 3 means a check failed: a result, not an error of the run.
            "--ignore-exit-code-3",
            *extra]


def make_dir(path: Path) -> None:
    """A directory that the converter (another user) can read."""
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o755)


def service(check: str) -> str:
    """The service of a check: the first word of its ID, as Prowler 5.49.0 reads it
    (prowler/lib/check/check.py, execute_checks)."""
    return check.split("_", 1)[0]


def parts(checks_file: Path, provider: str) -> list[tuple[str, list[str]]]:
    """The scan list of the provider in parts: one part for each service, in the order
    of their names, each with its checks in the order of the list.

    Prowler makes the client of a service the first time a check imports it, and the
    client keeps what it read until the process ends. A check of one service stays
    with the other checks of that service, so no part reads a service that another
    part read already (a check can still read the client of another service)."""
    checks = json.loads(checks_file.read_text())[provider]
    grouped: dict[str, list[str]] = {}
    for check in checks:
        grouped.setdefault(service(check), []).append(check)
    return sorted(grouped.items())


def child(command: list[str]) -> tuple[int, int | None]:
    """Run one process of Prowler: its exit code (minus the signal when a signal
    ended it) and the peak of its resident memory, in bytes."""
    process = subprocess.Popen(command)
    _, status, usage = os.wait4(process.pid, 0)
    process.returncode = os.waitstatus_to_exitcode(status)
    # ru_maxrss is in KiB on Linux.
    return process.returncode, usage.ru_maxrss * 1024


class BadPart(Exception):
    """The OCSF file of a part is not one JSON array."""


CHUNK = 1024 * 1024
SPACE = b" \t\r\n"


def read_at(file, offset: int, length: int) -> bytes:
    file.seek(offset)
    chunk = file.read(length)
    if len(chunk) != length:
        raise BadPart("its file changed while it was read")
    return chunk


def first_byte(file, start: int, stop: int) -> int | None:
    """The offset of the first byte in [start, stop) that is not JSON space, or None."""
    while start < stop:
        chunk = read_at(file, start, min(CHUNK, stop - start))
        kept = chunk.lstrip(SPACE)
        if kept:
            return start + len(chunk) - len(kept)
        start += len(chunk)
    return None


def last_byte(file, start: int, stop: int) -> int | None:
    """The offset of the last byte in [start, stop) that is not JSON space, or None."""
    while start < stop:
        length = min(CHUNK, stop - start)
        kept = read_at(file, stop - length, length).rstrip(SPACE)
        if kept:
            return stop - length + len(kept) - 1
        stop -= length
    return None


def append_records(produced: Path, joined, records: bool) -> bool:
    """Copy the records of the JSON array of one part into the array that `joined` holds
    open, with a comma before them when the array has records already, and return
    whether the array has records now. In chunks: the run never holds a file in
    memory.

    Prowler 5.49.0 writes the array as `[`, the records with a comma between them,
    and `]` (prowler/lib/outputs/ocsf/ocsf.py, batch_write_data_to_file). The script
    checks the brackets at the two ends only; the converter reads each record."""
    size = produced.stat().st_size
    with produced.open("rb") as file:
        first, last = first_byte(file, 0, size), last_byte(file, 0, size)
        if first is None or first == last or read_at(file, first, 1) != b"[" or read_at(file, last, 1) != b"]":
            raise BadPart("its file is not one JSON array")
        start, stop = first_byte(file, first + 1, last), last_byte(file, first + 1, last)
        if start is None:
            return records
        if records:
            joined.write(b",")
        file.seek(start)
        left = stop + 1 - start
        while left:
            chunk = file.read(min(CHUNK, left))
            if not chunk:
                raise BadPart("its file changed while it was read")
            joined.write(chunk)
            left -= len(chunk)
    return True


def staging(results: Path, provider: str) -> Path:
    """`prowler/<provider>/tmp/` on the volume, with directories the converter can read."""
    directory = results / "prowler" / provider
    make_dir(directory)
    make_dir(directory / "tmp")
    return directory / "tmp"


def publish(partial: Path, results: Path, provider: str, system: str, now: datetime.datetime) -> Path:
    """Move the whole file from tmp/ to its name, in one step."""
    final = results / "prowler" / provider / f"{system}.{now.strftime(STAMP)}.ocsf.json"
    partial.chmod(0o644)
    os.replace(partial, final)
    return final


def prune(results: Path, provider: str, system: str, now: datetime.datetime) -> list[Path]:
    """Delete the files of the system whose time is older than 14 days, and the
    leftovers of a run of the system that stopped between the copy and the move."""
    directory = results / "prowler" / provider
    pattern = re.compile(rf"^{re.escape(system)}\.(\d{{8}}T\d{{6}}Z)\.ocsf\.json(\.partial)?$")
    removed = []
    for folder in (directory, directory / "tmp"):
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            match = pattern.match(path.name)
            if not match:
                continue
            stamp = datetime.datetime.strptime(match[1], STAMP).replace(tzinfo=UTC)
            if match[2] or now - stamp > datetime.timedelta(days=KEEP_DAYS):
                path.unlink()
                removed.append(path)
    return removed


def mib(value: int | None) -> str:
    return "unknown" if value is None else f"{value / 2**20:.0f} MiB"


def memory_peak() -> str:
    try:
        return f"{int(MEMORY_PEAK.read_text()) / 2**20:.0f} MiB"
    except (OSError, ValueError):
        return "unknown"


def run_part(args: argparse.Namespace, work: Path, mutelist_file: Path, index: int,
             piece: tuple[str, list[str]], joined, records: bool, call) -> tuple[str | None, bool, int | None]:
    """Run one part and copy its records into `joined`: why it failed, or None, whether
    the array has records now, and the peak of memory of the process of the part."""
    name, checks = piece
    checks_file = work / f"checks-{name}.json"
    checks_file.write_text(json.dumps({args.provider: checks}))
    output = work / f"output-{index}"
    prefix = f"{args.system}.{name}"
    code, peak = call(prowler_command(args.provider, checks_file, mutelist_file, output, prefix, args.extra))
    produced = output / f"{prefix}.ocsf.json"
    why = None
    if code != 0:
        why = f"prowler ended with code {code}"
    elif produced.is_file() and produced.stat().st_size == 0:
        # The converter reads an empty file as a bad file: a part that leaves
        # one fails, as a run of the whole list did.
        why = "prowler wrote an empty OCSF file"
    elif produced.is_file():
        try:
            records = append_records(produced, joined, records)
        except BadPart as error:
            why = str(error)
    # No file: Prowler made no finding for the checks of this part. Other parts can.
    shutil.rmtree(output, ignore_errors=True)
    checks_file.unlink()
    return why, records, peak


def run(args: argparse.Namespace, now=lambda: datetime.datetime.now(UTC), call=child) -> int:
    started = now()
    try:
        checks, skipped = accepted_checks(args.accepted, args.system, started.date())
    except UnreadableList as error:
        print(f"the list of accepted findings can not be read ({error}): no run", flush=True)
        return 2
    pieces = parts(args.checks, args.provider)
    with tempfile.TemporaryDirectory(dir=args.work) as work:
        work = Path(work)
        mutelist_file = work / "mutelist.yaml"
        # JSON is YAML, and Prowler reads the file with yaml.safe_load.
        mutelist_file.write_text(json.dumps(mutelist(checks)))
        print(f"prowler {args.provider} for {args.system}: {len(checks)} accepted checks muted,"
              f" {skipped} entries of the list skipped, {len(pieces)} parts", flush=True)
        partial = staging(args.results, args.provider) / f"{args.system}.{started.strftime(STAMP)}.ocsf.json.partial"
        failed, records = [], False
        with partial.open("wb") as joined:
            joined.write(b"[")
            for index, piece in enumerate(pieces, 1):
                part = f"part {index} of {len(pieces)}: {piece[0]}, {len(piece[1])} checks"
                print(f"{part}: starts", flush=True)
                why, records, peak = run_part(args, work, mutelist_file, index, piece, joined, records, call)
                print(f"{part}: {'ends' if why is None else f'fails ({why})'},"
                      f" memory peak of the part: {mib(peak)}", flush=True)
                if why is not None:
                    failed.append(piece[0])
            joined.write(b"]")
        if failed:
            # A night with a part missing is no result: the converter keeps the file
            # of the last whole run, and its time ages until the rule for a late
            # result fires. Partial results would drop the checks of the part.
            print(f"{len(failed)} of {len(pieces)} parts failed ({', '.join(failed)}):"
                  " no file for the converter", flush=True)
            code = 1
        elif not records:
            print("prowler wrote no record in any part: no file for the converter", flush=True)
            code = 1
        else:
            final = publish(partial, args.results, args.provider, args.system, now())
            print(f"wrote {final.relative_to(args.results)}", flush=True)
            code = 0
    # A file of a run that failed is a leftover in tmp/: prune deletes it.
    for path in prune(args.results, args.provider, args.system, now()):
        print(f"deleted {path.relative_to(args.results)}", flush=True)
    print(f"memory peak of the container: {memory_peak()}", flush=True)
    return code


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", required=True, help="aws or cloudflare")
    parser.add_argument("--system", required=True, help="the name of the system in security/registry/systems.yaml")
    parser.add_argument("--checks", type=Path, required=True, help="the --checks-file of the provider")
    parser.add_argument("--results", type=Path, default=Path("/results"), help="the root of the volume of results")
    parser.add_argument("--accepted", type=Path, default=Path("/accepted/accepted.json"))
    parser.add_argument("--work", type=Path, default=Path("/tmp"), help="the scratch space of Prowler")
    parser.add_argument("extra", nargs="*", help="more arguments for Prowler, after --")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse(sys.argv[1:])))
