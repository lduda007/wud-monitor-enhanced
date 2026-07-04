# Changelog

## 1.2.0

### Changed

- **Trigger buttons now refresh state immediately** once the trigger `POST` request returns successfully, instead of waiting ~10s. The trigger request only returns after WUD has processed it, so the delay was unnecessary.

## 1.1.0

Adds on-demand WUD triggers, richer container data, multi-instance ergonomics, and a ready-made dashboard card on top of the original update-monitoring integration.

### Added

- **Per-container trigger buttons** — run any WUD trigger (e.g. `docker.local`) on a container on demand via `POST /api/containers/{id}/triggers/{type}/{name}`, one button per available trigger.
- **"Triggers excluded" config option** — list trigger identifiers for which no per-container button is created.
- **"Add instance name to sensors" config option** — prefixes per-container sensor and button names with the instance name so entities can be told apart across multiple WUD instances.
- **Refresh States button** — re-fetches container data (`GET /api/containers`) without asking WUD to run an update scan.
- **New sensor attributes:**
  - `display_icon` — container icon reported by WUD (`displayIcon`).
  - `available_triggers` — triggers configured for the container.
  - `release_notes` — link to the container's release notes / changelog (shown when an update is available).
  - `error` — error message reported by WUD for the container (e.g. registry rate limit).
- **Lovelace dashboard card** (`lovelace/wud-monitor-dashboard.yaml`) — auto-discovers entities, groups containers by instance/watcher, colour-codes by severity, surfaces a per-instance Errors section, and exposes rescan / release-notes / update controls per row.
- **Brand icon** for the integration.

### Changed

- **Current version detection** now prefers the image tag (`org.opencontainers.image.version` label can return a false-positive version because of WUD cloned labels when updating a container).
- **State auto-refreshes ~10s after a trigger runs**, giving WUD time to process the trigger before the container payload is re-read.
- **Longer HTTP timeouts** — 15s for data fetches, 60s for running a trigger (which can recreate a container on the WUD side).

### Fixed

- **Compose-project Force Scan** now scans each container in the project correctly.
- **Single-container scan** now uses `POST` instead of `GET` on the container watch endpoint.