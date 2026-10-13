# 03 – Architektur-Best-Practices 2026 für die Raumheizungs-Integration (Sonoff TRVZB über ZHA)

Stand: 2026-10-12. Bezugspunkt: Home Assistant Core 2026.10.x, `zha`-Library 2.3.1 (2026-10-09), zigpy 2.x,
`pytest-homeassistant-custom-component` 0.13.372 (gekoppelt an HA 2026.10.1, Python ≥ 3.14).

Legende: **[✓]** aus Primärquelle verifiziert (Dev-Docs oder Core-/Library-Quellcode gelesen) · **[?]** aus Erinnerung
bzw. nicht vollständig verifiziert – vor Nutzung im Code prüfen · **[!]** Befund, der die Spezifikation beeinflusst.

---

## 0. Kurzfassung der Empfehlungen

| Thema | Empfehlung |
|---|---|
| Config-Modell | **Ein Config-Entry + ein Subentry pro Raum** (`ConfigSubentryFlow`, Typ `room`). Globale Parameter (Verify-Delay, Retries, Heartbeat, Debounce) im Options-Flow des Haupt-Entries. `single_config_entry: true`. |
| Laufzeit | **Kein `DataUpdateCoordinator`** (nichts zu pollen). Event-getrieben über `async_track_state_change_event` + Heartbeat `async_track_time_interval`. Pro Raum ein `RoomController` (HA-Adapter) um eine **reine Python-Zustandsmaschine** (`core/`), Zustand in `entry.runtime_data`. |
| ZHA-Zugriff | **Direkt über `homeassistant.components.zha.helpers` + `zha`-Library** (`Device.write_zigbee_attribute`, `Device.async_get_cluster(...).read_attributes`), gekapselt in einem Adapter mit vier Methoden. Service `zha.set_zigbee_cluster_attribute` kann nicht lesen → als alleiniger Weg ungeeignet. |
| Fehler-Reporting | **Repairs-Issue** (nicht fixable, `severity=ERROR`, pro TRV) + Bus-Event + Log „einmal bei Ausfall, einmal bei Rückkehr“. `async_delete_issue` beim nächsten erfolgreichen Write. Kein `persistent_notification`. |
| Entitäten | Pro Raum **genau eine `sensor`-Entität** `room_status` (enum ok/degraded/error, `EntityCategory.DIAGNOSTIC`) an einem Raum-Device (1 Device je Subentry) + `diagnostics.py`. Attribute sparsam. |
| Gerüst | `ludeeus/integration_blueprint` ist aktuell (letzter Commit 2026-07-19, HA ≥ 2026.6.4, Python 3.14, Ruff, hassfest + HACS-Action) → als Startpunkt nehmen, Beispiel-Code (API/Coordinator) entfernen, Tests ergänzen. |
| Workflow | **GSD Core als Rückgrat** (`.planning/` mit Roadmap/Phasen, im Repo committed), Superpowers nur als Disziplin-Skills innerhalb der Phasen (TDD, systematic-debugging, verification-before-completion, worktrees). Obsidian spiegelt nur ROADMAP/STATE-Essenz, nicht `.planning/`. |

**[!] Zwei Befunde, die die Spezifikation ändern (Details in §3.4):**
1. Der TRVZB hat **kein „Fenster offen“-Eingangsattribut**. `open_window` (0xFC11/0x6000) ist der Schalter für die *geräteinterne* Fenster-Erkennung (Temperaturabfall > 1,5 °C in 4,5 min). „Fenster offen“ muss die Integration über den Thermostat-Modus umsetzen (`climate.set_hvac_mode: off` + Wiederherstellung) – verifizierbar über den `climate`-State.
2. Für externe Temperatur gibt es zwei Attribute: `external_temperature_sensor_value` (0x600D, int16, °C × 100) und `external_temperature_sensor_enable` / „temperature_sensor_select“ (0x600E, uint8: 0 = intern, 1 = extern, 2 = extern offline → Gerät meldet Fallback, 3 = extern wieder da). Der Sensor-Modus intern/extern ist also genau dieses Attribut.

---

## 1. Config-Modell für Räume

### 1.1 Vergleich

| Kriterium | (a) 1 Entry + Subentries pro Raum | (b) 1 Config-Entry pro Raum | (c) 1 Entry + Options-Flow mit Listen |
|---|---|---|---|
| Verfügbar seit | HA 2025.3 (Dev-Blog 16.02.2025) [✓] | immer | immer |
| Raum anlegen/bearbeiten/löschen in der UI | nativ: „Raum hinzufügen“-Button am Entry, je Subentry Menü *Neu konfigurieren* / *Löschen* [✓ Mechanik, ? exakte Frontend-Darstellung] | „Integration hinzufügen“ je Raum; jeder Raum eigene Karte | selbstgebaut: Menü-Schritte, Listen in Formularen – HA-Formulare können keine Tabellen; Löschen/Umbenennen wird hakelig |
| Globale Einstellungen (Retries, Heartbeat) | einmal im Haupt-Entry (Options/Reconfigure) | je Raum dupliziert oder YAML | einmal |
| Geräte-Registry | 1 Raum-Device je Subentry (seit 2026.8: Device gehört genau 1 Entry + ≤ 1 Subentry) [✓] | 1 Device je Entry | Devices müssten manuell mit Subentry-los verwaltet werden |
| Reload-Granularität | Reload des ganzen Entries bei jeder Raumänderung (alle Controller starten neu) | nur der betroffene Raum | ganzer Entry |
| Entity-Zuordnung | `async_add_entities(..., config_subentry_id=...)` [✓] | normal | manuell per unique_id |
| Zukunftssicherheit | Core-Richtung (MQTT, OpenAI, Template nutzen es) | ok | wird von Core-Reviewern nicht mehr empfohlen |
| Komplexität Code | mittel (zwei Flow-Klassen) | gering | hoch (Listenverwaltung in Flows) |

**Empfehlung: (a).** Es ist genau der Anwendungsfall, für den Subentries gebaut wurden („mehrere Konfigurationsstücke, die eine gemeinsame Ressource teilen“ – hier die ZHA-Instanz und die globalen Parameter). Nachteil „Reload des ganzen Entries“ ist für eine Handvoll Räume irrelevant (Controller starten in < 1 s, Writes werden ohnehin idempotent nachgezogen).

### 1.2 Verifizierte Fakten zur Subentry-API (Core `config_entries.py`, dev-Branch, Oktober 2026) [✓]

- `ConfigSubentry`: `data: MappingProxyType`, `subentry_id` (ULID, automatisch), `subentry_type: str`, `title: str`, `unique_id: str | None`. Zugriff über `entry.subentries: MappingProxyType[str, ConfigSubentry]`.
- `ConfigFlow.async_get_supported_subentry_types(cls, config_entry) -> dict[str, type[ConfigSubentryFlow]]` (classmethod + `@callback`).
- `ConfigSubentryFlow`: Schritte `async_step_user` (Anlegen) und `async_step_reconfigure` (Bearbeiten); nur diese beiden Quellen, keine Discovery/Reauth. Helfer: `self._get_entry()`, `self._get_reconfigure_subentry()`, `self._entry_id`, `self._subentry_type`. (Umbenennung März 2025: `_get_reconfigure_entry` → `_get_entry`.)
- Anlegen: `self.async_create_entry(title=..., data=..., unique_id=...)` → der Flow-Manager ruft `hass.config_entries.async_add_subentry(entry, ConfigSubentry(...))` → das löst die **Update-Listener** des Entries aus (via `_async_update_entry`), aber **keinen automatischen Reload**.
- Bearbeiten: `self.async_update_and_abort(entry, subentry, data=..., data_updates=..., title=..., unique_id=...)` (kein Reload) oder `self.async_update_reload_and_abort(...)` (Reload). **Achtung:** Letzteres wirft `ValueError("Cannot update and reload entry with update listeners")`, wenn der Entry Update-Listener hat.
- Verwaltung: `hass.config_entries.async_add_subentry / async_update_subentry / async_remove_subentry`.
- Subentry-`unique_id` muss nur innerhalb des Entries eindeutig sein.
- Platform-Setup: `async_add_entities(entities, config_subentry_id=subentry_id)`; HA validiert, dass die ID zum Entry gehört.
- Übersetzungen: `strings.json` → `"config_subentries": { "<type>": { "title", "initiate_flow": {"user": "...", "reconfigure": "..."}, "entry_type": "...", "step": {...}, "error": {...}, "abort": {...} } }` (Struktur aus `mqtt/strings.json` verifiziert).

**Daraus folgende Entscheidung (konsistentes Reload-Muster):**
Ein Update-Listener am Entry, der bei *jeder* Änderung (Options, Subentry add/update/remove) `hass.config_entries.async_schedule_reload(entry.entry_id)` aufruft; der Subentry-Reconfigure benutzt deshalb `async_update_and_abort` (nicht die Reload-Variante), der Options-Flow die normale `OptionsFlow`-Klasse (nicht `OptionsFlowWithReload`). Genau so machen es die Core-Integrationen mit Subentries [? aus Erinnerung: `openai_conversation`].

### 1.3 Selectors [✓ `helpers/selector.py` + Selector-Doku]

- `EntitySelector(EntitySelectorConfig(multiple=True, reorder=True, filter=[EntityFilterSelectorConfig(domain="sensor", device_class="temperature")]))` – `filter` ist eine Liste von Filter-Dicts (`integration`, `domain`, `device_class`, `supported_features`, `unit_of_measurement`, optional `device={integration, manufacturer, model, model_id}`). `reorder=True` erlaubt dem Nutzer, die **Priorität der Temperatursensoren per Drag-and-drop** festzulegen – damit braucht es kein eigenes Prioritätsfeld.
- Filter für TRVs: `{"integration": "zha", "domain": "climate"}` (optional zusätzlich `device.manufacturer="SONOFF"`, `device.model="TRVZB"`).
- Filter für Fenster: `{"domain": "binary_sensor", "device_class": ["window", "opening", "door"]}`.
- `AreaSelector(AreaSelectorConfig(entity=[{"integration": "zha", "domain": "climate"}]))` – zeigt nur Areas, in denen ZHA-Climate-Entitäten liegen. Vorbefüllen: `entity_registry.async_entries_for_area(registry, area_id)` [✓] + Device-Area beachten (Entität erbt Area vom Device, wenn `entry.area_id is None` → `device_registry.async_get(entry.device_id).area_id`) [?].
- `NumberSelector`/`DurationSelector`/`SelectSelector` für globale Optionen.

### 1.4 UI-Ablauf für den Nutzer

1. *Einstellungen → Integrationen → „Raumheizung“ hinzufügen* → Config-Flow, Schritt `user`: nur globale Defaults (oder gar kein Formular: `async_create_entry` sofort; Defaults in Options). Danach existiert der Entry einmalig.
2. Auf der Entry-Karte: Button **„Raum hinzufügen“** (`initiate_flow.user`) → Subentry-Flow:
   - Schritt `user`: Name + optional Area (AreaSelector). Bei gewählter Area werden im nächsten Schritt die Entitäten vorbefüllt.
   - Schritt `entities`: TRVs (climate, zha, multiple), Fenstersensoren (binary_sensor), Temperatursensoren (sensor/temperature, multiple + reorder = Priorität), später Anwesenheit.
   - Validierung: mindestens 1 TRV; jedes TRV muss auf ein ZHA-Device mit IEEE auflösbar sein (sonst `errors["trvs"] = "not_zha"`).
3. Jeder Raum erscheint als Zeile unter dem Entry mit Menü *Neu konfigurieren* (gleiches Formular, Vorbelegung via `self.add_suggested_values_to_schema(schema, subentry.data)`) und *Löschen*.
4. *Optionen* am Haupt-Entry: Verify-Delay, Retries, Heartbeat-Intervall, Debounce, Temperatur-Schwelle, Fenster-Latch-Dauer.

### 1.5 Code-Skelett Subentry-Flow

```python
# config_flow.py
from __future__ import annotations
from typing import Any
import probatio as vol
from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry, ConfigFlow, ConfigFlowResult, ConfigSubentryFlow,
    OptionsFlow, SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    AreaSelector, AreaSelectorConfig, EntitySelector, EntitySelectorConfig,
    NumberSelector, NumberSelectorConfig, TextSelector,
)
from .const import (
    DOMAIN, SUBENTRY_ROOM, CONF_NAME, CONF_AREA, CONF_TRVS, CONF_WINDOWS,
    CONF_TEMP_SENSORS, CONF_VERIFY_DELAY, CONF_RETRIES, CONF_HEARTBEAT,
)

ROOM_SCHEMA = vol.Schema({
    vol.Required(CONF_TRVS): EntitySelector(EntitySelectorConfig(
        multiple=True, filter=[{"integration": "zha", "domain": "climate"}])),
    vol.Optional(CONF_WINDOWS, default=[]): EntitySelector(EntitySelectorConfig(
        multiple=True, filter=[{"domain": "binary_sensor",
                                "device_class": ["window", "opening", "door"]}])),
    vol.Optional(CONF_TEMP_SENSORS, default=[]): EntitySelector(EntitySelectorConfig(
        multiple=True, reorder=True,            # Reihenfolge = Priorität
        filter=[{"domain": "sensor", "device_class": "temperature"}])),
})


class HeatingConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)          # mit single_config_entry im Manifest
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Raumheizung", data={})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HeatingOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ROOM: RoomSubentryFlow}


class RoomSubentryFlow(ConfigSubentryFlow):
    """Raum anlegen / bearbeiten."""

    _draft: dict[str, Any]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._draft = user_input
            return await self.async_step_entities()
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_NAME): TextSelector(),
            vol.Optional(CONF_AREA): AreaSelector(AreaSelectorConfig(
                entity=[{"integration": "zha", "domain": "climate"}])),
        }))

    async def async_step_entities(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_TRVS]:
                errors[CONF_TRVS] = "no_trv"
            else:
                return self.async_create_entry(
                    title=self._draft[CONF_NAME],
                    data={**self._draft, **user_input},
                    unique_id=self._draft[CONF_NAME].casefold(),
                )
        suggested = self._prefill_from_area(self._draft.get(CONF_AREA))
        return self.async_show_form(
            step_id="entities",
            data_schema=self.add_suggested_values_to_schema(ROOM_SCHEMA, suggested),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            # Update-Listener des Entries löst den Reload aus -> KEIN async_update_reload_and_abort
            return self.async_update_and_abort(
                self._get_entry(), subentry, data_updates=user_input, title=user_input.get(CONF_NAME, subentry.title)
            )
        schema = vol.Schema({vol.Required(CONF_NAME): TextSelector()}).extend(ROOM_SCHEMA.schema)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, dict(subentry.data)),
        )

    def _prefill_from_area(self, area_id: str | None) -> dict[str, list[str]]:
        if not area_id:
            return {}
        reg = er.async_get(self.hass)
        entries = er.async_entries_for_area(reg, area_id)   # [?] Device-Area-Vererbung zusätzlich prüfen
        return {
            CONF_TRVS: [e.entity_id for e in entries if e.domain == "climate" and e.platform == "zha"],
            CONF_WINDOWS: [e.entity_id for e in entries if e.domain == "binary_sensor"
                           and e.original_device_class in ("window", "opening", "door")],
            CONF_TEMP_SENSORS: [e.entity_id for e in entries if e.domain == "sensor"
                                and e.original_device_class == "temperature"],
        }


class HeatingOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)   # Update-Listener -> Reload
        opts = self.config_entry.options
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(CONF_VERIFY_DELAY, default=opts.get(CONF_VERIFY_DELAY, 8)):
                NumberSelector(NumberSelectorConfig(min=2, max=60, step=1, unit_of_measurement="s")),
            vol.Required(CONF_RETRIES, default=opts.get(CONF_RETRIES, 3)):
                NumberSelector(NumberSelectorConfig(min=1, max=10, step=1)),
            vol.Required(CONF_HEARTBEAT, default=opts.get(CONF_HEARTBEAT, 10)):
                NumberSelector(NumberSelectorConfig(min=1, max=60, step=1, unit_of_measurement="min")),
        }))
```

```json
// strings.json (Auszug) – translations/en.json identisch, translations/de.json übersetzt
{
  "config": { "step": { "user": { "title": "Raumheizung einrichten" } },
              "abort": { "single_instance_allowed": "Bereits eingerichtet." } },
  "options": { "step": { "init": { "title": "Globale Einstellungen",
      "data": { "verify_delay": "Rücklese-Verzögerung", "retries": "Versuche", "heartbeat": "Heartbeat" },
      "data_description": { "verify_delay": "Sekunden zwischen Schreiben und Rücklesen." } } } },
  "config_subentries": {
    "room": {
      "title": "Raum",
      "entry_type": "Raum",
      "initiate_flow": { "user": "Raum hinzufügen", "reconfigure": "Raum bearbeiten" },
      "step": {
        "user": { "title": "Raum anlegen", "data": { "name": "Name", "area": "Bereich (zum Vorbefüllen)" } },
        "entities": { "title": "Geräte zuordnen",
          "data": { "trvs": "Thermostate (ZHA)", "windows": "Fenstersensoren", "temp_sensors": "Temperatursensoren" },
          "data_description": { "temp_sensors": "Reihenfolge = Priorität; der erste verfügbare Sensor wird verwendet." } },
        "reconfigure": { "title": "Raum bearbeiten" }
      },
      "error": { "no_trv": "Mindestens ein Thermostat auswählen.", "not_zha": "Nur ZHA-Thermostate werden unterstützt." },
      "abort": { "reconfigure_successful": "Raum aktualisiert." }
    }
  },
  "issues": {
    "trv_unreachable": {
      "title": "Thermostat {trv} in {room} antwortet nicht",
      "description": "{attempts} Schreibversuche für `{attribute}` sind fehlgeschlagen (zuletzt: {error}). Prüfe Batterie, Zigbee-Reichweite oder ob das Gerät neu angelernt werden muss. Das Problem verschwindet automatisch, sobald ein Schreibvorgang wieder gelingt."
    }
  },
  "entity": { "sensor": { "room_status": { "name": "Status",
      "state": { "ok": "OK", "degraded": "Eingeschränkt", "error": "Fehler" } } } }
}
```

---

## 2. Laufzeit-Architektur

### 2.1 Coordinator vs. Event-getrieben

`DataUpdateCoordinator` ist für *Polling eines Backends* gedacht (ein Fetch, viele Entitäten) [✓ Dev-Docs „Fetching data“]. Diese Integration hat kein Backend zu pollen: die Wahrheit liegt in HA-States (Sensoren, Fenster, climate) und die einzige Ausgangsrichtung sind Zigbee-Writes. Push-Integrationen sollen laut Doku in `async_added_to_hass` abonnieren und `async_write_ha_state()` selbst aufrufen. Ein Coordinator würde nur Boilerplate (UpdateFailed-Semantik, `update_interval`) einbringen, das hier nicht passt.

**Empfehlung:** rein event-getrieben + Heartbeat:
- `async_track_state_change_event(hass, entity_ids, action)` [✓ Signatur `helpers/event.py`] – eine Subscription pro Raum über alle konfigurierten Entitäten (Sensoren, Fenster, TRV-climate für `available`/`hvac_mode`).
- `async_track_time_interval(hass, action, timedelta(minutes=N), cancel_on_shutdown=True)` [✓] – Heartbeat: externe Temperatur periodisch neu schreiben (Firmware-Fallback vermeiden), Staleness prüfen, offene Retries nachziehen.
- `homeassistant.helpers.debounce.Debouncer(hass, logger, cooldown=..., immediate=False, function=...)` [✓] – ein Debouncer pro Raum für „Sensor-Flattern“ (Temperatur) und `async_call_later` [✓] für Fenster-Stabilitätsfenster.
- Alle Unsubscribe-Callbacks via `entry.async_on_unload(...)` registrieren.

### 2.2 Schichten und Testbarkeit

```
custom_components/<domain>/
  core/                      # reine Python-Logik, KEIN homeassistant-Import
    model.py                 # RoomConfig, RoomState (frozen dataclasses), Events, Commands
    room_logic.py            # RoomLogic.handle(event, now) -> list[Command]   (Zustandsmaschine)
    write_plan.py            # Write-Queue-Logik: Dedupe, Rate-Limit, Retry-Plan (ohne asyncio)
  adapters/
    zha_gateway.py           # Protocol TrvGateway + ZhaLibraryGateway (echte Zigbee-Writes/Reads)
    ha_climate.py            # climate.set_hvac_mode / set_temperature via hass.services
  controller.py              # RoomController: HA-Events -> RoomLogic -> Commands -> Adapter (asyncio)
  __init__.py                # async_setup_entry: Controller je Subentry, runtime_data, Listener
  sensor.py / diagnostics.py / repairs.py / config_flow.py / const.py
tests/
  core/test_room_logic.py    # tabellengetriebene Tests ohne hass (schnell, deterministisch)
  test_controller.py         # mit hass-Fixture, FakeGateway, async_fire_time_changed
  test_config_flow.py        # Subentry add/reconfigure, Options
```

Prinzipien:
- `RoomLogic` ist **pur**: Eingaben sind Events (`SensorChanged(entity, value|None, ts)`, `WindowChanged`, `TrvAvailability`, `Tick(now)`, `WriteResult(cmd, ok, readback)`), Ausgaben sind Commands (`WriteExternalTemp(trv, value)`, `SetSensorMode(trv, EXTERNAL|INTERNAL)`, `SetHvacMode(trv, off|restore)`, `RaiseIssue`, `ClearIssue`, `UpdateStatus`). Keine Timer, keine `await`s – Zeit kommt als Parameter.
- `RoomController` ist dünn: übersetzt HA-Events → Logik-Events, führt Commands aus (asyncio-Queue, seriell pro Raum), hält `RoomState` und ruft `async_write_ha_state` der Status-Entität.
- `TrvGateway` als `typing.Protocol` → `FakeGateway` in Tests (programmierbare Fehler/Readbacks).

### 2.3 Zustandsmaschine (Pseudocode)

```python
# core/room_logic.py (Pseudocode, Python-nah)
class TempSource(Enum): EXTERNAL = "external"; INTERNAL = "internal"
class Health(Enum): OK = "ok"; DEGRADED = "degraded"; ERROR = "error"

@dataclass
class RoomState:
    active_sensor: str | None = None          # entity_id des aktuell genutzten Sensors
    last_external_temp: float | None = None
    last_external_write: datetime | None = None
    window_open: bool = False
    window_latched_until: datetime | None = None   # Latch bei Sensorausfall
    saved_hvac_modes: dict[str, str] = field(default_factory=dict)   # trv -> Modus vor "Fenster offen"
    trv_health: dict[str, Health] = field(default_factory=dict)
    failed_attempts: dict[tuple[str, str], int] = field(default_factory=dict)  # (trv, attr) -> n

class RoomLogic:
    def __init__(self, cfg: RoomConfig, params: GlobalParams): ...

    def handle(self, ev: Event, now: datetime, st: RoomState) -> tuple[RoomState, list[Command]]:
        match ev:
            case SensorChanged() | Tick():
                st, cmds = self._reconcile_temperature(now, st)
            case WindowChanged(entity, is_open):
                st, cmds = self._reconcile_windows(now, st)
            case TrvAvailability(trv, available=False):
                st.trv_health[trv] = Health.ERROR          # kein Write-Versuch, Issue erst nach Timeout
            case WriteResult(cmd, ok, readback, error):
                st, cmds = self._on_write_result(cmd, ok, readback, error, now, st)
        return st, cmds + [UpdateStatus(self._health(st))]

    # --- Temperatur: Prioritätsliste, Fallback, Hysterese, Heartbeat ---
    def _reconcile_temperature(self, now, st):
        candidate = first(s for s in self.cfg.temp_sensors            # Reihenfolge = Priorität
                          if s.value is not None and now - s.last_changed < self.params.stale_after)
        cmds = []
        if candidate is None:                                          # kein Sensor brauchbar
            if st.active_sensor is not None:                           # Übergang extern -> intern
                cmds += [SetSensorMode(trv, TempSource.INTERNAL) for trv in self.cfg.trvs]
                st.active_sensor = None
            return st, cmds
        if st.active_sensor is None:                                   # Übergang intern -> extern
            cmds += [SetSensorMode(trv, TempSource.EXTERNAL) for trv in self.cfg.trvs]
        st.active_sensor = candidate.entity_id
        changed = st.last_external_temp is None or abs(candidate.value - st.last_external_temp) >= self.params.temp_delta  # z.B. 0.1 °C
        due = st.last_external_write is None or now - st.last_external_write >= self.params.heartbeat
        if changed or due:
            cmds += [WriteExternalTemp(trv, round(candidate.value, 1)) for trv in self.cfg.trvs]
            st.last_external_temp = candidate.value
            st.last_external_write = now
        return st, cmds

    # --- Fenster: any-open-Semantik, Stabilitätsfenster im Controller (Debounce), Latch bei Ausfall ---
    def _reconcile_windows(self, now, st):
        known = [w for w in self.cfg.windows if w.value is not None]
        if not known and st.window_open:                               # alle Fenstersensoren ausgefallen
            if st.window_latched_until is None:
                st.window_latched_until = now + self.params.window_latch_max   # z.B. 60 min
            if now < st.window_latched_until:
                return st, []                                           # Zustand "offen" halten
            open_now = False                                            # Latch abgelaufen -> sicherer Zustand: zu
        else:
            open_now = any(w.value for w in known)
            st.window_latched_until = None
        cmds = []
        if open_now and not st.window_open:
            cmds += [SetHvacMode(trv, "off", remember=True) for trv in self.cfg.trvs]
        elif not open_now and st.window_open:
            cmds += [SetHvacMode(trv, "restore") for trv in self.cfg.trvs]
        st.window_open = open_now
        return st, cmds

    # --- Write-Ergebnisse: Verify, Retry-Zähler, Issue auf/zu ---
    def _on_write_result(self, cmd, ok, readback, error, now, st):
        key = (cmd.trv, cmd.attribute)
        if ok:
            st.failed_attempts.pop(key, None)
            st.trv_health[cmd.trv] = Health.OK
            return st, [ClearIssue(cmd.trv)]
        n = st.failed_attempts[key] = st.failed_attempts.get(key, 0) + 1
        if n < self.params.retries:
            return st, [Retry(cmd, delay=self.params.backoff(n))]       # 5s, 15s, 45s ...
        st.trv_health[cmd.trv] = Health.ERROR
        return st, [RaiseIssue(cmd.trv, cmd.attribute, n, error), FireEvent("write_failed", cmd, error)]

    def _health(self, st) -> Health:
        if any(h is Health.ERROR for h in st.trv_health.values()): return Health.ERROR
        if st.active_sensor is None and self.cfg.temp_sensors or st.window_latched_until: return Health.DEGRADED
        return Health.OK
```

Der `RoomController` setzt `Retry` in `async_call_later` um, führt `WriteExternalTemp`/`SetSensorMode` über die Write-Queue mit Verify aus (siehe §3.5) und `SetHvacMode` über den HA-Service `climate.set_hvac_mode` (Verify: `climate`-State `hvac_mode` nach X s).

### 2.4 Zustand und Lebenszyklus [✓ Quality-Scale-Regel `runtime-data`]

```python
# __init__.py
from dataclasses import dataclass
from homeassistant.config_entries import ConfigEntry

@dataclass
class HeatingRuntimeData:
    gateway: TrvGateway
    rooms: dict[str, RoomController]          # key = subentry_id

type HeatingConfigEntry = ConfigEntry[HeatingRuntimeData]
PLATFORMS = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: HeatingConfigEntry) -> bool:
    try:
        gateway = ZhaLibraryGateway(hass)         # wirft, wenn ZHA nicht geladen/gestartet
    except ZhaUnavailable as err:
        raise ConfigEntryNotReady("ZHA gateway not ready") from err
    params = GlobalParams.from_options(entry.options)
    rooms = {
        sid: RoomController(hass, gateway, RoomConfig.from_subentry(sub), params)
        for sid, sub in entry.subentries.items() if sub.subentry_type == SUBENTRY_ROOM
    }
    entry.runtime_data = HeatingRuntimeData(gateway, rooms)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    for ctrl in rooms.values():
        await ctrl.async_start()                 # Listener registrieren, Initial-Sync (idempotente Writes)
        entry.async_on_unload(ctrl.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))
    return True

async def _async_reload_on_update(hass: HomeAssistant, entry: HeatingConfigEntry) -> None:
    hass.config_entries.async_schedule_reload(entry.entry_id)   # Options + Subentry add/update/remove

async def async_unload_entry(hass: HomeAssistant, entry: HeatingConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

Die Status-Entität pro Raum wird in `sensor.py` mit `async_add_entities([RoomStatusSensor(ctrl)], config_subentry_id=sid)` angelegt und bekommt `DeviceInfo(identifiers={(DOMAIN, sid)}, name=subentry.title)` → ein Raum-Device je Subentry (seit 2026.8 die vorgesehene Zuordnung [✓ Dev-Blog 21.07.2026]).

---

## 3. ZHA-Zugriff

### 3.1 Variante (a): Service `zha.set_zigbee_cluster_attribute` [✓ Core `zha/services.py`]

- Admin-Service; Schema: `ieee` (EUI64-String), `endpoint_id`, `cluster_id`, `cluster_type` (`in`/`out`), `attribute` (int oder Name), `value`, optional `manufacturer`. Handler: `gateway.get_device(ieee)` → `await zha_device.write_zigbee_attribute(...)`, Ergebnis wird nur geloggt.
- **Kein Lese-Service**, kein `SupportsResponse` – es gibt weder `zha.read_zigbee_cluster_attribute` noch eine Response. Lesen geht nur über den **WebSocket-Befehl `zha/devices/clusters/attributes/value`** (Admin, für das Frontend gedacht; ruft intern `cluster.read_attributes([attribute], allow_cache=False, only_cache=False, manufacturer=...)` auf). Aus Python ist der WS-Befehl nur umständlich (eigene Connection) nutzbar.
- Fazit: als alleiniger Weg ungeeignet (keine Verifikation). Brauchbar nur als Notfall-Fallback fürs Schreiben.

### 3.2 Variante (b): direkte Objekte [✓ Core `zha/helpers.py`, `zha`-Lib `zigbee/device.py`]

```python
from homeassistant.components.zha.helpers import (
    get_zha_gateway,            # -> zha.application.gateway.Gateway
    get_zha_gateway_proxy,      # -> ZHAGatewayProxy (device_proxies: dict[EUI64, ZHADeviceProxy])
    async_get_zha_device_proxy, # (hass, device_registry_id) -> ZHADeviceProxy  (löst ("zha", ieee) selbst auf)
)
```
- `Gateway.get_device(ieee: EUI64) -> zha.zigbee.device.Device`
- `Device.write_zigbee_attribute(endpoint_id, cluster_id, attribute: int|str, value, cluster_type="in", manufacturer=UNDEFINED) -> WriteAttributesResponse | None` – wandelt den Wert per `cluster.find_attribute(attribute)` in den ZCL-Typ, ruft `cluster.write_attributes({attr: value}, manufacturer=manufacturer)`; Zigbee-Fehler → `ZHAException`.
- `Device.async_get_cluster(endpoint_id, cluster_id, cluster_type="in") -> zigpy.zcl.Cluster`; lesen: `success, failure = await cluster.read_attributes([attr], allow_cache=False, only_cache=False, manufacturer=...)` (zigpy 2.x, Signatur verifiziert; `manufacturer` wird per `**kwargs` durchgereicht).
- `Device.available`, `Device.ieee`, `Device.is_mains_powered`, `Device.last_seen`.
- Auflösung climate-Entity → Device/IEEE: `er.async_get(hass).async_get(entity_id)` → `RegistryEntry.platform == "zha"` und `.device_id` → `async_get_zha_device_proxy(hass, device_id).device.ieee`. Intern nimmt der Helper `device.identifiers` mit `domain == "zha"` und `EUI64.convert(...)`. (ZHA registriert Devices mit `identifiers={("zha", str(ieee))}` [✓].)

**Stabilität:** alles interne API ohne Garantie. Brüche der letzten zwei Jahre: 2024.8 (Auslagerung in die `zha`-Lib, `zha.core.gateway` → `homeassistant.components.zha.helpers`), 2026.6/2.0.0 (Quirks aus zigpy heraus, „direct entity access“; es gibt **keine `cluster_handlers` mehr** im `zha`-Paket), 2026.8 (Device-Registry: 1 Device = 1 Entry). `mdeweerd/zha-toolkit` lebt damit per `try/except ImportError` + HA-Versionsweichen (`get_zha_gateway` für ≥ 2024.8, davor `hass.data["zha"]`) und wählt zigpy-Pfade per Version (`write_attributes_raw` ab zigpy 1.2) [✓ `zha_toolkit/utils.py`]. Die hier benötigte Oberfläche ist winzig (4 Aufrufe) → das Risiko ist beherrschbar, wenn sie in **einem** Modul gekapselt ist und ein Import-/Attributfehler zu `ConfigEntryNotReady` + Repairs-Issue „zha_api_incompatible“ führt statt zu Abstürzen in Callbacks.

### 3.3 Variante (c, neu): Quirk-Entitäten über öffentliche Services

Seit HA 2026.2 ist der TRVZB-Quirk in ZHA eingebaut [? Forum-Bericht] und legt pro TRV u. a. `number.<trv>_external_temperature_sensor_value`, `switch.<trv>_external_temperature_sensor`, `switch.<trv>_open_window` an [✓ `zhaquirks/sonoff/trvzb.py`]. Man könnte `number.set_value` / `switch.turn_on` aufrufen (stabile API); ein fehlgeschlagener ZCL-Write wirft dort `HomeAssistantError`, der Entity-State spiegelt den zigpy-Cache (wird nur bei erfolgreichem Write aktualisiert). Nachteile: kein echtes Rücklesen vom Gerät, Entity-IDs müssen über die Entity-Registry (Device-ID + `translation_key`) gefunden werden, Abhängigkeit vom Quirk-Zuschnitt, Nutzer könnte Entitäten deaktivieren.

**Empfehlung:** (b) als Primärweg (Write + echtes Read-back), gekapselt im Adapter; (c) als dokumentierter Plan B, falls (b) nach einem HA-Update bricht; (a) nicht verwenden. Zusätzlich die Quirk-Entität `switch.*_external_temperature_sensor` **lesend** per `async_track_state_change_event` beobachten: sie zeigt kostenlos, wenn das Gerät selbst auf „extern offline“ (0x02) umschaltet.

### 3.4 Sonoff-TRVZB-Attribute (Cluster 0xFC11, Endpoint vermutlich 1 [?]) [✓ Quirk + Zigbee2MQTT-Konverter]

| Attribut | ID | Typ | Bedeutung |
|---|---|---|---|
| `external_temperature_sensor_value` | 0x600D | int16s | externe Temperatur in **0,01 °C** (0.0–99.9 °C) |
| `external_temperature_sensor_enable` (Z2M: `temperatureSensorSelect`) | 0x600E | uint8 | 0 = intern, 1 = extern, 2 = extern offline (vom Gerät gemeldet, Fallback intern), 3 = extern wieder online (vom Gerät gemeldet) |
| `open_window` | 0x6000 | Bool | **geräteinterne** Fenster-Erkennung ein/aus – kein Eingang für „Fenster ist offen“ |
| `frost_protection_temperature` | 0x6002 | int16s | greift bei `system_mode = off` |
| `temperature_control_accuracy` | 0x6011 | int16s | Hysterese −1.0…−0.2 °C (FW ≥ 1.3.0) |
| `child_lock` | 0x0000 | Bool | |

Der Quirk definiert alle Attribute mit `manufacturer_code=None` → **ohne** Manufacturer-Flag schreiben (`manufacturer` auf Default lassen; im Service wäre das `-1`). Z2M-Doku: Synchronisation des externen Werts „muss außerhalb erfolgen“, ein Timeout, nach dem das Gerät auf 0x02 fällt, ist nicht dokumentiert [?] → Heartbeat-Rewrite alle 5–10 min einplanen und auf 0x02/0x03-Reports reagieren.

**[!] Konsequenz für die Spezifikation:** „Fenster offen → an TRV schreiben“ wird zu „Fenster offen → `climate.set_hvac_mode(off)` (vorherigen Modus merken), Fenster zu → Modus wiederherstellen“; Verifikation über den `climate`-State. Zusätzlich beim Raum-Setup einmalig `open_window = 0` schreiben, damit sich Geräte- und Integrationslogik nicht überlagern (als Option anbieten).

### 3.5 Write + Verify (Code-Skizze)

```python
# adapters/zha_gateway.py
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Any, Protocol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

class GatewayError(Exception): ...
class ZhaUnavailable(GatewayError): ...

@dataclass(frozen=True)
class AttrTarget:
    ieee: str          # "00:12:4b:00:..."
    endpoint: int      # TRVZB: 1 [?]
    cluster: int       # 0xFC11
    attribute: int     # 0x600D / 0x600E / 0x6000

class TrvGateway(Protocol):
    def resolve_ieee(self, climate_entity_id: str) -> str: ...
    def is_available(self, ieee: str) -> bool: ...
    async def write_attribute(self, t: AttrTarget, value: Any) -> None: ...
    async def read_attribute(self, t: AttrTarget) -> Any: ...

class ZhaLibraryGateway:
    """Dünner Adapter auf die internen ZHA-Objekte. Einziger Ort mit zha-Imports."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        try:
            from homeassistant.components.zha.helpers import get_zha_gateway, async_get_zha_device_proxy
            from zigpy.types import EUI64
        except ImportError as err:                      # ZHA-API hat sich geändert
            raise ZhaUnavailable(str(err)) from err
        self._get_gateway, self._get_proxy, self._eui64 = get_zha_gateway, async_get_zha_device_proxy, EUI64
        try:
            self._get_gateway(hass)                     # KeyError/AttributeError, wenn ZHA nicht läuft
        except Exception as err:                        # noqa: BLE001 – interne API, bewusst breit
            raise ZhaUnavailable("ZHA gateway not initialised") from err

    def resolve_ieee(self, climate_entity_id: str) -> str:
        entry = er.async_get(self.hass).async_get(climate_entity_id)
        if entry is None or entry.platform != "zha" or entry.device_id is None:
            raise GatewayError(f"{climate_entity_id} is not a ZHA entity")
        return str(self._get_proxy(self.hass, entry.device_id).device.ieee)

    def _device(self, ieee: str):
        dev = self._get_gateway(self.hass).get_device(self._eui64.convert(ieee))
        if dev is None:
            raise GatewayError(f"ZHA device {ieee} not found")
        return dev

    def is_available(self, ieee: str) -> bool:
        return bool(self._device(ieee).available)

    async def write_attribute(self, t: AttrTarget, value: Any) -> None:
        try:
            await self._device(t.ieee).write_zigbee_attribute(t.endpoint, t.cluster, t.attribute, value)
        except Exception as err:   # ZHAException / ValueError (Cluster fehlt) / asyncio.TimeoutError
            raise GatewayError(str(err)) from err

    async def read_attribute(self, t: AttrTarget) -> Any:
        cluster = self._device(t.ieee).async_get_cluster(t.endpoint, t.cluster)
        try:
            success, failure = await cluster.read_attributes([t.attribute], allow_cache=False, only_cache=False)
        except Exception as err:
            raise GatewayError(str(err)) from err
        if t.attribute in failure or t.attribute not in success:
            raise GatewayError(f"read failed: {failure.get(t.attribute)}")
        return success[t.attribute]


# controller.py – Verify/Retry (das Zählen der Versuche liegt in RoomLogic; hier nur ein Versuch)
@dataclass(frozen=True)
class WriteOutcome:
    ok: bool
    readback: Any = None
    error: str | None = None

async def write_and_verify(gw: TrvGateway, t: AttrTarget, value: Any, *, verify_delay: float, timeout: float = 20) -> WriteOutcome:
    if not gw.is_available(t.ieee):
        return WriteOutcome(False, error="device unavailable")
    try:
        async with asyncio.timeout(timeout):
            await gw.write_attribute(t, value)
            await asyncio.sleep(verify_delay)            # Batteriegerät: Antwort kommt erst beim nächsten Poll
            readback = await gw.read_attribute(t)
    except (GatewayError, TimeoutError) as err:
        return WriteOutcome(False, error=str(err))
    return WriteOutcome(readback == value, readback=readback, error=None if readback == value else "mismatch")
```

Hinweise: Werte **vor** dem Adapter in ZCL-Rohwerte wandeln (`int(round(temp * 100))`, Enum-Ints), damit `readback == value` ein reiner Int-Vergleich ist. Pro Raum nur **eine** laufende Write-Operation (asyncio-Lock/Queue), damit Verify-Delays nicht parallel mehrere Zigbee-Anfragen erzeugen. `write_zigbee_attribute` liefert bei zigpy eine `WriteAttributesResponse`; den Status daraus zu prüfen ist möglich, aber das Read-back ist die belastbare Wahrheit [?].

---

## 4. Fehler-Reporting ohne Entitäten-Müll

- **Repairs** [✓ `helpers/issue_registry.py`]: `ir.async_create_issue(hass, DOMAIN, issue_id, *, is_fixable: bool, severity: IssueSeverity, translation_key: str, translation_placeholders=None, is_persistent=False, data=None, learn_more_url=None, issue_domain=None, breaks_in_ha_version=None)`; `ir.async_delete_issue(hass, DOMAIN, issue_id)` (kein Fehler, wenn nicht vorhanden). Severity: `ERROR` = „jetzt kaputt, Handeln nötig“, `WARNING` = „bricht später“, `CRITICAL` nur für Panik. Quality-Scale-Regel `repair-issues` (Gold): Issues müssen **actionable** sein – daher Text mit Handlungsanleitung (Batterie, Reichweite, neu anlernen).
- Best Practice 2026 für „Gerät antwortet nach 3 Versuchen nicht“:
  1. Log `warning` **einmal** beim Übergang OK→ERROR und `info` einmal bei ERROR→OK (Regel `log-when-unavailable`), nicht pro Retry.
  2. Repairs-Issue `trv_unreachable_<ieee>` (nicht fixable, `is_persistent=False` – nach Neustart wird ohnehin neu geprüft), Placeholders `room`, `trv`, `attribute`, `attempts`, `error`.
  3. Bus-Event `hass.bus.async_fire(f"{DOMAIN}_write_failed", {"room": ..., "trv": ..., "attribute": ..., "error": ...})` für Automationen/Benachrichtigungen des Nutzers.
  4. Status-Entität des Raums auf `error`.
  5. Auflösen: sobald ein Write für dieses TRV wieder verifiziert ist → `async_delete_issue`, Status zurück, Event `..._recovered`.
- Kein `persistent_notification` (altes Muster; Repairs sind der Ersatz, und Notifications verschwinden nicht automatisch) [?]. Keine Entität pro Fehler.
- Optional (später, Gold-Glanz): fixable Issue mit `RepairsFlow` „Jetzt erneut synchronisieren“ (`repairs.py`, `async_create_fix_flow`, Confirm-Schritt → `async_create_entry` löscht das Issue automatisch) [✓ Repairs-Doku].

```python
# controller.py (Auszug)
from homeassistant.helpers import issue_registry as ir

def _issue_id(ieee: str) -> str:
    return f"trv_unreachable_{ieee.replace(':', '')}"

@callback
def raise_trv_issue(hass, room: str, trv_name: str, ieee: str, attribute: str, attempts: int, error: str) -> None:
    ir.async_create_issue(
        hass, DOMAIN, _issue_id(ieee),
        is_fixable=False, is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="trv_unreachable",
        translation_placeholders={"room": room, "trv": trv_name, "attribute": attribute,
                                  "attempts": str(attempts), "error": error},
        data={"ieee": ieee, "room": room},
    )

@callback
def clear_trv_issue(hass, ieee: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, _issue_id(ieee))
```

---

## 5. Entitäten-Minimalismus

- **Ja, eine Diagnose-Entität pro Raum ist angemessen** – und sie ist die einzige, die die Integration anlegt. Vorschlag: `sensor` mit `device_class=SensorDeviceClass.ENUM`, `options=["ok","degraded","error"]`, `translation_key="room_status"`, `has_entity_name=True`, `entity_category=EntityCategory.DIAGNOSTIC`, `should_poll=False`, `unique_id=f"{subentry_id}_status"`. Attribute (nur bei Änderung schreiben): `active_temperature_sensor`, `temperature_source` (external/internal), `window_open`, `window_latched`, `last_write` (ISO), `last_write_ok`, `retry_count`, `trv_health: {name: ok|error}`.
  - `EntityCategory.DIAGNOSTIC` = „zeigt Diagnose, erlaubt kein Ändern“; solche Entitäten landen nicht auf automatisch generierten Dashboards und werden von Area-/Device-Zielen in Service-Aufrufen ausgenommen [✓ Entity-Doku, ? Dashboard-Verhalten].
  - Warnung aus der Entity-Doku: häufig wechselnde Attribute blähen die Recorder-DB auf → `last_write` nicht bei jedem Heartbeat schreiben, sondern nur bei Statuswechsel; alternativ `retry_count` weglassen und nur im Diagnostics-Dump zeigen.
- Alternativen: **nur `diagnostics.py`** (Download im Entry-Menü; Gold-Regel `diagnostics`; `async_get_config_entry_diagnostics(hass, entry)` liefert RoomState-Dump + Config, `async_redact_data` für nichts Nötiges – IEEE ist unkritisch) – sinnvoll **zusätzlich**, ersetzt aber keine Live-Sicht. **Nur Repairs** – zu grob, zeigt keinen Normalbetrieb.
- Quality Scale für Custom-Integrationen [✓]: formal „Custom“-Tier, nichts ist Pflicht; sinnvoll zu erfüllen: Bronze (`config-flow` + Tests, `entity-unique-id`, `has-entity-name`, `runtime-data`, `unique-config-entry`, `test-before-setup`, `entity-event-setup`), Silber (`config-entry-unloading`, `entity-unavailable`, `log-when-unavailable`, `parallel-updates`, `test-coverage` ≥ 95 %), Gold-Teile (`diagnostics`, `entity-category`, `entity-translations`, `repair-issues`, `reconfiguration-flow`, `devices`). `quality_scale.yaml` im Integrationsordner als Self-Tracking (done/exempt mit Begründung) – hassfest für Custom-Repos prüft die Datei vermutlich nicht [?].

---

## 6. Projektgerüst und Qualität

### 6.1 Manifest [✓ Manifest-Doku]

```json
{
  "domain": "room_heating",
  "name": "Raumheizung (ZHA)",
  "version": "0.1.0",
  "codeowners": ["@ImSorry99"],
  "config_flow": true,
  "single_config_entry": true,
  "dependencies": ["zha"],
  "integration_type": "hub",
  "iot_class": "local_push",
  "documentation": "https://github.com/<user>/<repo>",
  "issue_tracker": "https://github.com/<user>/<repo>/issues",
  "requirements": [],
  "loggers": ["custom_components.room_heating"]
}
```
- `dependencies: ["zha"]` garantiert, dass ZHA **geladen** (importierbar) ist, nicht dass sein Config-Entry eingerichtet ist → in `async_setup_entry` prüfen und `ConfigEntryNotReady` werfen (HA wiederholt mit Backoff, keine Non-Debug-Logs). `after_dependencies` wäre nur für optionale Abhängigkeiten richtig.
- `integration_type`: `hub` (Geräte pro Raum, erscheint unter *Integrationen*). `helper` wäre semantisch denkbar, Helper-Integrationen laufen aber über den Helfer-Dialog; ob Subentries dort sauber dargestellt werden, ist unklar [?] → `hub`.
- `iot_class`: `local_push` (reagiert auf Events; kein Polling).
- `version` ist für Custom-Integrationen Pflicht; `quality_scale` im Manifest nur für Core.

### 6.2 Dateien
`__init__.py`, `manifest.json`, `config_flow.py`, `const.py`, `controller.py`, `core/*.py`, `adapters/*.py`, `sensor.py`, `diagnostics.py`, `repairs.py` (optional), `strings.json`, `translations/en.json` (= strings.json), `translations/de.json`, `icons.json` (Icon für `room_status`-States), `quality_scale.yaml`, `brand/icon.png` + `brand/logo.png` (lokale Brand-Bilder für Custom-Integrationen; bis dahin `ignore: brands` in der HACS-Action). Repo-Root: `hacs.json` (`name`, `homeassistant: "2026.8.0"` als Minimum – ab da gelten die neuen Device-Registry-Regeln, Subentries gibt es seit 2025.3), `README.md`, `.github/workflows/`.

### 6.3 Startpunkt: `ludeeus/integration_blueprint` [✓ GitHub-API, Dateien gelesen]
- Aktiv gepflegt: letzte Commits 2026-07-19 (HA-Minimum 2026.6.4, Requirements aufgeteilt, Credential-Persistence in Actions aus). Enthält: `custom_components/integration_blueprint/{__init__,api,binary_sensor,config_flow,const,coordinator,data,entity,sensor,switch}.py`, `translations/`, `.devcontainer.json` (Debian-Image, Python-Feature, Port 8123, `scripts/setup`/`develop`), `.ruff.toml` (`select = ["ALL"]`, Core-nahe Ignores, `target-version = "py314"`), `.github/workflows/lint.yml` (Ruff check + format) und `validate.yml` (hassfest + `hacs/action` mit `category: integration`, SHA-gepinnt), Renovate.
- Fehlt: Tests, `pyproject.toml`, uv, mypy. Beispiel-API/Coordinator passen nicht zu dieser Integration → löschen.
- Alternativen: `python3 -m script.scaffold integration` (nur im Core-Checkout, erzeugt Core-Layout – für Custom-Repos nur als Referenz), `cookiecutter-homeassistant-custom-component` (zuletzt 2020/21, veraltet), `custom-components/blueprint` (Fork des gleichen Templates). **Empfehlung:** Blueprint als Template nehmen, ausdünnen, mit `pyproject.toml` (uv) und `tests/` ergänzen.

### 6.4 Tests [✓ PyPI-Metadaten, `tests/common.py`]
- `pytest-homeassistant-custom-component==0.13.372` zieht `homeassistant==2026.10.1`, `pytest==9.0.3`, `pytest-asyncio==1.4.0`, `syrupy`, `freezegun`, `pytest-freezer`, `pytest-socket`, `pytest-timeout`; `requires_python >= 3.14`. Patch-Versionen folgen HA-Releases täglich → im Projekt auf die HA-Version pinnen, die produktiv läuft.
- Fixtures: `hass`, `enable_custom_integrations` (als autouse-Fixture in `tests/conftest.py`), `freezer` (FrozenDateTimeFactory), `snapshot`; Helfer aus `pytest_homeassistant_custom_component.common`: `MockConfigEntry(domain=..., data=..., options=..., subentries_data=[ConfigSubentryData(data=..., subentry_type="room", title="Bad", unique_id=...)])` [✓ `subentries_data` existiert], `async_fire_time_changed(hass, dt)` – zusammen mit `freezer.tick(timedelta(...))` für Heartbeat/Retry-Tests.
- Muster: `hass.states.async_set("sensor.bad_temp", "21.3", {...}); await hass.async_block_till_done()` → FakeGateway prüfen; Config-Flow-Tests über `hass.config_entries.subentries.async_init((entry.entry_id, "room"), context={"source": "user"})` und `async_configure` [? exakter Aufruf].
- `pytest.ini`/`pyproject`: `asyncio_mode = "auto"`, `testpaths = ["tests"]`, `--cov=custom_components.room_heating`.

### 6.5 Tooling
- `pyproject.toml` mit `[dependency-groups] dev = ["pytest-homeassistant-custom-component==0.13.x", "mypy", "ruff"]`; `uv sync` statt pip (die `zha`-Lib selbst nutzt uv). Blueprint-Skripte bleiben pip-basiert – entweder umstellen oder parallel lassen.
- Ruff: Blueprint-Konfig übernehmen; mypy: `strict = true` für `core/` und `adapters/` (Platinum-Regel `strict-typing` als Ziel), `ignore_missing_imports` für `zigpy`/`zha` falls Stubs fehlen.
- GitHub Actions: `validate.yml` (hassfest + HACS), `lint.yml` (ruff check/format, mypy), `test.yml` (uv + pytest mit Coverage), optional `release.yml` (Tag → GitHub-Release; HACS nimmt die neueste Release-Version, ohne Releases den Default-Branch).
- Dev-Container: für Unit-Tests und UI-Flows ausreichend; **ZHA-Writes lassen sich dort nicht testen** (kein Koordinator). Für Integrationstests: Test-HA mit zweitem Koordinator oder ein „Testraum“ in der Produktiv-Instanz mit konservativen Parametern.

---

## 7. ZHA-spezifische Punkte

- **Rate-Limiting:** zigpy serialisiert Requests pro Radio, aber Batterie-Endgeräte (TRVZB) empfangen Writes erst beim nächsten Poll beim Parent-Router → Writes dauern Sekunden. Regeln: pro Raum ein serieller Write-Worker; externe Temperatur nur bei Δ ≥ 0,1–0,2 °C **und** frühestens alle 30–60 s; Heartbeat-Rewrite alle 5–10 min; Sensor-Modus-Writes nur bei Übergang; Verify-Delay 5–10 s; Backoff 5 s → 15 s → 45 s.
- **Debounce:** Temperatur über `Debouncer(cooldown=…)`; Fenster „stabil für N Sekunden“ über `async_call_later` mit Cancel-Reschedule (Logik im Core, Timer im Controller).
- **„device unavailable“ erkennen** [✓ `zha` `zigbee/device.py`, `application/helpers.py`, `const.py`]: `Device.available = _available and on_network`. Ein `DeviceAvailabilityChecker` läuft alle 30–45 s (nur wenn `allow_polling`, das die Gateway nach dem Start setzt): `last_seen` jünger als `consider_unavailable_*` → verfügbar; sonst bis zu 2 Pings (Basic-Cluster `manufacturer` lesen), dann unverfügbar. Defaults: **mains 2 h, battery 6 h** (ZHA-Optionen). Die `climate`-Entität spiegelt exakt `device.available` (`PlatformEntity.available`) → das `available`-Attribut der climate-Entity **reicht als Signal**, ist bei Batteriegeräten aber bis zu 6 h träge. Darum: eigener Write-Fehler ist der schnelle Indikator; `available == False` → Writes gar nicht erst versuchen, Status `error`, Issue erst nach Grace-Zeit. Zusätzlich feuert ZHA bei Offline-Übergang `zha_event` mit `device_event_type: "device_offline"` (Bus-Event mit `device_ieee`, `device_id`) [✓].
- **Reports statt Polling:** die zigpy-`Cluster`-Klasse ist eine `EventBase` und emittiert `attribute_report` (`AttributeReportedEvent`: `attribute_id`, `value`, `endpoint_id`, `cluster_id`), `attribute_written`, `attribute_updated` (deprecated) [✓ zigpy `zcl/__init__.py`]. Subscription vermutlich `cluster.on_event("attribute_report", callback)` nach `zha`-Vorbild (`EventBase.on_event(event_name, callback) -> unsubscribe`) [? zigpy-`EventBase`-Signatur nicht gelesen]; das alte `cluster.add_listener(obj)` mit `attribute_updated(attrid, value, ts)` ist als „wird bald entfernt“ markiert. Für den TRVZB-Hersteller-Cluster ist nicht bekannt, ob 0x600D/0x600E reportet werden; 0x600E meldet laut Z2M zumindest 0x02/0x03 bei Sensor-offline/online. **Empfehlung:** Verify weiter über explizites Read-back (deterministisch, testbar); Reports nur opportunistisch, und praktisch kostenlos über die Quirk-Entitäten (`switch.*_external_temperature_sensor`) via `async_track_state_change_event`.
- **Manufacturer-Code:** Quirk-Attribute mit `manufacturer_code=None` → Writes ohne Manufacturer-Header; nicht `0x1286` o. ä. setzen [✓ Quirk].
- **Verify-Toleranz:** externe Temperatur als `int16` in 0,01 °C; Read-back exakt vergleichen (das Gerät rundet nicht) [?]. Falls doch Abweichungen: Toleranz ±1 (0,01 °C).

---

## 8. Workflow: Superpowers vs. GSD

**Fakten [✓ GitHub]**
- *Superpowers* (`obra/superpowers`, 298 k Stars, letzter Push 2026-10-10): Skill-Bibliothek, keine Projektverwaltung. Ablauf: `brainstorming` → Spec unter `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` → `writing-plans` → Plan unter `docs/superpowers/plans/YYYY-MM-DD-<feature>.md` (Tasks mit TDD-Schritten) → `subagent-driven-development` oder `executing-plans` → `test-driven-development`, `requesting-code-review`, `finishing-a-development-branch`; dazu `systematic-debugging`, `verification-before-completion`, `using-git-worktrees`. Installation als Plugin (`superpowers@claude-plugins-official`). Keine Roadmap-/Phasen-/State-Konzepte; der Zustand ist das Repo + die Markdown-Docs.
- *GSD*: das Original `glittercowboy/get-shit-done` ist seit 2026-06-26 archiviert; Nachfolger **`open-gsd/gsd-core`** (10,4 k Stars, v1.7.0, letzter Push 2026-10-12, `npx @opengsd/gsd-core@latest`). Die lokal installierten `gsd-*`-Skills entsprechen diesem Nachfolger. `.planning/` mit `PROJECT.md`, `REQUIREMENTS.md`, `ROADMAP.md`, `STATE.md`, `config.json`, `MILESTONES.md`, `research/`, `phases/XX-name/{CONTEXT,RESEARCH,XX-YY-PLAN,XX-YY-SUMMARY,VERIFICATION}.md`, `todos/`, `spikes/`, `sketches/`. Phasen-Loop *Discuss → Plan → Execute (parallele Wellen, frischer Kontext, ein Commit pro Task) → Verify (UAT, Fix-Pläne) → Ship*. `commit_docs: true` (Default) committet `.planning/` ins Repo; Forschungsagenten erzeugen `research/` beim `new-project`. Bemerkenswert: gsd-core nutzt im eigenen Repo `docs/superpowers/specs/` – beide Welten werden dort kombiniert.

**Empfehlung für dieses Solo-Projekt**
1. **GSD Core als Rückgrat**: `/gsd-new-project` → PROJECT.md/REQUIREMENTS/ROADMAP mit klaren Phasen (z. B. 1 Gerüst+Config-Flow, 2 Core-Logik mit Tests, 3 ZHA-Adapter+Verify, 4 Repairs/Status-Entität, 5 Diagnostics/Doku/HACS-Release). `.planning/` committen (`commit_docs: true`) – das ist die Spec/Docs-Quelle im Repo. Diese Recherche-Dateien gehören nach `.planning/research/`.
2. **Superpowers als Disziplin innerhalb der GSD-Phasen**: `test-driven-development`, `systematic-debugging`, `verification-before-completion`, `using-git-worktrees` triggern automatisch und ergänzen `gsd-execute-phase` gut. **Nicht** beide Planungsebenen parallel fahren: `brainstorming`/`writing-plans` von Superpowers nur, wenn man bewusst ohne GSD arbeitet (z. B. für einen kleinen Spike). Falls Superpowers trotzdem Spezifikationen erzeugt, in `.planning/phases/<XX>/` ablegen statt `docs/superpowers/`.
3. **Obsidian-Spiegel**: nicht `.planning/` syncen (zu viel, zu technisch, Secrets-/Noise-Risiko), sondern nach dem `obsidian-tool-docs`-Skill eine Planungsnotiz je Projekt im Vault (Übersicht, Status, offene Entscheidungen, Wünsche) – manuell am Ende jeder Phase (`/gsd-complete-milestone`/`verify-work` als Trigger) aus `ROADMAP.md` + `STATE.md` verdichtet. Technische Spec bleibt im Repo (`README.md`, `.planning/`), der Vault verlinkt auf das Repo.

---

## 9. Offene Punkte / zu verifizieren vor Phase 3

1. Endpoint des TRVZB-Herstellerclusters (vermutlich 1) und ob 0x600D/0x600E Attribut-Reports senden → mit `zha/devices/clusters/attributes/value` in der ZHA-Geräteseite („Cluster verwalten“) prüfen.
2. Timeout, nach dem der TRVZB ohne frischen externen Wert auf 0x02 (extern offline) fällt → Heartbeat-Intervall danach festlegen.
3. Verhalten von `hvac_mode: off` ↔ Frostschutz und Rückkehr in `auto` (Wochenprogramm) vs. `heat` (manuell) – welcher Modus wird gemerkt/wiederhergestellt.
4. Frontend-Darstellung der Subentries (Button-Text aus `initiate_flow.user`, Reconfigure/Delete im Zeilenmenü) einmal in HA 2026.10 anschauen.
5. zigpy-`EventBase.on_event`-Signatur, falls Reports abonniert werden sollen.

---

## Quellen

**Home Assistant Developer Docs**
- Config Flow Handler (Subentries, Reconfigure): https://developers.home-assistant.io/docs/config_entries_config_flow_handler
- Config Entries Index (Subentries, Lifecycle): https://developers.home-assistant.io/docs/config_entries_index
- Options Flow: https://developers.home-assistant.io/docs/config_entries_options_flow_handler
- Data Entry Flow (Selectors, Sections, Menus): https://developers.home-assistant.io/docs/data_entry_flow_index
- Blog „Config subentries“ (16.02.2025): https://developers.home-assistant.io/blog/2025/02/16/config-subentries
- Blog „Config subentry flow changes“ (24.03.2025): https://developers.home-assistant.io/blog/2025/03/24/config-subentry-flow-changes
- Blog „Devices are restricted to a single config entry and at most one subentry“ (21.07.2026, HA 2026.8): https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry
- Blog „Device entry config entries deprecation“ (15.09.2026, HA 2026.10): https://developers.home-assistant.io/blog/2026/09/15/device-entry-config-entries-deprecation
- Device Registry: https://developers.home-assistant.io/docs/device_registry_index
- Fetching Data / DataUpdateCoordinator: https://developers.home-assistant.io/docs/integration_fetching_data
- Setup Failures (ConfigEntryNotReady): https://developers.home-assistant.io/docs/integration_setup_failures
- Repairs: https://developers.home-assistant.io/docs/core/platform/repairs
- Diagnostics: https://developers.home-assistant.io/docs/core/platform/diagnostics
- Entity (EntityCategory, Attribute-Warnung): https://developers.home-assistant.io/docs/core/entity
- Integration Quality Scale + Checkliste: https://developers.home-assistant.io/docs/core/integration-quality-scale/ , https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist
- Regeln: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/repair-issues , https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-unavailable
- Manifest: https://developers.home-assistant.io/docs/creating_integration_manifest
- Dateistruktur / Brand-Bilder: https://developers.home-assistant.io/docs/creating_integration_file_structure
- Translations (`strings.json`, issues, config_subentries): https://developers.home-assistant.io/docs/internationalization/core
- Testing (Snapshots, MockConfigEntry): https://developers.home-assistant.io/docs/development_testing
- Creating Component / Scaffold: https://developers.home-assistant.io/docs/creating_component_index
- Selectors (Nutzer-Doku): https://www.home-assistant.io/docs/blueprint/selectors/

**Core-/Library-Quellcode (dev-Branch, gelesen am 2026-10-12)**
- `homeassistant/config_entries.py` (ConfigSubentry, ConfigSubentryFlow, Flow-Manager): https://raw.githubusercontent.com/home-assistant/core/dev/homeassistant/config_entries.py
- `homeassistant/helpers/entity_platform.py` (`config_subentry_id`), `helpers/event.py`, `helpers/debounce.py`, `helpers/issue_registry.py`, `helpers/selector.py`, `helpers/entity_registry.py`, `tests/common.py` (`MockConfigEntry.subentries_data`, `async_fire_time_changed`) – alle unter https://github.com/home-assistant/core
- ZHA in Core: `components/zha/services.py`, `components/zha/websocket_api.py`, `components/zha/helpers.py`: https://github.com/home-assistant/core/tree/dev/homeassistant/components/zha
- MQTT-Subentry-Strings/Flow (Referenz): https://github.com/home-assistant/core/tree/dev/homeassistant/components/mqtt
- `zha`-Library (2.3.1): https://github.com/zigpy/zha – `zha/zigbee/device.py`, `zha/application/helpers.py`, `zha/application/const.py`, `zha/application/platforms/__init__.py`, `zha/event.py`, Releases: https://github.com/zigpy/zha/releases
- zigpy `zcl/__init__.py` (Cluster-Events, `read_attributes`/`write_attributes`): https://github.com/zigpy/zigpy
- Sonoff-TRVZB-Quirk: https://github.com/zigpy/zha-device-handlers/blob/dev/zhaquirks/sonoff/trvzb.py
- Zigbee2MQTT TRVZB (Attribut-Semantik, `temperature_sensor_select`): https://www.zigbee2mqtt.io/devices/TRVZB.html , Konverter: https://github.com/Koenkk/zigbee-herdsman-converters/blob/master/src/devices/sonoff.ts
- ZHA-Service-Doku: https://www.home-assistant.io/actions/zha.set_zigbee_cluster_attribute/
- zha-toolkit: https://github.com/mdeweerd/zha-toolkit (README, `custom_components/zha_toolkit/utils.py`)

**Tooling / Templates / Workflows**
- integration_blueprint: https://github.com/ludeeus/integration_blueprint
- pytest-homeassistant-custom-component: https://github.com/MatthewFlamm/pytest-homeassistant-custom-component , https://pypi.org/project/pytest-homeassistant-custom-component/
- HACS Publish: https://hacs.xyz/docs/publish/start/ , https://hacs.xyz/docs/publish/integration/ , https://hacs.xyz/docs/publish/action/
- hassfest Action: https://github.com/home-assistant/actions
- Superpowers: https://github.com/obra/superpowers (Skills `brainstorming`, `writing-plans`)
- GSD Core: https://github.com/open-gsd/gsd-core (README, `docs/COMMANDS.md`, `docs/USER-GUIDE.md`); Original (archiviert): https://github.com/glittercowboy/get-shit-done
