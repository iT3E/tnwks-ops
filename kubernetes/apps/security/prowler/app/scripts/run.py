"""Run Prowler for one system of the security registry, and leave one OCSF file for the converter.

One CronJob of ../ runs this script with the Python of the Prowler image, for one
provider and one system. The script:

1. Writes the mutelist of the run from the list of accepted findings (the Secret
   security-accepted-findings, optional): each entry of `prowler` for this
   system whose end date has not passed. It is the only exception list of the
   run, so the default mutelist of Prowler is off too. An entry that does not
   fit is skipped and counted; a list that can not be read stops the run.
2. Runs Prowler with the checks of the registry for the provider, the output
   format json-ocsf and no report to the vendor.
3. Moves the finished file to `prowler/<provider>/<system>.<time>.ocsf.json` on
   the volume of results, through `prowler/<provider>/tmp/`, so the converter
   never reads half a file (security/converter/README.md, "The files of
   results"). A run that fails, or leaves no file or an empty one, moves no
   file.
4. Deletes the files of the system that are older than 14 days.
5. Prints the peak of memory of the container, the measure of the run.

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
    (Design Doc 4.1.2), an end that date.fromisoformat refuses) is skipped and
    counted, and the log has its number and why, never a value of the list. One bad
    entry then does not stop each run until the list is fixed. A list that can not
    be read at all (not UTF-8, not JSON, a key twice in one object, not an array)
    raises UnreadableList: the run then fails and mutes nothing, as it can not know
    what to mute."""
    if not path.is_file():
        return {}, 0
    try:
        entries = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
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


def mutelist(checks: dict[str, str]) -> dict:
    """The mutelist of Prowler that mutes each accepted check on each resource of the run.

    A run reads one system, so the account and the region are `*`."""
    return {"Mutelist": {"Accounts": {"*": {"Checks": {
        check: {"Regions": ["*"], "Resources": ["*"], "Description": f"Accepted until {until}"}
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


def publish(produced: Path, results: Path, provider: str, system: str, now: datetime.datetime) -> Path:
    """Copy the OCSF file into tmp/ of the volume, then move it to its name in one step."""
    directory = results / "prowler" / provider
    staging = directory / "tmp"
    make_dir(directory)
    make_dir(staging)
    final = directory / f"{system}.{now.strftime(STAMP)}.ocsf.json"
    partial = staging / f"{final.name}.partial"
    shutil.copyfile(produced, partial)
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


def memory_peak() -> str:
    try:
        return f"{int(MEMORY_PEAK.read_text()) / 2**20:.0f} MiB"
    except (OSError, ValueError):
        return "unknown"


def run(args: argparse.Namespace, now=lambda: datetime.datetime.now(UTC), call=subprocess.call) -> int:
    started = now()
    try:
        checks, skipped = accepted_checks(args.accepted, args.system, started.date())
    except UnreadableList as error:
        print(f"the list of accepted findings can not be read ({error}): no run", flush=True)
        return 2
    with tempfile.TemporaryDirectory(dir=args.work) as work:
        work = Path(work)
        mutelist_file = work / "mutelist.yaml"
        # JSON is YAML, and Prowler reads the file with yaml.safe_load.
        mutelist_file.write_text(json.dumps(mutelist(checks)))
        output = work / "output"
        name = f"{args.system}.run"
        command = prowler_command(args.provider, args.checks, mutelist_file, output, name, args.extra)
        print(f"prowler {args.provider} for {args.system}: {len(checks)} accepted checks muted,"
              f" {skipped} entries of the list skipped", flush=True)
        code = call(command)
        produced = output / f"{name}.ocsf.json"
        if code != 0:
            print(f"prowler ended with code {code}: no file for the converter", flush=True)
        elif not produced.is_file():
            print("prowler wrote no OCSF file: no file for the converter", flush=True)
            code = 1
        elif produced.stat().st_size == 0:
            # The converter reads an empty file as a bad file and serves no
            # result: the same as no file, so the run fails and moves nothing.
            print("prowler wrote an empty OCSF file: no file for the converter", flush=True)
            code = 1
        else:
            final = publish(produced, args.results, args.provider, args.system, now())
            print(f"wrote {final.relative_to(args.results)}", flush=True)
    for path in prune(args.results, args.provider, args.system, now()):
        print(f"deleted {path.relative_to(args.results)}", flush=True)
    print(f"memory peak of the container: {memory_peak()}", flush=True)
    return code


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", required=True, help="aws, cloudflare or github")
    parser.add_argument("--system", required=True, help="the name of the system in security/registry/systems.yaml")
    parser.add_argument("--checks", type=Path, required=True, help="the --checks-file of the provider")
    parser.add_argument("--results", type=Path, default=Path("/results"), help="the root of the volume of results")
    parser.add_argument("--accepted", type=Path, default=Path("/accepted/accepted.json"))
    parser.add_argument("--work", type=Path, default=Path("/tmp"), help="the scratch space of Prowler")
    parser.add_argument("extra", nargs="*", help="more arguments for Prowler, after --")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse(sys.argv[1:])))
