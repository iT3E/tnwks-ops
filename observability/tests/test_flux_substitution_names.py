"""The names-only check of the encrypted files of the Flux children outside
the class (kubernetes/apps/flux-system/admission/substitution_names.py).

The tests use made-up files only. The round trip with sops runs only when
SUBSTITUTION_NAMES_SOPS_TEST=1 and sops and age-keygen are on the PATH: in a
container with no credentials and a throwaway key. It never reads a file of
the repo.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "kubernetes/apps/flux-system/admission"))

import substitution_names as check

MADE_UP = "made-up-value-7f3a"
TEXT = f"""\
apiVersion: v1
kind: Secret
metadata:
  name: demo
stringData:
  url: https://${{KNOWN_HOST}}/x
  token: "{MADE_UP}"
  missing: ${{MISSING_ONE}}-${{MISSING_TWO}}
  escaped: $${{NOT_A_VARIABLE}}
  defaulted: ${{WITH_DEFAULT:=on}}
"""


def test_names_reads_variables_and_skips_escapes_and_defaults():
    assert check.names(TEXT) == {"KNOWN_HOST", "MISSING_ONE", "MISSING_TWO"}


def test_report_prints_paths_and_names_only():
    items = [("site", "kubernetes/apps/a/app/secret.sops.yaml", {"KNOWN_HOST"}),
             ("site", "kubernetes/apps/b/app/secret.sops.yaml", {"KNOWN_HOST", "MISSING_ONE", "MISSING_TWO"}),
             ("site", "kubernetes/apps/a/app/secret.sops.yaml", {"KNOWN_HOST"})]
    lines, code = check.report(items, lambda path: TEXT)
    assert lines == ["site kubernetes/apps/a/app/secret.sops.yaml: MISSING_ONE MISSING_TWO"]
    assert code == 1
    assert MADE_UP not in "\n".join(lines)


def test_report_is_empty_when_each_name_is_known():
    items = [("site", "x.sops.yaml", {"KNOWN_HOST", "MISSING_ONE", "MISSING_TWO"})]
    assert check.report(items, lambda path: TEXT) == ([], 0)


def test_a_file_that_does_not_decrypt_shows_its_exit_code_only():
    def fail(path):
        raise check.Undecrypted(128)
    lines, code = check.report([("site", "x.sops.yaml", set())], fail)
    assert (lines, code) == (["site x.sops.yaml: did not decrypt (sops exit 128)"], 2)


def test_entries_are_the_encrypted_files_outside_the_class_by_path():
    # The walk names the files and reads none of them.
    items = check.entries()
    assert items
    for site, path, known in items:
        assert ".sops." in path, path
        assert not check.classlist.is_class_path(str(Path(path).parent)), path
        assert "SECRET_DOMAIN" in known


def test_each_site_knows_the_keys_of_its_own_template():
    # Each site has a template of its own (docs/operations/flux-admission.md,
    # "Substitution"): a key of one template is no key of the other site.
    keys = {site: check.template_keys(check.classlist.walk(site)) for site in check.classlist.SITES}
    assert keys["wsl-02"] < keys["wsl"]
    for site, path, known in check.entries():
        others = set().union(*(k for s, k in keys.items() if s != site)) - keys[site]
        assert keys[site] <= known and not (others & known - set(check.classlist.load(
            check.classlist.ROOT / f"kubernetes/clusters/{site}/cluster-settings.yaml")[0]["data"])), (site, path)


@pytest.mark.skipif(os.environ.get("SUBSTITUTION_NAMES_SOPS_TEST") != "1"
                    or not (shutil.which("sops") and shutil.which("age-keygen")),
                    reason="a container with sops, age-keygen and SUBSTITUTION_NAMES_SOPS_TEST=1")
def test_the_round_trip_with_sops_prints_no_value(tmp_path, capsys, monkeypatch):
    # In the folder of the test, so that sops reads no .sops.yaml of the repo.
    monkeypatch.chdir(tmp_path)
    key = tmp_path / "key.txt"
    subprocess.run(["age-keygen", "-o", str(key)], check=True, capture_output=True)
    recipient = next(line.split(": ")[1] for line in key.read_text().splitlines() if "public key" in line)
    plain = tmp_path / "secret.sops.yaml"
    plain.write_text(TEXT)
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(key))
    encrypted = subprocess.run(["sops", "--encrypt", "--age", recipient, "--encrypted-regex", "^(stringData)$",
                                str(plain)], check=True, capture_output=True, text=True).stdout
    assert MADE_UP not in encrypted
    assert "ENC[" in encrypted
    plain.write_text(encrypted)
    lines, code = check.report([("site", str(plain), {"KNOWN_HOST"})], check.sops_decrypt)
    assert (lines, code) == ([f"site {plain}: MISSING_ONE MISSING_TWO"], 1)
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(tmp_path / "none.txt"))
    failed, code = check.report([("site", str(plain), set())], check.sops_decrypt)
    assert code == 2 and failed[0].startswith(f"site {plain}: did not decrypt (sops exit ")
    out = capsys.readouterr()
    assert MADE_UP not in out.out + out.err + "\n".join(lines + failed)
