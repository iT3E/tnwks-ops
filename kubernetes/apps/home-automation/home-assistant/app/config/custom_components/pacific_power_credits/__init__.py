"""Pacific Power's statement-backed solar credit balance."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_MODE, CONF_SNAPSHOT, MODE_BILL
from .coordinator import CreditCoordinator

PLATFORMS = [Platform.SENSOR]
type CreditConfigEntry = ConfigEntry[CreditCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: CreditConfigEntry) -> bool:
    """Load the imported balance; live grid readings belong to upstream Pacific Power."""
    entry.runtime_data = CreditCoordinator(hass, entry)
    await entry.runtime_data.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CreditConfigEntry) -> bool:
    """Unload the credit entities."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: CreditConfigEntry) -> bool:
    """Preserve the credit snapshot and remove the retired billing login path."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            data={CONF_MODE: MODE_BILL, CONF_SNAPSHOT: dict(entry.data[CONF_SNAPSHOT])},
            version=2,
            minor_version=1,
        )
    return True
