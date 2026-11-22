"""The switch of security/tests/conftest.py that runs the PowerShell tests in parallel, and their shards."""

import shlex
from pathlib import Path
from types import SimpleNamespace

import conftest
import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "observability.yaml"


def config(markexpr: str, numprocesses=None, worker: bool = False, pdb: bool = False, xdist: bool = True):
    option = SimpleNamespace(markexpr=markexpr)
    if xdist:
        option.numprocesses = numprocesses
    c = SimpleNamespace(option=option, getoption=lambda name: pdb)
    if worker:
        c.workerinput = {}
    return c


def test_only_a_run_of_the_pwsh_tests_alone_takes_one_worker_per_cpu(capsys):
    on = config("pwsh")
    conftest.pytest_cmdline_main(on)
    assert on.option.numprocesses == "auto"
    assert capsys.readouterr().err == conftest.PARALLEL_LINE + "\n"
    for name, c in (("the other tests", config("not pwsh")), ("both marks", config("")),
                    ("a worker of xdist", config("pwsh", worker=True)), ("--pdb", config("pwsh", pdb=True))):
        conftest.pytest_cmdline_main(c)
        assert c.option.numprocesses is None, name
    # A -n of the command line stays, -n 0 among them.
    for given in (0, 2):
        c = config("pwsh", numprocesses=given)
        conftest.pytest_cmdline_main(c)
        assert c.option.numprocesses == given
    # With xdist off (-p no:xdist), there is no option to set.
    off = config("pwsh", xdist=False)
    conftest.pytest_cmdline_main(off)
    assert not hasattr(off.option, "numprocesses")
    # Only the parallel run prints its line.
    assert capsys.readouterr().err == ""


def test_the_powershell_job_runs_the_pwsh_tests_alone():
    """The switch matches the mark expression exactly: the job must give `-m pwsh` and no other mark."""
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["powershell"]["steps"]
    runs = [shlex.split(step["run"]) for step in steps if "pytest" in step.get("run", "")]
    assert len(runs) == 1, runs
    args = runs[0][runs[0].index("pytest") + 1:]
    marks = [args[i + 1] for i, a in enumerate(args) if a == "-m"] + [a[2:] for a in args if a.startswith("-m") and a != "-m"]
    assert marks == ["pwsh"], args
    # Nor an option that keeps it in one process.
    given = set(args) | {"".join(args[i : i + 2]) for i in range(len(args))}
    assert not {"-n0", "--numprocesses=0", "--pdb", "no:xdist"} & given, args


def test_each_test_is_in_exactly_one_shard():
    ids = [f"security/tests/test_x.py::test_{n}[{case}]" for n in range(200) for case in ("a", "b")]
    for count in (1, 2, 3, 5):
        parts = {index: [i for i in ids if conftest.shard_of(i, count) == index] for index in range(1, count + 1)}
        assert sorted(i for part in parts.values() for i in part) == sorted(ids)
        # About the same number of tests in each.
        assert min(map(len, parts.values())) > len(ids) / count / 2, count


def test_the_shard_option_keeps_its_part_and_deselects_the_rest():
    items = [SimpleNamespace(nodeid=f"security/tests/test_x.py::test_{n}") for n in range(40)]
    seen = []
    for index in (1, 2):
        mine, gone = list(items), []
        c = SimpleNamespace(getoption=lambda name, index=index: f"{index}/2",
                            hook=SimpleNamespace(pytest_deselected=lambda items, gone=gone: gone.extend(items)))
        conftest.pytest_collection_modifyitems(c, mine)
        assert mine and gone and len(mine) + len(gone) == len(items)
        assert not {i.nodeid for i in mine} & {i.nodeid for i in gone}
        seen += mine
    assert sorted(i.nodeid for i in seen) == sorted(i.nodeid for i in items)
    # With no option, each test stays.
    mine = list(items)
    conftest.pytest_collection_modifyitems(SimpleNamespace(getoption=lambda name: None), mine)
    assert mine == items


@pytest.mark.parametrize("text", ["", "0/2", "3/2", "2", "a/b", "1/0", "-1/2", "1/2/3"])
def test_a_shard_that_is_not_one_of_n_stops_the_run(text):
    with pytest.raises(pytest.UsageError):
        conftest.parse_shard(text)
    assert conftest.parse_shard("2/3") == (2, 3)


def test_the_matrix_of_the_powershell_job_has_each_shard_one_time():
    """A shard that the matrix does not name is a part of the tests that no runner runs."""
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["powershell"]
    (step,) = [s for s in job["steps"] if "pytest" in s.get("run", "")]
    args = shlex.split(step["run"])
    assert args[args.index("--shard") + 1] == "$SHARD" and args.count("--shard") == 1, args
    shards = job["strategy"]["matrix"]["shard"]
    assert len(shards) >= 2
    # The matrix gives I/N whole, so the N of each entry is the count of the entries.
    assert sorted(shards) == [f"{index}/{len(shards)}" for index in range(1, len(shards) + 1)]
    assert step["env"] == {"SHARD": "${{ matrix.shard }}"}
    # One failed shard does not stop the others, so a run shows each failure.
    assert job["strategy"]["fail-fast"] is False
