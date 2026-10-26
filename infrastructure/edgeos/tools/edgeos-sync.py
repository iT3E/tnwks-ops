#!/usr/bin/env python3
"""Plan/apply an EdgeOS device's configuration from this repo.

The desired configuration is infrastructure/edgeos/<device>/config.sh: the
device's `show configuration commands` output, one `set` line each, with
secrets as ${name} placeholders filled from <device>/secrets.sops.yaml.

  plan   diff live vs repo (secret values masked), then stage the changes in a
         candidate session on the device and DISCARD them, so EdgeOS itself
         validates every line without committing anything. Exit 2 on changes.
  apply  session A stages the changes, schedules commit-confirm (the device
         reboots into its saved config unless confirmed) and commits. Session
         B, a fresh login that proves the device is still reachable,
         confirms and saves. Nothing is saved before the confirm, so the
         rollback target is always the pre-change config.

The device's SSH host key is pinned (device.json host_key). Authentication is
the ssh_* password from SOPS (sshpass), or a key when EDGEOS_SSH_KEY_FILE is
set (CI). EDGEOS_VIA_TUNNEL=1 connects to 127.0.0.1:<ci_port> instead, the
runner's wireproxy forward to the device (device.json ci_port).

Stdlib only. Needs ssh and sops on PATH (bin/sops inside the repo), and
sshpass for password logins.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
SOPS = str(REPO / "bin" / "sops") if (REPO / "bin" / "sops").exists() else "sops"
WRAPPER = "/opt/vyatta/sbin/vyatta-cfg-cmd-wrapper"
CONFIG_MGMT = "/opt/vyatta/sbin/vyatta-config-mgmt.pl"
SHOW_CMDS = "/opt/vyatta/bin/vyatta-op-cmd-wrapper show configuration commands"
PLACEHOLDER = re.compile(r"\$\{([a-z0-9_]+)\}")
SSH_OPTS = [
    "-q", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
    # Older EdgeOS images only offer these; harmless on newer ones.
    "-o", "KexAlgorithms=+diffie-hellman-group14-sha1",
]
PASSWORD_OPTS = ["-o", "BatchMode=no", "-o", "PubkeyAuthentication=no",
                 "-o", "PreferredAuthentications=password,keyboard-interactive"]


def die(msg: str) -> None:
    print(f"edgeos-sync: {msg}", file=sys.stderr)
    sys.exit(1)


def load_device(name: str) -> tuple[dict, dict, list[str]]:
    d = ROOT / name
    device = json.loads((d / "device.json").read_text())
    out = subprocess.run(
        [SOPS, "-d", "--output-type", "json", str(d / "secrets.sops.yaml")],
        capture_output=True, text=True)
    if out.returncode != 0:
        die("cannot decrypt %s/secrets.sops.yaml: %s" % (name, out.stderr.strip()[-300:]))
    secrets = {k: v for k, v in json.loads(out.stdout).items() if k != "sops"}
    lines = []
    for raw in (d / "config.sh").read_text().splitlines():
        if not raw.startswith("set "):
            continue
        missing = [m for m in PLACEHOLDER.findall(raw) if m not in secrets]
        if missing:
            die("config.sh uses undefined secret(s): %s" % ", ".join(missing))
        lines.append(PLACEHOLDER.sub(lambda m: str(secrets[m.group(1)]), raw))
    return device, secrets, lines


def ssh(device: dict, secrets: dict, remote: str, stdin: str | None = None) -> str:
    if os.environ.get("EDGEOS_VIA_TUNNEL") == "1":
        host, port = "127.0.0.1", int(device["ci_port"])
    else:
        host, port = device["host"], int(device.get("port", 22))
    key_file = os.environ.get("EDGEOS_SSH_KEY_FILE")
    with tempfile.NamedTemporaryFile("w", suffix=".known_hosts") as kh:
        # Pin the host key for whatever address we actually connect to.
        kh.write("%s %s\n" % (host if port == 22 else "[%s]:%d" % (host, port), device["host_key"]))
        kh.flush()
        opts = [*SSH_OPTS, "-o", "UserKnownHostsFile=" + kh.name, "-p", str(port)]
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}
        if key_file:
            cmd = ["ssh", *opts, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-i", key_file]
        else:
            cmd = ["sshpass", "-e", "ssh", *opts, *PASSWORD_OPTS]
            env["SSHPASS"] = secrets["ssh_password"]
        cmd += ["%s@%s" % (secrets["ssh_user"], host), remote]
        out = subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=env, timeout=300)
    if out.returncode != 0:
        die("ssh to %s:%d failed (%d): %s" % (host, port, out.returncode, out.stderr.strip()[-500:]))
    return out.stdout


def live_lines(device: dict, secrets: dict) -> list[str]:
    return [l.rstrip() for l in ssh(device, secrets, SHOW_CMDS).splitlines() if l.startswith("set ")]


def tokens(line: str) -> tuple[str, ...]:
    # Compare parsed tokens, not text: EdgeOS quotes some values (MACs, for
    # one) that config.sh may write bare.
    return tuple(shlex.split(line))


def diff(live: list[str], desired: list[str]) -> tuple[list[str], list[str]]:
    live_set, want_set = {tokens(l) for l in live}, {tokens(l) for l in desired}
    # Deepest paths first, so children go before their parents.
    deletes = sorted((l for l in live if tokens(l) not in want_set), key=lambda l: -len(tokens(l)))
    sets = [l for l in desired if tokens(l) not in live_set]
    return deletes, sets


def mask(line: str, secrets: dict) -> str:
    for k, v in secrets.items():
        if k.startswith("ssh_") or not isinstance(v, str) or len(v) < 4:
            continue
        line = line.replace(v, "${%s}" % k)
    return line


def stage_script(deletes: list[str], sets: list[str]) -> str:
    body = ["set -e", f"{WRAPPER} begin", f"trap '{WRAPPER} end' EXIT"]
    body += ["%s delete %s" % (WRAPPER, " ".join(shlex.quote(t) for t in shlex.split(l)[1:])) for l in deletes]
    body += ["%s set %s" % (WRAPPER, " ".join(shlex.quote(t) for t in shlex.split(l)[1:])) for l in sets]
    return "\n".join(body) + "\n"


def run_session(device: dict, secrets: dict, script: str) -> str:
    # The config wrapper must run in the vyattacfg group; one bash keeps one
    # session (the wrapper keys sessions on its parent PID).
    return ssh(device, secrets, "sg vyattacfg -c '/bin/bash -s'", stdin=script)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["plan", "apply"])
    ap.add_argument("device", help="directory under infrastructure/edgeos, e.g. ep-r6")
    ap.add_argument("--confirm-minutes", type=int, default=5)
    args = ap.parse_args()

    device, secrets, desired = load_device(args.device)
    live = live_lines(device, secrets)
    deletes, sets = diff(live, desired)

    print("%s (%s): %d to delete, %d to set" % (args.device, device["host"], len(deletes), len(sets)))
    for l in deletes:
        print("  - " + mask(l, secrets)[4:])
    for l in sets:
        print("  + " + mask(l, secrets)[4:])
    if not deletes and not sets:
        print("No changes. The device matches the repo.")
        return 0

    if args.action == "plan":
        # Device-side validation: stage, then discard. Nothing is committed.
        run_session(device, secrets, stage_script(deletes, sets).replace(
            f"trap '{WRAPPER} end' EXIT", f"trap '{WRAPPER} discard; {WRAPPER} end' EXIT"))
        print("Validated on the device (staged and discarded).")
        return 2

    # commit-confirm, without its interactive prompt (which needs a TTY): the
    # same steps vyatta-config-mgmt.pl takes. The rollback target is the saved
    # boot config, i.e. the pre-change state, since nothing is saved until the
    # confirm. The job id goes where EdgeOS's own `confirm` looks for it.
    rollback = "/config/edgeos-sync-rollback.boot"
    commit = stage_script(deletes, sets) + "\n".join([
        f"cp /config/config.boot {rollback}",
        f"job=$(echo 'sudo sg vyattacfg \"{CONFIG_MGMT} --action rollback --file {rollback}\"'"
        f" | at now + {args.confirm_minutes} minutes 2>&1 | sed -n 's/^job \\([0-9]*\\) .*/\\1/p')",
        '[ -n "$job" ] || { echo "could not schedule the rollback; not committing" >&2; exit 1; }',
        # /var/run needs root; `confirm` reads this file to cancel the job.
        'echo "$job" | sudo tee /var/run/confirm.job >/dev/null',
        # A commit that fails leaves the running config untouched; cancel the
        # rollback so the device does not reboot for nothing.
        f'{WRAPPER} commit || {{ {CONFIG_MGMT} --action confirm; echo "commit failed; rollback cancelled" >&2; exit 1; }}',
    ]) + "\n"
    run_session(device, secrets, commit)
    print("Committed with commit-confirm (%d min). Confirming from a new session..." % args.confirm_minutes)
    # Cancel the rollback, prove it is gone, then save.
    run_session(device, secrets, "\n".join([
        "set -e",
        "job=$(cat /var/run/confirm.job)",
        f"{CONFIG_MGMT} --action confirm",
        'if sudo atq | grep -q "^$job[[:space:]]"; then echo "rollback job $job still queued" >&2; exit 1; fi',
        f"{WRAPPER} save",
    ]) + "\n")
    after_deletes, after_sets = diff(live_lines(device, secrets), desired)
    if after_deletes or after_sets:
        die("confirmed and saved, but the device still differs from the repo (%d/%d)"
            % (len(after_deletes), len(after_sets)))
    print("Confirmed and saved. The device matches the repo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
