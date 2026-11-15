"""The host check of a WSL distro (role wsl_host_check) keeps the contract of the
registry, writes the shared series with the text of the Windows host check, and a
query that fails gives no result, never a pass.

The script is loaded as a module, and each query gets a fake answer.
"""

import importlib.util
import json
import os
import re
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


# The tries of a host that refuses the write: root reaches Windows, which refuses; the
# distro user stops at the mode of /mnt/c.
PASSING_TRIES = [(0, 0, "uid 0: Windows refused the write"), (1000, 2, "uid 1000: Linux refuses the write")]


def run(state=None, packages=(), lists=NOW - 3600, reboot=False, writes=None, **limits):
    def fake_packages():
        if isinstance(packages, Exception):
            raise packages
        return list(packages)

    def fake_lists():
        if isinstance(lists, Exception):
            raise lists
        return lists

    def fake_reboot():
        if isinstance(reboot, Exception):
            raise reboot
        return reboot

    def fake_writes():
        return writes if writes is not None else PASSING_TRIES

    measured = check.measure(NOW, limits.get("days", 14), limits.get("hours", 72),
                             {} if state is None else state,
                             packages=fake_packages, lists=fake_lists, reboot=fake_reboot, writes=fake_writes)
    return measured, textfile.parse(check.render(SYSTEM, measured, NOW))


def test_a_distro_with_no_pending_upgrade_gives_a_result_of_0_and_the_time():
    measured, families = run()
    assert textfile.series(families, RESULT) == {
        (("check", "updates-pending"), ("severity", "high"), ("source", "host-check"), ("system", SYSTEM)): 0.0}
    assert textfile.value(families, LAST, source="host-check", system=SYSTEM) == NOW
    assert textfile.value(families, FAILED, system=SYSTEM) == 0
    assert textfile.value(families, "homelab_security_host_updates_pending", system=SYSTEM) == 0
    assert textfile.value(families, "homelab_security_host_reboot_pending", system=SYSTEM) == 0
    assert measured["state"] == {}


def test_an_upgrade_fails_the_check_only_after_it_waited_past_the_limit():
    state = {"old": NOW - 15 * DAY, "edge": NOW - 14 * DAY, "fresh": NOW - DAY}
    measured, families = run(state=state, packages=["old", "edge", "fresh", "new"], reboot=True)
    assert textfile.value(families, RESULT, source="host-check", check="updates-pending", system=SYSTEM, severity="high") == 1
    assert textfile.value(families, "homelab_security_host_updates_pending", system=SYSTEM) == 4
    assert textfile.value(families, "homelab_security_host_reboot_pending", system=SYSTEM) == 1
    assert measured["state"] == {"old": NOW - 15 * DAY, "edge": NOW - 14 * DAY, "fresh": NOW - DAY, "new": NOW}


def test_an_upgrade_that_was_installed_leaves_the_state():
    measured, _ = run(state={"gone": NOW - 30 * DAY, "kept": NOW - DAY}, packages=["kept"])
    assert measured["state"] == {"kept": NOW - DAY}


BROKEN = {
    "apt-get fails": {"packages": check.QueryError("apt-get exited 100")},
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
        measured = check.measure(NOW, 14, 72, None, packages=list, lists=lambda: NOW, reboot=lambda: False)
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
    assert measured["state"] == {"old": NOW - 30 * DAY}


def test_a_fact_that_no_check_reads_costs_no_result():
    _, families = run(reboot=OSError("no access"))
    assert RESULT in families
    assert "homelab_security_host_reboot_pending" not in families


class Proc:
    def __init__(self, returncode, stdout):
        self.returncode, self.stdout = returncode, stdout


SIMULATION = """NOTE: This is only a simulation!
Reading package lists...
Inst libssl3t64 [3.0.13-0ubuntu3.4] (3.0.13-0ubuntu3.5 Ubuntu:24.04/noble-updates [amd64])
Inst openssl [3.0.13-0ubuntu3.4] (3.0.13-0ubuntu3.5 Ubuntu:24.04/noble-updates [amd64])
Inst linux-tools-6.8.0-90 (6.8.0-90.91 Ubuntu:24.04/noble-updates [amd64])
Conf libssl3t64 (3.0.13-0ubuntu3.5 Ubuntu:24.04/noble-updates [amd64])
"""


def test_the_query_simulates_and_counts_only_upgrades():
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Proc(0, SIMULATION)

    assert check.pending_packages(run=fake_run) == ["libssl3t64", "openssl"]
    argv, kwargs = calls[0]
    assert argv[:2] == ["apt-get", "-s"], "a simulation installs nothing"
    assert "Debug::NoLocking=1" in argv
    assert kwargs["env"]["LC_ALL"] == "C", "the lines are parsed in English"


@pytest.mark.parametrize("answer", [Proc(100, ""), OSError("no apt-get"), subprocess.TimeoutExpired("apt-get", 600)])
def test_an_apt_get_that_fails_is_a_failed_query(answer):
    def fake_run(argv, **kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer

    with pytest.raises(check.QueryError):
        check.pending_packages(run=fake_run)


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


@pytest.mark.parametrize("text", ["not json", '{"version": 2, "first_pending": {}}', '{"version": 1}',
                                  '{"version": 1, "first_pending": {"a": "x"}}'])
def test_a_state_file_that_can_not_be_read_is_a_failed_query(tmp_path, text):
    (tmp_path / "state.json").write_text(text)
    with pytest.raises(check.QueryError):
        check.read_state(tmp_path / "state.json")


def test_no_state_file_starts_with_no_upgrade_seen(tmp_path):
    assert check.read_state(tmp_path / "state.json") == {}


def fake_apt(tmp_path, stdout, code=0):
    """A directory with an apt-get that prints stdout, first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "out").write_text(stdout)
    apt = bin_dir / "apt-get"
    apt.write_text(f"#!/bin/sh\ncat '{bin_dir / 'out'}'\nexit {code}\n")
    apt.chmod(0o755)
    return bin_dir


def root_answer(path, answer=0, moment=NOW, uid=0):
    path.write_text(json.dumps({"version": 1, "uid": uid, "answer": answer, "time": moment,
                                "message": f"uid {uid}: an answer"}))


def main(tmp_path, monkeypatch, code=0, lists_date=FRESH, now=NOW, writes=None, root=0):
    monkeypatch.setenv("PATH", f"{fake_apt(tmp_path, SIMULATION, code)}:/usr/bin:/bin")
    monkeypatch.setattr(check.time, "time", lambda: now)
    lists = tmp_path / "lists"
    lists.mkdir(exist_ok=True)
    release(lists, "security_dists_noble-security_InRelease", "Ubuntu", "noble-security", lists_date)
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
    assert textfile.value(families, RESULT, source="host-check", check="updates-pending", system=SYSTEM, severity="high") == 0
    state = json.loads((tmp_path / "state.json").read_text())
    assert state == {"version": 1, "first_pending": {"libssl3t64": NOW, "openssl": NOW}}

    rc, out = main(tmp_path, monkeypatch, now=NOW + 15 * DAY, lists_date="Sat, 28 Nov 2026 09:00:00 UTC")
    assert rc == 0
    families = textfile.parse((out / "tnwks_security_host_check_distro.prom").read_text())
    assert textfile.value(families, RESULT, source="host-check", check="updates-pending", system=SYSTEM, severity="high") == 2
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
    assert json.loads((tmp_path / "state.json").read_text())["version"] == 1


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

    measured = check.measure(NOW, 14, 72, {}, packages=lambda: order.append("packages") or [],
                             lists=lambda: order.append("lists") or NOW, reboot=lambda: False, writes=writes)
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
    measured = check.measure(NOW, 14, 72, {}, packages=list, lists=lambda: NOW, reboot=lambda: False)
    families = textfile.parse(check.render(SYSTEM, measured, NOW))
    (labels,) = textfile.series(families, RESULT)
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
    assert named == set(check.SEVERITY) == {"updates-pending"}
