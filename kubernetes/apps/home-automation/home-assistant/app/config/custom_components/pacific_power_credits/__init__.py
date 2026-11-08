"""Pacific Power's statement-backed solar credit balance."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import CreditCoordinator

PLATFORMS = [Platform.SENSOR]
type CreditConfigEntry = ConfigEntry[CreditCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: CreditConfigEntry) -> bool:
    """Load an imported balance or start automatic statement checks."""
    entry.runtime_data = CreditCoordinator(hass, entry)
    await entry.runtime_data.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CreditConfigEntry) -> bool:
    """Unload entities and stop polling."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
