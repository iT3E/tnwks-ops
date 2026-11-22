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


# Each form of the table of the CI test (test_flux_admission.py,
# test_the_forms_of_a_variable), the lab of the envsubst of Flux in strict mode.
STOPS = ["${X}", "${X/./-}", "${X//./-}", "${X/#a/b}", "${X/%a/b}", "${X:1}", "${X:1:2}", "${#X}",
         "${X^}", "${X^^}", "${X,}", "${X,,}", "${X#a}", "${X##a}", "${X%a}", "${X%%a}", "$$${X}"]
PASSES = ["${X:=d}", "${X:-d}", "${X=d}", "${X:+d}", "${X:?d}", "${X:-$${Y}}", "$${X}", "$$$${X}", "$X"]
UNREADABLE = ["${X-d}", "${X+d}", "${X?d}", "${}", "${ X}", "${X"]


def value(text):
    """A made-up Secret with one value, in a block string, so that each form
    reaches the reader as it stands."""
    return "kind: Secret\nstringData:\n  v: |\n    " + text + "\n"


def test_names_reads_each_form_as_the_ci_test_does():
    for text in STOPS:
        assert check.names(value(text)) == {"X"}, text
    for text in PASSES:
        assert check.names(value(text)) == set(), text
    assert check.names(value("${X:-${Y}}")) == {"Y"}
    for text in UNREADABLE:
        assert check.names(value(text)) == {"<a form that Flux can not read>"}, text


def test_names_skips_comments_and_objects_that_turn_substitution_off():
    text = ("kind: Secret\n# ${IN_A_COMMENT}\nstringData:\n  a: ${KEPT}  # ${AFTER_A_VALUE}\n"
            "  b: |\n    # ${IN_A_BLOCK}\n---\nkind: Secret\nmetadata:\n  annotations:\n"
            "    kustomize.toolkit.fluxcd.io/substitute: disabled\nstringData:\n  c: ${OFF}\n")
    assert check.names(text) == {"KEPT", "IN_A_BLOCK"}


def test_a_text_that_is_not_yaml_shows_its_path_only():
    text = "stringData: [" + MADE_UP
    lines, code = check.report([("site", "x.sops.yaml", set())], lambda path: text)
    assert (lines, code) == (["site x.sops.yaml: did not parse as YAML"], 2)


def test_a_text_nested_too_deep_shows_its_path_only():
    # The parser stops with a RecursionError, not a YAMLError.
    text = "stringData: {a: " + "[" * 5000 + MADE_UP + "]" * 5000 + "}"
    with pytest.raises(check.Unparsed):
        check.names(text)
    lines, code = check.report([("site", "x.sops.yaml", set())], lambda path: text)
    assert (lines, code) == (["site x.sops.yaml: did not parse as YAML"], 2)


def test_the_script_and_the_ci_test_have_one_reader():
    # Both call classlist.flux_variables; neither has a pattern of its own.
    source = (ROOT / "kubernetes/apps/flux-system/admission/substitution_names.py").read_text()
    assert "re.compile" not in source and "classlist.flux_variables(" in source
    assert "classlist.flux_text(" in source and "classlist.substitution_off(" in source
    test = (ROOT / "observability/tests/test_flux_admission.py").read_text()
    assert "FLUX_VARIABLE" not in test and "def flux_variables" not in test
    assert "def text_of" not in test and "def substitution_off" not in test


def test_an_unreadable_form_shows_no_text_of_the_file():
    # A value that holds "${" with no closing brace stops the build too; the
    # line names the path and a fixed marker, and no part of the value.
    text = "kind: Secret\nstringData:\n  token: \"" + MADE_UP + "${abc" + MADE_UP + "\"\n"
    lines, code = check.report([("site", "x.sops.yaml", set())], lambda path: text)
    assert (lines, code) == (["site x.sops.yaml: <a form that Flux can not read>"], 1)
    assert "abc" not in lines[0] and MADE_UP not in lines[0]


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
