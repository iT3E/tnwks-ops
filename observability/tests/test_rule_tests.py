"""rule_tests.py splits a promtool test file into shards and loses no test.

CI runs the generated rule tests through it (the `rule-tests` job of
.github/workflows/observability.yaml). A test that falls out of every shard
would never run, so each test of each generated file must be in exactly one
shard, with its text unchanged.
"""

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "observability"))
import rule_tests

GENERATED = sorted([*(ROOT / "observability/generated").glob("rule-tests*.yaml"),
                    *(ROOT / "security/generated").glob("rule-tests*.yaml")])


def test_the_generated_files_are_found():
    names = {str(path.relative_to(ROOT)) for path in GENERATED}
    assert {"observability/generated/rule-tests.yaml", "security/generated/rule-tests.yaml",
            "security/generated/rule-tests-every-source.yaml"} <= names


@pytest.mark.parametrize("duration, expected", [
    ("36h30m", 131400), ("1d", 86400), ("90s", 90), ("2w", 1209600), ("500ms", 0.5),
    ("0", 0), ("", 0), (None, 0),
])
def test_seconds_reads_a_prometheus_duration(duration, expected):
    assert rule_tests.seconds(duration) == expected


def test_cost_is_the_last_eval_time_of_a_test():
    test = {"alert_rule_test": [{"eval_time": "10m"}, {"eval_time": "2h"}],
            "promql_expr_test": [{"eval_time": "1h"}]}
    assert rule_tests.cost(test) == 7200 + 60
    assert rule_tests.cost({"input_series": []}) == 60
    assert rule_tests.cost({"alert_rule_test": None}) == 60


@pytest.mark.parametrize("path", GENERATED, ids=lambda path: str(path.relative_to(ROOT)))
@pytest.mark.parametrize("count", [1, 2, 4, 16])
def test_each_test_is_in_exactly_one_shard(path, count, tmp_path):
    doc = yaml.safe_load(path.read_text())
    written = rule_tests.split(path, 3, count, tmp_path)
    assert 1 <= len(written) <= count
    seen = []
    for _, name, _, tests in written:
        shard = yaml.safe_load((tmp_path / name).read_text())
        assert len(shard["tests"]) == tests > 0
        # promtool finds the rule files at the place the container mounts them.
        assert shard["rule_files"] == [f"/rules/3/{rule}" for rule in doc["rule_files"]]
        assert {k: v for k, v in shard.items() if k not in ("tests", "rule_files")} == \
               {k: v for k, v in doc.items() if k not in ("tests", "rule_files")}
        seen += shard["tests"]
    assert len(seen) == len(doc["tests"])
    remaining = list(doc["tests"])
    for test in seen:
        remaining.remove(test)  # raises if a shard holds a test the file does not
    assert remaining == []


def test_shards_have_about_equal_cost():
    tests = [{"name": str(n), "alert_rule_test": [{"eval_time": f"{n}h"}]} for n in range(1, 41)]
    parts = rule_tests.shards(tests, 4)
    loads = [sum(map(rule_tests.cost, part)) for part in parts]
    assert len(parts) == 4
    # Longest first into the lightest shard: no two shards differ by more than one test.
    assert max(loads) - min(loads) <= max(map(rule_tests.cost, tests))


def test_no_more_shards_than_tests():
    tests = [{"name": "a"}, {"name": "b"}]
    assert [len(part) for part in rule_tests.shards(tests, 8)] == [1, 1]
    assert rule_tests.shards([], 8) == []


def test_a_rule_file_outside_the_directory_is_refused(tmp_path):
    path = tmp_path / "rule-tests.yaml"
    path.write_text(yaml.safe_dump({"rule_files": ["../rules.yaml"], "tests": [{"name": "a"}]}))
    with pytest.raises(SystemExit):
        rule_tests.split(path, 0, 2, tmp_path)
