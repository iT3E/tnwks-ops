"""The marks of the tests of security/.

`pwsh` marks a test that runs PowerShell. Each start of PowerShell takes about
two seconds on a runner, so .github/workflows/observability.yaml runs these
tests in their own job (`-m pwsh`), and the `contracts` job runs the others
(`-m "not pwsh"`). Each test is in exactly one of the two.
"""


def pytest_configure(config):
    config.addinivalue_line("markers", "pwsh: the test runs PowerShell")
