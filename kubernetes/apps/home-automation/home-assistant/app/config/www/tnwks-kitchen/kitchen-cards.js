/*
 * tnwks kitchen cards: the custom cards behind the Kitchen dashboard
 * (dashboards/kitchen.yaml) on the Apolosign wall display.
 *
 * Plain custom elements, no build step and no dependencies. Every card
 * renders into its own shadow root from `hass` and re-renders at most a few
 * times a second (patched in place with morph(), not replaced). Routines are
 * the items of the todo.kitchen_routines list (see "routines" below).
 *
 * Served from /local/tnwks-kitchen/ (a ConfigMap mount). HA caches /local for
 * a long time, so bump ?v= on the resource in configuration.yaml whenever
 * this file changes.
 */

const VERSION = "1.3.1";

// The HA app keeps this module in memory until the page reloads, but HA hands
// the page a new dashboard config live, including right after a restart. An
// old module with a new config is how every kid card crashed on 2026-10-31
// (member keys had changed). So when the resource URL HA serves stops
// matching the one this module was loaded from (a new ?v=), reload the page.
// At most once every 10 minutes, so a bad deploy can't loop.
const SELF = new URL(import.meta.url);
let versionWatched = false;
function watchVersion(hass) {
  const conn = hass?.connection;
  if (versionWatched || !conn?.sendMessagePromise) return;
  versionWatched = true;
  const check = () =>
    conn
      .sendMessagePromise({ type: "lovelace/resources" })
      .then((res) => {
        const want = (res || []).map((r) => new URL(r.url, location.origin)).find((u) => u.pathname === SELF.pathname);
        if (!want || want.search === SELF.search) return;
        const last = Number(sessionStorage.getItem("tnwks-kitchen-reload") || 0);
        if (Date.now() - last < 10 * 60 * 1000) return;
        sessionStorage.setItem("tnwks-kitchen-reload", String(Date.now()));
        console.info(`tnwks kitchen cards: loaded ${SELF.search || "(no ?v)"}, HA serves ${want.search}; reloading`);
        location.reload();
      })
      .catch(() => {});
  check();
  conn.addEventListener?.("ready", check); // reconnected, e.g. after an HA restart
  setInterval(check, 15 * 60 * 1000);
}

// The family. Trackers drive the "home" dot; keywords put calendar events in
// a person's column (an event naming nobody spans every column). Names aren't
// in this file (the repo is public): a person's display name is the friendly
// name of their stars counter, which HA reads from an env var
// (packages/kitchen_routines.yaml), and syncFamily() picks it up from hass.
const FAMILY = [
  { key: "adult1", color: "#ef6f8a", tracker: "device_tracker.pixel_8_pro" },
  { key: "adult2", color: "#a98bf0", tracker: "device_tracker.pixel_9a" },
  { key: "kid1", color: "#4fb3f0", kid: true },
  { key: "kid2", color: "#f07a5f", kid: true },
];
for (const m of FAMILY) {
  m.name = m.key;
  m.keywords = m.kid ? ["kids"] : [];
}

function syncFamily(hass) {
  for (const m of FAMILY) {
    const n = hass?.states?.[`counter.stars_${m.key}`]?.attributes?.friendly_name;
    if (!n || n === m.name) continue;
    m.name = n;
    m.keywords = [n.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), ...(m.kid ? ["kids"] : [])];
  }
}
const MEMBER = Object.fromEntries(FAMILY.map((m) => [m.key, m]));
function member(key) {
  const m = MEMBER[key];
  if (!m) throw new Error(`unknown member "${key}" (expected one of ${Object.keys(MEMBER).join(", ")})`);
  return m;
}

const PERIODS = [
  { key: "morning", label: "Morning", icon: "🌅" },
  { key: "afternoon", label: "Afternoon", icon: "☀️" },
  { key: "evening", label: "Evening", icon: "🌙" },
  { key: "anytime", label: "Anytime", icon: "⭐" },
];

const WEATHER_ICON = {
  "clear-night": "🌙", cloudy: "☁️", exceptional: "⚠️", fog: "🌫️", hail: "🌨️",
  lightning: "🌩️", "lightning-rainy": "⛈️", partlycloudy: "⛅", pouring: "🌧️",
  rainy: "🌦️", snowy: "❄️", "snowy-rainy": "🌨️", sunny: "☀️", windy: "💨",
  "windy-variant": "💨",
};
const WEATHER_TEXT = {
  "clear-night": "Clear", cloudy: "Cloudy", fog: "Fog", hail: "Hail", lightning: "Lightning",
  "lightning-rainy": "Storms", partlycloudy: "Partly cloudy", pouring: "Heavy rain", rainy: "Rain",
  snowy: "Snow", "snowy-rainy": "Sleet", sunny: "Sunny", windy: "Windy", "windy-variant": "Windy",
};

// ---------------------------------------------------------------- helpers

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
const today = () => DAYS[new Date().getDay()];

function currentPeriod(d = new Date()) {
  const h = d.getHours() + d.getMinutes() / 60;
  if (h < 12) return "morning";
  if (h < 17) return "afternoon";
  return "evening";
}

function fmtTime(d) {
  let h = d.getHours();
  const m = d.getMinutes();
  const ap = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  return m ? `${h}:${String(m).padStart(2, "0")} ${ap}` : `${h} ${ap}`;
}
const fmtDay = (d) => d.toLocaleDateString(undefined, { weekday: "short" });
const startOfDay = (d = new Date()) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const startOfWeek = (d = new Date()) => addDays(startOfDay(d), -d.getDay());

/** All routines for a member, sorted, with today's due flag and points. */
function chores(hass, member) {
  const day = today();
  if (Routines.active(hass)) {
    Routines.sync(hass);
    return Routines.all(member).map((r) => ({
      id: r.uid,
      routine: r,
      title: r.title,
      emoji: r.emoji,
      period: r.period,
      points: Number(r.points_by_day?.[day] ?? r.points ?? 0),
      due: r.days.includes(day),
      done: Routines.isDone(r),
      order: r.index,
    }));
  }
  // Before todo.kitchen_routines exists: the old git-defined input_booleans.
  return Object.values(hass.states)
    .filter((s) => s.entity_id.startsWith("input_boolean.") && s.attributes.kitchen_chore && s.attributes.member === member)
    .map((s) => {
      const a = s.attributes;
      const pbd = a.points_by_day || {};
      return {
        id: s.entity_id,
        title: a.title || a.friendly_name,
        emoji: a.emoji || "✅",
        period: a.period || "anytime",
        points: Number(pbd[day] ?? a.points ?? 0),
        due: (a.days || DAYS).includes(day),
        done: s.state === "on",
        order: a.order ?? 0,
      };
    })
    .sort((a, b) => a.order - b.order);
}
function setChore(hass, id, done) {
  if (id.startsWith("input_boolean.")) return hass.callService("input_boolean", done ? "turn_on" : "turn_off", { entity_id: id });
  return Routines.set(hass, id, done);
}

function stars(hass, member) {
  const s = hass.states[`counter.stars_${member}`];
  return s ? Number(s.state) || 0 : 0;
}

/** The next chore a member should do now: current period, then anytime, then later periods. */
function nextChore(list) {
  const order = ["morning", "afternoon", "evening"];
  const cur = currentPeriod();
  const later = order.slice(order.indexOf(cur));
  for (const p of [cur, "anytime", ...later.slice(1)]) {
    const c = list.find((x) => x.due && !x.done && x.period === p);
    if (c) return c;
  }
  // Earlier periods that were missed.
  return list.find((x) => x.due && !x.done) || null;
}

// Calendar events across several calendars, cached per range for a few minutes.
const calCache = new Map();
async function fetchEvents(hass, entities, start, end) {
  const key = `${entities.join(",")}|${start.toISOString()}|${end.toISOString()}`;
  const hit = calCache.get(key);
  if (hit && Date.now() - hit.t < 4 * 60 * 1000) return hit.p;
  const p = Promise.all(
    entities
      .filter((e) => hass.states[e])
      .map((e) =>
        hass
          .callApi("GET", `calendars/${e}?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`)
          .then((evs) => evs.map((ev) => normalizeEvent(ev, e)))
          .catch(() => [])
      )
  ).then((lists) => lists.flat().sort((a, b) => a.start - b.start));
  calCache.set(key, { t: Date.now(), p });
  return p;
}

function normalizeEvent(ev, calendar) {
  const allDay = !!(ev.start && ev.start.date);
  const parse = (x) => (x.dateTime ? new Date(x.dateTime) : new Date(`${x.date}T00:00:00`));
  const start = parse(ev.start);
  const end = parse(ev.end);
  const text = `${ev.summary || ""} ${ev.description || ""}`.toLowerCase();
  let who = FAMILY.filter((m) => m.keywords.some((k) => new RegExp(`\\b${k}\\b`).test(text))).map((m) => m.key);
  return { title: ev.summary || "(no title)", start, end, allDay, who, calendar };
}

function eventColor(ev) {
  if (ev.who.length === 1) return MEMBER[ev.who[0]].color;
  return "#f2d36b";
}

// ------------------------------------------------------------- DOM morph

// Renders used to replace a card's whole shadow DOM on every change, which
// costs a full re-layout on the tablet, resets scroll positions and cuts
// every CSS transition short. morph() patches the live DOM to match the new
// HTML instead: unchanged nodes stay put, changed attributes are updated in
// place (so class changes animate), and elements with a data-k key are
// matched by key (a new key is a new element, so its entrance animation runs).
function morphChildren(a, b) {
  let cur = a.firstChild;
  for (const nb of Array.from(b.childNodes)) {
    const key = nb.nodeType === 1 ? nb.getAttribute("data-k") : null;
    if (key != null) {
      let hit = cur;
      while (hit && !(hit.nodeType === 1 && hit.getAttribute("data-k") === key && hit.nodeName === nb.nodeName)) hit = hit.nextSibling;
      if (hit) {
        if (hit !== cur) a.insertBefore(hit, cur);
        morphNode(hit, nb);
        cur = hit.nextSibling;
      } else a.insertBefore(nb, cur);
      continue;
    }
    if (cur && cur.nodeType === nb.nodeType && cur.nodeName === nb.nodeName && !(cur.nodeType === 1 && cur.hasAttribute("data-k"))) {
      morphNode(cur, nb);
      cur = cur.nextSibling;
    } else a.insertBefore(nb, cur);
  }
  while (cur) {
    const n = cur.nextSibling;
    a.removeChild(cur);
    cur = n;
  }
}
function morphNode(a, b) {
  if (a.nodeType !== 1) {
    if (a.nodeValue !== b.nodeValue) a.nodeValue = b.nodeValue;
    return;
  }
  for (const { name } of Array.from(a.attributes)) if (!b.hasAttribute(name)) a.removeAttribute(name);
  for (const { name, value } of Array.from(b.attributes)) if (a.getAttribute(name) !== value) a.setAttribute(name, value);
  if (a.nodeName === "INPUT" || a.nodeName === "TEXTAREA" || a.nodeName === "SELECT") return; // keep what's being typed
  morphChildren(a, b);
}
const tpl = document.createElement("template");
function morph(root, html) {
  tpl.innerHTML = html;
  morphChildren(root, tpl.content);
}

// ------------------------------------------------------------ animations

const REDUCED = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const MOTION_CSS = `
  button { position:relative; overflow:hidden; transition:transform .14s ease, background-color .25s, color .25s, opacity .25s, filter .14s; }
  button:active { transform:scale(.96); filter:brightness(1.12); }
  .rip { position:absolute; border-radius:50%; background:currentColor; opacity:.25; transform:scale(0); pointer-events:none;
    animation:krip .55s ease-out forwards; }
  @keyframes krip { to { transform:scale(1); opacity:0; } }
  @keyframes kin { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:none; } }
  @keyframes kfade { from { opacity:0; } to { opacity:1; } }
  @keyframes kpop { 0% { transform:scale(.5); } 60% { transform:scale(1.22); } 100% { transform:scale(1); } }
  @keyframes kbump { 0% { transform:scale(1); } 35% { transform:scale(1.3); color:#ffd54a; } 100% { transform:scale(1); } }
  @keyframes kout { to { opacity:0; transform:translateX(28px); } }
  .enter { animation:kin .26s ease-out both; }
  .fade { animation:kfade .22s ease-out both; }
  .bump { display:inline-block; animation:kbump .6s ease-out; }
  @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation:none !important; transition:none !important; } }
`;
const FX_CSS = `
  .f { position:fixed; left:0; top:0; pointer-events:none; will-change:transform, opacity; font-family:Roboto,"Noto Sans",sans-serif; }
  .float { font-size:24px; font-weight:700; color:#ffd54a; text-shadow:0 2px 6px rgba(0,0,0,.5); animation:fxup 1.1s ease-out forwards; }
  @keyframes fxup { 0% { opacity:0; transform:translate(-50%,-50%) scale(.6); } 15% { opacity:1; transform:translate(-50%,-70%) scale(1.15); }
    100% { opacity:0; transform:translate(-50%,-260%) scale(1); } }
  .spark { width:9px; height:9px; border-radius:50%; animation:fxspark .6s ease-out forwards; }
  @keyframes fxspark { from { opacity:1; transform:translate(-50%,-50%) rotate(var(--r)) translateX(0) scale(1); }
    to { opacity:0; transform:translate(-50%,-50%) rotate(var(--r)) translateX(var(--d)) scale(.3); } }
  .conf { width:10px; height:14px; border-radius:2px; animation:fxconf var(--t) cubic-bezier(.2,.6,.4,1) forwards; }
  @keyframes fxconf { 0% { opacity:1; transform:translate(var(--x0),var(--y0)) rotate(0); }
    100% { opacity:0; transform:translate(var(--x1),var(--y1)) rotate(var(--rot)); } }
  .p { position:fixed; top:0; left:0; opacity:var(--o); will-change:transform; animation:var(--anim) var(--t) linear var(--delay) infinite; font-size:var(--s); }
  @keyframes pfall { from { transform:translate3d(var(--x),-10vh,0) rotate(0); } to { transform:translate3d(calc(var(--x) + var(--sway)),110vh,0) rotate(var(--rot)); } }
  @keyframes prise { from { transform:translate3d(var(--x),110vh,0) rotate(0); } to { transform:translate3d(calc(var(--x) + var(--sway)),-10vh,0) rotate(var(--rot)); } }
  @keyframes pdrift { 0% { transform:translate3d(-12vw,var(--y),0) rotate(-8deg); } 50% { transform:translate3d(50vw,calc(var(--y) + var(--sway)),0) rotate(8deg); }
    100% { transform:translate3d(112vw,var(--y),0) rotate(-8deg); } }
  @keyframes ptwinkle { 0%,100% { opacity:0; transform:translate3d(var(--x),var(--y),0) scale(.6); } 50% { opacity:var(--o); transform:translate3d(var(--x),var(--y),0) scale(1.1); } }
  @media (prefers-reduced-motion: reduce) { .f, .p { display:none; } }
`;
/** Tap feedback that outlives a re-render: drawn on one fixed layer over the whole page. */
const FX = {
  layer() {
    if (!this._root || !this._host.isConnected) {
      this._host = document.createElement("div");
      this._host.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:9000;overflow:hidden";
      this._root = this._host.attachShadow({ mode: "open" });
      this._root.innerHTML = `<style>${FX_CSS}</style>`;
      document.body.appendChild(this._host);
    }
    return this._root;
  },
  _add(cls, style, text, ms) {
    if (REDUCED()) return;
    const el = document.createElement("div");
    el.className = `f ${cls}`;
    el.style.cssText = style;
    if (text) el.textContent = text;
    this.layer().appendChild(el);
    setTimeout(() => el.remove(), ms);
  },
  float(x, y, text) {
    this._add("float", `left:${x}px;top:${y}px`, text, 1200);
  },
  burst(x, y, color = "#4cd17a", n = 10) {
    for (let i = 0; i < n; i++)
      this._add("spark", `left:${x}px;top:${y}px;background:${i % 3 ? color : "#ffd54a"};--r:${(360 / n) * i + Math.random() * 20}deg;--d:${34 + Math.random() * 26}px`, "", 700);
  },
  confetti(rect, colors) {
    const cs = colors || ["#ff9f43", "#4cd17a", "#4fb3f0", "#f07a5f", "#ffd54a", "#a98bf0"];
    const cx = rect.left + rect.width / 2;
    for (let i = 0; i < 46; i++) {
      const x0 = cx + (Math.random() - 0.5) * 40;
      const y0 = rect.top + rect.height * 0.35;
      const x1 = x0 + (Math.random() - 0.5) * rect.width * 1.3;
      const y1 = y0 + 120 + Math.random() * rect.height * 0.9;
      this._add("conf", `background:${cs[i % cs.length]};--x0:${x0}px;--y0:${y0}px;--x1:${x1}px;--y1:${y1}px;--rot:${(Math.random() - 0.5) * 900}deg;--t:${1.1 + Math.random() * 0.9}s`, "", 2200);
    }
  },
  /** Center of an element, for anchoring an effect to what was tapped. */
  at(el) {
    const r = el.getBoundingClientRect();
    return [r.left + r.width / 2, r.top + r.height / 2];
  },
};

// ---------------------------------------------------------------- themes

// Holiday themes: a palette, falling/rising/drifting emoji, a garland over
// the header, and a greeting on the day. The dashboard offers each one in its
// window (lead days before, through `after` days past the holiday) and the
// choice is input_select.kitchen_theme (packages/kitchen_display.yaml), so
// every screen in the house shows the same one. A holiday theme switches
// itself off in the week after its holiday.
function easter(y) {
  const a = y % 19, b = Math.floor(y / 100), c = y % 100, d = Math.floor(b / 4), e = b % 4, f = Math.floor((b + 8) / 25);
  const g = Math.floor((b - f + 1) / 3), h = (19 * a + b - d - g + 15) % 30, i = Math.floor(c / 4), k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451);
  const n = h + l - 7 * m + 114;
  return new Date(y, Math.floor(n / 31) - 1, (n % 31) + 1);
}
const nthWeekday = (y, mo, wd, n) => {
  const first = new Date(y, mo, 1);
  return new Date(y, mo, 1 + ((wd - first.getDay() + 7) % 7) + 7 * (n - 1));
};
const DEFAULT_THEME = { name: "Everyday", emoji: "🏠", vars: {} };
const THEMES = {
  newyear: { name: "New Year", emoji: "🎉", day: (y) => new Date(y, 0, 1), lead: 2, greet: "Happy New Year!",
    vars: { accent: "#e9c46a", bg: "#0e0f1a", card: "#181a2b", card2: "#22253a", line: "#30344d" },
    particles: ["✨", "🎉", "🎊", "⭐"], motion: "twinkle", garland: "🎉 ✨ 🎊 🥂 ✨" },
  valentines: { name: "Valentine's", emoji: "💕", day: (y) => new Date(y, 1, 14), lead: 7, greet: "Happy Valentine's Day!",
    vars: { accent: "#ff5c8a", bg: "#170d12", card: "#24151d", card2: "#311d28", line: "#432837" },
    particles: ["💕", "💗", "❤️", "💘"], motion: "rise", garland: "💕 💌 💗 🌹 💘" },
  stpatricks: { name: "St. Patrick's", emoji: "☘️", day: (y) => new Date(y, 2, 17), lead: 5, greet: "Happy St. Patrick's Day!",
    vars: { accent: "#3ccf6e", bg: "#0c1510", card: "#15231a", card2: "#1d3024", line: "#284232" },
    particles: ["☘️", "🍀", "🌈", "🪙"], motion: "fall", garland: "☘️ 🍀 🌈 🪙 ☘️" },
  easter: { name: "Easter", emoji: "🐣", day: easter, lead: 7, greet: "Happy Easter!",
    vars: { accent: "#b48cf2", bg: "#13121a", card: "#1e1c29", card2: "#292637", line: "#37334a" },
    particles: ["🌷", "🐣", "🥚", "🐰", "🌼"], motion: "rise", garland: "🐣 🌷 🥚 🐰 🌼" },
  july4: { name: "4th of July", emoji: "🎆", day: (y) => new Date(y, 6, 4), lead: 4, greet: "Happy 4th of July!",
    vars: { accent: "#4f8cff", bg: "#0d1220", card: "#161d30", card2: "#1f283f", line: "#2c3753" },
    particles: ["⭐", "🎆", "🎇", "✨"], motion: "twinkle", garland: "🎆 ⭐ 🇺🇸 ⭐ 🎇" },
  halloween: { name: "Halloween", emoji: "🎃", day: (y) => new Date(y, 9, 31), lead: 14, greet: "Happy Halloween!",
    vars: { accent: "#ff7a1a", bg: "#120d17", card: "#1d1626", card2: "#2a2035", line: "#3a2d48" },
    particles: ["🦇", "👻", "🎃", "🕸️", "🦇"], motion: "drift", garland: "🎃 🦇 🕸️ 👻 🍬" },
  thanksgiving: { name: "Thanksgiving", emoji: "🦃", day: (y) => nthWeekday(y, 10, 4, 4), lead: 7, greet: "Happy Thanksgiving!",
    vars: { accent: "#e08a3c", bg: "#15110d", card: "#221b14", card2: "#2e251b", line: "#3d3125" },
    particles: ["🍂", "🍁", "🍂", "🌰"], motion: "fall", garland: "🍂 🦃 🥧 🌽 🍁" },
  christmas: { name: "Christmas", emoji: "🎄", day: (y) => new Date(y, 11, 25), lead: 24, after: 1, greet: "Merry Christmas!",
    vars: { accent: "#e5484d", ok: "#3fbf6a", bg: "#0d1512", card: "#16211c", card2: "#1f2d26", line: "#2b3d33" },
    particles: ["❄️", "❄️", "✨", "❄️"], motion: "fall", garland: "🎄 ❄️ 🎁 ⛄ ✨" },
};
const MS_DAY = 86400000;
/** The holiday whose window today falls in: { key, theme, date, days } (days until it, 0 = today). */
function holidayNow(now = new Date()) {
  const t = startOfDay(now);
  for (const [key, th] of Object.entries(THEMES)) {
    for (const y of [t.getFullYear(), t.getFullYear() + 1]) {
      const d = th.day(y);
      const days = Math.round((d - t) / MS_DAY);
      if (days <= th.lead && days >= -(th.after || 0)) return { key, theme: th, date: d, year: d.getFullYear(), days };
    }
  }
  return null;
}
/** True for a holiday theme in the month after its holiday (time to switch it off). */
function themeExpired(key, now = new Date()) {
  const th = THEMES[key];
  if (!th) return false;
  const t = startOfDay(now);
  return [t.getFullYear() - 1, t.getFullYear()].some((y) => {
    const end = addDays(th.day(y), th.after || 0);
    return t > end && t - end <= 7 * MS_DAY;
  });
}
const THEME_ENTITY = "input_select.kitchen_theme";
const THEME_ANSWERED = "input_text.kitchen_theme_answered";
const Theme = {
  key: "default",
  current() {
    return THEMES[this.key] || DEFAULT_THEME;
  },
  apply(hass) {
    const s = hass?.states?.[THEME_ENTITY];
    let key = s && THEMES[s.state] ? s.state : "default";
    if (key !== "default" && themeExpired(key)) {
      // One screen switches it back for the house; the rest follow its state.
      if (!this._expiring) {
        this._expiring = true;
        hass.callService("input_select", "select_option", { entity_id: THEME_ENTITY, option: "default" }).finally(() => (this._expiring = false));
      }
      key = "default";
    }
    if (key === this.key) return;
    this.key = key;
    const th = this.current();
    const root = document.documentElement.style;
    for (const v of ["accent", "ink", "bg", "card", "card2", "line", "ok"]) {
      if (th.vars[v]) root.setProperty(`--k-${v}`, th.vars[v]);
      else root.removeProperty(`--k-${v}`);
    }
    this._particles(th);
    window.dispatchEvent(new CustomEvent("kitchen-theme", { detail: key }));
  },
  // A dozen slow emoji over the page. Only transform and opacity animate, so
  // the tablet's GPU does the work and the cards never re-layout.
  _particles(th) {
    this._host?.remove();
    this._host = null;
    if (!th.particles || REDUCED()) return;
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:8000;overflow:hidden";
    const root = host.attachShadow({ mode: "open" });
    const n = th.motion === "twinkle" ? 14 : 12;
    let html = `<style>${FX_CSS}</style>`;
    for (let i = 0; i < n; i++) {
      const anim = { fall: "pfall", rise: "prise", drift: "pdrift", twinkle: "ptwinkle" }[th.motion] || "pfall";
      const t = th.motion === "drift" ? 26 + Math.random() * 22 : th.motion === "twinkle" ? 3 + Math.random() * 4 : 16 + Math.random() * 18;
      const style = [
        `--x:${Math.round(Math.random() * 100)}vw`, `--y:${Math.round(8 + Math.random() * 80)}vh`, `--sway:${Math.round((Math.random() - 0.5) * 120)}px`,
        `--rot:${Math.round((Math.random() - 0.5) * 540)}deg`, `--t:${t.toFixed(1)}s`, `--delay:${(-Math.random() * t).toFixed(1)}s`,
        `--s:${Math.round(18 + Math.random() * 14)}px`, `--o:${(0.35 + Math.random() * 0.3).toFixed(2)}`, `--anim:${anim}`,
      ].join(";");
      html += `<div class="p" style="${style}">${th.particles[i % th.particles.length]}</div>`;
    }
    root.innerHTML = html;
    document.body.appendChild(host);
    this._host = host;
  },
};

// -------------------------------------------------------------- routines

// Routines live in a Local To-do list (todo.kitchen_routines, UI-owned), one
// item per routine, so the family can add and edit them on the display. The
// item's summary is the routine's name, its status is "done today", and its
// description is JSON: {"member","emoji","period","points","days"[,
// "points_by_day"]}. Checking one off goes through script.kitchen_routine_set
// (packages/kitchen_routines.yaml), which also moves the stars; the midnight
// reset reopens everything. Before that list exists, the cards fall back to
// the old input_boolean chores.
const ROUTINES = "todo.kitchen_routines";
const ALL_DAYS = DAYS;
function parseRoutine(i, index) {
  let m = {};
  try {
    m = JSON.parse(i.description || "{}") || {};
  } catch (_) {}
  if (!m.member) return null;
  return {
    uid: i.uid, index, title: i.summary, member: m.member, emoji: m.emoji || "✅", period: m.period || "anytime",
    points: Number(m.points) || 0, points_by_day: m.points_by_day || null, days: Array.isArray(m.days) && m.days.length ? m.days : ALL_DAYS,
    done: i.status === "completed",
  };
}
const Routines = {
  items: null,
  pending: new Map(),
  _sub: null,
  active(hass) {
    return !!hass?.states?.[ROUTINES];
  },
  sync(hass) {
    if (this._sub || !this.active(hass)) return;
    this._sub = hass.connection
      .subscribeMessage((msg) => {
        this.items = (msg.items || []).map(parseRoutine).filter(Boolean);
        for (const r of this.items) {
          const p = this.pending.get(r.uid);
          if (p && (p.done === r.done || Date.now() - p.t > 10000)) this.pending.delete(r.uid);
        }
        this._notify();
      }, { type: "todo/item/subscribe", entity_id: ROUTINES })
      .catch(() => {
        this._sub = null;
        return null;
      });
  },
  _notify() {
    window.dispatchEvent(new Event("kitchen-routines"));
  },
  all(member) {
    return (this.items || []).filter((r) => !member || r.member === member);
  },
  isDone(r) {
    const p = this.pending.get(r.uid);
    return p && Date.now() - p.t < 10000 ? p.done : r.done;
  },
  set(hass, uid, done) {
    this.pending.set(uid, { done, t: Date.now() });
    this._notify();
    if (hass.states["script.kitchen_routine_set"]) return hass.callService("script", "kitchen_routine_set", { uid, done });
    return hass.callService("todo", "update_item", { item: uid, status: done ? "completed" : "needs_action" }, { entity_id: ROUTINES });
  },
  save(hass, r, uid) {
    const meta = { member: r.member, emoji: r.emoji, period: r.period, points: Number(r.points) || 0, days: r.days };
    if (r.points_by_day) meta.points_by_day = r.points_by_day;
    const description = JSON.stringify(meta);
    if (uid) return hass.callService("todo", "update_item", { item: uid, rename: r.title, description }, { entity_id: ROUTINES });
    return hass.callService("todo", "add_item", { item: r.title, description }, { entity_id: ROUTINES });
  },
  remove(hass, uid) {
    return hass.callService("todo", "remove_item", { item: uid }, { entity_id: ROUTINES });
  },
};

// ----------------------------------------------------------------- sheet

// A bottom-sheet dialog on its own layer (the routine editor, the theme
// picker). `render(state)` returns the body HTML; `onAction(action, data,
// sheet)` handles taps on [data-action]; sheet.update() redraws (morphed, so
// inputs keep their text); sheet.close() animates it away.
const SHEET_CSS = `
  .bd { position:fixed; inset:0; background:rgba(0,0,0,.55); animation:kfade .2s ease-out both; }
  .sh { position:fixed; left:50%; top:7vh; width:min(660px,94vw); max-height:84vh; overflow:auto; transform:translateX(-50%);
    background:var(--card); border-radius:24px; padding:20px; box-shadow:0 18px 50px rgba(0,0,0,.6); animation:shin .26s cubic-bezier(.2,.9,.3,1.1) both; }
  @keyframes shin { from { opacity:0; transform:translate(-50%,24px) scale(.97); } to { opacity:1; transform:translate(-50%,0); } }
  .closing .bd { animation:kfade .18s reverse both; }
  .closing .sh { animation:shin .18s reverse both; }
  .tt { font-size:22px; font-weight:500; margin-bottom:14px; display:flex; align-items:center; gap:10px; }
  .lb { font-size:13px; color:var(--sub); text-transform:uppercase; letter-spacing:.06em; margin:14px 0 8px; }
  input.tx { width:100%; background:var(--card2); border:1px solid var(--line); border-radius:14px; color:var(--txt); font:inherit; font-size:18px; padding:14px 16px; outline:none; }
  input.tx:focus { border-color:var(--accent); }
  .chips { display:flex; flex-wrap:wrap; gap:8px; }
  .chips button { padding:10px 14px; border-radius:14px; background:var(--card2); font-size:15px; color:var(--sub); display:flex; align-items:center; gap:8px; }
  .chips button.on { background:var(--accent); color:var(--ink); font-weight:500; }
  .chips button .av { width:24px; height:24px; font-size:12px; }
  .emo { display:grid; grid-template-columns:repeat(10,1fr); gap:6px; }
  .emo button { font-size:24px; height:46px; border-radius:12px; background:var(--card2); display:grid; place-items:center; }
  .emo button.on { background:var(--accent); }
  .step { display:flex; align-items:center; gap:14px; }
  .step button { width:46px; height:46px; border-radius:14px; background:var(--card2); font-size:24px; display:grid; place-items:center; }
  .step b { font-size:24px; min-width:70px; text-align:center; }
  .hint { color:var(--sub); font-size:13px; margin-top:6px; }
  .acts { display:flex; gap:10px; margin-top:22px; }
  .acts button { padding:14px 20px; border-radius:14px; background:var(--card2); font-size:16px; font-weight:500; }
  .acts .go { background:var(--accent); color:var(--ink); margin-left:auto; }
  .acts .go[disabled] { opacity:.4; }
  .acts .del { color:#ff8a80; }
  .acts .del.arm { background:#c62828; color:#fff; }
  .grid { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
  .grid button { background:var(--card2); border-radius:16px; padding:16px 10px; text-align:center; font-size:15px; }
  .grid button span { display:block; font-size:34px; margin-bottom:6px; }
  .grid button.on { outline:3px solid var(--accent); }
`;
function openSheet({ render, onAction, onInput }) {
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;inset:0;z-index:9500";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>${BASE_CSS}${MOTION_CSS}${SHEET_CSS}</style><div class="wrap"><div class="bd" data-action="cancel"></div><div class="sh"></div></div>`;
  const sh = root.querySelector(".sh");
  const sheet = {
    root,
    update() {
      morph(sh, render());
    },
    close() {
      root.querySelector(".wrap").classList.add("closing");
      setTimeout(() => host.remove(), 180);
    },
  };
  root.addEventListener("click", (e) => {
    const el = e.composedPath().find((n) => n.dataset && n.dataset.action);
    if (!el || el.disabled) return;
    if (el.dataset.action === "cancel") return sheet.close();
    onAction(el.dataset.action, el.dataset, sheet, e);
  });
  root.addEventListener("pointerdown", ripple);
  root.addEventListener("input", (e) => onInput?.(e.target, sheet));
  document.body.appendChild(host);
  sheet.update();
  return sheet;
}
function ripple(e) {
  if (REDUCED()) return;
  const b = e.composedPath().find((n) => n.tagName === "BUTTON");
  if (!b) return;
  const r = b.getBoundingClientRect();
  const size = Math.max(r.width, r.height) * 2.2;
  const s = document.createElement("span");
  s.className = "rip";
  s.style.cssText = `width:${size}px;height:${size}px;left:${e.clientX - r.left - size / 2}px;top:${e.clientY - r.top - size / 2}px`;
  b.appendChild(s);
  setTimeout(() => s.remove(), 600);
}

const ROUTINE_EMOJI = ["🪥", "🛏️", "👕", "🥣", "🎒", "🌮", "🧹", "🧸", "📚", "📝", "🎹", "🎻", "🚿", "🛁", "🍽️", "🧺", "🌱", "🐱", "🗑️", "📬",
  "🏃", "🧘", "💧", "🍎", "🥦", "🦷", "💊", "🎨", "⚽", "🚲", "👟", "🧴", "🪴", "🐶", "🧼", "📖", "✏️", "🎵", "⭐", "✅"];
const fmtDays = (days) => {
  const s = new Set(days);
  if (s.size === 7) return "Every day";
  if (s.size === 5 && ["mon", "tue", "wed", "thu", "fri"].every((d) => s.has(d))) return "Weekdays";
  if (s.size === 2 && s.has("sat") && s.has("sun")) return "Weekends";
  return DAYS.filter((d) => s.has(d)).map((d) => d[0].toUpperCase() + d.slice(1, 3)).join(", ");
};
/** The add / edit routine dialog, like the Apolosign "New routine" screen. */
function routineEditor(hass, { member: who, routine }) {
  const st = routine
    ? { ...routine, days: [...routine.days] }
    : { title: "", member: who, emoji: "⭐", period: currentPeriod(), points: 1, days: [...ALL_DAYS], points_by_day: null };
  let armed = false;
  const sheet = openSheet({
    render() {
      const people = FAMILY.map((m) => `<button class="${st.member === m.key ? "on" : ""}" data-action="member" data-key="${m.key}"><div class="av" style="background:${m.color}">${esc(m.name[0])}</div>${esc(m.name)}</button>`).join("");
      const per = PERIODS.map((p) => `<button class="${st.period === p.key ? "on" : ""}" data-action="period" data-key="${p.key}">${p.icon} ${p.label}</button>`).join("");
      const days = DAYS.map((d) => `<button class="${st.days.includes(d) ? "on" : ""}" data-action="day" data-key="${d}">${d[0].toUpperCase()}${d.slice(1, 3)}</button>`).join("");
      const quick = [["Every day", ALL_DAYS], ["Weekdays", ["mon", "tue", "wed", "thu", "fri"]], ["Weekends", ["sun", "sat"]]]
        .map(([l, ds]) => `<button data-action="days" data-key="${ds.join(",")}">${l}</button>`).join("");
      const emo = ROUTINE_EMOJI.map((e) => `<button class="${st.emoji === e ? "on" : ""}" data-action="emoji" data-key="${e}">${e}</button>`).join("");
      const pbd = st.points_by_day ? Object.entries(st.points_by_day).map(([d, n]) => `${d[0].toUpperCase()}${d.slice(1, 3)} ${n}`).join(", ") : "";
      return `<div class="tt">${st.emoji} ${routine ? "Edit routine" : "New routine"}</div>
        <input class="tx" name="title" placeholder="What needs doing? (e.g. Feed the cat)" value="${esc(st.title)}" autocomplete="off" autocapitalize="sentences" enterkeyhint="done">
        <div class="lb">Who</div><div class="chips">${people}</div>
        <div class="lb">When</div><div class="chips">${per}</div>
        <div class="lb">Days</div><div class="chips">${days}</div><div class="chips" style="margin-top:8px">${quick}</div>
        <div class="lb">Stars</div><div class="step"><button data-action="pts" data-key="-1">−</button><b>⭐ ${st.points}</b><button data-action="pts" data-key="1">+</button></div>
        ${pbd ? `<div class="hint">Some days are worth more: ${esc(pbd)}. Changing the stars here replaces that.</div>` : ""}
        <div class="lb">Icon</div><div class="emo">${emo}</div>
        <div class="acts">${routine ? `<button class="del ${armed ? "arm" : ""}" data-action="delete">${armed ? "Tap again to delete" : "Delete"}</button>` : ""}
          <button data-action="cancel">Cancel</button><button class="go" data-action="save" ${st.title.trim() && st.days.length ? "" : "disabled"}>${routine ? "Save" : "Add routine"}</button></div>`;
    },
    onInput(t, s) {
      if (t.name === "title") {
        st.title = t.value;
        s.update();
      }
    },
    onAction(a, d, s) {
      if (a !== "delete") armed = false;
      if (a === "member") st.member = d.key;
      else if (a === "period") st.period = d.key;
      else if (a === "day") st.days = st.days.includes(d.key) ? st.days.filter((x) => x !== d.key) : DAYS.filter((x) => x === d.key || st.days.includes(x));
      else if (a === "days") st.days = d.key.split(",");
      else if (a === "emoji") st.emoji = d.key;
      else if (a === "pts") {
        st.points = Math.max(0, Math.min(20, st.points + Number(d.key)));
        st.points_by_day = null;
      } else if (a === "delete") {
        if (!armed) armed = true;
        else {
          Routines.remove(hass, routine.uid);
          return s.close();
        }
      } else if (a === "save") {
        if (!st.title.trim()) return;
        Routines.save(hass, { ...st, title: st.title.trim() }, routine?.uid);
        return s.close();
      }
      s.update();
    },
  });
  if (!routine) setTimeout(() => sheet.root.querySelector("input.tx")?.focus(), 250);
  return sheet;
}
/** The theme picker (🎨 next to the tabs). */
function themePicker(hass) {
  const sheet = openSheet({
    render() {
      const cur = Theme.key;
      const opts = [["default", DEFAULT_THEME], ...Object.entries(THEMES)];
      return `<div class="tt">🎨 Display theme</div><div class="grid">${opts
        .map(([k, t]) => `<button class="${cur === k ? "on" : ""}" data-action="pick" data-key="${k}"><span>${t.emoji}</span>${esc(t.name)}</button>`)
        .join("")}</div><div class="hint" style="margin-top:12px">Holiday themes also offer themselves on the Home tab when one is coming up, and switch off a few days after.</div>
        <div class="acts"><button class="go" data-action="cancel">Done</button></div>`;
    },
    onAction(a, d, s) {
      if (a !== "pick") return;
      hass.callService("input_select", "select_option", { entity_id: THEME_ENTITY, option: d.key });
      Theme.key = null; // repaint even if HA is slow to answer
      Theme.apply({ ...hass, states: { ...hass.states, [THEME_ENTITY]: { state: d.key } } });
      s.update();
    },
  });
  return sheet;
}

// ------------------------------------------------------------- base class

const BASE_CSS = `
  :host { display:block; --bg:var(--k-bg,#111318); --card:var(--k-card,#1c1f26); --card2:var(--k-card2,#252932); --line:var(--k-line,#2e333d);
    --txt:var(--k-txt,#e8eaed); --sub:var(--k-sub,#9aa0aa); --accent:var(--k-accent,#ff9f43); --ink:var(--k-ink,#1b1206); --ok:var(--k-ok,#4cd17a);
    color:var(--txt); font-family:Roboto,"Noto Sans",sans-serif; }
  * { box-sizing:border-box; }
  .card { background:var(--card); border-radius:18px; padding:14px; height:100%; transition:background-color .4s; }
  .h { font-size:13px; font-weight:500; color:var(--sub); letter-spacing:.06em; text-transform:uppercase;
    margin-bottom:10px; display:flex; justify-content:space-between; align-items:center; }
  .h b { color:var(--txt); font-weight:500; text-transform:none; letter-spacing:0; }
  .av { width:34px; height:34px; border-radius:50%; display:grid; place-items:center; font-weight:700;
    font-size:15px; color:#111; position:relative; flex:none; }
  .muted { color:var(--sub); }
  .empty { color:var(--sub); font-size:14px; padding:6px 0; }
  button { all:unset; box-sizing:border-box; cursor:pointer; -webkit-tap-highlight-color:transparent; }
`;

class KitchenCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._raf = null;
    this.shadowRoot.addEventListener("click", (e) => this._click(e));
    this.shadowRoot.addEventListener("pointerdown", (e) => {
      ripple(e);
      this._pressStart(e);
    });
    for (const t of ["pointerup", "pointercancel", "pointerleave"]) this.shadowRoot.addEventListener(t, () => clearTimeout(this._press));
    this._onShow = () => this._dirty && this._queue(true);
    this._onRoutines = () => this._queue();
  }
  setConfig(config) {
    this.config = config || {};
    this._queue(!!this._rendered);
  }
  set hass(h) {
    syncFamily(h);
    watchVersion(h);
    Theme.apply(h);
    const first = !this._hass;
    this._hass = h;
    if (first) this.firstHass?.();
    if (this.shouldRender ? this.shouldRender(h) : true) this._queue();
  }
  get hass() {
    return this._hass;
  }
  connectedCallback() {
    this._tick = setInterval(() => this._queue(), this.tickMs || 30000);
    window.addEventListener("kitchen-page-show", this._onShow);
    window.addEventListener("kitchen-routines", this._onRoutines);
    this.connected?.();
    if (this._dirty) this._queue(true);
  }
  disconnectedCallback() {
    clearInterval(this._tick);
    window.removeEventListener("kitchen-page-show", this._onShow);
    window.removeEventListener("kitchen-routines", this._onRoutines);
    this.disconnected?.();
  }
  // Visible means "its page is showing", judged by the parent: a card that
  // hides itself (notes with hide_missing, the holiday banner) must still
  // render, or it could never show itself again.
  _visible() {
    if (!this.isConnected) return false;
    const p = this.parentNode?.nodeType === 1 ? this.parentNode : this;
    return p.checkVisibility ? p.checkVisibility() : p.offsetParent !== null || p === document.body;
  }
  // hass is replaced on every state change in the house (Sense alone updates
  // several times a second), so renders are coalesced to at most ~4 a second.
  // The first render, a tap and a page coming into view render right away.
  // A card on a hidden page only marks itself dirty and catches up when its
  // page is shown, so the five tabs cost what one does.
  _queue(now) {
    if (now) {
      clearTimeout(this._raf);
      this._raf = null;
      return this._renderNow();
    }
    if (this._raf) return;
    this._raf = setTimeout(() => {
      this._raf = null;
      this._renderNow();
    }, this._rendered ? 250 : 0);
  }
  _renderNow() {
    if (!this._hass || !this.config) return;
    if (this._rendered && !this._visible()) {
      this._dirty = true;
      return;
    }
    this._dirty = false;
    this._rendered = true;
    if (this.paint) return this._guard(() => this.paint());
    let body;
    try {
      body = this.render();
    } catch (err) {
      body = this._errorHtml(err);
    }
    if (body === this._last) return;
    this._last = body;
    if (!this._body) {
      this.shadowRoot.innerHTML = `<style>${BASE_CSS}${MOTION_CSS}${this.css || ""}</style><div class="kroot"></div>`;
      this._body = this.shadowRoot.querySelector(".kroot");
    }
    morph(this._body, body);
    this.rendered?.();
  }
  // One broken card shows a small error box; the rest of the view keeps working.
  _errorHtml(err) {
    console.error(`${this.localName}:`, err);
    return `<div class="card"><div class="empty">⚠️ This card hit a problem: ${esc(err?.message || err)}</div></div>`;
  }
  _guard(fn) {
    try {
      fn();
    } catch (err) {
      this.shadowRoot.innerHTML = `<style>${BASE_CSS}</style>${this._errorHtml(err)}`;
      this._built = false;
      this._body = null;
    }
  }
  // Long-press a routine to edit it.
  _pressStart(e) {
    const el = e.composedPath().find((n) => n.dataset && n.dataset.chore);
    if (!el || !Routines.active(this._hass)) return;
    clearTimeout(this._press);
    this._press = setTimeout(() => {
      this._swallow = Date.now();
      const r = Routines.all().find((x) => x.uid === el.dataset.chore);
      if (r) routineEditor(this._hass, { routine: r });
    }, 600);
  }
  _click(e) {
    if (this._swallow && Date.now() - this._swallow < 800) return;
    const el = e.composedPath().find((n) => n.dataset && (n.dataset.chore || n.dataset.toggle || n.dataset.action));
    if (!el) return;
    if (el.dataset.chore) {
      const done = el.dataset.on !== "1";
      el.classList.add(done ? "checking" : "pending");
      if (done) {
        const tick = el.querySelector(".tick, .chk") || el;
        const [x, y] = FX.at(tick);
        FX.burst(x, y, getComputedStyle(this).getPropertyValue("--ok").trim() || "#4cd17a");
        const pts = Number(el.dataset.pts || 0);
        if (pts) FX.float(x, y - 10, `+${pts} ⭐`);
      }
      setChore(this._hass, el.dataset.chore, done);
    } else if (el.dataset.toggle) {
      const on = el.dataset.on === "1";
      el.classList.add("pending");
      this._hass.callService("input_boolean", on ? "turn_off" : "turn_on", { entity_id: el.dataset.toggle });
    } else {
      this.action?.(el.dataset.action, el.dataset, e, el);
    }
  }
  /** Confetti over this card when `key` (e.g. "all done") turns true; not on first render. */
  _celebrate(key, now) {
    this._cel = this._cel || {};
    const before = this._cel[key];
    this._cel[key] = now;
    if (now && before === false) requestAnimationFrame(() => FX.confetti(this.getBoundingClientRect()));
  }
  getCardSize() {
    return 4;
  }
}

// -------------------------------------------------------------- header

class HeaderCard extends KitchenCard {
  tickMs = 10000;
  firstHass() {
    this._subscribeForecast();
  }
  disconnected() {
    this._unsub?.then((u) => u()).catch(() => {});
    this._unsub = null;
  }
  connected() {
    if (this._hass && !this._unsub) this._subscribeForecast();
  }
  _subscribeForecast() {
    const e = this.config?.weather || "weather.forecast_home";
    if (!this._hass.states[e] || this._unsub) return;
    this._unsub = this._hass.connection
      .subscribeMessage((m) => {
        this._forecast = m.forecast;
        this._queue();
      }, { type: "weather/subscribe_forecast", forecast_type: "daily", entity_id: e })
      .catch(() => null);
  }
  css = `
    .top { display:flex; justify-content:space-between; align-items:flex-start; padding:4px 6px 0; }
    .clock { font-size:64px; line-height:1; letter-spacing:-.02em; }
    .clock small { font-size:24px; color:var(--sub); margin-left:6px; }
    .date { font-size:20px; color:var(--sub); margin-top:6px; }
    .wx { text-align:right; }
    .wx .t { font-size:40px; line-height:1; }
    .wx .d { font-size:15px; color:var(--sub); margin-top:4px; }
    .people { display:flex; gap:8px; justify-content:flex-end; margin-top:10px; }
    .home::after { content:""; position:absolute; right:-2px; bottom:-2px; width:11px; height:11px; border-radius:50%;
      background:var(--ok); border:2px solid var(--bg); }
    .away { opacity:.45; }
    :host([compact]) .clock { font-size:44px; }
    .greet { margin:12px 6px 0; font-size:17px; min-height:24px; }
    .greet span { color:var(--sub); }
    .offer { margin:8px 0 0; display:flex; align-items:center; gap:10px; background:var(--card2); border-radius:14px; padding:3px 3px 3px 12px; font-size:16px;
      animation:kin .4s ease-out both; }
    .offer .e { font-size:24px; display:inline-block; animation:kpop .6s ease-out both, hbob 2.4s ease-in-out .6s infinite alternate; }
    @keyframes hbob { to { transform:translateY(-3px) rotate(8deg); } }
    .offer .t { flex:1; }
    .offer .t span { color:var(--sub); }
    .offer button { padding:7px 12px; border-radius:12px; font-size:14px; font-weight:500; background:var(--card); }
    .offer button.go { background:var(--hol); color:#140c05; }
  `;
  shouldRender() {
    if (this.config.compact) return true;
    const cals = this.config.calendars || ["calendar.family"];
    const s = startOfDay();
    if (this._evKey !== s.toDateString() || Date.now() - (this._evAt || 0) > 5 * 60 * 1000) {
      this._evKey = s.toDateString();
      this._evAt = Date.now();
      fetchEvents(this._hass, cals, s, addDays(s, 1)).then((e) => {
        this._events = e;
        this._queue();
      });
    }
    return true;
  }
  // "Good evening! 7 chores left · 2 more events today · 5 to buy", like the Apolosign home card.
  _greeting(now) {
    const h = now.getHours();
    const hol = holidayNow(now);
    const th = Theme.current();
    const hi = th.greet && hol && hol.key === Theme.key && hol.days === 0
      ? `${th.greet} ${th.emoji}`
      : `${h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"}!`;
    const parts = [];
    let due = 0;
    let left = 0;
    for (const m of FAMILY) {
      for (const c of chores(this._hass, m.key)) {
        if (!c.due) continue;
        due++;
        if (!c.done) left++;
      }
    }
    if (due) parts.push(left ? `${left} chore${left === 1 ? "" : "s"} left` : "all chores done 🎉");
    if (this._events) {
      const n = this._events.filter((e) => e.allDay || e.end > now).length;
      if (n) parts.push(`${n} ${n === this._events.length ? "" : "more "}event${n === 1 ? "" : "s"} today`);
    }
    const todo = this._hass.states[this.config.list || "todo.shopping_list"];
    const k = Number(todo?.state);
    if (k > 0) parts.push(`${k} to buy`);
    return `<div class="greet">${hi}${parts.length ? ` <span>${parts.join(" · ")}</span>` : ""}</div>`;
  }
  action(a) {
    if (a === "hol-on" || a === "hol-no") answerHoliday(this._hass, a === "hol-on", this.getBoundingClientRect());
    this._queue(true);
  }
  render() {
    const now = new Date();
    let h = now.getHours();
    const ap = h >= 12 ? "PM" : "AM";
    h = h % 12 || 12;
    const w = this._hass.states[this.config.weather || "weather.forecast_home"];
    const f = this._forecast?.[0];
    const temp = w ? Math.round(w.attributes.temperature) : null;
    const people = FAMILY.map((m) => {
      const t = m.tracker && this._hass.states[m.tracker];
      const cls = t ? (t.state === "home" ? "home" : "away") : "";
      return `<div class="av ${cls}" style="background:${m.color}">${m.name[0]}</div>`;
    }).join("");
    const compact = this.config.compact;
    return `<div class="top">
      <div>
        <div class="clock" style="${compact ? "font-size:44px" : ""}">${h}:${String(now.getMinutes()).padStart(2, "0")}<small>${ap}</small></div>
        <div class="date" style="${compact ? "font-size:16px" : ""}">${now.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}</div>
      </div>
      <div class="wx">
        ${w ? `<div class="t" style="${compact ? "font-size:30px" : ""}">${WEATHER_ICON[w.state] || ""} ${temp}°</div>` : ""}
        ${!compact && w ? `<div class="d">${WEATHER_TEXT[w.state] || w.state}${f ? ` · H ${Math.round(f.temperature)}° L ${Math.round(f.templow)}°` : ""}</div>` : ""}
        ${compact ? "" : `<div class="people">${people}</div>`}
      </div>
    </div>${compact ? "" : this._offerOrGreeting(now)}`;
  }
  // While a holiday is coming up and its theme is off, the greeting line asks about it instead.
  _offerOrGreeting(now) {
    const o = this.config.holiday_offer === false ? null : holidayOffer(this._hass);
    if (!o) return this._greeting(now);
    return `<div class="offer" data-k="offer" style="--hol:${o.theme.vars.accent}"><span class="e">${o.theme.emoji}</span>
      <div class="t">${esc(o.text)} <span>Dress up the display?</span></div>
      <button data-action="hol-no">Not this year</button><button class="go" data-action="hol-on">Turn it on</button></div>`;
  }
}

// ---------------------------------------------------------------- tabs

class TabsCard extends KitchenCard {
  connected() {
    this._loc = () => this._queue();
    window.addEventListener("location-changed", this._loc);
    window.addEventListener("popstate", this._loc);
  }
  disconnected() {
    window.removeEventListener("location-changed", this._loc);
    window.removeEventListener("popstate", this._loc);
  }
  css = `
    .tabs { display:flex; gap:8px; padding:0 2px; flex-wrap:wrap; }
    .tab { padding:9px 16px; border-radius:20px; background:var(--card); color:var(--sub); font-size:15px; font-weight:500; }
    .tab.on { background:var(--accent); color:var(--ink); }
  `;
  action(a, d) {
    if (a !== "go") return;
    const base = location.pathname.split("/")[1];
    history.pushState(null, "", `/${base}/${d.path}${location.search}`);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
    this._queue(true);
  }
  render() {
    const cur = location.pathname.split("/")[2] || "";
    return `<div class="tabs">${(this.config.views || [])
      .map((v) => `<button class="tab ${v.path === cur ? "on" : ""}" data-action="go" data-path="${esc(v.path)}">${esc(v.title)}</button>`)
      .join("")}</div>`;
  }
}

// ------------------------------------------------------- calendar views

const CAL_CSS = `
  .colhs { display:grid; grid-template-columns:44px repeat(var(--cols),minmax(0,1fr)); }
  .colh { display:flex; align-items:center; gap:6px; font-size:14px; font-weight:500; padding:0 4px 8px; min-width:0; white-space:nowrap; overflow:hidden; }
  .colh .av { width:24px; height:24px; font-size:11px; }
  .colh.day { flex-direction:column; gap:2px; font-size:12px; color:var(--sub); font-weight:400; padding:0 0 6px; }
  .colh.day b { font-size:16px; color:var(--txt); font-weight:500; width:30px; height:30px; display:grid; place-items:center; border-radius:50%; }
  .colh.day.td b { background:var(--accent); color:var(--ink); }
  .allday { display:flex; flex-wrap:wrap; gap:6px; margin:0 0 8px 44px; }
  .adg { display:grid; grid-template-columns:repeat(var(--cols),minmax(0,1fr)); gap:3px; margin:0 0 6px 44px; }
  .adg > div { display:flex; flex-direction:column; gap:3px; min-width:0; }
  .chip { border-radius:9px; padding:4px 8px; font-size:12.5px; color:#111; font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .adg .chip { font-size:11px; padding:3px 5px; border-radius:6px; }
  .grid { position:relative; }
  .hl { position:absolute; left:44px; right:0; border-top:1px solid var(--line); opacity:.6; }
  .hr { position:absolute; left:0; width:38px; text-align:right; font-size:11px; color:var(--sub); transform:translateY(-50%); }
  .lane { position:absolute; top:0; bottom:0; border-left:1px solid var(--line); }
  .ev { position:absolute; border-radius:9px; padding:5px 7px; font-size:12.5px; line-height:1.25; color:#111;
    overflow:hidden; font-weight:500; }
  .ev i { display:block; font-style:normal; font-weight:400; font-size:11px; opacity:.75; margin-top:2px; }
  .ev.short { white-space:nowrap; text-overflow:ellipsis; padding:3px 7px; }
  .ev.narrow { font-size:11px; padding:3px 4px; border-radius:6px; }
  .ev.span { opacity:.55; }
  .ev.past { opacity:.38; }
  .now { position:absolute; border-top:2px solid #ff4d4d; }
  .now::before { content:""; position:absolute; left:-4px; top:-5px; width:8px; height:8px; border-radius:50%; background:#ff4d4d; }
  .up { display:grid; grid-template-columns:86px 1fr auto; gap:7px 10px; font-size:14.5px; align-items:center; }
  .up .d { color:var(--sub); font-size:13px; }
  .dots { display:flex; gap:3px; } .dots span { width:10px; height:10px; border-radius:50%; }
  .day { font-size:15px; font-weight:500; margin:14px 0 6px; color:var(--accent); }
  .day:first-child { margin-top:0; }
  .row { display:flex; gap:12px; align-items:center; background:var(--card2); border-radius:12px; padding:10px 12px; margin-bottom:6px; }
  .row .bar { width:5px; align-self:stretch; border-radius:3px; }
  .row .tm { width:96px; color:var(--sub); font-size:13px; flex:none; }
  .row .t { font-size:15px; flex:1; }
`;

/** Events overlapping a day (all-day ends are exclusive). */
const onDay = (e, d) => e.start < addDays(d, 1) && e.end > d && !(e.allDay && e.end.getTime() === d.getTime());

/**
 * A time grid: hour lines down the left, `ncols` lanes, timed events placed by
 * start/end. colsOf(e) gives an event's lanes (null spans them all), dayOf(i)
 * the day lane i shows, nowCol the lane for the red now line (-1 for none,
 * null for all of them). Overlapping events in a lane split it side by side.
 */
function timeGridHtml(timed, { H, sh, eh, ncols, colsOf, dayOf, nowCol, narrow }) {
  const ph = H / (eh - sh);
  const now = new Date();
  const y = (d, day0) => Math.max(0, Math.min(H, ((d - day0) / 3600000 - sh) * ph));
  const colL = (i) => `calc(44px + (100% - 44px) * ${i} / ${ncols})`;
  const colW = (n) => `calc((100% - 44px) * ${n} / ${ncols})`;
  let html = "";
  for (let hr = sh; hr <= eh; hr += 2) {
    const t = (hr - sh) * ph;
    html += `<div class="hl" style="top:${t}px"></div><div class="hr" style="top:${t}px">${hr % 12 || 12}${hr >= 12 ? "p" : "a"}</div>`;
  }
  for (let i = 0; i < ncols; i++) html += `<div class="lane" style="left:${colL(i)}"></div>`;
  const placed = timed.map((e) => ({ e, cols: colsOf(e) }));
  const box = (e, day0) => {
    let top = y(e.start, day0);
    const hgt = Math.max(22, y(e.end, day0) - top);
    top = Math.min(top, H - hgt); // before/after the grid's hours: pin to the edge, don't hide it
    return { top, hgt };
  };
  const label = (e, hgt) => `${esc(e.title)}${hgt > 34 && !narrow ? `<i>${fmtTime(e.start)} – ${fmtTime(e.end)}</i>` : ""}`;
  const cls = (e, hgt) => (e.end < now ? " past" : "") + (hgt <= 34 ? " short" : "") + (narrow ? " narrow" : "");
  for (const { e, cols } of placed) {
    if (!cols) {
      const { top, hgt } = box(e, dayOf(0));
      html += `<div class="ev span${cls(e, hgt)}" style="top:${top}px;height:${hgt}px;left:calc(${colL(0)} + 4px);width:calc(${colW(ncols)} - 8px);background:${eventColor(e)}">${label(e, hgt)}</div>`;
      continue;
    }
    for (const c of cols) {
      const { top, hgt } = box(e, dayOf(c));
      const same = placed.filter((o) => o.e !== e && o.cols?.includes(c) && o.e.start < e.end && e.start < o.e.end).map((o) => o.e);
      const idx = [e, ...same].sort((a, b) => a.start - b.start || a.title.localeCompare(b.title)).indexOf(e);
      const n = same.length + 1;
      const pad = narrow ? 2 : 4;
      html += `<div class="ev${cls(e, hgt)}" style="top:${top}px;height:${hgt}px;left:calc(${colL(c)} + ${pad}px + (${colW(1)} - ${2 * pad}px) * ${idx} / ${n});width:calc((${colW(1)} - ${2 * pad}px) / ${n} - 2px);background:${eventColor(e)}">${label(e, hgt)}</div>`;
    }
  }
  const nh = now.getHours() + now.getMinutes() / 60;
  if (nowCol !== -1 && nh >= sh && nh <= eh) {
    const l = nowCol == null ? "40px" : colL(nowCol);
    const w = nowCol == null ? "calc(100% - 40px)" : colW(1);
    html += `<div class="now" style="top:${(nh - sh) * ph}px;left:${l};width:${w}"></div>`;
  }
  return `<div class="grid" style="height:${H}px">${html}</div>`;
}

/** One day, one column per person. Events naming nobody span every column. */
function familyDayHtml(evs, day, H, sh, eh) {
  const isToday = day.getTime() === startOfDay().getTime();
  const allDay = evs.filter((e) => e.allDay);
  const timed = evs.filter((e) => !e.allDay);
  const colhs = `<div class="colhs" style="--cols:${FAMILY.length}"><div></div>${FAMILY.map((m) => `<div class="colh"><div class="av" style="background:${m.color}">${m.name[0]}</div>${esc(m.name)}</div>`).join("")}</div>`;
  const chips = allDay.length ? `<div class="allday">${allDay.map((e) => `<div class="chip" style="background:${eventColor(e)}">${esc(e.title)}</div>`).join("")}</div>` : "";
  const grid = timeGridHtml(timed, {
    H, sh, eh, ncols: FAMILY.length,
    colsOf: (e) => (e.who.length ? e.who.map((k) => FAMILY.findIndex((m) => m.key === k)) : null),
    dayOf: () => day,
    nowCol: isToday ? null : -1,
  });
  return colhs + chips + grid;
}

/** Sun-Sat, one column per day. */
function weekHtml(evs, wk0, H, sh, eh) {
  const days = [...Array(7)].map((_, i) => addDays(wk0, i));
  const td = startOfDay().getTime();
  const ti = days.findIndex((d) => d.getTime() === td);
  const colhs = `<div class="colhs" style="--cols:7"><div></div>${days
    .map((d, i) => `<button class="colh day ${i === ti ? "td" : ""}" data-action="day" data-date="${d.toISOString()}">${fmtDay(d)}<b>${d.getDate()}</b></button>`)
    .join("")}</div>`;
  const allDay = evs.filter((e) => e.allDay);
  let adg = "";
  if (allDay.length) {
    adg = `<div class="adg" style="--cols:7">${days
      .map((d) => {
        const on = allDay.filter((e) => onDay(e, d));
        return `<div>${on.slice(0, 2).map((e) => `<div class="chip" style="background:${eventColor(e)}">${esc(e.title)}</div>`).join("")}${on.length > 2 ? `<div class="muted" style="font-size:11px">+${on.length - 2}</div>` : ""}</div>`;
      })
      .join("")}</div>`;
  }
  const grid = timeGridHtml(evs.filter((e) => !e.allDay), {
    H, sh, eh, ncols: 7, narrow: true,
    colsOf: (e) => days.map((d, i) => (onDay(e, d) ? i : -1)).filter((i) => i >= 0),
    dayOf: (i) => days[i],
    nowCol: ti,
  });
  return colhs + adg + grid;
}

const MONTH_CSS = `
  .mh { display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); font-size:12px; color:var(--sub); text-align:center; margin-bottom:4px; }
  .mg { display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:3px; }
  .mc { background:var(--card2); border-radius:8px; padding:4px 3px; display:flex; flex-direction:column; gap:2px; min-width:0; overflow:hidden; }
  .mc .n { font-size:13px; text-align:center; width:24px; height:24px; line-height:24px; border-radius:50%; align-self:center; flex:none; }
  .mc.td .n { background:var(--accent); color:var(--ink); font-weight:500; }
  .mc.om { opacity:.4; }
  .mc .chip { font-size:10.5px; padding:2px 4px; border-radius:5px; }
  .mc .more { font-size:10.5px; color:var(--sub); text-align:center; }
`;

/** The whole month, Sunday-first weeks, up to `max` chips a day. */
function monthHtml(evs, anchor, rowH) {
  const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const g0 = startOfWeek(first);
  const dim = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0).getDate();
  const weeks = Math.ceil((first.getDay() + dim) / 7);
  const td = startOfDay().getTime();
  const max = Math.max(1, Math.floor((rowH - 34) / 19));
  let cells = "";
  for (let i = 0; i < weeks * 7; i++) {
    const d = addDays(g0, i);
    const on = evs.filter((e) => onDay(e, d)).sort((a, b) => b.allDay - a.allDay || a.start - b.start);
    const shown = on.length > max ? on.slice(0, max - 1) : on;
    cells += `<button class="mc ${d.getMonth() !== anchor.getMonth() ? "om" : ""} ${d.getTime() === td ? "td" : ""}" style="height:${rowH}px" data-action="day" data-date="${d.toISOString()}">
      <div class="n">${d.getDate()}</div>${shown.map((e) => `<div class="chip" style="background:${eventColor(e)}">${esc(e.title)}</div>`).join("")}${on.length > shown.length ? `<div class="more">+${on.length - shown.length} more</div>` : ""}</button>`;
  }
  return `<div class="mh">${DAYS.map((d) => `<div>${d[0].toUpperCase()}${d.slice(1)}</div>`).join("")}</div><div class="mg">${cells}</div>`;
}

const whoDots = (e) =>
  `<div class="dots">${(e.who.length ? e.who : FAMILY.map((m) => m.key)).map((k) => `<span style="background:${MEMBER[k].color}"></span>`).join("")}</div>`;

/** Events grouped under a heading per day. */
function agendaHtml(evs) {
  let out = "";
  let last = "";
  for (const e of evs) {
    const d = e.start.toDateString();
    if (d !== last) {
      last = d;
      const isToday = d === new Date().toDateString();
      out += `<div class="day">${isToday ? "Today" : e.start.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" })}</div>`;
    }
    out += `<div class="row"><div class="bar" style="background:${eventColor(e)}"></div><div class="tm">${e.allDay ? "All day" : `${fmtTime(e.start)}`}</div><div class="t">${esc(e.title)}</div>${whoDots(e)}</div>`;
  }
  return out;
}

const NOT_CONNECTED = "No calendar connected yet. Add the family Google calendar in Home Assistant and it shows up here.";

class FamilyDayCard extends KitchenCard {
  tickMs = 60000;
  css = CAL_CSS;
  shouldRender() {
    this._load();
    return true;
  }
  _load() {
    const s = startOfDay();
    const key = s.toDateString();
    if (this._loadedKey === key && Date.now() - this._loadedAt < 5 * 60 * 1000) return;
    this._loadedKey = key;
    this._loadedAt = Date.now();
    fetchEvents(this._hass, this.config.calendars || [], s, addDays(s, 1)).then((evs) => {
      this._events = evs;
      this._queue();
    });
  }
  render() {
    const cals = (this.config.calendars || []).filter((e) => this._hass.states[e]);
    const head = `<div class="h">Today <b>${this._events ? `${this._events.length} event${this._events.length === 1 ? "" : "s"}` : ""}</b></div>`;
    if (!cals.length) return `<div class="card">${head}<div class="empty">${NOT_CONNECTED}</div></div>`;
    const c = this.config;
    return `<div class="card">${head}${familyDayHtml(this._events || [], startOfDay(), c.height || 392, c.start_hour ?? 7, c.end_hour ?? 21)}</div>`;
  }
}

class AgendaCard extends KitchenCard {
  tickMs = 60000;
  css = CAL_CSS;
  shouldRender() {
    const s = startOfDay();
    const from = this.config.from_tomorrow ? addDays(s, 1) : s;
    const key = from.toDateString();
    if (this._key !== key || Date.now() - (this._at || 0) > 5 * 60 * 1000) {
      this._key = key;
      this._at = Date.now();
      fetchEvents(this._hass, this.config.calendars || [], from, addDays(from, this.config.days || 3)).then((e) => {
        this._events = e;
        this._queue();
      });
    }
    return true;
  }
  render() {
    const cals = (this.config.calendars || []).filter((e) => this._hass.states[e]);
    const title = this.config.title || "Coming up";
    if (!cals.length) return `<div class="card"><div class="h">${esc(title)}</div><div class="empty">No calendar connected yet.</div></div>`;
    const evs = (this._events || []).slice(0, this.config.max || 999);
    if (this.config.style === "list") {
      if (!evs.length) return `<div class="card"><div class="h">${esc(title)}</div><div class="empty">Nothing coming up.</div></div>`;
      return `<div class="card"><div class="h">${esc(title)}</div><div class="up">${evs
        .map((e) => `<div class="d">${fmtDay(e.start)} ${e.allDay ? "" : fmtTime(e.start)}</div><div>${esc(e.title)}</div>${whoDots(e)}`)
        .join("")}</div></div>`;
    }
    return `<div class="card"><div class="h">${esc(title)}</div>${agendaHtml(evs) || '<div class="empty">Nothing on the calendar.</div>'}</div>`;
  }
}

/**
 * The Calendar tab: Family (a day, one column per person), Week, Month and
 * List (two weeks), with back/forward, Today, and a person filter. Goes back
 * to today's default view after 10 minutes without a tap.
 */
const CAL_MODES = [
  { key: "family", label: "Family" },
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
  { key: "list", label: "List" },
];
class CalendarCard extends KitchenCard {
  tickMs = 60000;
  css = `${CAL_CSS}${MONTH_CSS}
    .ctl { display:flex; align-items:center; gap:8px; margin-bottom:10px; }
    .seg { display:flex; background:var(--card2); border-radius:12px; padding:3px; }
    .seg button { padding:8px 12px; border-radius:10px; font-size:14px; color:var(--sub); }
    .seg button.on { background:var(--accent); color:var(--ink); font-weight:500; }
    .nav { margin-left:auto; display:flex; align-items:center; gap:4px; }
    .nav button { min-width:36px; height:36px; border-radius:10px; background:var(--card2); display:grid; place-items:center; font-size:18px; padding:0 10px; }
    .nav .lbl { font-size:14px; font-weight:500; min-width:110px; text-align:center; }
    .nav .tdb { font-size:13px; color:var(--sub); }
    .who { display:flex; gap:6px; margin-bottom:10px; flex-wrap:wrap; }
    .who button { display:flex; align-items:center; gap:6px; padding:4px 10px 4px 4px; border-radius:16px; background:var(--card2); font-size:13px; }
    .who button .av { width:24px; height:24px; font-size:11px; }
    .who button.off { opacity:.4; }
  `;
  _reset() {
    this._mode = this.config.mode || "family";
    this._anchor = startOfDay();
    this._who = null;
  }
  _range() {
    const a = this._anchor;
    if (this._mode === "week") return [startOfWeek(a), addDays(startOfWeek(a), 7)];
    if (this._mode === "month") {
      const g0 = startOfWeek(new Date(a.getFullYear(), a.getMonth(), 1));
      return [g0, addDays(g0, 42)];
    }
    if (this._mode === "list") return [a, addDays(a, 14)];
    return [a, addDays(a, 1)];
  }
  shouldRender() {
    if (!this._mode || Date.now() - (this._touched || 0) > 10 * 60 * 1000) {
      const stale = this._mode && (this._mode !== (this.config.mode || "family") || this._anchor.getTime() !== startOfDay().getTime() || this._who);
      if (!this._mode || stale) this._reset();
    }
    this._load();
    return true;
  }
  _load() {
    const [s, e] = this._range();
    const key = `${s.toISOString()}|${e.toISOString()}`;
    if (this._key === key && Date.now() - (this._at || 0) < 5 * 60 * 1000) return;
    this._key = key;
    this._at = Date.now();
    fetchEvents(this._hass, this.config.calendars || [], s, e).then((evs) => {
      if (this._key !== key) return;
      this._events = evs;
      this._queue();
    });
  }
  action(a, d) {
    this._touched = Date.now();
    const before = this._range().join();
    const m = this._mode;
    const step = (n) => {
      const x = this._anchor;
      if (m === "month") this._anchor = new Date(x.getFullYear(), x.getMonth() + n, 1);
      else this._anchor = addDays(x, n * (m === "week" ? 7 : m === "list" ? 14 : 1));
    };
    if (a === "mode") {
      this._mode = d.mode;
      if (d.mode !== "month") this._anchor = startOfDay(this._anchor.getMonth() === new Date().getMonth() ? new Date() : this._anchor);
    } else if (a === "prev") step(-1);
    else if (a === "next") step(1);
    else if (a === "today") this._anchor = startOfDay();
    else if (a === "who") this._who = this._who === d.key ? null : d.key;
    else if (a === "day") {
      this._mode = "family";
      this._anchor = startOfDay(new Date(d.date));
    } else return;
    if (this._range().join() !== before) this._load();
    this._queue(true);
  }
  _label() {
    const a = this._anchor;
    const md = (x) => x.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    if (this._mode === "week") {
      const s = startOfWeek(a);
      const e = addDays(s, 6);
      return `${md(s)} – ${s.getMonth() === e.getMonth() ? e.getDate() : md(e)}`;
    }
    if (this._mode === "month") return a.toLocaleDateString(undefined, { month: "long", year: "numeric" });
    if (this._mode === "list") return `${md(a)} – ${md(addDays(a, 13))}`;
    return a.getTime() === startOfDay().getTime() ? "Today" : a.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  }
  render() {
    const cals = (this.config.calendars || []).filter((e) => this._hass.states[e]);
    if (!cals.length) return `<div class="card"><div class="h">Calendar</div><div class="empty">${NOT_CONNECTED}</div></div>`;
    const c = this.config;
    const H = c.height || 760;
    const sh = c.start_hour ?? 6;
    const eh = c.end_hour ?? 22;
    let evs = this._events;
    const seg = CAL_MODES.map((x) => `<button class="${x.key === this._mode ? "on" : ""}" data-action="mode" data-mode="${x.key}">${x.label}</button>`).join("");
    const isNow = this._range()[0] <= new Date() && new Date() < this._range()[1];
    const ctl = `<div class="ctl"><div class="seg">${seg}</div><div class="nav">
      <button data-action="prev">‹</button><div class="lbl">${esc(this._label())}</div><button data-action="next">›</button>
      ${isNow ? "" : `<button class="tdb" data-action="today">Today</button>`}</div></div>`;
    let who = "";
    if (this._mode !== "family") {
      who = `<div class="who">${FAMILY.map((m) => `<button class="${this._who && this._who !== m.key ? "off" : ""}" data-action="who" data-key="${m.key}"><div class="av" style="background:${m.color}">${m.name[0]}</div>${esc(m.name)}</button>`).join("")}</div>`;
      if (evs && this._who) evs = evs.filter((e) => !e.who.length || e.who.includes(this._who));
    }
    let body;
    if (!evs) body = `<div class="empty">Loading…</div>`;
    else if (this._mode === "week") body = weekHtml(evs, startOfWeek(this._anchor), H - 90, sh, eh);
    else if (this._mode === "month") body = monthHtml(evs, this._anchor, Math.floor((H - 30) / 6));
    else if (this._mode === "list") body = `<div style="max-height:${H - 50}px;overflow-y:auto">${agendaHtml(evs) || `<div class="empty">Nothing on the calendar.</div>`}</div>`;
    else body = familyDayHtml(evs, this._anchor, H - 40, sh, eh);
    return `<div class="card">${ctl}${who}<div class="fade" data-k="${this._mode}|${this._range()[0].getTime()}">${body}</div></div>`;
  }
}

// ---------------------------------------------------------- kid summary

const KID_CSS = `
  .row { display:flex; align-items:center; gap:12px; }
  .ring { width:58px; height:58px; border-radius:50%; display:grid; place-items:center; flex:none; }
  .ring div { width:44px; height:44px; border-radius:50%; background:var(--card); display:grid; place-items:center; font-size:20px; font-weight:700; color:#111; }
  .name { font-size:20px; font-weight:500; }
  .meta { font-size:13px; color:var(--sub); margin-top:2px; }
  .stars { margin-left:auto; text-align:right; font-size:22px; font-weight:700; }
  .stars small { display:block; font-size:11px; color:var(--sub); font-weight:400; }
  .next { margin-top:12px; display:flex; align-items:center; gap:10px; background:var(--card2); border-radius:14px; padding:12px; font-size:15px; width:100%; min-height:56px; }
  .next .chk { margin-left:auto; width:30px; height:30px; border-radius:50%; border:2px solid var(--sub); flex:none; }
  .next.alldone { color:var(--ok); justify-content:center; }
  .next .chk { transition:background-color .25s, border-color .25s; display:grid; place-items:center; }
  .next.checking .chk { background:var(--ok); border-color:var(--ok); animation:kpop .4s ease-out; }
  .next.checking .chk::after { content:"✓"; color:#111; font-weight:700; }
  .pending { opacity:.5; }
`;

/** Weekly stars (counter delta since Sunday) and the AM/PM sticker grid, from recorder history. */
const histCache = new Map();
function weekHistory(hass, member) {
  const key = `${member}|${startOfWeek().toDateString()}|${Math.floor(Date.now() / 300000)}`;
  if (histCache.has(key)) return histCache.get(key);
  const start = startOfWeek();
  const ids = [`counter.stars_${member}`, `binary_sensor.${member}_routines_am_done`, `binary_sensor.${member}_routines_pm_done`];
  const p = hass
    .callApi("GET", `history/period/${start.toISOString()}?filter_entity_id=${ids.join(",")}&minimal_response&no_attributes`)
    .then((res) => {
      const by = {};
      for (const series of res) if (series.length) by[series[0].entity_id] = series;
      const ctr = by[ids[0]];
      const startStars = ctr ? Number(ctr[0].state) : null;
      const grid = { am: Array(7).fill(false), pm: Array(7).fill(false) };
      for (const half of ["am", "pm"]) {
        const s = by[ids[half === "am" ? 1 : 2]] || [];
        for (const st of s) {
          if (st.state !== "on") continue;
          const t = new Date(st.last_changed);
          const di = Math.floor((startOfDay(t) - start) / 86400000);
          if (di >= 0 && di < 7) grid[half][di] = true;
        }
      }
      return { startStars, grid };
    })
    .catch(() => ({ startStars: null, grid: null }));
  histCache.set(key, p);
  return p;
}

class KidCard extends KitchenCard {
  css = KID_CSS;
  shouldRender(h) {
    weekHistory(h, this.config.member).then((w) => {
      if (w !== this._week) {
        this._week = w;
        this._queue();
      }
    });
    return true;
  }
  render() {
    const m = member(this.config.member);
    const list = chores(this._hass, m.key);
    const due = list.filter((c) => c.due);
    const done = due.filter((c) => c.done).length;
    const pct = due.length ? Math.round((100 * done) / due.length) : 0;
    const st = stars(this._hass, m.key);
    const wk = this._week?.startStars != null ? st - this._week.startStars : null;
    const n = nextChore(list);
    const next = n
      ? `<button class="next enter" data-k="${esc(n.id)}" data-chore="${esc(n.id)}" data-on="0" data-pts="${n.points}">${n.emoji} ${esc(n.title)}<div class="chk"></div></button>`
      : `<div class="next alldone enter" data-k="alldone">${due.length ? "All done today! 🎉" : "No chores today"}</div>`;
    if (this._lastSt != null && st > this._lastSt) this._bumpAt = st;
    this._lastSt = st;
    this._celebrate("all", due.length > 0 && done === due.length);
    return `<div class="card">
      <div class="row">
        <div class="ring" style="background:conic-gradient(${m.color} ${pct}%, var(--card2) 0)"><div style="color:${m.color}">${m.name[0]}</div></div>
        <div><div class="name">${m.name}</div><div class="meta">${done} of ${due.length} done</div></div>
        <div class="stars"><span class="${this._bumpAt === st ? "bump" : ""}" data-k="s${st}">⭐ ${st}</span><small>${wk != null ? `+${Math.max(0, wk)} this week` : ""}</small></div>
      </div>
      ${next}
    </div>`;
  }
}

// ------------------------------------------------------- routine column

class RoutineCard extends KitchenCard {
  css = `${KID_CSS}
    .rk { background:var(--card); border-radius:22px; padding:16px; display:flex; flex-direction:column; gap:8px; min-height:100%; }
    .hd { display:flex; align-items:center; gap:10px; }
    .hd .nm { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .hd .av { width:52px; height:52px; font-size:22px; }
    .nm { font-size:26px; font-weight:500; }
    .pts { margin-left:auto; font-size:28px; font-weight:700; text-align:right; white-space:nowrap; }
    .pts small { display:block; font-size:12px; color:var(--sub); font-weight:400; }
    .bar { height:12px; border-radius:6px; background:var(--card2); overflow:hidden; }
    .bar div { height:100%; border-radius:6px; transition:width .3s; }
    .seg { display:flex; gap:6px; }
    .seg button { flex:1; text-align:center; padding:9px 0; border-radius:12px; background:var(--card2); font-size:13px; color:var(--sub); white-space:nowrap; }
    .seg button.on { color:#111; font-weight:500; }
    .seg button.done:not(.on) { color:var(--ok); }
    .sec { font-size:13px; color:var(--sub); text-transform:uppercase; letter-spacing:.06em; margin-top:4px; }
    .ch { display:flex; align-items:center; gap:12px; background:var(--card2); border-radius:16px; padding:10px 12px; font-size:18px; min-height:60px; width:100%; }
    .ch .ic { font-size:26px; width:34px; text-align:center; flex:none; }
    .ch .tt { flex:1; }
    .ch .p { font-size:13px; color:var(--accent); font-weight:500; white-space:nowrap; }
    .ch .tick { width:36px; height:36px; border-radius:50%; border:2.5px solid var(--sub); flex:none; display:grid; place-items:center; font-size:20px; }
    .donegrid { display:grid; grid-template-columns:1fr 1fr; gap:6px; }
    .ch.done { opacity:.45; min-height:44px; padding:6px 8px; gap:6px; font-size:13px; border-radius:12px; }
    .ch.done .ic { font-size:16px; width:20px; }
    .ch.done .tick { border:0; background:var(--ok); color:#111; width:20px; height:20px; font-size:11px; }
    .ch .tick { transition:background-color .25s, border-color .25s; }
    .ch.checking { background:color-mix(in srgb, var(--ok) 20%, var(--card2)); }
    .ch.checking .tick { background:var(--ok); border-color:var(--ok); animation:kpop .4s ease-out; }
    .ch.checking .tick::after { content:"✓"; color:#111; font-weight:700; }
    .ch.pending { opacity:.45; }
    .tools { display:flex; gap:6px; flex:none; }
    .tools button { width:34px; height:34px; border-radius:50%; background:var(--card2); color:var(--sub); font-size:20px; display:grid; place-items:center; }
    .tools button.add { color:var(--txt); }
    .tools button.on { background:var(--accent); color:var(--ink); width:auto; padding:0 14px; border-radius:18px; font-size:15px; font-weight:500; }
    .er { display:flex; align-items:center; gap:10px; background:var(--card2); border-radius:12px; padding:7px 10px; width:100%; font-size:15px; }
    .er .ic { font-size:20px; width:26px; text-align:center; }
    .er .tt { flex:1; min-width:0; }
    .er .tt i { display:block; font-style:normal; font-size:12px; color:var(--sub); margin-top:2px; }
    .er .pen { color:var(--sub); font-size:16px; }
    .wk { background:var(--card2); border-radius:16px; padding:12px; }
    .wk .t { font-size:12px; color:var(--sub); text-transform:uppercase; letter-spacing:.06em; margin-bottom:8px; }
    .wk .g { display:grid; grid-template-columns:repeat(7,1fr); gap:5px; text-align:center; font-size:11px; color:var(--sub); }
    .wk .c { height:26px; border-radius:7px; background:var(--card); display:grid; place-items:center; font-size:14px; }
    .wk .c.y { background:rgba(255,200,60,.18); }
    .wk .c.td { outline:1px solid var(--sub); }
    .goal { background:var(--card2); border-radius:16px; padding:12px; font-size:14px; color:var(--sub); }
    .goal b { color:var(--txt); font-weight:500; }
    .none { color:var(--sub); font-size:15px; padding:8px 2px; }
  `;
  shouldRender(h) {
    weekHistory(h, this.config.member).then((w) => {
      if (w !== this._week) {
        this._week = w;
        this._queue();
      }
    });
    return true;
  }
  action(a, d) {
    if (a === "period") {
      this._period = d.period;
      this._periodSetAt = Date.now();
    } else if (a === "add") routineEditor(this._hass, { member: this.config.member });
    else if (a === "edit") this._edit = !this._edit;
    else if (a === "editr") {
      const r = Routines.all().find((x) => x.uid === d.uid);
      if (r) routineEditor(this._hass, { routine: r });
      return;
    }
    this._queue(true);
  }
  render() {
    const m = member(this.config.member);
    const list = chores(this._hass, m.key).filter((c) => c.due);
    // Follow the clock unless someone picked a period in the last 10 minutes.
    if (!this._period || Date.now() - (this._periodSetAt || 0) > 10 * 60 * 1000) this._period = currentPeriod();
    const done = list.filter((c) => c.done);
    const todayPts = done.reduce((n, c) => n + c.points, 0);
    const pct = list.length ? (100 * done.length) / list.length : 0;
    const st = stars(this._hass, m.key);
    const periods = PERIODS.filter((p) => p.key !== "anytime" && list.some((c) => c.period === p.key));
    if (!periods.some((p) => p.key === this._period) && periods.length) {
      // e.g. no afternoon chores today: show the next period that has some.
      const order = ["morning", "afternoon", "evening"];
      this._period = periods.find((p) => order.indexOf(p.key) >= order.indexOf(this._period))?.key || periods[periods.length - 1].key;
    }
    const seg = periods
      .map((p) => {
        const items = list.filter((c) => c.period === p.key);
        const all = items.every((c) => c.done);
        const on = p.key === this._period;
        return `<button class="${on ? "on" : ""} ${all ? "done" : ""}" style="${on ? `background:${m.color}` : ""}" data-action="period" data-period="${p.key}">${p.icon} ${p.label}${all ? " ✓" : ""}</button>`;
      })
      .join("");
    const row = (c) =>
      `<button class="ch ${c.done ? "done fade" : "enter"}" data-k="${esc(c.id)}" data-chore="${esc(c.id)}" data-on="${c.done ? 1 : 0}" data-pts="${c.points}"><span class="ic">${c.emoji}</span><span class="tt">${esc(c.title)}</span>${
        c.points && !c.done ? `<span class="p">+${c.points}</span>` : ""
      }<div class="tick">${c.done ? "✓" : ""}</div></button>`;
    const cur = list.filter((c) => c.period === this._period && !c.done);
    const any = list.filter((c) => c.period === "anytime" && !c.done);
    const curLabel = PERIODS.find((p) => p.key === this._period)?.label || "";
    let body = "";
    if (periods.length) body += `<div class="sec">${curLabel}</div>${cur.length ? cur.map(row).join("") : `<div class="none">All done ✓</div>`}`;
    if (any.length) body += `<div class="sec">Anytime today</div>${any.map(row).join("")}`;
    if (done.length) body += `<div class="sec">Done today</div><div class="donegrid">${done.map(row).join("")}</div>`;
    if (!list.length) body = `<div class="none">No chores today.</div>`;
    const editable = Routines.active(this._hass);
    if (this._edit && editable) {
      const mine = chores(this._hass, m.key);
      body = `<div class="sec">All of ${esc(m.name)}'s routines · tap one to change it</div>${PERIODS.map((p) => {
        const xs = mine.filter((c) => c.period === p.key);
        return xs.length ? `<div class="sec">${p.icon} ${p.label}</div>${xs.map((c) => `<button class="er enter" data-k="e${esc(c.id)}" data-action="editr" data-uid="${esc(c.id)}"><span class="ic">${c.emoji}</span><span class="tt">${esc(c.title)}<i>${fmtDays(c.routine.days)}${c.routine.points ? ` · ⭐ ${c.routine.points}` : ""}</i></span><span class="pen">✎</span></button>`).join("")}` : "";
      }).join("") || `<div class="none">No routines yet.</div>`}`;
    }
    const tools = editable
      ? `<div class="tools"><button class="add" data-action="add" aria-label="Add a routine">+</button><button class="${this._edit ? "on" : ""}" data-action="edit" aria-label="Edit routines">${this._edit ? "Done" : "✎"}</button></div>`
      : "";
    if (this._lastSt != null && st > this._lastSt) this._bumpAt = st;
    this._lastSt = st;
    this._celebrate("all", list.length > 0 && done.length === list.length);
    if (periods.length) this._celebrate(`p-${this._period}`, cur.length === 0 && list.some((c) => c.period === this._period));

    let week = "";
    if (m.kid && this._week?.grid) {
      const di = new Date().getDay();
      const cells = DAYS.map((d) => `<div>${d[0].toUpperCase()}</div>`).join("") +
        ["am", "pm"].map((h) => this._week.grid[h].map((y, i) => `<div class="c ${y ? "y" : ""} ${i === di ? "td" : ""}">${y ? "⭐" : ""}</div>`).join("")).join("");
      week = `<div class="wk"><div class="t">This week · AM / PM</div><div class="g">${cells}</div></div>`;
    }
    let goal = "";
    const gt = this._hass.states[`input_text.routines_goal_${m.key}`]?.state;
    const gs = Number(this._hass.states[`input_number.routines_goal_stars_${m.key}`]?.state || 0);
    if (gt && gt !== "unknown" && gs > 0) {
      goal = st >= gs
        ? `<div class="goal">🎉 <b>Goal reached!</b> ${esc(gt)}</div>`
        : `<div class="goal">🎯 <b>${gs - st} more stars</b> to ${esc(gt)}</div>`;
    }
    return `<div class="rk">
      <div class="hd"><div class="av" style="background:${m.color}">${m.name[0]}</div><div class="nm">${m.name}</div>${tools}<div class="pts"><span class="${this._bumpAt === st ? "bump" : ""}" data-k="s${st}">⭐ ${st}</span><small>${todayPts ? `+${todayPts} today` : "stars"}</small></div></div>
      <div class="bar"><div style="width:${pct}%;background:${m.color}"></div></div>
      ${periods.length && !this._edit ? `<div class="seg">${seg}</div>` : ""}
      <div class="fade" data-k="${this._edit ? "edit" : this._period}" style="display:flex;flex-direction:column;gap:8px">${body}</div>
      ${this._edit ? "" : week}
      ${this._edit ? "" : goal}
    </div>`;
  }
}

// --------------------------------------------------------- adult strip

class AdultCard extends KitchenCard {
  css = `
    .ad { background:var(--card); border-radius:18px; padding:12px 14px; }
    .top { display:flex; align-items:center; gap:10px; font-size:15px; }
    .top .av { width:30px; height:30px; font-size:13px; }
    .top .x { margin-left:auto; color:var(--sub); font-size:14px; }
    .chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
    .chip { background:var(--card2); border-radius:12px; padding:8px 10px; font-size:13px; }
    .chip { transition:opacity .3s, background-color .25s; }
    .chip.done { opacity:.4; text-decoration:line-through; }
    .chip.checking { background:var(--ok); color:#111; }
    .chip.add { color:var(--sub); padding:8px 12px; }
    .pending { opacity:.5; }
  `;
  action(a) {
    if (a === "add") routineEditor(this._hass, { member: this.config.member });
  }
  render() {
    const m = member(this.config.member);
    const list = chores(this._hass, m.key).filter((c) => c.due);
    const done = list.filter((c) => c.done).length;
    const st = stars(this._hass, m.key);
    const add = Routines.active(this._hass) ? `<button class="chip add" data-action="add" aria-label="Add a routine">+</button>` : "";
    return `<div class="ad"><div class="top"><div class="av" style="background:${m.color}">${m.name[0]}</div><div>${m.name}</div>
      <div class="x">${st ? `⭐ ${st} · ` : ""}${done} / ${list.length}</div></div>
      <div class="chips">${list.map((c) => `<button class="chip ${c.done ? "done" : ""}" data-k="${esc(c.id)}" data-chore="${esc(c.id)}" data-on="${c.done ? 1 : 0}" data-pts="${c.points}">${c.emoji} ${esc(c.title)}</button>`).join("")}${list.length ? "" : `<span class="muted" style="font-size:13px;padding:8px 0">No routines today</span>`}${add}</div>
    </div>`;
  }
}

// ----------------------------------------------------------------- todo

class TodoCard extends KitchenCard {
  css = `
    .li { display:flex; gap:10px; align-items:center; font-size:15px; padding:7px 0; width:100%; }
    .li .c { width:20px; height:20px; border-radius:6px; border:2px solid var(--sub); flex:none; }
    .li.pending { opacity:.4; }
    .more { color:var(--sub); font-size:13px; margin-top:4px; }
  `;
  firstHass() {
    this._sub();
  }
  connected() {
    if (this._hass && !this._unsub) this._sub();
  }
  disconnected() {
    this._unsub?.then((u) => u && u()).catch(() => {});
    this._unsub = null;
  }
  _sub() {
    const e = this.config?.entity;
    if (!e || !this._hass.states[e] || this._unsub) return;
    this._unsub = this._hass.connection
      .subscribeMessage((m) => {
        this._items = (m.items || []).filter((i) => i.status === "needs_action");
        this._queue();
      }, { type: "todo/item/subscribe", entity_id: e })
      .catch(() => null);
  }
  action(a, d) {
    if (a === "complete") {
      this._hass.callService("todo", "update_item", { item: d.uid, status: "completed" }, { entity_id: this.config.entity });
    }
  }
  render() {
    const e = this._hass.states[this.config.entity];
    const title = this.config.title || e?.attributes.friendly_name || "List";
    if (!e) return `<div class="card"><div class="h">${esc(title)}</div><div class="empty">List not set up yet.</div></div>`;
    const items = this._items || [];
    const max = this.config.max || 6;
    return `<div class="card"><div class="h">${esc(title)} <b>${items.length || ""}</b></div>
      ${items.length ? items.slice(0, max).map((i) => `<button class="li" data-action="complete" data-uid="${esc(i.uid)}"><div class="c"></div>${esc(i.summary)}</button>`).join("") : `<div class="empty">Nothing on the list.</div>`}
      ${items.length > max ? `<div class="more">+${items.length - max} more</div>` : ""}
    </div>`;
  }
}

// ---------------------------------------------------------- lists tab

/** Subscribes to the items of several to-do entities; calls back on changes. */
class TodoSubs {
  constructor(onChange) {
    this.items = {};
    this.unsubs = {};
    this.onChange = onChange;
  }
  sync(hass, entities) {
    for (const e of entities) {
      if (this.unsubs[e] || !hass.states[e]) continue;
      this.unsubs[e] = hass.connection
        .subscribeMessage((m) => {
          this.items[e] = m.items || [];
          this.onChange();
        }, { type: "todo/item/subscribe", entity_id: e })
        .catch(() => {
          delete this.unsubs[e];
          return null;
        });
    }
  }
  close() {
    for (const u of Object.values(this.unsubs)) u?.then((f) => f && f()).catch(() => {});
    this.unsubs = {};
  }
}

const LIST_CSS = `
  .tabs { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }
  .tabs button { padding:9px 14px; border-radius:14px; background:var(--card2); font-size:15px; color:var(--sub); }
  .tabs button.on { background:var(--accent); color:var(--ink); font-weight:500; }
  .tabs button b { font-weight:500; margin-left:4px; opacity:.8; }
  form.add { display:flex; gap:8px; margin-bottom:10px; }
  form.add input { flex:1; min-width:0; background:var(--card2); border:1px solid var(--line); border-radius:12px; color:var(--txt);
    font:inherit; font-size:16px; padding:12px 14px; outline:none; }
  form.add input:focus { border-color:var(--accent); }
  form.add button { padding:0 18px; border-radius:12px; background:var(--accent); color:var(--ink); font-weight:500; font-size:15px; display:grid; place-items:center; }
  .li { display:flex; gap:12px; align-items:center; width:100%; background:var(--card2); border-radius:14px; padding:12px 14px; margin-bottom:6px; font-size:17px; min-height:56px; }
  .li .c { width:26px; height:26px; border-radius:50%; border:2.5px solid var(--sub); flex:none; display:grid; place-items:center; font-size:14px; }
  .li .t { flex:1; min-width:0; }
  .li .t i { display:block; font-style:normal; font-size:13px; color:var(--sub); margin-top:2px; }
  .li.done { opacity:.5; min-height:44px; padding:8px 14px; font-size:15px; }
  .li.done .t { text-decoration:line-through; }
  .li.done .c { border:0; background:var(--ok); color:#111; }
  .li.pending { opacity:.35; }
  .li .c { transition:background-color .2s, border-color .2s; }
  .li.leaving { animation:kout .3s ease-in forwards; }
  .li.leaving .c { background:var(--ok); border-color:var(--ok); }
  .li.leaving .t { text-decoration:line-through; }
  .note.leaving { animation:nout .3s ease-in forwards; }
  @keyframes nout { to { opacity:0; transform:scale(.6) rotate(-8deg); } }
  .note.enter { animation:kpop .35s ease-out both; }
  .ch { display:flex; align-items:center; justify-content:space-between; margin:14px 2px 8px; font-size:13px; color:var(--sub);
    text-transform:uppercase; letter-spacing:.06em; }
  .ch button { color:var(--sub); padding:6px 10px; border-radius:10px; background:var(--card2); text-transform:none; letter-spacing:0; }
  .foot { color:var(--sub); font-size:13px; margin-top:10px; }
`;

const fmtDue = (s) => {
  if (!s) return "";
  const d = new Date(s.length === 10 ? `${s}T00:00:00` : s);
  return `Due ${d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}${s.length === 10 ? "" : ` ${fmtTime(d)}`}`;
};

/**
 * The Lists tab, like the Apolosign To-dos screen: a chip per list with its
 * count, an add box, big tap-to-check rows, and a Completed section with
 * Clear. Lists that don't exist in HA yet are named in the footer.
 * The add box is built once (re-rendering it would eat what's being typed);
 * only the chips and rows are redrawn.
 */
class ListsCard extends KitchenCard {
  css = LIST_CSS;
  constructor() {
    super();
    this._subs = new TodoSubs(() => this._queue());
  }
  disconnected() {
    this._subs.close();
  }
  shouldRender(h) {
    this._subs.sync(h, (this.config.lists || []).map((l) => l.entity));
    return true;
  }
  action(a, d, ev, el) {
    const e = this._cur;
    if (a === "list") {
      this._cur = d.entity;
      this._showDone = false;
    } else if (a === "done" || a === "undo") {
      // Let the row slide out before the list redraws without it.
      el?.classList.add("leaving");
      if (a === "done" && el) FX.burst(...FX.at(el.querySelector(".c") || el), "#4cd17a", 8);
      setTimeout(() => this._hass.callService("todo", "update_item", { item: d.uid, status: a === "done" ? "completed" : "needs_action" }, { entity_id: e }), 260);
      return;
    } else if (a === "clear") {
      this._hass.callService("todo", "remove_completed_items", {}, { entity_id: e });
      return;
    } else if (a === "show-done") this._showDone = !this._showDone;
    this._queue();
  }
  _build() {
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}${this.css}</style><div class="card"><div class="tabs"></div>
      <form class="add"><input enterkeyhint="done" autocomplete="off" autocapitalize="sentences"><button type="submit">Add</button></form>
      <div class="items"></div><div class="foot"></div></div>`;
    const f = this.shadowRoot.querySelector("form");
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const inp = f.querySelector("input");
      const v = inp.value.trim();
      if (!v || !this._cur) return;
      this._hass.callService("todo", "add_item", { item: v }, { entity_id: this._cur });
      inp.value = "";
    });
    this._built = true;
  }
  _set(sel, html) {
    const el = this.shadowRoot.querySelector(sel);
    if (el._html !== html) {
      el._html = html;
      el.innerHTML = html;
    }
  }
  paint() {
    if (!this._built) this._build();
    const all = (this.config.lists || []).map((l) => ({ ...l, st: this._hass.states[l.entity] }));
    const lists = all.filter((l) => l.st);
    const missing = all.filter((l) => !l.st);
    const form = this.shadowRoot.querySelector("form");
    this._set(".foot", missing.length ? `Not set up yet: ${missing.map((l) => esc(l.title)).join(", ")}. Add each as a Local To-do list in Home Assistant.` : "");
    if (!lists.length) {
      form.style.display = "none";
      this._set(".tabs", "");
      this._set(".items", `<div class="empty">No lists yet.</div>`);
      return;
    }
    form.style.display = "";
    if (!lists.some((l) => l.entity === this._cur)) this._cur = lists[0].entity;
    const cur = lists.find((l) => l.entity === this._cur);
    form.querySelector("input").placeholder = `Add to ${cur.title}…`;
    this._set(".tabs", lists
      .map((l) => {
        const n = (this._subs.items[l.entity] || []).filter((i) => i.status === "needs_action").length;
        return `<button class="${l.entity === this._cur ? "on" : ""}" data-action="list" data-entity="${l.entity}">${l.icon || ""} ${esc(l.title)}${n ? `<b>${n}</b>` : ""}</button>`;
      })
      .join(""));
    const items = this._subs.items[this._cur];
    if (!items) return this._set(".items", `<div class="empty">Loading…</div>`);
    const open = items.filter((i) => i.status === "needs_action");
    const done = items.filter((i) => i.status === "completed");
    this._seen = this._seen || {};
    const seen = this._seen[this._cur];
    this._seen[this._cur] = new Set(items.map((i) => i.uid));
    const row = (i, isDone) => {
      const sub = [i.description, fmtDue(i.due)].filter(Boolean).map(esc).join(" · ");
      return `<button class="li ${isDone ? "done" : ""} ${seen && !seen.has(i.uid) ? "enter" : ""}" data-action="${isDone ? "undo" : "done"}" data-uid="${esc(i.uid)}"><div class="c">${isDone ? "✓" : ""}</div><div class="t">${esc(i.summary)}${sub && !isDone ? `<i>${sub}</i>` : ""}</div></button>`;
    };
    let html = open.length ? open.map((i) => row(i, false)).join("") : `<div class="empty">Nothing on ${esc(cur.title)}. Add something above.</div>`;
    if (done.length) {
      html += `<div class="ch"><button data-action="show-done">Completed (${done.length}) ${this._showDone ? "▴" : "▾"}</button><button data-action="clear">Clear</button></div>`;
      if (this._showDone) html += done.map((i) => row(i, true)).join("");
    }
    this._set(".items", html);
  }
}

/**
 * Sticky notes (the Apolosign message board and Stickies): one note per
 * open item of a to-do list, an add box, and ✕ to take a note down.
 * `hide_missing: true` hides the card until the list exists.
 */
const NOTE_COLORS = ["#f7e08a", "#a7d8f5", "#f5b5c8", "#c3b5f5", "#b5f0c8", "#f8c79a"];
class NotesCard extends ListsCard {
  css = `${LIST_CSS}
    .notes { display:grid; grid-template-columns:repeat(var(--cols,3),minmax(0,1fr)); gap:8px; }
    .note { position:relative; border-radius:6px 6px 18px 6px; padding:12px 12px 14px; color:#2a2410; font-size:15px; line-height:1.3;
      min-height:84px; box-shadow:0 2px 6px rgba(0,0,0,.35); word-break:break-word; }
    .note i { display:block; font-style:normal; font-size:12px; opacity:.7; margin-top:6px; }
    .note button { position:absolute; top:4px; right:4px; width:26px; height:26px; border-radius:50%; display:grid; place-items:center;
      font-size:13px; color:rgba(0,0,0,.55); }
    .note .tx { padding-right:16px; }
    .notes.one .note { min-height:0; padding:9px 12px 10px; font-size:14px; border-radius:6px 6px 14px 6px; }
  `;
  shouldRender(h) {
    this._subs.sync(h, [this.config.entity]);
    return true;
  }
  action(a, d, ev, el) {
    if (a !== "done") return;
    el?.closest(".note")?.classList.add("leaving");
    setTimeout(() => this._hass.callService("todo", "update_item", { item: d.uid, status: "completed" }, { entity_id: this.config.entity }), 260);
  }
  _build() {
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}${this.css}</style><div class="card"><div class="h"><span class="ttl"></span></div>
      <div class="items"></div>
      ${this.config.add === false ? "" : `<form class="add" style="margin:10px 0 0"><input enterkeyhint="done" autocomplete="off" autocapitalize="sentences"><button type="submit">Post</button></form>`}</div>`;
    this._built = true;
    const f = this.shadowRoot.querySelector("form");
    if (!f) return;
    f.querySelector("input").placeholder = this.config.placeholder || "Write a note…";
    f.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const inp = f.querySelector("input");
      const v = inp.value.trim();
      if (!v) return;
      this._hass.callService("todo", "add_item", { item: v }, { entity_id: this.config.entity });
      inp.value = "";
    });
    this._built = true;
  }
  paint() {
    const e = this._hass.states[this.config.entity];
    const title = this.config.title || "Notes";
    if (!e) {
      this.style.display = this.config.hide_missing ? "none" : "";
      this._built = false;
      this.shadowRoot.innerHTML = `<style>${BASE_CSS}</style><div class="card"><div class="h">${esc(title)}</div><div class="empty">Not set up yet. Add a Local To-do list called "${esc(title)}" in Home Assistant.</div></div>`;
      return;
    }
    this.style.display = "";
    if (!this._built) this._build();
    this.shadowRoot.querySelector(".ttl").textContent = title;
    const items = (this._subs.items[this.config.entity] || []).filter((i) => i.status === "needs_action");
    const max = this.config.max || 9;
    const seen = this._seenNotes;
    this._seenNotes = new Set(items.map((i) => i.uid));
    const html = items.length
      ? `<div class="notes ${this.config.columns === 1 ? "one" : ""}" style="--cols:${this.config.columns || 3}">${items
          .slice(0, max)
          .map((i, n) => `<div class="note ${seen && !seen.has(i.uid) ? "enter" : ""}" style="background:${NOTE_COLORS[n % NOTE_COLORS.length]}"><div class="tx">${esc(i.summary)}</div>${i.description ? `<i>${esc(i.description)}</i>` : ""}<button data-action="done" data-uid="${esc(i.uid)}" aria-label="Take down">✕</button></div>`)
          .join("")}</div>${items.length > max ? `<div class="foot">+${items.length - max} more</div>` : ""}`
      : `<div class="empty">No notes yet.${this.config.add === false ? "" : " Be the first one ✨"}</div>`;
    this._set(".items", html);
  }
}

// ---------------------------------------------------------------- meals

class MealsCard extends KitchenCard {
  tickMs = 300000;
  css = `
    .meal { display:flex; gap:12px; align-items:center; padding:8px 0; border-bottom:1px solid var(--line); }
    .meal:last-child { border:0; }
    .meal .e { width:44px; height:44px; border-radius:12px; background:var(--card2); display:grid; place-items:center; font-size:24px; flex:none; }
    .meal .l { font-size:12px; color:var(--sub); }
    .meal .n { font-size:15px; }
  `;
  shouldRender() {
    const s = startOfDay();
    const key = s.toDateString();
    if (this._key !== key || Date.now() - (this._at || 0) > 10 * 60 * 1000) {
      this._key = key;
      this._at = Date.now();
      fetchEvents(this._hass, [this.config.calendar].filter(Boolean), s, addDays(s, this.config.days || 3)).then((e) => {
        this._events = e;
        this._queue();
      });
    }
    return true;
  }
  render() {
    const title = this.config.title || "Dinner";
    if (!this.config.calendar || !this._hass.states[this.config.calendar])
      return `<div class="card"><div class="h">${esc(title)}</div><div class="empty">Meal plan not connected yet.</div></div>`;
    const s = startOfDay();
    const rows = [];
    for (let i = 0; i < (this.config.days || 3); i++) {
      const d = addDays(s, i);
      const ev = (this._events || []).find((e) => startOfDay(e.start).getTime() === d.getTime());
      rows.push(`<div class="meal"><div class="e">${ev ? "🍽️" : "·"}</div><div><div class="l">${i === 0 ? "Tonight" : fmtDay(d)}</div><div class="n ${ev ? "" : "muted"}">${ev ? esc(ev.title) : "Nothing planned"}</div></div></div>`);
    }
    return `<div class="card"><div class="h">${esc(title)}</div>${rows.join("")}</div>`;
  }
}

/**
 * The Meals tab, like the Apolosign week grid: a row per day (Sun-Sat), a
 * column per meal slot, each slot a meal plan calendar (Mealie makes one per
 * meal type). Back/forward a week; returns to this week after 10 minutes.
 */
const SLOT_TINT = ["#3b3522", "#1e3542", "#40263a", "#2f2944"];
class MealWeekCard extends KitchenCard {
  tickMs = 300000;
  css = `
    .ctl { display:flex; align-items:center; gap:6px; margin-bottom:10px; }
    .ctl .lbl { font-size:16px; font-weight:500; flex:1; }
    .ctl button { min-width:36px; height:36px; border-radius:10px; background:var(--card2); display:grid; place-items:center; font-size:18px; padding:0 10px; }
    .ctl .tdb { font-size:13px; color:var(--sub); }
    .g { display:grid; grid-template-columns:52px repeat(var(--slots),minmax(0,1fr)); gap:6px; }
    .sh { font-size:13px; text-align:center; padding:6px 0; border-radius:10px; font-weight:500; }
    .d { display:flex; flex-direction:column; align-items:center; justify-content:center; font-size:12px; color:var(--sub); }
    .d b { font-size:18px; color:var(--txt); font-weight:500; }
    .d.td b { color:var(--accent); }
    .c { border-radius:12px; padding:8px; display:flex; flex-direction:column; justify-content:center; align-items:center; gap:4px;
      text-align:center; font-size:13px; line-height:1.25; overflow:hidden; }
    .c .e { font-size:22px; }
    .c .x { color:var(--sub); opacity:.5; font-size:18px; }
    .c.td { outline:2px solid var(--accent); outline-offset:-2px; }
  `;
  shouldRender() {
    if (!this._wk || (Date.now() - (this._touched || 0) > 10 * 60 * 1000 && this._wk.getTime() !== startOfWeek().getTime())) this._wk = startOfWeek();
    this._load();
    return true;
  }
  _load() {
    const key = this._wk.toISOString();
    if (this._key === key && Date.now() - (this._at || 0) < 10 * 60 * 1000) return;
    this._key = key;
    this._at = Date.now();
    const slots = this.config.slots || [];
    Promise.all(slots.map((s) => fetchEvents(this._hass, [s.calendar], this._wk, addDays(this._wk, 7)))).then((r) => {
      if (this._key !== key) return;
      this._events = r;
      this._queue();
    });
  }
  action(a) {
    this._touched = Date.now();
    const before = this._wk.getTime();
    if (a === "prev") this._wk = addDays(this._wk, -7);
    else if (a === "next") this._wk = addDays(this._wk, 7);
    else if (a === "today") this._wk = startOfWeek();
    else return;
    if (this._wk.getTime() === before) return;
    this._load();
    this._queue(true);
  }
  render() {
    const slots = this.config.slots || [];
    const title = this.config.title || "Meals";
    if (!slots.some((s) => this._hass.states[s.calendar]))
      return `<div class="card"><div class="h">${esc(title)}</div><div class="empty">Meal plan not connected yet. Add the Mealie integration in Home Assistant and this week fills in.</div></div>`;
    const md = (x) => x.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    const isNow = this._wk.getTime() === startOfWeek().getTime();
    const ctl = `<div class="ctl"><button data-action="prev">‹</button><button data-action="next">›</button><div class="lbl">${md(this._wk)} – ${md(addDays(this._wk, 6))}</div>${isNow ? "" : `<button class="tdb" data-action="today">This week</button>`}</div>`;
    const rowH = Math.floor((this.config.height || 840) / 7);
    const td = startOfDay().getTime();
    let g = `<div></div>${slots.map((s, i) => `<div class="sh" style="background:${SLOT_TINT[i % 4]}">${s.icon || ""} ${esc(s.label)}</div>`).join("")}`;
    for (let i = 0; i < 7; i++) {
      const d = addDays(this._wk, i);
      const isTd = d.getTime() === td;
      g += `<div class="d ${isTd ? "td" : ""}" style="height:${rowH}px">${fmtDay(d)}<b>${d.getDate()}</b></div>`;
      slots.forEach((s, si) => {
        const evs = (this._events?.[si] || []).filter((e) => onDay(e, d));
        const inner = evs.length
          ? `<div class="e">${s.emoji || "🍽️"}</div>${evs.map((e) => `<div>${esc(e.title)}</div>`).join("")}`
          : `<div class="x">·</div>`;
        g += `<div class="c ${isTd ? "td" : ""}" style="background:${SLOT_TINT[si % 4]};height:${rowH}px">${inner}</div>`;
      });
    }
    return `<div class="card">${ctl}<div class="g" style="--slots:${slots.length}">${g}</div></div>`;
  }
}

// ---------------------------------------------------------------- chips

class ChipsCard extends KitchenCard {
  css = `
    .house { display:flex; gap:8px; }
    .chip { flex:1; background:var(--card); border-radius:14px; padding:10px; font-size:13px; color:var(--sub); min-width:0; }
    .chip b { display:block; color:var(--txt); font-size:15px; font-weight:500; margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .chip.on b { color:var(--accent); }
  `;
  render() {
    const out = (this.config.chips || []).map((c) => {
      const s = this._hass.states[c.entity];
      if (!s) return "";
      let val;
      let on = false;
      if (c.attribute) val = `${Math.round(Number(s.attributes[c.attribute]))}${c.unit || ""}`;
      else if (c.on_text || c.off_text) {
        on = s.state === "on";
        val = on ? c.on_text || "On" : c.off_text || "Off";
      } else val = s.state;
      return `<div class="chip ${on ? "on" : ""}">${esc(c.name)}<b>${esc(val)}</b></div>`;
    });
    return `<div class="house">${out.join("")}</div>`;
  }
}

// ---------------------------------------------------------------- stack

// Lays out child cards (any card type) in a padded column, or `columns: 2`
// side by side, with the wall display's spacing. HA's own stacks have no
// padding inside a panel view and a different gap.
class StackCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }
  setConfig(config) {
    this.config = config;
    this._build();
  }
  async _build() {
    const c = this.config;
    const helpers = await window.loadCardHelpers();
    const gap = c.gap ?? 12;
    const root = document.createElement("div");
    // Columns are flex rather than grid, so a card that hides itself (notes
    // with hide_missing) gives its room to the others.
    root.style.cssText = c.columns
      ? `display:flex;gap:${gap}px;align-items:stretch`
      : `display:flex;flex-direction:column;gap:${gap}px;padding:${c.padding ?? 0}px;min-height:${c.padding ? `calc(100vh - ${2 * c.padding}px)` : "auto"}`;
    this._cards = (c.cards || []).map((cc) => {
      const el = helpers.createCardElement(cc);
      if (c.columns) el.style.cssText = "flex:1 1 0;min-width:0";
      if (cc.grow) el.style.flex = "1";
      if (this._hass) el.hass = this._hass;
      root.appendChild(el);
      return el;
    });
    this.shadowRoot.replaceChildren(root);
  }
  set hass(h) {
    this._hass = h;
    for (const el of this._cards || []) el.hass = h;
  }
  getCardSize() {
    return 10;
  }
}

// ----------------------------------------------------------------- pages

// The whole display is one HA view with this card in it: the header, the big
// tabs, and every tab's cards as pages that stay mounted. Switching tabs only
// flips which page is visible, so it's instant: no HA view change, nothing
// re-subscribes or refetches, and cards on hidden pages skip their renders
// until they're shown (KitchenCard._renderNow). The page is kept in the URL
// hash (#calendar), and the display goes back to Home after `idle_home`
// minutes (default 5) without a touch.
class PagesCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._built = {};
    this._touched = Date.now();
  }
  setConfig(config) {
    this.config = config;
    this._build();
  }
  css = `
    :host { display:block; }
    .wrap { display:flex; flex-direction:column; gap:12px; min-height:100vh; background:var(--bg); transition:background-color .6s; position:relative; }
    .garland { flex:1; display:flex; justify-content:space-evenly; font-size:19px; opacity:.9; pointer-events:none; overflow:hidden; min-width:0; }
    .garland span { animation:gsw 3.2s ease-in-out infinite alternate; transform-origin:50% 0; }
    .garland span:nth-child(odd) { animation-delay:-1.6s; }
    @keyframes gsw { from { transform:rotate(-9deg); } to { transform:rotate(9deg); } }
    .bar { display:flex; gap:8px; align-items:center; position:relative; padding:0 2px; }
    .tabs { display:flex; gap:8px; position:relative; flex-wrap:wrap; }
    .pill { position:absolute; left:0; top:0; height:38px; border-radius:20px; background:var(--accent); transition:transform .28s cubic-bezier(.3,.9,.3,1.1), width .28s, background-color .4s; }
    .tab { padding:9px 16px; border-radius:20px; background:var(--card); color:var(--sub); font-size:15px; font-weight:500; height:38px; position:relative; transition:color .2s, background-color .4s; }
    .tab.on { color:var(--ink); background:transparent; }
    .theme { margin-left:auto; flex:none; width:38px; height:38px; border-radius:50%; background:var(--card); display:grid; place-items:center; font-size:18px; }
    .page[hidden] { display:none; }
    .page.enter { animation:kin .24s ease-out both; }
  `;
  async _build() {
    const c = this.config;
    this._helpers = this._helpers || (await window.loadCardHelpers());
    const pad = c.padding ?? 16;
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}${MOTION_CSS}${this.css}</style>
      <div class="wrap" style="padding:${pad}px"><div class="hdr"></div>
      <div class="bar"><div class="tabs"><div class="pill"></div>${(c.pages || [])
        .map((p) => `<button class="tab" data-page="${esc(p.path)}">${esc(p.title)}</button>`)
        .join("")}</div><div class="garland"></div>${c.theme_button === false ? "" : `<button class="theme" data-theme aria-label="Theme">🎨</button>`}</div>
      <div class="pages">${(c.pages || []).map((p) => `<div class="page" data-page="${esc(p.path)}" hidden></div>`).join("")}</div></div>`;
    const root = this.shadowRoot;
    root.addEventListener("pointerdown", (e) => {
      this._touched = Date.now();
      ripple(e);
    });
    root.addEventListener("click", (e) => {
      const el = e.composedPath().find((n) => n.dataset && (n.dataset.page || "theme" in n.dataset) && n.tagName === "BUTTON");
      if (!el) return;
      if ("theme" in el.dataset) themePicker(this._hass);
      else this.show(el.dataset.page);
    });
    this._header = this._helpers.createCardElement({ type: "custom:kitchen-header-card", ...(c.header || {}) });
    root.querySelector(".hdr").appendChild(this._header);
    if (this._hass) this._header.hass = this._hass;
    window.addEventListener("hashchange", () => this.show(this._fromHash(), true));
    window.addEventListener("kitchen-theme", () => this._garland());
    this._garland();
    this.show(this._fromHash(), true);
    // Build the other pages once the first one is up, so the first visit to a tab is as quick as the rest.
    setTimeout(() => (c.pages || []).forEach((p) => this._page(p.path)), 1200);
    clearInterval(this._idle);
    this._idle = setInterval(() => {
      const home = (c.pages || [])[0]?.path;
      if (home && this._cur !== home && Date.now() - this._touched > (c.idle_home ?? 5) * 60000) this.show(home);
    }, 30000);
  }
  _fromHash() {
    const h = decodeURIComponent(location.hash.slice(1));
    const pages = this.config.pages || [];
    return (pages.find((p) => p.path === h) || pages[0] || {}).path;
  }
  _page(path) {
    if (this._built[path]) return this._built[path];
    const p = (this.config.pages || []).find((x) => x.path === path);
    const host = this.shadowRoot.querySelector(`.page[data-page="${CSS.escape(path)}"]`);
    if (!p || !host) return null;
    const el = this._helpers.createCardElement({ type: "custom:kitchen-stack-card", cards: p.cards || [], gap: p.gap ?? 12 });
    if (this._hass) el.hass = this._hass;
    host.appendChild(el);
    this._built[path] = el;
    return el;
  }
  show(path, initial) {
    if (!path) return;
    this._page(path);
    const page = (this.config.pages || []).find((p) => p.path === path);
    for (const el of this.shadowRoot.querySelectorAll(".page")) {
      const on = el.dataset.page === path;
      if (on && el.hidden) {
        el.hidden = false;
        if (!initial) {
          el.classList.remove("enter");
          void el.offsetWidth;
          el.classList.add("enter");
        }
      } else if (!on) el.hidden = true;
    }
    for (const b of this.shadowRoot.querySelectorAll(".tab")) b.classList.toggle("on", b.dataset.page === path);
    const compact = page?.compact_header ?? path !== (this.config.pages || [])[0]?.path;
    if (!!this._header.config?.compact !== compact) this._header.setConfig({ type: "custom:kitchen-header-card", ...(this.config.header || {}), compact });
    this._cur = path;
    this._pill(initial);
    if (decodeURIComponent(location.hash.slice(1)) !== path) history.replaceState(history.state, "", `${location.pathname}${location.search}#${encodeURIComponent(path)}`);
    // Cards on the page that went stale while hidden render now, before the next frame.
    window.dispatchEvent(new Event("kitchen-page-show"));
  }
  _pill(instant) {
    const b = this.shadowRoot.querySelector(`.tab[data-page="${CSS.escape(this._cur)}"]`);
    const pill = this.shadowRoot.querySelector(".pill");
    if (!b || !pill) return;
    if (!b.offsetWidth) return requestAnimationFrame(() => this._pill(true));
    if (instant) pill.style.transition = "none";
    pill.style.width = `${b.offsetWidth}px`;
    pill.style.transform = `translate(${b.offsetLeft}px, ${b.offsetTop}px)`;
    if (instant) requestAnimationFrame(() => (pill.style.transition = ""));
  }
  _garland() {
    const g = this.shadowRoot.querySelector(".garland");
    if (!g) return;
    const t = Theme.current();
    g.innerHTML = t.garland && !REDUCED() ? t.garland.split(" ").map((x) => `<span>${x}</span>`).join("") : "";
  }
  set hass(h) {
    this._hass = h;
    Theme.apply(h);
    if (this._header) this._header.hass = h;
    for (const el of Object.values(this._built)) el.hass = h;
  }
  getCardSize() {
    return 20;
  }
}

// --------------------------------------------------------------- holiday

// "<holiday>-<year>" tokens; a fresh input_text reads "unknown", which isn't one.
const answeredTokens = (v) => String(v || "").split(",").filter((x) => /^[a-z0-9]+-\d{4}$/.test(x));
/** The holiday offer to show now, or null: in a holiday's window, its theme off, not answered yet. */
function holidayOffer(hass) {
  const h = holidayNow();
  const s = hass.states[THEME_ENTITY];
  const ans = hass.states[THEME_ANSWERED];
  if (!h || !s || !ans || s.state === h.key) return null;
  const tok = `${h.key}-${h.year}`;
  if (answeredTokens(ans.state).includes(tok) || holidayOffer.local === tok) return null;
  const when = h.days === 0 ? "is today" : h.days < 0 ? "was yesterday" : h.days === 1 ? "is tomorrow" : `is in ${h.days} days`;
  return { ...h, tok, text: `${h.theme.name} ${when}!` };
}
function answerHoliday(hass, on, rect) {
  const o = holidayOffer(hass);
  if (!o) return;
  const prev = answeredTokens(hass.states[THEME_ANSWERED]?.state).filter((x) => x !== o.tok);
  hass.callService("input_text", "set_value", { entity_id: THEME_ANSWERED, value: [...prev, o.tok].slice(-8).join(",").slice(-250) });
  if (on) {
    hass.callService("input_select", "select_option", { entity_id: THEME_ENTITY, option: o.key });
    if (rect) FX.confetti(rect, [o.theme.vars.accent, "#ffd54a", "#ffffff"]);
  }
  holidayOffer.local = o.tok; // hide it here right away
}

// "🎃 Halloween is in 3 days! Dress up the display?" on Home while a holiday
// is coming up and its theme isn't on. Answers (either way) are remembered per
// holiday and year in input_text.kitchen_theme_answered, house-wide.
class HolidayCard extends KitchenCard {
  tickMs = 600000;
  css = `
    .hb { display:flex; align-items:center; gap:14px; background:linear-gradient(120deg, var(--card2), var(--card)); border:1px solid var(--line);
      border-radius:18px; padding:12px 14px; animation:kin .4s ease-out both; }
    .hb .e { font-size:34px; animation:kpop .6s ease-out both, hbob 2.4s ease-in-out .6s infinite alternate; }
    @keyframes hbob { to { transform:translateY(-4px) rotate(6deg); } }
    .hb .t { flex:1; font-size:17px; }
    .hb .t small { display:block; color:var(--sub); font-size:13px; margin-top:2px; }
    .hb button { padding:11px 16px; border-radius:14px; font-size:15px; font-weight:500; background:var(--card2); }
    .hb button.go { background:var(--hol); color:#140c05; }
  `;
  action(a) {
    answerHoliday(this._hass, a === "on", this.getBoundingClientRect());
    this._queue(true);
  }
  render() {
    const o = holidayOffer(this._hass);
    this.style.display = o ? "" : "none";
    if (!o) return "";
    return `<div class="hb" style="--hol:${o.theme.vars.accent}"><div class="e">${o.theme.emoji}</div>
      <div class="t">${esc(o.text)}<small>Dress up the display for it?</small></div>
      <button data-action="no">Not this year</button><button class="go" data-action="on">Turn it on</button></div>`;
  }
}

// ------------------------------------------------------------ register

const CARDS = {
  "kitchen-stack-card": [StackCard, "Kitchen stack", "Padded layout for the wall display"],
  "kitchen-header-card": [HeaderCard, "Kitchen header", "Clock, weather and who's home"],
  "kitchen-tabs-card": [TabsCard, "Kitchen tabs", "Big view tabs for the wall display"],
  "kitchen-family-day-card": [FamilyDayCard, "Kitchen family day", "Today's calendar, one column per person"],
  "kitchen-agenda-card": [AgendaCard, "Kitchen agenda", "Upcoming events"],
  "kitchen-kid-card": [KidCard, "Kitchen kid summary", "Progress, stars and the next chore"],
  "kitchen-routine-card": [RoutineCard, "Kitchen routines", "A person's chores for today"],
  "kitchen-adult-card": [AdultCard, "Kitchen adult routines", "Compact routines for a grown-up"],
  "kitchen-todo-card": [TodoCard, "Kitchen list", "A to-do list with tap to check off"],
  "kitchen-meals-card": [MealsCard, "Kitchen meals", "The next few dinners from a meal plan calendar"],
  "kitchen-chips-card": [ChipsCard, "Kitchen status chips", "A row of house status tiles"],
  "kitchen-calendar-card": [CalendarCard, "Kitchen calendar", "Family, week, month and list views with a person filter"],
  "kitchen-lists-card": [ListsCard, "Kitchen lists", "Several to-do lists with add, check off and clear"],
  "kitchen-notes-card": [NotesCard, "Kitchen notes", "Sticky notes from a to-do list"],
  "kitchen-meal-week-card": [MealWeekCard, "Kitchen meal week", "This week's meal plan, a column per meal"],
  "kitchen-pages-card": [PagesCard, "Kitchen pages", "The whole display: header, tabs and every tab's cards, switched instantly"],
  "kitchen-holiday-card": [HolidayCard, "Kitchen holiday", "Offers a holiday theme when one is coming up"],
};
window.customCards = window.customCards || [];
for (const [tag, [cls, name, description]] of Object.entries(CARDS)) {
  if (!customElements.get(tag)) customElements.define(tag, cls);
  window.customCards.push({ type: tag, name, description });
}
console.info(`%c tnwks kitchen cards ${VERSION} `, "background:#ff9f43;color:#1b1206;border-radius:4px");
