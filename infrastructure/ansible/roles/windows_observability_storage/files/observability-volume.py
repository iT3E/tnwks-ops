#!/usr/bin/env python3
"""Guard and mount the one explicitly managed monitoring VHDX filesystem.

Formatting requires an exact newly attached whole-disk device, expected size,
no partitions/mounts/signatures and the pre-recorded random filesystem UUID.
Normal startup only finds and mounts that UUID; it never formats a device.

The disk mounts inside worker-1's /var volume on the distro. Docker's data root
is a shared mount and Talos' /var is its slave, so the mount reaches the running
node with no restart and a missing disk stops only the pods that use it.
"""

import argparse
import json
import os
import subprocess
import uuid
from pathlib import Path

GIB = 1024**3
WORKER = "homelab-wsl-worker-1"
CLUSTER = "homelab-wsl"
STORAGE = "lib/rancher/k3s/storage/observability"


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed; no fallback filesystem was selected")
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


def disks():
    result = json.loads(run("lsblk", "--json", "--bytes", "--paths", "--output",
                            "NAME,SIZE,TYPE,FSTYPE,UUID,LABEL,MOUNTPOINTS"))["blockdevices"]
    for disk in result:
        # WSL's udev cache may lag a just-created filesystem. Probe metadata
        # directly, without changing any device, rather than trusting the cache.
        probe = subprocess.run(["blkid", "-p", "-o", "export", disk["name"]],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            fields = dict(line.split("=", 1) for line in probe.stdout.splitlines() if "=" in line)
            for source, target in [("TYPE", "fstype"), ("UUID", "uuid"), ("LABEL", "label")]:
                disk[target] = fields.get(source)
    return result


def validate_disk(device, expected_bytes, expected_uuid, allow_blank=False):
    assert device["type"] == "disk" and int(device["size"]) == expected_bytes, "Wrong disk type/size"
    assert not device.get("children"), "Partitioned devices are never initialized"
    if device.get("fstype"):
        assert device["fstype"] == "ext4" and device.get("uuid") == expected_uuid, "Unexpected existing filesystem"
    else:
        assert allow_blank, "The monitoring filesystem is absent; startup never formats"
    return device


def initialize(device, expected_bytes, expected_uuid):
    validate_disk(device, expected_bytes, expected_uuid, allow_blank=True)
    assert not any(device.get("mountpoints") or []), "Mounted devices are never initialized"
    if device.get("fstype") == "ext4":
        # A previous initialization may have finished before Windows recorded
        # completion. The pre-recorded UUID proves this is that same volume.
        return
    signatures = json.loads(run("wipefs", "--no-act", "--json", device["name"]))
    assert not signatures.get("signatures"), "Existing disk signatures prevent initialization"
    run("mkfs.ext4", "-q", "-m", "1", "-i", "1048576", "-L", "tnwks-obsv",
        "-U", expected_uuid, device["name"])


def mounted(device, point):
    if not point.is_mount():
        return False
    return os.stat(point).st_dev == os.stat(device["name"]).st_rdev


def attach(device, point, expected_uuid):
    if not mounted(device, point):
        assert not point.is_mount(), "Mountpoint contains a different filesystem"
        point.mkdir(parents=True, exist_ok=True, mode=0o711)
        assert not any(point.iterdir()), "Unbacked mountpoint is not empty; refusing to cover data"
        assert not any(device.get("mountpoints") or []), "Monitoring disk is mounted elsewhere"
        # containerd creates a missing hostPath itself, so a pod starting before
        # this mount would write to the distro disk. Immutable, it fails and retries.
        run("chattr", "+i", str(point))
        run("mount", "-t", "ext4", "-o", "noatime", device["name"], str(point))
    assert mounted(device, point), "Expected filesystem was not mounted"
    marker = point / ".tnwks-observability-id"
    if marker.exists():
        assert marker.read_text().strip() == expected_uuid, "Volume marker mismatch"
    else:
        marker.write_text(expected_uuid + "\n")
        marker.chmod(0o644)
    point.chmod(0o711)
    fd = os.open(point, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["probe", "initialize", "mount", "check"])
    parser.add_argument("--uuid")
    parser.add_argument("--size-gib", type=int, default=20, choices=[20])
    parser.add_argument("--device")
    args = parser.parse_args()
    observed = disks()
    if args.command == "probe":
        print(json.dumps({"disks": observed}))
        return
    expected_uuid = str(uuid.UUID(args.uuid))
    assert os.geteuid() == 0, "Mount management requires root"
    if args.command == "initialize":
        selected = [d for d in observed if d["name"] == args.device]
        assert len(selected) == 1, "New disk selection is ambiguous"
        initialize(selected[0], args.size_gib * GIB, expected_uuid)
        observed = disks()
    selected = [d for d in observed if d.get("uuid") == expected_uuid]
    point = mountpoint()
    if args.command == "check":
        ready = len(selected) == 1 and mounted(selected[0], point)
        marker = point / ".tnwks-observability-id"
        ready = ready and marker.is_file() and marker.read_text().strip() == expected_uuid
        print(json.dumps({"ready": bool(ready), "available": len(selected) == 1}))
        return
    assert len(selected) == 1, "Expected monitoring UUID is unavailable or ambiguous"
    validate_disk(selected[0], args.size_gib * GIB, expected_uuid)
    attach(selected[0], point, expected_uuid)
    print(json.dumps({"ready": True, "uuid": expected_uuid, "size_gib": args.size_gib}))


if __name__ == "__main__":
    main()
