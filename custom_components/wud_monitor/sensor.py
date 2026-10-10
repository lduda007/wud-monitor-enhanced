"""Sensor platform for WUD Monitor."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import voluptuous as vol

from homeassistant.util import dt as dt_util

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_DURATION,
    ATTR_UNTIL,
    ATTR_VERSION,
    CONF_ADD_INSTANCE_NAME,
    CONF_INSTANCE_NAME,
    CONTROLLER_DEVICE_SUFFIX,
    DEFAULT_ADD_INSTANCE_NAME,
    DOMAIN,
    SERVICE_SNOOZE_UPDATE,
    SERVICE_UNSNOOZE_UPDATE,
    SNOOZE_MIN_VERSION,
)
from .coordinator import WUDCoordinator

_LOGGER = logging.getLogger(__name__)

# HA 2026.8 added DeviceInfo "via_device_id" and deprecated "via_device" (removed
# in 2027.8). Older HA versions only understand "via_device".
_SUPPORTS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__

# Schema for the snooze_update action: an absolute end ("until") or a relative
# one ("duration"), never both; neither means "snooze indefinitely".
SNOOZE_UPDATE_SCHEMA = {
    vol.Exclusive(ATTR_UNTIL, "snooze_end", msg="Set either until or duration, not both"): cv.datetime,
    vol.Exclusive(ATTR_DURATION, "snooze_end", msg="Set either until or duration, not both"): cv.positive_time_period,
    vol.Optional(ATTR_VERSION): cv.string,
}


def _name_prefix(instance_name: str, add_instance_name: bool) -> str:
    """Return an instance-name prefix for per-container entity names.

    When enabled, per-container sensors/buttons are prefixed with the instance
    name so entities from multiple WUD instances can be told apart.
    Controller-level entities already embed the instance name in their names.
    """
    return f"{instance_name} " if add_instance_name else ""


def _get_compose_project(container: dict) -> str | None:
    """Extract the Docker Compose project name from container labels."""
    labels = container.get("labels", {}) or {}
    return labels.get("com.docker.compose.project")


def _get_current_version(container: dict) -> str:
    """Return the currently running version from image.tag.value."""
    image = container.get("image", {}) or {}
    tag = image.get("tag", {}) or {}
    return tag.get("value", "unknown")


def _get_new_version(container: dict) -> str | None:
    """
    Return the available update version.
    Prefers updateKind.remoteValue, falls back to result.tag.
    Returns None if no update is available.
    """
    update_kind = container.get("updateKind", {}) or {}
    remote = update_kind.get("remoteValue")
    if remote:
        return remote
    result = container.get("result", {}) or {}
    tag = result.get("tag")
    if tag and tag != _get_current_version(container):
        return tag
    return None


def _get_release_notes_url(container: dict) -> str | None:
    """
    Return a link to the container's release notes / changelog.
    Prefers result.link (the link WUD resolved for the update), falling back to
    the container's top-level link and finally the linkTemplate.
    """
    result = container.get("result", {}) or {}
    return result.get("link") or container.get("link") or container.get("linkTemplate")


def _get_error_message(container: dict) -> str | None:
    """Return the error message reported by WUD for this container, if any."""
    error = container.get("error", {}) or {}
    return error.get("message")


def _get_image_created(container: dict) -> tuple[str | None, int | None]:
    """
    Return (formatted_date, days_since) based on image.created.
    This represents when the new image version was published.
    """
    image = container.get("image", {}) or {}
    created_str = image.get("created")
    if not created_str:
        return None, None
    try:
        created_str = created_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(created_str)
        days = (datetime.now(timezone.utc) - dt).days
        return dt.strftime("%Y-%m-%d %H:%M UTC"), days
    except (ValueError, TypeError):
        return None, None


def _get_snoozed_until(container: dict) -> str | None:
    """Return snoozedUntil (epoch ms) as a local-time string, or None if unset.

    WUD leaves snoozedUntil empty when an update is snoozed indefinitely.
    """
    until_ms = container.get("snoozedUntil")
    if not until_ms:
        return None
    try:
        until = datetime.fromtimestamp(until_ms / 1000, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return dt_util.as_local(until).strftime("%Y-%m-%d %H:%M")


def _build_controller_device(entry_id: str, instance_name: str) -> dict:
    """Build device info for the Controller device."""
    return {
        "identifiers": {(DOMAIN, f"{entry_id}_{CONTROLLER_DEVICE_SUFFIX}")},
        "name": f"WUD @ {instance_name}",
        "manufacturer": "What's Up Docker",
        "model": "Controller",
    }


def _build_container_device(
    entry_id: str, instance_name: str, container: dict, controller_device_id: str | None
) -> dict:
    """
    Build device info for a container or compose-project device.
    Containers sharing the same compose project are grouped under one device.
    Containers without a project fall under the Controller device.

    ``controller_device_id`` is the device registry id of the Controller device
    (registered in ``async_setup_entry``); project devices link to it.
    """
    project = _get_compose_project(container)

    if project:
        device = {
            "identifiers": {(DOMAIN, f"{entry_id}_{project}")},
            "name": f"{instance_name} – {project}",
            "manufacturer": "What's Up Docker",
            "model": "Docker Compose Project",
        }
        if _SUPPORTS_VIA_DEVICE_ID:
            device["via_device_id"] = controller_device_id
        else:
            device["via_device"] = (DOMAIN, f"{entry_id}_{CONTROLLER_DEVICE_SUFFIX}")
        return device

    # No project — attach directly to the controller device
    return _build_controller_device(entry_id, instance_name)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up WUD Monitor sensors from a config entry."""
    coordinator: WUDCoordinator = hass.data[DOMAIN][entry.entry_id]
    instance_name = entry.data[CONF_INSTANCE_NAME]
    add_instance_name = entry.data.get(CONF_ADD_INSTANCE_NAME, DEFAULT_ADD_INSTANCE_NAME)

    entities: list[SensorEntity] = []

    # Controller-level sensors
    entities.append(WUDUpdateCountSensor(coordinator, entry, instance_name))
    entities.append(WUDTotalCountSensor(coordinator, entry, instance_name))
    entities.append(WUDLastPollSensor(coordinator, entry, instance_name))
    entities.append(WUDVersionSensor(coordinator, entry, instance_name))

    # Per-container sensors
    for container in coordinator.data or []:
        entities.append(
            WUDContainerSensor(coordinator, entry, instance_name, container, add_instance_name)
        )

    async_add_entities(entities)

    # Snooze actions target per-container sensors (wud_monitor.snooze_update /
    # wud_monitor.unsnooze_update). Registering again for another config entry
    # is harmless: the action dispatches to entities of every entry.
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SNOOZE_UPDATE, SNOOZE_UPDATE_SCHEMA, "async_snooze_update"
    )
    platform.async_register_entity_service(
        SERVICE_UNSNOOZE_UPDATE, {}, "async_unsnooze_update"
    )


class WUDControllerSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for Controller-level sensors."""

    def __init__(
        self,
        coordinator: WUDCoordinator,
        entry: ConfigEntry,
        instance_name: str,
        sensor_key: str,
    ) -> None:
        """Initialize the controller sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._instance_name = instance_name
        self._attr_unique_id = f"wud_{entry.entry_id}_{sensor_key}"
        self._attr_device_info = _build_controller_device(entry.entry_id, instance_name)

    async def async_snooze_update(self, **kwargs) -> None:
        """Reject snooze actions targeting a controller-level sensor."""
        raise ServiceValidationError(
            f"{self.entity_id} is not a container sensor; target a "
            "'… Update Available' sensor to snooze its update"
        )

    async def async_unsnooze_update(self) -> None:
        """Reject unsnooze actions targeting a controller-level sensor."""
        raise ServiceValidationError(
            f"{self.entity_id} is not a container sensor; target a "
            "'… Update Available' sensor to unsnooze its update"
        )


class WUDUpdateCountSensor(WUDControllerSensorBase):
    """Sensor reporting the number of containers with available updates."""

    def __init__(self, coordinator, entry, instance_name) -> None:
        super().__init__(coordinator, entry, instance_name, "update_count")
        self._attr_name = f"WUD @ {instance_name} Containers with Updates"
        self._attr_icon = "mdi:update"
        self._attr_native_unit_of_measurement = "containers"

    @property
    def native_value(self) -> int:
        """Return the count of containers that have an update available."""
        if not self.coordinator.data:
            return 0
        return sum(1 for c in self.coordinator.data if c.get("updateAvailable", False))

    @property
    def extra_state_attributes(self) -> dict:
        """Return a list of containers that have an update available."""
        if not self.coordinator.data:
            return {}
        updates = [
            {
                "name": c.get("name"),
                "current_version": _get_current_version(c),
                "new_version": _get_new_version(c) or "–",
                "semver_diff": (c.get("updateKind") or {}).get("semverDiff"),
            }
            for c in self.coordinator.data
            if c.get("updateAvailable", False)
        ]
        return {"containers": updates}


class WUDTotalCountSensor(WUDControllerSensorBase):
    """Sensor reporting the total number of monitored containers."""

    def __init__(self, coordinator, entry, instance_name) -> None:
        super().__init__(coordinator, entry, instance_name, "total_count")
        self._attr_name = f"WUD @ {instance_name} Monitored Containers"
        self._attr_icon = "mdi:docker"
        self._attr_native_unit_of_measurement = "containers"

    @property
    def native_value(self) -> int:
        """Return the total number of containers returned by WUD."""
        return len(self.coordinator.data or [])


class WUDLastPollSensor(WUDControllerSensorBase):
    """Sensor reporting when HA last successfully polled WUD."""

    def __init__(self, coordinator, entry, instance_name) -> None:
        super().__init__(coordinator, entry, instance_name, "last_poll")
        self._attr_name = f"WUD @ {instance_name} Last Poll"
        self._attr_icon = "mdi:clock-outline"

    @property
    def native_value(self) -> str | None:
        """Return the last poll time converted to the HA-configured local timezone."""
        last = getattr(self.coordinator, "last_poll_time", None)
        if last:
            local_dt = dt_util.as_local(last)
            return local_dt.strftime("%Y-%m-%d %H:%M:%S")
        return None


class WUDVersionSensor(WUDControllerSensorBase):
    """Sensor reporting the WUD version, as returned by GET /api/app."""

    def __init__(self, coordinator, entry, instance_name) -> None:
        super().__init__(coordinator, entry, instance_name, "version")
        self._attr_name = f"WUD @ {instance_name} Version"
        self._attr_icon = "mdi:information-outline"

    @property
    def native_value(self) -> str | None:
        """Return the WUD version, or None if it could not be determined."""
        return self.coordinator.wud_version


class WUDContainerSensor(CoordinatorEntity, SensorEntity):
    """Sensor representing a single WUD-monitored container."""

    def __init__(
        self,
        coordinator: WUDCoordinator,
        entry: ConfigEntry,
        instance_name: str,
        container: dict,
        add_instance_name: bool = DEFAULT_ADD_INSTANCE_NAME,
    ) -> None:
        """Initialize the container sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._instance_name = instance_name
        self._container_name = container["name"]
        self._container_watcher = container.get("watcher", "docker")

        prefix = _name_prefix(instance_name, add_instance_name)
        self._attr_name = f"{prefix}{container['name']} Update Available"

        # Stable unique_id — uses entry_id + watcher + name.
        # Container ID changes on every redeploy; watcher and name do not.
        self._attr_unique_id = (
            f"wud_{entry.entry_id}_{self._container_watcher}_{self._container_name}"
        )

        self._attr_device_info = _build_container_device(
            entry.entry_id, instance_name, container, coordinator.controller_device_id
        )

    def _get_container(self) -> dict | None:
        """Find this container's current data from the coordinator payload."""
        for c in self.coordinator.data or []:
            if c.get("name") == self._container_name and c.get("watcher") == self._container_watcher:
                return c
        return None

    def _get_available_triggers(self, container: dict):
        """Return the triggers available for this container.

        Delegates to the coordinator, which normalizes the container's
        ``triggerInclude`` and the cached triggers API result into a list.
        """
        return self.coordinator.available_triggers_for(container)

    @property
    def native_value(self) -> str:
        """Return 'Yes' if an update is available, otherwise 'No'."""
        container = self._get_container()
        if not container:
            return "unknown"
        return "Yes" if container.get("updateAvailable", False) else "No"

    @property
    def extra_state_attributes(self) -> dict:
        """Return detailed container attributes."""
        container = self._get_container()
        if not container:
            return {}

        image = container.get("image", {}) or {}
        registry = image.get("registry", {}) or {}
        update_kind = container.get("updateKind", {}) or {}

        current = _get_current_version(container)
        new = _get_new_version(container)
        available_since, days_available = _get_image_created(container)

        attrs: dict = {
            "instance_name": self._instance_name,
            "container_name": self._container_name,
            "container_id": container.get("id"),
            "image": image.get("name", "unknown"),
            "registry": registry.get("name", "unknown"),
            "current_version": current,
            "new_version": new or "–",
            "update_available": container.get("updateAvailable", False),
            "semver_diff": update_kind.get("semverDiff"),
            "status": container.get("status", "unknown"),
            "compose_project": _get_compose_project(container) or "–",
            "watcher": self._container_watcher,
            "display_icon": container.get("displayIcon"),
            "available_triggers": self._get_available_triggers(container),
        }

        # Only include date attributes when an update is actually available
        if container.get("updateAvailable") and available_since:
            attrs["available_since"] = available_since
            attrs["days_available"] = days_available

        # Link to release notes — only meaningful when an update is available.
        if container.get("updateAvailable"):
            release_notes = _get_release_notes_url(container)
            if release_notes:
                attrs["release_notes"] = release_notes

        # Error message reported by WUD (e.g. registry rate limit), if any.
        error_message = _get_error_message(container)
        if error_message:
            attrs["error"] = error_message

        # Snooze state — only on WUD 9.1.0+, so its presence also tells the
        # dashboard that the snooze actions are available for this container.
        # A snoozed update is reported by WUD with updateAvailable = false.
        if self.coordinator.supports_snooze:
            is_snoozed = bool(container.get("isSnoozed"))
            attrs["is_snoozed"] = is_snoozed
            if is_snoozed:
                attrs["snoozed_version"] = container.get("snoozedVersion")
                snoozed_until = _get_snoozed_until(container)
                if snoozed_until:
                    attrs["snoozed_until"] = snoozed_until

        return attrs

    def _get_snooze_target(self) -> dict:
        """Return this container's live data, validating that snoozing is possible."""
        if not self.coordinator.supports_snooze:
            raise ServiceValidationError(
                f"Snoozing updates requires WUD {SNOOZE_MIN_VERSION} or newer; "
                f"this instance reports version {self.coordinator.wud_version or 'unknown'}"
            )
        container = self._get_container()
        if not container:
            raise HomeAssistantError(
                f"Container '{self._container_name}' is no longer reported by WUD"
            )
        return container

    async def async_snooze_update(
        self,
        until: datetime | None = None,
        duration: timedelta | None = None,
        version: str | None = None,
    ) -> None:
        """Snooze this container's update until a time, for a duration, or indefinitely."""
        container = self._get_snooze_target()
        if not version and not (
            container.get("updateAvailable")
            or container.get("isSnoozed")
            or container.get("isCoolingDown")
        ):
            raise ServiceValidationError(
                f"Container '{self._container_name}' has no update to snooze"
            )

        if duration is not None:
            until = dt_util.utcnow() + duration
        elif until is not None and until.tzinfo is None:
            # The datetime selector sends a naive local time.
            until = until.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        if until is not None and until <= dt_util.utcnow():
            raise ServiceValidationError("The snooze end must be in the future")

        if not await self.coordinator.async_snooze_container(container["id"], version, until):
            raise HomeAssistantError(
                f"WUD failed to snooze the update of '{self._container_name}'; see the log for details"
            )
        _LOGGER.debug(
            "Snoozed update of '%s' until %s",
            self._container_name,
            until or "further notice",
        )
        await self.coordinator.async_request_refresh()

    async def async_unsnooze_update(self) -> None:
        """Remove the snooze from this container's update."""
        container = self._get_snooze_target()
        if not await self.coordinator.async_unsnooze_container(container["id"]):
            raise HomeAssistantError(
                f"WUD failed to unsnooze the update of '{self._container_name}'; see the log for details"
            )
        _LOGGER.debug("Unsnoozed update of '%s'", self._container_name)
        await self.coordinator.async_request_refresh()
