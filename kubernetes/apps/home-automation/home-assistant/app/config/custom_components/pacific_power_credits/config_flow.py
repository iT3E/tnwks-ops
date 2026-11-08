"""Standard HA setup for automatic statements or private PDF imports."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import selector

from .bill import (
    MAX_PDF_BYTES,
    CreditBalance,
    InvalidBill,
    parse_bill_pdf,
    validate_replacement,
)
from .client import CreditClient
from .const import (
    CONF_ACCOUNT_SEQUENCE,
    CONF_CUSTOMER_ID,
    CONF_MODE,
    CONF_SNAPSHOT,
    DOMAIN,
    MODE_BILL,
    MODE_PORTAL,
)
from .portal import AccountInfo, PacificPowerApiError, PacificPowerAuthError


class CreditConfigFlow(ConfigFlow, domain=DOMAIN):
    """Validate a real statement before creating or replacing an entry."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._accounts: list[AccountInfo] = []

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Choose the source without requiring an account for PDF imports."""
        return self.async_show_menu(step_id="user", menu_options=["bill", "portal"])

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Import a newer bill or switch to automatic account checks."""
        return self.async_show_menu(
            step_id="reconfigure", menu_options=["bill", "portal"]
        )

    async def async_step_reauth(self, entry_data: dict) -> ConfigFlowResult:
        """Ask for credentials again after authentication fails."""
        return await self.async_step_portal()

    def _existing_entry(self) -> ConfigEntry | None:
        if self.context.get("source") == "reauth":
            return self._get_reauth_entry()
        if self.context.get("source") == "reconfigure":
            return self._get_reconfigure_entry()
        return None

    def _read_upload(self, file_id: str) -> CreditBalance:
        """Run in an executor; always remove the uploaded PDF after parsing."""
        with process_uploaded_file(self.hass, file_id) as path:
            if path.stat().st_size > MAX_PDF_BYTES:
                raise InvalidBill("invalid_pdf")
            return parse_bill_pdf(path.read_bytes())

    async def async_step_bill(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Read a bill without retaining the original PDF or its text."""
        errors = {}
        if user_input is not None:
            try:
                balance = await self.hass.async_add_executor_job(
                    self._read_upload, user_input["bill_pdf"]
                )
                return await self._finish(balance, {CONF_MODE: MODE_BILL})
            except (InvalidBill, ValueError, OSError) as err:
                errors["base"] = self._bill_error(err)
        return self.async_show_form(
            step_id="bill",
            data_schema=vol.Schema(
                {vol.Required("bill_pdf"): selector.FileSelector({"accept": ".pdf"})}
            ),
            errors=errors,
        )

    async def async_step_portal(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Validate username/password sign-in without changing MFA settings."""
        errors = {}
        if user_input is not None:
            self._credentials = {
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input["credential"],
            }
            api = CreditClient(**self._credentials)
            try:
                await api.async_start()
                await api.async_login()
                user = await api.async_get_user_info()
                accounts = await api.async_get_accounts(user.get("webUserId", ""))
                # A statement belongs to an account, not each of its agreements.
                self._accounts = list(
                    {(a.customer_idn, a.account_sequence): a for a in accounts}.values()
                )
                if len(self._accounts) == 1:
                    account = self._accounts[0]
                    balance = await api.async_latest_balance(account)
                    return await self._finish(balance, self._portal_data(account))
                if self._accounts:
                    return await self.async_step_account()
                errors["base"] = "no_accounts"
            except PacificPowerAuthError:
                errors["base"] = "invalid_auth"
            except (PacificPowerApiError, aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            except InvalidBill as err:
                errors["base"] = self._bill_error(err)
            finally:
                await api.async_stop()
        return self.async_show_form(
            step_id="portal",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): selector.TextSelector(),
                    vol.Required("credential"): selector.TextSelector(
                        {"type": "password"}
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_account(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Select among multiple accounts without putting addresses in entity IDs."""
        errors = {}
        options = {
            str(i): f"Account ending {a.customer_idn[-4:]} / {a.account_sequence}"
            for i, a in enumerate(self._accounts)
        }
        if user_input is not None and user_input.get("account") in options:
            account = self._accounts[int(user_input["account"])]
            api = CreditClient(**self._credentials)
            try:
                await api.async_start()
                await api.async_login()
                await api.async_get_user_info()
                balance = await api.async_latest_balance(account)
                return await self._finish(balance, self._portal_data(account))
            except PacificPowerAuthError:
                return await self.async_step_portal()
            except (PacificPowerApiError, aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            except InvalidBill as err:
                errors["base"] = self._bill_error(err)
            finally:
                await api.async_stop()
        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema({vol.Required("account"): vol.In(options)}),
            errors=errors,
        )

    def _portal_data(self, account: AccountInfo) -> dict[str, Any]:
        return {
            CONF_MODE: MODE_PORTAL,
            **self._credentials,
            CONF_CUSTOMER_ID: account.customer_idn,
            CONF_ACCOUNT_SEQUENCE: account.account_sequence,
        }

    @staticmethod
    def _bill_error(err: Exception) -> str:
        return (
            str(err) if str(err) in {"wrong_account", "older_bill"} else "invalid_bill"
        )

    async def _finish(self, balance: CreditBalance, source: dict) -> ConfigFlowResult:
        data = {**source, CONF_SNAPSHOT: balance.as_dict()}
        if entry := self._existing_entry():
            validate_replacement(CreditBalance(**entry.data[CONF_SNAPSHOT]), balance)
            return self.async_update_reload_and_abort(
                entry,
                data=data,
                reason="reauth_successful"
                if self.context["source"] == "reauth"
                else "reconfigure_successful",
            )
        await self.async_set_unique_id(balance.account_hash)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Pacific Power solar credits", data=data)
