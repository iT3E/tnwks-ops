"""The switch of security/tests/conftest.py that runs the PowerShell tests in parallel."""

from types import SimpleNamespace

import conftest


def config(markexpr: str, numprocesses=None, worker: bool = False, pdb: bool = False, xdist: bool = True):
    option = SimpleNamespace(markexpr=markexpr)
    if xdist:
        option.numprocesses = numprocesses
    c = SimpleNamespace(option=option, getoption=lambda name: pdb)
    if worker:
        c.workerinput = {}
    return c


def test_only_a_run_of_the_pwsh_tests_alone_takes_one_worker_per_cpu():
    on = config("pwsh")
    conftest.pytest_cmdline_main(on)
    assert on.option.numprocesses == "auto"
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
