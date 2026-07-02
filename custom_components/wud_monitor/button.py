"""Button platform for WUD Monitor."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ADD_INSTANCE_NAME,
    CONF_INSTANCE_NAME,
    CONF_TRIGGERS_EXCLUDED,
    CONTROLLER_DEVICE_SUFFIX,
    DEFAULT_ADD_INSTANCE_NAME,
    DOMAIN,
    TRIGGER_REFRESH_DELAY,
)
from .coordinator import WUDCoordinator
from .sensor import (
    _build_container_device,
    _build_controller_device,
    _get_compose_project,
    _name_prefix,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up WUD Monitor buttons from a config entry."""
    coordinator: WUDCoordinator = hass.data[DOMAIN][entry.entry_id]
    instance_name = entry.data[CONF_INSTANCE_NAME]
    add_instance_name = entry.data.get(CONF_ADD_INSTANCE_NAME, DEFAULT_ADD_INSTANCE_NAME)
    excluded_triggers: set[str] = set(entry.data.get(CONF_TRIGGERS_EXCLUDED) or [])

    entities: list[ButtonEntity] = []

    # Controller-level buttons: scan all containers, and refresh state only
    entities.append(WUDScanAllButton(coordinator, entry, instance_name))
    entities.append(WUDRefreshButton(coordinator, entry, instance_name))

    # Track which compose projects already have a scan button to avoid duplicates
    projects_seen: set[str] = set()

    for container in coordinator.data or []:
        project = _get_compose_project(container)

        # Per-container scan button
        entities.append(
            WUDContainerScanButton(
                coordinator, entry, instance_name, container, add_instance_name
            )
        )

        # One button per available trigger, unless the trigger is excluded
        for trigger_id in coordinator.available_triggers_for(container):
            if trigger_id in excluded_triggers:
                continue
            if "." not in trigger_id:
                _LOGGER.warning(
                    "Skipping trigger '%s' for container '%s': expected '{type}.{name}' format",
                    trigger_id,
                    container.get("name"),
                )
                continue
            entities.append(
                WUDContainerTriggerButton(
                    coordinator, entry, instance_name, container, trigger_id, add_instance_name
                )
            )

        # One scan button per compose project, but only if the project has
        # more than one container — otherwise it is identical to the container scan button
        if project and project not in projects_seen:
            projects_seen.add(project)
            project_containers = [
                c for c in coordinator.data if _get_compose_project(c) == project
            ]
            if len(project_containers) > 1:
                entities.append(
                    WUDProjectScanButton(coordinator, entry, instance_name, project, project_containers)
                )

    # Remove button entities that are no longer wanted (e.g. buttons for
    # now-excluded triggers or removed containers) so they don't linger.
    valid_unique_ids = {entity.unique_id for entity in entities}
    ent_reg = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if reg_entry.domain == "button" and reg_entry.unique_id not in valid_unique_ids:
            _LOGGER.debug("Removing stale button entity %s", reg_entry.entity_id)
            ent_reg.async_remove(reg_entry.entity_id)

    async_add_entities(entities)


class WUDScanAllButton(CoordinatorEntity, ButtonEntity):
    """Button that triggers a scan of all WUD-monitored containers."""

    def __init__(
        self,
        coordinator: WUDCoordinator,
        entry: ConfigEntry,
        instance_name: str,
    ) -> None:
        """Initialize the scan all button."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = f"WUD @ {instance_name} Force Scan All"
        self._attr_unique_id = f"wud_{entry.entry_id}_scan_all"
        self._attr_icon = "mdi:refresh"
        self._attr_device_info = _build_controller_device(entry.entry_id, instance_name)

    async def async_press(self) -> None:
        """Trigger a full scan via POST /api/containers/watch."""
        success = await self.coordinator.async_trigger_scan_all()
        if success:
            _LOGGER.debug("WUD scan all triggered successfully")
            # Refresh coordinator data after triggering scan
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("WUD scan all failed")


class WUDRefreshButton(CoordinatorEntity, ButtonEntity):
    """Button that refreshes container states from WUD without triggering a scan.

    Unlike Force Scan All, this only re-fetches the current container data
    (GET /api/containers) — it does not ask WUD to watch for new updates.
    """

    def __init__(
        self,
        coordinator: WUDCoordinator,
        entry: ConfigEntry,
        instance_name: str,
    ) -> None:
        """Initialize the refresh button."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = f"WUD @ {instance_name} Refresh States"
        self._attr_unique_id = f"wud_{entry.entry_id}_refresh"
        self._attr_icon = "mdi:database-refresh"
        self._attr_device_info = _build_controller_device(entry.entry_id, instance_name)

    async def async_press(self) -> None:
        """Re-fetch container data from WUD without triggering a scan."""
        _LOGGER.debug("Refreshing WUD container states")
        await self.coordinator.async_request_refresh()


class WUDProjectScanButton(CoordinatorEntity, ButtonEntity):
    """Button that triggers a scan for all containers in a compose project."""

    def __init__(
        self,
        coordinator: WUDCoordinator,
        entry: ConfigEntry,
        instance_name: str,
        project: str,
        containers: list[dict],
    ) -> None:
        """Initialize the project scan button."""
        super().__init__(coordinator)
        self._entry = entry
        self._project = project
        self._container_ids = [c["id"] for c in containers]
        self._attr_name = f"{instance_name} – {project} Force Scan"
        self._attr_unique_id = f"wud_{entry.entry_id}_project_scan_{project}"
        self._attr_icon = "mdi:refresh"

        # Use the first container to build the project device info
        self._attr_device_info = _build_container_device(
            entry.entry_id, instance_name, containers[0]
        )

    async def async_press(self) -> None:
        """Trigger a scan for each container in the project sequentially."""
        _LOGGER.debug("Triggering WUD scan for project '%s' (%d containers)", self._project, len(self._container_ids))
        for container_id in self._container_ids:
            await self.coordinator.async_trigger_scan_container(container_id)
        await self.coordinator.async_request_refresh()


class WUDContainerScanButton(CoordinatorEntity, ButtonEntity):
    """Button that triggers a scan for a single container."""

    def __init__(
        self,
        coordinator: WUDCoordinator,
        entry: ConfigEntry,
        instance_name: str,
        container: dict,
        add_instance_name: bool = DEFAULT_ADD_INSTANCE_NAME,
    ) -> None:
        """Initialize the container scan button."""
        super().__init__(coordinator)
        self._entry = entry
        self._container_name = container["name"]
        self._container_watcher = container.get("watcher", "docker")
        self._container_id = container["id"]

        prefix = _name_prefix(instance_name, add_instance_name)
        self._attr_name = f"{prefix}{container['name']} Force Scan"
        self._attr_unique_id = (
            f"wud_{entry.entry_id}_{self._container_watcher}_{self._container_name}_scan"
        )
        self._attr_icon = "mdi:refresh"
        self._attr_device_info = _build_container_device(
            entry.entry_id, instance_name, container
        )

    def _get_current_container_id(self) -> str:
        """
        Look up the current container ID from coordinator data.
        Container IDs change on redeploy so we always fetch the latest.
        """
        for c in self.coordinator.data or []:
            if c.get("name") == self._container_name and c.get("watcher") == self._container_watcher:
                return c["id"]
        # Fall back to the ID stored at setup time
        return self._container_id

    async def async_press(self) -> None:
        """Trigger a scan for this specific container."""
        container_id = self._get_current_container_id()
        success = await self.coordinator.async_trigger_scan_container(container_id)
        if success:
            _LOGGER.debug("WUD scan triggered for container '%s'", self._container_name)
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("WUD scan failed for container '%s'", self._container_name)


class WUDContainerTriggerButton(CoordinatorEntity, ButtonEntity):
    """Button that runs a specific WUD trigger on a single container."""

    def __init__(
        self,
        coordinator: WUDCoordinator,
        entry: ConfigEntry,
        instance_name: str,
        container: dict,
        trigger_id: str,
        add_instance_name: bool = DEFAULT_ADD_INSTANCE_NAME,
    ) -> None:
        """Initialize the container trigger button.

        ``trigger_id`` is a WUD trigger identifier of the form ``{type}.{name}``.
        """
        super().__init__(coordinator)
        self._entry = entry
        self._container_name = container["name"]
        self._container_watcher = container.get("watcher", "docker")
        self._container_id = container["id"]
        self._trigger_id = trigger_id
        self._trigger_type, self._trigger_name = trigger_id.split(".", 1)

        prefix = _name_prefix(instance_name, add_instance_name)
        self._attr_name = f"{prefix}{container['name']} Trigger {trigger_id}"
        self._attr_unique_id = (
            f"wud_{entry.entry_id}_{self._container_watcher}_{self._container_name}"
            f"_trigger_{trigger_id}"
        )
        self._attr_icon = "mdi:play-circle-outline"
        self._attr_device_info = _build_container_device(
            entry.entry_id, instance_name, container
        )

    def _get_current_container_id(self) -> str:
        """
        Look up the current container ID from coordinator data.
        Container IDs change on redeploy so we always fetch the latest.
        """
        for c in self.coordinator.data or []:
            if c.get("name") == self._container_name and c.get("watcher") == self._container_watcher:
                return c["id"]
        # Fall back to the ID stored at setup time
        return self._container_id

    async def async_press(self) -> None:
        """Run this trigger on the container via the WUD run-trigger API."""
        container_id = self._get_current_container_id()
        success = await self.coordinator.async_run_container_trigger(
            container_id, self._trigger_type, self._trigger_name
        )
        if success:
            _LOGGER.debug(
                "WUD trigger '%s' run for container '%s'",
                self._trigger_id,
                self._container_name,
            )
        else:
            _LOGGER.warning(
                "WUD trigger '%s' for container '%s' did not confirm success; "
                "refreshing state anyway in case it is still running",
                self._trigger_id,
                self._container_name,
            )

        # WUD needs a moment to process the trigger (and it may still be running
        # even if the HTTP call timed out), so refresh state after a short delay.
        async def _refresh(_now) -> None:
            await self.coordinator.async_request_refresh()

        async_call_later(self.hass, TRIGGER_REFRESH_DELAY, _refresh)
