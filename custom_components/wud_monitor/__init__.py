"""WUD Monitor — Home Assistant integration for What's Up Docker."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_HOST,
    CONF_INSTANCE_NAME,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    CONF_USERNAME,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .coordinator import WUDCoordinator
from .sensor import _build_controller_device

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up WUD Monitor from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    poll_interval = entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    coordinator = WUDCoordinator(
        hass,
        host,
        port,
        poll_interval,
        username=entry.data.get(CONF_USERNAME),
        password=entry.data.get(CONF_PASSWORD),
    )

    # Perform the first refresh before setting up platforms so entities have data immediately
    await coordinator.async_config_entry_first_refresh()

    # Register the Controller device up front so compose project devices can
    # reference it by device id (via_device_id) when their entities are added.
    controller_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        **_build_controller_device(entry.entry_id, entry.data[CONF_INSTANCE_NAME]),
    )
    coordinator.controller_device_id = controller_device.id

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry when its configuration changes (e.g. options flow edits),
    # so entity changes such as newly excluded triggers take effect immediately.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry after its options/data are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a WUD Monitor config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
