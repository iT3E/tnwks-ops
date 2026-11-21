"""The names-only check of the encrypted files of the Flux children outside
the class, before they substitute from the reduced Secret
(docs/operations/flux-admission.md, "Substitution").

    python3 kubernetes/apps/flux-system/admission/substitution_names.py

The owner runs it by hand, with the key that decrypts the files (sops on the
PATH, as bin/sops gives it in the repo). For each site, it decrypts each
encrypted file of the build of each child outside the class into the memory
of this process, finds each variable of Flux (${NAME}, not an escaped
$${NAME} and not one with a default), and prints only the path of the file
and the names that are neither keys of the reduced Secret
cluster-secrets-apps of the site (the template of its Kustomization
flux-substitution) nor settings of the site nor inline substitutes of the
child. It prints, writes and logs no value: the decrypted text stays in a
variable of this process, and the error output of sops is dropped.

Flux substitutes in strict mode (the default of kustomize-controller since
v1.9): a variable with no key and no default stops the build of its child.
So each name that it prints would stop the apply of that child on that site
at the switch. An empty output (exit 0) means the switch stops no build.
Exit 1: names to add to the reduced Secret of the site.
Exit 2: a file did not decrypt (its path and the exit code of sops only).

Needs PyYAML, as classlist.py does.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import classlist

# The Kustomization of the reduced Secret; each site has a template of its own.
SUBSTITUTION_CHILD = "flux-system/flux-substitution"
# A variable of Flux; a doubled dollar sign is an escape, which Flux writes as
# one. The same form as the CI test of the plain files.
VARIABLE = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)(:=[^}]*)?\}")


class Undecrypted(Exception):
    """A file that sops did not decrypt; it carries the exit code only."""


def sops_decrypt(path: Path) -> str:
    """The decrypted text of a file, in memory only. The error output of
    sops is dropped, so no part of a file reaches the screen."""
    result = subprocess.run(["sops", "--decrypt", str(path)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise Undecrypted(result.returncode)
    return result.stdout


def names(text: str) -> set[str]:
    """The names of the variables of Flux in a text, with no default."""
    return {m.group(1) for m in VARIABLE.finditer(text) if m.group(2) is None}


def template_keys(children: dict) -> set[str]:
    """The keys of the reduced Secret of a site: the build of its
    Kustomization flux-substitution."""
    (secret,) = [d for d in children[SUBSTITUTION_CHILD].build.docs
                 if d.get("kind") == "Secret" and d["metadata"]["name"] == "cluster-secrets-apps"]
    return set(secret["stringData"])


def entries() -> list[tuple[str, str, set[str]]]:
    """Each encrypted file of a child outside the class, per site, as
    (site, path, the names that the child knows). No file is read here."""
    out = []
    for site in classlist.SITES:
        children = classlist.walk(site)
        keys = template_keys(children)
        (settings,) = classlist.load(classlist.ROOT / f"kubernetes/clusters/{site}/cluster-settings.yaml")
        known = keys | set(settings["data"])
        for child in sorted(children.values(), key=lambda c: c.key):
            if classlist.is_class_path(child.path):
                continue
            inline = set((child.doc["spec"].get("postBuild") or {}).get("substitute") or {})
            out += [(site, path, known | inline) for path in sorted(child.build.encrypted)]
    return out


def report(items: list[tuple[str, str, set[str]]], decrypt: Callable[[Path], str]) -> tuple[list[str], int]:
    """The lines to print and the exit code. A line holds a site, a path and
    names, never a value."""
    lines, code, seen = [], 0, set()
    for site, path, known in items:
        if (site, path) in seen:
            continue
        seen.add((site, path))
        try:
            text = decrypt(classlist.ROOT / path)
        except Undecrypted as e:
            lines.append(f"{site} {path}: did not decrypt (sops exit {e.args[0]})")
            code = 2
            continue
        missing = sorted(names(text) - known)
        del text
        if missing:
            lines.append(f"{site} {path}: {' '.join(missing)}")
            code = max(code, 1)
    return lines, code


def main() -> int:
    items = entries()
    lines, code = report(items, sops_decrypt)
    for line in lines:
        print(line)
    print(f"substitution-names files={len({(s, p) for s, p, _ in items})} findings={len(lines)}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
