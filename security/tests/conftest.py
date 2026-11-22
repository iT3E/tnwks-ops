"""The marks of the tests of security/, and how the PowerShell tests run.

`pwsh` marks a test that runs PowerShell. Each start of PowerShell takes about
two seconds on a runner, so .github/workflows/observability.yaml runs these
tests in their own job (`-m pwsh`), and the `contracts` job runs the others
(`-m "not pwsh"`, with `-n auto` on its command line). Each test is in exactly
one of the two.

A run of the `pwsh` tests alone (`-m pwsh`, as that job runs them) uses one
pytest-xdist worker for each CPU, unless the command line gives `-n` itself
(`-n 0` runs them in one process). Each of these tests keeps its files under
its own `tmp_path`, and each Ansible run of them has its own ANSIBLE_HOME. No
other run changes: no config file of pytest applies to `pytest security/tests`
(the root of the repository has none), and a run of the other tests, or of
both marks, stays in one process. When the switch turns the parallel run on,
it prints one line, so the log of a run shows which way it ran.

`--shard I/N` keeps one part of the collected tests: the job of the `pwsh`
tests gives it, so N runners share them (its matrix). A test is in the shard
that the CRC-32 of its node id names, so each test is in exactly one shard
on each runner, and the shards have about the same number of tests.
security/tests/test_pwsh_parallel.py checks that the matrix of the job has
each shard of 1..N one time.
"""

import sys
import zlib

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "pwsh: the test runs PowerShell")


def pytest_addoption(parser):
    parser.addoption("--shard", metavar="I/N", default=None,
                     help="run only shard I of N of the collected tests (1 <= I <= N)")


def parse_shard(text: str) -> tuple[int, int]:
    """`2/3` is (2, 3). Text that names no shard of 1..N stops the run."""
    index, _, count = text.partition("/")
    if not (index.isascii() and index.isdigit() and count.isascii() and count.isdigit()) \
            or not 1 <= int(index) <= int(count):
        raise pytest.UsageError(f"--shard {text}: not I/N with 1 <= I <= N")
    return int(index), int(count)


def shard_of(nodeid: str, count: int) -> int:
    """The shard (1..count) of a test."""
    return zlib.crc32(nodeid.encode()) % count + 1


def pytest_collection_modifyitems(config, items):
    text = config.getoption("--shard")
    if not text:
        return
    index, count = parse_shard(text)
    keep = [item for item in items if shard_of(item.nodeid, count) == index]
    drop = [item for item in items if shard_of(item.nodeid, count) != index]
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


PARALLEL_LINE = "security/tests: the pwsh tests run in parallel, one pytest-xdist worker for each CPU (-n 0 runs them in one process)"


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config):
    # A worker of xdist reads this file too: only the main process sets it.
    if hasattr(config, "workerinput") or config.option.markexpr != "pwsh":
        return
    # With xdist off (-p no:xdist), there is no option to set.
    if getattr(config.option, "numprocesses", "off") is None and not config.getoption("usepdb"):
        config.option.numprocesses = "auto"
        print(PARALLEL_LINE, file=sys.stderr, flush=True)
