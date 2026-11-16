"""Run promtool's unit tests of generated rules, in shards that run at the same time.

    uv run --project observability python observability/rule_tests.py \
        observability/generated/rule-tests.yaml

promtool evaluates every rule of a file at each interval of a test's simulated
time, one test after the other. A file with days of simulated time takes
minutes. The tests of a file do not depend on each other, so each file is
split into shards of about equal simulated time, and one promtool container
runs per shard. Each test is in exactly one shard, and its text does not
change. Needs Docker.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from versions import PROMETHEUS

SECONDS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
DURATION = re.compile(r"(\d+)(ms|[smhdwy])")


def seconds(duration):
    """A Prometheus duration (`36h30m`) in seconds. Text that is no duration is 0."""
    return sum(int(number) * SECONDS[unit] for number, unit in DURATION.findall(str(duration)))


def cost(test):
    """The simulated time of a test: promtool evaluates up to its last eval_time."""
    cases = (test.get("alert_rule_test") or []) + (test.get("promql_expr_test") or [])
    # One minute more, so tests with no time still spread over the shards.
    return max((seconds(case.get("eval_time", 0)) for case in cases), default=0) + 60


def shards(tests, count):
    """At most `count` lists of about equal cost. Each test is in exactly one."""
    bins = [[0, []] for _ in range(max(1, min(count, len(tests))))]
    for test in sorted(tests, key=cost, reverse=True):
        lightest = min(bins, key=lambda item: item[0])
        lightest[0] += cost(test)
        lightest[1].append(test)
    return [part for _, part in bins if part]


def split(path, index, count, scratch):
    """Write the shards of one test file to `scratch`. Returns a list of
    (shard, file name, cost, tests). The rule files stay where they are: the
    container sees the directory of `path` at /rules/<index>."""
    doc = yaml.safe_load(path.read_text())
    rule_files = doc.get("rule_files") or []
    for name in rule_files:
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise SystemExit(f"{path}: rule file {name} is not below the directory of the test file")
    parts = shards(doc["tests"], count)
    out = []
    for number, tests in enumerate(parts, 1):
        shard = {**doc, "rule_files": [f"/rules/{index}/{name}" for name in rule_files],
                 "tests": tests}
        name = f"{index}-{path.stem}-{number}-of-{len(parts)}.yaml"
        target = scratch / name
        target.write_text(yaml.safe_dump(shard, sort_keys=False, width=110))
        target.chmod(0o644)
        out.append((f"{number}/{len(parts)}", name, sum(map(cost, tests)), len(tests)))
    return out


def promtool(image, rules, index, scratch, name):
    start = time.monotonic()
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/promtool",
         "--mount", f"type=bind,src={rules},dst=/rules/{index},readonly",
         "--mount", f"type=bind,src={scratch},dst=/shards,readonly",
         image, "test", "rules", f"/shards/{name}"],
        capture_output=True, text=True, check=False)
    return result.returncode, result.stdout + result.stderr, time.monotonic() - start


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path, help="promtool test files")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1,
                        help="containers at the same time, and the most shards of one file")
    parser.add_argument("--image", default=PROMETHEUS)
    args = parser.parse_args()

    # One pull before the containers start, not one for each of them.
    if subprocess.run(["docker", "image", "inspect", args.image], capture_output=True,
                      check=False).returncode != 0:
        subprocess.run(["docker", "pull", "--quiet", args.image], check=True)

    with tempfile.TemporaryDirectory(prefix="rule-tests-") as tmp:
        scratch = Path(tmp)
        scratch.chmod(0o755)  # promtool runs as nobody in its image
        work = []
        for index, path in enumerate(args.files):
            rules = path.resolve().parent
            for shard, name, weight, tests in split(path, index, args.jobs, scratch):
                work.append((weight, f"{path} [{shard}]", tests,
                             (args.image, rules, index, scratch, name)))
        work.sort(key=lambda item: item[0], reverse=True)  # the longest shards start first
        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            results = list(pool.map(lambda item: promtool(*item[3]), work))
        failed = 0
        for (_, label, tests, _), (code, output, took) in zip(work, results, strict=True):
            print(f"{'ok    ' if code == 0 else 'FAILED'} {label}: {tests} tests, {took:.0f}s")
            if code != 0:
                failed += 1
                print(output.rstrip())
    total = sum(item[2] for item in work)
    print(f"{total} tests in {len(work)} shards, {time.monotonic() - start:.0f}s, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
