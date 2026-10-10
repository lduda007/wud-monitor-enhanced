# Changelog
## 1.4.0

Adds support for What's Up Docker 9.x.

### Added

- **Username and password** config options — optional HTTP Basic credentials sent with every WUD API request. Required for WUD 9.0.0+, which makes API authentication mandatory; leave them empty for older WUD instances without authentication. Setup reports a dedicated error when WUD requires credentials or rejects them.
- **WUD version detection** via `GET /api/app`, exposed as a new controller-level **Version** sensor. Snooze features are enabled only on WUD 9.1.0 or newer.
- **`container_name` sensor attribute** on per-container sensors.
- **Snooze sensor attributes** (WUD 9.1.0+): `is_snoozed`, `snoozed_version` and `snoozed_until` (local time; absent when snoozed indefinitely).
- **`wud_monitor.snooze_update` action** — snoozes a container's update indefinitely, for a `duration`, or `until` a date/time; optionally for a specific `version`.
- **`wud_monitor.unsnooze_update` action** — removes a container's snooze.
- **Dashboard card**: a collapsible **Snoozed** section per watcher showing which version is snoozed and until when, a **Snooze** button on containers with an update (7 days by default, configurable via `snooze_days`), and an **Unsnooze** button on snoozed containers.
- **Dashboard card**: configurable buttons on containers with an update — `show_scan_button`, `show_release_notes_button`, `show_snooze_button` and `show_update_button` flags near the top of the template (all `true` by default).

### Changed

- WUD API authentication/permission failures (HTTP 401/403) now log WUD's error message and a hint to check the credentials, instead of a bare status code.
- README: corrected the documented default poll interval to 60 minutes.
- **Minimum Home Assistant version is now 2024.12.0**, declared in `hacs.json` so HACS blocks installs on older versions. The options flow already depended on 2024.12 (older versions fail when opening **Configure**); the README previously stated 2024.1.

### Fixed

- Compose project devices now link to the Controller device with `via_device_id` on Home Assistant 2026.8+, fixing the deprecation warning about `via_device` (which stops working in 2027.8). Older Home Assistant versions keep using `via_device`.
- **Dashboard card**: container rows no longer start with the device name (e.g. `– docker jackett`) on Home Assistant 2026.8+, which prefixes entity friendly names with their device name. Rows are now labelled from the new `container_name` sensor attribute.

## 1.3.0

### Added

- **Configurable refresh delay after triggers** — a new **Refresh delay after trigger (seconds)** setting controls when container states are refreshed after a successful scan or trigger. `0` refreshes immediately (default), a positive value waits that many seconds (useful when WUD needs time to finish recreating a container), and `-1` disables the automatic refresh entirely.

## 1.2.1

### Changed

- **Default poll interval** changed from 15 minutes to 1 hour.

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