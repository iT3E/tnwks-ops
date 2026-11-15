"""Tests for .github/scripts/sops-mask.py, against a stub sops.

    python3 -m unittest discover -s .github/scripts -p 'test_sops_mask.py' -v
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("sops_mask", HERE / "sops-mask.py")
sops_mask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sops_mask)

# The stub prints <file>.json for `--decrypt --output-type json <file>`, or
# fails like sops (stderr naming a value) when <file>.fail exists.
STUB = textwrap.dedent(f"""\
    #!{sys.executable}
    import sys
    from pathlib import Path
    args = sys.argv[1:]
    assert args[:3] == ["--decrypt", "--output-type", "json"], args
    path = Path(args[3])
    if Path(str(path) + ".fail").exists():
        sys.stderr.write("Failed to decrypt: stub-secret-in-stderr\\n")
        sys.exit(128)
    sys.stdout.write(Path(str(path) + ".json").read_text())
""")


class SopsMask(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.sops = self.dir / "sops"
        self.sops.write_text(STUB)
        self.sops.chmod(self.sops.stat().st_mode | stat.S_IXUSR)
        self.cwd = os.getcwd()
        os.chdir(self.dir)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def file(self, name, data=None, fail=False):
        Path(name).write_text("sops: {}\n")
        if data is not None:
            Path(name + ".json").write_text(json.dumps(data))
        if fail:
            Path(name + ".fail").write_text("")
        return name

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = sops_mask.main(["--sops", str(self.sops), *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_every_string_value_one_line_each(self):
        f = self.file("a.sops.yaml", {
            "private1.name": "alphabet",
            "private1.description": "first line\n  second line  \n\n",
            "nested": {"list": ["betamax", {"deep": "gamma-ray"}], "n": 3, "b": True, "null": None},
            "empty": "",
        })
        rc, out, err = self.run_main(f)
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        self.assertEqual(out.splitlines(), [
            "::add-mask::alphabet",
            "::add-mask::first line",
            "::add-mask::second line",
            "::add-mask::betamax",
            "::add-mask::gamma-ray",
        ])

    def test_percent_is_escaped_for_the_runner(self):
        f = self.file("a.sops.yaml", {"k": "50%0A off"})
        rc, out, _ = self.run_main(f)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "::add-mask::50%250A off\n")

    def test_json_list_and_duplicates(self):
        a = self.file("a.sops.yaml", {"k": "the-same", "j": "first-one"})
        b = self.file("b.sops.yaml", {"k": "the-same", "j": "second-one"})
        rc, out, _ = self.run_main("--json", json.dumps([a, b]))
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), ["::add-mask::the-same", "::add-mask::first-one", "::add-mask::second-one"])

    def test_shortest_masked_length(self):
        f = self.file("a.sops.yaml", {"k": "x" * sops_mask.MIN_LENGTH})
        rc, out, _ = self.run_main(f)
        self.assertEqual(rc, 0)
        self.assertEqual(out, f"::add-mask::{'x' * sops_mask.MIN_LENGTH}\n")

    def test_short_value_fails_closed_naming_the_key_path(self):
        # The runner would mask it inside every longer word that contains it.
        short = "q" * (sops_mask.MIN_LENGTH - 1)
        a = self.file("a.sops.yaml", {"k": "long enough"})
        b = self.file("b.sops.yaml", {"env": {"vars": {"ACCOUNT": short}}, "ok": "long enough too"})
        rc, out, err = self.run_main(a, b)
        self.assertFailsClosed(rc, out, err, "b.sops.yaml", "env.vars.ACCOUNT", "shorter than")
        self.assertNotIn(short, err)

    def test_short_line_of_a_longer_value_fails(self):
        # Each line is its own mask, so each line must be long enough.
        f = self.file("a.sops.yaml", {"desc": "a long first line\n ab \n", "list": ["fine value"]})
        self.assertFailsClosed(*self.run_main(f), "desc", "shorter than")

    def test_short_value_in_a_list_names_its_index(self):
        f = self.file("a.sops.yaml", {"list": ["fine value", "abc"]})
        rc, out, err = self.run_main(f)
        self.assertFailsClosed(rc, out, err, "list[1]")
        self.assertNotIn("abc", err)

    def assertFailsClosed(self, rc, out, err, *expect):
        self.assertEqual(rc, 1)
        self.assertEqual(out, "", "nothing reaches stdout on failure")
        self.assertTrue(err.startswith("::error::sops-mask: "), err)
        for text in expect:
            self.assertIn(text, err)
        self.assertNotIn("stub-secret", err)

    def test_decrypt_failure_never_echoes_sops_output(self):
        a = self.file("a.sops.yaml", {"k": "fine value"})
        b = self.file("b.sops.yaml", fail=True)
        self.assertFailsClosed(*self.run_main(a, b), "b.sops.yaml", "exit 128")

    def test_missing_sops(self):
        f = self.file("a.sops.yaml", {"k": "a value"})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = sops_mask.main(["--sops", str(self.dir / "nope"), f])
        self.assertFailsClosed(rc, out.getvalue(), err.getvalue(), "not runnable")

    def test_missing_file(self):
        self.assertFailsClosed(*self.run_main("gone.sops.yaml"), "no such file")

    def test_not_a_sops_file(self):
        Path("plain.yaml").write_text("k: v\n")
        self.assertFailsClosed(*self.run_main("plain.yaml"), "not a *.sops.yaml")

    def test_no_values_fails(self):
        f = self.file("a.sops.yaml", {"k": "", "n": 1, "l": [" ", "\n"]})
        self.assertFailsClosed(*self.run_main(f), "no values")

    def test_no_files_fails(self):
        self.assertFailsClosed(*self.run_main("--json", "[]"), "no files")

    def test_bad_json(self):
        self.assertFailsClosed(*self.run_main("--json", "{"), "not JSON")
        self.assertFailsClosed(*self.run_main("--json", '{"a": 1}'), "not an array")

    def test_not_json_from_sops(self):
        f = self.file("a.sops.yaml")
        Path(f + ".json").write_text("not json")
        self.assertFailsClosed(*self.run_main(f), "not JSON")


if __name__ == "__main__":
    unittest.main()
