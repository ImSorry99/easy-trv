"""Constants for the Easy TRV integration."""

from typing import Final

from homeassistant.const import Platform

# The event names are defined in core.model, which cannot import this module.
# The aliases mark them as explicit re-exports.
from .core.model import EVENT_FAILSAFE as EVENT_FAILSAFE  # noqa: PLC0414
from .core.model import EVENT_RECOVERED as EVENT_RECOVERED  # noqa: PLC0414
from .core.model import EVENT_WRITE_FAILED as EVENT_WRITE_FAILED  # noqa: PLC0414

DOMAIN: Final = "easy_trv"

# Config subentries
SUBENTRY_ROOM: Final = "room"

PLATFORMS: Final = [Platform.SENSOR]

# Room configuration keys
CONF_TRVS: Final = "trvs"
CONF_TEMP_SENSORS: Final = "temp_sensors"
CONF_HUMIDITY_SENSORS: Final = "humidity_sensors"
CONF_AREA: Final = "area_id"
CONF_NAME: Final = "name"
CONF_PROFILE: Final = "profile"
CONF_WINDOW_DETECTION: Final = "window_detection"
CONF_HYSTERESIS: Final = "hysteresis"
CONF_REFRESH_MINUTES: Final = "refresh_minutes"
CONF_VERIFY_DELAY: Final = "verify_delay_seconds"
CONF_MAX_ATTEMPTS: Final = "max_attempts"
CONF_MIN_DELTA: Final = "min_delta_kelvin"
CONF_SENSOR_TIMEOUT: Final = "sensor_timeout_minutes"

# Defaults
DEFAULT_REFRESH_MINUTES: Final = 20
DEFAULT_VERIFY_DELAY: Final = 10
DEFAULT_MAX_ATTEMPTS: Final = 3
DEFAULT_MIN_DELTA: Final = 0.1
DEFAULT_SENSOR_TIMEOUT: Final = 120

# Timing
DEBOUNCE_SECONDS: Final = 5.0
OPERATION_TIMEOUT: Final = 20.0
BACKOFF_SECONDS: Final = (5.0, 15.0, 45.0)
TICK_SECONDS: Final = 60

# Repairs issues
ISSUE_ZHA_API: Final = "zha_api_incompatible"

# Profiles
PROFILE_AUTO: Final = "auto"
USER_PROFILES_DIR: Final = "easy_trv/profiles"
