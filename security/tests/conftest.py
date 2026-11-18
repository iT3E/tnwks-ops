"""The marks of the tests of security/, and how the PowerShell tests run.

`pwsh` marks a test that runs PowerShell. Each start of PowerShell takes about
two seconds on a runner, so .github/workflows/observability.yaml runs these
tests in their own job (`-m pwsh`), and the `contracts` job runs the others
(`-m "not pwsh"`). Each test is in exactly one of the two.

A run of the `pwsh` tests alone (`-m pwsh`, as that job runs them) uses one
pytest-xdist worker for each CPU, unless the command line gives `-n` itself
(`-n 0` runs them in one process). Each of these tests keeps its files under
its own `tmp_path`, and each Ansible run of them has its own ANSIBLE_HOME. No
other run changes: no config file of pytest applies to `pytest security/tests`
(the root of the repository has none), and a run of the other tests, or of
both marks, stays in one process.
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "pwsh: the test runs PowerShell")


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config):
    # A worker of xdist reads this file too: only the main process sets it.
    if hasattr(config, "workerinput") or config.option.markexpr != "pwsh":
        return
    # With xdist off (-p no:xdist), there is no option to set.
    if getattr(config.option, "numprocesses", "off") is None and not config.getoption("usepdb"):
        config.option.numprocesses = "auto"
