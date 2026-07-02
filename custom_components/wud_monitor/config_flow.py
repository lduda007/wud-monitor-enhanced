"""Config flow for WUD Monitor."""

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_ADD_INSTANCE_NAME,
    CONF_HOST,
    CONF_INSTANCE_NAME,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    CONF_TRIGGERS_EXCLUDED,
    DEFAULT_ADD_INSTANCE_NAME,
    DEFAULT_INSTANCE_NAME,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_TRIGGERS_EXCLUDED,
    DOMAIN,
)
from .coordinator import WUDCoordinator

_LOGGER = logging.getLogger(__name__)


def _build_schema(defaults: dict, trigger_options: list[str] | None = None) -> vol.Schema:
    """Build the configuration schema with optional defaults pre-filled.

    ``trigger_options`` are the trigger identifiers discovered across all
    monitored containers; they populate the "triggers excluded" selector so the
    user can pick from known triggers (custom values are also allowed).
    """
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                int, vol.Range(min=1, max=65535)
            ),
            vol.Required(
                CONF_INSTANCE_NAME, default=defaults.get(CONF_INSTANCE_NAME, DEFAULT_INSTANCE_NAME)
            ): str,
            vol.Required(
                CONF_POLL_INTERVAL, default=defaults.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
            ): vol.All(int, vol.Range(min=1, max=1440)),
            vol.Optional(
                CONF_TRIGGERS_EXCLUDED,
                default=defaults.get(CONF_TRIGGERS_EXCLUDED, DEFAULT_TRIGGERS_EXCLUDED),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=trigger_options or [],
                    multiple=True,
                    custom_value=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_ADD_INSTANCE_NAME,
                default=defaults.get(CONF_ADD_INSTANCE_NAME, DEFAULT_ADD_INSTANCE_NAME),
            ): bool,
        }
    )


def _collect_trigger_options(coordinator: WUDCoordinator | None) -> list[str]:
    """Return the sorted set of trigger identifiers across all containers."""
    if coordinator is None:
        return []
    seen: set[str] = set()
    for container in coordinator.data or []:
        seen.update(coordinator.available_triggers_for(container))
    return sorted(seen)


async def _test_connection(host: str, port: int) -> bool:
    """Test that we can reach the WUD API and get a valid response."""
    coordinator = WUDCoordinator(None, host, port, DEFAULT_POLL_INTERVAL)
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = f"http://{host}:{port}/api/containers"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                return response.status == 200
    except Exception:  # noqa: BLE001
        return False


class WUDMonitorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        """Handle the user initiation step."""
        errors = {}

        if user_input is not None:
            # Prevent duplicate entries for the same WUD instance
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()

            # Verify the connection before saving
            if not await _test_connection(user_input[CONF_HOST], user_input[CONF_PORT]):
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_INSTANCE_NAME],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return WUDMonitorOptionsFlow()


class WUDMonitorOptionsFlow(config_entries.OptionsFlow):
    """Handle options updates (host, port, poll interval)."""

    async def async_step_init(self, user_input: dict | None = None):
        """Handle the options step."""
        errors = {}

        if user_input is not None:
            # Re-test the connection in case host/port changed
            if not await _test_connection(user_input[CONF_HOST], user_input[CONF_PORT]):
                errors["base"] = "cannot_connect"
            else:
                # Update the config entry data and reload the integration
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=user_input
                )
                return self.async_create_entry(title="", data={})

        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        trigger_options = _collect_trigger_options(coordinator)

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(self.config_entry.data, trigger_options),
            errors=errors,
        )
