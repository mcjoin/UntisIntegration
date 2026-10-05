"""Calendar for Untis Public (timetable as calendar events)."""

from __future__ import annotations

import datetime as dt

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import STATUS_CANCELLED
from .coordinator import Lesson, UntisConfigEntry, UntisCoordinator
from .entity import UntisEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UntisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the calendar."""
    async_add_entities([UntisCalendar(entry.runtime_data)])


def _event(lesson: Lesson) -> CalendarEvent:
    renamed = lesson.subject != lesson.subject_short
    summary = (lesson.subject if renamed else lesson.subject_long or lesson.subject) or "Unterricht"
    if lesson.status == STATUS_CANCELLED:
        summary = f"❌ {summary} (Entfall)"
    elif lesson.is_changed:
        summary = f"⚠️ {summary}"
    description = [lesson.summary()] if lesson.is_changed else []
    if lesson.teachers:
        description.append("Lehrer: " + ", ".join(lesson.teachers))
    if lesson.info:
        description.append(lesson.info)
    if lesson.lesson_text:
        description.append(lesson.lesson_text)
    return CalendarEvent(
        start=lesson.start,
        end=lesson.end,
        summary=summary,
        description="\n".join(description) or None,
        location=", ".join(lesson.rooms) or None,
        uid=f"{lesson.start.isoformat()}-{lesson.subject}-{'-'.join(lesson.teachers)}",
    )


class UntisCalendar(UntisEntity, CalendarEntity):
    """Timetable of the class as a calendar."""

    def __init__(self, coordinator: UntisCoordinator) -> None:
        super().__init__(coordinator, "timetable")

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        for lesson in self.coordinator.data.lessons:
            if lesson.end > now and lesson.status != STATUS_CANCELLED:
                return _event(lesson)
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: dt.datetime, end_date: dt.datetime
    ) -> list[CalendarEvent]:
        return [
            _event(lesson)
            for lesson in self.coordinator.data.lessons
            if lesson.end > start_date and lesson.start < end_date
        ]
