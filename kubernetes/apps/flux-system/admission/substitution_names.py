"""The names-only check of the encrypted files of the Flux children outside
the class, before they substitute from the reduced Secret
(docs/operations/flux-admission.md, "Substitution").

    python3 kubernetes/apps/flux-system/admission/substitution_names.py

The owner runs it by hand, with the key that decrypts the files (sops on the
PATH, as bin/sops gives it in the repo). For each site, it decrypts each
encrypted file of the build of each child outside the class into the memory
of this process, finds each variable of Flux that needs a definition, and
prints only the path of the file and the names that are neither keys of the
reduced Secret
cluster-secrets-apps of the site (the template of its Kustomization
flux-substitution) nor settings of the site nor inline substitutes of the
child. It reads each object and each form of a variable as the CI test of
the plain files does, with the same functions of classlist.py: each object
after a round trip as the controller makes it (no YAML comment), no object
that turns substitution off, ${NAME} and each form that reads the value need
a definition, a form with a default does not, and an escape is no variable.
A form that Flux can not read stops the build too: it prints as
<a form that Flux can not read>, with no text of the file. A decrypted text
that is not YAML prints its path only. It prints, writes and logs no value:
the decrypted text stays in a variable of this process, and the error output
of sops and of the parser is dropped.

Flux substitutes in strict mode (the default of kustomize-controller since
v1.9): a variable with no key and no default stops the build of its child.
So each name that it prints would stop the apply of that child on that site
at the switch. An empty output (exit 0) means the switch stops no build.
Exit 1: names to add to the reduced Secret of the site.
Exit 2: a file did not decrypt (its path and the exit code of sops only),
or its text is not YAML (its path only).

Needs PyYAML, as classlist.py does.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classlist

# The Kustomization of the reduced Secret; each site has a template of its own.
SUBSTITUTION_CHILD = "flux-system/flux-substitution"


class Unparsed(Exception):
    """A decrypted text that is not YAML; it carries nothing of the text."""


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
    """The names of the variables of Flux in the objects of a decrypted file
    that need a definition, read as the CI test reads them. A form that Flux
    can not read is one fixed marker: the text around it may be a value, so
    none of it shows."""
    try:
        docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
    except yaml.YAMLError:
        # The error of the parser quotes the text, so it is dropped.
        raise Unparsed() from None
    found = (name for doc in docs if not classlist.substitution_off(doc)
             for name in classlist.flux_variables(classlist.flux_text(doc)))
    return {classlist.UNREADABLE + ">" if name.startswith(classlist.UNREADABLE) else name for name in found}


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
        try:
            missing = sorted(names(text) - known)
        except Unparsed:
            lines.append(f"{site} {path}: did not parse as YAML")
            code = 2
            continue
        finally:
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
