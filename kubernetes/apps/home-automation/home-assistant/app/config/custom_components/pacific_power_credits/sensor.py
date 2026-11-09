"""The banked energy balance and its statement context."""

from datetime import date

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CreditConfigEntry
from .const import CONF_MODE, DOMAIN
from .coordinator import CreditCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CreditConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a balance, statement date and billing-month expiry."""
    async_add_entities(
        CreditSensor(entry.runtime_data, key)
        for key in ("banked_energy", "statement_date", "expiry_billing_month")
    )


class CreditSensor(CoordinatorEntity[CreditCoordinator], SensorEntity):
    """A statement-backed value; it is never a grid-flow energy counter."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CreditCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.entry.unique_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.unique_id)},
            name="Pacific Power",
            manufacturer="PacifiCorp",
            model="Solar credit bank",
            entry_type=DeviceEntryType.SERVICE,
        )
        if key == "banked_energy":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_icon = "mdi:solar-power"
            # No state_class: this is a remaining balance that rises and falls,
            # not cumulative exports or consumption for the Energy dashboard.
        elif key == "statement_date":
            self._attr_device_class = SensorDeviceClass.DATE
        else:
            self._attr_icon = "mdi:calendar-clock"

    @property
    def native_value(self) -> float | date | str:
        """Return exactly what the statement records."""
        data = self.coordinator.data
        if self._key == "banked_energy":
            return data.kwh
        if self._key == "statement_date":
            return date.fromisoformat(data.statement_date)
        return data.expiry_month

    @property
    def extra_state_attributes(self) -> dict | None:
        """Keep the balance's date and source visible when it is used in a card."""
        if self._key != "banked_energy":
            return None
        return {
            "statement_date": self.coordinator.data.statement_date,
            "expiry_billing_month": self.coordinator.data.expiry_month,
            "source": self.coordinator.entry.data[CONF_MODE],
            "bill_import_kwh": self.coordinator.data.bill_import_kwh,
            "bill_export_kwh": self.coordinator.data.bill_export_kwh,
            "service_period_start": self.coordinator.data.service_period_start,
            "service_period_end": self.coordinator.data.service_period_end,
        }
