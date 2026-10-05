"""Data update coordinator for Untis Public."""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    Message,
    Period,
    UntisAuthError,
    UntisError,
    UntisPublicClient,
)
from .const import (
    CHANGED_STATUSES,
    CL_END,
    CL_NAME,
    CL_ROOM,
    CL_SCHOOL_DAYS_ONLY,
    CL_START,
    CL_TEACHER,
    CL_WEEKDAY,
    CL_WEEKS,
    CONF_CLASS_ID,
    CONF_CUSTOM_LESSONS,
    CONF_CLASS_NAME,
    CONF_DAYS_AHEAD,
    CONF_SCAN_INTERVAL,
    CONF_SCHOOL,
    CONF_SCHOOL_DISPLAY,
    CONF_SERVER,
    CONF_SUBJECT_NAMES,
    CONF_TEACHER_NAMES,
    DEFAULT_DAYS_AHEAD,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_CHANGE,
    EVENT_MESSAGE,
    STATUS_ADDITIONAL,
    STATUS_CANCELLED,
    STATUS_CUSTOM,
    STATUS_EVENT,
    STATUS_EXAM,
    STATUS_REGULAR,
    STATUS_ROOM_SUBSTITUTION,
    STATUS_SHIFT,
    STATUS_SUBSTITUTION,
    STORAGE_VERSION,
    WEEKS_EVEN,
    WEEKS_EVERY,
    WEEKS_ODD,
)

_LOGGER = logging.getLogger(__name__)

type UntisConfigEntry = ConfigEntry[UntisCoordinator]


@dataclass
class Lesson:
    """A lesson as exposed to Home Assistant."""

    start: dt.datetime
    end: dt.datetime
    subject: str
    subject_long: str
    teachers: list[str]
    rooms: list[str]
    original_teachers: list[str]
    original_rooms: list[str]
    status: str
    lesson_text: str
    substitution_text: str
    info: str
    # abbreviations as delivered by Untis (subject/teachers above may be renamed)
    subject_short: str = ""
    teachers_short: list[str] = field(default_factory=list)
    original_teachers_short: list[str] = field(default_factory=list)

    @property
    def date(self) -> dt.date:
        return self.start.date()

    @property
    def is_cancelled(self) -> bool:
        return self.status == STATUS_CANCELLED

    @property
    def is_changed(self) -> bool:
        return self.status in CHANGED_STATUSES

    @property
    def change_key(self) -> str:
        return "|".join(
            [
                # abbreviations, so renaming a teacher does not re-fire old changes
                self.start.isoformat(),
                self.subject_short,
                self.status,
                ",".join(self.teachers_short),
                ",".join(self.rooms),
                self.substitution_text,
            ]
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "start_time": self.start.strftime("%H:%M"),
            "end_time": self.end.strftime("%H:%M"),
            "subject": self.subject,
            "subject_short": self.subject_short,
            "subject_long": self.subject_long,
            "teachers": self.teachers,
            "teachers_short": self.teachers_short,
            "rooms": self.rooms,
            "original_teachers": self.original_teachers,
            "original_teachers_short": self.original_teachers_short,
            "original_rooms": self.original_rooms,
            "status": self.status,
            "changed": self.is_changed,
            "lesson_text": self.lesson_text,
            "substitution_text": self.substitution_text,
            "info": self.info,
        }

    def summary(self) -> str:
        """Short human readable description, e.g. for notifications."""
        time = f"{self.start:%d.%m. %H:%M}"
        label = {
            STATUS_CANCELLED: "Entfall",
            STATUS_SUBSTITUTION: "Vertretung",
            STATUS_ROOM_SUBSTITUTION: "Raumänderung",
            STATUS_ADDITIONAL: "Zusatzstunde",
            STATUS_SHIFT: "Verlegung",
            STATUS_EXAM: "Prüfung",
            STATUS_EVENT: "Veranstaltung",
        }.get(self.status, "Änderung")
        parts = [f"{time} {self.subject or '?'}: {label}"]
        if self.teachers and self.status != STATUS_CANCELLED:
            text = "Lehrer " + ", ".join(self.teachers)
            if self.original_teachers:
                text += " statt " + ", ".join(self.original_teachers)
            parts.append(text)
        if self.rooms and self.status != STATUS_CANCELLED:
            text = "Raum " + ", ".join(self.rooms)
            if self.original_rooms:
                text += " statt " + ", ".join(self.original_rooms)
            parts.append(text)
        if self.substitution_text:
            parts.append(self.substitution_text)
        return " – ".join(parts)


@dataclass
class UntisData:
    """Everything the entities need."""

    lessons: list[Lesson] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    class_name: str = ""
    last_update: dt.datetime | None = None

    def lessons_on(self, day: dt.date, include_cancelled: bool = True) -> list[Lesson]:
        return [
            lesson
            for lesson in self.lessons
            if lesson.date == day and (include_cancelled or not lesson.is_cancelled)
        ]

    def next_school_day(self, after: dt.date) -> dt.date | None:
        days = sorted(
            {lesson.date for lesson in self.lessons if lesson.date > after and not lesson.is_cancelled}
        )
        return days[0] if days else None

    def upcoming_changes(self, now: dt.datetime) -> list[Lesson]:
        return [lesson for lesson in self.lessons if lesson.is_changed and lesson.end >= now]


def _status(period: Period) -> str:
    flags = period.flags
    state = period.cell_state.upper()
    if flags.get("cancelled") or state == "CANCEL":
        return STATUS_CANCELLED
    if flags.get("exam") or state == "EXAM":
        return STATUS_EXAM
    if flags.get("additional") or state == "ADDITIONAL":
        return STATUS_ADDITIONAL
    if flags.get("shift") or state == "SHIFT":
        return STATUS_SHIFT
    if flags.get("substitution") or state == "SUBSTITUTION" or period.original_teachers:
        return STATUS_SUBSTITUTION
    if flags.get("roomSubstitution") or state == "ROOMSUBSTITUTION" or period.original_rooms:
        return STATUS_ROOM_SUBSTITUTION
    if flags.get("event") or state == "EVENT":
        return STATUS_EVENT
    return STATUS_REGULAR


def _to_lesson(
    period: Period, teacher_names: dict[str, str], subject_names: dict[str, str]
) -> Lesson:
    teachers = [t.name for t in period.teachers if t.name]
    original_teachers = [t.name for t in period.original_teachers if t.name]
    subjects = [s.name for s in period.subjects if s.name]
    subject_short = ", ".join(subjects) or period.lesson_text
    return Lesson(
        start=period.start,
        end=period.end,
        subject=", ".join(subject_names.get(s, s) for s in subjects) or period.lesson_text,
        subject_long=", ".join(s.long_name or s.name for s in period.subjects),
        teachers=[teacher_names.get(t, t) for t in teachers],
        rooms=[r.name for r in period.rooms if r.name],
        original_teachers=[teacher_names.get(t, t) for t in original_teachers],
        original_rooms=[r.name for r in period.original_rooms if r.name],
        status=_status(period),
        lesson_text=period.lesson_text,
        substitution_text=period.substitution_text,
        info=period.period_text or period.info,
        subject_short=subject_short,
        teachers_short=teachers,
        original_teachers_short=original_teachers,
    )


def build_custom_lessons(
    config: dict[str, dict[str, Any]],
    untis_lessons: list[Lesson],
    first_day: dt.date,
    last_day: dt.date,
    tz: dt.tzinfo,
) -> list[Lesson]:
    """Create the own lessons (AGs, Lerngruppen ...) configured in the options."""
    school_days = {lesson.date for lesson in untis_lessons if not lesson.is_cancelled}
    result: list[Lesson] = []
    for entry in config.values():
        name = (entry.get(CL_NAME) or "").strip()
        if not name or not entry.get(CL_START) or not entry.get(CL_END):
            continue
        weekday = int(entry.get(CL_WEEKDAY, 0))
        start = dt.time.fromisoformat(entry[CL_START])
        end = dt.time.fromisoformat(entry[CL_END])
        weeks = entry.get(CL_WEEKS, WEEKS_EVERY)
        day = first_day + dt.timedelta(days=(weekday - first_day.weekday()) % 7)
        while day <= last_day:
            week_no = day.isocalendar().week
            if (
                (weeks == WEEKS_EVEN and week_no % 2)
                or (weeks == WEEKS_ODD and not week_no % 2)
                or (entry.get(CL_SCHOOL_DAYS_ONLY, True) and day not in school_days)
            ):
                day += dt.timedelta(days=7)
                continue
            teacher = (entry.get(CL_TEACHER) or "").strip()
            room = (entry.get(CL_ROOM) or "").strip()
            result.append(
                Lesson(
                    start=dt.datetime.combine(day, start, tzinfo=tz),
                    end=dt.datetime.combine(day, end, tzinfo=tz),
                    subject=name,
                    subject_long=name,
                    teachers=[teacher] if teacher else [],
                    rooms=[room] if room else [],
                    original_teachers=[],
                    original_rooms=[],
                    status=STATUS_CUSTOM,
                    lesson_text="",
                    substitution_text="",
                    info="Eigener Eintrag",
                    subject_short=name,
                    teachers_short=[teacher] if teacher else [],
                )
            )
            day += dt.timedelta(days=7)
    return result


def html_to_text(value: str) -> str:
    """Convert the HTML of a message of the day to plain text."""
    value = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


class UntisCoordinator(DataUpdateCoordinator[UntisData]):
    """Fetch timetable + messages, detect changes and fire events."""

    config_entry: UntisConfigEntry

    def __init__(self, hass: HomeAssistant, entry: UntisConfigEntry) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_CLASS_NAME]}",
            update_interval=dt.timedelta(minutes=interval),
        )
        session = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
        self.client = UntisPublicClient(session, entry.data[CONF_SERVER], entry.data[CONF_SCHOOL])
        self.class_id: int = entry.data[CONF_CLASS_ID]
        self.class_name: str = entry.data[CONF_CLASS_NAME]
        self.school_display: str = entry.data.get(CONF_SCHOOL_DISPLAY, entry.data[CONF_SCHOOL])
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._known_changes: set[str] | None = None
        self._known_messages: set[str] | None = None
        # abbreviation -> long name as delivered by Untis (may be empty)
        self.seen_teachers: dict[str, str] = {}
        self.seen_subjects: dict[str, str] = {}

    @property
    def days_ahead(self) -> int:
        return self.config_entry.options.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD)

    async def _async_update_data(self) -> UntisData:
        if self._known_changes is None:
            stored = await self._store.async_load() or {}
            self._known_changes = set(stored.get("changes", []))
            self._known_messages = set(stored.get("messages", []))
            first_run = not stored
        else:
            first_run = False

        tz = dt_util.get_default_time_zone()
        today = dt_util.now().date()
        try:
            await self.client.async_login()
            await self._async_resolve_class(today)

            periods: list[Period] = []
            weeks = self.days_ahead // 7 + 1
            for week in range(weeks + 1):
                periods += await self.client.async_get_week(
                    self.class_id, today + dt.timedelta(weeks=week), tz
                )
            messages = await self.client.async_get_messages(today)
        except UntisAuthError as err:
            raise UpdateFailed(
                f"Anonymer Zugriff fehlgeschlagen (von der Schule deaktiviert?): {err}"
            ) from err
        except UntisError as err:
            raise UpdateFailed(f"Fehler beim Abruf von WebUntis: {err}") from err

        horizon = today + dt.timedelta(days=self.days_ahead)
        options = self.config_entry.options
        teacher_names = options.get(CONF_TEACHER_NAMES, {})
        subject_names = options.get(CONF_SUBJECT_NAMES, {})
        unique_periods = list({p.id: p for p in periods}.values())
        lessons = sorted(
            (_to_lesson(p, teacher_names, subject_names) for p in unique_periods),
            key=lambda lesson: (lesson.start, lesson.subject),
        )
        lessons = [lesson for lesson in lessons if lesson.date <= horizon]
        lessons += build_custom_lessons(
            options.get(CONF_CUSTOM_LESSONS, {}),
            lessons,
            today - dt.timedelta(days=today.weekday()),
            horizon,
            tz,
        )
        lessons.sort(key=lambda lesson: (lesson.start, lesson.subject))

        # abbreviations seen in the timetable -> offered for renaming in the options
        for period in unique_periods:
            for teacher in period.teachers + period.original_teachers:
                if teacher.name:
                    self.seen_teachers[teacher.name] = teacher.long_name
            for subject in period.subjects:
                if subject.name:
                    self.seen_subjects[subject.name] = subject.long_name

        message_dicts = [
            {
                "id": m.id,
                "subject": m.subject,
                "text": html_to_text(m.text),
                "html": m.text,
            }
            for m in messages
        ]

        self._detect_changes(lessons, messages, today, first_run)
        await self._store.async_save(
            {"changes": sorted(self._known_changes), "messages": sorted(self._known_messages)}
        )

        return UntisData(
            lessons=lessons,
            messages=message_dicts,
            class_name=self.class_name,
            last_update=dt_util.now(),
        )

    async def _async_resolve_class(self, today: dt.date) -> None:
        """Class IDs change with a new school year -> re-resolve by name."""
        classes = await self.client.async_get_classes(today)
        if not classes or any(c.id == self.class_id for c in classes):
            return
        for school_class in classes:
            if school_class.name == self.class_name:
                _LOGGER.info(
                    "Class %s got new id %s (was %s)",
                    self.class_name,
                    school_class.id,
                    self.class_id,
                )
                self.class_id = school_class.id
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, CONF_CLASS_ID: school_class.id},
                )
                return
        _LOGGER.warning("Class %s not found in current timetable", self.class_name)

    def _detect_changes(
        self,
        lessons: list[Lesson],
        messages: list[Message],
        today: dt.date,
        first_run: bool,
    ) -> None:
        assert self._known_changes is not None and self._known_messages is not None
        base = {
            "entry_id": self.config_entry.entry_id,
            "school": self.school_display,
            "class": self.class_name,
        }

        current_changes = {lesson.change_key: lesson for lesson in lessons if lesson.is_changed}
        for key, lesson in current_changes.items():
            if key in self._known_changes or first_run:
                continue
            if lesson.date < today:
                continue
            self.hass.bus.async_fire(
                EVENT_CHANGE, {**base, **lesson.as_dict(), "message": lesson.summary()}
            )
        # keep only keys that are still relevant (today or later)
        self._known_changes = {
            key for key in self._known_changes | set(current_changes) if key[:10] >= today.isoformat()
        }

        current_messages = {f"{m.id}|{hash_text(m.subject + m.text)}": m for m in messages}
        for key, message in current_messages.items():
            if key in self._known_messages or first_run:
                continue
            self.hass.bus.async_fire(
                EVENT_MESSAGE,
                {
                    **base,
                    "id": message.id,
                    "subject": message.subject,
                    "text": html_to_text(message.text),
                    "html": message.text,
                },
            )
        # messages are per day, remembering today's set is sufficient
        self._known_messages = set(current_messages) | {
            key for key in self._known_messages if key in current_messages
        }


def hash_text(value: str) -> str:
    """Stable short hash (python's hash() is randomized per process)."""
    return hashlib.sha1(value.encode()).hexdigest()[:12]
