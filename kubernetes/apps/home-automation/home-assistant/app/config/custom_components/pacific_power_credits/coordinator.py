"""Refresh the credit snapshot without estimating, expiring or zeroing it."""

from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bill import CreditBalance, InvalidBill, validate_replacement
from .client import CreditClient
from .const import (
    CONF_ACCOUNT_SEQUENCE,
    CONF_CUSTOMER_ID,
    CONF_MODE,
    CONF_SNAPSHOT,
    DOMAIN,
    MODE_PORTAL,
)
from .portal import AccountInfo, PacificPowerApiError, PacificPowerAuthError

_LOGGER = logging.getLogger(__name__)


class CreditCoordinator(DataUpdateCoordinator[CreditBalance]):
    """Poll the bill date every 12 hours; parse a PDF only when it changes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        automatic = entry.data[CONF_MODE] == MODE_PORTAL
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(hours=12) if automatic else None,
        )
        self.entry = entry
        self.data = CreditBalance(**entry.data[CONF_SNAPSHOT])

    async def _async_update_data(self) -> CreditBalance:
        if self.entry.data[CONF_MODE] != MODE_PORTAL:
            return self.data
        api = CreditClient(
            self.entry.data[CONF_USERNAME], self.entry.data[CONF_PASSWORD]
        )
        try:
            await api.async_start()
            await api.async_login()
            await api.async_get_user_info()
            account = AccountInfo(
                customer_idn=self.entry.data[CONF_CUSTOMER_ID],
                account_sequence=self.entry.data[CONF_ACCOUNT_SEQUENCE],
                agreement_sequence="",
                address="",
            )
            balance = await api.async_latest_balance(account, self.data)
            validate_replacement(self.data, balance)
        except PacificPowerAuthError as err:
            raise ConfigEntryAuthFailed(
                "Pacific Power sign-in failed; reauthenticate or import a bill PDF"
            ) from err
        except (
            PacificPowerApiError,
            InvalidBill,
            aiohttp.ClientError,
            TimeoutError,
        ) as err:
            # Never include remote payloads, bill text or credentials in the log.
            raise UpdateFailed(
                "Unable to verify the latest Pacific Power credit balance"
            ) from err
        finally:
            await api.async_stop()
        if balance != self.data:
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, CONF_SNAPSHOT: balance.as_dict()},
            )
        return balance
