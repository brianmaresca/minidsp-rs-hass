from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MiniDSPCoordinator

_LOGGER = logging.getLogger(__name__)


class MiniDSPOutputGain(CoordinatorEntity[MiniDSPCoordinator], NumberEntity):
    """Output channel gain control (-127 to 12 dB)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:volume-high"
    _attr_native_min_value = -127.0
    _attr_native_max_value = 12.0
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "dBFS"
    _attr_assumed_state = True

    def __init__(self, coordinator: MiniDSPCoordinator, output_index: int):
        super().__init__(coordinator)
        self._output_index = output_index
        self._optimistic_gain: float | None = None
        self._preset = self._current_preset()
        self._attr_unique_id = f"{coordinator.address}_output_{output_index}_gain"
        self._attr_name = f"Output {output_index} Gain"

    @property
    def native_value(self):  # type: ignore[override]
        # The status API has no output-gain readback.
        return self._optimistic_gain

    def _current_preset(self) -> int | None:
        return (self.coordinator.data or {}).get("master", {}).get("preset")

    @callback
    def _handle_coordinator_update(self) -> None:
        preset = self._current_preset()
        if not self.coordinator.last_update_success or preset != self._preset:
            self._optimistic_gain = None
        self._preset = preset
        super()._handle_coordinator_update()

    async def async_set_native_value(self, value: float):  # type: ignore[override]
        await self.coordinator._api.async_set_output_gain(
            self._output_index, float(value)
        )
        self._optimistic_gain = float(value)
        self.async_write_ha_state()
        # Refresh other state without discarding the successfully requested gain.
        await self.coordinator.async_request_refresh()

    @property
    def device_info(self):  # type: ignore[override]
        return {
            "identifiers": {(DOMAIN, self.coordinator.address)},
            "name": self.coordinator.name,
        }


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    stored = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator: MiniDSPCoordinator | None = stored.get("coordinator")
    if coordinator is None:
        _LOGGER.error("Coordinator not found during number platform setup")
        return

    # Determine number of output channels from output_levels
    data = coordinator.data or {}
    output_levels = data.get("output_levels", [])
    num_outputs = len(output_levels)

    entities = []
    for i in range(num_outputs):
        entities.append(MiniDSPOutputGain(coordinator, i))

    async_add_entities(entities)
