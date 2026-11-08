"""Expose an imported statement snapshot without making network requests."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .bill import CreditBalance
from .const import CONF_SNAPSHOT, DOMAIN

_LOGGER = logging.getLogger(__name__)


class CreditCoordinator(DataUpdateCoordinator[CreditBalance]):
    """Hold the last imported credit balance; a new bill updates it via Reconfigure."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN)
        self.entry = entry
        self.data = CreditBalance(**entry.data[CONF_SNAPSHOT])

    async def _async_update_data(self) -> CreditBalance:
        """Return only the balance actually read from the imported bill."""
        return self.data
