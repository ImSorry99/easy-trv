# Sonoff TRVZB unter ZHA – technisches Profil

Stand der Recherche: 2026-10-12. Ziel: Grundlage für eine HA-Custom-Integration, die 4× TRVZB per Zigbee-Attribut-Write mit externem Temperaturwert und Fensterstatus versorgt und später die nativen Schedules/Timer nutzt.

## 0. Legende Verifikationsstatus

- **[V]** = VERIFIZIERT: Quellcode selbst gelesen (Quirk, Z2M-Konverter, zigpy, zha-Library, HA core, zha-toolkit, Sonoffs eigenes ZHA-Script) oder offizielle Index-Dateien (Sonoff-OTA-JSON, zigpy-OTA-Index) geprüft.
- **[H]** = Herstellerangabe (Sonoff Help Center / eWeLink-Forum-Changelog von Sonoff-Mitarbeitern), nicht im Code überprüfbar.
- **[F]** = Foren-/Issue-Bericht einzelner Nutzer oder Entwickler-Beobachtung (nicht offiziell dokumentiert).

Alle Quelldateien wurden per `curl` in ein Scratch-Verzeichnis geladen und nur gelesen, nie ausgeführt.

## 1. Kurzfassung

- Der TRVZB hat genau einen herstellerspezifischen Cluster **0xFC11** auf Endpoint 1 (plus Standard-Thermostat 0x0201, Basic 0x0000, Power 0x0001, OTA 0x0019, und 0xFC57 laut Signatur in Issue #3851). Die Attribute in 0xFC11 werden **ohne Manufacturer-Code** geschrieben/gelesen [V] (Quirk: `manufacturer_code=None`; Sonoffs eigenes Script: `_is_manuf_specific = False`; Z2M: keine `manufacturerCode`-Option).
- Die ZHA-Quirk auf `dev` (Stand Commit e5c7b24, 2026-06-24; enthalten in zhaquirks 2.3.0 vom 2026-09-29) deckt alle 17 bekannten TRVZB-Attribute ab und erzeugt 13 Entitäten [V]. Kein `select`, kein `sensor`, kein `binary_sensor`.
- Die beiden Attribute für das Projekt: **`external_temperature_sensor_value` 0x600D (int16s, 0,01 °C, 0–99,9 °C)** und **`external_temperature_sensor_enable` 0x600E (uint8, 0=intern, 1=extern; Gerät meldet 2=Fail-safe/extern offline, 3=extern wieder da)** [V Z2M-Mapping, F für Bedeutung von 2/3].
- **Fenster offen (0x6000 `open_window`, Bool)** ist der **Ein/Aus-Schalter der geräteeigenen Erkennung** (Temperatursturz >1,5 °C in 4,5 min), **kein Eingang** für einen externen Fensterkontakt [V Z2M-Beschreibung/Kategorie „config“, H Sonoff-Doku]. Einen „window detected“-Status exposed der TRVZB nicht (anders als der Nachfolger TRV-ZBT).
- **Timeout:** Bleibt der externe Wert >2 h aus, fällt das TRV auf den internen Sensor zurück (0x600E → 2) und springt beim nächsten Write zurück (→ 3) [F, mehrfach unabhängig berichtet, u. a. vom Autor des Z2M-Konverters]. Also mindestens alle ~60–90 min schreiben.
- Firmware-Versionen: 1.1.x (Basis), 1.2.1 (Ventilöffnungsgrad, externer Sensor), 1.3.0 (Temp-Accuracy/Hysterese), 1.4.0/1.4.1 (Boost/Timer, Offset), 1.4.4 (Adaptive/PID „smart_temperature_control“) [H]. OTA via ZHA: Sonoff-Provider ist in zigpy standardmäßig aktiv, der Sonoff-Server liefert aktuell max. 1.4.1; **1.4.4 liegt im zigpy-OTA-Stable-Index** (HA ≥ 2026.3) [V].
- Eigene Quirk v2 ist für die Kernfunktion **nicht nötig**; sinnvoll wird sie nur, wenn man 0x600E als `select`/`sensor` (Fail-safe sichtbar machen) oder `system_mode=auto` sauber in HA haben will (siehe Abschnitt 11).

## 2. ZHA-Quirk: `zhaquirks/sonoff/trvzb.py` (Branch `dev`) [V]

Quelle: https://raw.githubusercontent.com/zigpy/zha-device-handlers/dev/zhaquirks/sonoff/trvzb.py

Aufbau:

```python
class CustomSonoffCluster(CustomCluster):
    cluster_id = 0xFC11
    class AttributeDefs(BaseAttributeDefs):
        child_lock = ZCLAttributeDef(id=0x0000, type=t.Bool, manufacturer_code=None)
        open_window = ZCLAttributeDef(id=0x6000, type=t.Bool, manufacturer_code=None)
        ...
QuirkBuilder("SONOFF", "TRVZB").replaces(CustomSonoffCluster).switch(...).number(...).write_attr_button(...).add_to_registry()
```

Historie der Datei (git log, [V]):

| Datum | Commit | PR | Inhalt |
|---|---|---|---|
| 2024-11-25 | e5582c3 | #3358 (fgsch) | Erste Quirk (child_lock, open_window, frost, Ventil-Diagnose, Öffnungsgrade) |
| 2025-04-30 | 70f2e70 | #3909 (danielkleinert) | FW-1.3-Attribute: `external_temperature_sensor_enable/value`, `temperature_control_accuracy` (Werte aus Z2M-PRs #8837/#8873) |
| 2026-01-28 | 9c3773c | #4658 | Migration auf `manufacturer_code=None` + neue zigpy-Attribut-Events |
| 2026-02-25 | ba90faa | #4740 (1775939187 = Sonoff-nah) | Boost/Timer (0x6014–0x6016) und Adaptive Mode 0x6017; ausgeliefert mit HA 2026.3.0 |
| 2026-03-24 | 8bd36ad | #4856 | `NumberDeviceClass.TEMPERATURE_DELTA` für Accuracy |
| 2026-06-24 | e5c7b24 | #5113 | Quirks-API aus zigpy herausgelöst (Imports `zhaquirks.builder`) |

Offene PRs mit TRVZB-Bezug (GitHub-Suche, [V]):

- **#4792 „Limit Sonoff TRVZB entities for installed firmware“** (TheJulianJES, Draft seit 2026-02-27, offen): Entitäten je nach OTA-`current_file_version` begrenzen: <0x1400 → 8 Basis-Entitäten, 0x1400–0x1403 → +Timer/Boost (12), ≥0x1404 oder unbekannt → +Adaptive (13). Noch ohne Unit-Test, Label „2.0 migration needed“. Für das Projekt relevant: Solange nicht gemerged, erscheinen auf alten Firmwares Entitäten, die das Gerät mit `UNSUPPORTED_ATTRIBUTE` quittiert.
- Keine offene PR fügt neue Attribute hinzu. Geschlossene Versuche #4476–#4479 (Nov/Dez 2025, Sonoff-naher Autor) wurden durch #4740 ersetzt.
- Issue #3851 (Schedule in ZHA, seit 2025-02-11 offen, kein Code) und #4623 (nur `off` in `hvac_modes` auf FW 1.4.1, offen).

Erzeugte HA-Entitäten der Quirk (Reihenfolge wie im Code) [V]:

| # | Plattform | Attribut | Parameter |
|---|---|---|---|
| 1 | switch | smart_temperature_control (0x6017) | on_value=2, off_value=0, „Adaptive mode“ |
| 2 | switch | child_lock (0x0000) | „Child lock“ |
| 3 | switch | open_window (0x6000) | „Open window“ |
| 4 | number | timer_mode_target_temperature (0x6016) | 4–35 °C, step 0.5, multiplier 0.01 |
| 5 | number | temporary_mode_duration (0x6015) | 0–1440 min, multiplier 1/60 (Rohwert = Sekunden) |
| 6 | button | temporary_mode (0x6014) := 0x00 | „Boost mode“ (unique_id_suffix boost_mode) |
| 7 | button | temporary_mode (0x6014) := 0x01 | „Timer mode“ |
| 8 | number | frost_protection_temperature (0x6002) | 4–35 °C, multiplier 0.01 |
| 9 | number | valve_opening_degree (0x600B) | 0–100, initially_disabled |
| 10 | number | valve_closing_degree (0x600C) | 0–100, initially_disabled |
| 11 | number | temperature_control_accuracy (0x6011) | −1.0…−0.2 °C, step 0.2, TEMPERATURE_DELTA, multiplier 0.01 |
| 12 | switch | external_temperature_sensor_enable (0x600E) | on=1/off=0, translation_key `external_temperature_sensor` |
| 13 | number | external_temperature_sensor_value (0x600D) | 0–99.9 °C, step 0.1, multiplier 0.01 |

Read-only-Diagnoseattribute (idle_steps, closing_steps, 3× Spannungen) sind definiert, aber **ohne Entität**.

### 2.1 Sonoffs eigenes „TRVZB_ZHA_Script“ [V]

Download (vom Sonoff Help Center verlinkt): https://resource-wangsu.helplook.net/docker_production/a4j0b0/article/F1vhxZrI/attachments/TRVZB_ZHA_Script.zip – enthält `trvzb.py` (2026-01-22) und eine XLSX-Installationsanleitung. Unterschiede zur Upstream-Quirk:

- Nutzt noch `zigpy.quirks.v2` (alte Import-Pfade) → inkompatibel mit aktuellem zigpy/zhaquirks (Forum-Berichte: bricht ab HA 2026.2, Upstream-Quirk reicht seit 2026.3).
- Fake-Attribute `boost_mode` 0x6018 / `timer_mode` 0x6019 (Bool, `is_manufacturer_specific=True`) werden in `write_attributes` auf `temporary_mode` 0x6014 umgebogen. Upstream löst das per `write_attr_button` mit `attribute_value`.
- `_is_manuf_specific` ist hart auf `False` gesetzt → Sonoff selbst will Frames **ohne** Manufacturer-Code. Starkes Indiz für Abschnitt 10.
- Keine zusätzlichen Attribute gegenüber Upstream. Für das Projekt: **nicht installieren**.

## 3. Zigbee2MQTT-Konverter als Referenz (`src/devices/sonoff.ts`, master) [V]

Quelle: https://raw.githubusercontent.com/Koenkk/zigbee-herdsman-converters/master/src/devices/sonoff.ts (Definition `zigbeeModel: ["TRVZB"]`, ~Zeile 10047).

Cluster-Definition `customSonoffTrvzb` (ID 0xFC11) mit Attributen: childLock 0x0000 BOOLEAN, tamper 0x2000 UINT8, illumination 0x2001 UINT8 (beide definiert, aber **nicht exposed** – vermutlich Copy aus anderen Sonoff-Geräten), openWindow 0x6000 BOOLEAN, frostProtectionTemperature 0x6002 INT16, idleSteps 0x6003 UINT16, closingSteps 0x6004 UINT16, valveOpeningLimitVoltage 0x6005, valveClosingLimitVoltage 0x6006, valveMotorRunningVoltage 0x6007 (UINT16), valveOpeningDegree 0x600B UINT8, valveClosingDegree 0x600C UINT8, tempAccuracy 0x6011 INT16, temporaryMode 0x6014 UINT8, temporaryModeTime 0x6015 UINT32, temporaryModeTemp 0x6016 INT16, externalTemperatureInput 0x600D INT16, temperatureSensorSelect 0x600E UINT8, smartTempControl 0x6017 BITMAP8. `commands: {}` – keine herstellerspezifischen Kommandos.

Exposes (Z2M-Doku + Code):

| Expose | Typ | Attribut | Details |
|---|---|---|---|
| climate | composite | 0x0201 | occupied_heating_setpoint 4–35/0.5; local_temperature; local_temperature_calibration −12.7…12.7 step 0.2; system_mode **off/auto/heat**; running_state idle/heat (STATE_GET) |
| battery | numeric | 0x0001 | |
| child_lock | binary LOCK/UNLOCK | 0x0000 | 1/0 |
| open_window | binary ON/OFF, Kategorie config | 0x6000 | „Automatically turns off the radiator when local temperature drops by more than 1.5°C in 4.5 minutes.“ |
| frost_protection_temperature | numeric 4–35 °C, scale 100 | 0x6002 | |
| temperature_sensor_select | enum local_temperature/remote_temperature/remote_source_offline | 0x600E | Lookup 0/1/2; Lesen: 0→local, **1 oder 3→remote**, 2→remote_source_offline |
| external_temperature_input | numeric 0–99.9 °C, scale 100, precision 1, config | 0x600D | „synchronisation … needs to happen outside of Zigbee2MQTT“ |
| idle_steps, closing_steps | numeric, STATE_GET, diagnostic | 0x6003/0x6004 | |
| valve_opening/closing_limit_voltage, valve_motor_running_voltage | numeric mV, STATE_GET | 0x6005–0x6007 | |
| valve_opening_degree / valve_closing_degree | numeric 0–100 % | 0x600B/0x600C | „only version v1.1.4 or higher“ |
| temperature_accuracy | numeric −1…−0.2 step 0.2, scale 100 | 0x6011 | Default −1; „only v1.3.0 or higher“ |
| temporary_mode_select | enum boost=0 / timer=1 | 0x6014 | |
| temporary_mode_duration | numeric 0–1440 min, scale 60 | 0x6015 | Rohwert Sekunden; Boost max 180 min, Timer max 24 h |
| timer_mode_target_temp | numeric 4–35 °C, scale 100 | 0x6016 | fz ignoriert Rohwert 0 |
| smart_temperature_control | binary true/false | 0x6017 | on=0x02, off=0x00; deaktiviert Öffnungsgrad + Accuracy |
| weekly_schedule_sunday … saturday | text, STATE_SET | **0x0201 Standard-Commands** | siehe Abschnitt 8 |

`configure`: bind hvacThermostat, Reporting für localTemperature, occupiedHeatingSetpoint, systemMode; Read von localTemperatureCalibration und 0xFC11 [0x0000, 0x6000, 0x6002–0x6007, 0x600E]. Außerdem `m.customTimeResponse("1970_UTC")` – der TRVZB fragt die Zeit über den Time-Cluster ab (relevant für Schedules; ZHA beantwortet Time-Requests ebenfalls).

### 3.1 Vergleich Z2M ↔ ZHA-Quirk

| Funktion | Z2M | ZHA-Quirk | Bemerkung |
|---|---|---|---|
| Attribute in 0xFC11 | 20 definiert (inkl. tamper/illumination) | 17 definiert | Gleiche IDs/Typen; Z2M hat 2 ungenutzte Extras |
| 0x600E Semantik | enum mit 3 Zuständen, Rohwert 3 wird abgebildet | **switch** (bool) | ZHA zeigt Fail-safe (2) als „on“ – siehe 10.2 |
| Wochenplan | ja, Standard-Cluster-Commands | **nein** (kein HA-Entity-Typ) | per Service/zigpy machbar, Abschnitt 8 |
| system_mode auto | ja | **nein** über climate (hvac_modes aus ctrl_sequence_of_oper) | Attribut-Write auf 0x001C=1 möglich |
| running_state | STATE_GET (Polling) | Reporting-Konfiguration in zha climate | |
| OTA | `ota: true` (Z2M-Index) | zigpy-OTA + Sonoff-Provider | Abschnitt 5 |
| Attribute nur im TRV-ZBT (Gen2) | 0x0010 faultCode, 0x0021 screenDirection, **0x6013 temperatureControlMode (ENUM8)**, 0x601C lowBatteryValveState, 0x601D weeklyScheduleActiveNum, 0x601E remoteAttributeLinkage (ARRAY), 0x6030, 0x6033, 0x6036/37 | – | **Nicht für TRVZB**; „valve control mode“ 0x6013 existiert beim TRVZB nicht, dort heißt Adaptive 0x6017 |

## 4. Vollständige Attribut-Tabelle

Skalierung „0,01 °C“ heißt: Rohwert 2150 = 21,50 °C. „FW ab“ nach Sonoff-Changelog/Z2M-Doku [H]; ohne Angabe = seit Auslieferung bekannt.

### 4.1 Cluster 0xFC11 (herstellerspezifisch, **ohne** Manufacturer-Code schreiben)

| ID | Name (Quirk / Z2M) | Typ | Skalierung / Werte | R/W | ZHA-Entität | Z2M-Expose | FW ab |
|---|---|---|---|---|---|---|---|
| 0x0000 | child_lock / childLock | Bool | 0/1 | RW | switch | binary | – |
| 0x2000 | – / tamper | uint8 | ? | ? | nein | nein | unklar, vermutlich Fremdkörper |
| 0x2001 | – / illumination | uint8 | ? | ? | nein | nein | unklar |
| 0x6000 | open_window / openWindow | Bool | 1 = Erkennung aktiv | RW | switch | binary (config) | – |
| 0x6002 | frost_protection_temperature | int16s | 0,01 °C; 4–35 °C | RW | number | numeric | – |
| 0x6003 | idle_steps | uint16 | Schritte | R | nein | numeric (diag) | – |
| 0x6004 | closing_steps | uint16 | Schritte | R | nein | numeric (diag) | – |
| 0x6005 | valve_opening_limit_voltage | uint16 | mV | R | nein | numeric (diag) | – |
| 0x6006 | valve_closing_limit_voltage | uint16 | mV | R | nein | numeric (diag) | – |
| 0x6007 | valve_motor_running_voltage | uint16 | mV | R | nein | numeric (diag) | – |
| 0x600B | valve_opening_degree | uint8 | 0–100 % | RW | number (disabled) | numeric | 1.1.4 / 1.2.1 |
| 0x600C | valve_closing_degree | uint8 | 0–100 % | RW | number (disabled) | numeric | 1.1.4 / 1.2.1 |
| 0x600D | external_temperature_sensor_value / externalTemperatureInput | int16s | 0,01 °C; gültig 0–9990 (0–99,9 °C), außerhalb wird ignoriert [F] | RW | number | numeric | 1.2.1 |
| 0x600E | external_temperature_sensor_enable / temperatureSensorSelect | uint8 (Z2M), Bitfeld vermutet | **W:** 0=intern, 1=extern. **R:** 0 intern, 1 extern, 2 Fail-safe (extern >2 h still → intern), 3 extern nach Wiederkehr [F] | RW + Report | switch | enum | 1.2.1 |
| 0x6011 | temperature_control_accuracy / tempAccuracy | int16s | 0,01 °C; −100…−20 (−1,0…−0,2 °C), Default −100 | RW | number | numeric | 1.3.0 |
| 0x6014 | temporary_mode / temporaryMode | uint8 | 0 = Boost, 1 = Timer (0xFF = aus nur beim TRV-ZBT belegt) | RW | 2× button | enum | 1.4.0 |
| 0x6015 | temporary_mode_duration / temporaryModeTime | uint32 | **Sekunden**; HA/Z2M zeigen Minuten (0–1440) | RW | number | numeric | 1.4.0 |
| 0x6016 | timer_mode_target_temperature / temporaryModeTemp | int16s | 0,01 °C; 4–35 °C | RW | number | numeric | 1.4.0 |
| 0x6017 | smart_temperature_control / smartTempControl | bitmap8 | 0x02 = Adaptive an, 0x00 = aus; Bit 0 unbekannt (Kommentar `# ControlModeType` in der Quirk) | RW | switch | binary | 1.4.4 |

### 4.2 Cluster 0x0201 Thermostat (Standard) [V zigpy-Definitionen; Support durch TRVZB laut Z2M-Konfiguration]

| ID | Name | Typ | Skalierung | R/W | ZHA | Z2M | Hinweis |
|---|---|---|---|---|---|---|---|
| 0x0000 | local_temperature | int16s | 0,01 °C | R + Report | climate.current_temperature | climate | Bei aktivem externem Sensor meldet das TRV hier den externen Wert [F, Z2M-PR-Autor] |
| 0x0008 | pi_heating_demand | uint8 | % | R | hvac_action, falls vorhanden | nicht exposed | Support durch TRVZB **unbestätigt** – testen |
| 0x0010 | local_temperature_calibration | int8s | 0,1 °C | RW | climate-Attribut/Number (ZHA ≥ 2024.3: ±7 °C) | −12.7…12.7 step 0.2 | Sonoff-App: ±0,2-Schritte „Temperature Offset“ seit 1.4.0 |
| 0x0012 | occupied_heating_setpoint | int16s | 0,01 °C; 4–35 °C | RW + Report | climate.target_temperature | climate | |
| 0x001B | ctrl_sequence_of_oper | enum8 | 2 = Heating_Only erwartet | R | bestimmt `hvac_modes` | – | Wenn nicht gemeldet → ZHA zeigt nur `off` (Issue #4623) |
| 0x001C | system_mode | enum8 | 0 Off, **1 Auto (Wochenplan)**, 4 Heat | RW + Report | climate.hvac_mode (off/heat) | off/auto/heat | Auto in ZHA nur per Attribut-Write erreichbar |
| 0x0020 | start_of_week | enum8 | | R | – | – | zum Verifizieren des Schedule-Supports lesen |
| 0x0021 | number_of_weekly_transitions | uint8 | | R | – | – | Z2M-Doku: 6/Tag |
| 0x0022 | number_of_daily_transitions | uint8 | | R | – | – | |
| 0x0029 | running_state | bitmap16 | Bit0 Heat_State_On, 0 = Idle | R + Report | hvac_action | running_state | Adaptive-Modus: „Heating“ trotz Ventil 100 % zu berichtet [F, iHost] |

Commands 0x0201 (Server): 0x00 setpoint_raise_lower, **0x01 set_weekly_schedule**, **0x02 get_weekly_schedule**, **0x03 clear_weekly_schedule** (laut Z2M-Issue #7100 beim TRVZB wirkungslos [F]). Client-Response 0x00 get_weekly_schedule_response.

### 4.3 Sonstige Cluster

| Cluster | Attribut | Nutzen |
|---|---|---|
| 0x0000 Basic | 0x4000 sw_build_id (string), 0x0001 app_version | Textuelle FW-Version, falls vom Gerät gefüllt (nicht verifiziert) |
| 0x0019 OTA (client) | current_file_version | **Quelle der FW-Version in ZHA** [V]: zha `Device.firmware_version` = `installed_version` der Update-Entität → Device-Registry `sw_version` (z. B. 0x00001404 = 1.4.4; 0x00001201 = 1.2.1; 0x00001300 = 1.3.0) |
| 0x0001 Power | battery_percentage_remaining | battery-Sensor; Reporting bis 24 h [Z2M-Doku] |
| 0x000A Time | – | TRV fragt Zeit ab (Z2M `customTimeResponse("1970_UTC")`); ZHA beantwortet Time-Requests nativ |
| 0xFC57 | unbekannt | in Signatur gelistet (Issue #3851), nirgends genutzt |

## 5. Firmware

### 5.1 Versionen und Features [H – eWeLink-Forum „【TRVZB】Firmware Changelog“, Erstpost von Sonoff-Mitarbeiter Archarr, zuletzt bearbeitet 2026-02-10]

| Version | Inhalt | Anmerkungen |
|---|---|---|
| 1.1.x (1.1.4–1.1.6) | Basis; 1.1.4: Ventilöffnungsgrad (laut Z2M-Doku), 1.1.6 empfohlen wegen Batterie-/Kalibrier-Genauigkeit | 1.1.4 wurde zeitweise aus dem Z2M-Index zurückgezogen [F] |
| 1.2.1 (Okt 2024) | Ventilöffnungsgrad über Hubs; **externer Sensor** (SNZB-02/02D/02P als Referenz) | zunächst nur ZBBridge-P; Z2M-Support ab herdsman-converters 23.0.0 (2025-02-23, PR #8873) |
| 1.3.0 (Feb 2025) | **Temp. Accuracy** (Hysterese 0,2–1,0 °C), DST-Umschaltung, Umbenennungen (Frost → Anti-freeze) | OTA 4864 = 0x1300; viele Nutzer sahen den externen Sensor erst hier, weil Z2M gleichzeitig nachzog |
| 1.4.0 / 1.4.1 (Nov 2025) | **Timer** („Schedule“ im Changelog = temporärer Timer), **Boost**, Temperature-Offset ±0,2, Display-Orientierung; 1.4.1 Bugfix bei Modus-Wechsel per Türsensor-Automation | OTA 5120/5121; HA-ZHA-Nutzer berichten `hvac_modes` nur `off` auf 1.4.1 (Issue #4623) |
| 1.4.4 (Feb 2026) | **Smart Temperature Control / Adaptive Mode** (PID-Ventilregelung); deaktiviert Öffnungsgrad + Accuracy | OTA 5124 = 0x1404; Release-Notes im zigpy-OTA sagen „1.4.44“ (Tippfehler) |
| 1.5.x | **keine Belege gefunden** (Stand 2026-10-12) | |

### 5.2 OTA über ZHA [V]

- zigpy aktiviert standardmäßig die Provider `zigpy_ota`, `ledvance`, `sonoff` (`zigpy/config/defaults.py: CONF_OTA_PROVIDERS_DEFAULT`). Der Sonoff-Provider liest https://zigbee-ota.sonoff.tech/releases/upgrade.json.
- Inhalt dieses Sonoff-Index heute für `model_id: TRVZB` (fw_manufacturer_id 4742 = 0x1286, fw_image_type 8199 = 0x2007): **1.3.0 (4864), 1.4.0 (5120), 1.4.1 (5121)** – 1.4.4 fehlt dort.
- zigpy-OTA (Repo `zigpy/zigpy-OTA`, default branch `dev`): `images/sonoff/1286-2007-00001404_fbc68d.ota.yaml` (PR #24, gemerged 2026-03-04, `model_names: [TRVZB]`, kein `channel`-Feld → stable). Der **Stable-Index** `release/files/stable/zigpy_v2_ota.json` enthält file_version 5124 mit binary_url auf Tag `2026.10.0`. Laut PR-Diskussion nur mit HA Core ≥ 2026.3.0 nutzbar (Schema-Bump wegen Telink-Blockgröße, zigpy ≥ 0.90 / PR #1781).
- Sonoff selbst schrieb im Changelog „firmware upgrades are not supported via ZHA … awaiting approval from the Home Assistant team“ – das ist durch den zigpy-OTA-Eintrag inzwischen überholt [V]. Nutzerberichte zu erfolgreichen 1.4.4-Updates via ZHA liegen vor (Issue #162320: „works with v1.4.4 and HA 2026.3.0b0“) [F].
- Versionsnummer in HA: Gerätekarte → „Firmware“ (= `sw_version` aus OTA `current_file_version`), oder Update-Entität `update.<trv>_firmware` (`installed_version`). Alternativ Attribut 0x0002 im OTA-Cluster (Client-Cluster, `cluster_type: out`) lesen.

## 6. Verhalten des externen Sensormodus

- **Aktivierung:** 0x600E := 1, danach 0x600D regelmäßig mit dem externen Wert beschreiben. Das TRV nutzt den Wert als Regelgröße und setzt **`local_temperature` (0x0201/0x0000) auf den externen Wert** – so beschreibt es der Z2M-PR-Autor photomoose: „the local temperature of the TRV will be set to the value of the external temperature sensor attribute“ [F]. Folge in ZHA: `climate.current_temperature` zeigt den externen Wert (ZHA liest ausschließlich `local_temperature/100` [V]). Eine separate interne Messung ist dann nicht mehr sichtbar.
- **Timeout/Fallback:** „Fallback to internal sensor appears to occur after 2 hours of silence from the external sensor“, Rückkehr zu extern beim nächsten Write; die eWeLink-App zeigt nach denselben 2 h eine Benachrichtigung [F, photomoose in PR #8873 und HA-Community 856444]. Beobachtete Statuswerte: 0x600E 1 → 2 (Fail-safe) → 3 (wieder extern) [F]. Blueprints schreiben deshalb bei jeder Sensoränderung **und** per `time_pattern` alle 15 min (ZHA-Blueprint von NMMunk/Weenig) bzw. empfehlen ≤ 90 min bei Sensoren mit 1 Nachkommastelle (Offler, 2025-03-21).
- **Gültiger Bereich:** 0–99,9 °C; Werte außerhalb werden still ignoriert [F, PR #8873]. Negative Außentemperaturen also nicht durchreichen.
- **Nebenwirkung:** Ein Write auf 0x600D kann ein per HA ausgeschaltetes TRV (system_mode off) wieder aufwecken/aktivieren (certainty6815, 2026-03-24) [F]. Empfehlung: nur schreiben, wenn `system_mode != off`, oder Verhalten mit den eigenen 4 Geräten testen.
- **Hysterese:** Ventil öffnet erst bei Ziel − Accuracy (Default 1 °C). Mit externem Sensor und Default-Accuracy wirkt das Verhalten träge; 0x6011 auf −20…−40 setzen oder Adaptive Mode (0x6017=2) nutzen [H/F].
- **ZHA-spezifische Einschränkung:** Die Quirk bildet 0x600E als Switch mit `on_value=1` ab; `is_on` ist dann `bool(wert)` [V zha switch.py], also zeigt der Switch bei 2 (Fail-safe) und 3 „on“. Der Fail-safe-Zustand ist in ZHA ohne eigene Entität **unsichtbar** – die eigene Integration sollte 0x600E selbst lesen/auf Reports hören.
- Unbekannt/unverifiziert: ob das TRV 0x600D zurückmeldet (Report) und ob es den Wert per Zeitstempel verwirft; Sonoff dokumentiert den Timeout nicht.

## 7. Fenster-offen (0x6000 `open_window`)

- **Status vs. Eingang:** Das Attribut ist ein R/W-Bool, das die **geräteeigene Erkennung ein-/ausschaltet** [V: Z2M-Kategorie `config` mit Beschreibung „Automatically turns off the radiator when local temperature drops by more than 1.5°C in 4.5 minutes“; H: Sonoff Help Center „Open Windows Detection“-Setting in der eWeLink-App]. Es ist **kein** Eingang „Fenster ist jetzt offen“. Der TRVZB exposed keinen separaten Erkennungs-Status (der Nachfolger TRV-ZBT hat dafür `open_window_detected`, read-only, und ein Z2M-Feature-Request #33211 wünscht genau die Settable-Variante).
- **Was das TRV tut** [H, Sonoff Help Center/Manual]: Erkennt Temperatursturz > 1,5 °C in 4,5 min → Ventil schließt, Display „OP“, Setpoint intern auf 12 °C. Ende der Phase: Temperaturanstieg > 1,5 °C in 4,5 min, Setpoint-Änderung per App/Gerät, oder 30 min ohne Bedienung. Z2M-Doku nennt dieselben Werte.
- **Wechselwirkung mit externem Sensor:** Nicht dokumentiert. Da `local_temperature` dem externen Wert folgt, dürfte die Erkennung auf dem extern gemeldeten Verlauf arbeiten – nur sinnvoll, wenn der externe Sensor schnell genug meldet (unverifiziert).
- **Für das Projekt (Fensterkontakt → TRV):** Es gibt keinen Zigbee-Eingang dafür. Optionen: (a) `system_mode` 0x001C := 0 (Off; Frostschutz 0x6002 bleibt aktiv) und beim Schließen alten Modus/Setpoint wiederherstellen; (b) Setpoint temporär senken (Setpoint-Write beendet jede laufende OP-Phase [H]); (c) Timer-Modus 0x6014=1 mit 0x6016 niedriger Temperatur und 0x6015 Dauer – kehrt automatisch zurück, aber die Dauer muss vorab bekannt sein. Forum-Idee, den `open_window`-Switch per Fensterkontakt einzuschalten (Matthias1111/NMMunk, Jan 2026), ist ungetestet [F] und nach Code-Lage nur das Aktivieren der Erkennung, nicht das Auslösen.

## 8. Standard-Wochenplan über 0x0201 [V zigpy-Signaturen, Z2M-Implementierung]

- Der TRVZB speichert den Wochenplan **im Gerät** und fährt ihn in `system_mode = Auto (1)` (Display ⏲). Z2M setzt ihn **ausschließlich mit dem Standard-Command `set_weekly_schedule` (0x01)**, `mode_for_sequence = Heat (0x01)`, pro Tag ein Command, max. **6 Transitions/Tag**, erste Transition muss 00:00 sein, Temperaturen 4–35 °C in 0,5-Schritten; Rückmeldung über `get_weekly_schedule_response` (Client-Command 0x00). Es gibt keine herstellerspezifischen Schedule-Attribute beim TRVZB (im Gegensatz zum TRV-ZBT: 0x601D `weeklyScheduleActiveNum`).
- zigpy-Schema (`zigpy/zcl/clusters/hvac.py`): `set_weekly_schedule(num_transitions_for_sequence: enum8, day_of_week_for_sequence: SeqDayOfWeek(bitmap8: Sunday=0x01 … Saturday=0x40, Away=0x80), mode_for_sequence: SeqMode(bitmap8: Heat=0x01, Cool=0x02), values: List[int16s])`. `values` ist eine **flache Liste** aus Paaren `[transition_time_min, heat_setpoint_0.01°C, …]`.
- Bekannte Probleme [F]: `clear_weekly_schedule` (0x03) ohne Wirkung (Z2M-Issue #7100, Feb 2024); Wochenplan nach Z2M-Updates „verschwunden“ (nur Anzeige, Issue #30435). Workaround zum Löschen: Tag mit einer Transition `00:00/<Setpoint>` überschreiben.
- ZHA: keine Entität; Issue #3851 seit Feb 2025 offen. Per `zha.issue_zigbee_cluster_command` oder direkt aus Python nutzbar (Snippets unten). `climate.set_hvac_mode` kann `auto` nicht setzen, weil ZHA `hvac_modes` aus `ctrl_sequence_of_oper` ableitet (Heating_Only → `[off, heat]`; HEAT_COOL=Auto nur bei Cooling_and_Heating) [V `zha/application/platforms/climate/const.py`]. Lösung: `system_mode` 0x001C := 1 per Attribut-Write; ZHA zeigt den Zustand dann als `heat_cool` (SYSTEM_MODE_2_HVAC) – kosmetisch unschön, funktional ok.
- Hinweis zur Zeitbasis: Das TRV holt sich die Uhrzeit über den Time-Cluster vom Koordinator. Vor dem ersten Schedule-Test prüfen, dass das Gerät die Zeit hat (ZHA beantwortet Time-Reads; in Z2M ist `customTimeResponse` explizit konfiguriert).

## 9. Schreib-/Lesezugriff aus Home Assistant

### 9.1 Services/Actions [V `homeassistant/components/zha/services.py` + `services.yaml`]

`zha.set_zigbee_cluster_attribute` – Felder: `ieee` (string), `endpoint_id` (int), `cluster_id` (int), `cluster_type` (`in`|`out`, default `in`), `attribute` (int **oder Name**), `value` (int | bool | string; Strings mit `0x`-Präfix werden für int-Typen geparst), `manufacturer` (int, optional; **weglassen** – siehe 10.1). Intern: `zha_device.write_zigbee_attribute()` → `cluster.find_attribute()` → `convert_zcl_value()` → `cluster.write_attributes({attr: value}, manufacturer=…)`.

```yaml
# (a) Externe Temperatur 21.4 °C setzen und externen Modus aktivieren
action: zha.set_zigbee_cluster_attribute
data:
  ieee: "a4:c1:38:xx:xx:xx:xx:xx"
  endpoint_id: 1
  cluster_id: 64529          # 0xFC11
  cluster_type: in
  attribute: 24590           # 0x600E external_temperature_sensor_enable (oder Name als String)
  value: 1
---
action: zha.set_zigbee_cluster_attribute
data:
  ieee: "a4:c1:38:xx:xx:xx:xx:xx"
  endpoint_id: 1
  cluster_id: 64529
  attribute: 24589           # 0x600D external_temperature_sensor_value
  value: 2140                # int16s, 0.01 °C → 21.40 °C
---
# Wochenplan Montag: 00:00 → 18 °C, 06:00 → 21 °C, 22:00 → 18 °C (Standard-Command 0x01)
action: zha.issue_zigbee_cluster_command
data:
  ieee: "a4:c1:38:xx:xx:xx:xx:xx"
  endpoint_id: 1
  cluster_id: 513            # 0x0201
  cluster_type: in
  command: 1                 # set_weekly_schedule
  command_type: server
  params:
    num_transitions_for_sequence: 3
    day_of_week_for_sequence: 2      # Monday-Bit
    mode_for_sequence: 1             # Heat
    values: [0, 1800, 360, 2100, 1320, 1800]
---
# Auto-Modus (Wochenplan) aktivieren
action: zha.set_zigbee_cluster_attribute
data:
  ieee: "a4:c1:38:xx:xx:xx:xx:xx"
  endpoint_id: 1
  cluster_id: 513
  attribute: 28              # 0x001C system_mode
  value: 1                   # Auto
```

Lesen: Es gibt **keinen** Service, aber den Admin-WebSocket-Befehl `zha/devices/clusters/attributes/value` mit `ieee`, `endpoint_id`, `cluster_id`, `cluster_type`, `attribute` (int), optional `manufacturer` → liest live (`allow_cache=False`) und gibt den Wert als String zurück [V `websocket_api.py`]. Die UI „Gerät → ⋮ → Zigbee-Gerät verwalten → Cluster“ nutzt genau diesen Befehl bzw. den Service (Feld „Manufacturer code override“) [V `zha-cluster-attributes.ts`].

### 9.2 Python aus einer eigenen Custom-Integration [V HA core `zha/helpers.py`, zha-Library `device.py`/`gateway.py`, zigpy `zcl/__init__.py`]

Seit HA 2024.8 ist die Logik in der Library `zha`; HA hält nur Proxies. Zugriffspfad:

```python
from homeassistant.components.zha.helpers import (
    get_zha_gateway,          # -> zha.application.gateway.Gateway
    get_zha_gateway_proxy,    # -> ZHAGatewayProxy (hat .gateway, .device_proxies[EUI64].device)
    async_get_zha_device_proxy,  # (hass, device_registry_id) -> ZHADeviceProxy
)
from zigpy.types import EUI64

async def write_external_temp(hass, ieee_str: str, temp_c: float) -> None:
    gateway = get_zha_gateway(hass)                  # raises ValueError wenn ZHA nicht läuft
    zha_dev = gateway.get_device(EUI64.convert(ieee_str))   # zha.zigbee.device.Device | None
    if zha_dev is None:
        raise RuntimeError("TRV nicht in ZHA")
    zigpy_dev = zha_dev.device                       # zigpy.device.Device
    cluster = zigpy_dev.endpoints[1].in_clusters[0xFC11]   # CustomSonoffCluster (wenn Quirk aktiv)

    # Schreiben – KEIN manufacturer übergeben (Quirk-Defs haben manufacturer_code=None)
    res = await cluster.write_attributes({
        "external_temperature_sensor_enable": 1,
        "external_temperature_sensor_value": round(temp_c * 100),   # int16s, 0.01 °C
    })
    # res == [[WriteAttributesStatusRecord(status=SUCCESS, attrid=None)]] bei Erfolg

    # Lesen (live, ohne Cache)
    ok, failed = await cluster.read_attributes(
        ["external_temperature_sensor_enable"], allow_cache=False
    )
    # ok == {'external_temperature_sensor_enable': 1|2|3}

# Alternative über das zha-Device (macht intern dasselbe inkl. Typkonvertierung):
#   await zha_dev.write_zigbee_attribute(1, 0xFC11, "external_temperature_sensor_value", 2140)
#   cluster = zha_dev.async_get_cluster(1, 0xFC11, cluster_type="in")

# Attribut-Reports (z. B. 0x600E springt auf 2) mitbekommen:
class _SonoffListener:
    def attribute_updated(self, attrid: int, value, timestamp) -> None:
        if attrid == 0x600E:
            ...  # Fail-safe erkennen, erneut schreiben
cluster.add_listener(_SonoffListener())

# Wochenplan direkt:
from zigpy.zcl.clusters.hvac import Thermostat
th = zigpy_dev.endpoints[1].in_clusters[Thermostat.cluster_id]
await th.set_weekly_schedule(
    num_transitions_for_sequence=3,
    day_of_week_for_sequence=Thermostat.SeqDayOfWeek.Monday,
    mode_for_sequence=Thermostat.SeqMode.Heat,
    values=[0, 1800, 6 * 60, 2100, 22 * 60, 1800],
)
await th.write_attributes({"system_mode": Thermostat.SystemMode.Auto})
```

Ohne angewandte Quirk (reiner zigpy-`Cluster` für 0xFC11 ohne AttributeDefs) schlägt `write_attributes({0x600D: …})` in `find_attribute` fehl; dann `cluster.write_attributes_raw([foundation.Attribute(0x600D, foundation.TypeValue(type=0x29, value=t.int16s(2140)))], manufacturer_code=None)` nutzen – oder die eigene Integration bringt eine eigene Quirk mit (Abschnitt 11).

Achtung Timing: Der TRVZB ist ein Sleepy End Device; Writes gehen in die Parent-Queue und werden beim nächsten Poll (typisch wenige Sekunden) zugestellt. `write_attributes` wartet auf die Antwort; `DeliveryError`/Timeout abfangen und wiederholen.

### 9.3 zha-toolkit als Referenz [V]

Repo https://github.com/mdeweerd/zha-toolkit: nicht archiviert, letzte Version **v1.2.2 (2026-08-24)**, letzter Commit 2026-10-09 (Dependabot), 307 Stars, 31 offene Issues. `utils.py`:

```python
def get_zha_gateway(hass):
    if parse_version(HA_VERSION) >= parse_version("2024.8"):
        return zha_helpers.get_zha_gateway(hass)
    ...
def get_zha_gateway_hass(hass):   # Proxy
    if parse_version(HA_VERSION) >= parse_version("2024.8"):
        return zha_helpers.get_zha_gateway_proxy(hass)
```

Geräte: `getattr(gateway_proxy, "device_proxies")`; Cluster: `get_cluster_from_params()` (sucht Endpoint automatisch, in- vor out-clusters); Write in `zcl_attr.py`: `cluster.write_attributes(attrs, manufacturer=params[MANF])` mit `read_before_write`/`read_after_write`. Services `zha_toolkit.attr_read` / `attr_write` (Parameter `ieee`, `endpoint`, `cluster`, `attribute`, `attr_type`, `attr_val`, `manf`, `state_id`, …) sind für Debugging aus HA heraus praktisch, als Laufzeit-Abhängigkeit des eigenen Projekts aber unnötig.

## 10. Datentyp- und Protokollfallen

### 10.1 Manufacturer-Code

- Zigpy-Logik [V `_get_effective_manufacturer_code`]: explizit übergebener `manufacturer` > `manufacturer_code` der Attributdefinition > `is_manufacturer_specific` > Fallback „Cluster-ID ≥ 0xFC00 → Geräte-Manufacturer-ID (0x1286)“. Die Upstream-Quirk setzt **`manufacturer_code=None`** → Frames ohne Manufacturer-Code. Sonoffs eigenes Script erzwingt dasselbe (`_is_manuf_specific = False`). Z2M sendet ebenfalls ohne.
- Konsequenz: Beim Service **kein** `manufacturer: 0x1286` angeben. Mit Quirk führt ein expliziter Code sogar zu `KeyError` in `find_attribute` (Definition passt nicht zum Code) bzw. zu einem Frame, das das Gerät voraussichtlich mit `UNSUPPORTED_ATTRIBUTE` (0x86) beantwortet. Der HA-Issue #162320 (2026.2.x, `UNSUPPORTED_ATTRIBUTE` auf 0x600E/0x600D) verschwand nach Entfernen des Sonoff-Scripts – Doppel-Quirk, Ursache nicht abschließend geklärt [F].
- Sonoff-Manufacturer-ID zur Sicherheit: 0x1286 (4742) [V OTA-Index].

### 10.2 Wertkodierung

| Attribut | Beim Schreiben | Fallen |
|---|---|---|
| 0x600D external value | `int16s`, Rohwert = °C × 100, nur 0…9990 gültig | Rundung: zha-Number-Entität rechnet `int(value / 0.01)` → z. B. 19,9 → **1989** (Truncation) [V `number/__init__.py`]. In eigener Integration `round(t*100)` verwenden. Negative Werte werden ignoriert [F]. |
| 0x600E enable | `uint8` 0 oder 1 schreiben | Lesen liefert 0/1/2/3; 2 = Fail-safe. ZHA-Switch zeigt 2 und 3 als „on“. Z2M-Issue #29650: „keeps changing“ ist dieses Fail-safe-Verhalten, kein Bug [F]. Nicht `t.Bool` verwenden (Rohwert 3 würde als Bool-Typ 0x10 falsch serialisiert). |
| 0x6000 open_window | `Bool` 0/1 | Nur Erkennung ein/aus (Abschnitt 7). |
| 0x6002, 0x6011, 0x6016 | `int16s` × 100 | 0x6011 nur −100…−20. |
| 0x6015 duration | `uint32` **Sekunden** | HA/Z2M zeigen Minuten; Boost max 180 min, Timer max 1440 min [Z2M]. |
| 0x6014 temporary_mode | `uint8` 0 Boost / 1 Timer | Beenden: Gerät kehrt nach Ablauf selbst zurück; 0xFF („disabled“) ist nur für TRV-ZBT belegt – am TRVZB testen. |
| 0x6017 smart_temperature_control | `bitmap8` 0x02 / 0x00 | Bedeutung von Bit 0 unbekannt; FW < 1.4.4 → UNSUPPORTED_ATTRIBUTE. |
| 0x0201/0x001C system_mode | `enum8` 0/1/4 | ZHA zeigt Auto als `heat_cool`. |
| 0x0201/0x0010 calibration | `int8s` × 10 | Range beim TRVZB −127…127 (Z2M −12.7…12.7). |

### 10.3 Firmware-Gating

Auf FW < 1.4.4 antworten 0x6017-Writes mit UNSUPPORTED_ATTRIBUTE, auf FW < 1.4.0 zusätzlich 0x6014–0x6016, auf FW < 1.3.0 0x6011, auf FW < 1.2.1 0x600D/0x600E. Vor dem Schreiben die FW-Version (OTA `current_file_version`) prüfen; PR #4792 will genau das in der Quirk nachrüsten.

## 11. Braucht das Projekt eine eigene Quirk (v2)?

**Für Kern-Funktion (externer Wert + „Fenster“ + Schedule) nicht nötig**, weil:

1. Die Upstream-Quirk definiert alle Attribute mit korrekten Typen und ohne Manufacturer-Code; die Integration kann die bestehende `CustomSonoffCluster`-Instanz direkt nutzen (`write_attributes` per Name).
2. Wochenplan und `system_mode=Auto` laufen über den Standard-Cluster, der ohne Quirk vorhanden ist.
3. Eine zweite Quirk für dieselbe Modellkennung konkurriert mit Upstream (`custom_quirks_path`, `CONF_CUSTOM_QUIRKS_PATH` [V HA `zha/const.py`]) – genau das verursachte die 2026.2-Probleme.

**Sinnvoll wäre eine eigene Quirk (oder ein Upstream-PR), wenn:**

- der Fail-safe-Zustand als HA-Entität sichtbar sein soll (`sensor`/`select` auf 0x600E mit Enum `0 internal / 1 external / 2 external_offline / 3 external_restored` statt Switch) – Upstream-PR wäre der bessere Weg (TheJulianJES ist responsiv; PR #4792 zeigt die Richtung);
- `hvac_modes` `auto` enthalten soll (ctrl_sequence_of_oper-Override ist ein Hack; eher: eigene `select`-Entität „Betriebsart“ in der Integration, die 0x001C schreibt);
- Reporting-Konfiguration für 0x600E/0x600D gewünscht ist (die Quirk konfiguriert kein Reporting für 0xFC11; ob das Gerät Reports für 0x600E sendet, ist unbestätigt → mit `configure_reporting` testen, sonst zyklisch lesen).

**Empfohlene Architektur der Integration:** Pro TRV ein Koordinator, der (1) den externen Sensor abonniert und bei Änderung ≥ 0,1 °C sowie spätestens alle 60 min 0x600D schreibt, (2) nach jedem Write bzw. per Listener 0x600E prüft und bei 2 erneut 0x600E:=1 + 0x600D schreibt, (3) Fensterkontakt → `system_mode` Off/alten Zustand merken und wiederherstellen (oder Timer-Modus), (4) Schedule-Editor schreibt Tageslisten per `set_weekly_schedule` und schaltet `system_mode` auf Auto, (5) Writes nur ausführt, wenn die FW die Attribute kann (OTA-Version lesen), und (6) `DeliveryError`/Timeouts mit Backoff wiederholt (Sleepy End Device).

## 12. Risiken (Kurzliste)

1. 2-h-Fail-safe: Bei stabilen Temperaturen (keine Änderung) ohne Zeit-Trigger fällt das TRV zurück – Zeit-Trigger ist Pflicht [F].
2. 0x600E in ZHA als Switch → Fail-safe unsichtbar; eigene Überwachung nötig [V].
3. `open_window` ist kein Eingang; Fenster-Logik muss über system_mode/Setpoint/Timer laufen [V/H].
4. ZHA kann `auto` nicht über die climate-Entität; Attribut-Write erforderlich; Anzeige dann `heat_cool` [V].
5. Firmware-Streuung in 4 Geräten → unterschiedliche Entitätssätze und UNSUPPORTED_ATTRIBUTE; PR #4792 noch nicht gemerged [V].
6. OTA 1.4.4 nur via zigpy-OTA (HA ≥ 2026.3); Sonoff-Server liefert nur bis 1.4.1 [V].
7. Write auf 0x600D kann ein ausgeschaltetes TRV aufwecken [F].
8. `clear_weekly_schedule` wirkungslos; Löschen durch Überschreiben [F].
9. Adaptive Mode (1.4.4) meldet `running_state` Heating bei geschlossenem Ventil – `hvac_action` nicht als Kessel-Anforderung nutzen [F].
10. Number-Entität der Quirk truncated (19,9 → 19,89) – nur kosmetisch, aber bei eigener Implementierung `round()` nutzen [V].

## 13. Quellen

Quellcode (gelesen):
- Quirk `dev`: https://raw.githubusercontent.com/zigpy/zha-device-handlers/dev/zhaquirks/sonoff/trvzb.py
- Z2M-Konverter: https://raw.githubusercontent.com/Koenkk/zigbee-herdsman-converters/master/src/devices/sonoff.ts
- Sonoff ZHA-Script: https://resource-wangsu.helplook.net/docker_production/a4j0b0/article/F1vhxZrI/attachments/TRVZB_ZHA_Script.zip
- zigpy HVAC-Cluster: https://raw.githubusercontent.com/zigpy/zigpy/dev/zigpy/zcl/clusters/hvac.py
- zigpy ZCL-Core (Manufacturer-Code-Logik, write/read): https://raw.githubusercontent.com/zigpy/zigpy/dev/zigpy/zcl/__init__.py
- zigpy OTA-Provider/Defaults: https://raw.githubusercontent.com/zigpy/zigpy/dev/zigpy/ota/providers.py , https://raw.githubusercontent.com/zigpy/zigpy/dev/zigpy/config/defaults.py
- zha-Library: https://raw.githubusercontent.com/zigpy/zha/dev/zha/zigbee/device.py , …/zha/application/gateway.py , …/zha/application/platforms/climate/__init__.py , …/climate/const.py , …/platforms/switch.py , …/platforms/number/__init__.py , …/application/helpers.py
- HA core ZHA: https://raw.githubusercontent.com/home-assistant/core/dev/homeassistant/components/zha/helpers.py , …/services.py , …/services.yaml , …/websocket_api.py , …/const.py
- HA frontend: https://raw.githubusercontent.com/home-assistant/frontend/dev/src/panels/config/integrations/integration-panels/zha/zha-cluster-attributes.ts
- zha-toolkit: https://raw.githubusercontent.com/mdeweerd/zha-toolkit/main/custom_components/zha_toolkit/utils.py , …/zcl_attr.py ; Repo-Metadaten via api.github.com
- Sonoff OTA-Index: https://zigbee-ota.sonoff.tech/releases/upgrade.json
- zigpy-OTA: https://github.com/zigpy/zigpy-OTA (dev: images/sonoff/1286-2007-00001404_fbc68d.ota.yaml; Stable-Index https://raw.githubusercontent.com/zigpy/zigpy-ota/release/files/stable/zigpy_v2_ota.json ; PR #24 https://github.com/zigpy/zigpy-OTA/issues/24 )
- ZHA-Blueprint (Gist Weenig): https://gist.github.com/Weenig/c4bd8e81a6033fde588d72140a7d4eac

PRs/Issues:
- https://github.com/zigpy/zha-device-handlers/pull/3358 , /pull/3909 , /pull/4740 , /pull/4792 (Draft) , /issues/3851 , /issues/3907 , /issues/4117 , /issues/4623
- https://github.com/zigpy/zigpy/issues/1679
- https://github.com/home-assistant/core/issues/162320
- https://github.com/Koenkk/zigbee-herdsman-converters/pull/8873 , /issues/7100
- https://github.com/Koenkk/zigbee2mqtt/issues/29650 , /issues/30435 , /issues/33211 , /discussions/19669

Hersteller:
- eWeLink-Forum Changelog (Sonoff-Mitarbeiter): https://forum.ewelink.cc/t/trvzb-firmware-changelog/74377 (JSON: /t/74377.json)
- Sonoff Help Center TRVZB: https://help.sonoff.tech/docs/trvzb
- Sonoff-Blog (ZHA + externer Sensor; Seite war während der Recherche rate-limited, nicht gelesen): https://sonoff.tech/en-us/blogs/news/sonoff-trv-with-an-external-temperature-sensor-home-assistant
- Z2M-Gerätedoku: https://www.zigbee2mqtt.io/devices/TRVZB.html

Community:
- https://community.home-assistant.io/t/sonoff-trvzb-external-temperature-sensor-zha-integration/968017
- https://community.home-assistant.io/t/sonoff-trvzb-external-temperature-sensor-calibration/856444
- https://community.home-assistant.io/t/new-sonoff-trv-firmware/949900
- https://community.home-assistant.io/t/perfect-heating-automation-with-sonoff-trvzb/800371
- https://github.com/jmcollin78/versatile_thermostat/discussions/967
- https://forum.ewelink.cc/t/trvzb-adaptive-mode-issue/208357 , https://forum.ewelink.cc/t/feature-request-home-assistant-integration-for-trvzb-schedules/205792
