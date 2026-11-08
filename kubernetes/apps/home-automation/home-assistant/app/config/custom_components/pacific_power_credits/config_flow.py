"""Import banked-kWh statements; utility sign-in belongs to upstream Pacific Power."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .bill import (
    MAX_PDF_BYTES,
    CreditBalance,
    InvalidBill,
    parse_bill_pdf,
    validate_replacement,
)
from .const import CONF_MODE, CONF_SNAPSHOT, DOMAIN, MODE_BILL


class CreditConfigFlow(ConfigFlow, domain=DOMAIN):
    """Keep credit imports separate from the maintained utility integration."""

    VERSION = 2

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self.async_step_bill(user_input)

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_bill(user_input)

    async def async_step_reauth(self, entry_data: dict) -> ConfigFlowResult:
        return await self.async_step_bill()

    def _existing_entry(self) -> ConfigEntry | None:
        if self.context.get("source") == "reauth":
            return self._get_reauth_entry()
        if self.context.get("source") == "reconfigure":
            return self._get_reconfigure_entry()
        return None

    def _read_upload(self, file_id: str) -> CreditBalance:
        """Always remove the PDF after parsing, including on failure."""
        with process_uploaded_file(self.hass, file_id) as path:
            if path.stat().st_size > MAX_PDF_BYTES:
                raise InvalidBill("invalid_pdf")
            return parse_bill_pdf(path.read_bytes())

    async def async_step_bill(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Read the recorded balance without retaining the document."""
        errors = {}
        if user_input is not None:
            try:
                balance = await self.hass.async_add_executor_job(
                    self._read_upload, user_input["bill_pdf"]
                )
                return await self._finish(balance)
            except (ValueError, OSError) as err:
                errors["base"] = (
                    str(err)
                    if str(err) in {"wrong_account", "older_bill"}
                    else "invalid_bill"
                )
        return self.async_show_form(
            step_id="bill",
            data_schema=vol.Schema(
                {vol.Required("bill_pdf"): selector.FileSelector({"accept": ".pdf"})}
            ),
            errors=errors,
        )

    async def _finish(self, balance: CreditBalance) -> ConfigFlowResult:
        data = {CONF_MODE: MODE_BILL, CONF_SNAPSHOT: balance.as_dict()}
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
