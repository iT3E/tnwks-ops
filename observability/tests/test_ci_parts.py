"""The `tests` job of .github/workflows/observability.yaml runs each test file of this folder.

The parts of its matrix are runners. A part names its files, and the part
`others` is the folder with those files left out. A file that no part runs
would never run in CI, so: each file that a part names is here, no two parts
name one file, and `others` leaves out those files and no other.
"""

import shlex
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TESTS = "observability/tests"
JOB = yaml.safe_load((ROOT / ".github/workflows/observability.yaml").read_text())["jobs"]["tests"]


def parts() -> dict:
    entries = JOB["strategy"]["matrix"]["include"]
    assert len({e["part"] for e in entries}) == len(entries)
    return {e["part"]: shlex.split(e["select"]) for e in entries}


def test_each_test_file_is_in_exactly_one_part():
    named = [arg for part, args in parts().items() if part != "others" for arg in args]
    assert named
    assert len(named) == len(set(named)), named
    here = {f"{TESTS}/{path.name}" for path in (ROOT / TESTS).glob("test_*.py")}
    # A name of a file of this folder, and no option that takes tests out.
    assert set(named) <= here, sorted(set(named) - here)
    others = parts()["others"]
    assert others[0] == TESTS
    assert sorted(others[1:]) == sorted(f"--ignore={name}" for name in named)


def test_the_step_runs_what_its_part_selects_and_nothing_less():
    (step,) = [s for s in JOB["steps"] if "pytest" in s.get("run", "")]
    assert step["env"] == {"SELECT": "${{ matrix.select }}"}
    (line,) = [text for text in step["run"].splitlines() if "pytest" in text]
    args = shlex.split(line)
    args = args[args.index("pytest") + 1:]
    assert args[0] == "${select[@]}"
    # No mark, no name filter and no left out test on the command line.
    assert not [a for a in args[1:] if a.startswith(("-k", "-m", "--deselect", "--ignore"))], args
    # A file stays on one worker: the tests of a file share its containers.
    assert args[args.index("--dist") + 1] == "loadfile"
    # One failed part does not stop the others.
    assert JOB["strategy"]["fail-fast"] is False
