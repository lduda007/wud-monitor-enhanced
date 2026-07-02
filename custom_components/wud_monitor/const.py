"""Constants for the WUD Monitor integration."""

DOMAIN = "wud_monitor"

# Config entry keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_INSTANCE_NAME = "instance_name"
CONF_POLL_INTERVAL = "poll_interval"
CONF_TRIGGERS_EXCLUDED = "triggers_excluded"
CONF_ADD_INSTANCE_NAME = "add_instance_name"

# Defaults
DEFAULT_PORT = 3000
DEFAULT_POLL_INTERVAL = 15  # minutes
DEFAULT_INSTANCE_NAME = "WUD"
DEFAULT_TRIGGERS_EXCLUDED: list[str] = []
DEFAULT_ADD_INSTANCE_NAME = False

# API endpoints
API_CONTAINERS = "/api/containers"
API_CONTAINERS_WATCH = "/api/containers/watch"
API_CONTAINER_WATCH = "/api/containers/{container_id}/watch"
API_CONTAINER_TRIGGERS = "/api/containers/{container_id}/triggers"
API_CONTAINER_RUN_TRIGGER = (
    "/api/containers/{container_id}/triggers/{trigger_type}/{trigger_name}"
)

# Device identifiers
CONTROLLER_DEVICE_SUFFIX = "controller"

# Seconds to wait after running a trigger before refreshing state — WUD needs
# a moment to process the trigger before the container payload reflects it.
TRIGGER_REFRESH_DELAY = 10

# Running a trigger can do real work on the WUD side (e.g. recreating a
# container), so allow a longer timeout than a plain data fetch.
RUN_TRIGGER_TIMEOUT = 60
