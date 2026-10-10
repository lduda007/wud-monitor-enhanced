"""Constants for the WUD Monitor integration."""

DOMAIN = "wud_monitor"

# Config entry keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_INSTANCE_NAME = "instance_name"
CONF_POLL_INTERVAL = "poll_interval"
CONF_TRIGGERS_EXCLUDED = "triggers_excluded"
CONF_ADD_INSTANCE_NAME = "add_instance_name"
CONF_TRIGGER_REFRESH_DELAY = "trigger_refresh_delay"
# Optional HTTP Basic credentials. Required since WUD 9.0.0, which enforces
# authentication on the REST API; older unprotected instances leave them empty.
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Defaults
DEFAULT_PORT = 3000
DEFAULT_POLL_INTERVAL = 60  # minutes
DEFAULT_INSTANCE_NAME = "WUD"
DEFAULT_TRIGGERS_EXCLUDED: list[str] = []
DEFAULT_ADD_INSTANCE_NAME = False
# Seconds to wait after a successful trigger/scan before refreshing state.
# 0 = refresh immediately (default); -1 = disable auto refresh.
DEFAULT_TRIGGER_REFRESH_DELAY = 0

# API endpoints
# /api/app is not authenticated (also in WUD 9) and returns {"name", "version"}.
API_APP = "/api/app"
API_CONTAINERS = "/api/containers"
API_CONTAINERS_WATCH = "/api/containers/watch"
API_CONTAINER_WATCH = "/api/containers/{container_id}/watch"
API_CONTAINER_TRIGGERS = "/api/containers/{container_id}/triggers"
API_CONTAINER_RUN_TRIGGER = (
    "/api/containers/{container_id}/triggers/{trigger_type}/{trigger_name}"
)
# POST snoozes (body: {"version", "until"}), DELETE unsnoozes. WUD 9.1.0+.
API_CONTAINER_SNOOZE = "/api/containers/{container_id}/snooze"

# Minimum WUD version exposing the snooze API and isSnoozed/snoozedVersion/
# snoozedUntil container properties.
SNOOZE_MIN_VERSION = "9.1.0"

# Action (service) names
SERVICE_SNOOZE_UPDATE = "snooze_update"
SERVICE_UNSNOOZE_UPDATE = "unsnooze_update"
ATTR_UNTIL = "until"
ATTR_DURATION = "duration"
ATTR_VERSION = "version"

# Device identifiers
CONTROLLER_DEVICE_SUFFIX = "controller"

# Running a trigger can do real work on the WUD side (e.g. recreating a
# container), so allow a longer timeout than a plain data fetch.
RUN_TRIGGER_TIMEOUT = 60
