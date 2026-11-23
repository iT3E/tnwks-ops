"""Consolidated AWS spend and the monthly budget that triggers the AWS action."""

from __future__ import annotations

from ..model import Board, p
from ..queries import metric


def budget_metric(name: str) -> str:
    return metric(f"tnwks_aws_budget_{name}", 'exported_job="aws_budget"')


COLLECTED = budget_metric("collected_timestamp_seconds")
FRESH = f"((time() - {COLLECTED} < 50400) and (month({COLLECTED}) == scalar(month())))"
ACTUAL = f"({budget_metric('actual_usd')} and {FRESH})"
FORECAST = f"({budget_metric('forecast_usd')} and {FRESH})"
LIMIT = f"({budget_metric('limit_usd')} and {FRESH})"


def board() -> Board:
    return Board(
        "tnwks-cloud-cost", "Cloud Cost", "cloud-cost", "l2",
        "How much has the AWS organization spent this month, and how close is it to the budget?",
        {
            "AWS": [
                p("Month-to-date spend", ACTUAL, "currencyUSD",
                  "Consolidated cost recorded by AWS Budgets for the current UTC calendar month.", "stat"),
                p("Monthly budget", LIMIT, "currencyUSD",
                  "The limit read from the AWS budget. Actual spend at the threshold triggers the provisioning restriction.",
                  "stat"),
                p("Budget used", f"{ACTUAL} / {LIMIT}", "percentunit",
                  "Recorded month-to-date spend as a share of the monthly budget.", "stat"),
                p("Budget remaining", f"{LIMIT} - {ACTUAL}", "currencyUSD",
                  "Budget less recorded spend. A negative amount means the budget has been exceeded.", "stat"),
                p("Projected month-end spend", FORECAST, "currencyUSD",
                  "AWS Budgets' month-end forecast. AWS may need more history before publishing it.",
                  "stat", empty="Forecast unavailable"),
                p("Last successful collection", f"{COLLECTED} * 1000", "dateTimeAsIso",
                  "The runner reads AWS every four hours. Current cost panels require a snapshot from this UTC month "
                  "less than 14 hours old. AWS billing figures can lag behind that read.", "stat"),
                p("Month-to-date spend over time", ACTUAL, "currencyUSD",
                  "Recorded spend at each observation. The total resets each UTC month. "
                  "Gaps indicate missing or stale snapshots."),
                p("Month-end forecast over time", FORECAST, "currencyUSD",
                  "AWS's projected full-month spend at each observation.",
                  empty="Forecast unavailable"),
            ],
        },
        "Observability", sections=True, time_from="now-30d",
    )
