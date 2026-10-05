"""Test the anonymous WebUntis access without Home Assistant.

Usage:
    python tools/untis_cli.py "Schulname Ort"             # search school, list classes
    python tools/untis_cli.py "Schulname Ort" 5a          # + timetable & messages of class 5a
Requires: pip install aiohttp
"""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib.util
import pathlib
import sys

import aiohttp

_API = pathlib.Path(__file__).parents[1] / "custom_components" / "untis_public" / "api.py"
_spec = importlib.util.spec_from_file_location("untis_api", _API)
api = importlib.util.module_from_spec(_spec)
sys.modules["untis_api"] = api
_spec.loader.exec_module(api)


async def main(query: str, class_name: str | None) -> None:
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        schools = await api.async_search_schools(session, query)
        for i, school in enumerate(schools):
            print(f"[{i}] {school.label}  -> {school.server} / {school.login_name}")
        if not schools:
            return
        school = schools[0]
        print(f"\nUsing: {school.display_name}")

        client = api.UntisPublicClient(session, school.server, school.login_name)
        await client.async_login()
        classes = await client.async_get_classes()
        print("Classes:", ", ".join(c.name for c in classes))

        if class_name:
            school_class = next((c for c in classes if c.name.lower() == class_name.lower()), None)
            if school_class is None:
                print("Class not found")
                return
            today = dt.date.today()
            for week in (0, 1):
                periods = await client.async_get_week(school_class.id, today + dt.timedelta(weeks=week))
                for p in sorted(periods, key=lambda p: p.start):
                    print(
                        f"{p.start:%a %d.%m. %H:%M}-{p.end:%H:%M} "
                        f"{','.join(s.name for s in p.subjects):8} "
                        f"{','.join(t.name for t in p.teachers):10} "
                        f"{','.join(r.name for r in p.rooms):8} "
                        f"{p.cell_state} {p.flags} {p.substitution_text}"
                    )
            print("\nMessages of the day:")
            for m in await client.async_get_messages():
                print(f"- {m.subject}: {m.text[:300]}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
