"""sensor.kitchen_lists: how many lists the Lists tab has, and the lists."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN, SIGNAL_UPDATED
from .manager import KitchenLists


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return
    async_add_entities([KitchenListsSensor(hass.data[DOMAIN])])


class KitchenListsSensor(SensorEntity):
    """State: the number of lists. Attribute `lists`: each list's settings, in order."""

    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_name = "Kitchen lists"
    _attr_unique_id = "kitchen_lists"
    _attr_icon = "mdi:format-list-checks"
    # The settings live in .storage/kitchen_lists; the recorder needn't copy them.
    _unrecorded_attributes = frozenset({"lists"})

    def __init__(self, lists: KitchenLists) -> None:
        self._lists = lists

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._updated)
        )

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return len(self._lists.lists)

    @property
    def extra_state_attributes(self) -> dict:
        return {"lists": self._lists.as_attribute()}
