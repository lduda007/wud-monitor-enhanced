"""DataUpdateCoordinator for WUD Monitor."""

import logging
from datetime import datetime, timedelta, timezone

import aiohttp
from awesomeversion import AwesomeVersion
from awesomeversion.exceptions import AwesomeVersionException

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_APP,
    API_CONTAINER_RUN_TRIGGER,
    API_CONTAINER_SNOOZE,
    API_CONTAINER_TRIGGERS,
    API_CONTAINERS,
    DOMAIN,
    RUN_TRIGGER_TIMEOUT,
    SNOOZE_MIN_VERSION,
)

_LOGGER = logging.getLogger(__name__)


def build_auth(username: str | None, password: str | None) -> aiohttp.BasicAuth | None:
    """Return HTTP Basic auth for the given credentials, or None when unset.

    Credentials are optional: WUD before 9.0.0 may run without authentication,
    in which case no Authorization header is sent at all.
    """
    if not username:
        return None
    return aiohttp.BasicAuth(username, password or "")


async def _async_read_error(response: aiohttp.ClientResponse) -> str:
    """Return the error detail from a failed WUD response.

    WUD 9 returns ``{"error": "<ErrorType>", "message": "<Details>"}``; older
    versions return a flat ``{"error": "..."}`` or plain text.
    """
    try:
        data = await response.json(content_type=None)
    except (aiohttp.ClientError, ValueError):
        return await response.text()
    if isinstance(data, dict):
        return str(data.get("message") or data.get("error") or data)
    return str(data)


class WUDCoordinator(DataUpdateCoordinator):
    """Coordinator that fetches all container data from WUD in a single API call."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        poll_interval: int,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        """Initialize the coordinator."""
        self.host = host
        self.port = port
        self._base_url = f"http://{host}:{port}"
        self._auth = build_auth(username, password)

        # WUD version reported by GET /api/app, refreshed on each poll.
        self.wud_version: str | None = None

        self.last_poll_time: object = None  # Set on each successful poll
        # Device registry id of the Controller device, set in async_setup_entry
        # before the platforms are set up; compose project devices link to it.
        self.controller_device_id: str | None = None
        # Cache of available triggers per container id, populated on each poll
        # for containers whose triggerInclude is empty.
        self.container_triggers: dict[str, list[str]] = {}
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=poll_interval),
        )

    def _session(self) -> aiohttp.ClientSession:
        """Return a new client session sending the configured credentials, if any."""
        return aiohttp.ClientSession(auth=self._auth)

    @property
    def supports_snooze(self) -> bool:
        """Return True when the WUD instance supports snoozing updates (9.1.0+).

        Decided from the version reported by /api/app; if that is unknown or not
        a parsable version (e.g. a dev build), fall back to whether the container
        payload carries the ``isSnoozed`` property.
        """
        if self.wud_version:
            try:
                return AwesomeVersion(self.wud_version) >= AwesomeVersion(SNOOZE_MIN_VERSION)
            except AwesomeVersionException:
                pass
        return any("isSnoozed" in c for c in self.data or [])

    async def _async_update_data(self) -> list[dict]:
        """Fetch container data from WUD API. Called by the coordinator on each poll."""
        url = f"{self._base_url}{API_CONTAINERS}"
        try:
            async with self._session() as session:
                version = await self._async_fetch_version(session)
                if version:
                    self.wud_version = version
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status in (401, 403):
                        raise UpdateFailed(
                            f"WUD rejected the request (HTTP {response.status}): "
                            f"{await _async_read_error(response)}. WUD 9.0+ requires a "
                            "username and password — check them in the integration settings"
                        )
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
        except (TimeoutError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"Error communicating with WUD at {self._base_url}: {err}") from err

    async def _async_fetch_version(self, session: aiohttp.ClientSession) -> str | None:
        """Return the WUD version from GET /api/app, or None if unavailable."""
        url = f"{self._base_url}{API_APP}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status != 200:
                    _LOGGER.debug("WUD app API returned HTTP %s", response.status)
                    return None
                data = await response.json()
                version = data.get("version") if isinstance(data, dict) else None
                return str(version) if version else None
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            _LOGGER.debug("Failed to fetch WUD version: %s", err)
            return None

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
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
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
        except (TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.warning("Failed to fetch triggers for container %s: %s", container_id, err)
            return None

    def available_triggers_for(self, container: dict) -> list[str]:
        """Return the normalized list of triggers available for a container.

        Prefer the container's own ``triggerInclude`` when set (a comma-separated
        string in the WUD payload); otherwise fall back to the triggers fetched
        from the WUD triggers API and cached (keyed by the live container id).
        Trigger identifiers are of the form ``{type}.{name}`` (e.g. ``docker.local``).
        """
        raw = container.get("triggerInclude") or self.container_triggers.get(
            container.get("id")
        )
        if not raw:
            return []
        if isinstance(raw, str):
            return [t.strip() for t in raw.split(",") if t.strip()]
        return [str(t) for t in raw if t]

    async def async_run_container_trigger(
        self, container_id: str, trigger_type: str, trigger_name: str
    ) -> bool:
        """Run a specific trigger on a container.

        Calls POST /api/containers/{id}/triggers/{triggerType}/{triggerName}.
        """
        url = f"{self._base_url}{API_CONTAINER_RUN_TRIGGER.format(container_id=container_id, trigger_type=trigger_type, trigger_name=trigger_name)}"
        try:
            async with self._session() as session:
                async with session.post(
                    url, timeout=aiohttp.ClientTimeout(total=RUN_TRIGGER_TIMEOUT)
                ) as response:
                    return response.status in (200, 202, 204)
        except (TimeoutError, aiohttp.ClientError) as err:
            # A timeout usually means the trigger is still running on the WUD
            # side rather than that it failed, so log at warning level.
            _LOGGER.warning(
                "Trigger %s.%s for container %s did not complete cleanly: %s",
                trigger_type,
                trigger_name,
                container_id,
                err,
            )
            return False

    async def async_trigger_scan_all(self) -> bool:
        """Trigger a scan of all containers via POST /api/containers/watch."""
        from .const import API_CONTAINERS_WATCH
        url = f"{self._base_url}{API_CONTAINERS_WATCH}"
        try:
            async with self._session() as session:
                async with session.post(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    return response.status in (200, 202, 204)
        except (TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.error("Failed to trigger WUD scan all: %s", err)
            return False

    async def async_trigger_scan_container(self, container_id: str) -> bool:
        """Trigger a scan for a specific container via GET /api/containers/{id}/watch."""
        from .const import API_CONTAINER_WATCH
        url = f"{self._base_url}{API_CONTAINER_WATCH.format(container_id=container_id)}"
        try:
            async with self._session() as session:
                async with session.post(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    return response.status in (200, 202, 204)
        except (TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.error("Failed to trigger WUD scan for container %s: %s", container_id, err)
            return False

    async def async_snooze_container(
        self,
        container_id: str,
        version: str | None = None,
        until: datetime | None = None,
    ) -> bool:
        """Snooze a container's update via POST /api/containers/{id}/snooze.

        Without ``version`` WUD snoozes the currently offered update (its
        ``result.tag`` or digest); without ``until`` it is snoozed indefinitely.
        """
        body: dict = {}
        if version:
            body["version"] = version
        if until is not None:
            # WUD expects an epoch timestamp in milliseconds.
            body["until"] = int(until.timestamp() * 1000)
        return await self._async_snooze_request("POST", container_id, body)

    async def async_unsnooze_container(self, container_id: str) -> bool:
        """Remove a container's snooze via DELETE /api/containers/{id}/snooze."""
        return await self._async_snooze_request("DELETE", container_id, None)

    async def _async_snooze_request(
        self, method: str, container_id: str, body: dict | None
    ) -> bool:
        """Send a snooze/unsnooze request and log WUD's error detail on failure."""
        url = f"{self._base_url}{API_CONTAINER_SNOOZE.format(container_id=container_id)}"
        try:
            async with self._session() as session:
                async with session.request(
                    method, url, json=body, timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status == 200:
                        return True
                    _LOGGER.error(
                        "WUD %s %s for container %s failed with HTTP %s: %s",
                        method,
                        API_CONTAINER_SNOOZE,
                        container_id,
                        response.status,
                        await _async_read_error(response),
                    )
                    return False
        except (TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.error("Failed to reach WUD to snooze container %s: %s", container_id, err)
            return False
