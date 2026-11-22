"""The attach of a USB device into the WSL distro (roles wsl_usb_attach and windows_usb_attach).

usbipd attach --wsl and usbipd list first ask the Windows service control manager whether the
usbipd service runs (usbipd-win 4.3.0, CheckAndReportServerRunning), and the service control
manager and the service grant that to interactive and service logons, SYSTEM and the
Administrators only. A task with no elevation (wsl_start_unelevated) holds none of them. The unit
of the distro attaches with the usbip client of usbipd-win instead, straight against the usbipd
service of the host, which needs no Windows right. These tests run the script of the unit in bash
with fakes of the usbip client, modprobe, ip and wslinfo, and of the USB devices in sysfs, and pin
that no attach path with the switch on runs a usbipd command that asks the service control manager.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from test_wsl_start_unelevated import windows_hosts

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLE = ANSIBLE / "roles/wsl_usb_attach"
SCRIPT = ROLE / "files/tnwks-usb-attach.sh"
UNIT = ROLE / "files/tnwks-usb-attach@.service"
BASH = shutil.which("bash")
ZSTICK = "0658:0200"
GATEWAY = "172.25.16.1"
ROUND = 7  # The interval of the loop in the tests: a sleep of 7 is the end of a round.

needs_bash = pytest.mark.skipif(BASH is None, reason="no bash")


def export(*devices: tuple[str, str]) -> str:
    """What `usbip list -r` prints for the devices that the host exports (usbip_list.c)."""
    lines = ["Exportable USB devices", "======================", f" - {GATEWAY}"]
    for busid, vidpid in devices:
        lines += [f"{busid:>11}: unknown vendor : unknown product ({vidpid})",
                  f"{'':>11}: USB\\VID_{vidpid[:4].upper()}&PID_{vidpid[5:].upper()}\\5&1",
                  f"{'':>11}: (Defined at Interface level) (00/00/00)",
                  f"{'':>11}:  0 - Communications / Abstract (modem) / None (02/02/00)", ""]
    return "\n".join(lines) + "\n"


class Fake:
    """The commands and the sysfs of the distro, as files the fakes read and write."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.devices = tmp_path / "bus/usb/devices"
        self.devices.mkdir(parents=True)
        self.calls = tmp_path / "calls"
        self.calls.write_text("")
        for name, body in {
            "modprobe": 'echo "modprobe $*" >>"$FAKE/calls"',
            "ip": 'echo "ip $*" >>"$FAKE/calls"; cat "$FAKE/route" 2>/dev/null || true',
            "usbip": r'''echo "usbip $*" >>"$FAKE/calls"
case "$1" in
  list) cat "$FAKE/list"; exit "$(cat "$FAKE/list.rc" 2>/dev/null || echo 0)" ;;
  attach)
    if [[ -f "$FAKE/attach.fail" ]]; then cat "$FAKE/attach.fail" >&2; exit 1; fi
    # The kernel enumerates the imported device on the vhci_hcd bus.
    dev="$FAKE/sys/devices/platform/vhci_hcd.0/usb3/3-1"
    mkdir -p "$dev"; echo "$FAKE_VID" >"$dev/idVendor"; echo "$FAKE_PID" >"$dev/idProduct"
    ln -sfn "$dev" "$FAKE/bus/usb/devices/3-1" ;;
esac''',
        }.items():
            path = self.bin / name
            path.write_text("#!/bin/bash\n" + body + "\n")
            path.chmod(0o755)
        self.route(f"default via {GATEWAY} dev eth0 proto kernel")
        self.networking("nat")
        self.exports((("1-2", "046d:c52b"), ("1-6", ZSTICK)))

    def route(self, text: str) -> None:
        (self.root / "route").write_text(text + "\n" if text else "")

    def exports(self, devices, rc: int = 0, text: str | None = None) -> None:
        (self.root / "list").write_text(export(*devices) if text is None else text)
        (self.root / "list.rc").write_text(str(rc))

    def networking(self, mode: str) -> None:
        path = self.bin / "wslinfo"
        path.write_text(f"#!/bin/bash\necho {mode}\n")
        path.chmod(0o755)

    def attached(self, vidpid: str = ZSTICK, bus: str = "platform/vhci_hcd.0/usb3/3-1") -> None:
        dev = self.root / "sys/devices" / bus
        dev.mkdir(parents=True)
        (dev / "idVendor").write_text(vidpid[:4] + "\n")
        (dev / "idProduct").write_text(vidpid[5:] + "\n")
        (self.devices / dev.name).symlink_to(dev)

    def unplug(self) -> str:
        """Shell lines that take the imported device off the vhci_hcd bus (a replug, a detach)."""
        return 'rm -f "$FAKE/bus/usb/devices/3-1"; rm -rf "$FAKE/sys/devices/platform/vhci_hcd.0"\n'

    def run(self, *args: str, rounds: int = 0, between: dict[int, str] | None = None) -> dict:
        """The script, sourced, with sleep as a stand-in: the loop stops after the given rounds, and
        the shell lines of between[n] run at the end of round n (each counted from 1)."""
        between = between or {}
        hooks = "".join(f'  if ((ROUND_DONE == {n})); then\n{text}\n  fi\n' for n, text in between.items())
        harness = (f'source "{SCRIPT}"\n'
                   'ROUND_DONE=0\n'
                   'sleep() {\n'
                   f'  [[ "$1" == "{ROUND}" ]] || return 0\n'
                   '  ROUND_DONE=$((ROUND_DONE + 1))\n'
                   + hooks +
                   f'  if ((ROUND_DONE >= {rounds})); then exit 0; fi\n'
                   '}\n'
                   'main "$@"\n')
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "FAKE": str(self.root), "USBIP_CLIENT": str(self.bin / "usbip"),
               "TNWKS_USB_DEVICES": str(self.devices), "TNWKS_USB_INTERVAL": str(ROUND),
               "FAKE_VID": ZSTICK[:4], "FAKE_PID": ZSTICK[5:]}
        result = subprocess.run([BASH, "-c", harness, "tnwks-usb-attach", *args], env=env, check=False,
                                capture_output=True, text=True, timeout=60)
        return {"code": result.returncode, "out": result.stdout.splitlines(), "err": result.stderr,
                "calls": self.calls.read_text().splitlines()}


@pytest.fixture
def fake(tmp_path: Path) -> Fake:
    return Fake(tmp_path)


ATTACH = ["modprobe vhci_hcd", "ip -4 route show default", f"usbip list -r {GATEWAY}"]


@needs_bash
def test_the_bus_id_of_the_vid_pid_is_attached_from_the_default_gateway(fake):
    out = fake.run("--once", "0658:0200")
    assert out["code"] == 0, out
    assert out["calls"] == [*ATTACH, f"usbip attach -r {GATEWAY} -b 1-6"]
    assert out["out"] == [f"0658:0200 is attached (bus id 1-6 from {GATEWAY})"]


@needs_bash
def test_the_vid_pid_matches_in_upper_case_too(fake):
    out = fake.run("--once", "0658:0200".upper())
    assert out["code"] == 0 and out["calls"][-1] == f"usbip attach -r {GATEWAY} -b 1-6", out


@needs_bash
def test_an_attached_device_is_left_as_it_is_with_no_call(fake):
    fake.attached()
    out = fake.run("--once", ZSTICK)
    assert out["code"] == 0 and out["calls"] == [] and out["out"] == ["0658:0200 is attached"], out


@needs_bash
def test_a_device_with_the_vid_pid_off_the_vhci_hcd_bus_is_not_the_attached_one(fake):
    fake.attached(bus="pci0000:00/usb1/1-1")
    out = fake.run("--once", ZSTICK)
    assert out["calls"] == [*ATTACH, f"usbip attach -r {GATEWAY} -b 1-6"], out


@needs_bash
def test_the_host_that_does_not_answer_yet_is_tried_again_and_logged_once(fake):
    fake.exports((), rc=1, text=f"usbip: error: could not connect to {GATEWAY}:3240\n")
    once = fake.run("--once", ZSTICK)
    assert once["code"] == 1 and "usbip attach" not in " ".join(once["calls"]), once
    assert once["out"] == [(f"0658:0200 is not attached: the host {GATEWAY} does not answer yet: "
                            f"usbip: error: could not connect to {GATEWAY}:3240")]
    fake.calls.write_text("")
    # Three rounds with no answer, then the host answers.
    loop = fake.run(ZSTICK, rounds=4, between={3: f"printf '%s' '{export(('1-6', ZSTICK))}' >\"$FAKE/list\"; "
                                                   "echo 0 >\"$FAKE/list.rc\""})
    assert loop["code"] == 0, loop
    assert loop["out"] == [once["out"][0], f"0658:0200 is attached (bus id 1-6 from {GATEWAY})"]
    assert loop["calls"].count(f"usbip list -r {GATEWAY}") == 4
    assert [c for c in loop["calls"] if c.startswith("usbip attach")] == [f"usbip attach -r {GATEWAY} -b 1-6"]


@needs_bash
def test_an_absent_device_is_not_attached_and_is_tried_again(fake):
    fake.exports((("1-2", "046d:c52b"),))
    out = fake.run(ZSTICK, rounds=2)
    assert out["code"] == 0, out
    assert out["out"] == [f"0658:0200 is not attached: the host {GATEWAY} exports no device 0658:0200 (absent, or not bound)"]
    assert out["calls"] == ATTACH * 2


@needs_bash
def test_a_new_bus_id_after_a_replug_is_found_again(fake):
    replug = fake.unplug() + f"printf '%s' '{export(('2-3', ZSTICK))}' >\"$FAKE/list\""
    out = fake.run(ZSTICK, rounds=3, between={1: replug})
    assert out["code"] == 0, out
    assert [c for c in out["calls"] if c.startswith("usbip attach")] == [
        f"usbip attach -r {GATEWAY} -b 1-6", f"usbip attach -r {GATEWAY} -b 2-3"]
    assert out["out"] == [f"0658:0200 is attached (bus id 1-6 from {GATEWAY})",
                          f"0658:0200 is attached (bus id 2-3 from {GATEWAY})"]


@needs_bash
def test_an_attach_that_the_host_refuses_is_logged_and_tried_again(fake):
    (fake.root / "attach.fail").write_text("usbip: error: Attach Request for 1-6 failed - Device busy (exported)\n")
    out = fake.run("--once", ZSTICK)
    assert out["code"] == 1, out
    assert out["out"] == [(f"0658:0200 is not attached: usbip attach of 1-6 from {GATEWAY}: "
                           "usbip: error: Attach Request for 1-6 failed - Device busy (exported)")]


@needs_bash
def test_no_default_route_yet_makes_no_call_to_the_host(fake):
    fake.route("")
    out = fake.run("--once", ZSTICK)
    assert out["code"] == 1 and out["calls"] == ["modprobe vhci_hcd", "ip -4 route show default"], out
    assert out["out"] == ["0658:0200 is not attached: no default route to the host yet"]


@needs_bash
@pytest.mark.parametrize(("mode", "host"), [("nat", GATEWAY), ("mirrored", "127.0.0.1")])
def test_the_host_is_the_gateway_in_nat_mode_and_the_loopback_in_mirrored_mode(fake, mode, host):
    fake.networking(mode)
    out = fake.run("--once", ZSTICK)
    assert f"usbip list -r {host}" in out["calls"] and f"usbip attach -r {host} -b 1-6" in out["calls"], out


@needs_bash
@pytest.mark.parametrize("arg", ["", "0658", "zstick", "0658:0200:1"])
def test_a_wrong_argument_is_a_usage_error(fake, arg):
    out = fake.run(arg)
    assert out["code"] == 2 and "usage:" in out["err"] and out["calls"] == [], out


def code_lines(path: Path) -> list[str]:
    return [line for line in path.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_the_unit_runs_the_script_as_root_and_starts_it_again():
    unit = UNIT.read_text()
    assert "ExecStart=/usr/local/bin/tnwks-usb-attach %i" in unit
    assert "Restart=always" in unit and "User=root" in unit and "WantedBy=multi-user.target" in unit


def test_with_the_switch_on_no_attach_path_runs_a_usbipd_command_that_asks_the_service_manager():
    """usbipd attach and usbipd list ask the service control manager; usbipd state and
    usbipd --version do not (usbipd-win 4.3.0). The unit of the distro runs no usbipd.exe at all,
    the Windows task runs no attach, bind or list with no elevation (its pwsh tests in
    test_wsl_move_start_tasks.py), and the watchdog of each host with the switch skips its USB
    attach."""
    script = "\n".join(code_lines(SCRIPT))
    assert "usbipd.exe" not in script.lower() and not re.search(r"\busbipd\s", script)
    assert 'USBIP="${USBIP_CLIENT:-/mnt/c/Program Files/usbipd-win/WSL/usbip}"' in script
    assert re.findall(r'"\$USBIP" (\w+)', script) == ["list", "attach"]
    for name, host in windows_hosts().items():
        if host["wsl_start_unelevated"]:
            assert "-SkipUsb" in host["wsl_watchdog_args"].split(), name
    watchdog = (ANSIBLE / "roles/windows_wsl_maintenance/files/Watch-TnwksWsl.ps1").read_text()
    assert "if (-not $SkipUsb) {\n    Sync-UsbDevices\n}" in watchdog


def test_each_distro_with_a_device_is_on_a_host_that_binds_it():
    """The client attaches a bound device only: each device of a distro is in the bound devices
    of its Windows host."""
    usb = yaml.safe_load((ANSIBLE / "roles/windows_usb_attach/defaults/main.yml").read_text())
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    for name, host in windows_hosts().items():
        distro = ANSIBLE / f"inventory/host_vars/{name}-ubuntu/main.yml"
        own = yaml.safe_load(distro.read_text()) or {}
        devices = own.get("wsl_usb_attach_devices", defaults["wsl_usb_attach_devices"])
        bound = host.get("usb_attach_bound_devices", usb["usb_attach_bound_devices"])
        assert set(devices) <= set(bound), name


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="no shellcheck")
def test_the_script_passes_shellcheck():
    result = subprocess.run(["shellcheck", str(SCRIPT)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
