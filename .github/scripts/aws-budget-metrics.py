#!/usr/bin/env python3
"""Publish the current AWS Budgets snapshot without logging billing data."""

import json
import os
import subprocess
import time
from decimal import Decimal, InvalidOperation
from urllib.error import URLError
from urllib.request import Request, urlopen


def read_budget():
    credentials = (
        f'user = "{os.environ["AWS_ACCESS_KEY_ID"]}:{os.environ["AWS_SECRET_ACCESS_KEY"]}"\n'
        f'header = "X-Amz-Security-Token: {os.environ["AWS_SESSION_TOKEN"]}"\n'
    )
    # curl performs SigV4 using credentials from stdin, outside the argument list.
    result = subprocess.run(
        [
            "curl", "-q", "--config", "-", "--silent", "--fail", "--max-time", "30",
            "--aws-sigv4", "aws:amz:us-east-1:budgets",
            "--header", "Content-Type: application/x-amz-json-1.1",
            "--header", "X-Amz-Target: AWSBudgetServiceGateway.DescribeBudget",
            "--data-binary", json.dumps({
                "AccountId": os.environ["BUDGET_ACCOUNT_ID"],
                "BudgetName": os.environ["BUDGET_NAME"],
            }),
            "https://budgets.amazonaws.com/",
        ],
        input=credentials,
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode:
        raise SystemExit(f"AWS DescribeBudget failed (curl exit {result.returncode}).")
    return json.loads(result.stdout)["Budget"]


def usd(spend):
    value = Decimal(spend["Amount"])
    if spend["Unit"] != "USD" or not value.is_finite():
        raise ValueError("Expected a finite USD amount.")
    return value


def metrics(budget, collected_at):
    spend = budget["CalculatedSpend"]
    limit = usd(budget["BudgetLimit"])
    if budget["BudgetType"] != "COST" or budget["TimeUnit"] != "MONTHLY" or limit <= 0:
        raise ValueError("Expected a monthly cost budget with a positive limit.")
    values = {
        "limit_usd": limit,
        "actual_usd": usd(spend["ActualSpend"]),
        "collected_timestamp_seconds": collected_at,
    }
    forecast = spend.get("ForecastedSpend")
    if forecast:
        values["forecast_usd"] = usd(forecast)
    return "".join(
        f"# TYPE tnwks_aws_budget_{name} gauge\ntnwks_aws_budget_{name} {value}\n"
        for name, value in values.items()
    )


def main():
    try:
        snapshot = metrics(read_budget(), int(time.time()))
    except (KeyError, ValueError, TypeError, InvalidOperation, OSError):
        raise SystemExit("Could not read a valid AWS budget snapshot.") from None

    url = f'{os.environ["PUSHGATEWAY_URL"]}/metrics/job/aws_budget'
    # PUT replaces the entire group. An unavailable forecast clears the old one.
    request = Request(
        url,
        data=snapshot.encode(),
        method="PUT",
        headers={"Content-Type": "text/plain; version=0.0.4"},
    )
    try:
        with urlopen(request, timeout=30):
            pass
    except (URLError, TimeoutError):
        raise SystemExit("Could not publish the AWS budget snapshot.") from None
    print("Published the AWS budget snapshot.")


if __name__ == "__main__":
    main()
