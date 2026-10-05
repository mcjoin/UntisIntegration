"""Base entity for Untis Public."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import UntisCoordinator


class UntisEntity(CoordinatorEntity[UntisCoordinator]):
    """Common base: one device per school class."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: UntisCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Untis {coordinator.class_name}",
            manufacturer="Untis",
            model=coordinator.school_display,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"https://{coordinator.client.server}/WebUntis/?school={coordinator.client.school}#/basic/timetable",
        )
