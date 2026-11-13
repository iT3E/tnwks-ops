"""Security (L2): Which safeguards have a result, and which of them pass?

The board input (security/generated/board.json, from the control registry) gives the
series, the values of state and the cycle of each source. The lines of the board are
series of the generated rules: a new safeguard or system needs no change here.
"""

from __future__ import annotations

import json

from ..design import NO_ALERTS
from ..model import Board, p
from ..queries import ALERT_SCOPE, ROOT, selector


def board() -> Board:
    data = json.loads((ROOT / "security/generated/board.json").read_text())
    series, values = data["series"], data["states"]
    # The words of a control: "No evidence", "Pass", "Fail".
    states = tuple((str(value), word.capitalize()) for word, value in values.items())
    safeguard = f'max by (safeguard) ({selector(series["safeguard"])})'
    has_result = f'max by (safeguard) ({selector(series["has_result"])})'
    control = selector(series["control"])
    accepted = f'max by (source, check, system) ({selector(series["accepted"])})'
    limits = "; ".join(
        f'{source["title"]} {source["late_after"]}'
        + "".join(f', {check} {times["late_after"]}' for check, times in source.get("checks", {}).items())
        + "".join(f', {kind} {times["late_after"]}' for kind, times in source.get("by_type", {}).items())
        for source in data["sources"])
    return Board(data["board"], "Security", "security", "l2",
              "Which safeguards have a result, and which of them pass?", {
            "Safeguards": [
                p("Safeguards in the registry", f"count({safeguard})", "short",
                  f'The lines of the control registry: {len(data["safeguards"])} safeguards of CIS Controls v8.1. '
                  "A count below that means that the rules of the registry do not run.", "stat"),
                p("With a result", f"sum({has_result})", "short",
                  "Safeguards for which one system or more has a fresh result, a pass or a fail. "
                  "For the others no check exists, or its source is late.", "stat"),
                p("Passing", f'sum({safeguard} == bool {values["pass"]})', "short",
                  "Safeguards for which each system that has a check passes. A safeguard with a result "
                  "on one system and none on a second system is not in this count.", "stat"),
                p("Failing", f'sum({safeguard} == bool {values["fail"]})', "failures",
                  "Safeguards for which one check or more fails on a system. The Systems tab says where.", "stat"),
                p("State of each safeguard",
                  f'max by (line, safeguard, title, function) ({selector(series["safeguard"])})', "state",
                  "One line for each safeguard: Fail when a check fails on one system or more, Pass when each "
                  "system that has a check for it passes, and No evidence when one of those systems has no "
                  "fresh result. A result counts only while its source is inside its limit. "
                  "A line never shows Pass for a check that did not run.",
                  "table", states=states, order="line"),
            ],
            "Systems": [
                p("Failing safeguards by system",
                  f'max by (system, system_type, safeguard) ({control} == {values["fail"]})', "state",
                  "Each safeguard that fails, on the system where it fails. The security stream `findings` in Loki "
                  "lists each failed finding.", "table", empty=NO_ALERTS, states=states),
                p("Safeguards with a result",
                  f'count by (system, system_type) ({control} > {values["no evidence"]})'
                  f' or on (system, system_type) (0 * count by (system, system_type) ({control}))', "short",
                  "For each system of the registry, the number of safeguards with a fresh result. "
                  "A system at 0 has no source that reports.", "table"),
                p("Systems in the registry",
                  f'max by (system, system_type, site, domain) ({selector(series["system"])})', "short",
                  "Each system that the platform checks, from security/registry/systems.yaml. "
                  "A new system is one line in that file.", "table", column=None),
            ],
            "Sources": [
                p("Age of the last result",
                  f'time() - max by (source, system) ({selector(series["last_result"])})', "s",
                  "Time since the newest result of each source, for each system. A source is late after: "
                  f"{limits}. These limits are in security/registry/sources.yaml.",
                  "table", empty="Unknown: no source reports", sort="desc"),
                p("Late sources",
                  f'max by (source, system) (ALERTS{{alertname="SecurityResultLate",alertstate="firing",{ALERT_SCOPE}}})',
                  "short",
                  "Each source whose newest result is older than its limit, or that serves no time. "
                  "The safeguards that rest on it show No evidence until it reports again.",
                  "table", empty=NO_ALERTS, column=None),
                p("Failing checks",
                  f'count by (source, system) ({selector(series["result"])} > 0)', "short",
                  "The number of checks of each source that fail on each system.", "table", empty=NO_ALERTS),
            ],
            "Accepted findings": [
                p("Accepted findings", f"count({accepted})", "short",
                  "The lines of the encrypted list of accepted findings. The owner writes each line, "
                  "with a reason and an end date. No value means that the list has no line, or that "
                  "nothing serves it.", "stat"),
                p("Past their end date", f"sum({accepted} < bool time())", "failures",
                  "Accepted findings whose end date has passed. The owner renews each one or removes it.", "stat"),
                p("Accepted findings by system", f"count by (system) ({accepted})", "short",
                  "The number of accepted findings of each system.", "table", empty=NO_ALERTS),
            ],
        }, "Security", sections=True)
