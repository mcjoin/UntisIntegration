"""Binary sensors for Untis Public."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .coordinator import UntisConfigEntry, UntisCoordinator, UntisData
from .entity import UntisEntity


def _has_school(data: UntisData, day: dt.date) -> bool:
    return bool(data.lessons_on(day, include_cancelled=False))


def _has_changes(data: UntisData, day: dt.date) -> bool:
    return any(lesson.is_changed for lesson in data.lessons_on(day))


@dataclass(frozen=True, kw_only=True)
class UntisBinarySensorDescription(BinarySensorEntityDescription):
    """Describes an Untis binary sensor."""

    value_fn: Callable[[UntisData, dt.date], bool]


BINARY_SENSORS: tuple[UntisBinarySensorDescription, ...] = (
    UntisBinarySensorDescription(
        key="school_today",
        icon="mdi:school",
        value_fn=lambda d, today: _has_school(d, today),
    ),
    UntisBinarySensorDescription(
        key="school_tomorrow",
        icon="mdi:school-outline",
        value_fn=lambda d, today: _has_school(d, today + dt.timedelta(days=1)),
    ),
    UntisBinarySensorDescription(
        key="changes_today",
        icon="mdi:swap-horizontal-bold",
        value_fn=lambda d, today: _has_changes(d, today),
    ),
    UntisBinarySensorDescription(
        key="changes_tomorrow",
        icon="mdi:swap-horizontal-bold",
        value_fn=lambda d, today: _has_changes(d, today + dt.timedelta(days=1)),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UntisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(UntisBinarySensor(coordinator, desc) for desc in BINARY_SENSORS)


class UntisBinarySensor(UntisEntity, BinarySensorEntity):
    """An Untis binary sensor."""

    entity_description: UntisBinarySensorDescription

    def __init__(
        self, coordinator: UntisCoordinator, description: UntisBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # re-evaluate "today"/"tomorrow" right after midnight
        self.async_on_remove(
            async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=5)
        )

    @callback
    def _midnight(self, _now: dt.datetime) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.coordinator.data, dt_util.now().date())
