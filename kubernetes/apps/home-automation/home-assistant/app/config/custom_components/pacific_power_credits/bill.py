"""Extract the recorded credit balance from a Pacific Power bill.

Never retain PDF text, customer names, addresses or account numbers. A missing
credit note is unknown, not zero. Expiry is a billing month, not an exact date.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO

from pypdf import PdfReader

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 20

_CREDIT = re.compile(
    r"You\s+have\s+excess\s+power\s+of\s+"
    r"(?P<kwh>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s+kwh\s+"
    r"available\s+for\s+future\s+use\s+until\s+your\s+billing\s+in\s+"
    r"(?P<month>\d{1,2})\s*/\s*(?P<year>\d{4})\b",
    re.IGNORECASE,
)
_BILL_DATE = re.compile(
    r"BILLING\s+DATE\s*:\s*(\w+\s+\d{1,2},?\s+\d{4})", re.IGNORECASE
)
_ACCOUNT = re.compile(r"ACCOUNT\s+NUMBER\s*:\s*(\d+\s*-\s*\d+)", re.IGNORECASE)


class InvalidBill(ValueError):
    """The bill could not supply an unambiguous credit balance."""


@dataclass(frozen=True)
class CreditBalance:
    """Only the non-identifying values needed by Home Assistant."""

    kwh: float
    statement_date: str
    expiry_month: str
    account_hash: str

    def as_dict(self) -> dict:
        """Return the snapshot persisted on the private HA volume."""
        return asdict(self)


def parse_bill_text(text: str) -> CreditBalance:
    """Parse a bill conservatively; never infer a missing balance."""
    if not re.search(r"pacific\s*power", text, re.IGNORECASE):
        raise InvalidBill("not_pacific_power")
    credits = {
        (Decimal(m["kwh"].replace(",", "")), int(m["year"]), int(m["month"]))
        for m in _CREDIT.finditer(text)
    }
    if len(credits) != 1:
        raise InvalidBill("missing_or_ambiguous_credit")
    accounts = {re.sub(r"\s", "", m[1]) for m in _ACCOUNT.finditer(text)}
    if len(accounts) != 1:
        raise InvalidBill("missing_or_ambiguous_account")
    dates: set[date] = set()
    for match in _BILL_DATE.finditer(text):
        value = re.sub(r"\s+", " ", match[1].replace(",", "")).strip()
        for fmt in ("%b %d %Y", "%B %d %Y"):
            try:
                dates.add(datetime.strptime(value, fmt).date())
                break
            except ValueError:
                continue
        else:
            raise InvalidBill("invalid_statement_date")
    if len(dates) != 1:
        raise InvalidBill("missing_or_ambiguous_date")
    kwh, year, month = credits.pop()
    try:
        expiry = date(year, month, 1)
    except ValueError as err:
        raise InvalidBill("invalid_expiry_month") from err
    statement = dates.pop()
    if (expiry.year, expiry.month) < (statement.year, statement.month):
        raise InvalidBill("expiry_before_statement")
    return CreditBalance(
        kwh=float(kwh),
        statement_date=statement.isoformat(),
        expiry_month=expiry.strftime("%Y-%m"),
        account_hash=sha256(accounts.pop().encode()).hexdigest(),
    )


def parse_bill_pdf(content: bytes) -> CreditBalance:
    """Extract text on an executor; the PDF is discarded after parsing."""
    if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF-"):
        raise InvalidBill("invalid_pdf")
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
            raise InvalidBill("invalid_pdf")
        return parse_bill_text(
            "\n".join(page.extract_text() or "" for page in reader.pages)
        )
    except InvalidBill:
        raise
    except Exception as err:
        # Parser exceptions can contain document text. Only expose this fixed code.
        raise InvalidBill("invalid_pdf") from err


def validate_replacement(previous: CreditBalance, current: CreditBalance) -> None:
    """Prevent an unrelated or older bill from replacing a recorded balance."""
    if current.account_hash != previous.account_hash:
        raise InvalidBill("wrong_account")
    if current.statement_date < previous.statement_date:
        raise InvalidBill("older_bill")
