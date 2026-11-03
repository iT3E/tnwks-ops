/*
 * tnwks alert schedule card: an on/off switch, day-of-week buttons and a
 * from/to time picker for one alert window, on the Security dashboard's
 * Alerts view (dashboards/security.yaml, packages/frigate_person_alerts.yaml).
 *
 *   type: custom:tnwks-alert-schedule-card
 *   title: Weekday alerts
 *   switch: input_boolean.weekday_person_alerts     # optional
 *   start: input_datetime.weekday_person_alerts_start
 *   end: input_datetime.weekday_person_alerts_end
 *   days: input_text.weekday_person_alerts_days     # optional; no row without it
 *   every_day: Every night                          # optional summary wording
 *
 * The days helper holds a comma list of three-letter days ("mon,tue,wed"),
 * Monday first. Every change goes straight to the helpers through their
 * services, and HA restores helpers across restarts, so nothing is saved here.
 * A time picker the user is editing isn't overwritten by a state update.
 *
 * Plain custom element, no build step. Served from /local/tnwks-security/
 * (a ConfigMap mount); bump ?v= on its resource in configuration.yaml with
 * every change, since HA caches /local for a long time.
 */

const VERSION = "1.0.0";
const DAYS = [
  ["mon", "M", "Mon"],
  ["tue", "T", "Tue"],
  ["wed", "W", "Wed"],
  ["thu", "T", "Thu"],
  ["fri", "F", "Fri"],
  ["sat", "S", "Sat"],
  ["sun", "S", "Sun"],
];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const hhmm = (state) => (/^\d{1,2}:\d{2}/.test(state || "") ? state.slice(0, 5).padStart(5, "0") : "");

function label12(t) {
  if (!t) return "--";
  const [h, m] = t.split(":").map(Number);
  const ap = h < 12 ? "AM" : "PM";
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${ap}`;
}

function daysSummary(sel, everyDay) {
  if (sel.length === 7) return everyDay;
  if (!sel.length) return "No days";
  const idx = sel.map((d) => DAYS.findIndex(([k]) => k === d)).sort((a, b) => a - b);
  // A consecutive run of three or more reads better as a range: Mon-Wed.
  const consecutive = idx.every((v, i) => i === 0 || v === idx[i - 1] + 1);
  if (consecutive && idx.length >= 3) return `${DAYS[idx[0]][2]}-${DAYS[idx[idx.length - 1]][2]}`;
  if (idx.join() === "0,1,2,3,4") return "Weekdays";
  if (idx.join() === "5,6") return "Weekends";
  return idx.map((i) => DAYS[i][2]).join(", ");
}

class AlertScheduleCard extends HTMLElement {
  setConfig(config) {
    if (!config.start || !config.end) throw new Error("start and end (input_datetime) are required");
    this.config = { every_day: "Every day", ...config };
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
  }

  getCardSize() {
    return this.config?.days ? 4 : 3;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  _st(id) {
    return id ? this._hass?.states[id] : undefined;
  }

  _selectedDays() {
    const raw = this._st(this.config.days)?.state || "";
    const set = new Set(raw.toLowerCase().split(/[\s,]+/));
    return DAYS.map(([k]) => k).filter((k) => set.has(k));
  }

  _build() {
    const c = this.config;
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        ha-card { padding: 14px 16px 16px; }
        .top { display: flex; align-items: center; gap: 12px; }
        .title { flex: 1; font-size: 16px; font-weight: 500; color: var(--primary-text-color); }
        .sum { font-size: 13px; color: var(--secondary-text-color); margin-top: 2px; }
        .sum.warn { color: var(--warning-color, #db8b00); }
        .sw { position: relative; width: 44px; height: 24px; border-radius: 12px; border: 0; padding: 0;
              background: var(--disabled-color, #bdbdbd); cursor: pointer; transition: background .15s; flex: none; }
        .sw::after { content: ""; position: absolute; top: 3px; left: 3px; width: 18px; height: 18px; border-radius: 50%;
              background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,.3); transition: transform .15s; }
        .sw[aria-checked="true"] { background: var(--primary-color); }
        .sw[aria-checked="true"]::after { transform: translateX(20px); }
        .body { transition: opacity .15s; }
        .off .body { opacity: .5; }
        .days { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; margin-top: 14px; }
        .day { height: 38px; border-radius: 19px; cursor: pointer; font: inherit; font-size: 14px; font-weight: 500;
               border: 1px solid var(--divider-color, #ccc); background: none; color: var(--primary-text-color); }
        .day[aria-pressed="true"] { background: var(--primary-color); border-color: var(--primary-color);
               color: var(--text-primary-color, #fff); }
        .times { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 14px; }
        label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--secondary-text-color); }
        input[type="time"] { font: inherit; font-size: 16px; padding: 8px 10px; border-radius: 8px;
               border: 1px solid var(--divider-color, #ccc); background: var(--card-background-color);
               color: var(--primary-text-color); color-scheme: light; min-width: 0; }
        /* The native picker's icon and popup follow HA's theme, not the OS. */
        :host([dark]) input[type="time"] { color-scheme: dark; }
        .day:focus-visible, .sw:focus-visible, input:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
      </style>
      <ha-card>
        <div class="top">
          <div class="title">${esc(c.title || "")}<div class="sum"></div></div>
          ${c.switch ? `<button class="sw" role="switch" aria-label="${esc(c.title || "Alerts")} on/off"></button>` : ""}
        </div>
        <div class="body">
          ${c.days ? `<div class="days">${DAYS.map(([k, s, l]) => `<button class="day" data-day="${k}" aria-label="${l}" title="${l}">${s}</button>`).join("")}</div>` : ""}
          <div class="times">
            <label>From<input type="time" step="300" data-which="start"></label>
            <label>To<input type="time" step="300" data-which="end"></label>
          </div>
        </div>
      </ha-card>`;
    root.querySelector(".sw")?.addEventListener("click", () => {
      const on = this._st(c.switch)?.state === "on";
      this._hass.callService("input_boolean", on ? "turn_off" : "turn_on", { entity_id: c.switch });
    });
    root.querySelectorAll(".day").forEach((b) =>
      b.addEventListener("click", () => {
        const sel = new Set(this._selectedDays());
        sel.has(b.dataset.day) ? sel.delete(b.dataset.day) : sel.add(b.dataset.day);
        const value = DAYS.map(([k]) => k).filter((k) => sel.has(k)).join(",");
        b.setAttribute("aria-pressed", String(sel.has(b.dataset.day))); // instant feedback
        this._hass.callService("input_text", "set_value", { entity_id: c.days, value });
      }),
    );
    root.querySelectorAll("input[type=time]").forEach((inp) =>
      inp.addEventListener("change", () => {
        if (!inp.value) return;
        const entity = inp.dataset.which === "start" ? c.start : c.end;
        this._hass.callService("input_datetime", "set_datetime", { entity_id: entity, time: `${inp.value}:00` });
      }),
    );
    this._built = true;
  }

  _update() {
    const c = this.config;
    const root = this.shadowRoot;
    const start = hhmm(this._st(c.start)?.state);
    const end = hhmm(this._st(c.end)?.state);
    const sel = this._selectedDays();
    const on = !c.switch || this._st(c.switch)?.state === "on";

    this.toggleAttribute("dark", !!this._hass.themes?.darkMode);
    root.querySelector("ha-card").classList.toggle("off", !on);
    root.querySelector(".sw")?.setAttribute("aria-checked", String(on));
    root.querySelectorAll(".day").forEach((b) => b.setAttribute("aria-pressed", String(sel.includes(b.dataset.day))));
    for (const inp of root.querySelectorAll("input[type=time]")) {
      const v = inp.dataset.which === "start" ? start : end;
      if (root.activeElement !== inp && inp.value !== v) inp.value = v;
    }

    let text;
    let warn = false;
    if (!this._st(c.start) || !this._st(c.end) || (c.days && !this._st(c.days))) {
      text = "Helper missing";
      warn = true;
    } else if (c.days && !sel.length) {
      text = "No days picked: never alerts";
      warn = true;
    } else if (start === end) {
      text = "From and To are the same";
      warn = true;
    } else {
      const days = c.days ? daysSummary(sel, c.every_day) : c.every_day;
      text = `${days} · ${label12(start)} - ${label12(end)}${end < start ? " (overnight)" : ""}`;
      if (!on) text = `Off · ${text}`;
    }
    const sum = root.querySelector(".sum");
    sum.textContent = text;
    sum.classList.toggle("warn", warn);
  }
}

if (!customElements.get("tnwks-alert-schedule-card")) {
  customElements.define("tnwks-alert-schedule-card", AlertScheduleCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "tnwks-alert-schedule-card",
    name: "Alert schedule",
    description: "On/off, days of the week and a time window for an alert.",
  });
  console.info(`tnwks-alert-schedule-card ${VERSION}`);
}
