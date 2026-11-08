// Measured house consumption -> appliances. Solar production is deliberately
// separate: a production meter alone cannot measure solar self-consumption.
// Plain custom element, no dependencies or build step. Bump the resource ?v=
// in configuration.yaml whenever this file changes.

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const COLORS = [
  "#3984c6",
  "#b78a22",
  "#44896b",
  "#a171ad",
  "#c16d4a",
  "#5d92a0",
  "#7773b4",
  "#8a8751",
];

export function dayStart(timeZone, now = new Date()) {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  const parts = (date) =>
    Object.fromEntries(
      formatter.formatToParts(date).map((p) => [p.type, p.value]),
    );
  const local = parts(now);
  const midnight = Date.UTC(+local.year, +local.month - 1, +local.day);
  let candidate = midnight;
  // Resolve midnight in HA's timezone, including days with a DST transition.
  for (let i = 0; i < 3; i++) {
    const p = parts(new Date(candidate));
    candidate +=
      midnight -
      Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
  }
  return new Date(candidate).toISOString();
}

export function powerWatts(state) {
  if (!state || state.state === "" || !Number.isFinite(Number(state.state)))
    return null;
  const factor = { W: 1, kW: 1000, MW: 1000000 }[
    state.attributes.unit_of_measurement
  ];
  return factor ? Math.max(0, Number(state.state) * factor) : null;
}

export function energyChange(rows) {
  const changes = (rows || [])
    .map((row) => row.change)
    .filter((v) => typeof v === "number" && Number.isFinite(v));
  if (!changes.length) return null;
  const value = changes.reduce((sum, v) => sum + v, 0);
  return value >= 0 ? value : null;
}

export function branches(total, devices, limit = 8) {
  const positive = devices
    .filter((d) => Number.isFinite(d.value) && d.value > 0)
    .sort((a, b) => b.value - a.value);
  const tracked = positive.reduce((sum, d) => sum + d.value, 0);
  const visible = positive.slice(0, limit);
  const others = positive.slice(limit).reduce((sum, d) => sum + d.value, 0);
  if (others > 0) visible.push({ name: "Smaller loads", value: others });
  if (total - tracked > Math.max(total * 0.001, 0.000001))
    visible.push({ name: "Untracked", value: total - tracked });
  return { visible, tracked };
}

class EnergyFlowCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._mode = "power";
  }

  setConfig(config) {
    this._config = { title: "Energy distribution", max_devices: 8, ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this.isConnected && !this._prefs && !this._loading) this._refresh();
    this._render();
  }

  connectedCallback() {
    if (!this.shadowRoot.firstChild) this._build();
    this._refresh();
    this._timer = setInterval(() => this._refresh(), 60000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  getCardSize() {
    return 8;
  }
  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  _build() {
    this.shadowRoot.innerHTML = `<style>
      :host { display: block; container-type: inline-size; color: var(--primary-text-color); }
      * { box-sizing: border-box; }
      ha-card { padding: 24px; }
      header { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; justify-content: space-between; }
      h2 { font-size: 22px; font-weight: 500; margin: 0; }
      .modes { display: flex; padding: 3px; border-radius: 24px; background: var(--secondary-background-color); }
      button { font: inherit; color: inherit; cursor: pointer; }
      .modes button { border: 0; background: transparent; border-radius: 20px; padding: 8px 16px; }
      .modes button[aria-pressed="true"] { background: var(--ha-card-background, var(--card-background-color)); box-shadow: 0 1px 4px #0002; font-weight: 600; }
      button:focus-visible, a:focus-visible { outline: 3px solid var(--primary-color); outline-offset: 3px; }
      .period { margin: 8px 0 24px; font-size: 13px; color: var(--secondary-text-color); }
      .diagram { --house-width: clamp(82px, 16cqi, 116px); --wire-width: clamp(28px, 10cqi, 96px); position: relative; display: grid; grid-template-columns: var(--house-width) var(--wire-width) minmax(0, 1fr); align-items: center; }
      .home { text-align: center; position: relative; }
      .home-label { position: absolute; inset-inline: 0; inset-block-end: 100%; margin-block-end: 8px; font-size: 14px; }
      .home button { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; border: 2px solid var(--primary-color); background: var(--ha-card-background, var(--card-background-color)); border-radius: 50%; inline-size: min(100%, 116px); aspect-ratio: 1; padding: 8px; margin-inline: auto; }
      .home strong { font-size: clamp(16px, 2.4cqi, 22px); font-weight: 600; white-space: nowrap; }
      .home ha-icon { --mdc-icon-size: 26px; color: var(--primary-color); }
      .wires { position: absolute; inset-block: 0; inset-inline-start: var(--house-width); inline-size: var(--wire-width); block-size: 100%; overflow: visible; }
      .meters { display: grid; grid-column: 3; gap: 10px; }
      .meter { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-block-size: 54px; padding: 10px 14px; border: 1px solid var(--divider-color); border-inline-start: 4px solid var(--meter-color); border-radius: 10px; background: var(--ha-card-background, var(--card-background-color)); text-align: start; }
      button.meter:hover { background: var(--secondary-background-color); }
      .meter-name { font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-inline-size: 0; }
      .reading { flex-shrink: 0; text-align: end; }
      .reading strong { font-size: 15px; font-weight: 600; display: block; }
      .percentage { color: var(--secondary-text-color); font-size: 12px; }
      .status { color: var(--secondary-text-color); font-size: 13px; margin: 18px 0 0; }
      .solar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; border-block-start: 1px solid var(--divider-color); padding-block-start: 18px; margin-block-start: 24px; }
      .solar-title { display: flex; gap: 10px; align-items: center; font-size: 14px; }
      .solar ha-icon { color: var(--energy-solar-color, #ce8b14); }
      .solar strong { font-size: 20px; font-weight: 600; }
      .solar small { color: var(--secondary-text-color); display: block; margin-block-start: 4px; }
      .note { margin: 14px 0 0; color: var(--secondary-text-color); font-size: 12px; line-height: 1.5; }
      .empty { padding: 30px 0; color: var(--secondary-text-color); }
      [hidden] { display: none !important; }
      @container (max-width: 480px) {
        ha-card { padding: 16px; }
        h2 { font-size: 20px; }
        .meter { padding-inline: 9px; gap: 6px; }
        .meter-name { font-size: 12px; }
        .reading strong { font-size: 13px; }
        .home ha-icon { --mdc-icon-size: 22px; }
      }
    </style><ha-card>
      <header><h2></h2><div class="modes" role="group" aria-label="Distribution period">
        <button type="button" data-mode="power" aria-pressed="true">Live</button>
        <button type="button" data-mode="energy" aria-pressed="false">Today</button>
      </div></header>
      <p class="period"></p><div class="chart" aria-live="off"></div>
      <p class="status" role="status" hidden></p>
      <div class="solar" hidden><div><span class="solar-title"><ha-icon icon="mdi:solar-power"></ha-icon>Solar generated today</span><small>Production · updated hourly</small></div><strong></strong></div>
      <p class="note">Solar production is separate from house consumption. Grid import/export is not measured.</p>
    </ha-card>`;
    this.shadowRoot.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (button?.dataset.mode) {
        this._mode = button.dataset.mode;
        this._render();
      } else if (button?.dataset.entity) {
        this.dispatchEvent(
          new CustomEvent("hass-more-info", {
            bubbles: true,
            composed: true,
            detail: { entityId: button.dataset.entity },
          }),
        );
      }
    });
    this._render();
  }

  async _refresh() {
    if (!this._hass || !this.isConnected || this._loading) return;
    const start = dayStart(this._hass.config.time_zone);
    if (this._daily?.start !== start) this._daily = null;
    if (this._lastFetch > Date.now() - 300000 && this._daily) return;
    this._loading = true;
    try {
      const prefs = await this._hass.callWS({ type: "energy/get_prefs" });
      if (!this.isConnected) return;
      this._prefs = prefs;
      const home = prefs.energy_sources.find((s) => s.type === "home");
      if (!home) return;
      const devices = prefs.device_consumption.filter(
        (d) => !d.included_in_stat,
      );
      const solar = prefs.energy_sources
        .filter((s) => s.type === "solar")
        .map((s) => s.stat_energy_from);
      const request = {
        type: "recorder/statistics_during_period",
        start_time: start,
        end_time: new Date().toISOString(),
        types: ["change"],
        units: { energy: "kWh" },
      };
      const [stats, solarStats] = await Promise.all([
        this._hass.callWS({
          ...request,
          statistic_ids: [
            ...new Set([
              home.stat_energy_to,
              ...devices.map((d) => d.stat_consumption),
            ]),
          ],
          period: "5minute",
        }),
        solar.length
          ? this._hass.callWS({
              ...request,
              statistic_ids: solar,
              period: "hour",
            })
          : Promise.resolve({}),
      ]);
      if (!this.isConnected) return;
      const solarValues = solar
        .map((id) => energyChange(solarStats[id]))
        .filter((v) => v !== null);
      this._daily = {
        start,
        values: Object.fromEntries(
          Object.entries(stats).map(([id, rows]) => [id, energyChange(rows)]),
        ),
        through: stats[home.stat_energy_to]?.at(-1)?.end,
        solar:
          solar.length && solarValues.length === solar.length
            ? solarValues.reduce((sum, v) => sum + v, 0)
            : null,
      };
      this._lastFetch = Date.now();
      this._error = false;
    } catch {
      this._error = true;
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _name(device) {
    if (device.name) return device.name;
    const entity = this._hass.entities?.[device.stat_consumption];
    const registered = this._hass.devices?.[entity?.device_id];
    const state = this._hass.states[device.stat_consumption];
    return (
      registered?.name_by_user ||
      registered?.name ||
      state?.attributes.friendly_name?.replace(/ (daily energy|power)$/i, "") ||
      device.stat_consumption
        .split(".")[1]
        .replace(/_daily_energy$/, "")
        .replaceAll("_", " ")
    );
  }

  _format(value, mode = this._mode) {
    if (value === null || !Number.isFinite(value)) return "Unavailable";
    const locale = this._hass.locale?.language || "en";
    const power = mode === "power";
    const scale = power && value >= 1000 ? 1000 : 1;
    const unit = power ? (scale === 1000 ? "kW" : "W") : "kWh";
    return `${new Intl.NumberFormat(locale, { maximumFractionDigits: power && scale === 1 ? 0 : 2 }).format(value / scale)} ${unit}`;
  }

  _render() {
    if (!this.shadowRoot.firstChild || !this._hass) return;
    this.shadowRoot.querySelector("h2").textContent =
      this._config?.title || "Energy distribution";
    for (const button of this.shadowRoot.querySelectorAll("[data-mode]"))
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.mode === this._mode),
      );
    const home = this._prefs?.energy_sources.find((s) => s.type === "home");
    const daily = this._mode === "energy";
    let period = daily ? "Recorded energy today" : "Live house power";
    if (daily && this._daily?.through) {
      const timestamp = this._daily.through;
      const through = new Date(
        typeof timestamp === "number" && timestamp < 1e12
          ? timestamp * 1000
          : timestamp,
      );
      period += ` · through ${new Intl.DateTimeFormat(this._hass.locale?.language || "en", { timeZone: this._hass.config.time_zone, hour: "numeric", minute: "2-digit" }).format(through)}`;
    }
    this.shadowRoot.querySelector(".period").textContent = period;
    const chart = this.shadowRoot.querySelector(".chart");
    const total = home
      ? daily
        ? (this._daily?.values[home.stat_energy_to] ?? null)
        : powerWatts(this._hass.states[home.stat_rate])
      : null;
    const devices = (this._prefs?.device_consumption || [])
      .filter((d) => !d.included_in_stat)
      .map((d) => ({
        name: this._name(d),
        entity: daily ? d.stat_consumption : d.stat_rate,
        value: daily
          ? (this._daily?.values[d.stat_consumption] ?? null)
          : powerWatts(this._hass.states[d.stat_rate]),
      }));
    const { visible, tracked } = branches(
      total,
      devices,
      this._config?.max_devices ?? 8,
    );
    const key = JSON.stringify([
      this._mode,
      total,
      visible,
      home?.stat_rate,
      home?.stat_energy_to,
      this._loading,
      this._error,
    ]);
    if (key !== this._chartKey) {
      const focused = this.shadowRoot.activeElement?.dataset.entity;
      this._chartKey = key;
      if (total === null) {
        const message = !this._prefs
          ? "Loading house meters…"
          : !home
            ? "A house consumption meter is not configured."
            : daily && !this._daily && !this._error
              ? "Loading today's readings…"
              : "House reading unavailable.";
        chart.innerHTML = `<p class="empty" role="status">${message}</p>`;
      } else {
        const height = Math.max(1, visible.length) * 64 - 10;
        const wires = visible
          .map(
            (d, i) =>
              `<path d="M0 ${height / 2} C45 ${height / 2} 45 ${i * 64 + 27} 100 ${i * 64 + 27}" fill="none" stroke="${COLORS[i % COLORS.length]}" stroke-opacity=".45" stroke-width="${Math.max(2, Math.min(1, total > 0 ? d.value / total : 0) * 18)}" vector-effect="non-scaling-stroke"/>`,
          )
          .join("");
        const meters = visible
          .map((d, i) => {
            const percent =
              total > 0 ? `${Math.round((d.value / total) * 100)}%` : "";
            const tag = d.entity ? "button" : "div";
            return `<${tag} ${d.entity ? `type="button" data-entity="${esc(d.entity)}"` : ""} class="meter" style="--meter-color:${COLORS[i % COLORS.length]}" aria-label="${esc(`${d.name}: ${this._format(d.value)}, ${percent} of house usage`)}"><span class="meter-name">${esc(d.name)}</span><span class="reading"><strong>${esc(this._format(d.value))}</strong><span class="percentage">${percent}</span></span></${tag}>`;
          })
          .join("");
        chart.innerHTML = `<div class="diagram"><div class="home"><span class="home-label">House</span><button type="button" data-entity="${esc(daily ? home.stat_energy_to : home.stat_rate)}" aria-label="${esc(`House: ${this._format(total)}`)}"><ha-icon icon="mdi:home-lightning-bolt"></ha-icon><strong>${esc(this._format(total))}</strong></button></div><svg class="wires" viewBox="0 0 100 ${height}" preserveAspectRatio="none" aria-hidden="true" focusable="false">${wires}</svg><div class="meters">${meters || '<p class="empty">No device usage reported.</p>'}</div></div>`;
      }
      if (focused) {
        const target =
          [...chart.querySelectorAll("[data-entity]")].find(
            (el) => el.dataset.entity === focused,
          ) ||
          chart.querySelector(".home button") ||
          this.shadowRoot.querySelector(`[data-mode="${this._mode}"]`);
        target?.focus({ preventScroll: true });
      }
    }
    const status = this.shadowRoot.querySelector(".status");
    status.textContent = this._error
      ? "Recorded totals could not be refreshed."
      : total !== null && tracked > total * 1.02
        ? "Device meters update separately; their latest readings exceed the house total."
        : "";
    status.hidden = !status.textContent;
    const solar = this.shadowRoot.querySelector(".solar");
    solar.hidden = this._daily?.solar == null;
    solar.querySelector("strong").textContent = this._format(
      this._daily?.solar ?? null,
      "energy",
    );
    const hasGrid = this._prefs?.energy_sources.some(
      (source) => source.type === "grid",
    );
    this.shadowRoot.querySelector(".note").textContent =
      `Solar production is separate from house consumption.${hasGrid ? "" : " Grid import/export is not measured."}`;
  }
}

if (!customElements.get("tnwks-energy-flow-card"))
  customElements.define("tnwks-energy-flow-card", EnergyFlowCard);
