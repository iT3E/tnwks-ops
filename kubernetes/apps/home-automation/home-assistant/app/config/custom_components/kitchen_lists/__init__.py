"""Kitchen lists: create, change and delete the wall display's lists.

Enabled from configuration.yaml with `kitchen_lists:`. The services are
ordinary (not admin-only), so the tablet's non-admin kiosk user can call
them; sensor.kitchen_lists carries the lists for the Lists tab's card.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .manager import KitchenLists

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

CREATE_SCHEMA = vol.Schema(
    {
        vol.Required("title"): cv.string,
        vol.Optional("icon"): cv.string,
        vol.Optional("expiry", default=False): cv.boolean,
    }
)
UPDATE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("title"): cv.string,
        vol.Optional("icon"): cv.string,
        vol.Optional("expiry"): cv.boolean,
        vol.Optional("position"): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)
DELETE_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_id})
RESTORE_SCHEMA = vol.Schema({vol.Optional("title"): cv.string})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    lists = KitchenLists(hass)
    await lists.async_load()
    hass.data[DOMAIN] = lists
    lists.async_listen_registry()

    async def create(call: ServiceCall) -> ServiceResponse:
        entity_id = await lists.async_create(
            call.data["title"], call.data.get("icon"), call.data["expiry"]
        )
        return {"entity_id": entity_id}

    async def update(call: ServiceCall) -> None:
        await lists.async_update(
            call.data["entity_id"],
            title=call.data.get("title"),
            icon=call.data.get("icon"),
            expiry=call.data.get("expiry"),
            position=call.data.get("position"),
        )

    async def delete(call: ServiceCall) -> None:
        await lists.async_delete(call.data["entity_id"])

    async def restore(call: ServiceCall) -> ServiceResponse:
        return {"entity_id": await lists.async_restore(call.data.get("title"))}

    hass.services.async_register(
        DOMAIN, "create", create, CREATE_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(DOMAIN, "update", update, UPDATE_SCHEMA)
    hass.services.async_register(DOMAIN, "delete", delete, DELETE_SCHEMA)
    hass.services.async_register(
        DOMAIN, "restore", restore, RESTORE_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.async_create_task(
        discovery.async_load_platform(hass, Platform.SENSOR, DOMAIN, {}, config)
    )
    return True
