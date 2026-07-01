"""DataUpdateCoordinator for WUD Monitor."""

import logging
from datetime import timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import API_CONTAINER_TRIGGERS, API_CONTAINERS, DOMAIN

_LOGGER = logging.getLogger(__name__)


class WUDCoordinator(DataUpdateCoordinator):
    """Coordinator that fetches all container data from WUD in a single API call."""

    def __init__(self, hass: HomeAssistant, host: str, port: int, poll_interval: int) -> None:
        """Initialize the coordinator."""
        self.host = host
        self.port = port
        self._base_url = f"http://{host}:{port}"

        self.last_poll_time: object = None  # Set on each successful poll
        # Cache of available triggers per container id, populated on each poll
        # for containers whose triggerInclude is empty.
        self.container_triggers: dict[str, list[str]] = {}
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=poll_interval),
        )

    async def _async_update_data(self) -> list[dict]:
        """Fetch container data from WUD API. Called by the coordinator on each poll."""
        from datetime import datetime, timezone
        url = f"{self._base_url}{API_CONTAINERS}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        raise UpdateFailed(f"WUD API returned HTTP {response.status}")
                    data = await response.json()
                    # API returns either a list or a dict with an "items" key
                    result = data if isinstance(data, list) else data.get("items", [])
                # Refresh the per-container available-triggers cache. Only containers
                # without an explicit triggerInclude need the extra API call.
                self.container_triggers = await self._async_fetch_all_triggers(session, result)
                # Store poll time only on success
                self.last_poll_time = datetime.now(timezone.utc)
                return result
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error communicating with WUD at {self._base_url}: {err}") from err

    async def _async_fetch_all_triggers(
        self, session: aiohttp.ClientSession, containers: list[dict]
    ) -> dict[str, list[str]]:
        """Fetch available triggers for every container lacking a triggerInclude."""
        triggers_map: dict[str, list[str]] = {}
        for container in containers:
            if container.get("triggerInclude"):
                continue
            container_id = container.get("id")
            if not container_id:
                continue
            triggers = await self._async_fetch_container_triggers(session, container_id)
            if triggers is not None:
                triggers_map[container_id] = triggers
        return triggers_map

    async def _async_fetch_container_triggers(
        self, session: aiohttp.ClientSession, container_id: str
    ) -> list[str] | None:
        """Fetch the triggers associated to a single container.

        Calls GET /api/containers/{id}/triggers and returns a list of trigger
        identifiers, or None if the call fails.
        """
        url = f"{self._base_url}{API_CONTAINER_TRIGGERS.format(container_id=container_id)}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    _LOGGER.debug(
                        "WUD triggers API returned HTTP %s for container %s",
                        response.status,
                        container_id,
                    )
                    return None
                data = await response.json()
                triggers = data if isinstance(data, list) else data.get("items", [])
                return [t.get("id") or t.get("name") for t in triggers if isinstance(t, dict)]
        except aiohttp.ClientError as err:
            _LOGGER.warning("Failed to fetch triggers for container %s: %s", container_id, err)
            return None

    async def async_trigger_scan_all(self) -> bool:
        """Trigger a scan of all containers via POST /api/containers/watch."""
        from .const import API_CONTAINERS_WATCH
        url = f"{self._base_url}{API_CONTAINERS_WATCH}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    return response.status in (200, 202, 204)
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to trigger WUD scan all: %s", err)
            return False

    async def async_trigger_scan_container(self, container_id: str) -> bool:
        """Trigger a scan for a specific container via GET /api/containers/{id}/watch."""
        from .const import API_CONTAINER_WATCH
        url = f"{self._base_url}{API_CONTAINER_WATCH.format(container_id=container_id)}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    return response.status in (200, 202, 204)
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to trigger WUD scan for container %s: %s", container_id, err)
            return False
