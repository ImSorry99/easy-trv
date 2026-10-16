"""Constants for the Easy TRV integration."""

from typing import Final

from homeassistant.const import Platform

# The setting defaults are defined in core.settings, which cannot import this module.
from .core import settings as _settings

# The event names are defined in core.model, which cannot import this module.
# The aliases mark them as explicit re-exports.
from .core.model import EVENT_FAILSAFE as EVENT_FAILSAFE  # noqa: PLC0414
from .core.model import EVENT_RECOVERED as EVENT_RECOVERED  # noqa: PLC0414
from .core.model import (
    EVENT_SETPOINT_ADOPTED as EVENT_SETPOINT_ADOPTED,  # noqa: PLC0414
)
from .core.model import EVENT_WRITE_FAILED as EVENT_WRITE_FAILED  # noqa: PLC0414

DOMAIN: Final = "easy_trv"

# Bus event fired when the heating state of a room changes.
EVENT_STATE_CHANGED: Final = "easy_trv_state_changed"

# Room setting value meaning "use the global setting"
SETTING_GLOBAL: Final = _settings.GLOBAL

# Config subentries
SUBENTRY_ROOM: Final = "room"

PLATFORMS: Final = [Platform.CLIMATE, Platform.SENSOR]

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
CONF_HIDE_THERMOSTAT_ENTITIES: Final = "hide_thermostat_entities"
CONF_SETPOINT_RANGE: Final = "setpoint_range"
CONF_SETPOINT_MIN: Final = "setpoint_min"
CONF_SETPOINT_MAX: Final = "setpoint_max"
CONF_WINDOW_WARN_MINUTES: Final = "window_warn_minutes"
CONF_WINDOW_WARN_COLDER: Final = "window_warn_colder"
CONF_WINDOW_CHECK_MINUTES: Final = "window_check_minutes"
CONF_WINDOW_CHECK_DROP: Final = "window_check_drop_kelvin"
CONF_OUTDOOR_CUTOFF: Final = "outdoor_cutoff"  # room-only: global / off
CONF_OUTDOOR_CUTOFF_CELSIUS: Final = "outdoor_cutoff_celsius"
CONF_WINDOW_OPEN_DELAY: Final = "window_open_delay_seconds"
CONF_STATISTICS: Final = "statistics"
# Entry-level and room entity configuration (not settings with a default)
CONF_OUTDOOR_SENSOR: Final = "outdoor_sensor"
CONF_WINDOW_CONTACTS: Final = "window_contacts"

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
DEFAULT_HIDE_THERMOSTAT_ENTITIES: Final = _settings.DEFAULT_HIDE_THERMOSTAT_ENTITIES
DEFAULT_SETPOINT_RANGE: Final = _settings.DEFAULT_SETPOINT_RANGE
DEFAULT_SETPOINT_MIN: Final = _settings.DEFAULT_SETPOINT_MIN
DEFAULT_SETPOINT_MAX: Final = _settings.DEFAULT_SETPOINT_MAX
DEFAULT_WINDOW_WARN_MINUTES: Final = _settings.DEFAULT_WINDOW_WARN_MINUTES
DEFAULT_WINDOW_WARN_COLDER: Final = _settings.DEFAULT_WINDOW_WARN_COLDER
DEFAULT_WINDOW_CHECK_MINUTES: Final = _settings.DEFAULT_WINDOW_CHECK_MINUTES
DEFAULT_WINDOW_CHECK_DROP: Final = _settings.DEFAULT_WINDOW_CHECK_DROP
DEFAULT_OUTDOOR_CUTOFF_CELSIUS: Final = _settings.DEFAULT_OUTDOOR_CUTOFF_CELSIUS
DEFAULT_WINDOW_OPEN_DELAY: Final = _settings.DEFAULT_WINDOW_OPEN_DELAY_SECONDS
DEFAULT_STATISTICS: Final = _settings.DEFAULT_STATISTICS

# Timing
DEBOUNCE_SECONDS: Final = 5.0
OPERATION_TIMEOUT: Final = 20.0
BACKOFF_SECONDS: Final = (5.0, 15.0, 45.0)
TICK_SECONDS: Final = 60
# How long a window contact may stay silent before Repairs says so. Not a setting:
# it rides out ZHA's start-up.
CONTACT_SILENT_ISSUE_MINUTES: Final = 10

# Repairs issues
ISSUE_ZHA_API: Final = "zha_api_incompatible"
ISSUE_SETTINGS_MIGRATED: Final = "settings_migrated"

# Profiles
PROFILE_AUTO: Final = "auto"
USER_PROFILES_DIR: Final = "easy_trv/profiles"
