"""Untis Public – anonymous WebUntis timetable for Home Assistant."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import UntisConfigEntry, UntisCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.CALENDAR]


async def async_setup_entry(hass: HomeAssistant, entry: UntisConfigEntry) -> bool:
    """Set up Untis Public from a config entry."""
    coordinator = UntisCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: UntisConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: UntisConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
