"""The host check of a Windows host (role windows_host_check) keeps the contract of the
registry, and a query that fails gives no result, never a pass.

The script runs in PowerShell with a fake answer for each query: it is dot-sourced
with -Library, and each query function is defined again. PowerShell is on the
runners of CI. A workstation with no `pwsh` skips those tests, and CI fails if it
has none.
"""

import base64
import datetime
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import textfile
import yaml

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLE = ANSIBLE / "roles/windows_host_check"
SCRIPT = ROLE / "files/Invoke-TnwksHostCheck.ps1"
REGISTRY = ROOT / "security/registry"
PWSH = shutil.which("pwsh")
NOW = datetime.datetime(2026, 11, 13, 12, 0, tzinfo=datetime.UTC)
CHECKS = ("defender-enabled", "defender-signatures", "firewall-enabled", "updates-pending")
RESULT = "homelab_security_check_result"
LAST = "homelab_security_last_result_timestamp_seconds"
FAILED = "homelab_security_host_check_failed_queries"

needs_pwsh = pytest.mark.skipif(PWSH is None and not os.environ.get("CI"),
                                reason="no pwsh on this workstation; CI runs these tests")

# A host where each query answers, and each check passes.
FAKES = r"""
$now = [datetime]::SpecifyKind([datetime]'2026-11-13T12:00:00', 'Utc')
function Get-TnwksDefenderStatus {
    [pscustomobject]@{
        AMServiceEnabled = $true; AntivirusEnabled = $true; RealTimeProtectionEnabled = $true
        AntivirusSignatureLastUpdated = $now.AddHours(-20)
        QuickScanEndTime = $now.AddDays(-2); FullScanEndTime = $now.AddDays(-9)
    }
}
function Get-TnwksFirewallProfiles {
    foreach ($name in 'Domain', 'Private', 'Public') { [pscustomobject]@{ Name = $name; Enabled = 'True' } }
}
function Get-TnwksPendingUpdates { }
function Get-TnwksLastInstall { $now.AddDays(-3) }
function Get-TnwksRebootPending { $false }
"""

MEASURE = r"""
$measure = Measure-TnwksHostCheck -Now $now -SignatureMaxAgeDays 7 -UpdatesMaxAgeDays 14
[Console]::Out.Write((Format-TnwksHostCheck -System 'HOST-A' -Measure $measure -Now $now))
"""


def pwsh(body: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run PowerShell with the script dot-sourced as a library, then `body`."""
    assert PWSH, "CI has pwsh on its runners"
    script = f"$ErrorActionPreference = 'Stop'\n. '{SCRIPT}' -Library\n{body}"
    encoded = base64.b64encode(script.encode("utf-16le")).decode()
    result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            check=False, capture_output=True, text=True, timeout=120)
    if check:
        assert result.returncode == 0, result.stderr
    return result


def host_check(fakes: str = "") -> dict:
    """The families of the file for a host with the base fakes, then `fakes`."""
    return textfile.parse(pwsh(FAKES + fakes + MEASURE).stdout)


@needs_pwsh
def test_a_host_where_each_check_passes_gives_four_results_of_0_and_the_time():
    families = host_check()
    assert textfile.series(families, RESULT) == {
        (("check", check), ("source", "host-check"), ("system", "HOST-A")): 0 for check in CHECKS}
    assert textfile.value(families, LAST, source="host-check", system="HOST-A") == NOW.timestamp()
    assert textfile.value(families, FAILED, system="HOST-A") == 0
    assert textfile.value(families, "homelab_security_host_defender_signature_timestamp_seconds",
                          system="HOST-A") == (NOW - datetime.timedelta(hours=20)).timestamp()
    # The newest of the quick and the full scan.
    assert textfile.value(families, "homelab_security_host_defender_last_scan_timestamp_seconds",
                          system="HOST-A") == (NOW - datetime.timedelta(days=2)).timestamp()
    assert textfile.value(families, "homelab_security_host_updates_pending", system="HOST-A") == 0
    assert textfile.value(families, "homelab_security_host_reboot_pending", system="HOST-A") == 0
    for profile in ("domain", "private", "public"):
        assert textfile.value(families, "homelab_security_host_firewall_enabled",
                              system="HOST-A", profile=profile) == 1


@needs_pwsh
def test_each_check_counts_the_items_that_fail_it():
    families = host_check(r"""
function Get-TnwksDefenderStatus {
    [pscustomobject]@{
        AMServiceEnabled = $true; AntivirusEnabled = $false; RealTimeProtectionEnabled = $false
        AntivirusSignatureLastUpdated = $now.AddDays(-8)
        QuickScanEndTime = $null; FullScanEndTime = $null
    }
}
function Get-TnwksFirewallProfiles {
    [pscustomobject]@{ Name = 'Domain'; Enabled = 'True' }
    [pscustomobject]@{ Name = 'Private'; Enabled = 'False' }
    [pscustomobject]@{ Name = 'Public'; Enabled = 'NotConfigured' }
}
# Released 30, 15 and 2 days ago: two are past 14 days.
function Get-TnwksPendingUpdates { $now.AddDays(-30); $now.AddDays(-15); $now.AddDays(-2) }
function Get-TnwksRebootPending { $true }
""")
    results = {dict(key)["check"]: value for key, value in textfile.series(families, RESULT).items()}
    assert results == {"defender-enabled": 2, "defender-signatures": 1,
                       "firewall-enabled": 2, "updates-pending": 2}
    assert textfile.value(families, LAST, source="host-check", system="HOST-A") == NOW.timestamp()
    assert textfile.value(families, "homelab_security_host_updates_pending", system="HOST-A") == 3
    assert textfile.value(families, "homelab_security_host_reboot_pending", system="HOST-A") == 1
    # No scan has ended yet: no series, rather than a time of 0.
    assert "homelab_security_host_defender_last_scan_timestamp_seconds" not in families


@needs_pwsh
def test_signatures_at_the_limit_pass_and_one_past_it_fail():
    at_limit = host_check(r"""
function Get-TnwksDefenderStatus {
    [pscustomobject]@{ AMServiceEnabled = $true; AntivirusEnabled = $true; RealTimeProtectionEnabled = $true
        AntivirusSignatureLastUpdated = $now.AddDays(-7); QuickScanEndTime = $null; FullScanEndTime = $null }
}""")
    assert textfile.value(at_limit, RESULT, source="host-check", check="defender-signatures", system="HOST-A") == 0
    past = host_check(r"""
function Get-TnwksDefenderStatus {
    [pscustomobject]@{ AMServiceEnabled = $true; AntivirusEnabled = $true; RealTimeProtectionEnabled = $true
        AntivirusSignatureLastUpdated = $now.AddDays(-7).AddMinutes(-1); QuickScanEndTime = $null; FullScanEndTime = $null }
}""")
    assert textfile.value(past, RESULT, source="host-check", check="defender-signatures", system="HOST-A") == 1


BROKEN = {
    "Defender does not answer": "function Get-TnwksDefenderStatus { throw 'no answer' }",
    "Defender gives no time for its signatures": r"""
function Get-TnwksDefenderStatus {
    [pscustomobject]@{ AMServiceEnabled = $true; AntivirusEnabled = $true; RealTimeProtectionEnabled = $true
        AntivirusSignatureLastUpdated = $null; QuickScanEndTime = $null; FullScanEndTime = $null }
}""",
    "Defender gives an object with no field for real-time protection": r"""
function Get-TnwksDefenderStatus {
    [pscustomobject]@{ AMServiceEnabled = $true; AntivirusEnabled = $true
        AntivirusSignatureLastUpdated = $now; QuickScanEndTime = $null; FullScanEndTime = $null }
}""",
    "the firewall does not answer": "function Get-TnwksFirewallProfiles { throw 'no answer' }",
    "the active store has no Public profile": r"""
function Get-TnwksFirewallProfiles {
    foreach ($name in 'Domain', 'Private') { [pscustomobject]@{ Name = $name; Enabled = 'True' } }
}""",
    "the firewall gives no profile": "function Get-TnwksFirewallProfiles { }",
    "the search of Windows Update fails": "function Get-TnwksPendingUpdates { throw 'search failed' }",
}


@needs_pwsh
@pytest.mark.parametrize("case", BROKEN)
def test_a_query_that_fails_gives_no_result_and_no_time(case):
    families = host_check(BROKEN[case])
    # No result series and no time: the rules read the source as late for the
    # host, and each line of the host as "no evidence". Never a pass.
    assert RESULT not in families
    assert LAST not in families
    assert textfile.value(families, FAILED, system="HOST-A") == 1


FAKE_SESSION = r"""
function Get-TnwksUpdateSession {
    $searcher = [pscustomobject]@{ Online = $false }
    $searcher | Add-Member -MemberType ScriptMethod -Name Search -Value {
        param($criteria)
        $script:seen = @{ criteria = $criteria; online = $this.Online }
        $update = [pscustomobject]@{ LastDeploymentChangeTime = [datetime]'2026-10-01T00:00:00' }
        [pscustomobject]@{ ResultCode = $script:code; Updates = @($update) } }
    $session = [pscustomobject]@{}
    $session | Add-Member -MemberType ScriptMethod -Name CreateUpdateSearcher -Value { $searcher }.GetNewClosure()
    $session
}
"""


@needs_pwsh
def test_a_search_that_windows_update_does_not_finish_is_a_failed_query():
    """The query itself, with a fake session: a result code other than 2 (succeeded)
    throws, as a search that did not finish can not say that nothing is pending."""
    fakes = FAKES.replace("function Get-TnwksPendingUpdates { }\n", "")
    assert "Get-TnwksPendingUpdates" not in fakes
    families = textfile.parse(pwsh(fakes + FAKE_SESSION + "$script:code = 4\n" + MEASURE).stdout)
    assert RESULT not in families and LAST not in families


@needs_pwsh
def test_the_search_asks_windows_update_online_for_updates_that_windows_would_install():
    output = pwsh(FAKE_SESSION + r"""
$script:code = 2
$released = @(Get-TnwksPendingUpdates)
[Console]::Out.Write(('{0}|{1}|{2}' -f $released.Count, $script:seen.online, $script:seen.criteria))
""").stdout
    count, online, criteria = output.split("|")
    assert count == "1" and online == "True"
    assert criteria == "IsInstalled=0 and IsHidden=0 and BrowseOnly=0 and Type='Software'"


@needs_pwsh
def test_a_fact_that_no_check_reads_costs_no_result():
    families = host_check(r"""
function Get-TnwksLastInstall { throw 'no history' }
function Get-TnwksRebootPending { throw 'no answer' }
""")
    assert len(textfile.series(families, RESULT)) == 4
    assert textfile.value(families, LAST, source="host-check", system="HOST-A") == NOW.timestamp()
    assert "homelab_security_host_updates_last_install_timestamp_seconds" not in families
    assert "homelab_security_host_reboot_pending" not in families


@needs_pwsh
def test_the_run_writes_the_whole_file_in_one_step_and_fails_when_a_query_fails(tmp_path):
    (tmp_path / "tnwks_security_host_check.prom").write_text("old\n")
    run = pwsh(FAKES + rf"""
Invoke-TnwksHostCheck -System 'HOST-A' -TextfileDir '{tmp_path}' -FileName 'tnwks_security_host_check.prom' `
    -SignatureMaxAgeDays 7 -UpdatesMaxAgeDays 14
""")
    assert run.stdout.strip() == "host check of HOST-A: 4 checks"
    written = (tmp_path / "tnwks_security_host_check.prom").read_bytes()
    assert not written.startswith(b"\xef\xbb\xbf") and b"\r" not in written
    families = textfile.parse(written.decode())
    assert len(textfile.series(families, RESULT)) == 4
    assert sorted(path.name for path in tmp_path.iterdir()) == ["tnwks_security_host_check.prom"]

    failed = pwsh(FAKES + rf"""
function Get-TnwksFirewallProfiles {{ throw 'no answer' }}
Invoke-TnwksHostCheck -System 'HOST-A' -TextfileDir '{tmp_path}' -FileName 'tnwks_security_host_check.prom' `
    -SignatureMaxAgeDays 7 -UpdatesMaxAgeDays 14
""", check=False)
    assert failed.returncode == 1
    # The log names the query, and no value of the host.
    assert "no result, these queries failed: firewall" in failed.stdout
    families = textfile.parse((tmp_path / "tnwks_security_host_check.prom").read_text())
    assert RESULT not in families and LAST not in families


@needs_pwsh
def test_a_run_with_no_system_writes_nothing(tmp_path):
    run = pwsh(FAKES + f"Invoke-TnwksHostCheck -TextfileDir '{tmp_path}' -FileName 'x.prom'", check=False)
    assert run.returncode != 0
    assert list(tmp_path.iterdir()) == []


@needs_pwsh
def test_a_label_value_is_escaped():
    output = pwsh(r"""[Console]::Out.Write((Format-TnwksSeries 'x' ([ordered]@{ a = 'b"c\d' }) 1))""").stdout
    assert output == r'x{a="b\"c\\d"} 1'


def test_the_script_is_ascii_for_windows_powershell():
    """Windows PowerShell 5.1 reads a script with no byte order mark in the code page
    of the system, so a letter outside ASCII would change."""
    SCRIPT.read_text(encoding="ascii")


def test_each_series_of_the_script_has_its_text():
    text = SCRIPT.read_text()
    help_block = text[text.index("$script:Help"):text.index("$script:Source")]
    named = set(re.findall(r"^\s+(homelab_\w+)\s+=", help_block, re.MULTILINE))
    used = set(re.findall(r"'(homelab_\w+)'", text))
    assert used <= named, f"series with no HELP text: {sorted(used - named)}"


def flat(tasks: list) -> list:
    """The tasks of a task list, with the tasks of each block in place of the block."""
    out = []
    for task in tasks:
        out.extend(flat(task["block"]) if "block" in task else [task])
    return out


def test_the_role_changes_nothing_in_a_check_run():
    """The pull request runs the role with --check on each host
    (.github/workflows/ansible-hosts.yaml), so no task may run in a check run, and the
    handler starts the task only in an apply."""
    tasks = flat(yaml.safe_load((ROLE / "tasks/main.yml").read_text()))
    handlers = yaml.safe_load((ROLE / "handlers/main.yml").read_text())
    for task in tasks + handlers:
        assert task.get("check_mode") is not False, task["name"]
        if "ansible.windows.win_powershell" in task:
            assert "SupportsShouldProcess" in task["ansible.windows.win_powershell"]["script"], task["name"]
            assert "ShouldProcess(" in task["ansible.windows.win_powershell"]["script"], task["name"]
    acl = next(task for task in tasks if "ansible.windows.win_acl" in task)
    assert "ansible_check_mode" in acl["when"], "a check run has no new directory to read an ACL of"


def test_the_way_back_removes_the_task_before_the_files():
    blocks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    assert [block["when"] for block in blocks] == ["host_check_state == 'present'", "host_check_state == 'absent'"]
    absent = blocks[1]["block"]
    assert absent[0]["tnwks_win_scheduled_task"]["state"] == "absent"
    assert yaml.safe_load((ROLE / "defaults/main.yml").read_text())["host_check_state"] == "present"


def test_both_hosts_get_the_same_settings():
    """Equal security on each site: no host or group sets a variable of the role."""
    inventory = ANSIBLE / "inventory"
    for path in sorted([*inventory.glob("host_vars/**/*.yml"), *inventory.glob("host_vars/*.yml"),
                        *inventory.glob("group_vars/**/*.yml"), *inventory.glob("group_vars/*.yml")]):
        data = yaml.safe_load(path.read_text()) or {}
        keys = [key for key in data if str(key).startswith("host_check_")]
        assert not keys, f"{path.relative_to(ROOT)} sets {keys}"


def test_the_task_runs_the_script_with_the_name_of_the_host_and_the_limits_of_the_role():
    template = (ROLE / "templates/host-check.xml.j2").read_text()
    arguments = re.search(r"<Arguments>(.*)</Arguments>", template)[1]
    assert '-System "{{ inventory_hostname | e }}"' in arguments
    for name in re.findall(r"\{\{ (\w+)", arguments):
        assert name == "inventory_hostname" or name.startswith("host_check_"), name
    assert "<UserId>S-1-5-18</UserId>" in template, "SYSTEM reads Defender, the firewall and Windows Update"
    assert "<Interval>PT6H</Interval>" in template, "the cycle of host-check in sources.yaml"
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    assert defaults["host_check_updates_max_age_days"] == 14, "Design Doc 0004, section 4.1.4"


def test_each_windows_host_of_the_inventory_lists_the_host_check_in_the_registry():
    hosts = yaml.safe_load((ANSIBLE / "inventory/hosts.yml").read_text())
    windows = set(hosts["all"]["children"]["windows_hosts"]["hosts"])
    systems = yaml.safe_load((REGISTRY / "systems.yaml").read_text())
    of_type = {system["name"]: system for system in systems if system["type"] == "windows-host"}
    assert set(of_type) == windows
    for system in of_type.values():
        assert "host-check" in system.get("sources", []), system["name"]
    sources = {source["name"]: source for source in yaml.safe_load((REGISTRY / "sources.yaml").read_text())}
    assert (sources["host-check"]["cycle"], sources["host-check"]["late_after"]) == ("6h", "12h")
