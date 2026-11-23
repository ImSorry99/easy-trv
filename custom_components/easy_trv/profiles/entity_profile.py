"""Entity profiles (Beta): drive a thermostat through its Home Assistant entities.

An entity profile names, per capability, an entity of the thermostat's device
instead of a Zigbee attribute. It works with any integration that creates such
entities (ZHA, Zigbee2MQTT, deCONZ, Tuya, ...). This module turns the document
into the native form the engine knows, so the engine and the room logic stay
unchanged: every capability gets a synthetic attribute number, and a
:class:`EntityBinding` per number tells the entity gateway what to call.

Pure Python, no Home Assistant imports.
"""

import re
from dataclasses import dataclass
from typing import Any, Final

import voluptuous as vol

from custom_components.easy_trv.profiles.schema import (
    INTENTS,
    PROFILE_SCHEMA,
    check_intents,
)

BACKEND_ENTITY: Final = "entity"
BACKEND_ZHA: Final = "zha"

# The kinds of entity a capability can use and what they carry.
KIND_NUMBER: Final = "number"
KIND_SWITCH: Final = "switch"
KIND_SELECT: Final = "select"
KIND_CLIMATE_TEMPERATURE: Final = "climate_temperature"
KIND_CLIMATE_MODE: Final = "climate_mode"

DOMAINS: Final = ("number", "switch", "select", "climate")
# What an entity profile must offer; the climate capabilities are added when
# the profile leaves them out, the source switch is optional.
REQUIRED_ENTITY_INTENTS: Final = ("set_room_temperature",)

_SLUG = vol.Match(re.compile(r"^[a-z][a-z0-9_]*\Z", re.ASCII))
_VERSION = vol.Match(re.compile(r"^\d+\.\d+\.\d+\Z", re.ASCII))
_NON_EMPTY_STR = vol.All(str, vol.Length(min=1))
_DEFAULT_MODES: Final = {"heat": "heat", "off": "off"}
_CLIMATE_SETPOINT: Final = {
    "intent": "set_setpoint",
    "entity": {"domain": "climate"},
    "observe": True,
    "range": [4, 35],
    "step": 0.5,
}
_CLIMATE_MODE: Final = {
    "intent": "set_hvac_mode",
    "entity": {"domain": "climate"},
    "observe": True,
}


@dataclass(frozen=True)
class EntityBinding:
    """How the entity gateway carries out one capability of an entity profile.

    ``key`` finds the entity on the thermostat's device (its translation key,
    or the end of its entity id); the climate kinds use the thermostat's own
    climate entity. ``options`` lists the select options or HVAC modes; the raw
    value of a mapped capability is the index into it.
    """

    capability: str
    kind: str
    key: str | None
    options: tuple[str, ...] = ()


_ENTITY = vol.Schema(
    {
        vol.Required("domain"): vol.In(DOMAINS),
        vol.Optional("key"): _SLUG,
    }
)

_CAPABILITY = vol.Schema(
    {
        vol.Required("intent"): vol.In(INTENTS),
        vol.Required("entity"): _ENTITY,
        vol.Optional("options"): vol.All(
            {_NON_EMPTY_STR: _NON_EMPTY_STR}, vol.Length(min=1)
        ),
        vol.Optional("range"): [vol.Coerce(float)],
        vol.Optional("step"): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
        vol.Optional("observe", default=False): bool,
        vol.Optional("min_firmware"): _VERSION,
    }
)

ENTITY_PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required("schema"): 1,
        vol.Required("id"): _SLUG,
        vol.Required("name"): _NON_EMPTY_STR,
        vol.Required("backend"): BACKEND_ENTITY,
        vol.Required("match"): {
            vol.Required("manufacturer"): _NON_EMPTY_STR,
            vol.Required("model"): _NON_EMPTY_STR,
        },
        vol.Optional("min_firmware", default="0.0.0"): _VERSION,
        vol.Optional("tested_firmware", default=list): [_VERSION],
        vol.Optional("contributor"): str,
        vol.Required("capabilities"): {_SLUG: _CAPABILITY},
    }
)


def _kind(name: str, capability: dict[str, Any]) -> str:
    """Return the binding kind of a capability, checking the domain fits.

    Raises:
        vol.Invalid: If the domain cannot carry the intent.

    """
    domain = capability["entity"]["domain"]
    intent = capability["intent"]
    if domain == "climate":
        if intent == "set_setpoint":
            return KIND_CLIMATE_TEMPERATURE
        if intent == "set_hvac_mode":
            return KIND_CLIMATE_MODE
        msg = f"capability {name!r}: a climate entity only carries setpoint and mode"
        raise vol.Invalid(msg)
    if intent in ("set_setpoint", "set_hvac_mode"):
        msg = f"capability {name!r}: {intent} uses the climate entity"
        raise vol.Invalid(msg)
    if "key" not in capability["entity"]:
        msg = f"capability {name!r}: a {domain} entity needs a key"
        raise vol.Invalid(msg)
    return {"number": KIND_NUMBER, "switch": KIND_SWITCH, "select": KIND_SELECT}[domain]


def _native(
    name: str, capability: dict[str, Any], attribute: int
) -> tuple[dict[str, Any], EntityBinding]:
    """Return the native capability document and the binding of one capability.

    Raises:
        vol.Invalid: If the capability is inconsistent.

    """
    kind = _kind(name, capability)
    entity = capability["entity"]
    write: dict[str, Any] = {"endpoint": 0, "cluster": 0, "attribute": attribute}
    options: tuple[str, ...] = ()
    if kind in (KIND_NUMBER, KIND_CLIMATE_TEMPERATURE):
        write["encode"] = "temperature_centi"
        verify: dict[str, Any] = {"rule": "equals"}
    elif kind == KIND_SWITCH and capability["intent"] == "set_source":
        # A switch that turns the external sensor on.
        write["encode"] = {"map": {"external": 1, "internal": 0}}
        verify = {"rule": "one_of", "external": [1], "internal": [0]}
    elif kind == KIND_SWITCH:
        write["encode"] = "bool"
        verify = {"rule": "equals"}
    else:
        mapping = capability.get("options")
        if mapping is None and kind == KIND_CLIMATE_MODE:
            mapping = _DEFAULT_MODES
        if mapping is None:
            msg = f"capability {name!r}: a select entity needs options"
            raise vol.Invalid(msg)
        options = tuple(dict.fromkeys(mapping.values()))
        write["encode"] = {"map": {key: options.index(v) for key, v in mapping.items()}}
        verify = {
            "rule": "one_of",
            **{key: [options.index(v)] for key, v in mapping.items()},
        }
    if "range" in capability:
        write["range"] = capability["range"]
    if "step" in capability:
        write["step"] = capability["step"]
    native: dict[str, Any] = {
        "intent": capability["intent"],
        "write": write,
        "verify": verify,
        "observe": capability["observe"],
    }
    if "min_firmware" in capability:
        native["min_firmware"] = capability["min_firmware"]
    if "key" in entity:
        native["entity"] = {
            "domain": entity["domain"],
            "translation_key": entity["key"],
        }
    return native, EntityBinding(name, kind, entity.get("key"), options)


def validate_entity_profile(data: dict[str, Any]) -> dict[str, Any]:
    """Validate an entity profile and return it in the native form.

    The result passes the native schema and carries ``backend`` and
    ``bindings`` (attribute number to :class:`EntityBinding`) on top.

    Raises:
        vol.Invalid: If the document is not a valid entity profile.

    """
    validated = ENTITY_PROFILE_SCHEMA(data)
    capabilities = dict(validated["capabilities"])
    intents = {capability["intent"] for capability in capabilities.values()}
    if "set_setpoint" not in intents:
        capabilities["setpoint"] = vol.Schema(_CAPABILITY)(dict(_CLIMATE_SETPOINT))
    if "set_hvac_mode" not in intents:
        capabilities["hvac_mode"] = vol.Schema(_CAPABILITY)(dict(_CLIMATE_MODE))
    native_capabilities: dict[str, Any] = {}
    bindings: dict[int, EntityBinding] = {}
    for attribute, (name, capability) in enumerate(capabilities.items(), start=1):
        native_capabilities[name], bindings[attribute] = _native(
            name, capability, attribute
        )
    native: dict[str, Any] = PROFILE_SCHEMA(
        {
            "schema": 1,
            "id": validated["id"],
            "name": validated["name"],
            "match": validated["match"],
            "min_firmware": validated["min_firmware"],
            "tested_firmware": validated["tested_firmware"],
            "capabilities": native_capabilities,
        }
    )
    check_intents(native, REQUIRED_ENTITY_INTENTS)
    native["backend"] = BACKEND_ENTITY
    native["bindings"] = bindings
    return native
