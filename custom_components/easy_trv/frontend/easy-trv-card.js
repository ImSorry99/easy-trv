/*
 * Easy TRV card: the house row and one block per room, found by itself.
 * One plain file, no build step, no imports. Served by the integration.
 * Text from states and attributes is only ever set as textContent.
 */
const TEXTS = {
  en: {
    title: "Easy TRV",
    presence: "Presence",
    auto: "Automatic",
    home: "Home",
    away: "Away",
    forced_since: "forced since",
    setback: "Away setback",
    own_setback: "Some rooms have their own setback.",
    heat: "Heat",
    frost: "Frost",
    off: "Off",
    window_open: "Window open",
    is_home: "Home",
    is_away: "Away",
    away_from: "away from",
    lowered: "Lowered target",
    stats: "Statistics",
    deviation: "Control deviation",
    heating_time: "Heating time",
    window_openings: "Window openings",
    window_open_time: "Window open time",
    write_failures: "Failed writes",
    dropouts: "Dropouts",
    nothing: "No Easy TRV rooms found.",
    minus: "Lower",
    plus: "Raise",
    ed_house: "Show the house row",
    ed_stats: "Show statistics",
    ed_rooms: "Rooms (entity ids of the room thermostats, one per line; empty = all)",
  },
  de: {
    title: "Easy TRV",
    presence: "Anwesenheit",
    auto: "Automatisch",
    home: "Zuhause",
    away: "Abwesend",
    forced_since: "erzwungen seit",
    setback: "Absenkung bei Abwesenheit",
    own_setback: "Einige Räume haben eine eigene Absenkung.",
    heat: "Heizen",
    frost: "Frost",
    off: "Aus",
    window_open: "Fenster offen",
    is_home: "Zuhause",
    is_away: "Abwesend",
    away_from: "abwesend ab",
    lowered: "Abgesenkte Zieltemperatur",
    stats: "Statistik",
    deviation: "Regelabweichung",
    heating_time: "Heizzeit",
    window_openings: "Fensteröffnungen",
    window_open_time: "Fenster offen (Dauer)",
    write_failures: "Fehlgeschlagene Schreibvorgänge",
    dropouts: "Ausfälle",
    nothing: "Keine Easy-TRV-Räume gefunden.",
    minus: "Senken",
    plus: "Erhöhen",
    ed_house: "Haus-Zeile anzeigen",
    ed_stats: "Statistik anzeigen",
    ed_rooms: "Räume (Entity-IDs der Raum-Thermostate, eine pro Zeile; leer = alle)",
  },
};

// The heating states of the room and the group each one tints the block with.
const GROUPS = {
  heating: "normal",
  frost: "normal",
  boost: "normal",
  setback_away: "lowered",
  off_manual: "lowered",
  off_window: "lowered",
  off_outdoor: "lowered",
  off_away: "lowered",
  window_pending: "lowered",
  restricted_unavailable: "restricted",
  restricted_contact: "restricted",
  restricted_unsupported: "restricted",
  restricted_schedule: "restricted",
  restricted_window_phase: "restricted",
  emergency: "fault",
  fault: "fault",
};
const GROUP_COLORS = {
  normal: "var(--success-color, #43a047)",
  lowered: "var(--info-color, #039be5)",
  restricted: "var(--warning-color, #ffa600)",
  fault: "var(--error-color, #db4437)",
  none: "var(--divider-color, #ccc)",
};
const STAT_KEYS = ["deviation", "heating_time", "window_openings", "window_open_time"];
const HOUSE_OPTIONS = ["auto", "home", "away"];
const MODES = [
  ["heat", "heat", "mdi:fire"],
  ["auto", "frost", "mdi:snowflake"],
  ["off", "off", "mdi:power"],
];

const STYLE = `
  :host { display: block; }
  ha-card { padding: 8px 12px 12px; }
  .row, .top, .status { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
  .row { justify-content: space-between; padding: 6px 0; }
  .label { font-weight: 500; }
  .grow { flex: 1 1 auto; min-width: 0; }
  .house { border-bottom: 1px solid var(--divider-color); margin-bottom: 8px; padding-bottom: 6px; }
  .room { border-left: 4px solid var(--divider-color); border-radius: 4px; margin: 8px 0;
    padding: 6px 8px 6px 10px; background: var(--secondary-background-color, transparent); }
  .name { font-size: 1.1em; font-weight: 500; cursor: pointer; background: none; border: 0;
    color: var(--primary-text-color); padding: 0; text-align: left; font-family: inherit; }
  .temp { font-size: 1.3em; color: var(--primary-text-color); }
  .btns { display: inline-flex; gap: 4px; flex-wrap: wrap; }
  button.b { display: inline-flex; align-items: center; justify-content: center; gap: 4px;
    min-width: 40px; min-height: 36px; padding: 0 10px; border-radius: 18px; cursor: pointer;
    border: 1px solid var(--divider-color); background: var(--card-background-color);
    color: var(--primary-text-color); font: inherit; }
  button.b.on { background: var(--primary-color); color: var(--text-primary-color, #fff);
    border-color: var(--primary-color); }
  button.b ha-icon { --mdc-icon-size: 18px; }
  .target { display: inline-flex; align-items: center; gap: 4px; }
  .status { color: var(--secondary-text-color); font-size: 0.9em; margin-top: 4px; }
  .status span { display: inline-flex; align-items: center; gap: 3px; }
  .status ha-icon { --mdc-icon-size: 18px; }
  .why { color: var(--secondary-text-color); font-size: 0.9em; margin-top: 2px; }
  .hint { color: var(--secondary-text-color); font-size: 0.85em; }
  .stats { margin-top: 4px; }
  .stats > button { background: none; border: 0; color: var(--secondary-text-color);
    cursor: pointer; padding: 4px 0; font: inherit; }
  .stat { display: flex; justify-content: space-between; padding: 2px 0; cursor: pointer; }
  .empty { padding: 12px 0; color: var(--secondary-text-color); }
`;

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return el;
}

class EasyTrvCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._open = new Set();
    this._signature = null;
    this._config = {};
  }

  static getConfigElement() {
    return document.createElement("easy-trv-card-editor");
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = { ...(config || {}) };
    this._signature = null;
    if (this._hass) this._render();
  }

  getCardSize() {
    return 3 + 2 * (this._lastRooms || 1);
  }

  set hass(hass) {
    this._hass = hass;
    const model = this._discover();
    const signature = JSON.stringify([
      hass.locale && hass.locale.language,
      [...this._open],
      model.used.map((id) => {
        const s = hass.states[id];
        return s ? [id, s.state, s.last_updated, s.attributes && s.attributes.hint] : [id];
      }),
    ]);
    if (signature === this._signature) return;
    this._signature = signature;
    this._model = model;
    this._render();
  }

  _t(key) {
    const lang = ((this._hass && this._hass.locale && this._hass.locale.language) || "en")
      .toLowerCase();
    return (lang.startsWith("de") ? TEXTS.de : TEXTS.en)[key] || TEXTS.en[key] || key;
  }

  // Find the house and the rooms by platform, device and translation key.
  _discover() {
    const hass = this._hass;
    const byDevice = {};
    for (const entry of Object.values(hass.entities || {})) {
      if (entry.platform !== "easy_trv" || !entry.device_id) continue;
      const domain = entry.entity_id.split(".")[0];
      const role = `${domain}.${entry.translation_key}`;
      (byDevice[entry.device_id] = byDevice[entry.device_id] || {})[role] =
        (byDevice[entry.device_id][role] || []).concat(entry.entity_id);
    }
    let house = null;
    let rooms = [];
    for (const roles of Object.values(byDevice)) {
      if (roles["climate.room"]) rooms.push(roles);
      else if (roles["select.presence"] || roles["number.away_setback"]) house = roles;
    }
    if (Array.isArray(this._config.rooms) && this._config.rooms.length) {
      rooms = this._config.rooms
        .map((id) => rooms.find((r) => r["climate.room"][0] === id))
        .filter(Boolean);
    }
    const used = [];
    const first = (roles, role) => (roles && roles[role] ? roles[role][0] : null);
    const alive = (id) => (id && hass.states[id] ? id : null);
    const houseModel =
      this._config.house === false || !house
        ? null
        : {
            select: alive(first(house, "select.presence")),
            number: alive(first(house, "number.away_setback")),
          };
    if (houseModel) used.push(houseModel.select, houseModel.number);
    const roomModels = rooms.map((roles) => {
      const model = {
        climate: first(roles, "climate.room"),
        state: alive(first(roles, "sensor.room_state")),
        window: alive(first(roles, "binary_sensor.window")),
        presence: alive(first(roles, "binary_sensor.presence")),
        stats: {},
        failures: [],
        dropouts: [],
      };
      for (const key of STAT_KEYS) model.stats[key] = alive(first(roles, `sensor.${key}`));
      model.failures = (roles["sensor.write_failures"] || []).filter(alive);
      model.dropouts = (roles["sensor.dropouts"] || []).filter(alive);
      used.push(model.climate, model.state, model.window, model.presence);
      used.push(...Object.values(model.stats), ...model.failures, ...model.dropouts);
      return model;
    });
    this._lastRooms = roomModels.length;
    return { house: houseModel, rooms: roomModels, used: used.filter(Boolean) };
  }

  _more(entityId) {
    this.dispatchEvent(
      new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true })
    );
  }

  _call(domain, service, data) {
    this._hass.callService(domain, service, data);
  }

  _time(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const lang = (this._hass.locale && this._hass.locale.language) || undefined;
    return date.toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });
  }

  _stateText(stateObj) {
    const hass = this._hass;
    return hass.formatEntityState ? hass.formatEntityState(stateObj) : stateObj.state;
  }

  _button(label, icon, on, onclick, title) {
    return h(
      "button",
      { class: `b${on ? " on" : ""}`, type: "button", onclick, title: title || label },
      icon && h("ha-icon", { icon }),
      label
    );
  }

  _render() {
    if (!this._model) this._model = this._discover();
    const { house, rooms } = this._model;
    const card = h("ha-card");
    if (house && (house.select || house.number)) card.append(this._houseRow(house));
    for (const room of rooms) card.append(this._roomBlock(room));
    if (!rooms.length && !(house && (house.select || house.number))) {
      card.append(h("div", { class: "empty" }, this._t("nothing")));
    }
    this.shadowRoot.replaceChildren(h("style", {}, STYLE), card);
  }

  _houseRow(house) {
    const box = h("div", { class: "house" });
    const select = house.select && this._hass.states[house.select];
    if (select) {
      const forced = select.state !== "auto" && select.attributes.forced_at;
      box.append(
        h(
          "div",
          { class: "row" },
          h("span", { class: "label" }, this._t("presence")),
          h(
            "span",
            { class: "btns" },
            HOUSE_OPTIONS.map((option) =>
              this._button(this._t(option), null, select.state === option, () =>
                this._call("select", "select_option", { entity_id: house.select, option })
              )
            )
          )
        )
      );
      if (forced) {
        box.append(
          h("div", { class: "hint" }, `${this._t("forced_since")} ${this._time(forced)}`)
        );
      }
    }
    const number = house.number && this._hass.states[house.number];
    if (number) {
      const value = Number(number.state);
      const step = Number(number.attributes.step) || 0.5;
      const set = (next) => {
        const min = Number(number.attributes.min);
        const max = Number(number.attributes.max);
        if (Number.isNaN(next) || next < min || next > max) return;
        this._call("number", "set_value", { entity_id: house.number, value: next });
      };
      box.append(
        h(
          "div",
          { class: "row" },
          h("span", { class: "label" }, this._t("setback")),
          h(
            "span",
            { class: "target" },
            this._button("−", null, false, () => set(value - step), this._t("minus")),
            h("span", { class: "temp" }, `${number.state} K`),
            this._button("+", null, false, () => set(value + step), this._t("plus"))
          )
        )
      );
      const own = number.attributes.rooms_with_own_setback;
      if (Array.isArray(own) ? own.length : own) {
        box.append(h("div", { class: "hint" }, this._t("own_setback")));
      }
    }
    return box;
  }

  _roomBlock(room) {
    const hass = this._hass;
    const climate = hass.states[room.climate];
    const state = room.state && hass.states[room.state];
    const block = h("div", { class: "room" });
    if (!climate) return block;
    const attrs = climate.attributes;
    const group = (state && GROUPS[state.state]) || "none";
    block.style.borderLeftColor = GROUP_COLORS[group];

    // Top line.
    const step = Number(attrs.target_temp_step) || 0.5;
    const target = attrs.temperature;
    const setTarget = (next) => {
      const min = Number(attrs.min_temp);
      const max = Number(attrs.max_temp);
      if (Number.isNaN(next) || next < min || next > max) return;
      this._call("climate", "set_temperature", { entity_id: room.climate, temperature: next });
    };
    const top = h(
      "div",
      { class: "top" },
      h(
        "button",
        { class: "name grow", type: "button", onclick: () => this._more(room.climate) },
        attrs.friendly_name || room.climate
      ),
      h(
        "span",
        { class: "temp" },
        attrs.current_temperature !== undefined && attrs.current_temperature !== null
          ? `${attrs.current_temperature}°`
          : "–"
      )
    );
    if (typeof target === "number") {
      top.append(
        h(
          "span",
          { class: "target" },
          this._button("−", null, false, () => setTarget(target - step), this._t("minus")),
          h("span", { class: "temp" }, `${target}°`),
          this._button("+", null, false, () => setTarget(target + step), this._t("plus"))
        )
      );
    }
    const modes = attrs.hvac_modes || [];
    top.append(
      h(
        "span",
        { class: "btns" },
        MODES.filter(([mode]) => modes.includes(mode)).map(([mode, key, icon]) =>
          this._button(this._t(key), icon, climate.state === mode, () =>
            this._call("climate", "set_hvac_mode", { entity_id: room.climate, hvac_mode: mode })
          )
        )
      )
    );
    block.append(top);

    // Status line.
    const status = h("div", { class: "status" });
    const icon = (name, text) =>
      status.append(h("span", { title: text }, h("ha-icon", { icon: name }), text));
    const window_ = room.window && hass.states[room.window];
    if (window_ && window_.state === "on") icon("mdi:window-open-variant", this._t("window_open"));
    const presence = room.presence && hass.states[room.presence];
    const presenceHome = presence ? presence.state === "on" : attrs.presence === "home";
    if (presence || attrs.presence === "home" || attrs.presence === "away") {
      let text = this._t(presenceHome ? "is_home" : "is_away");
      if (presence && presence.attributes.away_from) {
        text = `${this._t("away_from")} ${this._time(presence.attributes.away_from)}`;
      }
      icon(presenceHome ? "mdi:home" : "mdi:home-export-outline", text);
    }
    if (attrs.away_target !== undefined && attrs.away_target !== null) {
      icon("mdi:arrow-down-bold", `${this._t("lowered")} ${attrs.away_target}°`);
    }
    if (state) status.append(h("span", {}, this._stateText(state)));
    block.append(status);

    // Why line.
    if (state && state.state !== "heating" && state.attributes.hint) {
      block.append(h("div", { class: "why" }, state.attributes.hint));
    }

    const stats = this._stats(room);
    if (stats) block.append(stats);
    return block;
  }

  _stats(room) {
    if (this._config.statistics === false) return null;
    const hass = this._hass;
    const rows = [];
    const add = (key, id) => {
      const s = id && hass.states[id];
      if (!s || s.state === "unknown" || s.state === "unavailable") return;
      rows.push([this._t(key), this._stateText(s), id]);
    };
    for (const key of STAT_KEYS) add(key, room.stats[key]);
    for (const [key, ids] of [["write_failures", room.failures], ["dropouts", room.dropouts]]) {
      const total = ids.reduce((sum, id) => sum + (Number(hass.states[id].state) || 0), 0);
      if (total > 0) rows.push([this._t(key), String(total), ids[0]]);
    }
    if (!rows.length && !STAT_KEYS.some((key) => room.stats[key])) return null;
    const open = this._open.has(room.climate);
    const box = h(
      "div",
      { class: "stats" },
      h(
        "button",
        {
          type: "button",
          onclick: () => {
            if (open) this._open.delete(room.climate);
            else this._open.add(room.climate);
            this._signature = null;
            this.hass = this._hass;
          },
        },
        `${open ? "▾" : "▸"} ${this._t("stats")}`
      )
    );
    if (open) {
      for (const [label, value, id] of rows) {
        box.append(
          h("div", { class: "stat", onclick: () => this._more(id) }, h("span", {}, label), h("span", {}, value))
        );
      }
    }
    return box;
  }
}

class EasyTrvCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...(config || {}) };
    this._draw();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._drawn) this._draw();
  }

  _t(key) {
    const lang = ((this._hass && this._hass.locale && this._hass.locale.language) || "en")
      .toLowerCase();
    return (lang.startsWith("de") ? TEXTS.de : TEXTS.en)[key] || TEXTS.en[key] || key;
  }

  _change(patch) {
    this._config = { ...this._config, ...patch };
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      })
    );
  }

  _draw() {
    this._drawn = true;
    const cfg = this._config || {};
    const check = (key, label) => {
      const input = h("input", { type: "checkbox", checked: cfg[key] !== false });
      input.checked = cfg[key] !== false;
      input.addEventListener("change", () => this._change({ [key]: input.checked }));
      return h("label", { style: "display:flex;gap:8px;align-items:center;padding:6px 0" }, input, label);
    };
    const rooms = h("textarea", { rows: 3, style: "width:100%;box-sizing:border-box" });
    rooms.value = (cfg.rooms || []).join("\n");
    rooms.addEventListener("change", () => {
      const list = rooms.value.split("\n").map((l) => l.trim()).filter(Boolean);
      const next = { ...this._config };
      if (list.length) next.rooms = list;
      else delete next.rooms;
      this._config = next;
      this.dispatchEvent(
        new CustomEvent("config-changed", { detail: { config: next }, bubbles: true, composed: true })
      );
    });
    this.replaceChildren(
      check("house", this._t("ed_house")),
      check("statistics", this._t("ed_stats")),
      h("div", {}, this._t("ed_rooms")),
      rooms
    );
  }
}

customElements.define("easy-trv-card", EasyTrvCard);
customElements.define("easy-trv-card-editor", EasyTrvCardEditor);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "easy-trv-card",
  name: "Easy TRV",
  description: "House presence, setback and every Easy TRV room.",
  preview: false,
});
