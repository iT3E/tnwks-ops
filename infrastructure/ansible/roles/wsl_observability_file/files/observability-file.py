#!/usr/bin/python3
"""Keep the monitoring data of site 1 in a file on the disk of the distro.

The file is 19 GiB, formatted ext4 with a random UUID that is recorded before
the format, and mounted by a loop device. Its point is the point of the
monitoring VHDX before it (role windows_observability_storage): the storage
path in the /var volume of worker-1. No disk is attached by Windows, so the
account that owns the distro needs no admin right for it (Design Doc 0004,
section 5.13).

  create   make the file and its file system, once. A file with no record, or
           a file system with another UUID, is never formatted.
  stage    mount the file at the stage folder, for the copy of the data.
  unstage  unmount it from the stage folder.
  mount    mount the file at the point (the timer, in the phase file).
  release  unmount it from the point (the way back to the VHDX).
  check    print whether the file is mounted at the point, and at the stage.

The file is never mounted at two places, and never over another file system
or over data.
"""

import argparse
import json
import os
import subprocess
import uuid
from pathlib import Path

GIB = 1024**3
SIZE_GIB = 19
DIR = Path("/var/lib/tnwks-observability")
IMAGE = DIR / "observability.ext4"
STATE = DIR / "file.json"
STAGE = DIR / "stage"
MARKER = ".tnwks-observability-id"
# The same worker and path as observability-volume.py of the VHDX.
WORKER = "homelab-wsl-worker-1"
CLUSTER = "homelab-wsl"
STORAGE = "lib/rancher/k3s/storage/observability"


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed; nothing else is tried")
    return result.stdout


def mountpoint():
    # Format only these fields: the full inspect output holds the Talos config.
    labels, mounts = run("docker", "inspect", "--format", "{{json .Config.Labels}}\n{{json .Mounts}}",
                         WORKER).splitlines()
    labels = json.loads(labels)
    assert labels.get("talos.cluster.name") == CLUSTER and labels.get("talos.type") == "worker", "Unexpected worker"
    sources = [m["Source"] for m in json.loads(mounts) if m["Type"] == "volume" and m["Destination"] == "/var"]
    assert len(sources) == 1, "Worker has no single /var volume"
    return Path(sources[0]) / STORAGE


def write_state(record):
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(record) + "\n")
    temporary.chmod(0o600)
    os.replace(temporary, STATE)


def read_state():
    record = json.loads(STATE.read_text())
    assert record.get("path") == str(IMAGE) and record.get("size_gib") == SIZE_GIB, "The record is of another file"
    record["uuid"] = str(uuid.UUID(record["uuid"]))
    assert record.get("phase") in ("pending", "initialized"), "Unknown phase of the file"
    return record


def probe():
    # blkid exits 2 when it finds no signature.
    result = subprocess.run(["blkid", "-p", "-o", "export", str(IMAGE)], capture_output=True, text=True)
    if result.returncode == 2:
        return {}
    if result.returncode:
        raise RuntimeError("blkid failed")
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def loops():
    output = run("losetup", "--json", "--list").strip()
    devices = json.loads(output)["loopdevices"] if output else []
    return [device["name"] for device in devices if device.get("back-file") == str(IMAGE)]


def mounted(point):
    if not point.is_mount():
        return False
    return any(os.stat(point).st_dev == os.stat(device).st_rdev for device in loops())


def verify(record):
    assert IMAGE.stat().st_size == SIZE_GIB * GIB, "The file has the wrong size"
    found = probe()
    assert found.get("TYPE") == "ext4" and found.get("UUID") == record["uuid"], "The file system is not the recorded one"


def create():
    DIR.mkdir(mode=0o700, exist_ok=True)
    DIR.chmod(0o700)
    if not STATE.exists():
        assert not IMAGE.exists(), "The file has no record; it is never used or formatted"
        write_state({"path": str(IMAGE), "size_gib": SIZE_GIB, "uuid": str(uuid.uuid4()), "phase": "pending"})
    record = read_state()
    if record["phase"] == "initialized":
        verify(record)
        return False
    if not IMAGE.exists():
        # fallocate keeps the space in the file system of the distro. The disk
        # file of the distro grows only by the blocks that are written.
        run("fallocate", "--length", str(SIZE_GIB * GIB), str(IMAGE))
        IMAGE.chmod(0o600)
    assert IMAGE.stat().st_size == SIZE_GIB * GIB, "The file has the wrong size"
    assert not loops(), "An attached file is never formatted"
    found = probe()
    if found:
        # A run may have formatted it before it recorded that: the UUID is the proof.
        assert found.get("TYPE") == "ext4" and found.get("UUID") == record["uuid"], "Unexpected signature in the file"
    else:
        signatures = json.loads(run("wipefs", "--no-act", "--json", str(IMAGE)) or '{"signatures": []}')
        assert not signatures.get("signatures"), "Signatures in the file prevent the format"
        run("mkfs.ext4", "-q", "-m", "1", "-i", "1048576", "-L", "tnwks-obsv", "-U", record["uuid"], str(IMAGE))
    verify(record)
    record["phase"] = "initialized"
    write_state(record)
    return True


def attach(point, record):
    assert record["phase"] == "initialized", "The file is not made yet"
    if not mounted(point):
        verify(record)
        assert not point.is_mount(), "The point holds another file system"
        assert not loops(), "The file is mounted at another place"
        point.mkdir(parents=True, exist_ok=True, mode=0o711)
        assert not any(point.iterdir()), "The point holds data; it is never covered"
        if point != STAGE:
            # containerd makes a missing hostPath itself, so a pod that starts
            # before this mount would write to the distro disk. Immutable, it fails and retries.
            run("chattr", "+i", str(point))
        run("mount", "-t", "ext4", "-o", "loop,noatime", str(IMAGE), str(point))
    assert mounted(point), "The file is not mounted at the point"
    marker = point / MARKER
    if marker.exists():
        assert marker.read_text().strip() == record["uuid"], "The marker is of another file system"
    else:
        marker.write_text(record["uuid"] + "\n")
        marker.chmod(0o644)
    point.chmod(0o711)
    fd = os.open(point, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def release(point):
    if mounted(point):
        run("umount", str(point))
    assert not mounted(point), "The file is still mounted at the point"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["create", "stage", "unstage", "mount", "release", "check"])
    args = parser.parse_args()
    assert os.geteuid() == 0, "The file of the monitoring data needs root"
    if args.command == "create":
        print(json.dumps({"created": create()}))
        return
    record = read_state()
    if args.command == "check":
        point = mountpoint()
        marker = point / MARKER
        ready = mounted(point) and marker.is_file() and marker.read_text().strip() == record["uuid"]
        print(json.dumps({"ready": bool(ready), "staged": mounted(STAGE)}))
        return
    if args.command in ("stage", "unstage"):
        point = STAGE
    else:
        point = mountpoint()
    if args.command in ("stage", "mount"):
        attach(point, record)
    else:
        release(point)
    print(json.dumps({"command": args.command, "point": str(point), "uuid": record["uuid"]}))


if __name__ == "__main__":
    main()
