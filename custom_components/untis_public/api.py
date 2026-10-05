"""Minimal client for the anonymous (public) WebUntis API.

This module only depends on aiohttp so it can be tested outside Home Assistant.

Flow (the same one the Untis app / web client uses for "anonymous" access):
1. Search the school via the mobile school directory.
2. Log in anonymously via JSON-RPC (user ``#anonymous#``) -> JSESSIONID cookie.
3. Read classes, the weekly timetable and the messages of the day via
   ``/WebUntis/api/public/...``.
"""

from __future__ import annotations

import base64
import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

SCHOOL_SEARCH_URL = "https://mobile.webuntis.com/ms/schoolquery2"
ANONYMOUS_USER = "#anonymous#"
ANONYMOUS_OTP = 100170
USER_AGENT = "Mozilla/5.0 (HomeAssistant untis_public)"
TIMEOUT = aiohttp.ClientTimeout(total=30)

ELEMENT_CLASS = 1
ELEMENT_TEACHER = 2
ELEMENT_SUBJECT = 3
ELEMENT_ROOM = 4


class UntisError(Exception):
    """Generic Untis error."""


class UntisConnectionError(UntisError):
    """Server not reachable."""


class UntisAuthError(UntisError):
    """Anonymous access not possible (disabled by the school?)."""


class UntisTooManyResults(UntisError):
    """School search returned too many results."""


@dataclass
class School:
    """A school from the school directory."""

    server: str
    login_name: str
    display_name: str
    address: str = ""

    @property
    def label(self) -> str:
        return f"{self.display_name} ({self.address})" if self.address else self.display_name


@dataclass
class SchoolClass:
    """A class (Klasse)."""

    id: int
    name: str
    long_name: str = ""


@dataclass
class Element:
    """A timetable element (teacher, subject, room, class)."""

    type: int
    id: int
    name: str
    long_name: str = ""


@dataclass
class Period:
    """A raw timetable period, already resolved to names."""

    id: int
    lesson_id: int | None
    start: dt.datetime
    end: dt.datetime
    cell_state: str
    flags: dict[str, Any]
    subjects: list[Element] = field(default_factory=list)
    teachers: list[Element] = field(default_factory=list)
    rooms: list[Element] = field(default_factory=list)
    classes: list[Element] = field(default_factory=list)
    original_teachers: list[Element] = field(default_factory=list)
    original_rooms: list[Element] = field(default_factory=list)
    lesson_text: str = ""
    period_text: str = ""
    substitution_text: str = ""
    info: str = ""


@dataclass
class TimeSlot:
    """A period of the school's time grid (e.g. 1. Stunde 08:00-08:45)."""

    number: int
    label: str
    start: dt.time
    end: dt.time


@dataclass
class Message:
    """A message of the day."""

    id: int | str
    subject: str
    text: str


async def async_search_schools(session: aiohttp.ClientSession, query: str) -> list[School]:
    """Search schools in the public WebUntis school directory."""
    payload = {
        "id": "ha",
        "method": "searchSchool",
        "params": [{"search": query}],
        "jsonrpc": "2.0",
    }
    try:
        async with session.post(
            SCHOOL_SEARCH_URL, json=payload, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise UntisConnectionError(str(err)) from err

    if error := data.get("error"):
        if error.get("code") == -6003:
            raise UntisTooManyResults(error.get("message", ""))
        raise UntisError(error.get("message", str(error)))

    schools = []
    for item in (data.get("result") or {}).get("schools", []):
        server = item.get("server") or ""
        if not server and (url := item.get("serverUrl")):
            server = url.split("//", 1)[-1].split("/", 1)[0]
        schools.append(
            School(
                server=server,
                login_name=item.get("loginName", ""),
                display_name=item.get("displayName", item.get("loginName", "")),
                address=item.get("address", ""),
            )
        )
    return schools


class UntisPublicClient:
    """Anonymous WebUntis client for one school."""

    def __init__(self, session: aiohttp.ClientSession, server: str, school: str) -> None:
        self._session = session
        self.server = server.replace("https://", "").replace("http://", "").strip("/")
        self.school = school
        self._cookies: dict[str, str] = {}

    @property
    def _base(self) -> str:
        return f"https://{self.server}/WebUntis"

    def _headers(self) -> dict[str, str]:
        school_b64 = "_" + base64.b64encode(self.school.encode()).decode()
        cookies = {**self._cookies, "schoolname": f'"{school_b64}"'}
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        }

    def _store_cookies(self, resp: aiohttp.ClientResponse) -> None:
        for name, morsel in resp.cookies.items():
            self._cookies[name] = morsel.value

    # ------------------------------------------------------------------ auth
    async def async_login(self) -> None:
        """Log in anonymously and obtain a session cookie."""
        self._cookies = {}
        payload = {
            "id": "ha",
            "method": "getUserData2017",
            "params": [
                {
                    "auth": {
                        "clientTime": int(time.time() * 1000),
                        "user": ANONYMOUS_USER,
                        "otp": ANONYMOUS_OTP,
                    }
                }
            ],
            "jsonrpc": "2.0",
        }
        try:
            async with self._session.post(
                f"{self._base}/jsonrpc_intern.do",
                params={"m": "getUserData2017", "school": self.school, "v": "i2.2"},
                json=payload,
                headers=self._headers(),
                timeout=TIMEOUT,
            ) as resp:
                self._store_cookies(resp)
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UntisConnectionError(str(err)) from err

        if error := data.get("error"):
            raise UntisAuthError(error.get("message", str(error)))
        if "JSESSIONID" not in self._cookies:
            raise UntisAuthError("No session cookie received")

    # --------------------------------------------------------------- helpers
    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        try:
            async with self._session.get(
                f"{self._base}{path}", params=params, headers=self._headers(), timeout=TIMEOUT
            ) as resp:
                self._store_cookies(resp)
                if resp.status in (401, 403):
                    raise UntisAuthError(f"HTTP {resp.status} for {path}")
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UntisConnectionError(str(err)) from err
        if isinstance(data, dict) and data.get("isSessionTimeout"):
            raise UntisAuthError("Session timeout")
        return data

    # --------------------------------------------------------------- classes
    async def async_get_classes(self, date: dt.date | None = None) -> list[SchoolClass]:
        """Return the classes that have a public timetable."""
        date = date or dt.date.today()
        data = await self._get(
            "/api/public/timetable/weekly/pageconfig",
            {"type": ELEMENT_CLASS, "date": date.isoformat(), "isMyTimetableSelected": "false"},
        )
        elements = (data.get("data") or {}).get("elements") or []
        classes = [
            SchoolClass(
                id=int(el["id"]),
                name=el.get("name") or el.get("displayname") or str(el["id"]),
                long_name=el.get("longName") or "",
            )
            for el in elements
            if "id" in el
        ]
        return sorted(classes, key=lambda c: c.name)

    # ------------------------------------------------------------- timetable
    async def async_get_week(
        self, class_id: int, date: dt.date, tz: dt.tzinfo | None = None
    ) -> list[Period]:
        """Return all periods of the week containing ``date``."""
        params: dict[str, Any] = {
            "elementType": ELEMENT_CLASS,
            "elementId": class_id,
            "date": date.isoformat(),
            "formatId": 1,
        }
        try:
            data = await self._get("/api/public/timetable/weekly/data", params)
        except UntisConnectionError:
            # some servers reject formatId=1 -> retry without it
            params.pop("formatId")
            data = await self._get("/api/public/timetable/weekly/data", params)

        result = ((data.get("data") or {}).get("result") or {}).get("data") or {}
        if not result:
            error = ((data.get("data") or {}).get("error")) or data.get("error")
            if error:
                raise UntisError(str(error))
            return []

        elements: dict[tuple[int, int], Element] = {}
        for el in result.get("elements") or []:
            elements[(int(el.get("type", 0)), int(el.get("id", 0)))] = Element(
                type=int(el.get("type", 0)),
                id=int(el.get("id", 0)),
                name=el.get("name") or el.get("displayname") or "",
                long_name=el.get("longName") or el.get("alternatename") or "",
            )

        raw_periods = (result.get("elementPeriods") or {}).get(str(class_id)) or []
        return [self._parse_period(p, elements, tz) for p in raw_periods]

    @staticmethod
    def _parse_period(
        raw: dict[str, Any], elements: dict[tuple[int, int], Element], tz: dt.tzinfo | None
    ) -> Period:
        day = _parse_date(raw["date"])
        period = Period(
            id=int(raw.get("id", 0)),
            lesson_id=raw.get("lessonId"),
            start=dt.datetime.combine(day, _parse_time(raw["startTime"]), tzinfo=tz),
            end=dt.datetime.combine(day, _parse_time(raw["endTime"]), tzinfo=tz),
            cell_state=str(raw.get("cellState") or "STANDARD"),
            flags=raw.get("is") or {},
            lesson_text=raw.get("lessonText") or "",
            period_text=raw.get("periodText") or "",
            substitution_text=raw.get("substText") or "",
            info=raw.get("periodInfo") or "",
        )
        for ref in raw.get("elements") or []:
            el_type = int(ref.get("type", 0))
            el = elements.get((el_type, int(ref.get("id", 0))))
            org = elements.get((el_type, int(ref.get("orgId", 0) or 0)))
            if el_type == ELEMENT_SUBJECT and el:
                period.subjects.append(el)
            elif el_type == ELEMENT_TEACHER:
                if el:
                    period.teachers.append(el)
                if org and (not el or org.id != el.id):
                    period.original_teachers.append(org)
            elif el_type == ELEMENT_ROOM:
                if el:
                    period.rooms.append(el)
                if org and (not el or org.id != el.id):
                    period.original_rooms.append(org)
            elif el_type == ELEMENT_CLASS and el:
                period.classes.append(el)
        return period

    # ------------------------------------------------------------- time grid
    async def async_get_timegrid(self) -> list[TimeSlot]:
        """Return the school's time grid (normal lesson slots)."""
        slots: dict[tuple[int, int], TimeSlot] = {}
        try:
            data = await self._get("/api/public/timegrid", {})
            for idx, row in enumerate((data.get("data") or {}).get("rows") or [], start=1):
                key = (int(row["startTime"]), int(row["endTime"]))
                slots[key] = TimeSlot(
                    number=int(row.get("period") or idx),
                    label=str(row.get("description") or row.get("period") or idx),
                    start=_parse_time(row["startTime"]),
                    end=_parse_time(row["endTime"]),
                )
        except (UntisError, KeyError, ValueError) as err:
            _LOGGER.debug("Public timegrid not available: %s", err)

        if not slots:
            # fallback: JSON-RPC (works with the anonymous session on most servers)
            try:
                async with self._session.post(
                    f"{self._base}/jsonrpc.do",
                    params={"school": self.school},
                    json={"id": "ha", "method": "getTimegridUnits", "params": {}, "jsonrpc": "2.0"},
                    headers=self._headers(),
                    timeout=TIMEOUT,
                ) as resp:
                    result = (await resp.json(content_type=None)).get("result") or []
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                raise UntisConnectionError(str(err)) from err
            for day in result:
                for unit in day.get("timeUnits") or []:
                    key = (int(unit["startTime"]), int(unit["endTime"]))
                    slots.setdefault(
                        key,
                        TimeSlot(
                            number=0,
                            label=str(unit.get("name") or ""),
                            start=_parse_time(unit["startTime"]),
                            end=_parse_time(unit["endTime"]),
                        ),
                    )

        ordered = sorted(slots.values(), key=lambda s: s.start)
        for idx, slot in enumerate(ordered, start=1):
            slot.number = slot.number or idx
            slot.label = slot.label or str(idx)
        return ordered

    # -------------------------------------------------------------- messages
    async def async_get_messages(self, date: dt.date | None = None) -> list[Message]:
        """Return the messages of the day."""
        date = date or dt.date.today()
        data = await self._get(
            "/api/public/news/newsWidgetData", {"date": date.strftime("%Y%m%d")}
        )
        payload = data.get("data") or {}
        messages = [
            Message(
                id=m.get("id", idx),
                subject=m.get("subject") or "",
                text=m.get("text") or "",
            )
            for idx, m in enumerate(payload.get("messagesOfDay") or [])
        ]
        if system := payload.get("systemMessage"):
            if isinstance(system, dict):
                messages.insert(
                    0,
                    Message(
                        id=f"system-{system.get('id', 0)}",
                        subject=system.get("subject") or "System",
                        text=system.get("text") or "",
                    ),
                )
        return messages


def _parse_date(value: int | str) -> dt.date:
    value = str(value)
    return dt.date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _parse_time(value: int | str) -> dt.time:
    value = int(value)
    return dt.time(value // 100, value % 100)
