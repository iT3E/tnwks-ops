"""A strict reader of the .prom files of the host check, for the tests.

windows_exporter reads each .prom file with the text parser of Prometheus
(expfmt, UTF-8 names), and serves nothing of a file that does not parse. It also
refuses a time on a sample, and a series that two files both have. This reader
fails on each of those, so a test of a file that it reads stands for the exporter.
"""

import math
import re

NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\\n]|\\[\\"n])*)"')
SAMPLE = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)\{(.*)\} (\S+)")


def parse(text: str) -> dict:
    """{family: {"help": str, "type": str, "samples": {frozenset(labels): float}}}.

    Each family has one HELP and one TYPE line, before its samples, and its lines
    are together. A sample has labels in braces, a value, and no time."""
    assert text.endswith("\n"), "the file ends with a line feed"
    assert "\r" not in text and not text.startswith("﻿"), "LF only, and no byte order mark"
    families: dict = {}
    current = None
    for number, line in enumerate(text.splitlines(), 1):
        where = f"line {number}: {line!r}"
        if line.startswith("# HELP "):
            name, _, help_text = line[len("# HELP "):].partition(" ")
            assert NAME.fullmatch(name) and name not in families, where
            assert help_text.strip(), f"{where}: a HELP with no text"
            families[name] = {"help": help_text, "type": None, "samples": {}}
            current = name
        elif line.startswith("# TYPE "):
            name, _, kind = line[len("# TYPE "):].partition(" ")
            assert name == current and families[name]["type"] is None and not families[name]["samples"], where
            assert kind == "gauge", where
            families[name]["type"] = kind
        else:
            match = SAMPLE.fullmatch(line)
            assert match, f"{where}: not a sample with labels and a value, and no time"
            name, labels_text, value = match.groups()
            assert name == current and families[name]["type"], f"{where}: a sample out of its family"
            labels = {}
            rest = labels_text
            while rest:
                pair = LABEL.match(rest)
                assert pair, where
                assert pair[1] not in labels, where
                labels[pair[1]] = pair[2]
                rest = rest[pair.end():]
                if rest:
                    assert rest.startswith(","), where
                    rest = rest[1:]
            key = frozenset(labels.items())
            assert key not in families[name]["samples"], f"{where}: the same series twice"
            number_value = float(value)
            assert not math.isnan(number_value), where
            families[name]["samples"][key] = number_value
    return families


def series(families: dict, name: str) -> dict:
    """The samples of one family, as {tuple(sorted(labels)): value}."""
    if name not in families:
        return {}
    return {tuple(sorted(key)): value for key, value in families[name]["samples"].items()}


def value(families: dict, name: str, **labels) -> float | None:
    """The value of the one sample of a family with these labels, or None."""
    wanted = tuple(sorted(labels.items()))
    return series(families, name).get(wanted)


def relayed(document: dict) -> dict:
    """The families that the relay of the Windows host check writes for a document of a
    WSL distro that fits (role windows_host_check, -Relay): a model of the relay for the
    tests of the distro. security/tests/test_host_check_windows.py holds the relay itself
    to it. Each family has samples only."""
    system = document["system"]
    families: dict = {}

    def add(name: str, labels: dict, number: float) -> None:
        samples = families.setdefault(name, {"help": None, "type": "gauge", "samples": {}})["samples"]
        key = frozenset({"system": system, **labels}.items())
        assert key not in samples, f"the same series twice: {name} {labels}"
        samples[key] = float(number)

    if not document["failed"]:
        for check, number in document["results"].items():
            add("homelab_security_check_result",
                {"source": "host-check", "check": check, "severity": "high"}, number)
        add("homelab_security_last_result_timestamp_seconds", {"source": "host-check"}, document["time"])
    add("homelab_security_host_check_failed_queries", {}, len(document["failed"]))
    for detail in document["details"]:
        add(detail["name"], detail["labels"], detail["value"])
    return families
