# 01 – Bestehende Lösungen für raumbasierte TRV-Regelung in Home Assistant

Stand: 2026-10-12. Recherche-Agent (nur gelesen, nichts ausgeführt). Fokus: Sonoff TRVZB über ZHA, Vergleich gegen Olivers Idee („Integration füttert das TRV nur mit externer Temperatur + Fensterstatus, regelt nicht selbst, keine eigene climate-Entität“).

> **Wichtigste Korrektur vorab:** Der TRVZB (Gen 1) hat laut ZHA-Quirk, Z2M-Konverter und Sonoff-Doku **keinen nativen Eingang für einen externen Fensterstatus**. Das Attribut `open_window` (0x6000) ist nur der Ein/Aus-Schalter der *geräteeigenen* Fenster-Erkennung (Temperatursturz > 1,5 K in 4,5 min). Der native Eingang existiert nur für die externe Temperatur (0x600D/0x600E). Fensterlogik muss die Integration also selbst auf Setpoint/Mode/Ventil abbilden (Details in Abschnitt 3 und 7).

---

## 1. Feature-Matrix

| Kriterium | Better Thermostat (BT) | Versatile Thermostat (VT) | Advanced Heating Control (AHC, Blueprint) | Schedy (AppDaemon) | Smart Thermostat PID | Generic Thermostat (Core) | Scheduler Component | TRVZB-External-Sensor-Blueprints (Sonoff / Community) | **Olivers Idee** |
|---|---|---|---|---|---|---|---|---|---|
| Eigene climate-Entität? | **Ja** (1 pro Raum, „virtuelles“ Thermostat) | **Ja** (VTherm pro Raum + optional zentrale Config-VTherm) | Nein (Automation; braucht Helper) | Nein (steuert TRV-climate direkt) | Ja | Ja | Nein (Schalter pro Zeitplan) | Nein | **Nein** |
| Methode externe Temperatur | 3 Kalibrierungstypen: Setpoint-Verschiebung („Target Temperature Based“), Offset (`local_temperature_calibration` via `number.set_value`), Direct-Valve (opening/closing degree). **Seit 1.9.2 für TRVZB zusätzlich nativ:** schreibt Raumtemperatur in `external_temperature_input`, schaltet Sensorquelle auf extern, Keep-alive alle 30 min | over_climate: Setpoint-Verschiebung (PI-Offset, Modi light/medium/strong/slow/expert) oder Direct-Valve (TPI → opening/closing degree). Keine native External-Sensor-Nutzung | 3 Modi: Offset-Kalibrierung (`Offset_alt + (Sensor − TRV)`), generische Kalibrierung (Setpoint-Shift), **„external sensor“** (schreibt Sensorwert in number, schaltet select/switch auf extern, Keep-alive nach 20 min) | Nur Setpoint; kein externer Sensor | PID → PWM-Switch oder 0–100-Ventil | Nur Switch | – | **Nativ:** `number.set_value` auf `external_temperature_input` bei jedem Sensor-Update | **Nativ** (0x600D + 0x600E) |
| Fenster | Binary-Sensor/Gruppe, Delay in beide Richtungen; alt: `unknown` = offen; 2.0-Beta: offline-Kontakt = geschlossen | Sensor-Modus (Delay, Aktion off/fan_only/frost/eco) **oder** Auto-Erkennung per Temperatur-Slope (°C/h, max. Dauer) | 1 Entität (Gruppe für mehrere), Reaktionszeit 30 s, Fenster-Temp oder `off` | Ja (über Expressions/Statistik) | Nein | Nein | Nein | Nein | Ja (N Sensoren, „zuletzt offen bleibt offen“) |
| Abwesenheit | Nur Preset `away` (keine Presence-Logik) | Presence-Sensor + Away-Temperatur pro Preset; Zentralmodus | Personen, Device-Tracker, Proximity, Presence-Sensor, Gast, Party, Away-Offset | Über Expressions | Preset `away` | Preset `away` | Als Bedingung | Nein | Roadmap (Offset oder Aus) |
| Scheduling | Nein (extern) | Nein (extern, z. B. Scheduler) | Mehrere Schedule-Helper + Adjustments-Liste | **Kern-Feature** | Nein | Nein | **Kern-Feature** | Nein | Roadmap: nativ im TRV (Zigbee Weekly Schedule) |
| Presets | 7 (away/eco/comfort/home/sleep/boost/activity) + Cooling-Varianten, je `number` | eco/comfort/boost (+ abs-/clim-Varianten, hidden: away/power/safety) | comfort/eco/frost/party | frei | 7 | 6 | – | – | Roadmap (Frost/Eco/Komfort/Boost) |
| Multi-TRV pro Raum | Ja (mehrere „real thermostats“) | Ja (bis 4 Underlyings) | Ja (Liste, `repeat`) | Ja (Actors pro Room) | Mehrere Ausgänge | 1 Switch | n/a | Mehrere number-Entitäten | Ja (M TRVs) |
| Write-Verifikation / Retry | `blocking=True`; wartet bis 360 s auf Bestätigung, dann **„assuming applied“** (#2404); 1.9.0: ignorierte Offsets werden neu gesendet; 1.9.3: exponentielles Backoff pro Regelzyklus; 2.0-Beta: Retries 30 s–30 min (Kühlung), Decision-Log | Kein Rücklesen; Schutz nur über `regulation_threshold` + `period_min`; Keep-alive für Switches | **Keine** (nur „nur senden, wenn anders“ + `action_call_delay`) | **Ja:** sendet erneut, bis Actor den Wert zurückmeldet | Nein | `keep_alive` | `track_conditions`, kein Retry bei Fehlern | Nein (Conditions gegen unknown/unavailable) | **Kern-Feature** (Schreiben → Rücklesen → N Retries → Fehler) |
| Sonoff-TRVZB-Support | `model_fixes/TRVZB.py`: Ventil-Prozent (opening/closing), 10 %-Bump-Workaround beim Schließen, External-Temp (0–99,9, 1 Dezimale), Sensorquelle umschalten | Seit 6.8 „Sonoff TRVZB“-Menü (opening/closing degree + offset-calibration), Regel „Threshold ≥ 3 %, sonst verliert das TRV seine Kalibrierung“ | Generisch (external-sensor-Modus passt, wenn Entitäts-ID „external“ enthält) | Generisch | Generisch (Ventil 0–100) | – | – | Speziell | Speziell |
| ZHA vs. Z2M | Beides über climate-Entität; README nennt Z2M/deCONZ/Tado/generic; TRVZB-Entitätssuche über `translation_key`/ID-Teile (seit #2312 deterministisch); 1.9.3: Direct-Valve für TRVZB unter ZHA | Beides über Entitäten; Issues fast alle mit Z2M | Beides (Entitätsnamen-Heuristik „offset“/„calibration“/„external“) | Beides | Beides | – | Beides | Community-Blueprint: Z2M ≥ 2.1.2; Sonoff-Guide: ZHA + Z2M | ZHA (Quirk-Entitäten oder Raw-Attribute) |
| Entitäten pro Raum (ca.) | 1 climate + 7 number (14 mit Kühlung) + 1 switch/TRV + 6 sensor (+4 MPC / +5 PID) → **15–25** | 1 climate + 5–15 sensor/select/switch + Preset-numbers → **10–22** (+ zentrale Config) | 0 (aber Helper: schedule, input_boolean, input_number pro Raum) | 0 | ~1–3 | 1 | 1 pro Zeitplan | 0 | Ziel: wenige Diagnose-Entitäten |
| Wartungsstatus / letztes Release | **1.9.3 (2026-10-06)**, 2.0.0-Beta1 (2026-10-11), sehr aktiv, Min-HA 2026.7.2 (2.0: 2026.9) | **10.5.1 (2026-10-12)**, sehr aktiv, Plugin-System seit 10.0 | v5.5.7, letzter Commit **2026-01-20**, Doku „under construction“ | **Maintenance-Mode seit 2022-02-23** | 2026.10.1 (2026-10-12), aktiv | Core | v3.3.8 (2024-11-16) | Community v1.0.1 (2025) | – |

---

## 2. Pro Lösung: Architektur und Schmerzpunkte

### 2.1 Better Thermostat (KartoffelToby/better_thermostat)

**Architektur**
- Eine virtuelle `climate`-Entität pro Raum, die 1..n echte TRV-`climate`-Entitäten „ersetzt“. Raumsensor Pflicht, optional Feuchte, Außensensor/Wetter (3-Tage-Mittel, Prüfung täglich 05:00, „off temperature“-Schwelle), Fenstersensor(-gruppe) mit Delay, Toleranz (Hysterese).
- Kalibrierungstyp: *Target Temperature Based* (Setpoint-Shift), *Offset Based* (schreibt `local_temperature_calibration`, keyword-basierte Entitätssuche: `local_temperature_calibration`/`local_calibration`/`calibration`), *Direct Valve* (Ventilprozent). Kalibrierungsmodus (Regler): Normal, Aggressive, AI Time Based (Default), MPC v2 (seit 1.9.0, Raumgrößen-Presets, eigener QP-Solver seit 1.9.3), PID (Auto-Tune Beta), TPI.
- **TRVZB-spezifisch (`model_fixes/TRVZB.py`)**: `maybe_set_sonoff_valve_percent` (schreibt opening degree und `100 − x` als closing degree; Workaround: beim weiteren Schließen erst +10 % öffnen, nach 5 s Zielwert), `maybe_set_external_temperature` (clamp 0–99,9, 1 Dezimale) + `maybe_select_external_sensor`. Seit 1.9.2: Raumtemperatur alle **30 min** erneut gesendet (wegen 2-h-Fail-safe des TRV).
- Schreibpfad (`adapters/generic.py`): `climate.set_temperature`/`number.set_value` mit `blocking=True`, Offset-Clamp auf Gerätebereich (Default ±6 bzw. ±10), **keine** Rundung auf `step`, 3 s Sleep, dann HVAC-Mode erneut setzen. Keine Retries im Adapter; die Controlling-Schicht wartet bis 360 s auf Bestätigung und nimmt den Wert dann als angewendet an.
- `initial_tweak` schaltet am TRV `window_detection`/`away_mode`-Switches **aus** (Konfliktvermeidung) und setzt Child-Lock.
- Entitäten: `number` pro Preset (CONFIG), `switch` child_lock pro TRV (+ PID-Autotune), `sensor`: external_temp_ema, ema_1h, temp_slope, heating_power, heat_loss, solar_intensity (+ 4 MPC- oder 5 PID-Sensoren).
- 2.0.0-Beta1 (2026-10-11): Entscheidungslogik von Gerätebefehlen getrennt, Decision-Log, Fallback-Leiter bei Sensorausfall (TRV-Mittelwert → Ventil halten → Normalbetrieb nach 5 min guter Werte), `control_mode`/`degraded_for_seconds`/`calibrator_health`, Sommermodus (gedämpfte Außentemperatur, 1 K Hysterese), offline-Fensterkontakt = geschlossen.

**Schmerzpunkte (Issues/Threads)**
- #2404 „Unconfirmed TRV setpoint is silently 'assumed applied' and never retried“ – Ursache im Einzelfall `max_heat_setpoint_limit` 30,5 °C vs. geschriebene 35 °C; Maintainer bestätigt Wunsch nach Repair-Issue bei unbestätigten Writes. https://github.com/KartoffelToby/better_thermostat/issues/2404
- #1489 (62 Kommentare) „BT / Sonoff TRVZB – Offset wrong and/or UI indication wrong“: Offset wird zu selten korrigiert („last offset change was over an hour ago“), Anzeige „heizt“ bei geschlossenem Ventil. https://github.com/KartoffelToby/better_thermostat/issues/1489
- #1358 (54) „Support valve position calibration for Sonoff TRVZB“ (später umgesetzt). https://github.com/KartoffelToby/better_thermostat/issues/1358
- #1621 (39) „[1.7.0] thermostats don't start heating“ – TRVZB-Ventile öffnen 30+ min verzögert. https://github.com/KartoffelToby/better_thermostat/issues/1621
- #2291 (PR) „keep the control cycle running when a TRV write fails“ – **abgelehnte Writes der externen Temperatur (schlafendes/unerreichbares TRVZB) stoppten den ganzen Regelzyklus**. https://github.com/KartoffelToby/better_thermostat/pull/2291
- #2312 (PR) deterministische TRVZB-Geschwister-Suche – vorher konnte die Raumtemperatur in die **falsche Entität** geschrieben werden, wenn eine alte Entität mit ähnlicher ID existierte. https://github.com/KartoffelToby/better_thermostat/pull/2312
- #1549 (40) Thermostate `unavailable` nach Z2M 2.0. #1652 (31) invertierter Offset bei Tuya. #2741/#2742 Tuya-Kalibrierentität nicht erkannt / Sollwert springt auf 15 °C nach Offset-Write. #2744/#2395 virtuelle Zieltemperatur springt bzw. oszilliert bei zwei TRVs (FRITZ!DECT). #2415 HomematicIP-Duty-Cycle → Writes auf 600 s/Kanal gedrosselt. #2743 Wunsch nach Preset-abhängiger Ventilbegrenzung (Fernwärme). #2740 Matter-Binding (TRV mit eigener Sensorbindung ohne BT-Feedback-Loop).
- Community (simon42): `local_temperature_calibration` „von −8,4 bis 12,7 ohne erkennbare Logik“, BT schaltet TRVs ab, Tuya-ZHA-Ventile hängen nach BT-Einsatz (Neu-Pairing nötig). https://community.simon42.com/t/better-thermostat-mit-sonoff-trvzb-und-zigbee2mqtt/65646 , https://community.simon42.com/t/better-thermostat-schaltet-einfach-ab/12712
- Discussion #728: Fenstersensor `unavailable` → BT selbst `unavailable`; unknown galt als „offen“. https://github.com/KartoffelToby/better_thermostat/discussions/728
- Fazit: sehr mächtig, aber ein Regler-Zoo (6 Modi) plus Gerätequirks per Heuristik; genau das Modell, das Oliver kritisiert (eigene climate-Entität, Regelung in HA). **Wichtig:** BT macht seit 1.9.2 für den TRVZB bereits „externe Temperatur nativ schreiben + Keep-alive“ – Olivers Alleinstellung ist also nicht die Methode, sondern der Verzicht auf Regelung/climate und die Robustheitsschicht.

### 2.2 Versatile Thermostat (jmcollin78/versatile_thermostat)

**Architektur**
- Virtuelle `climate` (VTherm) pro Raum, Typen `over_switch` (TPI → Schalter), `over_climate` (Setpoint an Unter-Thermostat), `over_valve` (0–100-Ventil) + „zentrale Konfiguration“ (gemeinsame Presets/Fenster/Presence, `select.central_mode` Auto/Stopped/Heat only/Cool only/Frost protection, zentraler Kessel).
- TPI: `on_percent = coef_int·(Soll−Raum) + coef_ext·(Soll−Außen)` (Defaults 0,6 / 0,01), Zyklus `cycle_min`, Mindest-Ein/Aus-Zeiten, Schwellen seit 7.4, Auto-TPI-Lernen (#1396).
- over_climate-Autoregulation: Setpoint-Offset aus kp/ki/k_ext (light 1,5 K … strong/expert), nur gesendet, wenn Offset-Änderung ≥ `regulation_threshold` und `period_min` verstrichen; alternativ **Direct-Valve**: TPI-Prozent → `valve_opening_degree`, closing = `100 − x`, Parameter `opening_threshold`, `max_closing_degree`, `minimum_opening_degrees`, `max_opening_degrees`. **TRVZB-Hinweis:** Threshold ≥ 3 %, sonst verliert das TRV seine Kalibrierung; `hvac_action` des TRVZB gilt als unzuverlässig (VT korrigiert es selbst).
- Fenster: Sensor-Modus (Delay; Aktion off/fan_only/frost/eco) oder Auto-Modus (Slope °C/h, Beendigungsschwelle, max. Dauer; geglätteter `slope`-Sensor; ungeeignet für Flure). Presence: Sensor on/home, Away-Temperatur je Preset, Personen-Gruppen nur via Template. Safety-Mode: Sensor stumm > `safety_delay_min` → Preset `safety`, `safety_default_on_percent`.
- Entitäten: climate, Energie, „last seen“ Innen/Außen, Security-State, Auto-Start/Stop-Select, Follow-Underlying-Switch, TPI-Leistungssensoren, Presence/Window/Motion-Binary-Sensoren, Ventil-%, Preset-Temperaturen → **6–16+ pro VTherm**.

**Schmerzpunkte**
- #645 (219 Kommentare) „Native Sonoff TRVZB support“: Offset-Kalibrierung auf Minimum setzen, damit Ventil öffnet → Display zeigt Unsinn; **Step-Dropping** (wöchentliche Neukalibrierung eines Ventils), interne Hysterese ~1 K, `hvac_action` „Idle“ trotz Heizen, closing-degree-Bug. https://github.com/jmcollin78/versatile_thermostat/issues/645
- #1220 (137) Mindestöffnung verhindert Schließen. #927 (27) Closing-Degree 99 statt 100 → Wasser fließt weiter. #1831 (55) nach HA-Neustart heizt VTherm trotz Überschreitung. **#2049** opening/closing `unknown` nach Neustart → „Initialisation error“, weil TRVZB seine Ventilposition **nicht zurückmeldet** (reine Sollwerte, Z2M-Cache leer) – Fix 10.5.1. https://github.com/jmcollin78/versatile_thermostat/issues/2049
- #1679 (54) Ventile öffnen nur 20–30 % („Not VTherm issue“), #1685 (42)/#1708 Auto-TPI lernt falsche Heizrate / fällt nach Neustart ab, #1554 (32) externe Ventilbegrenzung → ständiges Auf/Zu, #683 (40) falsche Temperatur nach Fenster-Schließen, #595 Wunsch `local_temperature_calibration` statt Setpoint-Shift (Display zeigt sonst falschen Sollwert).
- Fazit: beeindruckend komplett (Presence, Fenster-Auto, Zentralmodus, Lastabwurf), aber Konfigurationstiefe und Entitätenflut sind hoch; die TRVZB-Integration lebt von Ventil-Prozent-Writes, die den Motor belasten (Step-Dropping) und ohne Rückmeldung blind bleiben.

### 2.3 Advanced Heating Control (panhans/HomeAssistant, Blueprint `advanced_heating_control.yaml`)

Richtiger Name: „🔥 Advanced Heating Control V5“ (Oliver nannte es „Adaptive Heating Control“). Version 5.5.7, 3 953 Zeilen YAML, `mode: queued`, benötigt Integration `uptime`, min. HA 2024.10. Forum-Thread seit 10/2022 mit > 2 300 Beiträgen; Doku-Site „under construction“. Letzter Commit 2026-01-20.

**Umsetzung**
- Eine Automation pro Raum. Inputs: mehrere `climate`, optionaler externer Raumsensor, Komfort/Eco (Fallback 22/19 °C oder input_number), HVAC-Modi, mehrere Schedules + Selector, Personen/Device-Tracker (Ankunft/Abwesenheit 2 s), Proximity (Zone, 500 m), Presence-Sensor mit eigenem Schedule (5 min), Adjustments-Liste (Zeit/Tage/Scheduler/Comfort/Eco/Kalibrierung/Modus), Force-Max/Force-Eco, Party (Zahl im Namen = Temperatur), Gast als simulierte Person, Away-Offset 0–10, Fenster/Türen (30 s, Fenster-Temp 0 = `off`), Frostschutz, Winter-/Aktivierungs-Entität, Liming-Protection (wöchentlicher Max-Temp-Lauf), Custom Action/Condition, Legacy-Restore.
- Zieltemperatur-Priorität: AHC inaktiv → Fenster → Party → Frost → Comfort/Eco (−Away-Offset).
- Kalibrierung: *Offset* (`Offset_alt + (Sensor − TRV_temp)`, Clamp ±10,9, 1 Dezimale, nur ab `calibration_delta`), *generisch* (Setpoint-Shift), ***external sensor*** (Sensorwert direkt in `number`, select/switch auf „external“, Rundung auf `step`, Min/Max, **Keep-alive nach 20 min**), Tado-Service. Aggressive Mode (zusätzlicher Offset bei großer Abweichung). Dynamic Valve Positioning (linear/√/quadratisch, Schrittweite, Max, Timeout).
- Aktionen nur bei Änderung; `climate.set_hvac_mode`/`set_temperature`/`number.set_value`. **Kein Rücklesen, keine Retries** (nur `action_call_delay`).

**Grenzen**
- Blueprints können keine mehreren optionalen Trigger → nur eine Fenster-Entität (Gruppe nötig). Manuelle Änderungen am TRV sind von eigenen nicht sicher unterscheidbar (HA-Limitierung). UI-/Physical-Sync „experimental“. Kombination mit BT nur ohne External-Sensor/Kalibrierung/Aggressive. Riesige YAML, schwer debugbar; keine Fehlersignalisierung bei Schreibfehlern; Presence/Schedule-Interaktion sorgt im Thread regelmäßig für Verwirrung („heizt trotz Abwesenheit zur Schedule-Zeit“).
- Quellen: https://community.home-assistant.io/t/advanced-heating-control/469873 , https://github.com/panhans/HomeAssistant/blob/main/blueprints/automation/panhans/advanced_heating_control.yaml

### 2.4 Weitere Lösungen (kurz)

- **Schedy (hass-apps, AppDaemon)** – Rooms/Actors/Schedules mit Python-Expressions; **sendet Werte erneut, bis der Actor die Änderung zurückmeldet** (genau Olivers Verifikationsidee, 2018 schon da). Maintenance-Mode seit 2022-02-23, keine neuen Features. https://github.com/efficiosoft/hass-apps , https://hass-apps.readthedocs.io/en/stable/apps/schedy/
- **Awesome Thermostat (dadge)** – Vorläufer von VT (Presets, Fenster, Motion); letzter Commit 2022-01-04 → tot. https://github.com/dadge/awesome_thermostat
- **Smart Thermostat PID (ScratMan/HASmartThermostat)** – eigene climate, PID → PWM-Switch oder 0–100-Ventil/number, Außentemperatur-Kompensation `ke`, Autotune „not recommended“, `sensor_stall` 6 h → `output_safety` 5 %. Release 2026.10.1 (2026-10-12). Regelt selbst; für Olivers Ansatz nur als Referenz für Sensor-Stall-Handling. https://github.com/ScratMan/HASmartThermostat
- **Generic Thermostat (Core)** – 1 Switch + Sensor, `cold_tolerance`/`hot_tolerance` 0,3, `min_cycle_duration`, `keep_alive`, Presets. Kein TRV-Setpoint, kein Fenster, kein Schedule. https://www.home-assistant.io/integrations/generic_thermostat/
- **Scheduler Component (nielsfaber)** – `switch.schedule_<token>` pro Zeitplan, Timeslots, Conditions, `track_conditions` (führt Aktionen erneut aus, wenn Bedingungen wieder erfüllt), beliebige Services (`climate.set_temperature`), Scheduler-Card. Kein Retry bei fehlgeschlagener Aktion. v3.3.8 (2024-11-16). https://github.com/nielsfaber/scheduler-component
- **TRVZB-External-Sensor-Blueprints**
  - Community „Sonoff TRVZB External Temperature Sensor Calibration“ v1.0.1 (Z2M ≥ 2.1.2, FW ≥ 1.2.1): Trigger = State-Change des Sensors, Conditions `not unavailable/unknown`, Aktion `number.set_value` auf `number.<trv>_external_temperature_input` (mehrere TRVs). **Kein Keep-alive** – verlangt Sensor-Reporting `max_rep_interval < 7200 s` + `force_update: true`, weil das TRV nach **2 h ohne Wert auf den internen Sensor zurückfällt**. Thread-Erkenntnisse: Sensoren mit 1 Dezimale (SNZB-02P) erzeugen „gleiche Werte“ → kein Trigger; Fehler „Expected one of: 0, 1, got '3'“ beim Sensor-Select (Bitfeld). https://community.home-assistant.io/t/sonoff-trvzb-external-temperature-sensor-calibration/856444 , YAML-Fork: https://github.com/kvanbiesen/HASonoffAutomation/blob/e0d47bb4b70422521d8a23b0b995846c59bb4958/blueprints/sonoff-trvzb-external-temperature-sensor-calibration.yaml
  - Sonoff-Guide (ZHA + Z2M, TRV-ZBT/TRVZB): ZHA-Pfad = Switch „External temperature sensor“ am Gerät einschalten, Automation `number.set_value` auf „External temperature sensor value“, **nur** auf die Temperatur-Entität triggern (nicht Batterie/LQI). https://sonoff.tech/blogs/news/sonoff-trv-with-an-external-temperature-sensor-home-assistant (Blueprint-Seite war während der Recherche dauerhaft 429)
  - **Kein gefundenes Beispiel nutzt `zha.set_zigbee_cluster_attribute`** für den TRVZB – seit dem ZHA-Quirk (Nov 2024) gibt es die Entitäten direkt. Der Raw-Weg wäre: `cluster_id: 64529` (0xFC11), `endpoint_id: 1`, `attribute: 24589` (0x600D, int16 ×100) bzw. `24590` (0x600E, uint8 1 = extern), `manufacturer: -1` (Quirk setzt keinen Manufacturer-Code).
- **zha-toolkit (mdeweerd)** – Referenz für robustes Schreiben: `attr_write` mit `read_before_write`/`read_after_write`, `write_if_equal`, `tries` (bei schlafenden Geräten z. T. > 100), `fail_exception`, Events `event_success/fail/done`; Gateway-Zugriff über `homeassistant.components.zha.helpers.get_zha_gateway(hass)` / `get_zha_gateway_proxy(hass)` (seit HA 2024.8), zigpy `cluster.read_attributes_raw(...)` / `cluster.write_attributes_raw(..., manufacturer_code=...)`, Retry auf `DeliveryError`/`TimeoutError`. v1.2.2 (2026-08-24). https://github.com/mdeweerd/zha-toolkit

---

## 3. Faktenblatt Sonoff TRVZB (Zigbee-Ebene, Stand ZHA-Quirk dev + Z2M master)

Custom-Cluster **0xFC11** (kein Manufacturer-Code; ZHA-Quirk `zhaquirks/sonoff/trvzb.py`, seit #3358 2024-11-25, FW-1.3-Attribute #3909 2025-04-30, Adaptive Mode #4740 2026-02-25; seit HA 2026.2 im Core, eigener Sonoff-Quirk kollidiert):

| Attribut | ID | Typ | ZHA-Entität (Name) | Semantik |
|---|---|---|---|---|
| child_lock | 0x0000 | Bool | switch „Child lock“ | Knopf sperren |
| **open_window** | 0x6000 | Bool | switch „Open window“ | **Ein/Aus der geräteeigenen Erkennung** (Abfall > 1,5 K in 4,5 min → Ventil zu, Display „OP“, Ziel 12 °C; Ende bei manueller Änderung, Anstieg > 1,5 K/4,5 min oder nach 30 min). **Kein externer Fenster-Eingang.** |
| frost_protection_temperature | 0x6002 | int16s | number | 4–35 °C, greift bei `system_mode: off` |
| idle_steps / closing_steps / Spannungen | 0x6003–0x6007 | ro | – | Motor-Kalibrierdaten |
| valve_opening_degree | 0x600B | uint8 | number (initially disabled) | Max-Öffnung beim Öffnen, FW ≥ 1.1.4 |
| valve_closing_degree | 0x600C | uint8 | number (initially disabled) | Position beim Schließen (100 = ganz zu) |
| **external_temperature_sensor_value** | 0x600D | int16s ×0,01 | number „External temperature sensor value“ (0–99,9, step 0,1) | Externer Messwert; **verfällt nach ~2 h** → intern |
| **external_temperature_sensor_enable** | 0x600E | uint8 | switch „External temperature sensor“ | Z2M-Select: 0 local / 1 remote / **2 remote_source_offline** (Gerät meldet selbst); bei Wiederverbindung meldet das Gerät **0x03** → Rücklesen muss 1/2/3 tolerieren |
| temperature_control_accuracy | 0x6011 | int16s | number „Temperature control accuracy“ | −1,0 … −0,2 K (Step 0,2), Default −1 = **1 K Hysterese**; FW ≥ 1.3.0 |
| temporary_mode / duration / timer target | 0x6014/15/16 | | button „Boost mode“/„Timer mode“, numbers | Boost ≤ 180 min, Timer ≤ 24 h; FW ≥ 1.4.0 |
| **smart_temperature_control** | 0x6017 | bitmap8 | switch „Adaptive mode“ (on = 2, off = 0) | PID-Ventilmodulation, FW ≥ **1.4.4**; deaktiviert opening degree + accuracy |

Standard-Thermostat-Cluster 0x0201: `occupied_heating_setpoint` 4–35 °C, `local_temperature` (zeigt bei externem Sensor den externen Wert), `local_temperature_calibration` −12,7…12,7 (Step 0,2), `system_mode` off/auto/heat (**auto = Wochenplan im Gerät**), `running_state` idle/heat. Wochenplan: Standard `SetWeeklySchedule` (Z2M `weekly_schedule_<tag>`, max. 6 Übergänge/Tag, 4–35 °C in 0,5-Schritten, erster um 00:00) – unter ZHA nur per `zha.issue_zigbee_cluster_command` (Cluster 0x0201, Command 0x01), kein Core-UI. Gen 2 (TRV-ZBT) ist anders: `remoteAttributeLinkage` 0x601E, `open_window_detected` (read-only), eigener `scheduleGroup`-Befehl (12 Übergänge).

Firmware/Verhalten:
- 1.2.1 externer Sensor (eWeLink-seitig nur SNZB-02/02D/02P), 1.3.0 accuracy, 1.4.0 Boost/Timer/Offset ±0,2, **1.4.4 Adaptive Mode** (gestaffelter Rollout; Update ~45–50 min; unter ZHA/Z2M-OTA unsicher: HA zeigt 1.3.0 während Z2M 1.4.4 meldet, OTA bricht mit „ABORT“ ab, 1.4.0-OTA wurde aus Koenkk-Index wieder entfernt).
- Adaptive-Mode-Berichte: Ventil schließt am Sollwert und öffnet nicht wieder, Überschwingen bis 3 K, „Heating“ bei 100 % geschlossen, Modus geht nach Batteriewechsel verloren (Bridge 3.3.0), „lernt nicht“ (Workaround: Auto-Modus toggeln). https://forum.ewelink.cc/t/trvzb-new-adaptive-mode-doesn-t-work-correctly/207511 , https://forum.ewelink.cc/t/trvzb-adaptive-mode-issue/208357 , https://forum.ewelink.cc/t/trvzb-firmware-changelog/74377?page=6
- Ohne Adaptive Mode: reine Zweipunktregelung mit Hysterese (accuracy), optional begrenzt durch opening degree; `running_state`/`hvac_action` unzuverlässig (VT, BT #1489) → nicht für Kesselsteuerung nutzen.
- Batteriegerät (sleepy end device): Writes werden abgelehnt/verzögert, wenn das Gerät schläft oder außer Reichweite ist (BT #2291); ZHA `ZIGBEE_DELIVERY_FAILED`, Source-Routing half (simon42); dünnes Mesh häufige Ursache. https://community.simon42.com/t/funktionsweise-sonoff-thermostat-trvzb/17874?page=3 , https://community.simon42.com/t/sonoff-trvzb-thermostate-problem-mit-der-erreichbarkeit/71136
- Ventil-Prozent-Writes führen zu Step-Dropping/Neukalibrierung (VT #645) – ein Argument **für** Olivers „nicht selbst regeln“.

---

## 4. Klau-Liste (übernehmen)

1. **Keep-alive für die externe Temperatur** – BT: alle 30 min, AHC: nach 20 min ohne Update. Grund: 2-h-Fail-safe des TRVZB. Ohne Keep-alive fällt das TRV bei stabiler Raumtemperatur (Sensor meldet nichts Neues, v. a. bei 1-Dezimal-Sensoren) stillschweigend auf den internen Sensor zurück. Intervall ≤ 60 min, besser 20–30 min.
2. **Sensorquelle aktiv umschalten und beim Rücklesen 1/2/3 akzeptieren** – BT `maybe_select_external_sensor` prüft „steht schon auf extern?“ vor dem Schreiben; Z2M-Konverter: Gerät meldet 2 (offline) bzw. 3 (reconnect). Verifikation darf nicht auf `== 1` bestehen.
3. **Nur schreiben, wenn sich der Wert wirklich ändert, mit Schwelle** – AHC `calibration_delta`, VT `regulation_threshold`/`period_min`, BT-HomematicIP-Drossel 600 s/Kanal. Reduziert Zigbee-Last und Batterieverbrauch. Für die externe Temperatur: Rundung auf 0,1 K (TRV-Auflösung) + Keep-alive statt Dauerfeuer.
4. **Rücklese-Verifikation wie Schedy/zha-toolkit** – Schedy sendet, bis der Actor den Wert zurückmeldet; zha-toolkit `read_after_write` + `tries` + `fail_exception` + Events. Fehlgeschlagene Writes als Repair-Issue/Attribut sichtbar machen (BT #2404 wünscht sich genau das).
5. **Exponentielles Backoff + Fehlerlog-Deduplizierung** – BT 1.9.3 (dauerhafte Gerätefehler max. 1×/h loggen), BT 2.0 Retries 30 s → 30 min. Ein unerreichbares TRV darf den Raum nicht blockieren (BT 1.9.3, #2291).
6. **Sensor-Stall-Handling mit Zeitfenster** – VT `safety_delay_min` + `safety`-Preset, Smart-PID `sensor_stall` 6 h, BT 2.0 Fallback-Leiter (erst andere Quellen, dann Halten, Rückkehr nach 5 min guter Werte). Für Oliver: Hygrometer-Stall → External-Sensor-Modus am TRV **sofort** abschalten (nicht erst nach 2 h) und Diagnose-Attribut `degraded_for_seconds` o. ä. setzen.
7. **Deterministische Entitätszuordnung über `translation_key`/Unique-ID, nicht über Namens-Heuristik** – BT #2312 hat Raumtemperatur in die falsche Entität geschrieben. Unter ZHA: Quirk-Entitäten per Device-ID + translation_key (`external_temperature_sensor_value`, `external_temperature_sensor`, `open_window`, `adaptive_mode`) auflösen; alternativ Raw-Attribut via zigpy.
8. **Geräteeigene Fenster-Erkennung bewusst behandeln** – BT schaltet `window_detection` beim Init aus (Konfliktvermeidung). Oliver sollte sie entweder explizit abschalten oder als Backup belassen – aber dokumentiert, nicht zufällig.
9. **Fensterkontakt-Delay in beide Richtungen** (BT, AHC 30 s, VT) und **Zustand merken/wiederherstellen** nach Fenster-zu (VT #683 zeigt, was passiert, wenn man es falsch macht: falsche Temperatur nach Schließen).
10. **Außentemperatur als gedämpfter Wert** – BT: 3-Tage-Mittel, täglicher Check 05:00; 2.0: 1 K Hysterese + Tages-Hoch/Tief. Für die Roadmap „Außentemperatur-Abschaltung“ direkt so übernehmen (kein Flattern an der Schwelle).
11. **Startup-Grace und „TRV kommt später“** – BT #2416: Raum startet auch, wenn ein TRV nach dem Karenzfenster fehlt; VT #2049: nicht auf Werte warten, die das Gerät nie meldet (Opening-Degree ist write-only).
12. **Z2M-Thermostate beim Start in `heat`/manual zwingen** (BT 1.9.2), weil `auto` den Gerätewochenplan aktiviert und HA-Sollwerte überschreibt – relevant, sobald Oliver native Schedules nutzt: dann gilt das Umgekehrte (nicht in `heat` zwingen).
13. **Decision-Log** (BT 2.0): jeder Write mit Grund protokolliert („Sensor X → 21,3 °C, Delta 0,2 ≥ Schwelle“). Macht die Integration debugbar, ohne Debug-Logging.
14. **`temperature_control_accuracy` als Konfigurationsoption** anbieten (−0,2 statt Default −1,0), weil die 1-K-Hysterese der häufigste Grund für „heizt zu spät“ ist (ewelink-Forum, HA-Thread 856444).

## 5. Vermeide-Liste (Fehler der anderen)

1. **„Assume applied“ nach Timeout** (BT #2404, 360 s): niemals stillschweigend annehmen, dass ein Write angekommen ist; Fehlerzustand explizit machen.
2. **Ventil-Prozent als Regelgröße aus HA** (BT Direct-Valve, VT Direct-Valve, AHC Dynamic Valve Positioning): write-only, Step-Dropping, wöchentliche Neukalibrierung, Deadlocks nach Neustart (#2049), 99-vs-100-Bugs (#927), Threshold-Fallen (≥ 3 %). Oliver: Finger weg, höchstens `valve_opening_degree` als statische Obergrenze (Hydraulik/Geräusch, BT #2743, VT #1554).
3. **Offset-Kalibrierung (`local_temperature_calibration`) als Pseudo-Regler**: Offsets „von −8,4 bis 12,7“ ohne erkennbare Logik, Display zeigt Unsinn (VT #595, #645), Geräte mit ±5-Grenzen laufen an den Anschlag (BT #1489, #1652). Mit nativem External-Sensor-Eingang ist das überflüssig.
4. **Regler-Zoo** (BT: 6 Kalibrierungsmodi, VT: TPI + 5 Autoregulation-Modi + Expert-YAML): Support-Hölle, jedes Issue beginnt mit „welcher Modus?“. Nicht regeln = nicht tunen.
5. **Heuristische Entitätssuche über Namensteile** (BT „calibration“/„lock“/„window“, AHC „offset“/„external“ in der Entity-ID): bricht bei Umbenennung, alten Entitäten, Sprachen (#2312, #2741, AHC-5.5-Breaking-Change).
6. **`unknown`/`unavailable` ohne Policy**: BT alt: Fenster unknown = offen → Integration selbst `unavailable` (#728); BT 2.0: offline = geschlossen. Beides ist eine Entscheidung – sie muss konfigurierbar und mit Zeitlimit sein (siehe Abschnitt 6).
7. **Blockierende Regelzyklen**: ein abgelehnter Write stoppte bei BT den ganzen Zyklus (#2291). Pro TRV isolieren.
8. **Blueprint-Monolith** (AHC 3 953 Zeilen): keine Tests, keine Fehlerpfade, nur eine Fenster-Entität, Presence/Schedule-Interaktionen undurchschaubar. Integration mit Tests ist der richtige Weg – aber nur, wenn der Scope klein bleibt.
9. **Auf `hvac_action`/`running_state` des TRVZB vertrauen** (Kesselsteuerung): laut VT, BT #1489 und ewelink-Forum unzuverlässig.
10. **Adaptive Mode als Voraussetzung**: nur FW ≥ 1.4.4, OTA unter ZHA fragil, Verhalten laut Nutzern teils schlechter als Hysterese. Nur optionaler Schalter.
11. **Entitätenflut** (BT 15–25, VT 10–22 pro Raum): Olivers Hauptkritik – bleibt nur gültig, wenn die eigene Integration wirklich bei einer Handvoll Diagnose-Entitäten bleibt (Status/Fehler/letzter Write pro Raum, keine Preset-numbers pro Raum × Preset).
12. **Stiller Gerätewochenplan** (`system_mode: auto`): wer Schedules im TRV nutzt und gleichzeitig Sollwerte aus HA schreibt, bekommt Überschreibungen zur nächsten Schaltzeit (BT 1.9.2 musste Z2M-Geräte deshalb in manual zwingen).

## 6. Lücke – was Olivers Ansatz füllt, und wo er nicht besser ist

**Was es so noch nicht gibt**
- Eine Integration, die **nur** die nativen Eingänge des TRVZB bedient (externe Temperatur + Sensorquelle, perspektivisch Wochenplan, Frost, Accuracy) und **keine** eigene Regelung/climate-Entität mitbringt. BT ist seit 1.9.2 technisch nah dran (schreibt External-Temp + Keep-alive), aber immer als Nebenprodukt eines Reglers mit climate-Entität.
- Ein **Raum-Modell mit N Sensoren und M TRVs** mit Aggregation (Median/Mittel, Staleness-Filter) – BT/VT/AHC kennen nur einen Raumsensor (BT glättet ihn per EMA).
- **Schreiben → Rücklesen → Retry → Fehler als Repair/Attribut** als Kernfunktion. Das hat nur Schedy (2018, AppDaemon, Maintenance-Mode) und zha-toolkit (Service, keine Raumlogik).
- Explizite **Degradations-Policies** („Sensor weg → External-Modus aus“, „Fenster weg + zuletzt offen → offen“), die der Nutzer sieht.

**Wo der Ansatz NICHT besser ist / Risiken**
1. **Fenster ist kein nativer Eingang.** Die Integration muss „Fenster offen“ selbst abbilden: `system_mode: off` (dann greift `frost_protection_temperature`), oder Sollwert temporär auf Frost/Eco, oder `valve_opening_degree` 0 (write-only, Ventil-Write – eigentlich vermeiden). Jede Variante ist ein Schreibvorgang auf eine Regelgröße, die auch der Nutzer bedient → Konflikterkennung („Nutzer hat gedreht, während Fenster offen“, vgl. BT PR #2738) und Zustandsrestore nach Schließen (VT #683) sind nötig. Das ist genau die Logik, die BT/VT/AHC haben und die Oliver „nicht bauen“ wollte.
2. **Ohne climate-Entität fehlt der Raum-Sollwert.** Bei M TRVs pro Raum stellt der Nutzer jedes TRV einzeln, es sei denn, die Integration synchronisiert Sollwerte zwischen den TRVs (Write-through). Presets, Nachtabsenkung und Abwesenheit (Roadmap) brauchen ebenfalls eine Raumziel-Größe. Lösung ohne climate: Setpoint-Sync + ein `number`/`select` pro Raum – das sind dann doch wieder „Entitäten“, nur weniger.
3. **Die Regelqualität ist die der TRVZB-Firmware.** Standard: Zweipunkt mit 1 K Hysterese (einstellbar bis 0,2 K), kein Außentemperatur-/Vorhersage-Einfluss; Adaptive Mode unausgereift. BT (MPC/TPI) und VT (TPI mit `coef_ext`) halten Räume nachweislich enger. Wer ±0,1 K will, kommt mit „nur füttern“ nicht hin – Olivers ML-Roadmap müsste dann doch wieder Regelgrößen schreiben (Sollwert-Offset), womit die reine „Feeder“-Philosophie bricht.
4. **Fail-safe-Tempo:** Das TRV fällt selbst nach 2 h auf intern zurück. Olivers aktives Abschalten ist schneller (gut), muss aber auch bei totem ZHA/HA sicher sein – das kann die Integration nicht, dort hilft nur das Keep-alive-Prinzip (das TRV „vergisst“ HA von selbst).
5. **„Fenster weg + zuletzt offen → offen“ kann einen Raum stundenlang kalt lassen** (Batterie leer bei offenem Fenster → bis jemand es merkt). BT 2.0 entscheidet bewusst gegenteilig (offline = zu). Besser: letzter Zustand halten **mit Max-Dauer** (VT-Analog: Auto-Fenster max. 30 min) und Benachrichtigung.
6. **ZHA-Abhängigkeit:** Raw-Zugriff auf zigpy-Cluster (wie zha-toolkit) ist an interne HA-APIs gekoppelt (Bruch 2024.8 „get_zha_gateway“). Die Quirk-Entitäten (`number`/`switch`) sind stabiler und funktionieren identisch unter Z2M (`external_temperature_input`, `temperature_sensor_select`). Empfehlung: Entitäts-Ebene als Primärpfad, Raw-Read nur optional für Verifikation. Sonst bleibt die Integration ZHA-only und bricht bei Core-Refactorings.
7. **Rücklesen bei schlafenden Geräten** ist nicht trivial: Lese-Requests scheitern, solange das Gerät schläft (zha-toolkit empfiehlt bei solchen Geräten z. T. > 100 Versuche); der TRVZB pollt zwar häufig, aber ZHA-Reporting für 0x600D/0x600E ist im Quirk **nicht** konfiguriert – die Entität zeigt nach dem Write ggf. nur den optimistischen Wert. „Nach X Sekunden zurücklesen“ muss ein echtes `read_attributes` sein, nicht ein State-Vergleich.
8. **Native Schedules (Roadmap):** TRVZB kann Standard-Wochenplan (6 Übergänge/Tag), aber unter ZHA nur per Raw-Command, kein Readback-Komfort, und `system_mode: auto` kollidiert mit allen Sollwert-Writes aus HA (Presets/Abwesenheit). Das wird ein eigenes Zustandsmodell („wer darf den Sollwert schreiben?“). Danfoss-Doku warnt zudem, dass Zeitpläne nach Power-Cycle/OTA verloren gehen können → Re-Sync nötig.
9. **Firmware-Fragmentierung:** 1.2.1 (External Sensor) bis 1.4.4 (Adaptive); OTA unter ZHA unsicher. Die Integration muss Feature-Gates pro Gerät haben (Attribut vorhanden? Entität vorhanden?) und darf nicht an fehlenden Entitäten scheitern.
10. **Marktlücke ist schmal:** BT 1.9.2+/2.0 + ein Scheduler + ein Presence-Blueprint decken 90 % ab, und BT hat ein Team. Der echte USP ist „weniger, aber verifiziert“ – das muss sich in Stabilität zeigen, nicht in Features.

---

## 7. Quellen

Better Thermostat
- README: https://github.com/KartoffelToby/better_thermostat (raw: https://raw.githubusercontent.com/KartoffelToby/better_thermostat/master/README.md)
- Releases: https://github.com/KartoffelToby/better_thermostat/releases (1.9.3: https://github.com/KartoffelToby/better_thermostat/releases/tag/1.9.3)
- Doku Konfiguration: https://better-thermostat.org/configuration
- model_fixes: https://github.com/KartoffelToby/better_thermostat/tree/master/custom_components/better_thermostat/model_fixes (TRVZB.py, default.py)
- adapters/generic.py, sensor.py, number.py, switch.py im Repo
- Issues/PRs: #2404, #1489, #1358, #1621, #2291, #2312, #1549, #1652, #2741, #2742, #2744, #2395, #2415, #2416, #2743, #2740, #2728, #2738; Discussion #728
- Community: https://community.simon42.com/t/better-thermostat-mit-sonoff-trvzb-und-zigbee2mqtt/65646 , https://community.simon42.com/t/better-thermostat-funktionier-nur-bedingt/68641 , https://community.simon42.com/t/better-thermostat-schaltet-einfach-ab/12712

Versatile Thermostat
- README: https://github.com/jmcollin78/versatile_thermostat ; Releases: https://github.com/jmcollin78/versatile_thermostat/releases (10.5.1)
- Doku: documentation/en/over-climate.md, self-regulation.md, algorithms.md, feature-window.md, feature-presence.md, feature-presets.md, feature-central-mode.md, reference.md, troubleshooting.md
- Issues: #645, #1220, #927, #1831, #2049, #1679, #1685, #1708, #1554, #683, #595, #1144, #1396, #345, #1204, #129, #348

Advanced Heating Control
- Thread: https://community.home-assistant.io/t/advanced-heating-control/469873
- YAML: https://github.com/panhans/HomeAssistant/blob/main/blueprints/automation/panhans/advanced_heating_control.yaml ; Commits: https://api.github.com/repos/panhans/HomeAssistant/commits?path=blueprints/automation/panhans/advanced_heating_control.yaml
- Doku (Platzhalter): https://panhans.github.io/HomeAssistant/

Weitere
- Schedy: https://github.com/efficiosoft/hass-apps , https://hass-apps.readthedocs.io/en/stable/apps/schedy/
- Awesome Thermostat: https://github.com/dadge/awesome_thermostat
- Smart Thermostat PID: https://github.com/ScratMan/HASmartThermostat
- Generic Thermostat: https://www.home-assistant.io/integrations/generic_thermostat/
- Scheduler Component: https://github.com/nielsfaber/scheduler-component
- zha-toolkit: https://github.com/mdeweerd/zha-toolkit (utils.py: Gateway-Zugriff, retry)
- ZHA-Action: https://www.home-assistant.io/actions/zha.set_zigbee_cluster_attribute/

Sonoff TRVZB
- ZHA-Quirk: https://github.com/zigpy/zha-device-handlers/blob/dev/zhaquirks/sonoff/trvzb.py (Commits #3358, #3909, #4740)
- ZHA strings (Entitätsnamen): https://github.com/home-assistant/core/blob/dev/homeassistant/components/zha/strings.json
- Z2M Device-Page: https://www.zigbee2mqtt.io/devices/TRVZB.html ; Konverter: https://github.com/Koenkk/zigbee-herdsman-converters/blob/master/src/devices/sonoff.ts
- Z2M Discussion External Sensor: https://github.com/Koenkk/zigbee2mqtt/discussions/26308
- Community-Blueprint: https://community.home-assistant.io/t/sonoff-trvzb-external-temperature-sensor-calibration/856444 ; https://github.com/kvanbiesen/HASonoffAutomation
- Sonoff Guide: https://sonoff.tech/blogs/news/sonoff-trv-with-an-external-temperature-sensor-home-assistant ; Doku: https://help.sonoff.tech/docs/trvzb ; OTA: https://help.sonoff.tech/docs/How-to-upgrade-Zigbee-firmware-in-ZHA-and-Z2M
- Firmware/Adaptive: https://forum.ewelink.cc/t/trvzb-firmware-changelog/74377 (S. 5/6), https://forum.ewelink.cc/t/trvzb-new-adaptive-mode-doesn-t-work-correctly/207511 , https://forum.ewelink.cc/t/trvzb-adaptive-mode-issue/208357 , https://sonoff.tech/blogs/news/trvzb-upgrade-smart-temperature-control-for-more-stable-room-temperature , https://community.home-assistant.io/t/new-sonoff-trv-firmware/949900
- ZHA-Praxis: https://community.simon42.com/t/funktionsweise-sonoff-thermostat-trvzb/17874?page=3 , https://community.simon42.com/t/sonoff-trvzb-thermostate-problem-mit-der-erreichbarkeit/71136 , https://community.simon42.com/t/sonoff-trvzb-update-auf-firmware-version-1-4-4-wird-nicht-angeboten/81383
- Weekly Schedule (Danfoss-Spec als Referenz für Cluster 0x0201 SetWeeklySchedule): https://assets.danfoss.com/documents/latest/284715/AM375549618098en-000103.pdf
