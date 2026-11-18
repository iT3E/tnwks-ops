"""The switch of security/tests/conftest.py that runs the PowerShell tests in parallel."""

import shlex
from pathlib import Path
from types import SimpleNamespace

import conftest
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
