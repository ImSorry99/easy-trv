"""Constants for the Easy TRV integration."""

from typing import Final

from homeassistant.const import Platform

# The setting defaults are defined in core.settings, which cannot import this module.
from .core import settings as _settings

# The event names are defined in core.model, which cannot import this module.
# The aliases mark them as explicit re-exports.
from .core.model import EVENT_FAILSAFE as EVENT_FAILSAFE  # noqa: PLC0414
from .core.model import EVENT_RECOVERED as EVENT_RECOVERED  # noqa: PLC0414
from .core.model import EVENT_WRITE_FAILED as EVENT_WRITE_FAILED  # noqa: PLC0414

DOMAIN: Final = "easy_trv"

# Room setting value meaning "use the global setting"
SETTING_GLOBAL: Final = _settings.GLOBAL

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
CONF_ADAPTIVE_MODE: Final = "adaptive_mode"
CONF_FROST_TEMPERATURE: Final = "frost_temperature"

# Defaults
DEFAULT_REFRESH_MINUTES: Final = _settings.DEFAULT_REFRESH_MINUTES
DEFAULT_VERIFY_DELAY: Final = _settings.DEFAULT_VERIFY_DELAY
DEFAULT_MAX_ATTEMPTS: Final = _settings.DEFAULT_MAX_ATTEMPTS
DEFAULT_MIN_DELTA: Final = _settings.DEFAULT_MIN_DELTA
DEFAULT_SENSOR_TIMEOUT: Final = _settings.DEFAULT_SENSOR_TIMEOUT
DEFAULT_WINDOW_DETECTION: Final = _settings.DEFAULT_WINDOW_DETECTION
DEFAULT_HYSTERESIS: Final = _settings.DEFAULT_HYSTERESIS
DEFAULT_ADAPTIVE_MODE: Final = _settings.DEFAULT_ADAPTIVE_MODE
DEFAULT_FROST_TEMPERATURE: Final = _settings.DEFAULT_FROST_TEMPERATURE

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
