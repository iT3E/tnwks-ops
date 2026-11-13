#!/usr/bin/env python3
"""Mask every value of SOPS files in this job's log, before Terraform runs.

    python3 -I .github/scripts/sops-mask.py --sops PATH FILE [FILE ...]
    python3 -I .github/scripts/sops-mask.py --sops PATH --json '["a.sops.yaml"]'

terraform.yaml runs this for a root whose [groups.terraform.<name>] entry in
.github/ci/domains.toml has `mask_sops` (file names relative to the root, which
is the working directory). The logs are public, and a Terraform error can
quote a value that the root reads from SOPS (a private repository's name in
an API URL, for example).

Each file is decrypted with sops (SOPS_AGE_KEY from the runner's env) and
every non-empty string value, at any depth, is printed as one
`::add-mask::<line>` per non-blank line, trimmed. Nothing else goes to stdout.

Fails closed (exit 1, nothing printed to stdout) when sops is missing, a file
is missing, is not a *.sops.yaml file, does not decrypt, or has no string
value. Errors name the file only: never a value, and never sops's own
output.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


class MaskError(Exception):
    pass


def escape(value: str) -> str:
    """Workflow-command data escaping (actions/runner ActionCommand.cs)."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def strings(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in strings(v)]
    return []


def lines(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        for line in value.splitlines():
            line = line.strip()
            if line and line not in out:
                out.append(line)
    return out


def decrypt(sops: str, path: str) -> object:
    if not path.endswith(".sops.yaml"):
        raise MaskError(f"{path}: not a *.sops.yaml file")
    if not Path(path).is_file():
        raise MaskError(f"{path}: no such file")
    try:
        result = subprocess.run(
            [sops, "--decrypt", "--output-type", "json", path],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        raise MaskError(f"sops not runnable at {sops}") from None
    if result.returncode != 0:
        raise MaskError(f"{path}: did not decrypt (sops exit {result.returncode})")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise MaskError(f"{path}: sops output is not JSON") from None


def masks(sops: str, files: list[str]) -> list[str]:
    if not files:
        raise MaskError("no files to mask")
    out: list[str] = []
    for path in files:
        found = lines(strings(decrypt(sops, path)))
        if not found:
            raise MaskError(f"{path}: no values to mask")
        out.extend(line for line in found if line not in out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sops", default="sops", help="sops binary")
    parser.add_argument("--json", help="JSON array of files (the matrix's mask_sops)")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)

    try:
        files = list(args.files)
        if args.json is not None:
            listed = json.loads(args.json)
            if not isinstance(listed, list) or not all(isinstance(f, str) for f in listed):
                raise MaskError("--json is not an array of file names")
            files.extend(listed)
        found = masks(args.sops, files)
    except (MaskError, ValueError) as e:
        print(f"::error::sops-mask: {e}" if isinstance(e, MaskError) else "::error::sops-mask: --json is not JSON", file=sys.stderr)
        return 1
    sys.stdout.write("".join(f"::add-mask::{escape(line)}\n" for line in found))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
