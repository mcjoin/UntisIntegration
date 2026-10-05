"""Sensors for Untis Public."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .coordinator import Lesson, UntisConfigEntry, UntisCoordinator, UntisData
from .entity import UntisEntity

MAX_LIST = 30


def _day_attrs(lessons: list[Lesson], day: dt.date | None) -> dict[str, Any]:
    active = [lesson for lesson in lessons if not lesson.is_cancelled]
    return {
        "date": day.isoformat() if day else None,
        "first_start": active[0].start.isoformat() if active else None,
        "last_end": active[-1].end.isoformat() if active else None,
        "subjects": list(dict.fromkeys(lesson.subject for lesson in active)),
        "changes": [lesson.summary() for lesson in lessons if lesson.is_changed],
        "lessons": [lesson.as_dict() for lesson in lessons],
    }


def _today(now: dt.datetime) -> dt.date:
    return now.date()


def _tomorrow(now: dt.datetime) -> dt.date:
    return now.date() + dt.timedelta(days=1)


def _next_day(data: UntisData, now: dt.datetime) -> dt.date | None:
    return data.next_school_day(now.date())


def _count(data: UntisData, day: dt.date | None) -> int:
    """Number of time slots with lessons (parallel group lessons count once)."""
    if not day:
        return 0
    return len({(lesson.start, lesson.end) for lesson in data.lessons_on(day, include_cancelled=False)})


def _first_start(data: UntisData, day: dt.date | None) -> dt.datetime | None:
    lessons = data.lessons_on(day, include_cancelled=False) if day else []
    return lessons[0].start if lessons else None


def _last_end(data: UntisData, day: dt.date | None) -> dt.datetime | None:
    lessons = data.lessons_on(day, include_cancelled=False) if day else []
    return max(lesson.end for lesson in lessons) if lessons else None


def _current(data: UntisData, now: dt.datetime) -> Lesson | None:
    for lesson in data.lessons:
        if lesson.start <= now < lesson.end and not lesson.is_cancelled:
            return lesson
    return None


def _next(data: UntisData, now: dt.datetime) -> Lesson | None:
    for lesson in data.lessons:
        if lesson.start > now and not lesson.is_cancelled:
            return lesson
    return None


@dataclass(frozen=True, kw_only=True)
class UntisSensorDescription(SensorEntityDescription):
    """Describes an Untis sensor."""

    value_fn: Callable[[UntisData, dt.datetime], Any]
    attrs_fn: Callable[[UntisData, dt.datetime], dict[str, Any]] | None = None


SENSORS: tuple[UntisSensorDescription, ...] = (
    UntisSensorDescription(
        key="lessons_today",
        icon="mdi:school",
        native_unit_of_measurement="Stunden",
        value_fn=lambda d, now: _count(d, _today(now)),
        attrs_fn=lambda d, now: _day_attrs(d.lessons_on(_today(now)), _today(now)),
    ),
    UntisSensorDescription(
        key="lessons_tomorrow",
        icon="mdi:school-outline",
        native_unit_of_measurement="Stunden",
        value_fn=lambda d, now: _count(d, _tomorrow(now)),
        attrs_fn=lambda d, now: _day_attrs(d.lessons_on(_tomorrow(now)), _tomorrow(now)),
    ),
    UntisSensorDescription(
        key="next_school_day",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-arrow-right",
        value_fn=_next_day,
        attrs_fn=lambda d, now: _day_attrs(
            d.lessons_on(day) if (day := _next_day(d, now)) else [], day
        ),
    ),
    UntisSensorDescription(
        key="school_start_today",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-start",
        value_fn=lambda d, now: _first_start(d, _today(now)),
    ),
    UntisSensorDescription(
        key="school_end_today",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-end",
        value_fn=lambda d, now: _last_end(d, _today(now)),
    ),
    UntisSensorDescription(
        key="school_start_tomorrow",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:alarm",
        value_fn=lambda d, now: _first_start(d, _tomorrow(now)),
    ),
    UntisSensorDescription(
        key="school_end_tomorrow",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-end",
        value_fn=lambda d, now: _last_end(d, _tomorrow(now)),
    ),
    UntisSensorDescription(
        key="school_start_next_day",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:alarm",
        value_fn=lambda d, now: _first_start(d, _next_day(d, now)),
    ),
    UntisSensorDescription(
        key="current_lesson",
        icon="mdi:human-male-board",
        value_fn=lambda d, now: (lesson.subject if (lesson := _current(d, now)) else None),
        attrs_fn=lambda d, now: (lesson.as_dict() if (lesson := _current(d, now)) else {}),
    ),
    UntisSensorDescription(
        key="next_lesson",
        icon="mdi:page-next-outline",
        value_fn=lambda d, now: (lesson.subject if (lesson := _next(d, now)) else None),
        attrs_fn=lambda d, now: (lesson.as_dict() if (lesson := _next(d, now)) else {}),
    ),
    UntisSensorDescription(
        key="changes",
        icon="mdi:swap-horizontal-bold",
        native_unit_of_measurement="Änderungen",
        value_fn=lambda d, now: len(d.upcoming_changes(now)),
        attrs_fn=lambda d, now: {
            "changes": [lesson.summary() for lesson in d.upcoming_changes(now)][:MAX_LIST],
            "lessons": [lesson.as_dict() for lesson in d.upcoming_changes(now)][:MAX_LIST],
        },
    ),
    UntisSensorDescription(
        key="messages",
        icon="mdi:message-alert-outline",
        native_unit_of_measurement="Nachrichten",
        value_fn=lambda d, now: len(d.messages),
        attrs_fn=lambda d, now: {
            "subjects": [m["subject"] for m in d.messages],
            "text": "\n\n".join(
                (f"{m['subject']}\n{m['text']}" if m["subject"] else m["text"]) for m in d.messages
            ),
            "messages": [
                {"id": m["id"], "subject": m["subject"], "text": m["text"]} for m in d.messages
            ],
        },
    ),
    UntisSensorDescription(
        key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:update",
        entity_registry_enabled_default=False,
        value_fn=lambda d, now: d.last_update,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UntisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    async_add_entities(UntisSensor(coordinator, description) for description in SENSORS)


class UntisSensor(UntisEntity, SensorEntity):
    """An Untis sensor."""

    entity_description: UntisSensorDescription

    def __init__(self, coordinator: UntisCoordinator, description: UntisSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # time based values (current lesson, today/tomorrow) change without new data
        self.async_on_remove(
            async_track_time_interval(self.hass, self._tick, dt.timedelta(minutes=1))
        )

    @callback
    def _tick(self, _now: dt.datetime) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data, dt_util.now())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data, dt_util.now())
