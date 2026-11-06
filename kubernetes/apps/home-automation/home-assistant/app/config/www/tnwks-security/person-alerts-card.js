/*
 * tnwks person alerts card: one adult's own person-alert settings, on the
 * Security dashboard's Alerts view (dashboards/security.yaml,
 * packages/frigate_person_alerts.yaml).
 *
 *   type: custom:tnwks-person-alerts-card
 *   person: person.adult1
 *   plan: sensor.person_alerts       # optional, this is the default
 *
 * The helpers are found by the person's object id: input_*.person_alerts_
 * <adult1>_{night, night_critical, night_start, night_end, away,
 * schedule, schedule_days, schedule_start, schedule_end}. The card shows,
 * in plain words, what this adult's phone gets right now (from the plan
 * sensor's `plan` attribute, the same template the alert automation runs)
 * and what each of the three rules does:
 *   - Night: on/off, From/To (every night), ring through Do Not Disturb.
 *   - Away: Off / When <name>'s away / When everyone's away.
 *   - Schedule: on/off, day buttons, From/To.
 * Every change goes straight to the helpers through their services, and HA
 * restores helpers across restarts, so nothing is saved here. A time picker
 * being edited isn't overwritten by a state update.
 *
 * Plain custom element, no build step. Served from /local/tnwks-security/
 * (a ConfigMap mount); bump ?v= on its resource in configuration.yaml with
 * every change, since HA caches /local for a long time.
 */

const VERSION = "2.0.0";
const DAYS = [
  ["mon", "M", "Mon"],
  ["tue", "T", "Tue"],
  ["wed", "W", "Wed"],
  ["thu", "T", "Thu"],
  ["fri", "F", "Fri"],
  ["sat", "S", "Sat"],
  ["sun", "S", "Sun"],
];
// input_select options, in the package's order (the template compares these).
const AWAY = ["Off", "When I'm away", "When everyone's away"];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const hhmm = (state) => (/^\d{1,2}:\d{2}/.test(state || "") ? state.slice(0, 5).padStart(5, "0") : "");

function label12(t) {
  if (!t) return "--";
  const [h, m] = t.split(":").map(Number);
  const ap = h < 12 ? "AM" : "PM";
  return m ? `${h % 12 || 12}:${String(m).padStart(2, "0")} ${ap}` : `${h % 12 || 12} ${ap}`;
}

function daysSummary(sel) {
  if (sel.length === 7) return "Every day";
  if (!sel.length) return "";
  const idx = sel.map((d) => DAYS.findIndex(([k]) => k === d)).sort((a, b) => a - b);
  if (idx.join() === "0,1,2,3,4") return "Weekdays";
  if (idx.join() === "5,6") return "Weekends";
  // A consecutive run of three or more reads better as a range: Mon-Wed.
  const consecutive = idx.every((v, i) => i === 0 || v === idx[i - 1] + 1);
  if (consecutive && idx.length >= 3) return `${DAYS[idx[0]][2]}-${DAYS[idx[idx.length - 1]][2]}`;
  return idx.map((i) => DAYS[i][2]).join(", ");
}

const firstName = (hass, id) => (hass.states[id]?.attributes.friendly_name || id.split(".")[1]).split(" ")[0];

class PersonAlertsCard extends HTMLElement {
  setConfig(config) {
    if (!config.person || !config.person.startsWith("person.")) throw new Error("person (a person.* entity) is required");
    const k = `person_alerts_${config.person.split(".")[1]}`;
    this.config = { plan: "sensor.person_alerts", ...config };
    this.e = {
      night: `input_boolean.${k}_night`,
      critical: `input_boolean.${k}_night_critical`,
      nightStart: `input_datetime.${k}_night_start`,
      nightEnd: `input_datetime.${k}_night_end`,
      away: `input_select.${k}_away`,
      schedule: `input_boolean.${k}_schedule`,
      days: `input_text.${k}_schedule_days`,
      schedStart: `input_datetime.${k}_schedule_start`,
      schedEnd: `input_datetime.${k}_schedule_end`,
    };
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
  }

  getCardSize() {
    return 10;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  _st(id) {
    return id ? this._hass?.states[id] : undefined;
  }

  _on(id) {
    return this._st(id)?.state === "on";
  }

  _selectedDays() {
    const raw = this._st(this.e.days)?.state || "";
    const set = new Set(raw.toLowerCase().split(/[\s,]+/));
    return DAYS.map(([k]) => k).filter((k) => set.has(k));
  }

  _others() {
    const plan = this._st(this.config.plan)?.attributes.plan || [];
    return plan.map((x) => x.person).filter((p) => p !== this.config.person);
  }

  _build() {
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    const times = (which) => `
      <div class="times">
        <label>From<input type="time" step="300" data-entity="${which}Start"></label>
        <label>To<input type="time" step="300" data-entity="${which}End"></label>
      </div>`;
    root.innerHTML = `
      <style>
        ha-card { padding: 16px; }
        .head { display: flex; align-items: center; gap: 12px; }
        .avatar { width: 40px; height: 40px; border-radius: 50%; flex: none; background: var(--primary-color) center/cover;
                  color: var(--text-primary-color, #fff); display: flex; align-items: center; justify-content: center;
                  font-size: 18px; font-weight: 500; }
        .who { flex: 1; min-width: 0; }
        .name { font-size: 20px; font-weight: 500; color: var(--primary-text-color); }
        .where { font-size: 13px; color: var(--secondary-text-color); }
        .now { margin-top: 14px; padding: 10px 12px; border-radius: 10px; font-size: 14px; line-height: 1.35;
               background: rgba(127,127,127,.12); color: var(--primary-text-color); }
        .now b { font-weight: 500; }
        .now[data-tier="critical"] { background: rgba(219, 68, 55, .16); }
        .now[data-tier="night"] { background: rgba(103, 58, 183, .16); }
        .now[data-tier="normal"] { background: rgba(3, 169, 244, .16); }
        .now[data-tier="warn"] { background: rgba(219, 139, 0, .18); }
        .rule { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--divider-color, #ccc); }
        .top { display: flex; align-items: center; gap: 12px; }
        .title { flex: 1; font-size: 16px; font-weight: 500; color: var(--primary-text-color); }
        .sum { font-size: 13px; color: var(--secondary-text-color); margin-top: 2px; font-weight: 400; }
        .sum.warn { color: var(--warning-color, #db8b00); }
        .sw { position: relative; width: 44px; height: 24px; border-radius: 12px; border: 0; padding: 0;
              background: var(--disabled-color, #bdbdbd); cursor: pointer; transition: background .15s; flex: none; }
        .sw::after { content: ""; position: absolute; top: 3px; left: 3px; width: 18px; height: 18px; border-radius: 50%;
              background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,.3); transition: transform .15s; }
        .sw[aria-checked="true"] { background: var(--primary-color); }
        .sw[aria-checked="true"]::after { transform: translateX(20px); }
        .body { transition: opacity .15s; }
        .rule.off .body { opacity: .45; }
        .sub { display: flex; align-items: center; gap: 12px; margin-top: 12px; font-size: 14px; color: var(--primary-text-color); }
        .sub span { flex: 1; }
        .sub small { display: block; font-size: 12px; color: var(--secondary-text-color); }
        .days { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; margin-top: 12px; }
        .day, .seg button { height: 38px; border-radius: 19px; cursor: pointer; font: inherit; font-size: 14px; font-weight: 500;
               border: 1px solid var(--divider-color, #ccc); background: none; color: var(--primary-text-color); }
        .day[aria-pressed="true"], .seg button[aria-pressed="true"] { background: var(--primary-color);
               border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
        .seg { display: grid; grid-template-columns: 1fr 1.4fr 1.6fr; gap: 6px; margin-top: 12px; }
        .seg button { height: auto; min-height: 38px; padding: 6px 8px; border-radius: 12px; font-size: 13px; line-height: 1.2; }
        .times { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
        label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--secondary-text-color); }
        input[type="time"] { font: inherit; font-size: 16px; padding: 8px 10px; border-radius: 8px;
               border: 1px solid var(--divider-color, #ccc); background: var(--card-background-color);
               color: var(--primary-text-color); color-scheme: light; min-width: 0; }
        /* The native picker's icon and popup follow HA's theme, not the OS. */
        :host([dark]) input[type="time"] { color-scheme: dark; }
        button:focus-visible, input:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
      </style>
      <ha-card>
        <div class="head">
          <div class="avatar"></div>
          <div class="who"><div class="name"></div><div class="where"></div></div>
        </div>
        <div class="now"></div>

        <div class="rule" data-rule="night">
          <div class="top">
            <div class="title">At night<div class="sum"></div></div>
            <button class="sw" role="switch" data-entity="night" aria-label="Night alerts on/off"></button>
          </div>
          <div class="body">
            ${times("night")}
            <div class="sub"><span>Ring through Do Not Disturb<small>Rings on the alarm stream, even on silent</small></span>
              <button class="sw" role="switch" data-entity="critical" aria-label="Ring through Do Not Disturb"></button></div>
          </div>
        </div>

        <div class="rule" data-rule="away">
          <div class="top"><div class="title">When away from home<div class="sum"></div></div></div>
          <div class="body">
            <div class="seg">${AWAY.map((o, i) => `<button data-away="${i}"></button>`).join("")}</div>
          </div>
        </div>

        <div class="rule" data-rule="schedule">
          <div class="top">
            <div class="title">On a schedule<div class="sum"></div></div>
            <button class="sw" role="switch" data-entity="schedule" aria-label="Scheduled alerts on/off"></button>
          </div>
          <div class="body">
            <div class="days">${DAYS.map(([k, s, l]) => `<button class="day" data-day="${k}" aria-label="${l}" title="${l}">${s}</button>`).join("")}</div>
            ${times("sched")}
          </div>
        </div>
      </ha-card>`;

    root.querySelectorAll(".sw").forEach((b) =>
      b.addEventListener("click", () => {
        const id = this.e[b.dataset.entity];
        this._hass.callService("input_boolean", this._on(id) ? "turn_off" : "turn_on", { entity_id: id });
      }),
    );
    root.querySelectorAll(".seg button").forEach((b) =>
      b.addEventListener("click", () =>
        this._hass.callService("input_select", "select_option", { entity_id: this.e.away, option: AWAY[+b.dataset.away] }),
      ),
    );
    root.querySelectorAll(".day").forEach((b) =>
      b.addEventListener("click", () => {
        const sel = new Set(this._selectedDays());
        sel.has(b.dataset.day) ? sel.delete(b.dataset.day) : sel.add(b.dataset.day);
        const value = DAYS.map(([k]) => k).filter((k) => sel.has(k)).join(",");
        b.setAttribute("aria-pressed", String(sel.has(b.dataset.day))); // instant feedback
        this._hass.callService("input_text", "set_value", { entity_id: this.e.days, value });
      }),
    );
    root.querySelectorAll("input[type=time]").forEach((inp) =>
      inp.addEventListener("change", () => {
        if (!inp.value) return;
        this._hass.callService("input_datetime", "set_datetime", { entity_id: this.e[inp.dataset.entity], time: `${inp.value}:00` });
      }),
    );
    this._built = true;
  }

  // "9 PM - 6 AM" plus a warning, for one window.
  _window(startId, endId) {
    const s = hhmm(this._st(startId)?.state);
    const e = hhmm(this._st(endId)?.state);
    if (!s || !e) return { text: "Time not set", warn: true };
    if (s === e) return { text: "From and To are the same: never alerts", warn: true };
    return { text: `${label12(s)} - ${label12(e)}${e < s ? " (overnight)" : ""}`, warn: false };
  }

  _update() {
    const h = this._hass;
    const root = this.shadowRoot;
    const p = this._st(this.config.person);
    const name = firstName(h, this.config.person);
    const others = this._others().map((o) => firstName(h, o));
    const missing = Object.values(this.e).filter((id) => !this._st(id));

    this.toggleAttribute("dark", !!h.themes?.darkMode);
    const av = root.querySelector(".avatar");
    const pic = p?.attributes.entity_picture;
    av.style.backgroundImage = pic ? `url("${pic}")` : "";
    av.textContent = pic ? "" : name.slice(0, 1).toUpperCase();
    root.querySelector(".name").textContent = name;
    const loc = p?.state;
    root.querySelector(".where").textContent =
      loc === "home" ? "Home now" : loc === "not_home" ? "Away now" : loc && !["unknown", "unavailable"].includes(loc) ? `At ${loc} now` : "Location unknown";

    // Switches, buttons, times.
    root.querySelectorAll(".sw").forEach((b) => b.setAttribute("aria-checked", String(this._on(this.e[b.dataset.entity]))));
    const away = this._st(this.e.away)?.state;
    const awayLabels = ["Off", `When ${name}'s away`, "When everyone's away"];
    root.querySelectorAll(".seg button").forEach((b, i) => {
      b.textContent = awayLabels[i];
      b.setAttribute("aria-pressed", String(away === AWAY[i]));
    });
    const sel = this._selectedDays();
    root.querySelectorAll(".day").forEach((b) => b.setAttribute("aria-pressed", String(sel.includes(b.dataset.day))));
    for (const inp of root.querySelectorAll("input[type=time]")) {
      const v = hhmm(this._st(this.e[inp.dataset.entity])?.state);
      if (root.activeElement !== inp && inp.value !== v) inp.value = v;
    }

    // One line per rule, saying exactly what it does.
    const setRule = (rule, on, text, warn) => {
      const el = root.querySelector(`.rule[data-rule="${rule}"]`);
      el.classList.toggle("off", !on);
      const sum = el.querySelector(".sum");
      sum.textContent = text;
      sum.classList.toggle("warn", !!warn);
    };
    const nightOn = this._on(this.e.night);
    const nw = this._window(this.e.nightStart, this.e.nightEnd);
    const ring = this._on(this.e.critical) ? "rings through Do Not Disturb" : "normal push, waits for Do Not Disturb";
    setRule("night", nightOn, nightOn ? `Every night ${nw.text} · ${ring}` : `Off · ${name} gets no night alerts`, nightOn && nw.warn);

    const awayIdx = AWAY.indexOf(away);
    const everyone = others.length === 1 ? `${name} and ${others[0]} are both` : `${[name, ...others].join(", ")} are all`;
    const awayText = [
      `Off · being away doesn't send ${name} anything`,
      `Any time ${name} is away from home, normal push`,
      `Only while ${everyone} away, normal push`,
    ][awayIdx] ?? "Not set";
    setRule("away", awayIdx > 0, awayText, awayIdx < 0);

    const schedOn = this._on(this.e.schedule);
    const sw = this._window(this.e.schedStart, this.e.schedEnd);
    let schedText;
    let schedWarn = false;
    if (!schedOn) schedText = `Off · ${name} gets no scheduled alerts`;
    else if (!sel.length) [schedText, schedWarn] = ["No days picked: never alerts", true];
    else [schedText, schedWarn] = [`${daysSummary(sel)} ${sw.text} · home or not, normal push`, sw.warn];
    setRule("schedule", schedOn, schedText, schedWarn);

    // Right now, from the same template the automation runs.
    const now = root.querySelector(".now");
    const plan = (this._st(this.config.plan)?.attributes.plan || []).find((x) => x.person === this.config.person);
    let tier;
    let html;
    if (missing.length) {
      tier = "warn";
      html = `<b>Setting missing</b> (${esc(missing[0])}${missing.length > 1 ? ` and ${missing.length - 1} more` : ""})`;
    } else if (!plan) {
      tier = "warn";
      html = `<b>Status unknown</b>: ${esc(this.config.plan)} has no entry for ${esc(name)}`;
    } else {
      tier = plan.tier;
      const why = { Night: "night window", Away: "away from home", Schedule: "on schedule" };
      const reasons = String(plan.why || "").split(" + ").filter(Boolean).map((w) => why[w] || w).join(", ");
      html = {
        critical: `<b>Right now:</b> a person on camera <b>rings ${esc(name)}'s phone through Do Not Disturb</b> (${reasons})`,
        night: `<b>Right now:</b> a person on camera sends ${esc(name)} a <b>night alert</b> (${reasons})`,
        normal: `<b>Right now:</b> a person on camera sends ${esc(name)} a <b>normal push</b> (${reasons})`,
        quiet: `<b>Right now:</b> ${esc(name)} gets <b>nothing</b>`,
      }[tier] ?? `<b>Right now:</b> ${esc(tier)}`;
    }
    now.dataset.tier = tier;
    now.innerHTML = html;
  }
}

if (!customElements.get("tnwks-person-alerts-card")) {
  customElements.define("tnwks-person-alerts-card", PersonAlertsCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "tnwks-person-alerts-card",
    name: "Person alerts",
    description: "One adult's person-alert settings: night, away and schedule, with what applies right now.",
  });
  console.info(`tnwks-person-alerts-card ${VERSION}`);
}
