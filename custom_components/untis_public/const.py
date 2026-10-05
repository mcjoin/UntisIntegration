"""Constants for the Untis Public integration."""

from __future__ import annotations

DOMAIN = "untis_public"

CONF_SERVER = "server"
CONF_SCHOOL = "school"  # login name of the school (e.g. "gym-musterstadt")
CONF_SCHOOL_DISPLAY = "school_display"
CONF_CLASS_ID = "class_id"
CONF_CLASS_NAME = "class_name"
CONF_SEARCH = "search"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_DAYS_AHEAD = "days_ahead"
CONF_TEACHER_NAMES = "teacher_names"  # {abbreviation: display name}
CONF_SUBJECT_NAMES = "subject_names"  # {abbreviation: display name}
CONF_ADDITIONAL = "additional"

# own lessons (AGs, Lerngruppen ...) not delivered by Untis
CONF_CUSTOM_LESSONS = "custom_lessons"  # {"1": {...}, "2": {...}, "3": {...}}
MAX_CUSTOM_LESSONS = 3
CL_NAME = "name"
CL_WEEKDAY = "weekday"  # "0" = Monday
CL_SLOT_FROM = "slot_from"
CL_SLOT_TO = "slot_to"
CL_START = "start"  # "HH:MM", resolved from the time grid when saving
CL_END = "end"
CL_TEACHER = "teacher"
CL_ROOM = "room"
CL_WEEKS = "weeks"  # every / even / odd (ISO calendar week)
CL_SCHOOL_DAYS_ONLY = "school_days_only"
WEEKS_EVERY = "every"
WEEKS_EVEN = "even"
WEEKS_ODD = "odd"

DEFAULT_SCAN_INTERVAL = 15  # minutes
DEFAULT_DAYS_AHEAD = 14

SCHOOL_SEARCH_URL = "https://mobile.webuntis.com/ms/schoolquery2"

EVENT_CHANGE = f"{DOMAIN}_change"
EVENT_MESSAGE = f"{DOMAIN}_message"

STORAGE_VERSION = 1

# Lesson status values exposed to Home Assistant
STATUS_REGULAR = "regular"
STATUS_CANCELLED = "cancelled"
STATUS_SUBSTITUTION = "substitution"
STATUS_ROOM_SUBSTITUTION = "room_substitution"
STATUS_ADDITIONAL = "additional"
STATUS_SHIFT = "shift"
STATUS_EXAM = "exam"
STATUS_EVENT = "event"
STATUS_CUSTOM = "custom"  # own lesson added in the options

CHANGED_STATUSES = {
    STATUS_CANCELLED,
    STATUS_SUBSTITUTION,
    STATUS_ROOM_SUBSTITUTION,
    STATUS_ADDITIONAL,
    STATUS_SHIFT,
    STATUS_EXAM,
    STATUS_EVENT,
}
