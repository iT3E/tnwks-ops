"""scripts/ci-report.py summarizes a run of the hosts workflow
(.github/workflows/ansible-hosts.yaml) and writes its drift metrics.

One ansible-playbook run takes windows-host.yml and then wsl-distro.yml, and it
stops before wsl-distro.yml when the play of windows-host.yml ends with a
failure. The distro then has no recap line: the summary says that it did not
run, the metrics count it as failed, and the run still fails.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infrastructure/ansible/scripts/ci-report.py"

# Host names made up for the tests.
LOG = """\
TASK [The temporary account] ***************************************************
fatal: [win-a]: FAILED! => changed=false
PLAY RECAP *********************************************************************
localhost                  : ok=4    changed=0    unreachable=0    failed=0    skipped=1    rescued=0    ignored=0
win-a                      : ok=54   changed=0    unreachable=0    failed=1    skipped=9    rescued=0    ignored=0
"""


def report(tmp_path: Path, log: str, hosts: str, rc: int) -> tuple[str, str]:
    path = tmp_path / "run.log"
    path.write_text(log)
    summary, prom = tmp_path / "summary.md", tmp_path / "drift.prom"
    run = subprocess.run([sys.executable, "-I", str(SCRIPT), "--log", str(path), "--site", "site_x",
                          "--hosts", hosts, "--mode", "apply", "--rc", str(rc),
                          "--summary", str(summary), "--prom", str(prom)],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    return summary.read_text(), prom.read_text()


def test_a_host_with_no_recap_line_did_not_run_and_the_run_fails(tmp_path):
    summary, prom = report(tmp_path, LOG, "win-a,distro-a", rc=2)
    assert "#### Ansible apply: `site_x` (exit 2)" in summary
    assert "| `win-a` | 54 | 0 | 1 | 0 | 9 |" in summary
    assert "| `distro-a` | did not run | | | | |" in summary
    assert "**Did not run:** `distro-a`. ansible-playbook stopped before its play, and the run fails (exit 2)." in summary
    assert "- `win-a`: The temporary account" in summary
    assert 'tnwks_windows_drift_check_failed{host="win-a"} 1' in prom
    assert 'tnwks_windows_drift_check_failed{host="distro-a"} 1' in prom, "not checked counts as failed"


def test_a_run_where_each_host_ran_has_no_line_for_a_host_that_did_not_run(tmp_path):
    log = LOG.replace("failed=1", "failed=0").replace("fatal: [win-a]: FAILED! => changed=false\n", "") + (
        "distro-a                   : ok=30   changed=0    unreachable=0    failed=0    skipped=4    rescued=0    ignored=0\n")
    summary, prom = report(tmp_path, log, "win-a,distro-a", rc=0)
    assert "did not run" not in summary and "Did not run" not in summary
    assert "| `distro-a` | 30 | 0 | 0 | 0 | 4 |" in summary
    assert 'tnwks_windows_drift_check_failed{host="distro-a"} 0' in prom
