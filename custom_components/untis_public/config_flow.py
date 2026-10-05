"""Config flow for Untis Public: search school -> pick school -> pick class."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .api import (
    School,
    SchoolClass,
    TimeSlot,
    UntisAuthError,
    UntisConnectionError,
    UntisError,
    UntisPublicClient,
    UntisTooManyResults,
    async_search_schools,
)
from .const import (
    CL_END,
    CL_NAME,
    CL_ROOM,
    CL_SCHOOL_DAYS_ONLY,
    CL_SLOT_FROM,
    CL_SLOT_TO,
    CL_START,
    CL_TEACHER,
    CL_WEEKDAY,
    CL_WEEKS,
    CONF_ADDITIONAL,
    CONF_CLASS_ID,
    CONF_CLASS_NAME,
    CONF_CUSTOM_LESSONS,
    CONF_DAYS_AHEAD,
    CONF_SCAN_INTERVAL,
    CONF_SCHOOL,
    CONF_SCHOOL_DISPLAY,
    CONF_SEARCH,
    CONF_SERVER,
    CONF_SUBJECT_NAMES,
    CONF_TEACHER_NAMES,
    DEFAULT_DAYS_AHEAD,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_CUSTOM_LESSONS,
    STATUS_CUSTOM,
    WEEKS_EVEN,
    WEEKS_EVERY,
    WEEKS_ODD,
)

_LOGGER = logging.getLogger(__name__)


class UntisPublicConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._schools: list[School] = []
        self._school: School | None = None
        self._classes: list[SchoolClass] = []

    def _session(self) -> aiohttp.ClientSession:
        return async_create_clientsession(self.hass, cookie_jar=aiohttp.DummyCookieJar())

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose between school search and manual entry."""
        return self.async_show_menu(step_id="user", menu_options=["search", "manual"])

    async def async_step_search(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: search the school by name / city."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._schools = await async_search_schools(
                    self._session(), user_input[CONF_SEARCH].strip()
                )
            except UntisTooManyResults:
                errors["base"] = "too_many_results"
            except UntisConnectionError:
                errors["base"] = "cannot_connect"
            except UntisError:
                _LOGGER.exception("School search failed")
                errors["base"] = "unknown"
            else:
                if not self._schools:
                    errors["base"] = "no_results"
                elif len(self._schools) == 1:
                    self._school = self._schools[0]
                    return await self.async_step_select_class()
                else:
                    return await self.async_step_select_school()

        return self.async_show_form(
            step_id="search",
            data_schema=vol.Schema({vol.Required(CONF_SEARCH): str}),
            errors=errors,
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Alternative: enter server + school login name manually."""
        if user_input is not None:
            self._school = School(
                server=user_input[CONF_SERVER],
                login_name=user_input[CONF_SCHOOL],
                display_name=user_input[CONF_SCHOOL],
            )
            return await self.async_step_select_class()
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {vol.Required(CONF_SERVER): str, vol.Required(CONF_SCHOOL): str}
            ),
        )

    async def async_step_select_school(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: choose one school of the search results."""
        if user_input is not None:
            index = int(user_input[CONF_SCHOOL])
            self._school = self._schools[index]
            return await self.async_step_select_class()

        options = [
            SelectOptionDict(value=str(i), label=school.label)
            for i, school in enumerate(self._schools)
        ]
        return self.async_show_form(
            step_id="select_school",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCHOOL): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
        )

    async def async_step_select_class(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: choose the class."""
        assert self._school is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            class_id = int(user_input[CONF_CLASS_ID])
            school_class = next(c for c in self._classes if c.id == class_id)
            await self.async_set_unique_id(
                f"{self._school.server}_{self._school.login_name}_{school_class.name}".lower()
            )
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"{school_class.name} – {self._school.display_name}",
                data={
                    CONF_SERVER: self._school.server,
                    CONF_SCHOOL: self._school.login_name,
                    CONF_SCHOOL_DISPLAY: self._school.display_name,
                    CONF_CLASS_ID: school_class.id,
                    CONF_CLASS_NAME: school_class.name,
                },
            )

        if not self._classes:
            client = UntisPublicClient(
                self._session(), self._school.server, self._school.login_name
            )
            try:
                await client.async_login()
                self._classes = await client.async_get_classes()
            except UntisAuthError:
                return self.async_abort(reason="anonymous_disabled")
            except UntisConnectionError:
                return self.async_abort(reason="cannot_connect")
            except UntisError:
                _LOGGER.exception("Loading classes failed")
                return self.async_abort(reason="unknown")
            if not self._classes:
                return self.async_abort(reason="no_classes")

        options = [
            SelectOptionDict(
                value=str(c.id),
                label=f"{c.name} ({c.long_name})" if c.long_name and c.long_name != c.name else c.name,
            )
            for c in self._classes
        ]
        return self.async_show_form(
            step_id="select_class",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CLASS_ID): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                    )
                }
            ),
            description_placeholders={"school": self._school.display_name},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return UntisPublicOptionsFlow()


class UntisPublicOptionsFlow(OptionsFlow):
    """Options: settings and renaming of teacher/subject abbreviations."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "teachers", "subjects", "custom_lessons"]
        )

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    def _seen(self, attr: str) -> dict[str, str]:
        """Abbreviations seen in the current timetable (abbreviation -> long name)."""
        coordinator = getattr(self.config_entry, "runtime_data", None)
        return dict(getattr(coordinator, attr, {}) or {})

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(
                **{
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_DAYS_AHEAD: int(user_input[CONF_DAYS_AHEAD]),
                }
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=5, max=240, step=5, unit_of_measurement="min",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_DAYS_AHEAD,
                        default=options.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=35, step=1, unit_of_measurement="d",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )

    async def async_step_teachers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self._rename_step("teachers", CONF_TEACHER_NAMES, "seen_teachers", user_input)

    async def async_step_subjects(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self._rename_step("subjects", CONF_SUBJECT_NAMES, "seen_subjects", user_input)

    def _rename_step(
        self, step_id: str, option: str, seen_attr: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """One text field per abbreviation + a free text field for further ones."""
        current: dict[str, str] = dict(self.config_entry.options.get(option, {}))
        seen = self._seen(seen_attr)
        abbreviations = sorted(set(seen) | set(current), key=str.casefold)

        if user_input is not None:
            names = {
                abbr: value.strip()
                for abbr in abbreviations
                if (value := user_input.get(abbr)) and value.strip() and value.strip() != abbr
            }
            names.update(parse_name_lines(user_input.get(CONF_ADDITIONAL, "")))
            return self._save(**{option: names})

        schema: dict[Any, Any] = {
            vol.Optional(abbr, description={"suggested_value": current.get(abbr)}): str
            for abbr in abbreviations
        }
        schema[vol.Optional(CONF_ADDITIONAL)] = TextSelector(TextSelectorConfig(multiline=True))
        hints = [f"{abbr} = {long}" for abbr, long in sorted(seen.items()) if long and long != abbr]
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(schema),
            description_placeholders={
                "count": str(len(abbreviations)),
                "hints": "\n".join(hints) if hints else "-",
            },
        )

    # ------------------------------------------------- own lessons (AGs ...)
    async def async_step_custom_lessons(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Menu with the slots for own lessons."""
        german = self.hass.config.language.startswith("de")
        custom = self.config_entry.options.get(CONF_CUSTOM_LESSONS, {})
        days = WEEKDAYS_DE if german else WEEKDAYS_EN
        menu: dict[str, str] = {}
        for number in range(1, MAX_CUSTOM_LESSONS + 1):
            entry = custom.get(str(number)) or {}
            prefix = f"{'Eigene Stunde' if german else 'Own lesson'} {number}: "
            if entry.get(CL_NAME):
                menu[f"custom_{number}"] = (
                    f"{prefix}{entry[CL_NAME]} – {days[int(entry[CL_WEEKDAY])][:2]} "
                    f"{entry[CL_START]}–{entry[CL_END]}"
                )
            else:
                menu[f"custom_{number}"] = prefix + ("(frei)" if german else "(empty)")
        return self.async_show_menu(step_id="custom_lessons", menu_options=menu)

    async def async_step_custom_1(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._custom_lesson_step(1, user_input)

    async def async_step_custom_2(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._custom_lesson_step(2, user_input)

    async def async_step_custom_3(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._custom_lesson_step(3, user_input)

    async def _async_timegrid(self) -> list[TimeSlot]:
        """Time grid of the school; falls back to the slots seen in the timetable."""
        if getattr(self, "_timegrid", None):
            return self._timegrid
        data = self.config_entry.data
        client = UntisPublicClient(
            async_create_clientsession(self.hass, cookie_jar=aiohttp.DummyCookieJar()),
            data[CONF_SERVER],
            data[CONF_SCHOOL],
        )
        slots: list[TimeSlot] = []
        try:
            await client.async_login()
            slots = await client.async_get_timegrid()
        except UntisError as err:
            _LOGGER.warning("Could not load time grid: %s", err)
        if not slots:
            coordinator = getattr(self.config_entry, "runtime_data", None)
            seen = sorted(
                {
                    (lesson.start.time(), lesson.end.time())
                    for lesson in (coordinator.data.lessons if coordinator and coordinator.data else [])
                    if lesson.status != STATUS_CUSTOM
                }
            )
            slots = [
                TimeSlot(number=i, label=str(i), start=start, end=end)
                for i, (start, end) in enumerate(seen, start=1)
            ]
        self._timegrid = slots
        return slots

    async def _custom_lesson_step(
        self, number: int, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        step_id = f"custom_{number}"
        custom: dict[str, dict[str, Any]] = {
            key: dict(value)
            for key, value in self.config_entry.options.get(CONF_CUSTOM_LESSONS, {}).items()
        }
        current = custom.get(str(number), {})
        slots = await self._async_timegrid()
        if not slots:
            return self.async_abort(reason="no_timegrid")
        by_number = {str(slot.number): slot for slot in slots}
        errors: dict[str, str] = {}

        if user_input is not None:
            name = (user_input.get(CL_NAME) or "").strip()
            if not name:
                # empty name = remove this own lesson
                custom.pop(str(number), None)
                return self._save(**{CONF_CUSTOM_LESSONS: custom})
            slot_from = by_number[user_input[CL_SLOT_FROM]]
            slot_to = by_number.get(user_input.get(CL_SLOT_TO) or "", slot_from)
            if slot_to.start < slot_from.start:
                errors[CL_SLOT_TO] = "slot_order"
            else:
                custom[str(number)] = {
                    CL_NAME: name,
                    CL_WEEKDAY: user_input[CL_WEEKDAY],
                    CL_SLOT_FROM: str(slot_from.number),
                    CL_SLOT_TO: str(slot_to.number),
                    CL_START: slot_from.start.strftime("%H:%M"),
                    CL_END: slot_to.end.strftime("%H:%M"),
                    CL_TEACHER: (user_input.get(CL_TEACHER) or "").strip(),
                    CL_ROOM: (user_input.get(CL_ROOM) or "").strip(),
                    CL_WEEKS: user_input.get(CL_WEEKS, WEEKS_EVERY),
                    CL_SCHOOL_DAYS_ONLY: user_input.get(CL_SCHOOL_DAYS_ONLY, True),
                }
                return self._save(**{CONF_CUSTOM_LESSONS: custom})
            current = user_input

        german = self.hass.config.language.startswith("de")
        slot_options = [
            SelectOptionDict(
                value=str(slot.number),
                label=(
                    f"{slot.label}. {'Stunde' if german else 'period'} "
                    f"({slot.start:%H:%M}–{slot.end:%H:%M})"
                ),
            )
            for slot in slots
        ]
        slot_select = SelectSelector(
            SelectSelectorConfig(options=slot_options, mode=SelectSelectorMode.DROPDOWN)
        )

        def suggested(key: str, default: Any = None) -> dict[str, Any]:
            return {"suggested_value": current.get(key, default)}

        schema = vol.Schema(
            {
                vol.Optional(CL_NAME, description=suggested(CL_NAME)): str,
                vol.Required(CL_WEEKDAY, default=current.get(CL_WEEKDAY, "0")): SelectSelector(
                    SelectSelectorConfig(
                        options=[str(day) for day in range(7)],
                        translation_key="weekday",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CL_SLOT_FROM,
                    default=current.get(CL_SLOT_FROM, slot_options[0]["value"]),
                ): slot_select,
                vol.Optional(CL_SLOT_TO, description=suggested(CL_SLOT_TO)): slot_select,
                vol.Optional(CL_TEACHER, description=suggested(CL_TEACHER)): str,
                vol.Optional(CL_ROOM, description=suggested(CL_ROOM)): str,
                vol.Required(CL_WEEKS, default=current.get(CL_WEEKS, WEEKS_EVERY)): SelectSelector(
                    SelectSelectorConfig(
                        options=[WEEKS_EVERY, WEEKS_EVEN, WEEKS_ODD],
                        translation_key="weeks",
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CL_SCHOOL_DAYS_ONLY, default=current.get(CL_SCHOOL_DAYS_ONLY, True)
                ): bool,
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
            description_placeholders={"number": str(number)},
        )


WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def parse_name_lines(text: str) -> dict[str, str]:
    """Parse lines like ``MUE = Frau Müller`` (also ``:`` as separator)."""
    names: dict[str, str] = {}
    for line in text.splitlines():
        for sep in ("=", ":"):
            if sep in line:
                abbr, name = (part.strip() for part in line.split(sep, 1))
                if abbr and name:
                    names[abbr] = name
                break
    return names
