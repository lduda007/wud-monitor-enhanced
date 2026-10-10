"""Config flow for WUD Monitor."""

import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    API_CONTAINERS,
    CONF_ADD_INSTANCE_NAME,
    CONF_HOST,
    CONF_INSTANCE_NAME,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    CONF_TRIGGER_REFRESH_DELAY,
    CONF_TRIGGERS_EXCLUDED,
    CONF_USERNAME,
    DEFAULT_ADD_INSTANCE_NAME,
    DEFAULT_INSTANCE_NAME,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_TRIGGER_REFRESH_DELAY,
    DEFAULT_TRIGGERS_EXCLUDED,
    DOMAIN,
)
from .coordinator import WUDCoordinator, build_auth

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
            # Credentials are optional (WUD < 9.0 may run without auth). They use
            # suggested_value rather than default so clearing the field removes them.
            vol.Optional(
                CONF_USERNAME,
                description={"suggested_value": defaults.get(CONF_USERNAME)},
            ): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username")
            ),
            vol.Optional(
                CONF_PASSWORD,
                description={"suggested_value": defaults.get(CONF_PASSWORD)},
            ): TextSelector(
                TextSelectorConfig(
                    type=TextSelectorType.PASSWORD, autocomplete="current-password"
                )
            ),
            vol.Required(
                CONF_INSTANCE_NAME, default=defaults.get(CONF_INSTANCE_NAME, DEFAULT_INSTANCE_NAME)
            ): str,
            vol.Required(
                CONF_POLL_INTERVAL, default=defaults.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
            ): vol.All(int, vol.Range(min=1, max=1440)),
            vol.Required(
                CONF_TRIGGER_REFRESH_DELAY,
                default=defaults.get(
                    CONF_TRIGGER_REFRESH_DELAY, DEFAULT_TRIGGER_REFRESH_DELAY
                ),
            ): vol.All(int, vol.Range(min=-1, max=3600)),
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


def _clean_input(user_input: dict) -> dict:
    """Drop empty optional credentials so an unprotected WUD sends no auth header."""
    data = dict(user_input)
    for key in (CONF_USERNAME, CONF_PASSWORD):
        if not data.get(key):
            data.pop(key, None)
    return data


async def _test_connection(data: dict) -> str | None:
    """Test that we can reach the WUD API with the given credentials.

    Returns None on success, otherwise the error key to show on the form.
    """
    username = data.get(CONF_USERNAME)
    auth = build_auth(username, data.get(CONF_PASSWORD))
    url = f"http://{data[CONF_HOST]}:{data[CONF_PORT]}{API_CONTAINERS}"
    try:
        async with aiohttp.ClientSession(auth=auth) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    return None
                if response.status in (401, 403):
                    # WUD 9.0+ enforces authentication on the REST API.
                    return "invalid_auth" if username else "auth_required"
                return "cannot_connect"
    except Exception:  # noqa: BLE001
        return "cannot_connect"


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
            data = _clean_input(user_input)
            if error := await _test_connection(data):
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=user_input[CONF_INSTANCE_NAME],
                    data=data,
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
    """Handle options updates (host, port, credentials, poll interval)."""

    async def async_step_init(self, user_input: dict | None = None):
        """Handle the options step."""
        errors = {}

        if user_input is not None:
            # Re-test the connection in case host/port/credentials changed
            data = _clean_input(user_input)
            if error := await _test_connection(data):
                errors["base"] = error
            else:
                # Update the config entry data and reload the integration
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=data
                )
                return self.async_create_entry(title="", data={})

        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        trigger_options = _collect_trigger_options(coordinator)

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(self.config_entry.data, trigger_options),
            errors=errors,
        )
