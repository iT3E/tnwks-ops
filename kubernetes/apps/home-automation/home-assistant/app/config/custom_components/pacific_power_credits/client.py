"""Read the latest statement using the portal's own billing endpoints."""

from __future__ import annotations

import asyncio
import base64
import binascii
from datetime import datetime

from .bill import MAX_PDF_BYTES, CreditBalance, InvalidBill, parse_bill_pdf
from .portal import AccountInfo, PacificPowerApi, PacificPowerApiError


class CreditClient(PacificPowerApi):
    """Add read-only statement retrieval to the upstream authentication client."""

    async def async_latest_balance(
        self, account: AccountInfo, previous: CreditBalance | None = None
    ) -> CreditBalance:
        """Download only when a new monthly statement is available."""
        response = await self._api_call(
            "/api/account/getAccountInfo",
            {
                "getAccountInfoRequestBody": {
                    "accountRequest": {
                        "customerIDN": account.customer_idn,
                        "accountSequence": account.account_sequence,
                    }
                }
            },
        )
        account_data = response.get("getAccountInfoResponseBody", {}).get(
            "accountInfo", {}
        )
        value = account_data.get("currentStatementDate")
        try:
            statement_date = datetime.fromisoformat(value).date().isoformat()
        except (TypeError, ValueError) as err:
            raise PacificPowerApiError("Latest statement date unavailable") from err
        if previous and statement_date == previous.statement_date:
            return previous
        response = await self._api_call(
            "/api/account-additional/getStatementPDF",
            {
                "getStatementPDFRequestBody": {
                    "webUserID": self._web_user_id,
                    "custAccountID": account.customer_idn,
                    "custAccountSeq": account.account_sequence,
                    "statementDate": value,
                }
            },
        )
        encoded = response.get("getStatementPDFResponseBody", {}).get("PDFContent")
        if not isinstance(encoded, str) or len(encoded) > 4 * (
            (MAX_PDF_BYTES + 2) // 3
        ):
            raise InvalidBill("invalid_pdf")
        try:
            pdf = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as err:
            raise InvalidBill("invalid_pdf") from err
        balance = await asyncio.to_thread(parse_bill_pdf, pdf)
        if balance.statement_date != statement_date:
            raise InvalidBill("statement_date_mismatch")
        return balance
