#!/usr/bin/env python3
"""Prove the monitoring mount's two assumptions on a disposable volume and container.

A mount under a named volume on the host reaches a running container, and an
immutable bare mountpoint refuses writes until then. Needs root through sudo.
"""

import secrets
import subprocess

IMAGE = "docker.io/library/caddy:2.11.9-alpine@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee"


def run(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0 or not check, result.stderr[:1000]
    return result


def main():
    name = "observability-mount-fixture-" + secrets.token_hex(4)
    point = None
    try:
        run("docker", "volume", "create", name)
        run("docker", "run", "-d", "--name", name, "--volume", name + ":/var/fixture",
            "--entrypoint", "/bin/sh", IMAGE, "-c", "sleep 300")
        source = run("docker", "volume", "inspect", "--format", "{{.Mountpoint}}", name).stdout.strip()
        point = source + "/observability"
        run("sudo", "-n", "mkdir", point)
        run("sudo", "-n", "chattr", "+i", point)
        bare = run("docker", "exec", name, "mkdir", "/var/fixture/observability/pvc", check=False)
        assert bare.returncode != 0, "A bare mountpoint accepted a write"
        run("sudo", "-n", "mount", "-t", "tmpfs", "-o", "size=1m", "fixture", point)
        run("docker", "exec", name, "mkdir", "/var/fixture/observability/pvc")
        assert run("sudo", "-n", "test", "-d", point + "/pvc", check=False).returncode == 0
        run("sudo", "-n", "umount", point)
        gone = run("docker", "exec", name, "test", "-e", "/var/fixture/observability/pvc", check=False)
        assert gone.returncode != 0, "The unmount did not reach the container"
        print("PASS: an immutable bare mountpoint refuses writes; a host mount reaches and leaves a running container")
    finally:
        if point:
            run("sudo", "-n", "umount", point, check=False)
            run("sudo", "-n", "chattr", "-i", point, check=False)
        run("docker", "rm", "-f", name, check=False)
        run("docker", "volume", "rm", name, check=False)


if __name__ == "__main__":
    main()
