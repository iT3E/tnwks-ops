"""The host check of a WSL distro (role wsl_host_check) keeps the contract of the
registry, writes the shared series with the text of the Windows host check, and a
query that fails gives no result, never a pass.

The script is loaded as a module, and each query gets a fake answer.
"""

import importlib.util
import json
import os
import re
import shutil
import signal
import stat
import subprocess
from pathlib import Path

import pytest
import textfile
import yaml

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLE = ANSIBLE / "roles/wsl_host_check"
SCRIPT = ROLE / "files/tnwks-host-check.py"
WINDOWS = ANSIBLE / "roles/windows_host_check"
REGISTRY = ROOT / "security/registry"
NOW = 1_794_571_200.0  # 2026-11-13T12:00:00Z
DAY = 86400
SYSTEM = "WSL-02-ubuntu"
RESULT = "homelab_security_check_result"
LAST = "homelab_security_last_result_timestamp_seconds"
FAILED = "homelab_security_host_check_failed_queries"


def load():
    spec = importlib.util.spec_from_loader("tnwks_host_check", loader=None, origin=str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    exec(compile(SCRIPT.read_text(), str(SCRIPT), "exec"), module.__dict__)  # noqa: S102
    return module


check = load()


@pytest.fixture(autouse=True)
def files_of_this_user_count_as_roots(monkeypatch):
    """The tests run as one user, and the check reads only an answer that root owns. The
    tests of that rule set ROOT_UID themselves."""
    monkeypatch.setattr(check, "ROOT_UID", os.geteuid())


# The tries of a host that refuses the write: root reaches Windows, which refuses; the
# distro user stops at the mode of /mnt/c.
PASSING_TRIES = [(0, 0, "uid 0: Windows refused the write"), (1000, 2, "uid 1000: Linux refuses the write")]


UPDATES = "Ubuntu/noble-updates"
SECURITY = "Ubuntu/noble-security"


def offered(names, keys=(UPDATES,)) -> dict:
    """Upgrades from the given lists, in the form of the simulation."""
    if isinstance(names, dict):
        return names
    return {name: tuple(keys) for name in names}


def state_of(first=None, lists=None) -> dict:
    return {"first_seen": dict(first or {}), "lists": dict(lists or {})}


def run(state=None, packages=(), lists=None, reboot=False, writes=None, upgrades=None, holds=(),
        dates=None, now=NOW, **limits):
    """A run of the check with fakes. state is a map of clocks, or a state from state_of.
    upgrades is the second simulation (by default the same as packages); dates the Dates
    of the lists now."""
    def fake_packages():
        if isinstance(packages, Exception):
            raise packages
        return offered(packages)

    def fake_upgrades():
        if isinstance(upgrades, Exception):
            raise upgrades
        return offered(packages if upgrades is None else upgrades)

    def fake_holds():
        if isinstance(holds, Exception):
            raise holds
        return set(holds)

    def fake_lists():
        if isinstance(lists, Exception):
            raise lists
        return now - 3600 if lists is None else lists

    def fake_reboot():
        if isinstance(reboot, Exception):
            raise reboot
        return reboot

    def fake_writes():
        return writes if writes is not None else PASSING_TRIES

    if state is None:
        state = state_of()
    elif "first_seen" not in state:
        state = state_of(state)
    measured = check.measure(now, limits.get("days", 14), limits.get("hours", 72), state,
                             packages=fake_packages, lists=fake_lists, reboot=fake_reboot, writes=fake_writes,
                             upgrades=fake_upgrades, holds=fake_holds,
                             dates=lambda: dict(dates if dates is not None else {UPDATES: now - 3600}))
    return measured, textfile.parse(check.render(SYSTEM, measured, now))


def result(families, name="updates-pending"):
    return textfile.value(families, RESULT, source="host-check", check=name, system=SYSTEM, severity="high")


def test_a_distro_with_no_pending_upgrade_gives_a_result_of_0_and_the_time():
    measured, families = run()
    assert textfile.series(families, RESULT) == {
        (("check", name), ("severity", "high"), ("source", "host-check"), ("system", SYSTEM)): 0.0
        for name in ("updates-pending", "updates-held")}
    assert textfile.value(families, LAST, source="host-check", system=SYSTEM) == NOW
    assert textfile.value(families, FAILED, system=SYSTEM) == 0
    assert textfile.value(families, "homelab_security_host_updates_pending", system=SYSTEM) == 0
    assert textfile.value(families, "homelab_security_host_updates_held", system=SYSTEM) == 0
    assert textfile.value(families, "homelab_security_host_reboot_pending", system=SYSTEM) == 0
    assert measured["state"] == state_of(lists={UPDATES: NOW - 3600})


def test_an_upgrade_fails_the_check_only_after_it_waited_past_the_limit():
    state = state_of({"old": NOW - 15 * DAY, "edge": NOW - 14 * DAY, "fresh": NOW - DAY}, {UPDATES: NOW - 7 * 3600})
    measured, families = run(state=state, packages=["old", "edge", "fresh", "new"], reboot=True)
    assert result(families) == 1
    assert textfile.value(families, "homelab_security_host_updates_pending", system=SYSTEM) == 4
    assert textfile.value(families, "homelab_security_host_reboot_pending", system=SYSTEM) == 1
    # The new upgrade came out after the Date of its list at the last run with a result.
    assert measured["state"]["first_seen"] == {"old": NOW - 15 * DAY, "edge": NOW - 14 * DAY, "fresh": NOW - DAY,
                                               "new": NOW - 7 * 3600}


def test_an_upgrade_that_was_installed_leaves_the_state():
    measured, _ = run(state={"gone": NOW - 30 * DAY, "kept": NOW - DAY}, packages=["kept"])
    assert measured["state"]["first_seen"] == {"kept": NOW - DAY}


# Design Doc 0004, section 5.6: an age that the check does not know counts as old,
# with a bound from the Dates of the lists.


def test_with_no_state_file_each_upgrade_counts_as_old():
    measured, families = run(state=None, packages=["openssl"], dates={UPDATES: NOW - 3600})
    assert result(families) == 1
    assert measured["state"] == state_of({"openssl": 0.0}, {UPDATES: NOW - 3600})


def test_a_state_file_of_the_old_form_keeps_its_clocks_and_a_new_upgrade_counts_as_old():
    measured, families = run(state=state_of({"openssl": NOW - DAY}), packages=["openssl", "curl"])
    assert result(families) == 1
    assert measured["state"]["first_seen"] == {"openssl": NOW - DAY, "curl": 0.0}


def test_an_upgrade_seen_6_hours_after_a_run_with_a_result_from_a_list_1_day_old_passes():
    state = state_of({}, {UPDATES: NOW - 6 * 3600 - DAY})
    measured, families = run(state=state, packages=["openssl"])
    assert result(families) == 0
    assert measured["state"]["first_seen"] == {"openssl": NOW - 6 * 3600 - DAY}
    # Its clock runs from the Date of the list: it fails 14 days after that Date.
    _, families = run(state=measured["state"], packages=["openssl"], now=NOW + 12 * DAY + 18 * 3600)
    assert result(families) == 0
    _, families = run(state=measured["state"], packages=["openssl"], now=NOW + 13 * DAY)
    assert result(families) == 1


def test_an_upgrade_from_a_list_with_no_date_in_the_state_counts_as_old():
    measured, families = run(state=state_of({}, {UPDATES: NOW - DAY}), packages=offered(["x"], ["Vendor/stable"]))
    assert result(families) == 1
    assert measured["state"]["first_seen"] == {"x": 0.0}


def test_an_upgrade_of_two_lists_takes_the_oldest_date():
    state = state_of({}, {UPDATES: NOW - DAY, SECURITY: NOW - 2 * DAY})
    measured, _ = run(state=state, packages=offered(["openssl"], [UPDATES, SECURITY, "Vendor/stable"]))
    assert measured["state"]["first_seen"] == {"openssl": NOW - 2 * DAY}


def test_a_host_off_for_a_week_fails_by_the_dates_of_its_last_run_with_a_result():
    """Day 0: a run with a result, lists dated 6 hours before. The host is off until day
    7. Its run at boot fails on old lists, and keeps the state. The next run sees the
    upgrades of the week first: their clocks start at the Date of day 0, not at day 7."""
    day0 = state_of({}, {UPDATES: NOW - 6 * 3600})
    boot, families = run(state=day0, packages=["openssl"], lists=NOW - 8 * DAY, now=NOW + 7 * DAY)
    assert RESULT not in families and boot["failed"] == ["package-lists"]
    after, families = run(state=day0, packages=["openssl"], lists=NOW + 7 * DAY, now=NOW + 7 * DAY + 3600,
                          dates={UPDATES: NOW + 7 * DAY})
    assert result(families) == 0
    assert after["state"]["first_seen"] == {"openssl": NOW - 6 * 3600}
    _, families = run(state=after["state"], packages=["openssl"], now=NOW + 14 * DAY)
    assert result(families) == 1, "the old rule started the clock at day 7 and failed at day 21"


# Design Doc 0004, section 5.7: an update that the host holds back.


def test_a_held_package_at_the_first_run_of_updates_held_fails_at_once():
    """A state of the old form never saw the hold: the plain simulation hides it."""
    measured, families = run(state=state_of({"curl": NOW - DAY}), packages=["curl"], upgrades=["curl", "openssl"],
                             holds=["openssl"])
    assert result(families, "updates-held") == 1
    assert result(families, "updates-pending") == 0
    assert textfile.value(families, "homelab_security_host_updates_held", system=SYSTEM) == 1
    assert measured["state"]["first_seen"] == {"curl": NOW - DAY, "openssl": 0.0}


def test_a_held_package_fails_after_14_days():
    state = state_of({}, {UPDATES: NOW - 3600})
    first, families = run(state=state, packages=[], upgrades=["openssl"], holds=["openssl"])
    assert result(families, "updates-held") == 0
    _, families = run(state=first["state"], packages=[], upgrades=["openssl"], holds=["openssl"],
                      now=NOW + 14 * DAY - 2 * 3600)
    assert result(families, "updates-held") == 0
    _, families = run(state=first["state"], packages=[], upgrades=["openssl"], holds=["openssl"], now=NOW + 14 * DAY)
    assert result(families, "updates-held") == 1
    assert result(families, "updates-pending") == 0


def test_a_package_that_waits_10_days_and_is_then_held_fails_4_days_later():
    """One clock: a hold buys no time."""
    state = state_of({"openssl": NOW - 10 * DAY}, {UPDATES: NOW - 3600})
    held, families = run(state=state, packages=[], upgrades=["openssl"], holds=["openssl"])
    assert result(families, "updates-held") == 0
    assert held["state"]["first_seen"] == {"openssl": NOW - 10 * DAY}
    _, families = run(state=held["state"], packages=[], upgrades=["openssl"], holds=["openssl"],
                      now=NOW + 4 * DAY + 3600)
    assert result(families, "updates-held") == 1


def test_a_phased_update_that_the_host_gets_after_10_days_fails_4_days_later():
    """The second simulation sees the phased update from its first day; the plain one
    offers it on day 10, with the clock of day 0."""
    state = state_of({}, {UPDATES: NOW - 3600})
    phased, families = run(state=state, packages=[], upgrades=["openssl"])
    assert result(families, "updates-pending") == 0 and result(families, "updates-held") == 0
    got, families = run(state=phased["state"], packages=["openssl"], now=NOW + 10 * DAY)
    assert result(families, "updates-pending") == 0
    _, families = run(state=got["state"], packages=["openssl"], now=NOW + 14 * DAY)
    assert result(families, "updates-pending") == 1


def test_a_package_that_leaves_the_list_loses_its_clock():
    state = state_of({"openssl": NOW - 20 * DAY}, {UPDATES: NOW - 3600})
    measured, families = run(state=state, packages=[], upgrades=[], holds=["openssl"])
    assert result(families, "updates-held") == 0
    assert measured["state"]["first_seen"] == {}


BROKEN = {
    "apt-get fails": {"packages": check.QueryError("apt-get exited 100")},
    "the second simulation fails": {"upgrades": check.QueryError("apt-get exited 100")},
    "apt-mark fails": {"holds": check.QueryError("apt-mark exited 1")},
    "no package lists": {"lists": check.QueryError("no Release file")},
    "old package lists": {"lists": NOW - 73 * 3600},
    "an unreadable state file": {"state": "unreadable"},
    "a write as root that succeeds": {"writes": [(0, 1, "uid 0: the write succeeded")]},
}


@pytest.mark.parametrize("case", BROKEN)
def test_a_query_that_fails_gives_no_result_and_no_time(case):
    arguments = dict(BROKEN[case])
    unreadable = arguments.pop("state", None) == "unreadable"
    if unreadable:
        measured = check.measure(NOW, 14, 72, None, packages=dict, lists=lambda: NOW, reboot=lambda: False,
                                 upgrades=dict, holds=set)
        families = textfile.parse(check.render(SYSTEM, measured, NOW))
    else:
        measured, families = run(**arguments)
    assert RESULT not in families
    assert LAST not in families
    assert textfile.value(families, FAILED, system=SYSTEM) == 1


def test_package_lists_at_the_limit_still_give_a_result():
    _, families = run(lists=NOW - 72 * 3600)
    assert RESULT in families


def test_a_failed_package_query_keeps_the_state():
    measured, _ = run(state={"old": NOW - 30 * DAY}, packages=check.QueryError("apt-get exited 100"))
    assert measured["state"] == state_of({"old": NOW - 30 * DAY})


def test_a_fact_that_no_check_reads_costs_no_result():
    _, families = run(reboot=OSError("no access"))
    assert RESULT in families
    assert "homelab_security_host_reboot_pending" not in families


class Proc:
    def __init__(self, returncode, stdout):
        self.returncode, self.stdout = returncode, stdout


SIMULATION = """NOTE: This is only a simulation!
Reading package lists...
Inst libssl3t64 [3.0.13-0ubuntu3.4] (3.0.13-0ubuntu3.5 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])
Inst openssl [3.0.13-0ubuntu3.4] (3.0.13-0ubuntu3.5 Ubuntu:24.04/noble-updates [amd64])
Inst linux-tools-6.8.0-90 (6.8.0-90.91 Ubuntu:24.04/noble-updates [amd64])
Inst gdal-data [3.9.3+dfsg-1~noble0] (3.11.4+dfsg-1~noble0 ubuntugis-unstable:24.04/noble [all])
Inst docker-ce [5:28.5.1-1~ubuntu.24.04~noble] (5:29.0.0-1~ubuntu.24.04~noble Docker CE:noble [amd64])
Conf libssl3t64 (3.0.13-0ubuntu3.5 Ubuntu:24.04/noble-updates [amd64])
"""


def test_the_query_simulates_and_names_the_lists_of_each_upgrade():
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Proc(0, SIMULATION)

    assert check.pending_packages(run=fake_run) == {
        "libssl3t64": (SECURITY, UPDATES), "openssl": (UPDATES,),
        "gdal-data": ("ubuntugis-unstable/noble",), "docker-ce": ("Docker CE/noble",)}
    argv, kwargs = calls[0]
    assert argv[:2] == ["apt-get", "-s"], "a simulation installs nothing"
    assert "Debug::NoLocking=1" in argv and "--ignore-hold" not in argv
    assert kwargs["env"]["LC_ALL"] == "C", "the lines are parsed in English"


def test_the_second_simulation_lists_held_and_phased_upgrades_and_installs_nothing():
    calls = []
    check.every_upgrade(run=lambda argv, **kwargs: calls.append(argv) or Proc(0, SIMULATION))
    assert calls[0][:2] == ["apt-get", "-s"] and calls[0][-1] == "dist-upgrade"
    assert "--ignore-hold" in calls[0]
    assert "APT::Get::Always-Include-Phased-Updates=true" in calls[0]


def test_the_holds_come_from_apt_mark_which_only_reads():
    calls = []
    assert check.held_packages(run=lambda argv, **kwargs: calls.append(argv) or Proc(0, "openssl\nlibc6\n\n")) == {
        "openssl", "libc6"}
    assert calls == [["apt-mark", "showhold"]]


def test_an_upgrade_line_that_can_not_be_read_is_a_failed_query():
    with pytest.raises(check.QueryError):
        check.pending_packages(run=lambda argv, **kwargs: Proc(0, "Inst odd [1.0] no lists\n"))


def test_the_lists_of_the_simulation_have_the_names_of_the_release_files(tmp_path):
    """apt names a list by its Label (or Origin), and its Suite: so do the Dates."""
    release(tmp_path, "archive_dists_noble-updates_InRelease", "Ubuntu", "noble-updates", FRESH)
    release(tmp_path, "security_dists_noble-security_InRelease", "Ubuntu", "noble-security", STALE)
    (tmp_path / "ppa_dists_noble_InRelease").write_text(
        "Origin: LP-PPA-ubuntugis-ubuntugis-unstable\nLabel: ubuntugis-unstable\nSuite: noble\nDate: " + STALE + "\n")
    (tmp_path / "odd_dists_x_InRelease").write_text("Origin: Odd\nSuite: x\nDate: not a date\n")
    assert check.list_dates(tmp_path) == {UPDATES: NOW - 3 * 3600, SECURITY: NOW - 5 * DAY,
                                          "ubuntugis-unstable/noble": NOW - 5 * DAY}
    simulated = check.pending_packages(run=lambda argv, **kwargs: Proc(0, SIMULATION))
    assert set(simulated["gdal-data"]) <= set(check.list_dates(tmp_path))


@pytest.mark.parametrize("query", ["pending_packages", "every_upgrade", "held_packages"])
@pytest.mark.parametrize("answer", [Proc(100, ""), OSError("no apt-get"), subprocess.TimeoutExpired("apt-get", 600)])
def test_an_apt_get_or_apt_mark_that_fails_is_a_failed_query(answer, query):
    def fake_run(argv, **kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer

    with pytest.raises(check.QueryError):
        getattr(check, query)(run=fake_run)


def release(directory, name, origin, suite, date):
    (directory / name).write_text("-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n"
                                  f"Origin: {origin}\nLabel: {origin}\nSuite: {suite}\nDate: {date}\n")


FRESH = "Fri, 13 Nov 2026 09:00:00 UTC"   # NOW - 3 h
STALE = "Sun, 08 Nov 2026 12:00:00 UTC"   # NOW - 5 days


def test_the_age_of_the_lists_is_the_oldest_date_of_the_updates_and_security_lists(tmp_path):
    release(tmp_path, "archive_dists_noble-updates_InRelease", "Ubuntu", "noble-updates", FRESH)
    release(tmp_path, "security_dists_noble-security_InRelease", "Ubuntu", "noble-security",
            "Fri, 13 Nov 2026 06:00:00 UTC")
    # The base suite does not change after its release, and a list of another source
    # does not count: neither makes the age.
    release(tmp_path, "archive_dists_noble_InRelease", "Ubuntu", "noble", "Thu, 25 Apr 2024 15:10:33 UTC")
    release(tmp_path, "vendor_dists_stable_InRelease", "Vendor", "stable-security", "Mon, 01 Jan 2024 00:00:00 UTC")
    (tmp_path / "archive_dists_noble_main_binary-amd64_Packages").write_text("Date: Mon, 01 Jan 2024 00:00:00 UTC\n")
    assert check.lists_time(tmp_path) == NOW - 6 * 3600


def test_a_fresh_list_of_another_source_does_not_hide_an_old_security_list(tmp_path):
    release(tmp_path, "security_dists_noble-security_InRelease", "Ubuntu", "noble-security", STALE)
    release(tmp_path, "archive_dists_noble-updates_InRelease", "Ubuntu", "noble-updates", FRESH)
    release(tmp_path, "vendor_dists_stable_InRelease", "Vendor", "stable", FRESH)
    newest = check.lists_time(tmp_path)
    assert newest == NOW - 5 * DAY
    _, families = run(lists=newest)
    assert RESULT not in families and LAST not in families


def test_old_lists_of_the_base_suite_and_of_another_source_do_not_fail_the_check(tmp_path):
    release(tmp_path, "security_dists_noble-security_InRelease", "Ubuntu", "noble-security", FRESH)
    release(tmp_path, "archive_dists_noble-updates_InRelease", "Ubuntu", "noble-updates", FRESH)
    release(tmp_path, "archive_dists_noble_InRelease", "Ubuntu", "noble", STALE)
    release(tmp_path, "vendor_dists_stable_InRelease", "Vendor", "stable-updates", STALE)
    _, families = run(lists=check.lists_time(tmp_path))
    assert textfile.value(families, RESULT, source="host-check", check="updates-pending", system=SYSTEM,
                          severity="high") == 0


def test_a_debian_security_list_counts(tmp_path):
    release(tmp_path, "deb_dists_bookworm-security_InRelease", "Debian", "bookworm-security", STALE)
    assert check.lists_time(tmp_path) == NOW - 5 * DAY


def test_an_updates_list_with_a_date_that_can_not_be_read_is_a_failed_query(tmp_path):
    release(tmp_path, "security_dists_noble-security_InRelease", "Ubuntu", "noble-security", "not a date")
    with pytest.raises(check.QueryError):
        check.lists_time(tmp_path)


def test_only_lists_of_other_sources_is_a_failed_query(tmp_path):
    release(tmp_path, "archive_dists_noble_InRelease", "Ubuntu", "noble", FRESH)
    release(tmp_path, "vendor_dists_stable_InRelease", "Vendor", "stable-security", FRESH)
    with pytest.raises(check.QueryError):
        check.lists_time(tmp_path)


def test_no_release_file_is_a_failed_query(tmp_path):
    with pytest.raises(check.QueryError):
        check.lists_time(tmp_path)


@pytest.mark.parametrize("text", ["not json", '{"version": 3, "first_seen": {}, "lists": {}}', '{"version": 1}',
                                  '{"version": 1, "first_pending": {"a": "x"}}', '{"version": 2, "first_seen": {}}',
                                  '{"version": 2, "first_seen": {}, "lists": {"a": "x"}}'])
def test_a_state_file_that_can_not_be_read_is_a_failed_query(tmp_path, text):
    (tmp_path / "state.json").write_text(text)
    with pytest.raises(check.QueryError):
        check.read_state(tmp_path / "state.json")


def test_no_state_file_starts_with_no_upgrade_seen(tmp_path):
    assert check.read_state(tmp_path / "state.json") == state_of()


def test_a_state_file_of_the_old_form_keeps_its_clocks_and_has_no_dates(tmp_path):
    (tmp_path / "state.json").write_text('{"version": 1, "first_pending": {"openssl": 1794000000}}')
    assert check.read_state(tmp_path / "state.json") == state_of({"openssl": 1_794_000_000.0})


def test_a_state_file_of_the_new_form_is_read_as_it_was_written(tmp_path):
    state = state_of({"openssl": NOW - DAY}, {UPDATES: NOW - 2 * DAY})
    (tmp_path / "state.json").write_text(check.state_text(state))
    assert check.read_state(tmp_path / "state.json") == state
    assert json.loads((tmp_path / "state.json").read_text())["version"] == 2


def fake_apt(tmp_path, stdout, code=0, holds=""):
    """A directory with an apt-get that prints stdout, and an apt-mark that prints
    holds, first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "out").write_text(stdout)
    (bin_dir / "holds").write_text(holds)
    for name, out in (("apt-get", "out"), ("apt-mark", "holds")):
        tool = bin_dir / name
        tool.write_text(f"#!/bin/sh\ncat '{bin_dir / out}'\nexit {code}\n")
        tool.chmod(0o755)
    return bin_dir


def root_answer(path, answer=0, moment=NOW, uid=0):
    path.write_text(json.dumps({"version": 1, "uid": uid, "answer": answer, "time": moment,
                                "message": f"uid {uid}: an answer"}))


def main(tmp_path, monkeypatch, code=0, lists_date=FRESH, now=NOW, writes=None, root=0, simulation=SIMULATION):
    monkeypatch.setenv("PATH", f"{fake_apt(tmp_path, simulation, code)}:/usr/bin:/bin")
    monkeypatch.setattr(check.time, "time", lambda: now)
    lists = tmp_path / "lists"
    lists.mkdir(exist_ok=True)
    release(lists, "security_dists_noble-security_InRelease", "Ubuntu", "noble-security", lists_date)
    release(lists, "archive_dists_noble-updates_InRelease", "Ubuntu", "noble-updates", lists_date)
    out = tmp_path / "textfile"
    out.mkdir(exist_ok=True)
    tried = []
    if writes is None:
        # The distro user stops at the mode of /mnt/c: no evidence, and no fail.
        monkeypatch.setattr(check, "write_test", lambda path: tried.append(path) or (1000, 2, "uid 1000: Linux"))
    answer = tmp_path / "write-test.json"
    if root is not None:
        root_answer(answer, root, now)
    rc = check.main(["--system", SYSTEM, "--textfile-dir", str(out), "--state-file", str(tmp_path / "state.json"),
                     "--write-test-dir", str(writes or tmp_path / "host"), "--write-test-result", str(answer),
                     "--lists-dir", str(lists), "--reboot-file", str(tmp_path / "reboot-required")])
    assert writes is not None or tried == [tmp_path / "host"]
    return rc, out


def test_the_run_writes_the_whole_file_in_one_step_and_keeps_its_state(tmp_path, monkeypatch):
    rc, out = main(tmp_path, monkeypatch)
    assert rc == 0
    assert sorted(p.name for p in out.iterdir()) == ["tnwks_security_host_check_distro.prom"]
    raw = (out / "tnwks_security_host_check_distro.prom").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw
    families = textfile.parse(raw.decode())
    # No state file: the age of each upgrade is not known, so it counts as old.
    assert result(families) == 4
    state = json.loads((tmp_path / "state.json").read_text())
    assert state == {"version": 2, "first_seen": {"libssl3t64": 0.0, "openssl": 0.0, "gdal-data": 0.0, "docker-ce": 0.0},
                     "lists": {UPDATES: NOW - 3 * 3600, SECURITY: NOW - 3 * 3600}}

    # The upgrades are installed; two new ones come out. The next run dates them by
    # the Dates that the state keeps.
    later = "\n".join(line for line in SIMULATION.replace("libssl3t64", "libc6").replace("openssl", "curl").splitlines()
                      if "gdal" not in line and "docker" not in line) + "\n"
    rc, out = main(tmp_path, monkeypatch, now=NOW + 6 * 3600, lists_date="Sat, 14 Nov 2026 09:00:00 UTC",
                   simulation=later)
    assert rc == 0
    assert result(textfile.parse((out / "tnwks_security_host_check_distro.prom").read_text())) == 0
    assert json.loads((tmp_path / "state.json").read_text())["first_seen"] == {
        "libc6": NOW - 3 * 3600, "curl": NOW - 3 * 3600}

    rc, out = main(tmp_path, monkeypatch, now=NOW + 15 * DAY, lists_date="Sat, 28 Nov 2026 09:00:00 UTC",
                   simulation=later)
    assert rc == 0
    families = textfile.parse((out / "tnwks_security_host_check_distro.prom").read_text())
    assert result(families) == 2
    assert sorted(p.name for p in out.iterdir()) == ["tnwks_security_host_check_distro.prom"]


def test_a_run_with_a_failed_query_writes_no_result_and_exits_1(tmp_path, monkeypatch):
    rc, out = main(tmp_path, monkeypatch, code=100)
    assert rc == 1
    families = textfile.parse((out / "tnwks_security_host_check_distro.prom").read_text())
    assert RESULT not in families and LAST not in families
    assert textfile.value(families, FAILED, system=SYSTEM) == 1


def test_a_state_file_that_can_not_be_read_gives_no_result_and_stays_as_it_is(tmp_path, monkeypatch):
    (tmp_path / "state.json").write_text("{not json")
    before = (tmp_path / "state.json").stat()
    rc, out = main(tmp_path, monkeypatch)
    assert rc == 1
    families = textfile.parse((out / "tnwks_security_host_check_distro.prom").read_text())
    assert RESULT not in families and LAST not in families
    assert (tmp_path / "state.json").read_text() == "{not json"
    after = (tmp_path / "state.json").stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_the_first_run_writes_the_state(tmp_path, monkeypatch):
    assert not (tmp_path / "state.json").exists()
    rc, _ = main(tmp_path, monkeypatch)
    assert rc == 0
    assert json.loads((tmp_path / "state.json").read_text())["version"] == 2


def test_a_run_with_a_failed_query_leaves_the_state_as_it_was(tmp_path, monkeypatch):
    """Design Doc 0004, section 5.6: only a run with a result writes the state."""
    state = state_of({"openssl": NOW - DAY}, {UPDATES: NOW - 2 * DAY})
    (tmp_path / "state.json").write_text(check.state_text(state))
    rc, _ = main(tmp_path, monkeypatch, lists_date=STALE)
    assert rc == 1
    assert check.read_state(tmp_path / "state.json") == state


def refuse(monkeypatch):
    """Windows refuses each new file, as it does for an account with no right."""
    real = os.open

    def fake(path, flags, *args, **kwargs):
        if flags & os.O_CREAT:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, flags, *args, **kwargs)

    monkeypatch.setattr(check.os, "open", fake)


def linux_allows(monkeypatch, allows=True):
    """What access() with the effective ids answers: the check of the mode by Linux."""
    asked = []

    def fake(path, mode, *, effective_ids=False):
        asked.append((path, mode, effective_ids))
        return allows

    monkeypatch.setattr(check.os, "access", fake)
    return asked


def host_dir(tmp_path):
    host = tmp_path / "textfile_inputs"
    host.mkdir()
    (host / "tnwks_security_host_check.prom").write_text("x 1\n")
    return host


def test_a_refusal_by_windows_is_the_pass_and_names_the_user(tmp_path, monkeypatch):
    host = host_dir(tmp_path)
    asked = linux_allows(monkeypatch)
    refuse(monkeypatch)
    uid, answer, message = check.write_test(host)
    assert (uid, answer) == (os.geteuid(), check.REFUSED)
    assert message.startswith(f"uid {os.geteuid()}: Windows refused the write")
    assert asked == [(host, os.W_OK | os.X_OK, True)], "the kernel's check, with the effective ids"
    assert sorted(p.name for p in host.iterdir()) == ["tnwks_security_host_check.prom"]


def test_a_refusal_by_the_mode_of_the_directory_never_reaches_windows_and_is_no_pass(tmp_path, monkeypatch):
    """Reviewer's note: the refusal must come from Windows, not from Linux."""
    host = host_dir(tmp_path)
    linux_allows(monkeypatch, allows=False)
    opened = []
    monkeypatch.setattr(check.os, "open", lambda *a, **k: opened.append(a) or 1 / 0)
    uid, answer, message = check.write_test(host)
    assert (uid, answer) == (os.geteuid(), check.LINUX_REFUSED)
    assert "Linux refuses the write by the mode of the directory" in message
    assert "does not reach Windows" in message
    assert opened == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root passes the check of the mode")
def test_a_directory_whose_mode_refuses_the_user_is_a_refusal_by_linux(tmp_path):
    host = tmp_path / "textfile_inputs"
    host.mkdir()
    host.chmod(0o555)
    try:
        _, answer, message = check.write_test(host)
    finally:
        host.chmod(0o755)
    assert answer == check.LINUX_REFUSED
    assert "dr-xr-xr-x" in message
    assert list(host.iterdir()) == []


def test_a_write_that_succeeds_removes_only_its_own_file(tmp_path):
    host = tmp_path / "textfile_inputs"
    (host / "wsl-distro").mkdir(parents=True)
    kept = {"tnwks_security_host_check.prom": "a 1\n", ".tnwks-write-test-0000000000000000.tmp": "b\n",
            "wsl-distro/tnwks_security_host_check_distro.prom": "c 1\n"}
    for name, text in kept.items():
        (host / name).write_text(text)
    uid, answer, message = check.write_test(host)
    assert (uid, answer) == (os.geteuid(), check.WRITTEN)
    assert message == f"uid {os.geteuid()}: the write succeeded, and the file is removed"
    assert {str(p.relative_to(host)): p.read_text() for p in host.rglob("*") if p.is_file()} == kept


def test_a_file_with_the_name_of_the_try_stays_as_it_is_and_the_try_is_not_done(tmp_path, monkeypatch):
    """The try never opens a file that is there: an exclusive create."""
    host = tmp_path / "textfile_inputs"
    host.mkdir()
    there = host / ".tnwks-write-test-00112233aabbccdd.tmp"
    there.write_text("not the test's\n")
    monkeypatch.setattr(check.secrets, "token_hex", lambda size: "00112233aabbccdd")
    uid, answer, message = check.write_test(host)
    assert answer == check.NOT_DONE
    assert there.read_text() == "not the test's\n"
    assert sorted(p.name for p in host.iterdir()) == [there.name]
    assert check.write_failed([(0, check.REFUSED, ""), (uid, answer, message)])


def test_the_file_of_the_try_is_not_a_file_that_the_exporter_reads(tmp_path, monkeypatch):
    made = []
    real = os.open

    def spy(path, flags, *args, **kwargs):
        made.append(Path(path))
        return real(path, flags, *args, **kwargs)

    monkeypatch.setattr(check.os, "open", spy)
    check.write_test(tmp_path)
    (path,) = made
    assert path.parent == tmp_path
    assert not path.name.endswith(".prom")


@pytest.mark.parametrize("case", ["gone", "a file", "not listed"])
def test_a_directory_that_is_gone_or_can_not_be_listed_is_no_pass(tmp_path, monkeypatch, case):
    """Reviewer's note: a directory that is gone or moved is not a pass."""
    host = tmp_path / "textfile_inputs"
    if case == "a file":
        host.write_text("")
    elif case == "not listed":
        host.mkdir()

        def no_list(path):
            raise PermissionError(13, "Permission denied", str(path))

        monkeypatch.setattr(check.os, "listdir", no_list)
    refuse(monkeypatch)
    uid, answer, message = check.write_test(host)
    assert answer == check.NOT_DONE
    assert message.startswith(f"uid {uid}: the textfile directory")


def test_a_write_that_fails_in_another_way_is_no_pass(tmp_path, monkeypatch):
    def full(path, flags, *args, **kwargs):
        raise OSError(28, "No space left on device", str(path))

    monkeypatch.setattr(check.os, "open", full)
    _, answer, message = check.write_test(tmp_path)
    assert answer == check.NOT_DONE and "neither" in message


VERDICTS = {
    "root refused by Windows, the user stops at Linux": ([(0, 0, ""), (1000, 2, "")], False),
    "root and the user refused by Windows": ([(0, 0, ""), (1000, 0, "")], False),
    "root wrote": ([(0, 1, ""), (1000, 2, "")], True),
    "the user wrote": ([(0, 0, ""), (1000, 1, "")], True),
    "root stops at Linux (a read-only mount)": ([(0, 2, ""), (1000, 2, "")], True),
    "no answer of root": ([(0, 3, ""), (1000, 0, "")], True),
    "no try of root at all": ([(1000, 0, "")], True),
    "the directory is gone for the user": ([(0, 0, ""), (1000, 3, "")], True),
}


@pytest.mark.parametrize("case", VERDICTS)
def test_only_a_refusal_by_windows_for_root_and_no_write_is_the_pass(case):
    tries, failed = VERDICTS[case]
    assert check.write_failed(tries) is failed


def test_the_write_test_runs_first_and_each_try_is_a_series_with_its_uid():
    order = []

    def writes():
        order.append("writes")
        return [(0, check.WRITTEN, "uid 0: the write succeeded"), (1000, check.LINUX_REFUSED, "uid 1000: Linux")]

    measured = check.measure(NOW, 14, 72, state_of(), packages=lambda: order.append("packages") or {},
                             lists=lambda: order.append("lists") or NOW, reboot=lambda: False, writes=writes,
                             upgrades=dict, holds=set)
    assert order == ["writes", "lists", "packages"]
    assert measured["failed"] == ["textfile-write"]
    families = textfile.parse(check.render(SYSTEM, measured, NOW))
    assert RESULT not in families and LAST not in families
    assert textfile.value(families, FAILED, system=SYSTEM) == 1
    assert textfile.series(families, "homelab_security_host_write_test_result") == {
        (("system", SYSTEM), ("uid", "0")): 1.0, (("system", SYSTEM), ("uid", "1000")): 2.0}


@pytest.mark.parametrize(("answer", "moment", "uid"), [
    (None, NOW, 0),             # no file: the step as root did not run
    (0, NOW - 901, 0),          # an answer of an earlier run
    (0, NOW + 120, 0),          # a time in the future
    (0, NOW, 1000),             # not an answer of root
    (7, NOW, 0),                # not an answer
])
def test_an_answer_of_root_that_is_missing_old_or_odd_is_not_done(tmp_path, answer, moment, uid):
    path = tmp_path / "write-test.json"
    if answer is not None:
        root_answer(path, answer, moment, uid)
    assert check.read_root_result(path, NOW)[:2] == (0, check.NOT_DONE)


def test_the_answer_of_root_must_be_owned_by_root_and_not_be_a_link(tmp_path, monkeypatch):
    """The distro user owns the runtime directory, so it could put a file of its own, or
    a link, in the place of the answer of root."""
    assert load().ROOT_UID == 0
    path = tmp_path / "write-test.json"
    root_answer(path, check.REFUSED, NOW - 60)
    assert check.read_root_result(path, NOW)[:2] == (0, check.REFUSED)
    monkeypatch.setattr(check, "ROOT_UID", os.geteuid() + 1)
    uid, answer, message = check.read_root_result(path, NOW)
    assert (uid, answer) == (0, check.NOT_DONE) and f"owned by uid {os.geteuid()}" in message
    monkeypatch.setattr(check, "ROOT_UID", os.geteuid())
    link = tmp_path / "link.json"
    link.symlink_to(path)
    assert check.read_root_result(link, NOW)[:2] == (0, check.NOT_DONE)


def test_a_fifo_or_a_directory_in_the_place_of_the_answer_is_not_done_at_once(tmp_path):
    """The distro user could put a FIFO in that place when the step of root writes no
    answer. Without O_NONBLOCK the open would wait for a writer until the service times
    out, and the run would write nothing. The alarm ends the test if the open waits."""
    fifo = tmp_path / "write-test.json"
    os.mkfifo(fifo)
    directory = tmp_path / "dir.json"
    directory.mkdir()

    def waited(signum, frame):
        raise TimeoutError("the open waited for a writer")

    before = signal.signal(signal.SIGALRM, waited)
    signal.alarm(5)
    try:
        for path in (fifo, directory):
            uid, answer, message = check.read_root_result(path, NOW)
            assert (uid, answer) == (0, check.NOT_DONE) and "not a regular file" in message, path
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, before)


# The image of the jobs of the repository that run Python; Debian has setpriv.
PYTHON_IMAGE = ("docker.io/library/python:3.15.2-slim-trixie"
                "@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928")
READ_AS_NOBODY = ("from pathlib import Path; import sys, time; m = {}; "
                  "exec(compile(open('/check.py').read(), 'check', 'exec'), m); "
                  "m['ROOT_UID'] = int(sys.argv[1]) if len(sys.argv) > 1 else 0; "
                  "print(m['read_root_result'](Path('/run/t/write-test.json'), time.time())[1])")
# A file in the form of an answer of root that refuses: version 1, uid 0, answer 0, now.
FORGE_AS_NOBODY = ("import json, os, time; "
                   "open('/run/t/own.json', 'w').write(json.dumps({'version': 1, 'uid': 0, 'answer': 0, "
                   "'time': time.time(), 'message': 'uid 0: refused'})); "
                   "os.replace('/run/t/own.json', '/run/t/write-test.json')")


def test_an_answer_that_root_writes_is_read_by_the_distro_user_and_not_one_of_its_own():
    """The case of the service, across two users in a container with no network: root
    writes the answer (the step with the prefix +), and a user with no capability reads
    it. That user owns the runtime directory, as the distro user does, so it can put a
    file of its own in that place, in the form of a refusal: the check then reads no
    answer (3). Read as if root were uid 65534, the same file is a refusal (0), so only
    the check of the owner tells them apart. A FIFO in that place is no answer at once."""
    if not shutil.which("docker"):
        if os.environ.get("CI"):
            pytest.fail("Docker is needed to test the answer of root across users")
        pytest.skip("docker not installed")
    nobody = "setpriv --reuid=65534 --regid=65534 --clear-groups"
    script = f"""set -e
mkdir -p /run/t /textfile && chown 65534:65534 /run/t && chmod 0755 /run/t
python3 /check.py --system X --textfile-dir /out --write-test-dir /textfile --write-test-only \\
  --write-test-result /run/t/write-test.json || true
stat -c '%a %u' /run/t/write-test.json
{nobody} python3 -c "{READ_AS_NOBODY}"
{nobody} python3 -c "{FORGE_AS_NOBODY}"
{nobody} python3 -c "{READ_AS_NOBODY}"
{nobody} python3 -c "{READ_AS_NOBODY}" 65534
{nobody} sh -c 'rm -f /run/t/write-test.json && mkfifo /run/t/write-test.json'
{nobody} timeout 20 python3 -c "{READ_AS_NOBODY}"
"""
    result = subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", f"{SCRIPT}:/check.py:ro",
                             PYTHON_IMAGE, "sh", "-c", script],
                            capture_output=True, text=True, timeout=600, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    mode, root_read, own_read, own_as_root, fifo_read = result.stdout.split("\n")[-6:-1]
    # Root may write in the plain directory of the container: answer 1, read as written.
    assert (mode, root_read) == ("644 0", str(check.WRITTEN))
    assert (own_read, own_as_root) == (str(check.NOT_DONE), str(check.REFUSED))
    assert fifo_read == str(check.NOT_DONE)


def test_the_mode_of_a_file_lets_the_distro_user_read_the_answer_of_root(tmp_path):
    """Without root: the bits that the reader needs. Owner root, reader neither root nor
    of its group, so only the bits of "other" count."""
    answer = tmp_path / "write-test.json"
    check.write_atomic(answer, "{}\n", check.ROOT_RESULT_MODE)
    assert stat.S_IMODE(answer.stat().st_mode) & stat.S_IROTH
    check.write_atomic(tmp_path / "state.json", "{}\n")
    assert not stat.S_IMODE((tmp_path / "state.json").stat().st_mode) & stat.S_IROTH, "a state stays private"


def test_a_try_that_fails_in_another_way_names_its_errno(tmp_path, monkeypatch):
    """Whether drvfs gives EACCES or EPERM for a refusal is known only on a host: the
    message of the answer 3 names the errno, so the first live read says it."""
    host = host_dir(tmp_path)
    linux_allows(monkeypatch)
    real = os.open

    def create_fails_with(error):
        def fake(path, flags, *args, **kwargs):
            if flags & os.O_CREAT:
                raise error
            return real(path, flags, *args, **kwargs)
        monkeypatch.setattr(check.os, "open", fake)

    create_fails_with(OSError(5, "Input/output error"))
    _, answer, message = check.write_test(host)
    assert answer == check.NOT_DONE and "errno 5 EIO" in message
    for code, name in ((13, "EACCES"), (1, "EPERM")):
        create_fails_with(PermissionError(code, "denied"))
        _, answer, message = check.write_test(host)
        assert answer == check.REFUSED and f"errno {code} {name}" in message


def test_an_answer_of_root_of_this_run_is_read(tmp_path):
    path = tmp_path / "write-test.json"
    root_answer(path, check.REFUSED, NOW - 60)
    assert check.read_root_result(path, NOW) == (0, check.REFUSED, "uid 0: an answer")


@pytest.mark.parametrize("root", [check.WRITTEN, check.LINUX_REFUSED, check.NOT_DONE, None])
def test_a_run_with_no_refusal_by_windows_for_root_writes_no_result(tmp_path, monkeypatch, root):
    rc, out = main(tmp_path, monkeypatch, root=root)
    assert rc == 1
    families = textfile.parse((out / "tnwks_security_host_check_distro.prom").read_text())
    assert RESULT not in families and LAST not in families
    assert textfile.value(families, FAILED, system=SYSTEM) == 1


def test_a_run_whose_own_write_succeeds_writes_no_result_and_leaves_no_file(tmp_path, monkeypatch):
    host = tmp_path / "textfile_inputs"
    host.mkdir()
    rc, out = main(tmp_path, monkeypatch, writes=host)
    assert rc == 1
    families = textfile.parse((out / "tnwks_security_host_check_distro.prom").read_text())
    assert RESULT not in families and LAST not in families
    assert list(host.iterdir()) == []


def test_one_try_alone_prints_its_uid_and_exits_with_its_answer(tmp_path, monkeypatch, capsys):
    host = tmp_path / "textfile_inputs"
    host.mkdir()
    answer = tmp_path / "run/write-test.json"
    answer.parent.mkdir()
    argv = ["--system", SYSTEM, "--textfile-dir", str(tmp_path / "out"), "--write-test-dir", str(host),
            "--write-test-only"]
    assert check.main([*argv, "--write-test-result", str(answer)]) == check.WRITTEN
    assert f"write test of {SYSTEM}: uid {os.geteuid()}: the write succeeded" in capsys.readouterr().out
    assert list(host.iterdir()) == []
    assert not (tmp_path / "out").exists()
    written = json.loads(answer.read_text())
    assert (written["version"], written["uid"], written["answer"]) == (1, os.geteuid(), check.WRITTEN)
    # The check reads this file as the distro user, with no capability: it needs the
    # read bit of "other", as root owns the file.
    assert stat.S_IMODE(answer.stat().st_mode) == 0o644
    linux_allows(monkeypatch)
    refuse(monkeypatch)
    assert check.main(argv) == check.REFUSED
    assert f"uid {os.geteuid()}: Windows refused the write" in capsys.readouterr().out
    assert check.main([*argv[:-2], str(tmp_path / "gone"), "--write-test-only"]) == check.NOT_DONE


def test_the_mount_of_a_path_is_its_type_and_options(tmp_path):
    info = tmp_path / "mountinfo"
    info.write_text("22 1 0:20 / / rw,relatime - ext4 /dev/sdc rw\n"
                    "80 22 0:51 / /mnt/c rw,noatime - 9p drvfs rw,aname=drvfs;path=C:\\;uid=1000;gid=1000\n")
    assert check.mount_of(Path("/mnt/c/Program Files/x"), info).startswith("9p rw,noatime,rw,aname=drvfs")
    assert check.mount_of(Path("/var/lib"), info) == "ext4 rw,relatime,rw"
    assert check.mount_of(Path("/x"), tmp_path / "none") == "unknown"


def test_a_label_value_is_escaped():
    assert check.label_value('a\\b"c\nd') == 'a\\\\b\\"c\\nd'


def windows_help() -> dict[str, str]:
    text = (WINDOWS / "files/Invoke-TnwksHostCheck.ps1").read_text()
    block = text[text.index("$script:Help"):text.index("$script:Source")]
    return dict(re.findall(r"^\s+(homelab_\w+)\s+=\s+'([^']*)'", block, re.MULTILINE))


def test_each_series_that_the_windows_host_also_writes_has_the_same_text():
    """windows_exporter stops serving a series that two files give two texts."""
    windows = windows_help()
    shared = set(check.HELP) & set(windows)
    assert shared >= {RESULT, LAST, FAILED, "homelab_security_host_updates_pending",
                      "homelab_security_host_reboot_pending"}
    for name in shared:
        assert check.HELP[name] == windows[name], name


def test_the_result_series_has_the_label_names_of_the_windows_host_check():
    """windows_exporter needs the same label names for one family in two files."""
    text = (WINDOWS / "files/Invoke-TnwksHostCheck.ps1").read_text()
    line = next(row for row in text.splitlines() if "Add-Line 'homelab_security_check_result'" in row)
    windows = re.findall(r"(\w+) = \$", line.split("[ordered]@{", 1)[1])
    measured = check.measure(NOW, 14, 72, state_of(), packages=dict, lists=lambda: NOW, reboot=lambda: False,
                             upgrades=dict, holds=set)
    families = textfile.parse(check.render(SYSTEM, measured, NOW))
    labels, _ = textfile.series(families, RESULT)
    assert sorted(windows) == sorted(name for name, _ in labels) == ["check", "severity", "source", "system"]


def test_the_file_name_is_not_a_name_of_the_windows_host():
    """windows_exporter refuses two files with the same name in its tree."""
    windows = yaml.safe_load((WINDOWS / "defaults/main.yml").read_text())
    distro = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    assert distro["wsl_host_check_file"] != windows["host_check_file"]
    assert distro["wsl_host_check_file"].endswith(".prom")


def test_the_distro_writes_to_the_directory_that_the_windows_role_opens_to_it():
    windows = yaml.safe_load((WINDOWS / "defaults/main.yml").read_text())
    distro = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    windows_dir = windows["host_check_distro_dir"].replace("{{ host_check_textfile_dir }}",
                                                           windows["host_check_textfile_dir"])
    assert windows_dir.startswith("C:\\")
    assert distro["wsl_host_check_textfile_dir"] == "/mnt/c/" + windows_dir[3:].replace("\\", "/")


def flat(tasks: list) -> list:
    out = []
    for task in tasks:
        out.extend(flat(task["block"]) if "block" in task else [task])
    return out


def test_the_role_changes_nothing_in_a_check_run():
    """The pull request runs the role with --check on each distro
    (.github/workflows/ansible-hosts.yaml). No task may run in a check run, the handler
    starts a run only in an apply, and a check run does not start a timer whose unit it
    did not write. The one command is the write test, which a check run skips."""
    tasks = flat(yaml.safe_load((ROLE / "tasks/main.yml").read_text()))
    handlers = yaml.safe_load((ROLE / "handlers/main.yml").read_text())
    for task in tasks + handlers:
        assert task.get("check_mode") is not False, task["name"]
        assert "ansible.builtin.shell" not in task, task["name"]
        if "ansible.builtin.command" in task:
            assert task["name"] == "Write test of the textfile directory"
            assert not {"creates", "removes"} & set(task["ansible.builtin.command"]), "a check run would run it"
    run_now = next(task for task in handlers if task["name"] == "Run the host check")
    assert run_now["when"] == "not ansible_check_mode"
    assert run_now["ansible.builtin.systemd"]["no_block"] is True
    timer = next(task for task in tasks if task["name"] == "Host check timer is enabled and running")
    assert "ansible_check_mode" in timer["when"]


def test_the_last_task_of_the_role_tries_the_write_as_root_and_as_the_distro_user():
    """Design Doc 0004, section 5.9: the effective right, not the entry."""
    present = yaml.safe_load((ROLE / "tasks/main.yml").read_text())[0]["block"]
    write, verdict = present[-2:]
    assert write["name"] == "Write test of the textfile directory"
    argv = write["ansible.builtin.command"]["argv"]
    assert argv[0] == "/usr/local/bin/{{ wsl_host_check_name }}"
    assert argv[argv.index("--write-test-dir") + 1] == "{{ wsl_host_check_write_test_dir }}"
    assert argv[-1] == "--write-test-only"
    assert write["become_user"] == "{{ item }}"
    assert write["loop"] == ["root", "{{ wsl_distro_user }}"]
    assert write["changed_when"] is False
    assert "wsl_host_check_write_test_fails_play" in verdict["failed_when"]
    assert "selectattr('item', 'equalto', 'root')" in verdict["failed_when"], "root must get a refusal by Windows"
    assert "select('in', [1, 3])" in verdict["failed_when"], "no try may write, or fail to be done"
    assert verdict["when"] == "not ansible_check_mode"


def test_the_service_tries_the_write_as_root_before_the_check_and_the_check_reads_it():
    """The service runs as the distro user, whose try can stop at the mode of /mnt/c.
    The step before the check runs as root (the prefix +), and its exit code does not
    stop the check (the prefix -)."""
    unit = (ROLE / "templates/tnwks-host-check.service.j2").read_text()
    pre = unit.split("ExecStartPre=", 1)[1].split("\nExecStart=", 1)[0]
    assert pre.startswith("-+/usr/local/bin/{{ wsl_host_check_name }}")
    assert "--write-test-only" in pre
    result = '--write-test-result "/run/{{ wsl_host_check_name }}/write-test.json"'
    assert result in pre
    assert result in unit.split("\nExecStart=", 1)[1]
    assert "RuntimeDirectory={{ wsl_host_check_name }}" in unit


def test_the_write_test_tries_the_textfile_directory_of_the_host():
    """The directory above the one that the distro may change: the exporter reads each
    .prom file under it."""
    windows = yaml.safe_load((WINDOWS / "defaults/main.yml").read_text())
    distro = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    textfile_dir = windows["host_check_textfile_dir"]
    assert distro["wsl_host_check_write_test_dir"] == "/mnt/c/" + textfile_dir[3:].replace("\\", "/")
    assert distro["wsl_host_check_textfile_dir"].startswith(distro["wsl_host_check_write_test_dir"] + "/")
    unit = (ROLE / "templates/tnwks-host-check.service.j2").read_text()
    assert '--write-test-dir "{{ wsl_host_check_write_test_dir }}"' in unit
    assert distro["wsl_host_check_write_test_fails_play"] is False, "on with the start of each distro with no elevation"


def test_the_way_back_stops_the_timer_before_the_files_go():
    blocks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    assert [block["when"] for block in blocks] == ["wsl_host_check_state == 'present'",
                                                   "wsl_host_check_state == 'absent'"]
    absent = blocks[1]["block"]
    assert absent[0]["ansible.builtin.systemd"]["state"] == "stopped"
    gone = absent[1]["loop"]
    assert "{{ wsl_host_check_textfile_dir }}/{{ wsl_host_check_file }}" in gone
    assert yaml.safe_load((ROLE / "defaults/main.yml").read_text())["wsl_host_check_state"] == "present"


def test_both_distros_get_the_same_settings():
    """Equal security on each site: no host or group sets a variable of the role."""
    inventory = ANSIBLE / "inventory"
    for path in sorted([*inventory.glob("host_vars/**/*.yml"), *inventory.glob("group_vars/*.yml")]):
        data = yaml.safe_load(path.read_text()) or {}
        keys = [key for key in data if str(key).startswith("wsl_host_check_")]
        assert not keys, f"{path.relative_to(ROOT)} sets {keys}"


def test_the_service_runs_as_the_distro_user_with_the_limits_of_the_role():
    unit = (ROLE / "templates/tnwks-host-check.service.j2").read_text()
    assert "User={{ wsl_distro_user }}" in unit, "apt-get -s needs no root"
    assert "NoNewPrivileges=yes" in unit
    assert '--system "{{ inventory_hostname }}"' in unit
    for name in re.findall(r"\{\{ (\w+)", unit):
        assert name in ("inventory_hostname", "wsl_distro_user") or name.startswith("wsl_host_check_"), name
    timer = (ROLE / "files/tnwks-host-check.timer").read_text()
    assert "OnCalendar=*-*-* 00/6:38:00" in timer, "the cycle of host-check in sources.yaml"
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    assert defaults["wsl_host_check_updates_max_age_days"] == 14, "Design Doc 0004, section 4.1.4"


def test_the_playbook_runs_the_role_and_the_script_runs_with_the_system_python():
    playbook = yaml.safe_load((ANSIBLE / "playbooks/wsl-distro.yml").read_text())
    roles = [entry["role"] for entry in playbook[0]["roles"]]
    assert "wsl_host_check" in roles
    assert SCRIPT.read_text().startswith("#!/usr/bin/python3\n")


def test_each_distro_of_the_inventory_lists_the_host_check_in_the_registry():
    """Each distro lists the source since its first result was on site 2, so the rule for a
    late result watches it."""
    hosts = yaml.safe_load((ANSIBLE / "inventory/hosts.yml").read_text())
    distros = set(hosts["all"]["children"]["wsl_distros"]["hosts"])
    systems = yaml.safe_load((REGISTRY / "systems.yaml").read_text())
    of_type = {system["name"]: system for system in systems if system["type"] == "wsl-distro"}
    assert set(of_type) == distros
    for system in of_type.values():
        assert "host-check" in system.get("sources", []), system["name"]
    controls = yaml.safe_load((REGISTRY / "controls.yaml").read_text())
    named = {item["check"] for line in controls for item in line["checks"].get("wsl-distro", [])
             if item["source"] == "host-check"}
    assert named == set(check.SEVERITY) == {"updates-pending", "updates-held"}
