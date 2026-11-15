"""The host check of a Windows host (role windows_host_check) keeps the contract of the
registry, and a query that fails gives no result, never a pass.

The script runs in PowerShell with a fake answer for each query: it is dot-sourced
with -Library, and each query function is defined again. PowerShell is on the
runners of CI. A workstation with no `pwsh` skips those tests, and CI fails if it
has none.
"""

import base64
import datetime
import json
import os
import re
import shutil
import subprocess
import types
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
CHECKS = ("defender-enabled", "defender-signatures", "firewall-enabled", "updates-pending", "updates-held")
# Design Doc 0004, section 4.1.4: the alert covers Defender off, the firewall off and
# updates pending past 14 days. Section 5.8 adds old signatures, and section 5.7 gives
# a held update the severity of a pending one, so each check is high.
SEVERITY = {"defender-enabled": "high", "defender-signatures": "high",
            "firewall-enabled": "high", "updates-pending": "high", "updates-held": "high"}
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
function Get-TnwksHeldUpdates { }
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
def test_a_host_where_each_check_passes_gives_five_results_of_0_and_the_time():
    families = host_check()
    assert textfile.series(families, RESULT) == {
        (("check", check), ("severity", SEVERITY[check]), ("source", "host-check"), ("system", "HOST-A")): 0
        for check in CHECKS}
    assert textfile.value(families, LAST, source="host-check", system="HOST-A") == NOW.timestamp()
    assert textfile.value(families, FAILED, system="HOST-A") == 0
    assert textfile.value(families, "homelab_security_host_defender_signature_timestamp_seconds",
                          system="HOST-A") == (NOW - datetime.timedelta(hours=20)).timestamp()
    # The newest of the quick and the full scan.
    assert textfile.value(families, "homelab_security_host_defender_last_scan_timestamp_seconds",
                          system="HOST-A") == (NOW - datetime.timedelta(days=2)).timestamp()
    assert textfile.value(families, "homelab_security_host_updates_pending", system="HOST-A") == 0
    assert textfile.value(families, "homelab_security_host_updates_held", system="HOST-A") == 0
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
# Hidden: released 20 and 3 days ago, one past 14 days.
function Get-TnwksHeldUpdates { $now.AddDays(-20); $now.AddDays(-3) }
function Get-TnwksRebootPending { $true }
""")
    results = {dict(key)["check"]: value for key, value in textfile.series(families, RESULT).items()}
    assert results == {"defender-enabled": 2, "defender-signatures": 1,
                       "firewall-enabled": 2, "updates-pending": 2, "updates-held": 1}
    assert textfile.value(families, "homelab_security_host_updates_held", system="HOST-A") == 2
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
    assert textfile.value(at_limit, RESULT, source="host-check", check="defender-signatures", system="HOST-A",
                         severity="high") == 0
    past = host_check(r"""
function Get-TnwksDefenderStatus {
    [pscustomobject]@{ AMServiceEnabled = $true; AntivirusEnabled = $true; RealTimeProtectionEnabled = $true
        AntivirusSignatureLastUpdated = $now.AddDays(-7).AddMinutes(-1); QuickScanEndTime = $null; FullScanEndTime = $null }
}""")
    assert textfile.value(past, RESULT, source="host-check", check="defender-signatures", system="HOST-A",
                         severity="high") == 1


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
    "the search of hidden updates fails": "function Get-TnwksHeldUpdates { throw 'search failed' }",
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
    for query in ("Get-TnwksPendingUpdates", "Get-TnwksHeldUpdates"):
        fakes = FAKES.replace(f"function {query} {{ }}\n", "")
        assert query not in fakes
        families = textfile.parse(pwsh(fakes + FAKE_SESSION + "$script:code = 4\n" + MEASURE).stdout)
        assert RESULT not in families and LAST not in families


@needs_pwsh
def test_a_hidden_update_fails_after_14_days():
    """Design Doc 0004, section 5.7: a hidden update is dated by its release, so it
    needs no clock."""
    at_limit = host_check("function Get-TnwksHeldUpdates { $now.AddDays(-14) }")
    past = host_check("function Get-TnwksHeldUpdates { $now.AddDays(-14).AddHours(-1) }")
    for families, expected in ((at_limit, 0), (past, 1)):
        assert textfile.value(families, RESULT, source="host-check", check="updates-held", system="HOST-A",
                              severity="high") == expected
        assert textfile.value(families, RESULT, source="host-check", check="updates-pending", system="HOST-A",
                              severity="high") == 0


@needs_pwsh
def test_the_search_of_held_updates_asks_windows_update_online_for_hidden_updates():
    output = pwsh(FAKE_SESSION + r"""
$script:code = 2
$released = @(Get-TnwksHeldUpdates)
[Console]::Out.Write(('{0}|{1}|{2}' -f $released.Count, $script:seen.online, $script:seen.criteria))
""").stdout
    count, online, criteria = output.split("|")
    assert count == "1" and online == "True"
    assert criteria == "IsInstalled=0 and IsHidden=1 and Type='Software'"


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
    assert len(textfile.series(families, RESULT)) == 5
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
    assert run.stdout.strip() == "host check of HOST-A: 5 checks"
    written = (tmp_path / "tnwks_security_host_check.prom").read_bytes()
    assert not written.startswith(b"\xef\xbb\xbf") and b"\r" not in written
    families = textfile.parse(written.decode())
    assert len(textfile.series(families, RESULT)) == 5
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


def test_each_check_has_a_severity_and_the_alert_reads_the_checks_that_4_1_4_names():
    """SecurityFindingNew reads severity high and critical only (observability/rules.py)."""
    text = SCRIPT.read_text()
    block = text[text.index("$script:Severity"):text.index("}", text.index("$script:Severity"))]
    assert dict(re.findall(r"'([a-z-]+)'\s+=\s+'([a-z]+)'", block)) == SEVERITY
    rules = (ROOT / "observability/rules.py").read_text()
    assert 'SECURITY_FINDING_SEVERITY = "high|critical"' in rules


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
    for task in tasks:
        if "Get-Acl" in task.get("ansible.windows.win_powershell", {}).get("script", ""):
            assert "ansible_check_mode" in task["when"], "a check run has no new directory to read an ACL of"


def test_only_system_and_the_administrators_may_change_the_directory_of_the_script():
    """SYSTEM runs the script: the directory has no inherited rule, the Administrators
    own it, SYSTEM and the Administrators have full control and Users read. The rules are
    set before the copy of the script, in a check run only through ShouldProcess, and
    the account of the distro gets no right there."""
    tasks = flat(yaml.safe_load((ROLE / "tasks/main.yml").read_text()))
    names = [task["name"] for task in tasks]
    lock = next(task for task in tasks if task["name"].startswith("Only SYSTEM and the Administrators"))
    assert names.index(lock["name"]) < names.index("Host check script")
    script = lock["ansible.windows.win_powershell"]["script"]
    assert "SetAccessRuleProtection($true, $false)" in script, "no inherited rule"
    assert "$acl.SetOwner($admins)" in script and "'S-1-5-32-544'" in script
    assert re.findall(r"@\('(S-[0-9-]+)', '([^']+)'\)", script) == [
        ("S-1-5-18", "FullControl"), ("S-1-5-32-544", "FullControl"), ("S-1-5-32-545", "ReadAndExecute, Synchronize")]
    assert "$Ansible.Changed" in script
    assert "ansible_check_mode" in lock["when"]
    assert "Get-ChildItem" not in script, "the files below are reset after the copy"
    # A new file belongs to the account that made it, so the reset of the files comes
    # after the copy: one apply leaves nothing for a second run or a check run.
    files = next(task for task in tasks if task["name"] == "The files of the host check directory take its owner and rules")
    assert names.index("Host check script") < names.index(files["name"])
    reset = files["ansible.windows.win_powershell"]["script"]
    assert "SupportsShouldProcess" in reset and "ShouldProcess(" in reset
    assert "$reset.SetOwner($admins)" in reset and "SetAccessRuleProtection($false, $false)" in reset
    assert "GetAccessRules($false, $true, $sid)" in reset, "inherited rules that do not come from the directory"
    assert "$Ansible.Changed" in reset
    assert "ansible_check_mode" in files["when"]
    assert not any("ansible.windows.win_acl" in task for task in tasks), "no right on the textfile directory"


def test_the_distro_gets_no_right_on_the_textfile_directory_and_its_old_directory_goes():
    """Design Doc 0004, section 5.9: the distro writes its inbox, not the textfile
    directory, where the exporter can not tell its files from the host's."""
    tasks = flat(yaml.safe_load((ROLE / "tasks/main.yml").read_text()))
    gone = next(task for task in tasks if task["name"] == "No directory of the distro under the textfile directory")
    assert gone["ansible.windows.win_file"] == {"path": "{{ host_check_distro_dir }}", "state": "absent"}
    for task in tasks:
        text = yaml.safe_dump(task)
        if "host_check_distro_account" in text:
            assert task["name"].startswith("Only SYSTEM, the Administrators and the account of the distro"), task["name"]
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    assert defaults["host_check_distro_dir"] == "{{ host_check_textfile_dir }}\\wsl-distro"
    assert not defaults["host_check_inbox_dir"].startswith(defaults["host_check_textfile_dir"])
    assert defaults["host_check_inbox_dir"].startswith("C:\\ProgramData\\")


def test_the_inbox_has_no_inherited_rule_and_three_rules():
    """Only SYSTEM, the Administrators and the account of the distro may change the
    inbox. It does not inherit the rights of C:\\ProgramData, where each user may make
    files."""
    tasks = flat(yaml.safe_load((ROLE / "tasks/main.yml").read_text()))
    names = [task["name"] for task in tasks]
    lock = next(task for task in tasks if task["name"].startswith("Only SYSTEM, the Administrators and the account"))
    assert names.index("Inbox of the distro") < names.index(lock["name"])
    task = lock["ansible.windows.win_powershell"]
    assert task["parameters"] == {"Path": "{{ host_check_inbox_dir }}", "Account": "{{ host_check_distro_account }}"}
    script = task["script"]
    assert "SetAccessRuleProtection($true, $false)" in script, "no inherited rule"
    assert "$acl.SetOwner($admins)" in script and "'S-1-5-32-544'" in script
    want = script[script.index("$want = @("):script.index("\n)", script.index("$want = @("))]
    assert re.findall(r"@\((.+), '([^']+)'\)", want) == [
        ("(New-Object $sid 'S-1-5-18')", "FullControl"), ("$admins", "FullControl"), ("$distro", "Modify, Synchronize")]
    assert "NTAccount $Account).Translate($sid)" in script
    assert "GetAccessRules($true, $true, $sid)" in script, "an inherited rule is a change"
    assert "$Ansible.Changed" in script and "ShouldProcess(" in script
    assert "ansible_check_mode" in lock["when"]


def test_the_relay_task_runs_the_relay_as_system_each_hour_for_the_distro_of_the_host():
    template = (ROLE / "templates/host-check-relay.xml.j2").read_text()
    arguments = re.search(r"<Arguments>(.*)</Arguments>", template)[1]
    assert "-Relay" in arguments
    assert '-System "{{ host_check_distro_system | e }}"' in arguments
    assert '-TextfileDir "{{ host_check_textfile_dir | e }}"' in arguments
    for name in re.findall(r"\{\{ (\w+)", arguments):
        assert name.startswith("host_check_"), name
    assert "<UserId>S-1-5-18</UserId>" in template
    assert "<Interval>PT1H</Interval>" in template, "Design Doc 0004, section 5.9: each hour"
    tasks = flat(yaml.safe_load((ROLE / "tasks/main.yml").read_text()))
    relay = next(task for task in tasks if task.get("tnwks_win_scheduled_task", {}).get("name") == "{{ host_check_relay_task }}")
    assert "notify" not in relay, "the play of the distro writes the first document, after this play"
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    expression = defaults["host_check_distro_system"]
    assert "wsl_distro_windows_host" in expression and "inventory_hostname" in expression
    hosts = yaml.safe_load((ANSIBLE / "inventory/hosts.yml").read_text())
    for distro in hosts["all"]["children"]["wsl_distros"]["hosts"]:
        windows = yaml.safe_load((ANSIBLE / f"inventory/host_vars/{distro}/main.yml").read_text())["wsl_distro_windows_host"]
        assert windows in hosts["all"]["children"]["windows_hosts"]["hosts"], distro


@needs_pwsh
def test_the_powershell_of_the_tasks_and_handlers_parses():
    """The scripts of win_powershell run only on a host: CI parses them, with no run."""
    tasks = flat(yaml.safe_load((ROLE / "tasks/main.yml").read_text()))
    handlers = yaml.safe_load((ROLE / "handlers/main.yml").read_text())
    scripts = [task["ansible.windows.win_powershell"]["script"] for task in tasks + handlers
               if "ansible.windows.win_powershell" in task]
    assert len(scripts) == 4
    for script in scripts:
        encoded = base64.b64encode(script.encode()).decode()
        result = pwsh(
            "$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "'))\n"
            "$errors = $null\n"
            "[void][System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$null, [ref]$errors)\n"
            "[Console]::Out.Write($errors.Count)")
        assert result.stdout.strip() == "0", script


def test_the_way_back_removes_the_task_before_the_files():
    blocks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    assert [block["when"] for block in blocks] == ["host_check_state == 'present'", "host_check_state == 'absent'"]
    absent = blocks[1]["block"]
    assert absent[0]["tnwks_win_scheduled_task"]["state"] == "absent"
    assert absent[0]["loop"] == ["{{ host_check_task }}", "{{ host_check_relay_task }}"]
    assert {"{{ host_check_inbox_dir }}", "{{ host_check_distro_dir }}"} <= set(absent[1]["loop"])
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
    """Each host lists the source since its first result was on site 2, so the rule for a
    late result watches it."""
    hosts = yaml.safe_load((ANSIBLE / "inventory/hosts.yml").read_text())
    windows = set(hosts["all"]["children"]["windows_hosts"]["hosts"])
    systems = yaml.safe_load((REGISTRY / "systems.yaml").read_text())
    of_type = {system["name"]: system for system in systems if system["type"] == "windows-host"}
    assert set(of_type) == windows
    for system in of_type.values():
        assert "host-check" in system.get("sources", []), system["name"]
    controls = yaml.safe_load((REGISTRY / "controls.yaml").read_text())
    named = {item["check"] for line in controls for item in line["checks"].get("windows-host", [])
             if item["source"] == "host-check"}
    assert named == set(CHECKS)
    sources = {source["name"]: source for source in yaml.safe_load((REGISTRY / "sources.yaml").read_text())}
    assert (sources["host-check"]["cycle"], sources["host-check"]["late_after"]) == ("6h", "12h")


# --- The relay of the WSL distro (Design Doc 0004, section 5.9) ---------------------------

DISTRO = "WSL-02-ubuntu"
INBOX_FILE = "tnwks_security_host_check_distro.json"
DISTRO_FILE = "tnwks_security_host_check_distro.prom"


def load_distro_check():
    path = ANSIBLE / "roles/wsl_host_check/files/tnwks-host-check.py"
    module = types.ModuleType("tnwks_host_check_for_the_relay")
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def good_document(**changes) -> dict:
    document = {"version": 1, "system": DISTRO, "time": int(NOW.timestamp()) - 600,
                "results": {"updates-pending": 1, "updates-held": 0}, "failed": [], "details": [
                    {"name": "homelab_security_host_package_lists_timestamp_seconds", "labels": {},
                     "value": NOW.timestamp() - 7200},
                    {"name": "homelab_security_host_updates_pending", "labels": {}, "value": 3},
                    {"name": "homelab_security_host_write_test_result", "labels": {"uid": "0"}, "value": 0},
                    {"name": "homelab_security_host_write_test_result", "labels": {"uid": "1000"}, "value": 2}]}
    document.update(changes)
    return document


def relay(tmp_path, document=None, raw=None, system=DISTRO):
    """Run the relay of the script on one document; give its exit code, its output and
    the families of the .prom file of the distro."""
    inbox, out = tmp_path / "inbox", tmp_path / "textfile"
    inbox.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o777)
    if raw is not None:
        (inbox / INBOX_FILE).write_bytes(raw)
    elif document is not None:
        (inbox / INBOX_FILE).write_text(json.dumps(document))
    result = pwsh(f"""$now = [datetime]::SpecifyKind([datetime]'2026-11-13T12:00:00', 'Utc')
Invoke-TnwksRelay -System '{system}' -InboxDir '{inbox}' -InboxFile '{INBOX_FILE}' -TextfileDir '{out}' `
    -FileName '{DISTRO_FILE}' -Now $now
""", check=False)
    return result.returncode, result.stdout, textfile.parse((out / DISTRO_FILE).read_text())


def assert_no_result(families):
    assert RESULT not in families and LAST not in families
    assert textfile.value(families, FAILED, system=DISTRO) == 1


@needs_pwsh
def test_the_relay_writes_the_result_of_the_distro_with_its_name_and_its_time(tmp_path):
    code, output, families = relay(tmp_path, good_document())
    assert code == 0, output
    assert textfile.series(families, RESULT) == {
        (("check", "updates-held"), ("severity", "high"), ("source", "host-check"), ("system", DISTRO)): 0,
        (("check", "updates-pending"), ("severity", "high"), ("source", "host-check"), ("system", DISTRO)): 1}
    assert textfile.value(families, LAST, source="host-check", system=DISTRO) == NOW.timestamp() - 600
    assert textfile.value(families, FAILED, system=DISTRO) == 0
    assert textfile.value(families, "homelab_security_host_write_test_result", system=DISTRO, uid="1000") == 2
    help_block = SCRIPT.read_text()
    for name, family in families.items():
        assert family["help"] and f"{name} " in help_block, name


@needs_pwsh
def test_a_failed_run_of_the_distro_gives_the_count_of_failed_queries_and_no_result(tmp_path):
    code, _, families = relay(tmp_path, good_document(results={}, failed=["package-lists", "textfile-write"]))
    assert code == 1
    assert RESULT not in families and LAST not in families
    assert textfile.value(families, FAILED, system=DISTRO) == 2


BAD_DOCUMENTS = {
    "no document": None,
    "it names the host": good_document(system="WSL-02"),
    "it has a check of the host": good_document(results={"updates-pending": 0, "updates-held": 0,
                                                         "defender-enabled": 0}),
    "a time 10 minutes ahead": good_document(time=int(NOW.timestamp()) + 600),
    "a result that is not a whole number of 0 or more": good_document(results={"updates-pending": -1,
                                                                               "updates-held": 0}),
    "a result that is true": good_document(results={"updates-pending": True, "updates-held": 0}),
    "a result missing with no failed query": good_document(results={"updates-pending": 0}),
    "a failed query and a result": good_document(failed=["state"]),
    "a series of the host": good_document(details=[{"name": "homelab_security_host_defender_enabled",
                                                    "labels": {"protection": "service"}, "value": 1}]),
    "a label that is not a uid": good_document(details=[{"name": "homelab_security_host_write_test_result",
                                                         "labels": {"uid": "0\"} 1\nx{a=\""}, "value": 0}]),
    "another field": good_document(source="host-check"),
    "another version": good_document(version=2),
    "not JSON": "not json",
}


@needs_pwsh
@pytest.mark.parametrize("case", BAD_DOCUMENTS)
def test_a_document_that_does_not_fit_gives_no_result(tmp_path, case):
    document = BAD_DOCUMENTS[case]
    if isinstance(document, str):
        code, output, families = relay(tmp_path, raw=document.encode())
    else:
        code, output, families = relay(tmp_path, document)
    assert code == 1
    assert "relay" in output
    assert_no_result(families)


@needs_pwsh
def test_a_document_over_64_kib_gives_no_result(tmp_path):
    code, output, families = relay(tmp_path, raw=json.dumps(good_document()).encode() + b" " * 65536)
    assert code == 1 and "larger than 65536 bytes" in output
    assert_no_result(families)
    at_limit = json.dumps(good_document()).encode()
    code, _, families = relay(tmp_path, raw=at_limit + b" " * (65536 - len(at_limit)))
    assert code == 0 and RESULT in families


@needs_pwsh
def test_a_document_that_is_a_link_gives_no_result(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(good_document()))
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / INBOX_FILE).symlink_to(target)
    code, output, families = relay(tmp_path)
    assert code == 1 and "link" in output
    assert_no_result(families)


@needs_pwsh
def test_the_relay_writes_what_the_tests_of_the_distro_expect_of_it(tmp_path):
    """The tests of the distro read its documents through a model of the relay
    (textfile.relayed). The relay itself gives the same series for the documents of the
    distro script: a run with each result, and a run with failed queries."""
    distro = load_distro_check()
    measured = distro.measure(
        NOW.timestamp(), 14, 72, {"first_seen": {"openssl": NOW.timestamp() - 20 * 86400}, "lists": {}},
        packages=lambda: {"openssl": ("Ubuntu/noble-updates",)}, lists=lambda: NOW.timestamp() - 3600,
        reboot=lambda: True, upgrades=lambda: {"openssl": ("Ubuntu/noble-updates",), "curl": ("Ubuntu/noble",)},
        holds=lambda: {"curl"}, dates=dict,
        writes=lambda: [(0, 0, "uid 0: refused"), (1000, 2, "uid 1000: Linux")])
    failed = {**measured, "failed": ["package-lists"]}
    for index, case in enumerate((measured, failed)):
        document = json.loads(json.dumps(distro.document(DISTRO, case, NOW.timestamp() - 60)))
        _, _, families = relay(tmp_path / str(index), document)
        model = textfile.relayed(document)
        assert {name: family["samples"] for name, family in families.items()} == {
            name: family["samples"] for name, family in model.items()}


@needs_pwsh
def test_the_host_check_counts_the_files_of_the_write_test_of_the_distro(tmp_path):
    """A try of the distro removes its own file, so the count is 0. The host check, as
    SYSTEM, lists the textfile directory, so a file that a try left shows in the metrics.
    Other files do not count, and a directory that can not be listed leaves out the
    series and fails no query."""
    for name in (".tnwks-write-test-0123456789abcdef.tmp", ".tnwks-write-test-fedcba9876543210.tmp",
                 "tnwks_security_host_check.prom", ".tnwks-write-test-x.prom", "other.tmp"):
        (tmp_path / name).write_text("")
    (tmp_path / ".tnwks-write-test-dir.tmp").mkdir()
    body = FAKES + f"""
$measure = Measure-TnwksHostCheck -Now $now -SignatureMaxAgeDays 7 -UpdatesMaxAgeDays 14 -TextfileDir '{tmp_path}'
[Console]::Out.Write((Format-TnwksHostCheck -System 'HOST-A' -Measure $measure -Now $now))
"""
    families = textfile.parse(pwsh(body).stdout)
    assert textfile.value(families, "homelab_security_host_write_test_files", system="HOST-A") == 2
    gone = textfile.parse(pwsh(body.replace(str(tmp_path), str(tmp_path / "gone"))).stdout)
    assert "homelab_security_host_write_test_files" not in gone
    assert textfile.value(gone, FAILED, system="HOST-A") == 0
    run = (ROLE / "files/Invoke-TnwksHostCheck.ps1").read_text()
    assert "-TextfileDir $TextfileDir" in run[run.index("function Invoke-TnwksHostCheck"):]
    distro = (ANSIBLE / "roles/wsl_host_check/files/tnwks-host-check.py").read_text()
    assert 'f".tnwks-write-test-{secrets.token_hex(8)}.tmp"' in distro, "the name that the count reads"
