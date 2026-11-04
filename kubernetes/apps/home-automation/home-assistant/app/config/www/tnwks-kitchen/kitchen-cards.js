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

const VERSION = "1.12.1";

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
  return { title: ev.summary || "(no title)", start, end, allDay, who, calendar, description: ev.description || "", location: ev.location || "" };
}

// Events drawn anywhere carry data-action="ev" data-ev=<id>; a tap opens
// eventSheet(). fetchEvents caches its results, so an event keeps its id
// across renders.
const EVENTS = new Map();
let evSeq = 0;
function evId(e) {
  if (!e._id) {
    e._id = `e${++evSeq}`;
    EVENTS.set(e._id, e);
    if (EVENTS.size > 3000) EVENTS.delete(EVENTS.keys().next().value);
  }
  return e._id;
}
const evAttr = (e) => ` data-action="ev" data-ev="${evId(e)}"`;
/** Switch the wall display to another of its pages (Home, Calendar, ...). */
function goPage(path) {
  if (decodeURIComponent(location.hash.slice(1)) !== path) location.hash = encodeURIComponent(path);
}
/**
 * Mealie on the wall display: full screen inside HA, in a frame, with a bar
 * to close it. The tablet's launcher can't install the PWA, and Chrome would
 * take the screen over from the kiosk. Elsewhere Mealie opens outside HA.
 */
function openMealie(url, win) {
  if (!KM.active()) return openExternal(url, win);
  win?.close();
  document.querySelector(".tnwks-mealie")?.remove();
  const host = document.createElement("div");
  host.className = "tnwks-mealie";
  host.style.cssText = "position:fixed;inset:0;z-index:9600";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>${BASE_CSS}${MOTION_CSS}
    .mv { position:fixed; inset:0; display:flex; flex-direction:column; background:var(--bg); animation:kfade .2s ease-out both; }
    .mb { display:flex; align-items:center; gap:10px; padding:10px 14px; background:var(--card); }
    .mb b { flex:1; font-size:18px; font-weight:500; }
    .mb button { padding:10px 18px; border-radius:14px; background:var(--card2); font-size:16px; font-weight:500; }
    .mb .x { background:var(--accent); color:var(--ink); }
    iframe { flex:1; width:100%; border:0; background:#fff; }</style>
    <div class="mv"><div class="mb"><b>🍲 Mealie</b><button data-a="home">Recipes</button><button class="x" data-a="close">✕ Close</button></div>
    <iframe allow="clipboard-write; fullscreen; screen-wake-lock" referrerpolicy="strict-origin"></iframe></div>`;
  const frame = root.querySelector("iframe");
  frame.src = url;
  root.addEventListener("click", (e) => {
    const b = e.composedPath().find((n) => n.dataset?.a);
    if (!b) return;
    if (b.dataset.a === "close") host.remove();
    else frame.src = new URL("/", url).href;
  });
  root.addEventListener("pointerdown", ripple);
  document.body.appendChild(host);
}
/** Open a link outside HA: the HA app hands it to Android (an installed PWA such as Mealie catches its own links), browsers get a new tab. */
function openExternal(url, win) {
  if (KM._inApp()) location.href = url;
  else if (win) win.location = url;
  else window.open(url, "_blank", "noopener");
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
  .spark.big { width:6px; height:6px; animation-duration:1.15s; animation-timing-function:cubic-bezier(.1,.7,.3,1); }
  .pop { font-size:22px; animation:fxpop .95s cubic-bezier(.2,.7,.3,1) forwards; }
  @keyframes fxpop { 0% { opacity:0; transform:translate(-50%,-50%) scale(.3); } 20% { opacity:1; }
    100% { opacity:0; transform:translate(calc(-50% + var(--dx)),calc(-50% + var(--dy))) scale(1.1) rotate(var(--rot)); } }
  .conf.ce { width:auto; height:auto; background:none; font-size:20px; }
  .cam { position:fixed; left:0; top:0; will-change:transform; }
  .cam > i { display:block; font-style:normal; font-size:var(--s); line-height:1; transform-origin:50% 100%; }
  .cam.fly { animation:cfly var(--t) linear forwards; }
  .cam.fly > i { animation:cflap .32s ease-in-out infinite alternate; }
  .cam.walk, .cam.hop { animation:cwalk var(--t) linear forwards; }
  .cam.walk > i { animation:cwaddle .45s ease-in-out infinite alternate; }
  .cam.hop > i { animation:chop .62s cubic-bezier(.3,0,.7,1) infinite; }
  .cam.peek { animation:cpeek var(--t) ease-in-out forwards; }
  .cam.peek > i { animation:cwaddle 1.3s ease-in-out infinite alternate; }
  @keyframes cfly { from { transform:translate3d(-14vw,var(--y),0); } to { transform:translate3d(112vw,calc(var(--y) + var(--dy)),0); } }
  @keyframes cflap { from { transform:translateY(-7px) scaleY(1); } to { transform:translateY(7px) scaleY(.72); } }
  @keyframes cwalk { from { transform:translate3d(108vw,calc(100vh - var(--s) - 4px),0); } to { transform:translate3d(-14vw,calc(100vh - var(--s) - 4px),0); } }
  @keyframes cwaddle { from { transform:rotate(-7deg); } to { transform:rotate(7deg) translateY(-2px); } }
  @keyframes chop { 0%,100% { transform:translateY(0) scaleY(.88); } 45% { transform:translateY(-40px) scaleY(1.06); } }
  @keyframes cpeek { 0% { transform:translate3d(var(--x),100vh,0); } 20%,76% { transform:translate3d(var(--x),calc(100vh - var(--s) * .85),0); } 100% { transform:translate3d(var(--x),100vh,0); } }
  @media (prefers-reduced-motion: reduce) { .f, .p, .cam { display:none; } }
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
  // In a holiday theme a tap also pops a few of its emoji, and confetti is its colors with some of its emoji mixed in.
  burst(x, y, color = "#4cd17a", n = 10) {
    const th = Theme.current();
    const second = th.confetti ? th.confetti[0] : "#ffd54a";
    for (let i = 0; i < n; i++)
      this._add("spark", `left:${x}px;top:${y}px;background:${i % 3 ? color : second};--r:${(360 / n) * i + Math.random() * 20}deg;--d:${34 + Math.random() * 26}px`, "", 700);
    for (let i = 0; th.particles && i < 3; i++) {
      const a = -Math.PI / 2 + (i - 1) * 0.9 + (Math.random() - 0.5) * 0.5, d = 46 + Math.random() * 26;
      this._add("pop", `left:${x}px;top:${y}px;--dx:${Math.cos(a) * d}px;--dy:${Math.sin(a) * d}px;--rot:${(Math.random() - 0.5) * 60}deg`, th.particles[Math.floor(Math.random() * th.particles.length)], 1000);
    }
  },
  confetti(rect, colors) {
    const cs = colors || Theme.confetti();
    const emoji = Theme.current().particles;
    const cx = rect.left + rect.width / 2;
    for (let i = 0; i < 46; i++) {
      const x0 = cx + (Math.random() - 0.5) * 40;
      const y0 = rect.top + rect.height * 0.35;
      const x1 = x0 + (Math.random() - 0.5) * rect.width * 1.3;
      const y1 = y0 + 120 + Math.random() * rect.height * 0.9;
      const e = emoji && i % 6 === 0 ? emoji[(i / 6) % emoji.length] : "";
      this._add(e ? "conf ce" : "conf", `${e ? "" : `background:${cs[i % cs.length]};`}--x0:${x0}px;--y0:${y0}px;--x1:${x1}px;--y1:${y1}px;--rot:${(Math.random() - 0.5) * (e ? 240 : 900)}deg;--t:${1.1 + Math.random() * 0.9}s`, e, 2200);
    }
  },
  /** A firework bursting at x, y (the New Year and 4th of July cameos). */
  firework(x, y, colors = Theme.confetti()) {
    const n = 22, c = colors[Math.floor(Math.random() * colors.length)];
    for (let i = 0; i < n; i++)
      this._add("spark big", `left:${x}px;top:${y}px;background:${i % 4 ? c : "#ffffff"};--r:${(360 / n) * i + Math.random() * 8}deg;--d:${70 + Math.random() * 50}px`, "", 1250);
  },
  /** Center of an element, for anchoring an effect to what was tapped. */
  at(el) {
    const r = el.getBoundingClientRect();
    return [r.left + r.width / 2, r.top + r.height / 2];
  },
};

// ---------------------------------------------------------------- themes

// Holiday themes: a palette, dressed-up cards (SKINS), falling/rising/drifting
// emoji, a garland over the header, themed taps and confetti, a character that
// crosses the screen now and then, and a greeting on the day. The wall
// display offers each one in its window (lead days before, through `after`
// days past the holiday). Theme and mode belong to whoever picked them: the
// wall display's are input_select.kitchen_theme / kitchen_mode
// (packages/kitchen_display.yaml), which its screensaver reads too; everyone
// else's are kept with their HA user (Prefs), so they follow that person
// from device to device and nobody else's screen changes. Any theme can be
// picked any time; one left on through its holiday switches itself off in the
// week after (themeExpired).
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
// `garland` is emoji, or { lights } / { bunting } colors; `confetti` colors the
// celebrations and the sparks of a tap; `cameo` lists who may cross the screen
// (Theme._cameo: fly across the top, walk or hop along the bottom, peek up
// from the bottom edge, or fireworks).
const THEMES = {
  newyear: { name: "New Year", emoji: "🎉", day: (y) => new Date(y, 0, 1), lead: 2, greet: "Happy New Year!",
    vars: { accent: "#e9c46a", bg: "#0e0f1a", card: "#181a2b", card2: "#22253a", line: "#30344d" },
    particles: ["✨", "🎉", "🎊", "⭐"], motion: "twinkle", garland: "🎉 ✨ 🎊 🥂 ✨",
    confetti: ["#e9c46a", "#fff3c4", "#c9ced9", "#f4a3c0", "#4f8cff"], cameo: [{ kind: "fireworks" }, { kind: "fly", e: "🍾", s: 40 }] },
  valentines: { name: "Valentine's", emoji: "💕", day: (y) => new Date(y, 1, 14), lead: 7, greet: "Happy Valentine's Day!",
    vars: { accent: "#ff5c8a", bg: "#170d12", card: "#24151d", card2: "#311d28", line: "#432837" },
    particles: ["💕", "💗", "❤️", "💘"], motion: "rise", garland: "💕 💌 💗 🌹 💘",
    confetti: ["#ff5c8a", "#ff9ab8", "#ffffff", "#e5484d", "#ffd1dc"], cameo: [{ kind: "fly", e: "💘", t: 6 }, { kind: "peek", e: "💝" }] },
  stpatricks: { name: "St. Patrick's", emoji: "☘️", day: (y) => new Date(y, 2, 17), lead: 5, greet: "Happy St. Patrick's Day!",
    vars: { accent: "#3ccf6e", bg: "#0c1510", card: "#15231a", card2: "#1d3024", line: "#284232" },
    particles: ["☘️", "🍀", "🌈", "🪙"], motion: "fall", garland: "☘️ 🍀 🌈 🪙 ☘️",
    confetti: ["#3ccf6e", "#9be15d", "#e9c46a", "#ffffff", "#1f9e4f"], cameo: [{ kind: "peek", e: "🌈", s: 64 }, { kind: "hop", e: "🐸" }] },
  easter: { name: "Easter", emoji: "🐣", day: easter, lead: 7, greet: "Happy Easter!",
    vars: { accent: "#b48cf2", bg: "#13121a", card: "#1e1c29", card2: "#292637", line: "#37334a" },
    particles: ["🌷", "🐣", "🥚", "🐰", "🌼"], motion: "rise", garland: "🐣 🌷 🥚 🐰 🌼",
    confetti: ["#f7a8c4", "#ffe08a", "#a8e6c1", "#a8d2f7", "#cdb4f6"], cameo: [{ kind: "hop", e: "🐇" }, { kind: "peek", e: "🐣" }] },
  july4: { name: "4th of July", emoji: "🎆", day: (y) => new Date(y, 6, 4), lead: 4, greet: "Happy 4th of July!",
    vars: { accent: "#4f8cff", bg: "#0d1220", card: "#161d30", card2: "#1f283f", line: "#2c3753" },
    particles: ["⭐", "🎆", "🎇", "✨"], motion: "twinkle", garland: { bunting: ["#e5484d", "#f4f4f4", "#4f8cff"] },
    confetti: ["#e5484d", "#ffffff", "#4f8cff", "#ffd54a"], cameo: [{ kind: "fireworks" }] },
  halloween: { name: "Halloween", emoji: "🎃", day: (y) => new Date(y, 9, 31), lead: 14, greet: "Happy Halloween!",
    vars: { accent: "#ff7a1a", bg: "#120d17", card: "#1d1626", card2: "#2a2035", line: "#3a2d48" },
    particles: ["🦇", "👻", "🎃", "🕸️", "🦇"], motion: "drift", garland: { lights: ["#ff7a1a", "#a86cf0", "#7ee06a"] },
    confetti: ["#ff7a1a", "#a86cf0", "#7ee06a", "#ffd54a", "#2a2035"],
    cameo: [{ kind: "fly", e: "🦇", s: 38 }, { kind: "peek", e: "👻" }, { kind: "walk", e: "🕷️", s: 30 }] },
  thanksgiving: { name: "Thanksgiving", emoji: "🦃", day: (y) => nthWeekday(y, 10, 4, 4), lead: 7, greet: "Happy Thanksgiving!",
    vars: { accent: "#e08a3c", bg: "#15110d", card: "#221b14", card2: "#2e251b", line: "#3d3125" },
    particles: ["🍂", "🍁", "🍂", "🌰"], motion: "fall", garland: "🍂 🦃 🥧 🌽 🍁",
    confetti: ["#e08a3c", "#c0392b", "#e9c46a", "#8d5524", "#f3d9a4"], cameo: [{ kind: "walk", e: "🦃" }] },
  christmas: { name: "Christmas", emoji: "🎄", day: (y) => new Date(y, 11, 25), lead: 24, after: 1, greet: "Merry Christmas!",
    tint: "#3fbf6a", vars: { accent: "#e5484d", ok: "#3fbf6a", bg: "#0d1512", card: "#16211c", card2: "#1f2d26", line: "#2b3d33" },
    particles: ["❄️", "❄️", "✨", "❄️"], motion: "fall", garland: { lights: ["#e5484d", "#ffd54a", "#3fbf6a", "#4fb3f0"] },
    confetti: ["#e5484d", "#3fbf6a", "#ffffff", "#ffd54a"], cameo: [{ kind: "peek", e: "🎅" }, { kind: "peek", e: "⛄" }, { kind: "fly", e: "🦌", s: 40 }] },
};
const MS_DAY = 86400000;
// How a holiday reads in a sentence ("3 days to the 4th of July").
const HOLIDAY_TITLE = { newyear: "New Year's", valentines: "Valentine's Day", stpatricks: "St. Patrick's Day", july4: "the 4th of July" };
/** Days from today to a theme's next holiday (0 on the day). */
function daysUntil(th, now = new Date()) {
  const t = startOfDay(now);
  for (const y of [t.getFullYear(), t.getFullYear() + 1]) {
    const days = Math.round((th.day(y) - t) / MS_DAY);
    if (days >= 0) return days;
  }
  return null;
}
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
/**
 * True when a holiday theme should come down by itself: it's the week after
 * its holiday and it was picked before the holiday was over (left on from the
 * holiday). Picked after, it's a choice and stays, any time of year.
 * `pickedAt` is when it was picked (ms); unknown counts as before.
 */
function themeExpired(key, pickedAt, now = new Date()) {
  const th = THEMES[key];
  if (!th) return false;
  const t = startOfDay(now);
  return [t.getFullYear() - 1, t.getFullYear()].some((y) => {
    const end = addDays(th.day(y), th.after || 0);
    return t > end && t - end <= 7 * MS_DAY && !(pickedAt >= addDays(end, 1).getTime());
  });
}
const THEME_ENTITY = "input_select.kitchen_theme";
const THEME_ANSWERED = "input_text.kitchen_theme_answered";
/** localStorage that can't throw (it does in some private windows). */
const Local = {
  // Where devices kept their own theme and mode before Prefs; moved to the user once (Prefs._got).
  THEME: "tnwks-kitchen-theme",
  MODE: "tnwks-kitchen-mode",
  get(k) {
    try {
      return localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set(k, v) {
    try {
      if (v == null) localStorage.removeItem(k);
      else localStorage.setItem(k, v);
    } catch {}
  },
};
/** The wall display keeps its theme in the input_selects (the screensaver reads them); everyone else's is their own. */
const isWall = () => !!KM.home();

// Everyone but the wall display keeps their theme and mode with their HA user,
// in HA's frontend user data (frontend/set_user_data, the per-user store HA's
// own frontend uses), so a pick follows that person to every device they sign
// in on and changes nobody else's screen. Never picked means Everyday, auto,
// whatever the wall display wears. A copy per user in localStorage paints the
// last choice right away on load; the subscription then confirms or
// corrects it, and pushes picks made on the person's other devices.
const PREFS_KEY = "tnwks-kitchen-display";
const Prefs = {
  value: undefined, // undefined: not known yet; null: never picked
  _cacheKey(hass) {
    return `tnwks-kitchen-prefs:${hass?.user?.id || ""}`;
  },
  watch(hass) {
    const conn = hass?.connection;
    if (this._sub || !hass?.user || !conn?.subscribeMessage) return;
    if (this.value === undefined) {
      try {
        this.value = JSON.parse(Local.get(this._cacheKey(hass)) || "null");
      } catch {
        this.value = null;
      }
    }
    // home-assistant-js-websocket subscribes again by itself after a reconnect.
    this._sub = conn.subscribeMessage((m) => this._got(hass, m?.value ?? null), { type: "frontend/subscribe_user_data", key: PREFS_KEY }).catch((err) => {
      console.warn("tnwks kitchen cards: user theme", err);
      this._sub = null;
    });
  },
  _got(hass, v) {
    const old = { theme: Local.get(Local.THEME), mode: Local.get(Local.MODE) };
    if (old.theme || old.mode) {
      // This device's pick from before themes were per person becomes the person's, unless they already have one.
      Local.set(Local.THEME, null);
      Local.set(Local.MODE, null);
      if (!v) {
        v = Object.fromEntries(Object.entries(old).filter(([, x]) => x));
        this._send(hass, v);
      }
    }
    if (JSON.stringify(v) === JSON.stringify(this.value)) return;
    this.value = v;
    this._cache(hass);
    Theme.apply(Theme._hass || hass);
  },
  /** Save part of this person's choice ({ theme } or { mode }); the caller repaints. */
  update(hass, patch) {
    this.value = { ...(this.value || {}), ...patch };
    this._cache(hass);
    return this._send(hass, this.value);
  },
  _send(hass, value) {
    return hass.connection
      .sendMessagePromise({ type: "frontend/set_user_data", key: PREFS_KEY, value })
      .catch((err) => console.warn("tnwks kitchen cards: saving user theme", err));
  },
  _cache(hass) {
    Local.set(this._cacheKey(hass), this.value ? JSON.stringify(this.value) : null);
  },
};
const Theme = {
  key: "default",
  mode: "dark",
  current() {
    return THEMES[this.key] || DEFAULT_THEME;
  },
  apply(hass) {
    if (hass) this._hass = hass;
    const wall = isWall();
    // When the theme was picked: the select's last change on the wall display
    // (an HA restart resets that, which only ever keeps a theme up), the time
    // saved with the pick for everyone else.
    let key, pickedAt;
    if (wall) {
      const s = hass?.states?.[THEME_ENTITY];
      key = s && THEMES[s.state] ? s.state : "default";
      pickedAt = Date.parse(s?.last_changed);
    } else {
      Prefs.watch(hass);
      key = THEMES[Prefs.value?.theme] ? Prefs.value.theme : "default";
      pickedAt = Prefs.value?.at;
    }
    if (key !== "default" && themeExpired(key, pickedAt)) {
      if (!this._expiring) {
        this._expiring = true;
        const done = () => (this._expiring = false);
        if (wall) hass.callService("input_select", "select_option", { entity_id: THEME_ENTITY, option: "default" }).finally(done);
        else Prefs.update(hass, { theme: "default" }).finally(done);
      }
      key = "default";
    }
    const mode = resolveMode(hass);
    if (key === this.key && mode === this.mode) return;
    const keyChanged = key !== this.key;
    this.key = key;
    this.mode = mode;
    const th = this.current();
    this.palette = mode === "light" ? lightPalette(th) : darkPalette(th);
    const root = document.documentElement.style;
    for (const v of PALETTE_VARS) {
      if (this.palette[v]) root.setProperty(`--k-${v}`, this.palette[v]);
      else root.removeProperty(`--k-${v}`);
    }
    Skin.set(skinCss(key, this.palette, mode === "light"));
    if (keyChanged) {
      this._particles(th);
      this._cameos(th);
    }
    Bars.update();
    window.dispatchEvent(new CustomEvent("kitchen-theme", { detail: key }));
  },
  /** Colors for confetti and tap sparks in the active theme. */
  confetti() {
    return this.current().confetti || ["#ff9f43", "#4cd17a", "#4fb3f0", "#f07a5f", "#ffd54a", "#a98bf0"];
  },
  // Now and then (every two to three minutes, only while the screen is on)
  // one of the theme's characters crosses the page: on the particle layer,
  // transform-only, never in the way of a tap.
  _cameos(th) {
    clearTimeout(this._camT);
    if (!th.cameo || REDUCED()) return;
    const next = (ms) =>
      (this._camT = setTimeout(() => {
        this._cameo(th);
        next(100000 + Math.random() * 80000);
      }, ms));
    next(20000 + Math.random() * 25000);
  },
  _cameo(th, pick) {
    const root = this._host?.shadowRoot;
    if (!root || !Bars.owners.size || document.visibilityState !== "visible") return;
    const c = pick || th.cameo[Math.floor(Math.random() * th.cameo.length)];
    if (c.kind === "fireworks") {
      for (let i = 0; i < 4; i++)
        setTimeout(() => FX.firework(innerWidth * (0.12 + Math.random() * 0.76), innerHeight * (0.1 + Math.random() * 0.3), th.confetti), i * 650 + Math.random() * 300);
      return;
    }
    const t = c.t || { fly: 9, walk: 16, hop: 13, peek: 6.5 }[c.kind] || 9;
    const el = document.createElement("div");
    el.className = `cam ${c.kind}`;
    el.style.cssText = [`--t:${t}s`, `--s:${c.s || 46}px`, `--y:${Math.round(8 + Math.random() * 28)}vh`,
      `--dy:${Math.round((Math.random() - 0.5) * 24)}vh`, `--x:${Math.round(6 + Math.random() * 78)}vw`].join(";");
    el.innerHTML = `<i>${c.e}</i>`;
    root.appendChild(el);
    setTimeout(() => el.remove(), t * 1000 + 300);
  },
  /** The page background of the active theme and mode (what the system bars get). */
  bg() {
    return this.palette?.bg || DARK_BG;
  },
  // A dozen slow emoji over the page. Only transform and opacity animate, so
  // the tablet's GPU does the work and the cards never re-layout. The layer
  // hangs off <body>, outside HA's panels, so it's shown only while the
  // Kitchen dashboard is on screen (a kitchen-pages-card is in Bars.owners);
  // on other HA pages it's hidden, which also stops its animations.
  showFx() {
    if (this._host) this._host.style.display = Bars.owners.size ? "" : "none";
  },
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
    this.showFx();
  },
};

// ------------------------------------------------------ dark/light mode

// Dark, light or auto (dark while sun.sun is below the horizon): the wall
// display's is input_select.kitchen_mode (packages/kitchen_display.yaml),
// everyone else's is their own (Prefs), auto until they pick. Dark is the look the themes
// were made in. Light keeps each theme's accent and derives the rest from it:
// near-white cards on a faintly tinted page, dark text, and the accent (and
// the "done" green) darkened where they're used as text until they pass WCAG
// AA (4.5:1) on the cards. Before the helper exists, the wall display stays dark.
const MODE_ENTITY = "input_select.kitchen_mode";
const MODES = [
  { key: "dark", label: "Dark", icon: "🌙" },
  { key: "light", label: "Light", icon: "☀️" },
  { key: "auto", label: "Auto", icon: "🌗" },
];
/** The mode setting: the wall display's input_select (dark until it exists), or this person's own (auto until they pick). */
const pickedMode = (hass) => (isWall() ? hass?.states?.[MODE_ENTITY]?.state || "dark" : Prefs.value?.mode || "auto");
function resolveMode(hass) {
  const m = pickedMode(hass);
  if (m === "dark" || m === "light") return m;
  if (m !== "auto") return "dark";
  return hass?.states?.["sun.sun"]?.state === "above_horizon" ? "light" : "dark";
}
const DARK_BG = "#111318";
const DEFAULT_ACCENT = "#ff9f43";
const DEFAULT_OK = "#4cd17a";
const PALETTE_VARS = ["accent", "ink", "bg", "card", "card2", "line", "ok", "txt", "sub", "acct", "okt", "danger",
  "slot0", "slot1", "slot2", "slot3", "scrim", "shadow"];
const rgbOf = (hex) => {
  const h = hex.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
};
const hexOf = (rgb) => "#" + rgb.map((x) => Math.round(Math.max(0, Math.min(255, x))).toString(16).padStart(2, "0")).join("");
/** `t` of color a over color b (0 = b, 1 = a). */
const mix = (a, b, t) => {
  const x = rgbOf(a), y = rgbOf(b);
  return hexOf(x.map((v, i) => v * t + y[i] * (1 - t)));
};
const luminance = (hex) => {
  const [r, g, b] = rgbOf(hex).map((v) => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a, b) => {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};
/** `fg` darkened (on a light `bg`) or lightened (on a dark one) until it reaches `ratio` on `bg`. */
function readable(fg, bg, ratio = 4.5) {
  const toward = luminance(bg) > 0.18 ? "#000000" : "#ffffff";
  let c = fg;
  for (let t = 0.96; contrast(c, bg) < ratio && t > 0; t -= 0.04) c = mix(fg, toward, t);
  return c;
}
/** Readable on every one of `bgs`. */
const readableOn = (fg, ...bgs) => bgs.reduce((c, bg) => readable(c, bg), fg);
function lightPalette(th) {
  const accent = th.vars.accent || DEFAULT_ACCENT;
  const ok = th.vars.ok || DEFAULT_OK;
  // The everyday theme stays neutral gray; holidays tint the page with their accent.
  // `tint` overrides the accent for that (Christmas: green pages, red accents).
  const tint = th === DEFAULT_THEME ? 0 : 1;
  const tc = th.tint || accent;
  const p = {
    accent,
    ok,
    bg: mix(tc, "#e9ecf0", 0.1 * tint),
    card: mix(tc, "#ffffff", 0.03 * tint),
    card2: mix(tc, "#eef0f3", 0.08 * tint),
    line: mix(tc, "#d3d8df", 0.14 * tint),
    txt: mix(tc, "#1b1e24", 0.06 * tint),
    danger: "#c62828",
    slot0: "#f6eccd", slot1: "#d8eaf5", slot2: "#f4dbe9", slot3: "#e3def5",
    scrim: "rgba(20,24,32,.38)",
    shadow: "0 1px 2px rgba(20,24,32,.07)",
  };
  if (th.vars.ink) p.ink = th.vars.ink;
  p.sub = readableOn(mix(tc, "#5d636e", 0.08 * tint), p.card2, p.bg);
  p.acct = readableOn(accent, p.card2, p.bg);
  p.okt = readableOn(ok, p.card2, p.bg);
  return p;
}
/** Dark mode is each theme's own palette; only an accent too dark to read as text gets a lighter text shade. */
function darkPalette(th) {
  const p = { ...th.vars };
  const card2 = th.vars.card2 || "#252932";
  const acct = readable(th.vars.accent || DEFAULT_ACCENT, card2);
  if (acct !== (th.vars.accent || DEFAULT_ACCENT)) p.acct = acct;
  return p;
}

// ----------------------------------------------------------------- skins

// A holiday dresses the whole display. The page becomes a scene (SCENES
// below: a sky behind the header, a landscape along the bottom) and the cards
// float over it: a touch translucent, lit from the top, with a rim highlight,
// a soft shadow and a hairline in the theme's color. Each theme adds its own
// signature on top (Christmas snow and holly, Halloween drips and a cobweb, a
// gold, rainbow, flag, pastel, lace or harvest edge), and the active tab
// becomes a lit pill in the theme's color. It's all one stylesheet
// (Skin.sheet) that every card's shadow root adopts, rebuilt from the palette
// when the theme or mode changes, so light mode gets its own shades. Static
// throughout: nothing on the cards animates or re-lays out on the tablet.
const alpha = (hex, a) => `rgba(${rgbOf(hex).join(",")},${a})`;
const svgUrl = (w, h, body) =>
  `url("data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${body}</svg>`)}")`;
const tf = (x, y, rot = 0, s = 1) => `transform="translate(${x.toFixed(1)} ${y.toFixed(1)}) rotate(${rot.toFixed(0)}) scale(${s.toFixed(2)})"`;
// About 14px across at scale 1, centered on 0,0. The heart's tip is at 0,7.
const HEART = "M0 -2.5C-1.8 -6 -7 -5.2 -7 -1.4C-7 2 -3 4.4 0 7C3 4.4 7 2 7 -1.4C7 -5.2 1.8 -6 0 -2.5Z";
const BAT = "M0 -1C-1.5 -3 -4 -3 -5 -5C-5.2 -2.5 -7.5 -1.5 -10 -2C-8 0 -6.5 2 -3 1.5L0 3.5L3 1.5C6.5 2 8 0 10 -2C7.5 -1.5 5.2 -2.5 5 -5C4 -3 1.5 -3 0 -1Z";
/** A square tile of `n` shapes scattered on a jittered grid (seeded, so it looks the same on every render), at opacity `op`. */
function tile(size, n, op, seed, draw) {
  let s = seed;
  const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const k = Math.ceil(Math.sqrt(n)), cell = size / k;
  let body = "";
  for (let i = 0; i < n; i++) body += draw(((i % k) + 0.25 + rnd() * 0.5) * cell, (Math.floor(i / k) + 0.25 + rnd() * 0.5) * cell, i, rnd);
  return svgUrl(size, size, `<g opacity="${op}">${body}</g>`);
}
// Edges, repeated along the top of a card.
const EDGE = {
  snow: (fill, stroke) =>
    svgUrl(60, 14, `<path fill="${fill}"${stroke ? ` stroke="${stroke}" stroke-width="1"` : ""} d="M0 0H60V7C56 7 55 12 49 12S42 7 37 8S29 13 23 11S15 6 9 9S3 10 0 8Z"/>`),
  lace: (c, op) => svgUrl(20, 11, `<g fill="${c}" opacity="${op}"><path d="M0 0H20V2A10 9 0 0 1 0 2Z"/><circle cx="10" cy="5" r="1.6" fill="#000" fill-opacity=".25"/></g>`),
  drip: (c, op) =>
    svgUrl(70, 18, `<path fill="${c}" opacity="${op}" d="M0 0H70V4C64 4 63 5 62 9C61 14 57 14 56 9C55 5 52 4 46 4C40 4 40 6 39 12C38 17 34 17 33 12C32 6 30 4 22 4C16 4 15 5 14 7C13 10 10 10 9 7C8 5 6 4 0 4Z"/>`),
};
// Corner ornaments.
const ORN = {
  holly: (leaf, berry) => {
    const l = "M-16 0C-12 -3 -11 -7 -8 -5C-5 -8 -2 -9 0 -6C2 -9 5 -8 8 -5C11 -7 12 -3 16 0C12 3 11 7 8 5C5 8 2 9 0 6C-2 9 -5 8 -8 5C-11 7 -12 3 -16 0Z";
    return svgUrl(60, 42, `<path fill="${leaf}" transform="translate(19 17) rotate(-28)" d="${l}"/><path fill="${leaf}" transform="translate(41 17) rotate(28)" d="${l}"/>
      <g fill="${berry}"><circle cx="30" cy="26" r="4.5"/><circle cx="24.5" cy="30" r="4.2"/><circle cx="35.5" cy="30" r="4.2"/></g>
      <g fill="#ffffff" fill-opacity=".55"><circle cx="28.6" cy="24.6" r="1.2"/><circle cx="23.2" cy="28.6" r="1.1"/><circle cx="34.2" cy="28.6" r="1.1"/></g>`);
  },
  // A web strung across the top-right corner, with its spider.
  web: (c, op) => {
    const W = 100, pt = (r, a) => [W - Math.cos(a) * r, Math.sin(a) * r];
    const angs = Array.from({ length: 6 }, (_, i) => (i / 5) * (Math.PI / 2));
    const f = (v) => v.toFixed(1);
    let d = angs.map((a) => `M${W} 0L${pt(W, a).map(f).join(" ")}`).join("");
    for (const r of [17, 34, 54, 76]) {
      angs.forEach((a, i) => {
        const [x, y] = pt(r, a);
        if (!i) d += `M${f(x)} ${f(y)}`;
        else d += `Q${pt(r * 0.8, (a + angs[i - 1]) / 2).map(f).join(" ")} ${f(x)} ${f(y)}`;
      });
    }
    return svgUrl(W, W, `<g opacity="${op}" stroke="${c}" fill="none" stroke-width="1"><path d="${d}"/><path d="M58 0V60"/>
      <path d="M52 58L55 62L52 67M64 58L61 62L64 67M51 62H65M53 70L56 66M63 70L60 66" stroke-width="1.3"/><ellipse cx="58" cy="64" rx="3.4" ry="4.4" fill="${c}"/></g>`);
  },
};
// A background layer: [image, position, size, repeat].
const layer = {
  edge: (colors, h) => [`linear-gradient(90deg, ${colors.join(", ")})`, "0 0", `100% ${h}px`, "no-repeat"],
  strip: (img, w, h) => [img, "0 0", `${w}px ${h}px`, "repeat-x"],
  pattern: (img, size) => [img, "0 0", `${size}px ${size}px`, "repeat"],
  corner: (img, pos, size) => [img, pos, size, "no-repeat"],
};
const PASTEL = ["#f7a8c4", "#ffe08a", "#a8e6c1", "#a8d2f7", "#cdb4f6"];
const RAINBOW = ["#e5484d", "#ff9f43", "#ffd54a", "#3ccf6e", "#4f8cff", "#a98bf0"];
// Per theme: (palette, light) => { layers (signature, first on top), frame, css }.
const SKINS = {
  newyear: (p, light) => ({
    layers: [layer.edge(["#b8913a", "#fff3c4", "#e9c46a", "#fff3c4", "#b8913a"], 3)],
    frame: alpha("#e9c46a", light ? 0.5 : 0.3),
  }),
  valentines: (p, light) => ({
    layers: [layer.strip(EDGE.lace(light ? "#f3a7bf" : "#ff9ab8", light ? 0.6 : 0.32), 20, 11)],
  }),
  stpatricks: () => ({ layers: [layer.edge(RAINBOW, 3)] }),
  easter: (p, light) => ({ layers: [layer.edge(PASTEL, 3)], frame: alpha("#cdb4f6", light ? 0.55 : 0.28) }),
  july4: () => ({ layers: [layer.edge(["#e5484d 0 33%", "#f4f4f4 0 66%", "#4f8cff 0"], 3)] }),
  halloween: (p, light) => ({
    layers: [
      layer.strip(EDGE.drip("#8e4fc4", light ? 0.45 : 0.75), 47, 12),
      layer.corner(ORN.web(light ? "#3a2d48" : "#ffffff", light ? 0.3 : 0.2), "right 0 top 0", "100px 100px"),
      layer.pattern(tile(170, 4, light ? 0.1 : 0.12, 13, (x, y, i, r) => `<path ${tf(x, y, (r() - 0.5) * 30, 0.9 + r() * 0.5)} fill="${light ? "#3a2d48" : "#a86cf0"}" d="${BAT}"/>`), 170),
    ],
    frame: alpha("#8e4fc4", 0.45),
  }),
  thanksgiving: () => ({ layers: [layer.edge(["#a8452f", "#e08a3c", "#f3c76a", "#e08a3c", "#a8452f"], 3)] }),
  christmas: (p, light) => ({
    layers: [
      layer.corner(ORN.holly(light ? "#2f9e57" : alpha("#2f9e57", 0.8), light ? "#e5484d" : alpha("#e5484d", 0.9)), "50% 3px", "34px 24px"),
      layer.strip(EDGE.snow(light ? "#ffffff" : "#f4f8fb", light ? "#cfd9e2" : null), 60, 12),
    ],
    frame: alpha("#3fbf6a", 0.32),
    css: `
      .rk .bar div, .wd .bar div { background-image:repeating-linear-gradient(-45deg, rgba(255,255,255,.5) 0 6px, transparent 6px 12px) !important; }
      .pill { background-image:repeating-linear-gradient(-45deg, rgba(255,255,255,.2) 0 9px, transparent 9px 18px), ${pillGradient(p.accent)}; }`,
  }),
};
const pillGradient = (a) => `linear-gradient(135deg, ${mix("#ffffff", a, 0.3)}, ${a} 55%, ${mix("#000000", a, 0.12)})`;
/** The skin stylesheet for a theme, or "" for the everyday look. */
function skinCss(key, p, light) {
  const make = SKINS[key];
  if (!make) return "";
  const s = make(p, light);
  const a = p.accent;
  // The signature layers, then the light falling on the card from the top.
  // The small house tiles get the light only; the signature is for the big cards.
  const glow = [`linear-gradient(180deg, ${alpha(a, light ? 0.07 : 0.1)}, transparent 42%)`, "0 0", "auto", "no-repeat"];
  const bg = (L) => `background-image:${L.map((l) => l[0]).join(", ")}; background-position:${L.map((l) => l[1]).join(", ")}; background-size:${L.map((l) => l[2]).join(", ")}; background-repeat:${L.map((l) => l[3]).join(", ")};`;
  const surf = bg([...(s.layers || []), glow]);
  const frame = s.frame || alpha(a, light ? 0.4 : 0.24);
  const lift = light ? "0 6px 22px rgba(40,30,20,.1), 0 1px 2px rgba(40,30,20,.06)" : "0 10px 30px rgba(0,0,0,.4), 0 2px 6px rgba(0,0,0,.25)";
  const rim = `inset 0 1px 0 rgba(255,255,255,${light ? 0.75 : 0.07})`;
  return `
    .card, .rk, .rc, .ad, .house > .chip { background-color:color-mix(in srgb, var(--card) ${light ? 90 : 86}%, transparent); ${surf}
      box-shadow:${lift}, ${rim}, inset 0 0 0 1px ${frame}; }
    .house > .chip { ${bg([glow])} }
    .wrap > .sh { ${surf} box-shadow:0 18px 50px rgba(0,0,0,.45), ${rim}, inset 0 0 0 1px ${frame}; }
    .tab { background-color:color-mix(in srgb, var(--card) 82%, transparent); box-shadow:${rim}, inset 0 0 0 1px ${alpha(a, light ? 0.22 : 0.14)}; }
    .pill { background-image:${pillGradient(a)}; box-shadow:0 4px 16px ${alpha(a, light ? 0.3 : 0.4)}, inset 0 1px 0 rgba(255,255,255,.35); }
    .h { color:var(--acct); }
    ${s.css || ""}`;
}
/** One stylesheet every card's shadow root adopts, swapped in place when the theme changes. */
const Skin = {
  sheet: (() => {
    try {
      return new CSSStyleSheet();
    } catch {
      return null; // no constructable stylesheets: the cards just stay plain
    }
  })(),
  adopt(root) {
    if (this.sheet && root && !root.adoptedStyleSheets.includes(this.sheet)) root.adoptedStyleSheets = [...root.adoptedStyleSheets, this.sheet];
  },
  set(css) {
    if (!this.sheet || css === this._css) return;
    this._css = css;
    this.sheet.replaceSync(css);
  },
};

// ---------------------------------------------------------------- scenes

// The page behind the cards, per holiday: a sky behind the clock (a moon, an
// aurora, a rainbow, stars, bokeh, fireworks) and a landscape along the
// bottom (hills in three depths, trees, a barn, a snowy cabin, a haunted
// house, a skyline, pumpkins, tulips), in a dark and a light palette. Drawn
// procedurally into two inline SVGs at the page's real width (seeded, so it
// looks the same every time), so it stays crisp and costs one paint. The
// walking and hopping cameos cross the bottom of it.
const SKY_H = 380, LAND_H = 300;
const f1 = (v) => (+v).toFixed(1);
const seeded = (seed) => {
  let s = seed;
  return () => (s = (s * 16807) % 2147483647) / 2147483647;
};
/** Ridge points across the width: [x, y] every `step` px around `base`, up to `amp` above or below. */
function ridge(r, W, base, amp, step) {
  const pts = [];
  for (let x = -60; x < W + 60 + step; x += step) pts.push([x, base + (r() - 0.5) * 2 * amp]);
  return pts;
}
/** The ridge's height at x, eased between its points like the drawn curve. */
function ridgeY(pts, x) {
  for (let i = 0; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1];
    if (x <= x1) {
      const t = Math.max(0, (x - x0) / (x1 - x0));
      return y0 + (y1 - y0) * (3 * t * t - 2 * t * t * t);
    }
  }
  return pts[pts.length - 1][1];
}
// The scene being drawn (light or dark) and a counter for its gradient ids.
// Light comes from above: by day white, at night a warm moonlight.
const SC = { light: false, n: 0 };
const lit = (c, k) => mix(SC.light ? "#ffffff" : "#ffe6c4", c, k);
const shade = (c, k) => mix("#000000", c, k);
/** A vertical (or horizontal) gradient: [id, <linearGradient>]. Stops are [offset, color, opacity]. */
function grad(stops, horizontal) {
  const id = `sg${++SC.n}`;
  return [id, `<linearGradient id="${id}" x1="0" y1="0" x2="${horizontal ? 1 : 0}" y2="${horizontal ? 0 : 1}">${stops
    .map(([o, c, a = 1]) => `<stop offset="${o}" stop-color="${c}" stop-opacity="${a}"/>`).join("")}</linearGradient>`];
}
/** A hill: lit along its ridge, darkening toward its foot, with a thin bright rim where the light catches it. */
function hill(pts, H, fill) {
  let d = `M${f1(pts[0][0])} ${f1(pts[0][1])}`;
  for (let i = 1; i < pts.length; i++) {
    const [x0, y0] = pts[i - 1], [x1, y1] = pts[i], h = (x1 - x0) / 2;
    d += `C${f1(x0 + h)} ${f1(y0)} ${f1(x1 - h)} ${f1(y1)} ${f1(x1)} ${f1(y1)}`;
  }
  const [id, def] = grad([[0, lit(fill, SC.light ? 0.2 : 0.1)], [0.3, fill], [1, shade(fill, SC.light ? 0.1 : 0.35)]]);
  return `<defs>${def}</defs><path d="${d}V${H}H${f1(pts[0][0])}Z" fill="url(#${id})"/>
    <path d="${d}" fill="none" stroke="${lit(fill, SC.light ? 0.55 : 0.22)}" stroke-width="1.2" opacity=".75"/>`;
}
/** A band of mist at y between two layers of hills, so the far ones sit back. */
function fog(W, y, h, color, op) {
  const [id, def] = grad([[0, color, 0], [0.55, color, op], [1, color, 0]]);
  return `<defs>${def}</defs><rect x="-10" y="${f1(y - h / 2)}" width="${W + 20}" height="${f1(h)}" fill="url(#${id})"/>`;
}
/** Warm light spilling around a lit window or a jack-o'-lantern: a soft radial falloff, drawn behind it. */
function glowAt(x, y, rx, ry, color, op) {
  const id = `sg${++SC.n}`;
  return `<defs><radialGradient id="${id}"><stop offset="0" stop-color="${color}" stop-opacity="${op}"/><stop offset=".45" stop-color="${color}" stop-opacity="${f1(op * 0.35)}"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></radialGradient></defs>
    <ellipse cx="${f1(x)}" cy="${f1(y)}" rx="${f1(rx)}" ry="${f1(ry)}" fill="url(#${id})"/>`;
}
/** Positions across the width at fractions `fs`, nudged by up to `j` px. */
const spots = (r, W, fs, j = 24) => fs.map((f) => W * f + (r() - 0.5) * 2 * j);
// Props, drawn standing on the ground at y.
const ART = {
  pine(x, y, h, c, snow) {
    const w = h * 0.44;
    let s = `<rect x="${f1(x - h * 0.04)}" y="${f1(y - h * 0.12)}" width="${f1(h * 0.08)}" height="${f1(h * 0.14)}" fill="${c}"/>`;
    for (const [a, b, k] of [[0, 0.46, 0.5], [0.2, 0.72, 0.76], [0.42, 0.94, 1]]) {
      const top = y - h + a * h, bot = y - h + b * h, hw = w * k;
      s += `<path d="M${f1(x)} ${f1(top)}L${f1(x + hw)} ${f1(bot)}Q${f1(x)} ${f1(bot - h * 0.05)} ${f1(x - hw)} ${f1(bot)}Z" fill="${c}"/>
        <path d="M${f1(x)} ${f1(top)}L${f1(x + hw)} ${f1(bot)}Q${f1(x + hw * 0.5)} ${f1(bot - h * 0.03)} ${f1(x)} ${f1(bot - h * 0.05)}Z" fill="${shade(c, 0.3)}"/>`;
      if (snow) {
        const sy = top + (bot - top) * 0.45, sx = hw * 0.45;
        s += `<path d="M${f1(x)} ${f1(top)}L${f1(x + sx)} ${f1(sy)}Q${f1(x + sx * 0.4)} ${f1(sy - 3)} ${f1(x)} ${f1(sy + 1)}Q${f1(x - sx * 0.4)} ${f1(sy - 3)} ${f1(x - sx)} ${f1(sy)}Z" fill="${snow}"/>`;
      }
    }
    return s;
  },
  tree(x, y, h, cs, trunk) {
    // Crowns shaded underneath, then lit on top, so the tree reads round.
    const crowns = [[0.02, -0.5, 0.2], [-0.21, -0.58, 0.2], [0.22, -0.6, 0.21], [0, -0.74, 0.26]];
    const col = (i) => cs[i % cs.length];
    return `<path d="M${f1(x - h * 0.035)} ${f1(y)}L${f1(x - h * 0.018)} ${f1(y - h * 0.58)}H${f1(x + h * 0.018)}L${f1(x + h * 0.035)} ${f1(y)}Z" fill="${trunk}"/>` +
      crowns.map(([dx, dy, rr], i) => `<circle cx="${f1(x + dx * h)}" cy="${f1(y + dy * h)}" r="${f1(rr * h)}" fill="${shade(col(i), 0.22)}"/>`).join("") +
      crowns.map(([dx, dy, rr], i) => `<circle cx="${f1(x + dx * h - rr * h * 0.12)}" cy="${f1(y + dy * h - rr * h * 0.14)}" r="${f1(rr * h * 0.86)}" fill="${col(i)}"/>`).join("") +
      crowns.map(([dx, dy, rr], i) => `<circle cx="${f1(x + dx * h - rr * h * 0.32)}" cy="${f1(y + dy * h - rr * h * 0.36)}" r="${f1(rr * h * 0.42)}" fill="${lit(col(i), 0.3)}" opacity="${SC.light ? 0.4 : 0.7}"/>`).join("");
  },
  // Bare and crooked: every branch bends a little and splits in two, four times over.
  gnarled(x, y, h, c, r) {
    let d = "";
    const branch = (x0, y0, len, ang, w, depth) => {
      const x1 = x0 + Math.cos(ang) * len, y1 = y0 - Math.sin(ang) * len, bend = (r() - 0.5) * len * 0.5;
      const cx = (x0 + x1) / 2 - Math.sin(ang) * bend, cy = (y0 + y1) / 2 - Math.cos(ang) * bend;
      d += `<path d="M${f1(x0)} ${f1(y0)}Q${f1(cx)} ${f1(cy)} ${f1(x1)} ${f1(y1)}" stroke-width="${f1(w)}"/>`;
      if (depth > 0) {
        branch(x1, y1, len * (0.62 + r() * 0.16), ang + 0.32 + r() * 0.48, w * 0.62, depth - 1);
        branch(x1, y1, len * (0.58 + r() * 0.16), ang - 0.32 - r() * 0.48, w * 0.62, depth - 1);
      }
    };
    branch(x, y + 2, h * 0.4, Math.PI / 2 + (r() - 0.5) * 0.25, h * 0.08, 4);
    return `<g stroke="${c}" stroke-linecap="round" fill="none">${d}</g>`;
  },
  barn(x, y, w, h, wall, roof, trim, win) {
    const l = x - w / 2, rt = x + w / 2, eave = y - h * 0.6;
    const outline = `M${f1(l)} ${f1(eave)}L${f1(l + w * 0.17)} ${f1(y - h * 0.88)}L${f1(x)} ${f1(y - h)}L${f1(rt - w * 0.17)} ${f1(y - h * 0.88)}L${f1(rt)} ${f1(eave)}`;
    const dw = w * 0.3, dt = y - h * 0.44;
    return `<path d="M${f1(l)} ${f1(y)}V${f1(eave)}${outline.replace(/^M[^L]+/, "")}V${f1(y)}Z" fill="${wall}"/>
      <path d="${outline}" fill="none" stroke="${roof}" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>
      <path d="M${f1(x - dw / 2)} ${f1(dt)}H${f1(x + dw / 2)}V${f1(y)}H${f1(x - dw / 2)}ZM${f1(x - dw / 2)} ${f1(dt)}L${f1(x + dw / 2)} ${f1(y)}M${f1(x + dw / 2)} ${f1(dt)}L${f1(x - dw / 2)} ${f1(y)}" fill="none" stroke="${trim}" stroke-width="1.5"/>
      <rect x="${f1(x - w * 0.07)}" y="${f1(y - h * 0.8)}" width="${f1(w * 0.14)}" height="${f1(h * 0.13)}" fill="${win}"/>`;
  },
  cabin(x, y, w, h, wall, roof, win, snow, smoke) {
    const l = x - w / 2, rt = x + w / 2, eave = y - h * 0.55, cx = rt - w * 0.26;
    const winR = (wx) => `<rect x="${f1(wx)}" y="${f1(y - h * 0.42)}" width="${f1(w * 0.16)}" height="${f1(h * 0.2)}" fill="${win}"/>`;
    return `${SC.light ? "" : glowAt(x, y - h * 0.3, w * 1.3, h * 0.8, win, 0.22)}
      ${smoke ? `<path d="M${f1(cx + w * 0.05)} ${f1(y - h * 1.1)}c-6 -8 6 -12 0 -20s6 -12 0 -20" fill="none" stroke="${smoke}" stroke-width="4" stroke-linecap="round" opacity=".35"/>` : ""}
      <rect x="${f1(cx)}" y="${f1(y - h * 1.05)}" width="${f1(w * 0.1)}" height="${f1(h * 0.42)}" fill="${roof}"/>
      <rect x="${f1(l)}" y="${f1(eave)}" width="${f1(w)}" height="${f1(y - eave)}" fill="${wall}"/>
      <path d="M${f1(l - w * 0.1)} ${f1(eave + 1)}L${f1(x)} ${f1(y - h)}L${f1(rt + w * 0.1)} ${f1(eave + 1)}Z" fill="${roof}"/>
      ${snow ? `<path d="M${f1(l - w * 0.1)} ${f1(eave + 1)}L${f1(x)} ${f1(y - h)}L${f1(rt + w * 0.1)} ${f1(eave + 1)}" fill="none" stroke="${snow}" stroke-width="4.5" stroke-linejoin="round" stroke-linecap="round"/>` : ""}
      ${winR(l + w * 0.12)}${winR(rt - w * 0.28)}<rect x="${f1(x - w * 0.07)}" y="${f1(y - h * 0.36)}" width="${f1(w * 0.14)}" height="${f1(h * 0.36)}" fill="${roof}"/>`;
  },
  haunted(x, y, w, h, c, win) {
    const l = x - w / 2, mr = x + w * 0.12, tl = mr - 2, tr = x + w / 2, body = y - h * 0.5;
    const win1 = (wx, wy, ww = w * 0.09, wh = h * 0.12) => `<rect x="${f1(wx)}" y="${f1(wy)}" width="${f1(ww)}" height="${f1(wh)}" fill="${win}"/>`;
    return `<rect x="${f1(l)}" y="${f1(body)}" width="${f1(mr - l)}" height="${f1(y - body)}" fill="${c}"/>
      <path d="M${f1(l - w * 0.06)} ${f1(body + 1)}L${f1((l + mr) / 2 - w * 0.04)} ${f1(y - h * 0.82)}L${f1(mr + w * 0.04)} ${f1(body + 1)}Z" fill="${c}"/>
      <rect x="${f1(l + w * 0.1)}" y="${f1(y - h * 0.82)}" width="${f1(w * 0.06)}" height="${f1(h * 0.2)}" fill="${c}" transform="rotate(-6 ${f1(l + w * 0.13)} ${f1(y - h * 0.72)})"/>
      <rect x="${f1(tl)}" y="${f1(y - h * 0.86)}" width="${f1(tr - tl)}" height="${f1(h * 0.86)}" fill="${c}"/>
      <path d="M${f1(tl - w * 0.04)} ${f1(y - h * 0.85)}L${f1((tl + tr) / 2)} ${f1(y - h * 1.22)}L${f1(tr + w * 0.04)} ${f1(y - h * 0.85)}Z" fill="${c}"/>
      <path d="M${f1((tl + tr) / 2)} ${f1(y - h * 1.22)}V${f1(y - h * 1.34)}" stroke="${c}" stroke-width="1.5"/>
      <circle cx="${f1((tl + tr) / 2)}" cy="${f1(y - h * 0.68)}" r="${f1(w * 0.05)}" fill="${win}"/>
      ${win1(l + w * 0.1, body + h * 0.12)}${win1(l + w * 0.32, body + h * 0.12)}${win1((tl + tr) / 2 - w * 0.045, y - h * 0.42)}
      <rect x="${f1(l + w * 0.2)}" y="${f1(y - h * 0.2)}" width="${f1(w * 0.1)}" height="${f1(h * 0.2)}" fill="${win}" opacity=".55"/>`;
  },
  skyline(r, W, y, minH, maxH, c, win, lit) {
    let s = "";
    for (let x = -8; x < W + 8; ) {
      const bw = 24 + r() * 44, bh = minH + r() * (maxH - minH);
      s += `<rect x="${f1(x)}" y="${f1(y - bh)}" width="${f1(bw + 1)}" height="${f1(bh + 2)}" fill="${c}"/>`;
      if (r() < 0.22) s += `<path d="M${f1(x + bw / 2)} ${f1(y - bh)}V${f1(y - bh - 14)}" stroke="${c}" stroke-width="2"/>`;
      for (let wy = y - bh + 8; wy < y - 10; wy += 11) for (let wx = x + 5; wx < x + bw - 7; wx += 9) if (r() < lit) s += `<rect x="${f1(wx)}" y="${f1(wy)}" width="4" height="5" fill="${win}"/>`;
      x += bw + 1 + r() * 5;
    }
    return s;
  },
  clockTower(x, y, h, c, face) {
    const w = h * 0.2, cy = y - h * 0.68, cr = w * 0.36;
    return `<rect x="${f1(x - w / 2)}" y="${f1(y - h * 0.82)}" width="${f1(w)}" height="${f1(h * 0.82)}" fill="${c}"/>
      <rect x="${f1(x - w * 0.62)}" y="${f1(y - h * 0.84)}" width="${f1(w * 1.24)}" height="${f1(h * 0.04)}" fill="${c}"/>
      <path d="M${f1(x - w * 0.5)} ${f1(y - h * 0.84)}L${f1(x)} ${f1(y - h)}L${f1(x + w * 0.5)} ${f1(y - h * 0.84)}Z" fill="${c}"/>
      <circle cx="${f1(x)}" cy="${f1(cy)}" r="${f1(cr)}" fill="${face}"/>
      <path d="M${f1(x)} ${f1(cy)}V${f1(cy - cr * 0.75)}M${f1(x)} ${f1(cy)}L${f1(x - cr * 0.12)} ${f1(cy - cr * 0.5)}" stroke="${c}" stroke-width="1.6" stroke-linecap="round"/>`;
  },
  pumpkin(x, y, s, body, stem, face) {
    const e = (dx, rx) => `<ellipse cx="${f1(x + dx * s)}" cy="${f1(y - s * 0.7)}" rx="${f1(rx * s)}" ry="${f1(s * 0.7)}" fill="${body}" stroke="#000" stroke-opacity=".14"/>`;
    let g = (face && !SC.light ? glowAt(x, y - s * 0.6, s * 3.2, s * 1.8, face, 0.3) : "") +
      `<path d="M${f1(x)} ${f1(y - s * 1.3)}q${f1(s * 0.06)} ${f1(-s * 0.28)} ${f1(s * 0.26)} ${f1(-s * 0.34)}" stroke="${stem}" stroke-width="${f1(s * 0.16)}" stroke-linecap="round" fill="none"/>` + e(-0.42, 0.6) + e(0.42, 0.6) + e(0, 0.64);
    if (face) {
      const ey = y - s * 0.86, my = y - s * 0.46;
      g += `<path d="M${f1(x - s * 0.42)} ${f1(ey + s * 0.16)}L${f1(x - s * 0.24)} ${f1(ey - s * 0.12)}L${f1(x - s * 0.08)} ${f1(ey + s * 0.16)}ZM${f1(x + s * 0.08)} ${f1(ey + s * 0.16)}L${f1(x + s * 0.24)} ${f1(ey - s * 0.12)}L${f1(x + s * 0.42)} ${f1(ey + s * 0.16)}Z
        M${f1(x - s * 0.46)} ${f1(my - s * 0.08)}Q${f1(x)} ${f1(my + s * 0.34)} ${f1(x + s * 0.46)} ${f1(my - s * 0.08)}L${f1(x + s * 0.26)} ${f1(my + s * 0.04)}L${f1(x + s * 0.12)} ${f1(my - s * 0.06)}L${f1(x)} ${f1(my + s * 0.06)}L${f1(x - s * 0.12)} ${f1(my - s * 0.06)}L${f1(x - s * 0.26)} ${f1(my + s * 0.04)}Z" fill="${face}"/>`;
    }
    return g;
  },
  hay(x, y, w, h, c, line) {
    return `<rect x="${f1(x - w / 2)}" y="${f1(y - h)}" width="${f1(w)}" height="${f1(h)}" rx="${f1(h * 0.22)}" fill="${c}"/>
      <path d="M${f1(x - w / 2 + 3)} ${f1(y - h * 0.66)}H${f1(x + w / 2 - 3)}M${f1(x - w / 2 + 3)} ${f1(y - h * 0.33)}H${f1(x + w / 2 - 3)}" stroke="${line}" stroke-width="1.4" opacity=".6"/>`;
  },
  stone(x, y, s, c, cross) {
    if (cross) return `<path d="M${f1(x - s * 0.14)} ${f1(y)}V${f1(y - s * 1.1)}H${f1(x - s * 0.5)}V${f1(y - s * 1.36)}H${f1(x - s * 0.14)}V${f1(y - s * 1.7)}H${f1(x + s * 0.14)}V${f1(y - s * 1.36)}H${f1(x + s * 0.5)}V${f1(y - s * 1.1)}H${f1(x + s * 0.14)}V${f1(y)}Z" fill="${c}"/>`;
    return `<path d="M${f1(x - s * 0.5)} ${f1(y)}V${f1(y - s)}A${f1(s * 0.5)} ${f1(s * 0.5)} 0 0 1 ${f1(x + s * 0.5)} ${f1(y - s)}V${f1(y)}Z" fill="${c}" transform="rotate(${f1((x * 7) % 9 - 4)} ${f1(x)} ${f1(y)})"/>`;
  },
  tulip(x, y, h, c, stem) {
    const t = y - h;
    return `<path d="M${f1(x)} ${f1(y)}Q${f1(x + 2)} ${f1(y - h * 0.5)} ${f1(x)} ${f1(t)}" stroke="${stem}" stroke-width="1.6" fill="none"/>
      <path d="M${f1(x)} ${f1(y - 2)}Q${f1(x - 9)} ${f1(y - h * 0.4)} ${f1(x - 2)} ${f1(y - h * 0.6)}Q${f1(x - 2)} ${f1(y - h * 0.3)} ${f1(x)} ${f1(y - 2)}Z" fill="${stem}"/>
      <path d="M${f1(x - 5)} ${f1(t)}L${f1(x - 5.5)} ${f1(t - 8)}L${f1(x - 2.2)} ${f1(t - 4.5)}L${f1(x)} ${f1(t - 9)}L${f1(x + 2.2)} ${f1(t - 4.5)}L${f1(x + 5.5)} ${f1(t - 8)}L${f1(x + 5)} ${f1(t)}Q${f1(x)} ${f1(t + 5)} ${f1(x - 5)} ${f1(t)}Z" fill="${c}"/>`;
  },
  egg(x, y, s, c) {
    return `<ellipse cx="${f1(x)}" cy="${f1(y - s * 0.62)}" rx="${f1(s * 0.48)}" ry="${f1(s * 0.62)}" fill="${c}"/>
      <path d="M${f1(x - s * 0.46)} ${f1(y - s * 0.62)}l${f1(s * 0.23)} ${f1(-s * 0.16)}l${f1(s * 0.23)} ${f1(s * 0.16)}l${f1(s * 0.23)} ${f1(-s * 0.16)}l${f1(s * 0.23)} ${f1(s * 0.16)}" stroke="#fff" stroke-opacity=".7" stroke-width="1.3" fill="none"/>`;
  },
  potOfGold(x, y, s, gold) {
    let coins = "";
    for (const [dx, dy] of [[-0.42, 0], [-0.14, -0.1], [0.16, -0.06], [0.42, 0], [-0.28, -0.26], [0.02, -0.32], [0.3, -0.24]])
      coins += `<circle cx="${f1(x + dx * s)}" cy="${f1(y - s * 0.95 + dy * s)}" r="${f1(s * 0.2)}" fill="${gold}" stroke="#b8913a" stroke-width="1"/>`;
    return coins + `<path d="M${f1(x - s * 0.62)} ${f1(y - s * 0.95)}Q${f1(x - s * 0.8)} ${f1(y)} ${f1(x - s * 0.3)} ${f1(y)}H${f1(x + s * 0.3)}Q${f1(x + s * 0.8)} ${f1(y)} ${f1(x + s * 0.62)} ${f1(y - s * 0.95)}Z" fill="#141414"/>
      <rect x="${f1(x - s * 0.7)}" y="${f1(y - s * 1.02)}" width="${f1(s * 1.4)}" height="${f1(s * 0.16)}" rx="${f1(s * 0.08)}" fill="#222"/>`;
  },
  flag(x, y, h) {
    const fw = h * 0.5, fh = h * 0.3, top = y - h;
    let s = `<path d="M${f1(x)} ${f1(y)}V${f1(top - 3)}" stroke="#9aa4b5" stroke-width="2"/>`;
    for (let i = 0; i < 7; i++) s += `<rect x="${f1(x + 1)}" y="${f1(top + (fh / 7) * i)}" width="${f1(fw)}" height="${f1(fh / 7 + 0.3)}" fill="${i % 2 ? "#f4f4f4" : "#e5484d"}"/>`;
    return s + `<rect x="${f1(x + 1)}" y="${f1(top)}" width="${f1(fw * 0.42)}" height="${f1((fh / 7) * 4)}" fill="#3c5bbf"/>`;
  },
  cloud(x, y, s, c, op) {
    return `<g fill="${c}" opacity="${op}">${[[0, 0, 1], [-0.9, 0.25, 0.7], [0.95, 0.2, 0.75], [-0.4, -0.35, 0.7], [0.45, -0.3, 0.65]]
      .map(([dx, dy, rr]) => `<circle cx="${f1(x + dx * s)}" cy="${f1(y + dy * s)}" r="${f1(rr * s)}"/>`).join("")}</g>`;
  },
  heart: (x, y, s, c, op, rot = 0) => `<path ${tf(x, y, rot, s)} d="${HEART}" fill="${c}" opacity="${op}"/>`,
  // A tree whose crown is a heart, shaded and lit like the round ones.
  heartTree(x, y, h, c, trunk) {
    const s = h * 0.05, cy = y - h * 0.66;
    return `<path d="M${f1(x - h * 0.03)} ${f1(y)}Q${f1(x - h * 0.01)} ${f1(y - h * 0.3)} ${f1(x - h * 0.012)} ${f1(cy)}H${f1(x + h * 0.012)}Q${f1(x + h * 0.02)} ${f1(y - h * 0.3)} ${f1(x + h * 0.03)} ${f1(y)}Z" fill="${trunk}"/>
      <path ${tf(x + s * 0.6, cy + s * 0.5, 0, s)} d="${HEART}" fill="${shade(c, 0.25)}"/><path ${tf(x, cy, 0, s * 0.94)} d="${HEART}" fill="${c}"/>
      <path ${tf(x - s * 2.4, cy - s * 1.6, -20, s * 0.32)} d="${HEART}" fill="${lit(c, 0.35)}" opacity=".6"/>`;
  },
  // A heart balloon on a string, tied to the ground at x, y.
  balloon(x, y, s, c, string) {
    const bx = x + s * 0.8, by = y - s * 9;
    return `<path d="M${f1(x)} ${f1(y)}C${f1(x + s * 1.5)} ${f1(y - s * 3)} ${f1(bx - s * 1.2)} ${f1(by + s * 4)} ${f1(bx)} ${f1(by + s * 1.2)}" stroke="${string}" stroke-width="1" fill="none"/>
      <path ${tf(bx, by - s * 0.2, 8, s * 0.36)} d="${HEART}" fill="${c}"/><path ${tf(bx - s * 0.9, by - s * 1, -10, s * 0.1)} d="${HEART}" fill="#ffffff" opacity=".55"/>`;
  },
  sheep(x, y, s, wool, face) {
    return `<path d="M${f1(x - s * 0.5)} ${f1(y)}V${f1(y - s * 0.5)}M${f1(x + s * 0.4)} ${f1(y)}V${f1(y - s * 0.5)}" stroke="${face}" stroke-width="${f1(s * 0.16)}"/>` +
      ART.cloud(x, y - s * 0.8, s * 0.42, wool, 1) + `<ellipse cx="${f1(x + s * 0.85)}" cy="${f1(y - s * 0.95)}" rx="${f1(s * 0.24)}" ry="${f1(s * 0.3)}" fill="${face}"/>`;
  },
  // Sky.
  // A moon (or a soft sun by day): a lit disc with faint seas, in a wide halo.
  // A crescent cuts a disc out of the moon only (a mask), so the halo still glows around it.
  moon(x, y, r, c, glow, id, crescent) {
    const mask = crescent ? `<mask id="${id}m"><rect x="${f1(x - r * 2)}" y="${f1(y - r * 2)}" width="${f1(r * 4)}" height="${f1(r * 4)}" fill="#fff"/><circle cx="${f1(x + r * 0.42)}" cy="${f1(y - r * 0.26)}" r="${f1(r * 0.9)}" fill="#000"/></mask>` : "";
    return `<defs>${mask}<radialGradient id="${id}"><stop offset="0" stop-color="${glow}" stop-opacity=".55"/><stop offset=".22" stop-color="${glow}" stop-opacity=".22"/><stop offset=".5" stop-color="${glow}" stop-opacity=".07"/><stop offset="1" stop-color="${glow}" stop-opacity="0"/></radialGradient>
      <radialGradient id="${id}d" cx=".38" cy=".34" r=".75"><stop offset="0" stop-color="${mix("#ffffff", c, 0.45)}"/><stop offset=".65" stop-color="${c}"/><stop offset="1" stop-color="${mix("#000000", c, 0.12)}"/></radialGradient></defs>
      <circle cx="${f1(x)}" cy="${f1(y)}" r="${f1(r * 5)}" fill="url(#${id})"/>
      <g${crescent ? ` mask="url(#${id}m)"` : ""}><circle cx="${f1(x)}" cy="${f1(y)}" r="${f1(r)}" fill="url(#${id}d)"/>
      <g fill="${mix("#000000", c, 0.25)}" opacity="${SC.light || crescent ? 0 : 0.16}"><ellipse cx="${f1(x - r * 0.28)}" cy="${f1(y - r * 0.18)}" rx="${f1(r * 0.3)}" ry="${f1(r * 0.22)}"/><ellipse cx="${f1(x + r * 0.3)}" cy="${f1(y + r * 0.2)}" rx="${f1(r * 0.2)}" ry="${f1(r * 0.15)}"/><circle cx="${f1(x - r * 0.08)}" cy="${f1(y + r * 0.46)}" r="${f1(r * 0.1)}"/></g></g>`;
  },
  fence(pts, x0, x1, c) {
    let s = "", rail = (dy) => {
      let d = "";
      for (let x = x0; x <= x1; x += 12) d += `${d ? "L" : "M"}${f1(x)} ${f1(ridgeY(pts, x) - dy)}`;
      return `<path d="${d}" stroke="${c}" stroke-width="2.2" fill="none"/>`;
    };
    for (let x = x0; x <= x1; x += 26) s += `<rect x="${f1(x - 1.6)}" y="${f1(ridgeY(pts, x) - 21)}" width="3.2" height="23" rx="1" fill="${c}"/>`;
    return s + rail(16) + rail(8);
  },
  stars(r, W, n, maxY, c, op) {
    let s = "";
    for (let i = 0; i < n; i++) s += `<circle cx="${f1(r() * W)}" cy="${f1(r() * maxY)}" r="${f1(0.5 + r() * 1.1)}" fill="${c}" opacity="${(op * (0.35 + r() * 0.65)).toFixed(2)}"/>`;
    return s;
  },
  bokeh(r, W, n, maxY, cs, op, id) {
    let defs = "", s = "";
    cs.forEach((c, i) => (defs += `<radialGradient id="${id}${i}"><stop offset="0" stop-color="${c}" stop-opacity="1"/><stop offset=".6" stop-color="${c}" stop-opacity=".45"/><stop offset="1" stop-color="${c}" stop-opacity="0"/></radialGradient>`));
    for (let i = 0; i < n; i++) s += `<circle cx="${f1(r() * W)}" cy="${f1(20 + r() * maxY)}" r="${f1(10 + r() * 34)}" fill="url(#${id}${i % cs.length})" opacity="${(op * (0.4 + r() * 0.6)).toFixed(2)}"/>`;
    return `<defs>${defs}</defs>${s}`;
  },
  aurora(W, c1, c2, op, id) {
    const band = (y0, a, k) => {
      let d = `M-20 ${y0}`;
      for (let x = -20; x <= W + 40; x += 60) d += `L${x} ${f1(y0 + Math.sin(x / (110 * k)) * a + Math.sin(x / 47) * a * 0.3)}`;
      return `${d}V${y0 + 110}H-20Z`;
    };
    return `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${c1}" stop-opacity="0"/><stop offset=".35" stop-color="${c1}" stop-opacity=".9"/><stop offset="1" stop-color="${c2}" stop-opacity="0"/></linearGradient></defs>
      <path d="${band(40, 26, 1)}" fill="url(#${id})" opacity="${op}"/><path d="${band(92, 20, 1.6)}" fill="url(#${id})" opacity="${f1(op * 0.6)}"/>`;
  },
  rainbow(x, y, R, op) {
    return `<g fill="none" stroke-width="9" opacity="${op}">${RAINBOW.map((c, i) => `<path d="M${f1(x - (R - i * 9))} ${f1(y)}A${f1(R - i * 9)} ${f1(R - i * 9)} 0 0 1 ${f1(x + R - i * 9)} ${f1(y)}" stroke="${c}"/>`).join("")}</g>`;
  },
  burst(x, y, R, c, op) {
    let s = "";
    for (let i = 0; i < 18; i++) {
      const a = (i / 18) * Math.PI * 2, x1 = x + Math.cos(a) * R * 0.3, y1 = y + Math.sin(a) * R * 0.3, x2 = x + Math.cos(a) * R, y2 = y + Math.sin(a) * R;
      s += `<path d="M${f1(x1)} ${f1(y1)}L${f1(x2)} ${f1(y2)}"/><circle cx="${f1(x2 + Math.cos(a) * 4)}" cy="${f1(y2 + Math.sin(a) * 4)}" r="1.8" fill="${c}" stroke="none"/>`;
    }
    return `<g stroke="${c}" stroke-width="1.6" stroke-linecap="round" opacity="${op}">${s}</g>`;
  },
};
// Per theme: (light) => { sky: [top, middle] colors, glow (along the horizon), art (W, r), land (W, H, r) }.
// Per theme: (light) => { sky: [top, middle, horizon] colors, glow (along the
// horizon), art (W, r): what's in the sky, land (W, H, r): the landscape }.
// Three depths of hills with mist between them; the far ones paler, the near
// ones richer, everything lit from above.
const SCENES = {
  thanksgiving(light) {
    const c = light
      ? { sky: ["#f2d6b8", "#f7e6d2", "#f3d2ae"], glow: "rgba(240,150,70,.22)", moon: "#ffe2b0", halo: "#ffb86b", far: "#e9cfae", mid: "#dbb084", near: "#c99560",
          crowns: ["#d9803f", "#e8a54e", "#c0582f", "#c79a3a"], trunk: "#7a4e2e", barn: "#b04a32", roof: "#6e2a1e", trim: "#f6efe6", win: "#ffd27a", pumpkin: "#e07a2c", stem: "#5e4024", hay: "#dcb766", fence: "#8a6440" }
      : { sky: ["#170c1c", "#24131a", "#3a2016"], glow: "rgba(224,138,60,.22)", moon: "#f6c77e", halo: "#f0a050", far: "#2e2019", mid: "#3c281c", near: "#4e321e",
          crowns: ["#8a4420", "#a86228", "#76361a", "#94722e"], trunk: "#21140c", barn: "#62281b", roof: "#36140e", trim: "#a87454", win: "#ffc46b", pumpkin: "#c4681f", stem: "#3a2614", hay: "#86703a", fence: "#2a1a10" };
    return {
      sky: c.sky, glow: c.glow,
      art: (W, r) => ART.moon(W * 0.52, 92, 32, c.moon, c.halo, "sk-moon") + (light ? "" : ART.stars(r, W, 36, 250, "#ffe9c8", 0.55)),
      land(W, H, r) {
        const far = ridge(r, W, 112, 22, 170), mid = ridge(r, W, 172, 18, 150), near = ridge(r, W, 236, 10, 130);
        let s = hill(far, H, c.far);
        for (const x of spots(r, W, [0.55, 0.61, 0.9])) s += ART.tree(x, ridgeY(far, x) + 3, 30 + r() * 10, [c.crowns[1], c.crowns[3]], c.trunk);
        const bx = W * 0.75;
        s += ART.barn(bx, ridgeY(far, bx) + 4, 60, 48, c.barn, c.roof, c.trim, c.win);
        s += fog(W, 160, 70, c.sky[2], light ? 0.55 : 0.5) + hill(mid, H, c.mid);
        spots(r, W, [0.04, 0.15, 0.27, 0.4, 0.86, 0.97], 18).forEach((x, i) => (s += ART.tree(x, ridgeY(mid, x) + 4, 58 + r() * 36, [0, 1, 2, 3].map((k) => c.crowns[(i + k) % 4]), c.trunk)));
        s += fog(W, 228, 50, c.sky[2], light ? 0.4 : 0.35) + hill(near, H, c.near);
        s += ART.fence(near, W * 0.36, W * 0.56, c.fence);
        const hx = W * 0.18;
        s += ART.hay(hx, ridgeY(near, hx) + 6, 46, 28, c.hay, c.trunk) + ART.hay(hx + 40, ridgeY(near, hx + 40) + 7, 40, 24, c.hay, c.trunk);
        for (const [f, sz] of [[0.62, 18], [0.67, 12], [0.84, 15], [0.88, 10]]) s += ART.pumpkin(W * f, ridgeY(near, W * f) + 6, sz, c.pumpkin, c.stem);
        return s;
      },
    };
  },
  christmas(light) {
    const c = light
      ? { sky: ["#cfe0ea", "#e6eff4", "#eef4f7"], glow: "rgba(150,190,215,.2)", far: "#dde7ee", mid: "#edf2f6", near: "#ffffff", pine: "#2f6b4a", pineFar: "#7aa58e", snow: "#ffffff",
          wall: "#9a4b34", roof: "#5b3a2e", win: "#ffcf7a", smoke: "#9fb3c0", aurora: null }
      : { sky: ["#040d17", "#071a1d", "#0f2c2a"], glow: "rgba(120,200,170,.14)", far: "#22343c", mid: "#344b56", near: "#b9c9d3", pine: "#0c2519", pineFar: "#1a3a2c", snow: "#e6eef3",
          wall: "#3d2a21", roof: "#251a15", win: "#ffcf7a", smoke: "#8fa0aa", aurora: ["#3fe0a0", "#4fb3f0"] };
    return {
      sky: c.sky, glow: c.glow,
      art: (W, r) => c.aurora
        ? ART.aurora(W, c.aurora[0], c.aurora[1], 0.26, "sk-au") + ART.stars(r, W, 80, 320, "#ffffff", 0.85)
        : ART.cloud(W * 0.22, 74, 24, "#ffffff", 0.75) + ART.cloud(W * 0.78, 118, 19, "#ffffff", 0.65),
      land(W, H, r) {
        const far = ridge(r, W, 116, 22, 180), mid = ridge(r, W, 176, 16, 150), near = ridge(r, W, 240, 10, 140);
        let s = hill(far, H, c.far);
        for (const x of spots(r, W, [0.05, 0.1, 0.14, 0.52, 0.57, 0.92, 0.96], 10)) s += ART.pine(x, ridgeY(far, x) + 3, 26 + r() * 14, c.pineFar, c.snow);
        s += fog(W, 166, 70, c.sky[2], light ? 0.6 : 0.5) + hill(mid, H, c.mid);
        const cx = W * 0.7;
        s += ART.cabin(cx, ridgeY(mid, cx) + 4, 68, 56, c.wall, c.roof, c.win, c.snow, c.smoke);
        for (const x of spots(r, W, [0.03, 0.12, 0.21, 0.3, 0.38, 0.84, 0.92, 0.99], 12)) s += ART.pine(x, ridgeY(mid, x) + 4, 52 + r() * 36, c.pine, c.snow);
        s += fog(W, 232, 46, c.sky[2], light ? 0.4 : 0.3) + hill(near, H, c.near);
        for (const x of spots(r, W, [0.08, 0.5, 0.94], 10)) s += ART.pine(x, ridgeY(near, x) + 5, 30 + r() * 12, c.pine, c.snow);
        return s;
      },
    };
  },
  halloween(light) {
    const c = light
      ? { sky: ["#ddd0ea", "#ece4f3", "#f4dccb"], glow: "rgba(255,140,60,.16)", moon: "#ffffff", halo: "#c9b6e0", far: "#c9badb", mid: "#ad9ac3", near: "#86709f", house: "#76618f", tree: "#5a4574", win: "#ff9a3a", stone: "#9e8fb4", pumpkin: "#ff8a30", face: "#ffd27a", stem: "#4a3a5a", cloud: "#ece4f3" }
      : { sky: ["#07040e", "#140b20", "#2a1430"], glow: "rgba(255,122,26,.14)", moon: "#f3e7c6", halo: "#f3e7c6", far: "#1d1429", mid: "#241a33", near: "#0e0a15", house: "#150f20", tree: "#100b18", win: "#ffb347", stone: "#2d2542", pumpkin: "#e8691a", face: "#ffd27a", stem: "#2a1a10", cloud: "#3a2c4a" };
    return {
      sky: c.sky, glow: c.glow,
      art: (W, r) => ART.moon(W * 0.5, 110, 54, c.moon, c.halo, "sk-moon") + ART.stars(r, W, light ? 0 : 50, 280, "#ffffff", 0.65) +
        `<g fill="${c.cloud}" opacity="${light ? 0.85 : 0.45}"><path d="M${f1(W * 0.5 - 130)} 128c20-10 60-12 92-6s72 4 102-4 42 2 32 8-52 10-92 8-82 4-112 2-42-2-22-8Z"/><path d="M${f1(W * 0.5 - 40)} 156c30-8 72-6 102-2s42 6 22 10-62 4-92 2-52-4-32-10Z"/></g>`,
      land(W, H, r) {
        const far = ridge(r, W, 108, 26, 190), mid = ridge(r, W, 174, 18, 150), near = ridge(r, W, 240, 10, 130);
        let s = hill(far, H, c.far);
        const hx = W * 0.72;
        s += ART.haunted(hx, ridgeY(far, hx) + 5, 96, 84, c.house, c.win);
        s += fog(W, 160, 74, c.sky[2], light ? 0.55 : 0.55) + hill(mid, H, c.mid);
        const tx = W * 0.16;
        s += ART.gnarled(tx, ridgeY(mid, tx) + 4, 176, c.tree, r) + ART.gnarled(W * 0.93, ridgeY(mid, W * 0.93) + 4, 112, c.tree, r);
        s += fog(W, 232, 50, c.sky[2], light ? 0.45 : 0.4) + hill(near, H, c.near);
        spots(r, W, [0.34, 0.41, 0.47, 0.55], 8).forEach((x, i) => (s += ART.stone(x, ridgeY(near, x) + 5, 14 + r() * 7, c.stone, i === 1)));
        for (const [f, sz] of [[0.07, 16], [0.66, 14], [0.72, 10], [0.86, 15]]) s += ART.pumpkin(W * f, ridgeY(near, W * f) + 6, sz, c.pumpkin, c.stem, c.face);
        return s;
      },
    };
  },
  easter(light) {
    const c = light
      ? { sky: ["#d9e7ff", "#eef1ff", "#fbf3e2"], glow: "rgba(255,225,140,.28)", sun: "#fff3c4", halo: "#ffe08a", far: "#cfe8ca", mid: "#b6dfaf", near: "#98d092", stem: "#5fa45a" }
      : { sky: ["#0f0e24", "#18162e", "#28203e"], glow: "rgba(180,140,242,.14)", sun: "#ece6ff", halo: "#b48cf2", far: "#222a3a", mid: "#243c33", near: "#2b4c37", stem: "#4f8f55" };
    return {
      sky: c.sky, glow: c.glow,
      art: (W, r) => ART.moon(W * 0.5, 86, light ? 36 : 22, c.sun, c.halo, "sk-sun") +
        (light ? ART.cloud(W * 0.2, 74, 22, "#ffffff", 0.9) + ART.cloud(W * 0.82, 124, 17, "#ffffff", 0.8) : ART.stars(r, W, 50, 300, "#ffffff", 0.6)),
      land(W, H, r) {
        const far = ridge(r, W, 124, 20, 180), mid = ridge(r, W, 182, 16, 150), near = ridge(r, W, 244, 8, 140);
        // Blossom trees, pink by day and dusky rose at night.
        const bloom = light ? ["#f7b6cc", "#fbd0de", "#f19bb8"] : ["#8a5a78", "#a06a8c", "#76506a"], bark = light ? "#8a6a5a" : "#1a1420";
        let s = hill(far, H, c.far);
        for (const x of spots(r, W, [0.36, 0.44, 0.86], 10)) s += ART.tree(x, ridgeY(far, x) + 3, 32 + r() * 8, bloom, bark);
        s += fog(W, 170, 64, c.sky[2], light ? 0.5 : 0.45) + hill(mid, H, c.mid);
        for (const x of spots(r, W, [0.3, 0.46, 0.82], 14)) s += ART.tree(x, ridgeY(mid, x) + 4, 62 + r() * 24, bloom, bark);
        for (const x of spots(r, W, [0.06, 0.1, 0.14, 0.18, 0.6, 0.64, 0.69, 0.74, 0.9, 0.95], 8)) s += ART.tulip(x, ridgeY(mid, x) + 4, 24 + r() * 14, PASTEL[Math.floor(r() * 5)], c.stem);
        s += fog(W, 236, 40, c.sky[2], light ? 0.35 : 0.3) + hill(near, H, c.near);
        spots(r, W, [0.24, 0.285, 0.33, 0.8, 0.845], 5).forEach((x, i) => (s += ART.egg(x, ridgeY(near, x) + 5, 20 + r() * 7, PASTEL[(i * 2) % 5])));
        return s;
      },
    };
  },
  july4(light) {
    const c = light
      ? { sky: ["#cfe0ff", "#e8efff", "#f4f1ea"], glow: "rgba(79,140,255,.14)", far: "#cadcc4", city: "#bcc4d4", win: "#e7ebf3", near: "#a2cf95", tree: "#5f9e50" }
      : { sky: ["#03081a", "#0a1330", "#1a1838"], glow: "rgba(229,72,77,.14)", far: "#131b35", city: "#101733", win: "#ffd27a", near: "#090e1e", tree: "#070b18" };
    return {
      sky: c.sky, glow: c.glow,
      art: (W, r) => light
        ? ART.cloud(W * 0.25, 84, 22, "#ffffff", 0.9) + ART.cloud(W * 0.75, 124, 17, "#ffffff", 0.8)
        : ART.stars(r, W, 100, 330, "#ffffff", 0.85) + ART.burst(W * 0.28, 116, 50, "#e5484d", 0.45) + ART.burst(W * 0.6, 74, 38, "#ffffff", 0.35) + ART.burst(W * 0.84, 156, 44, "#4f8cff", 0.45),
      land(W, H, r) {
        const far = ridge(r, W, 146, 16, 190), near = ridge(r, W, 236, 10, 150);
        let s = hill(far, H, c.far) + ART.skyline(r, W, 214, 30, 96, c.city, c.win, light ? 0.06 : 0.14);
        s += fog(W, 214, 50, c.sky[2], light ? 0.5 : 0.4) + hill(near, H, c.near);
        for (const x of spots(r, W, [0.07, 0.15, 0.91])) s += ART.tree(x, ridgeY(near, x) + 5, 48 + r() * 22, [c.tree], c.tree);
        const fx = W * 0.62;
        s += ART.flag(fx, ridgeY(near, fx) + 3, 58);
        return s;
      },
    };
  },
  valentines(light) {
    const c = light
      ? { sky: ["#ffd6e4", "#ffeaf1", "#ffe2d4"], glow: "rgba(255,92,138,.18)", hearts: ["#ff9ab8", "#ff5c8a"], moon: "#ffffff", halo: "#ffb3c8", far: "#f8cdda", mid: "#f0b3c6", near: "#e69ab1",
          crowns: ["#ff5c8a", "#ff8fae", "#e5484d"], trunk: "#9a5a6a", string: "#b07a88" }
      : { sky: ["#12050d", "#1f0b17", "#3a1426"], glow: "rgba(255,92,138,.18)", hearts: ["#ff5c8a", "#ff9ab8"], moon: "#ffe6ee", halo: "#ff9ab8", far: "#2b1322", mid: "#38192d", near: "#46223a",
          crowns: ["#d9406e", "#ff6f96", "#b8323f"], trunk: "#1c0b14", string: "#8a5a6a" };
    return {
      sky: c.sky, glow: c.glow,
      art: (W, r) => {
        // A crescent: the moon with the sky laid over most of it.
        let s = ART.bokeh(r, W, 16, 320, c.hearts, light ? 0.3 : 0.22, "sk-bk") + ART.moon(W * 0.5, 96, 30, c.moon, c.halo, "sk-moon", true);
        if (!light) s += ART.stars(r, W, 40, 280, "#ffffff", 0.6);
        return s;
      },
      land(W, H, r) {
        const far = ridge(r, W, 122, 18, 170), mid = ridge(r, W, 180, 14, 150), near = ridge(r, W, 242, 8, 140);
        let s = hill(far, H, c.far);
        for (const x of spots(r, W, [0.58, 0.64, 0.9], 10)) s += ART.heartTree(x, ridgeY(far, x) + 3, 34 + r() * 8, c.crowns[1], c.trunk);
        s += fog(W, 168, 64, c.sky[2], 0.5) + hill(mid, H, c.mid);
        spots(r, W, [0.05, 0.16, 0.28, 0.84, 0.96], 14).forEach((x, i) => (s += ART.heartTree(x, ridgeY(mid, x) + 4, 62 + r() * 30, c.crowns[i % 3], c.trunk)));
        s += fog(W, 234, 44, c.sky[2], 0.35) + hill(near, H, c.near);
        spots(r, W, [0.5, 0.55, 0.6], 6).forEach((x, i) => (s += ART.balloon(x, ridgeY(near, x) + 2, 7 + r() * 2, c.crowns[i % 3], c.string)));
        return s;
      },
    };
  },
  stpatricks(light) {
    const c = light
      ? { sky: ["#d8f0e2", "#ecf8f1", "#f4f6e4"], glow: "rgba(60,207,110,.14)", far: "#b3e0be", mid: "#8bd09e", near: "#66bf80", gold: "#f3c94a", rainbow: 0.55,
          crowns: ["#4fa865", "#6cc07a", "#3f9255"], trunk: "#5a4630", wall: "#f4f1e8", thatch: "#a0814c", win: "#ffd27a", wool: "#ffffff", face: "#3a3a3a" }
      : { sky: ["#040e09", "#0a1a12", "#16301e"], glow: "rgba(233,196,106,.12)", far: "#102e1e", mid: "#153e27", near: "#1b5033", gold: "#e9c46a", rainbow: 0.32,
          crowns: ["#1f5a34", "#2a6e40", "#184a2b"], trunk: "#14100a", wall: "#c9c6bb", thatch: "#4a3b22", win: "#ffd27a", wool: "#d8dcd6", face: "#1a1a1a" };
    return {
      sky: c.sky, glow: c.glow,
      art: (W, r) => ART.rainbow(W * 0.5, 340, Math.min(W * 0.46, 330), c.rainbow) + (light ? ART.cloud(W * 0.16, 330, 20, "#ffffff", 0.9) + ART.cloud(W * 0.84, 330, 20, "#ffffff", 0.9) : ART.stars(r, W, 50, 280, "#ffffff", 0.6)),
      land(W, H, r) {
        const far = ridge(r, W, 118, 24, 170), mid = ridge(r, W, 176, 18, 150), near = ridge(r, W, 240, 10, 140);
        let s = hill(far, H, c.far);
        for (const x of spots(r, W, [0.08, 0.13, 0.6], 10)) s += ART.tree(x, ridgeY(far, x) + 3, 30 + r() * 8, c.crowns, c.trunk);
        s += fog(W, 164, 66, c.sky[2], 0.45) + hill(mid, H, c.mid);
        const hx = W * 0.3;
        s += ART.cabin(hx, ridgeY(mid, hx) + 4, 84, 54, c.wall, c.thatch, c.win, null, null);
        for (const x of spots(r, W, [0.06, 0.48, 0.92], 12)) s += ART.tree(x, ridgeY(mid, x) + 4, 54 + r() * 24, c.crowns, c.trunk);
        for (const x of spots(r, W, [0.62, 0.7, 0.76], 10)) s += ART.sheep(x, ridgeY(mid, x) + 3, 17, c.wool, c.face);
        s += fog(W, 232, 44, c.sky[2], 0.3) + hill(near, H, c.near);
        const px = W * 0.82;
        s += ART.potOfGold(px, ridgeY(near, px) + 6, 34, c.gold);
        return s;
      },
    };
  },
  newyear(light) {
    const c = light
      ? { sky: ["#efe7d2", "#f7f3e8", "#f3ead2"], glow: "rgba(233,196,106,.22)", far: "#dcd5c3", near: "#c3bba6", win: "#f7f1df", face: "#ffffff" }
      : { sky: ["#04051a", "#0b0d26", "#1c1a34"], glow: "rgba(233,196,106,.16)", far: "#161935", near: "#0b0d1f", win: "#e9c46a", face: "#fff3c4" };
    return {
      sky: c.sky, glow: c.glow,
      art: (W, r) => ART.bokeh(r, W, 20, 330, ["#e9c46a", "#fff3c4", "#f4a3c0"], light ? 0.45 : 0.5, "sk-bk") + (light ? "" : ART.stars(r, W, 70, 300, "#ffffff", 0.75)),
      land(W, H, r) {
        let s = ART.skyline(r, W, H, 96, 196, c.far, c.win, light ? 0.05 : 0.16) + fog(W, H - 70, 120, c.sky[2], light ? 0.45 : 0.35);
        s += ART.skyline(r, W, H, 40, 124, c.near, c.win, light ? 0.04 : 0.1);
        s += ART.clockTower(W * 0.5, H, 226, c.near, c.face);
        return s;
      },
    };
  },
};
/** The page scene for a theme: { bg (CSS background), html (the two SVGs) }, or null for the everyday look. */
function sceneFor(key, light, W) {
  const make = SCENES[key];
  if (!make) return null;
  SC.light = light;
  SC.n = 0;
  const s = make(light);
  const r = seeded(97 + key.length * 131);
  const svg = (cls, h, body) => `<svg class="${cls}" width="${W}" height="${h}" viewBox="0 0 ${W} ${h}" aria-hidden="true">${body}</svg>`;
  return {
    // The sky runs the whole page, from its top color down to the horizon behind the hills.
    bg: `radial-gradient(90% ${LAND_H}px at 50% 100%, ${s.glow}, transparent), linear-gradient(180deg, ${s.sky[0]} 0, ${s.sky[1]} 38%, ${s.sky[2]} 100%)`,
    html: svg("sky", SKY_H, s.art ? s.art(W, r) : "") + svg("land", LAND_H, s.land(W, LAND_H, r)),
  };
}

// The HA Companion app paints Android's status bar from the page's
// --app-header-background-color and the navigation bar (and the window behind
// the page) from --primary-background-color, read off <html> when the frontend
// sends `theme-update` on the external bus and when it connects (android
// WebViewActivity.getAndSetStatusBarNavigationBarColors). HA's own theme is the
// default light one, so the bars came out white over the dark cards. While the
// Kitchen dashboard is on screen, both are set to the dashboard's page color
// (in a stylesheet with !important, so HA re-applying its theme on a reconnect
// can't undo it; HA sends its own theme-update afterwards and the app reads ours)
// and the app is told to look again. Other dashboards get HA's colors back.
const Bars = {
  owners: new Set(),
  _color: null,
  attach(card) {
    this.owners.add(card);
    this.update();
    Theme.showFx();
  },
  detach(card) {
    this.owners.delete(card);
    this.update();
    Theme.showFx();
  },
  update() {
    const want = this.owners.size && !KM.escaped() ? Theme.bg() : null;
    if (want === this._color) return;
    this._color = want;
    if (want) {
      if (!this._el) {
        this._el = document.createElement("style");
        this._el.id = "tnwks-kitchen-bars";
      }
      this._el.textContent = `html { --app-header-background-color:${want} !important; --primary-background-color:${want} !important; }`;
      if (!this._el.isConnected) document.head.appendChild(this._el);
    } else this._el?.remove();
    // HA keeps <html>'s own background in step with --primary-background-color.
    document.documentElement.style.backgroundColor = getComputedStyle(document.documentElement).getPropertyValue("--primary-background-color").trim();
    this.notify();
  },
  /** Ask the Companion app to re-read the colors. Browsers have no bridge, so this does nothing there. */
  notify() {
    const ext = Theme._hass?.auth?.external;
    try {
      if (typeof ext?.fireMessage === "function") return ext.fireMessage({ type: "theme-update" });
      const msg = { type: "theme-update", id: Date.now() };
      if (window.externalAppV2?.postMessage) window.externalAppV2.postMessage(JSON.stringify({ type: "externalBus", payload: msg }));
      else if (window.externalApp?.externalBus) window.externalApp.externalBus(JSON.stringify(msg));
    } catch (err) {
      console.warn("tnwks kitchen cards: theme-update failed", err);
    }
  },
};
document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && Bars._color && Bars.notify());

// ------------------------------------------------------- kiosk escape

// kiosk-mode hides HA's header and sidebar on the wall display, which also
// hides the way to HA's menu. "Home Assistant menu" in the 🎨 sheet reloads
// the dashboard with ?disable_km (kiosk-mode's own off switch for one page
// load), and a "Back to kitchen" pill reloads it without that. A device that
// has shown the dashboard in kiosk mode remembers it (localStorage), so after
// `idle_home` minutes untouched it goes back to the kiosk dashboard by itself,
// also from another HA page. Only the HA app counts as a device (a desktop
// browser tried with ?kiosk shouldn't start jumping back), and ?clear_km_cache
// forgets it, like kiosk-mode.
// The wall display's browser (the HA app on the Apolosign tablet). Keep in step
// with kiosk_mode in dashboards/kitchen.yaml.
const WALL_UA = /Apolosign/i;
/** Leave the kiosk for the rest of HA, inside the app, without a reload. */
function leaveToHA(path) {
  history.pushState(null, "", path);
  window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
}
// mdi:home-assistant
const HA_LOGO = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21.8,13H20V21H13V17.67L15.79,14.88L16.5,15C17.66,15 18.6,14.06 18.6,12.9C18.6,11.74 17.66,10.8 16.5,10.8A2.1,2.1 0 0,0 14.4,12.9L14.5,13.61L13,15.13V9.65C13.66,9.29 14.1,8.6 14.1,7.8A2.1,2.1 0 0,0 12,5.7A2.1,2.1 0 0,0 9.9,7.8C9.9,8.6 10.34,9.29 11,9.65V15.13L9.5,13.61L9.6,12.9A2.1,2.1 0 0,0 7.5,10.8A2.1,2.1 0 0,0 5.4,12.9A2.1,2.1 0 0,0 7.5,15L8.21,14.88L11,17.67V21H4V13H2.25C1.83,13 1.42,13 1.42,12.79C1.43,12.57 1.85,12.15 2.28,11.72L11,3C11.33,2.67 11.67,2.33 12,2.33C12.33,2.33 12.67,2.67 13,3L17,7V6H19V9L21.78,11.78C22.18,12.18 22.59,12.59 22.6,12.8C22.6,13 22.2,13 21.8,13M7.5,12A0.9,0.9 0 0,1 8.4,12.9A0.9,0.9 0 0,1 7.5,13.8A0.9,0.9 0 0,1 6.6,12.9A0.9,0.9 0 0,1 7.5,12M16.5,12C17,12 17.4,12.4 17.4,12.9C17.4,13.4 17,13.8 16.5,13.8A0.9,0.9 0 0,1 15.6,12.9A0.9,0.9 0 0,1 16.5,12M12,6.9C12.5,6.9 12.9,7.3 12.9,7.8C12.9,8.3 12.5,8.7 12,8.7C11.5,8.7 11.1,8.3 11.1,7.8C11.1,7.3 11.5,6.9 12,6.9Z"/></svg>`;
const KM = {
  KEY: "tnwks-kitchen-kiosk",
  touched: Date.now(),
  _tokens() {
    return location.search.slice(1).split("&").filter(Boolean);
  },
  _has(...names) {
    return this._tokens().some((t) => names.includes(decodeURIComponent(t.split("=")[0])));
  },
  _store(k, v) {
    try {
      if (v === undefined) return localStorage.getItem(k);
      if (v === null) localStorage.removeItem(k);
      else localStorage.setItem(k, v);
    } catch {
      return null;
    }
  },
  /** kiosk-mode was told to hide things: by the dashboard's kiosk_mode (the wall display's user agent), the URL, or its own cache. */
  _asked() {
    return WALL_UA.test(navigator.userAgent) || this._has("kiosk", "hide_header", "hide_sidebar") || ["kmKiosk", "kmHideHeader", "kmHideSidebar"].some((k) => this._store(k) === "true");
  },
  active() {
    return !this._has("disable_km") && this._asked();
  },
  /** The kitchen URL this device shows in kiosk mode, or null. */
  home() {
    return this._store(this.KEY);
  },
  /** This is the wall display, showing HA's menu instead of the kiosk. */
  escaped() {
    return !!this.home() && !this.active();
  },
  _inApp() {
    return !!(window.externalAppV2 || window.externalApp || window.webkit?.messageHandlers?.externalBus);
  },
  /** Called while the Kitchen dashboard is on screen. */
  remember() {
    if (this._has("clear_km_cache")) return this._store(this.KEY, null);
    if (this.active() && this._inApp()) this._store(this.KEY, location.pathname);
  },
  _url(path, tokens, hash) {
    return `${path}${tokens.length ? `?${tokens.join("&")}` : ""}${hash || ""}`;
  },
  /** This page again with kiosk-mode off. */
  menuUrl() {
    const t = this._tokens().filter((x) => x.split("=")[0] !== "disable_km");
    return this._url(location.pathname, [...t, "disable_km"], location.hash);
  },
  /** The kiosk dashboard: this page without ?disable_km (adding ?kiosk if nothing else turns kiosk-mode on). */
  kitchenUrl(hash) {
    const path = this.home() || location.pathname;
    let t = path === location.pathname ? this._tokens().filter((x) => x.split("=")[0] !== "disable_km") : [];
    const asked = WALL_UA.test(navigator.userAgent) || t.some((x) => ["kiosk", "hide_header", "hide_sidebar"].includes(x.split("=")[0])) || ["kmKiosk", "kmHideHeader", "kmHideSidebar"].some((k) => this._store(k) === "true");
    if (!asked) t = [...t, "kiosk"];
    return this._url(path, t, hash ?? (path === location.pathname ? location.hash : ""));
  },
  go(url) {
    if (this._going) return;
    this._going = true;
    setTimeout(() => (this._going = false), 10000);
    console.info(`tnwks kitchen cards: kiosk ${url}`);
    // In the Android HA app a plain reload can leave the app: on the display
    // it opened Chrome instead. The app's own homeassistant://navigate link
    // reloads the page inside the app, query string included.
    if (this._inApp() && /Android/i.test(navigator.userAgent)) location.href = `homeassistant://navigate${url}`;
    else location.replace(url);
  },
};
window.addEventListener("pointerdown", () => (KM.touched = Date.now()), { capture: true, passive: true });
window.addEventListener("keydown", () => (KM.touched = Date.now()), { capture: true, passive: true });
// From anywhere in HA (another dashboard, Settings), back to the kiosk after the idle time.
setInterval(() => {
  const home = KM.home();
  if (!home || Date.now() - KM.touched < KM.idleMs) return;
  if (location.pathname !== home ? !Bars.owners.size : KM.escaped()) KM.go(KM.kitchenUrl(""));
}, 30000);
KM.idleMs = 5 * 60000;

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
  .bd { position:fixed; inset:0; background:var(--scrim); animation:kfade .2s ease-out both; }
  .sh { position:fixed; left:50%; top:7vh; width:min(660px,94vw); max-height:84vh; overflow:auto; transform:translateX(-50%);
    background:var(--card); border-radius:24px; padding:20px; box-shadow:0 18px 50px rgba(0,0,0,.45); animation:shin .26s cubic-bezier(.2,.9,.3,1.1) both; }
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
  .acts .del { color:var(--danger); }
  .acts .del.arm { background:#c62828; color:#fff; }
  .grid { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
  .grid button { background:var(--card2); border-radius:16px; padding:16px 10px; text-align:center; font-size:15px; box-shadow:inset 0 -4px 0 var(--sw, transparent); }
  .grid button span { display:block; font-size:34px; margin-bottom:6px; }
  .grid button.on { outline:3px solid var(--accent); }
  .seg3 { display:flex; gap:6px; background:var(--card2); border-radius:16px; padding:5px; }
  .seg3 button { flex:1; text-align:center; padding:11px 6px; border-radius:12px; font-size:16px; color:var(--sub); }
  .seg3 button.on { background:var(--accent); color:var(--ink); font-weight:500; }
  .acts .menu { color:var(--sub); font-size:15px; display:flex; align-items:center; gap:8px; }
  .evbar { width:6px; align-self:stretch; border-radius:3px; flex:none; }
  .evl { display:flex; align-items:center; gap:8px; font-size:17px; margin:6px 0; }
  .evd { background:var(--card2); border-radius:14px; padding:12px 14px; margin-top:12px; font-size:15px; line-height:1.45; max-height:40vh; overflow:auto; white-space:normal; overflow-wrap:anywhere; }
  .wgrid { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; }
  .ws { background:var(--card2); border-radius:14px; padding:10px 12px; }
  .ws small { display:block; color:var(--sub); font-size:12px; margin-bottom:3px; }
  .ws b { font-size:17px; font-weight:500; }
  .whs { display:flex; gap:6px; overflow-x:auto; padding-bottom:4px; }
  .wh { flex:none; width:62px; background:var(--card2); border-radius:14px; padding:8px 4px; text-align:center; display:flex; flex-direction:column; gap:3px; }
  .wh small { color:var(--sub); font-size:12px; }
  .wh span { font-size:24px; }
  .wh b { font-size:16px; font-weight:500; }
  .wh i { font-style:normal; font-size:11px; color:var(--acct); min-height:13px; }
  .wds { display:flex; flex-direction:column; gap:6px; }
  .wd { display:grid; grid-template-columns:62px 34px 40px 1fr 40px 44px; align-items:center; gap:8px; background:var(--card2); border-radius:14px; padding:8px 12px; font-size:16px; }
  .wd span { font-size:22px; }
  .wd small { color:var(--sub); text-align:right; font-size:15px; }
  .wd i { font-style:normal; font-size:12px; color:var(--acct); text-align:right; }
  .wd .bar { position:relative; height:6px; border-radius:3px; background:var(--line); }
  .wd .bar div { position:absolute; top:0; bottom:0; border-radius:3px; background:linear-gradient(90deg, #6bb7ff, var(--accent)); }
  .rw { display:flex; flex-direction:column; align-items:center; gap:4px; margin:4px 0 6px; }
  .rw span { font-size:72px; line-height:1.1; }
  .rw b { font-size:26px; font-weight:500; text-align:center; }
  .rw i { font-style:normal; font-size:18px; font-weight:700; color:var(--acct); }
  .amt { font-size:40px; font-weight:700; margin:2px 0 10px; }
`;
function openSheet({ render, onAction, onInput }) {
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;inset:0;z-index:9500";
  const root = host.attachShadow({ mode: "open" });
  Skin.adopt(root);
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
/** The theme picker (🎨 next to the tabs): theme, dark/light mode, and the way into HA's menu on the wall display. */
function themePicker(hass) {
  const latest = () => Theme._hass || hass;
  const wall = isWall();
  // The wall display sets the input_selects; anyone else sets their own (Prefs).
  const pick = (kind, option) => {
    const h = latest();
    if (!wall) {
      Prefs.update(h, kind === "theme" ? { theme: option, at: Date.now() } : { mode: option });
      return Theme.apply(h);
    }
    const entity = kind === "theme" ? THEME_ENTITY : MODE_ENTITY;
    h.callService("input_select", "select_option", { entity_id: entity, option });
    // Repaint now even if HA is slow to answer.
    if (kind === "theme") Theme.key = null;
    else Theme.mode = null;
    const s = h.states[entity];
    Theme.apply({ ...h, states: { ...h.states, [entity]: { ...s, state: option, last_changed: s?.state === option ? s.last_changed : new Date().toISOString() } } });
  };
  const sheet = openSheet({
    render() {
      const cur = Theme.key;
      const opts = [["default", DEFAULT_THEME], ...Object.entries(THEMES)];
      const h = latest();
      const ms = pickedMode(h);
      const mode = MODES.map((m) => `<button class="${ms === m.key ? "on" : ""}" data-action="mode" data-key="${m.key}">${m.icon} ${m.label}</button>`).join("");
      const who = h.user?.name ? esc(h.user.name) : "you";
      return `<div class="tt">🎨 Display theme</div><div class="grid">${opts
        .map(([k, t]) => `<button class="${cur === k ? "on" : ""}" data-action="pick" data-key="${k}" style="--sw:${t.vars.accent || DEFAULT_ACCENT}"><span>${t.emoji}</span>${esc(t.name)}</button>`)
        .join("")}</div><div class="hint" style="margin-top:12px">Any theme, any time.${wall ? " Holiday themes also offer themselves on the Home tab when one is coming up." : ""} One left on through its holiday switches back to Everyday a few days after.</div>
        ${wall && !h.states[MODE_ENTITY] ? "" : `<div class="lb">Dark or light</div><div class="seg3">${mode}</div>
        <div class="hint">${ms === "auto" ? `Auto is dark from sunset to sunrise (${Theme.mode} now).` : "Auto follows the sun: dark from sunset to sunrise."}</div>`}
        <div class="hint">${wall ? "This is the kitchen display's look, and its screensaver's. Everyone else's screens keep their own." : `Saved for ${who}, on every device you sign in on. Nobody else's screen changes, the kitchen display included.`}</div>
        <div class="acts"><button class="go" data-action="cancel">Done</button></div>`;
    },
    onAction(a, d, s) {
      if (a === "pick") pick("theme", d.key);
      else if (a === "mode") pick("mode", d.key);
      else if (a === "ha-menu") return KM.go(KM.menuUrl());
      else return;
      s.update();
    },
  });
  return sheet;
}

/** Weather details (tap the weather on the header): now, the next 12 hours, the next days, the sun. */
function weatherSheet(card) {
  const sheet = openSheet({
    render() {
      const h = card._hass, now = new Date();
      const e = card.config?.weather || "weather.forecast_home";
      const w = h.states[e];
      if (!w) return `<div class="tt">Weather</div><div class="empty">No weather entity.</div>`;
      const isDay = h.states["sun.sun"]?.state !== "below_horizon";
      const hourly = card._hourly ? Object.assign(card._hourly, { isDay }) : null;
      const n = weatherNow(w, hourly, now);
      const td = todayForecast(card._forecast, now);
      const a = w.attributes, u = a.wind_speed_unit || "mph";
      const stat = (k, v) => (v == null || v === "" ? "" : `<div class="ws"><small>${k}</small><b>${v}</b></div>`);
      const sun = h.states["sun.sun"]?.attributes || {};
      const rise = sun.next_rising && new Date(sun.next_rising), set = sun.next_setting && new Date(sun.next_setting);
      const hours = (card._hourly || []).map((x) => ({ ...x, t: new Date(x.datetime) })).filter((x) => x.t > new Date(now - 3600000)).slice(0, 12);
      const hourRow = hours.map((x) => {
        const night = rise && set ? (set < rise ? x.t > set && x.t < rise : x.t > set || x.t < rise) : false;
        const icon = x.condition === "sunny" && night ? WEATHER_ICON["clear-night"] : WEATHER_ICON[x.condition] || "";
        return `<div class="wh"><small>${x.t.toLocaleTimeString(undefined, { hour: "numeric" })}</small><span>${icon}</span><b>${Math.round(x.temperature)}°</b>${x.precipitation > 0 ? `<i>${x.precipitation}″</i>` : "<i></i>"}</div>`;
      }).join("");
      const days = card._forecast || [];
      const lo = Math.min(...days.map((d) => d.templow ?? d.temperature)), hi = Math.max(...days.map((d) => d.temperature));
      const dayRows = days.map((d) => {
        const t = new Date(d.datetime), l = d.templow ?? d.temperature;
        const left = ((l - lo) / Math.max(1, hi - lo)) * 100, width = ((d.temperature - l) / Math.max(1, hi - lo)) * 100;
        return `<div class="wd"><b>${sameDay(t, now) ? "Today" : t.toLocaleDateString(undefined, { weekday: "short" })}</b><span>${WEATHER_ICON[d.condition] || ""}</span>
          <small>${Math.round(l)}°</small><div class="bar"><div style="left:${left}%;width:${Math.max(4, width)}%"></div></div><b>${Math.round(d.temperature)}°</b>
          <i>${d.precipitation > 0 ? `${d.precipitation}″` : ""}</i></div>`;
      }).join("");
      return `<div class="tt"><span style="font-size:40px">${WEATHER_ICON[n.condition] || ""}</span><span><b style="font-size:40px;font-weight:400">${Math.round(n.temp)}°</b>
          <span class="muted" style="font-size:17px;margin-left:8px">${esc(WEATHER_TEXT[n.condition] || n.condition)}${td ? ` · H ${Math.round(td.temperature)}° L ${Math.round(td.templow)}°` : ""}</span></span></div>
        <div class="wgrid">${stat("Humidity", n.humidity != null ? `${Math.round(n.humidity)}%` : null)}${stat("Wind", n.wind != null ? `${Math.round(n.wind)} ${esc(u)} ${compass(n.bearing)}` : null)}
          ${stat("UV index", n.uv != null ? n.uv.toFixed(1) : null)}${stat("Clouds", n.cloud != null ? `${Math.round(n.cloud)}%` : null)}
          ${stat("Dew point", a.dew_point != null ? `${Math.round(a.dew_point)}°` : null)}${stat("Pressure", a.pressure != null ? `${a.pressure} ${esc(a.pressure_unit || "")}` : null)}
          ${stat("Sunrise", rise ? hm(rise) : null)}${stat("Sunset", set ? hm(set) : null)}</div>
        ${hourRow ? `<div class="lb">Next 12 hours</div><div class="whs">${hourRow}</div>` : ""}
        ${dayRows ? `<div class="lb">Next days</div><div class="wds">${dayRows}</div>` : ""}
        <div class="hint">${esc(a.attribution || "")}</div>
        <div class="acts"><button class="go" data-action="cancel">Done</button></div>`;
    },
    onAction() {},
  });
  const close = sheet.close;
  sheet.close = () => {
    card._wxSheet = null;
    close();
  };
  return sheet;
}

/** Details of a calendar event (or a planned meal), from a tap on it anywhere. */
function eventSheet(e, card) {
  if (!e) return;
  const hass = card._hass;
  const meal = /^calendar\.mealie_/.test(e.calendar) && card.config?.mealie;
  const sameD = (a, b) => a.toDateString() === b.toDateString();
  const dl = (d) => d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
  let when;
  if (e.allDay) {
    const last = addDays(e.end, -1);
    when = sameD(e.start, last) || last < e.start ? `${dl(e.start)} · All day` : `${dl(e.start)} – ${dl(last)}`;
  } else when = sameD(e.start, e.end) ? `${dl(e.start)} · ${fmtTime(e.start)} – ${fmtTime(e.end)}` : `${dl(e.start)} ${fmtTime(e.start)} – ${dl(e.end)} ${fmtTime(e.end)}`;
  const who = e.who.length ? e.who.map((k) => MEMBER[k]) : [];
  const cal = hass.states[e.calendar]?.attributes?.friendly_name || e.calendar;
  // Calendar descriptions are often HTML from Google; show them as text.
  const desc = e.description ? new DOMParser().parseFromString(e.description.replace(/<br\s*\/?>/gi, "\n"), "text/html").body.textContent.trim() : "";
  return openSheet({
    render() {
      return `<div class="tt"><span class="evbar" style="background:${eventColor(e)}"></span>${esc(e.title)}</div>
        <div class="evl">🕒 ${esc(when)}</div>
        ${e.location ? `<div class="evl">📍 ${esc(e.location)}</div>` : ""}
        ${who.length ? `<div class="evl">${who.map((m) => `<span class="av" style="background:${m.color};width:26px;height:26px;font-size:12px">${m.name[0]}</span> ${esc(m.name)}`).join(" &nbsp; ")}</div>` : ""}
        ${desc ? `<div class="evd">${esc(desc).replace(/\n/g, "<br>")}</div>` : ""}
        <div class="hint">${esc(cal)}</div>
        <div class="acts">${e.location ? `<button data-action="map">Directions</button>` : ""}${meal ? `<button data-action="recipe">Open in Mealie ↗</button>` : ""}<button class="go" data-action="cancel">Done</button></div>`;
    },
    onAction(a) {
      if (a === "map") openExternal(`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(e.location)}`);
      else if (a === "recipe") openRecipe(hass, meal, e);
    },
  });
}
const mealieBase = (m) => String(m.url || "").replace(/\/$/, "");
const mealiePlanner = (m) => `${mealieBase(m)}/household/mealplan/planner/view`;
/** Open the recipe behind a meal plan event in Mealie (its slug from mealie.get_mealplan), else the planner. */
async function openRecipe(hass, m, e) {
  const win = KM._inApp() || KM.active() ? null : window.open("about:blank", "_blank");
  let url = mealiePlanner(m);
  try {
    const day = `${e.start.getFullYear()}-${String(e.start.getMonth() + 1).padStart(2, "0")}-${String(e.start.getDate()).padStart(2, "0")}`;
    const r = await hass.callService("mealie", "get_mealplan", { config_entry_id: m.entry, start_date: day, end_date: day }, undefined, false, true);
    const plans = r?.response?.mealplan || [];
    const type = e.calendar.replace("calendar.mealie_", "");
    const p = plans.find((x) => x.entry_type === type && (x.recipe?.name === e.title || x.title === e.title)) || plans.find((x) => x.recipe?.name === e.title);
    if (p?.recipe?.slug) url = `${mealieBase(m)}/g/${m.group || "home"}/r/${p.recipe.slug}`;
  } catch (err) {
    console.warn("tnwks kitchen cards: mealie lookup", err);
  }
  openMealie(url, win);
}

// ------------------------------------------------------------- base class

const BASE_CSS = `
  :host { display:block; --bg:var(--k-bg,#111318); --card:var(--k-card,#1c1f26); --card2:var(--k-card2,#252932); --line:var(--k-line,#2e333d);
    --txt:var(--k-txt,#e8eaed); --sub:var(--k-sub,#9aa0aa); --accent:var(--k-accent,#ff9f43); --ink:var(--k-ink,#1b1206); --ok:var(--k-ok,#4cd17a);
    --acct:var(--k-acct,var(--accent)); --okt:var(--k-okt,var(--ok)); --danger:var(--k-danger,#ff8a80);
    --slot0:var(--k-slot0,#3b3522); --slot1:var(--k-slot1,#1e3542); --slot2:var(--k-slot2,#40263a); --slot3:var(--k-slot3,#2f2944);
    --scrim:var(--k-scrim,rgba(0,0,0,.55)); --shadow:var(--k-shadow,none);
    color:var(--txt); font-family:Roboto,"Noto Sans",sans-serif; }
  * { box-sizing:border-box; }
  .card { background:var(--card); border-radius:18px; padding:14px; height:100%; transition:background-color .4s; box-shadow:var(--shadow); }
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
    Skin.adopt(this.shadowRoot);
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
    if (!el) {
      // A tap on the card itself (not one of its buttons) goes to its page.
      if (this.config?.tap_page) goPage(this.config.tap_page);
      return;
    }
    if (el.dataset.action === "ev") return eventSheet(EVENTS.get(el.dataset.ev), this);
    if (el.dataset.action === "page") return goPage(el.dataset.page);
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

// The weather entity (met.no) only refreshes about once an hour, so "now"
// is read off the hourly forecast between its two nearest hours; the entity
// state is the fallback. Tapping the weather opens weatherSheet().
const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
const compass = (deg) => (deg == null ? "" : COMPASS[Math.round(deg / 45) % 8]);
const sameDay = (a, b) => a.toDateString() === b.toDateString();
const hm = (d) => d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
/** Now, from the hourly forecast: temperature interpolated, the rest from the current hour. */
function weatherNow(w, hourly, now = new Date()) {
  const a = w?.attributes || {};
  const out = { condition: w?.state, temp: a.temperature, humidity: a.humidity, wind: a.wind_speed, bearing: a.wind_bearing,
    uv: a.uv_index, cloud: a.cloud_coverage, dew: a.dew_point, pressure: a.pressure };
  const h = (hourly || []).map((x) => ({ ...x, t: new Date(x.datetime) })).filter((x) => !isNaN(x.t));
  const i = h.findIndex((x) => x.t > now);
  if (i > 0) {
    const p = h[i - 1], n = h[i], f = (now - p.t) / (n.t - p.t);
    if (p.temperature != null && n.temperature != null) out.temp = p.temperature + (n.temperature - p.temperature) * f;
    const c = f < 0.5 ? p : n;
    for (const [k, v] of [["condition", c.condition], ["humidity", c.humidity], ["wind", c.wind_speed], ["bearing", c.wind_bearing], ["uv", c.uv_index], ["cloud", c.cloud_coverage]])
      if (v != null) out[k] = v;
    if (out.condition === "sunny" && !(hourly.isDay ?? true)) out.condition = "clear-night";
  }
  return out;
}
/** Today's entry of the daily forecast (not just the first one). */
const todayForecast = (daily, now = new Date()) => (daily || []).find((d) => sameDay(new Date(d.datetime), now)) || daily?.[0];

class HeaderCard extends KitchenCard {
  tickMs = 10000;
  firstHass() {
    this._subscribeForecast();
  }
  disconnected() {
    for (const u of this._unsubs || []) u.then((f) => f?.()).catch(() => {});
    this._unsubs = null;
  }
  connected() {
    if (this._hass && !this._unsubs) this._subscribeForecast();
  }
  _subscribeForecast() {
    const e = this.config?.weather || "weather.forecast_home";
    if (!this._hass.states[e] || this._unsubs) return;
    const sub = (type, key) =>
      this._hass.connection
        .subscribeMessage((m) => {
          this[key] = m.forecast;
          this._queue();
          this._wxSheet?.update();
        }, { type: "weather/subscribe_forecast", forecast_type: type, entity_id: e })
        .catch(() => null);
    this._unsubs = [sub("daily", "_forecast"), sub("hourly", "_hourly")];
  }
  css = `
    .top { display:flex; justify-content:space-between; align-items:flex-start; padding:4px 6px 0; }
    .clock { font-size:64px; line-height:1; letter-spacing:-.02em; }
    .clock small { font-size:24px; color:var(--sub); margin-left:6px; }
    .date { font-size:20px; color:var(--sub); margin-top:6px; }
    .wx { text-align:right; cursor:pointer; border-radius:14px; padding:4px 6px; margin:-4px -6px; }
    .wx:active { background:var(--card2); }
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
    // A holiday theme counts down to its day (up to two months out).
    const until = th === DEFAULT_THEME ? null : daysUntil(th, now);
    if (until != null && until > 0 && until <= 60) {
      const t = HOLIDAY_TITLE[Theme.key] || th.name;
      parts.push(`${th.emoji} ${until === 1 ? `${t} is tomorrow` : `${until} days to ${t}`}`);
    }
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
    if (a === "wx") {
      this._wxSheet = weatherSheet(this);
      return;
    }
    if (a === "hol-on" || a === "hol-no") answerHoliday(this._hass, a === "hol-on", this.getBoundingClientRect());
    this._queue(true);
  }
  render() {
    const now = new Date();
    let h = now.getHours();
    const ap = h >= 12 ? "PM" : "AM";
    h = h % 12 || 12;
    const w = this._hass.states[this.config.weather || "weather.forecast_home"];
    const f = todayForecast(this._forecast, now);
    const wn = w ? weatherNow(w, this._hourly && Object.assign(this._hourly, { isDay: this._hass.states["sun.sun"]?.state !== "below_horizon" }), now) : null;
    const temp = wn ? Math.round(wn.temp) : null;
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
      <div class="wx"${w ? ` data-action="wx" role="button"` : ""}>
        ${w ? `<div class="t" style="${compact ? "font-size:30px" : ""}">${WEATHER_ICON[wn.condition] || ""} ${temp}°</div>` : ""}
        ${!compact && w ? `<div class="d">${WEATHER_TEXT[wn.condition] || wn.condition}${f ? ` · H ${Math.round(f.temperature)}° L ${Math.round(f.templow)}°` : ""} ›</div>` : ""}
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
  .day { font-size:15px; font-weight:500; margin:14px 0 6px; color:var(--acct); }
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
      html += `<div class="ev span${cls(e, hgt)}"${evAttr(e)} style="top:${top}px;height:${hgt}px;left:calc(${colL(0)} + 4px);width:calc(${colW(ncols)} - 8px);background:${eventColor(e)}">${label(e, hgt)}</div>`;
      continue;
    }
    for (const c of cols) {
      const { top, hgt } = box(e, dayOf(c));
      const same = placed.filter((o) => o.e !== e && o.cols?.includes(c) && o.e.start < e.end && e.start < o.e.end).map((o) => o.e);
      const idx = [e, ...same].sort((a, b) => a.start - b.start || a.title.localeCompare(b.title)).indexOf(e);
      const n = same.length + 1;
      const pad = narrow ? 2 : 4;
      html += `<div class="ev${cls(e, hgt)}"${evAttr(e)} style="top:${top}px;height:${hgt}px;left:calc(${colL(c)} + ${pad}px + (${colW(1)} - ${2 * pad}px) * ${idx} / ${n});width:calc((${colW(1)} - ${2 * pad}px) / ${n} - 2px);background:${eventColor(e)}">${label(e, hgt)}</div>`;
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
  const chips = allDay.length ? `<div class="allday">${allDay.map((e) => `<div class="chip"${evAttr(e)} style="background:${eventColor(e)}">${esc(e.title)}</div>`).join("")}</div>` : "";
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
        return `<div>${on.slice(0, 2).map((e) => `<div class="chip"${evAttr(e)} style="background:${eventColor(e)}">${esc(e.title)}</div>`).join("")}${on.length > 2 ? `<div class="muted" style="font-size:11px">+${on.length - 2}</div>` : ""}</div>`;
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
      <div class="n">${d.getDate()}</div>${shown.map((e) => `<div class="chip"${evAttr(e)} style="background:${eventColor(e)}">${esc(e.title)}</div>`).join("")}${on.length > shown.length ? `<div class="more">+${on.length - shown.length} more</div>` : ""}</button>`;
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
    out += `<div class="row"${evAttr(e)}><div class="bar" style="background:${eventColor(e)}"></div><div class="tm">${e.allDay ? "All day" : `${fmtTime(e.start)}`}</div><div class="t">${esc(e.title)}</div>${whoDots(e)}</div>`;
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
        .map((e) => `<div class="d"${evAttr(e)}>${fmtDay(e.start)} ${e.allDay ? "" : fmtTime(e.start)}</div><div${evAttr(e)}>${esc(e.title)}</div>${whoDots(e)}`)
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
  .next.alldone { color:var(--okt); justify-content:center; }
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
    // Spending stars on a reward doesn't take them off "this week".
    StarsLog.sync(this._hass);
    const wk = this._week?.startStars != null ? st - this._week.startStars + StarsLog.spentSince(m.key, startOfWeek()) : null;
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
    .rk { background:var(--card); box-shadow:var(--shadow); border-radius:22px; padding:16px; display:flex; flex-direction:column; gap:8px; min-height:100%; }
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
    .seg button.done:not(.on) { color:var(--okt); }
    .sec { font-size:13px; color:var(--sub); text-transform:uppercase; letter-spacing:.06em; margin-top:4px; }
    .ch { display:flex; align-items:center; gap:12px; background:var(--card2); border-radius:16px; padding:10px 12px; font-size:18px; min-height:60px; width:100%; }
    .ch .ic { font-size:26px; width:34px; text-align:center; flex:none; }
    .ch .tt { flex:1; }
    .ch .p { font-size:13px; color:var(--acct); font-weight:500; white-space:nowrap; }
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
    .goal { display:block; width:100%; background:var(--card2); border-radius:16px; padding:12px; font-size:14px; color:var(--sub); }
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
    // The reward this kid can get now (the priciest one), or the next one to save for.
    let goal = "";
    if (m.kid && Rewards.active(this._hass)) {
      Rewards.sync(this._hass);
      const rw = Rewards.forMember(m.key);
      const can = rw.filter((r) => r.cost <= st).pop();
      const next = rw.find((r) => r.cost > st);
      if (can) goal = `<button class="goal" data-action="page" data-page="rewards">🎁 <b>Enough stars for ${can.emoji} ${esc(can.title)}!</b> Tap to pick a reward</button>`;
      else if (next) goal = `<button class="goal" data-action="page" data-page="rewards">🎁 <b>${next.cost - st} more stars</b> to ${next.emoji} ${esc(next.title)}</button>`;
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
    .ad { background:var(--card); box-shadow:var(--shadow); border-radius:18px; padding:12px 14px; }
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
      rows.push(`<div class="meal"${ev ? evAttr(ev) : ""}><div class="e">${ev ? "🍽️" : "·"}</div><div><div class="l">${i === 0 ? "Tonight" : fmtDay(d)}</div><div class="n ${ev ? "" : "muted"}">${ev ? esc(ev.title) : "Nothing planned"}</div></div></div>`);
    }
    return `<div class="card"><div class="h">${esc(title)}</div>${rows.join("")}</div>`;
  }
}

/**
 * The Meals tab, like the Apolosign week grid: a row per day (Sun-Sat), a
 * column per meal slot, each slot a meal plan calendar (Mealie makes one per
 * meal type). Back/forward a week; returns to this week after 10 minutes.
 */
// Column tints, one per meal; dark and light values are --slot0..3 (BASE_CSS, lightPalette).
const SLOT_TINT = [0, 1, 2, 3].map((i) => `var(--slot${i})`);
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
    .d.td b { color:var(--acct); }
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
    if (a === "mealie" && this.config.mealie) return openMealie(mealiePlanner(this.config.mealie));
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
    const ml = this.config.mealie ? `<button class="tdb" data-action="mealie">Open Mealie ↗</button>` : "";
    const ctl = `<div class="ctl"><button data-action="prev">‹</button><button data-action="next">›</button><div class="lbl">${md(this._wk)} – ${md(addDays(this._wk, 6))}</div>${isNow ? "" : `<button class="tdb" data-action="today">This week</button>`}${ml}</div>`;
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
          ? `<div class="e">${s.emoji || "🍽️"}</div>${evs.map((e) => `<div class="m"${evAttr(e)}>${esc(e.title)}</div>`).join("")}`
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
    .chip { flex:1; background:var(--card); box-shadow:var(--shadow); border-radius:14px; padding:10px; font-size:13px; color:var(--sub); min-width:0; }
    .chip b { display:block; color:var(--txt); font-size:15px; font-weight:500; margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .chip.on b { color:var(--acct); }
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
    Skin.adopt(this.shadowRoot);
    this._built = {};
    this._touched = Date.now();
  }
  setConfig(config) {
    this.config = config;
    KM.idleMs = (config.idle_home ?? 5) * 60000;
    this._build();
  }
  connectedCallback() {
    KM.remember();
    Bars.attach(this);
    this._backPill();
  }
  disconnectedCallback() {
    Bars.detach(this);
    this._back?.remove();
    this._back = null;
  }
  // On the wall display with HA's menu showing (?disable_km): a pill back to the kiosk.
  _backPill() {
    if (!KM.escaped()) return;
    if (this._back?.isConnected) return;
    const host = document.createElement("div");
    host.className = "tnwks-kitchen-back";
    host.style.cssText = "position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:9400";
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `<style>${BASE_CSS}${MOTION_CSS}
      .back { display:flex; align-items:center; gap:8px; padding:12px 20px; border-radius:26px; background:var(--accent); color:var(--ink);
        font-size:16px; font-weight:500; box-shadow:0 6px 20px rgba(0,0,0,.35); animation:kin .3s ease-out both; }</style>
      <button class="back">🏠 Back to kitchen</button>`;
    root.querySelector("button").addEventListener("click", () => KM.go(KM.kitchenUrl()));
    root.addEventListener("pointerdown", ripple);
    document.body.appendChild(host);
    this._back = host;
  }
  css = `
    :host { display:block; }
    .wrap { display:flex; flex-direction:column; gap:12px; min-height:100vh; background:var(--bg); transition:background-color .6s; position:relative; }
    .scene { position:absolute; inset:0; overflow:hidden; pointer-events:none; z-index:0; }
    .scene svg { position:absolute; left:0; display:block; }
    .scene .sky { top:0; }
    .scene .land { bottom:0; }
    .hdr, .bar, .pages { position:relative; z-index:1; }
    .garland { flex:1; display:flex; justify-content:space-evenly; font-size:19px; opacity:.9; pointer-events:none; overflow:hidden; min-width:0; }
    .garland span { animation:gsw 3.2s ease-in-out infinite alternate; transform-origin:50% 0; }
    .garland span:nth-child(odd) { animation-delay:-1.6s; }
    @keyframes gsw { from { transform:rotate(-9deg); } to { transform:rotate(9deg); } }
    .garland.lights, .garland.bunting { align-self:stretch; align-items:flex-start; opacity:1; background:linear-gradient(var(--sub), var(--sub)) 0 7px / 100% 1px no-repeat; }
    .garland.lights i { position:relative; width:9px; height:13px; margin-top:10px; border-radius:50% 50% 46% 46% / 40% 40% 60% 60%;
      background:var(--c); box-shadow:0 0 10px 2px var(--c); animation:glow 2.6s ease-in-out var(--d) infinite alternate; }
    .garland.lights i:nth-child(even) { margin-top:13px; }
    .garland.lights i::before { content:""; position:absolute; left:2px; top:-4px; width:5px; height:5px; border-radius:1px; background:var(--sub); }
    @keyframes glow { from { opacity:1; } to { opacity:.35; } }
    .garland.bunting { justify-content:space-between; }
    .garland.bunting i { width:20px; height:22px; margin-top:7px; background:var(--c); clip-path:polygon(0 0,100% 0,50% 100%);
      transform-origin:50% 0; animation:gsw 3.2s ease-in-out infinite alternate; }
    .garland.bunting i:nth-child(odd) { animation-delay:-1.6s; }
    .bar { display:flex; gap:8px; align-items:center; position:relative; padding:0 2px; }
    .tabs { display:flex; gap:8px; position:relative; flex-wrap:wrap; }
    .pill { position:absolute; left:0; top:0; height:38px; border-radius:20px; background:var(--accent); transition:transform .28s cubic-bezier(.3,.9,.3,1.1), width .28s, background-color .4s; }
    .tab { padding:9px 16px; border-radius:20px; background:var(--card); color:var(--sub); font-size:15px; font-weight:500; height:38px; position:relative; transition:color .2s, background-color .4s; }
    .tab.on { color:var(--ink); background:transparent; }
    .tools { margin-left:auto; flex:none; display:flex; gap:8px; }
    .tool { display:flex; align-items:center; gap:6px; padding:9px 13px; }
    .tool svg { width:20px; height:20px; fill:currentColor; }
    .tool.ha { padding:9px 12px; }
    .page[hidden] { display:none; }
    .page.enter { animation:kin .24s ease-out both; }
  `;
  async _build() {
    const c = this.config;
    this._helpers = this._helpers || (await window.loadCardHelpers());
    const pad = c.padding ?? 16;
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}${MOTION_CSS}${this.css}</style>
      <div class="wrap" style="padding:${pad}px"><div class="scene" aria-hidden="true"></div><div class="hdr"></div>
      <div class="bar"><div class="tabs"><div class="pill"></div>${(c.pages || [])
        .map((p) => `<button class="tab" data-page="${esc(p.path)}">${esc(p.title)}</button>`)
        .join("")}</div><div class="garland"></div><div class="tools">${c.theme_button === false ? "" : `<button class="tab tool" data-theme aria-label="Display settings">🎨</button>`}${KM.active() ? `<button class="tab tool ha" data-ha aria-label="Home Assistant" title="Home Assistant">${HA_LOGO}</button>` : ""}</div></div>
      <div class="pages">${(c.pages || []).map((p) => `<div class="page" data-page="${esc(p.path)}" hidden></div>`).join("")}</div></div>`;
    const root = this.shadowRoot;
    root.addEventListener("pointerdown", (e) => {
      this._touched = Date.now();
      ripple(e);
    });
    root.addEventListener("click", (e) => {
      const el = e.composedPath().find((n) => n.dataset && (n.dataset.page || "theme" in n.dataset || "ha" in n.dataset) && n.tagName === "BUTTON");
      if (!el) return;
      if ("ha" in el.dataset) leaveToHA(this.config.ha_path || "/lovelace");
      else if ("theme" in el.dataset) themePicker(this._hass);
      else this.show(el.dataset.page);
    });
    this._header = this._helpers.createCardElement({ type: "custom:kitchen-header-card", ...(c.header || {}) });
    root.querySelector(".hdr").appendChild(this._header);
    if (this._hass) this._header.hass = this._hass;
    window.addEventListener("hashchange", () => this.show(this._fromHash(), true));
    window.addEventListener("kitchen-theme", () => {
      this._garland();
      this._scene();
    });
    this._garland();
    this._scene();
    this.show(this._fromHash(), true);
    // The pill is measured off the tab's box, which moves when the web font arrives or the bar wraps.
    document.fonts?.ready.then(() => this._pill(true));
    const ro = new ResizeObserver(() => {
      this._pill(true);
      this._garland();
      this._scene();
    });
    ro.observe(root.querySelector(".tabs"));
    ro.observe(root.querySelector(".garland"));
    ro.observe(root.querySelector(".scene"));
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
  // The holiday's scene behind everything (sceneFor), drawn at the page's width; redrawn when the theme, mode or width changes.
  _scene() {
    const el = this.shadowRoot.querySelector(".scene");
    if (!el) return;
    const W = Math.round(el.clientWidth);
    if (!W) return;
    const sig = `${Theme.key}|${Theme.mode}|${W}`;
    if (sig === this._sSig) return;
    this._sSig = sig;
    const s = sceneFor(Theme.key, Theme.mode === "light", W);
    el.style.background = s ? s.bg : "";
    el.innerHTML = s ? s.html : "";
  }
  // Emoji swinging on the bar, or a string of lights or bunting as many as fit (redone when the bar's width changes).
  _garland() {
    const g = this.shadowRoot.querySelector(".garland");
    if (!g) return;
    const t = Theme.current().garland;
    const kind = !t || REDUCED() ? "" : typeof t === "string" ? "emoji" : t.lights ? "lights" : "bunting";
    const n = kind === "lights" ? Math.max(3, Math.floor((g.clientWidth || 400) / 30)) : kind === "bunting" ? Math.max(3, Math.floor((g.clientWidth || 400) / 34)) : 0;
    const sig = `${Theme.key}|${kind}|${n}`;
    if (sig === this._gSig) return;
    this._gSig = sig;
    g.className = `garland${kind === "emoji" || !kind ? "" : ` ${kind}`}`;
    if (kind === "emoji") g.innerHTML = t.split(" ").map((x) => `<span>${x}</span>`).join("");
    else if (kind) {
      const cs = t.lights || t.bunting;
      g.innerHTML = Array.from({ length: n }, (_, i) => `<i style="--c:${cs[i % cs.length]};--d:${(-((i * 0.83) % 2.6)).toFixed(2)}s"></i>`).join("");
    } else g.innerHTML = "";
  }
  set hass(h) {
    this._hass = h;
    if (!this._readyHook && h?.connection?.addEventListener) {
      // After a reconnect the app may have re-read HA's own colors; send ours again.
      this._readyHook = true;
      h.connection.addEventListener("ready", () => Bars._color && Bars.notify());
    }
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
  if (!isWall()) return null; // the offer sets the wall display's theme, so only it asks
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

// ------------------------------------------------------------ LIFX themes

// The theme list is whatever HA's lifx.paint_theme offers (its selector
// options, from the aiolifx-themes library that follows the LIFX app), read
// at runtime, so new LIFX themes show up with HA updates and nothing here
// lists them. This only sorts them: by name into groups, and seasonal ones
// by the months they suit. Unknown names land in Moods.
const LIFX_GROUPS = [
  ["Holidays", "🎉", "halloween blood_moon bloodlust book_of_the_dead calaveras fright_night ghostly graveyard_chill haunted_fog paranormal pumpkin pumpkin_glow pumpkin_party redrum scream_queen slasher spiders_lair toxic_cauldron vampires_den witchs_ritual witchy zombie zombie_apocalypse thanksgiving turkey_dinner cranberry_harvest christmas baubles candy_cane candy_cane_twist deck_the_halls festive holly mistletoe santa santas_candy santas_workshop gold_star hanukkah menorah kwanzaa be_my_valentine valentines st_patricks_day shamrock lucky_shamrock leprechaun_treasure molly_malone whats_the_craic the_tricolour snake_banisher independence old_glory memorial_day arlington taps canada_day parade pride proud diwali ramadan oktoberfest"],
  ["Seasons", "🍂", "autumn autumn_table fall pumpkin_spice wheat_glow warm_ember sage_and_cedar spring cherry_blossom poppy winter_night winter_wonderland snowflake arctic pine_glow crystal_twist"],
  ["Music", "🎵", "classic_rock classical dance_pop disco funk garage_rock hip_hop_rap indie_pop jazz lo_fi pop psychedelic_rock punk synthwave vaporwave party neon"],
  ["Art", "🎨", "bijutsukai gauguin hokusai kandinsky klimt matisse mondrian monet rousseau van_gogh"],
  ["Space", "🪐", "aurora aurora_borealis extraterrestrial galaxy jupiter mars mercury moon neptune pluto saturn sci_fi spacey stardust sun uranus venus earth cyberpunk"],
  ["Nature", "🌊", "beach clouds coral_reef deep_sea desert fire forest outback storm_front tropical water ocean"],
  ["Moods", "✨", ""],
];
// Months (1-12) each seasonal theme suits; "In season" is this month's.
const LIFX_MONTHS = {
  10: "halloween blood_moon bloodlust book_of_the_dead fright_night ghostly graveyard_chill haunted_fog paranormal pumpkin pumpkin_glow pumpkin_party redrum scream_queen slasher spiders_lair toxic_cauldron vampires_den witchs_ritual witchy zombie zombie_apocalypse oktoberfest diwali autumn fall pumpkin_spice",
  11: "calaveras thanksgiving turkey_dinner cranberry_harvest autumn autumn_table fall pumpkin_spice wheat_glow warm_ember sage_and_cedar diwali",
  12: "christmas baubles candy_cane candy_cane_twist deck_the_halls festive holly mistletoe santa santas_candy santas_workshop gold_star hanukkah menorah kwanzaa winter_wonderland snowflake pine_glow crystal_twist winter_night",
  1: "winter_night winter_wonderland snowflake arctic crystal_twist",
  2: "be_my_valentine valentines love romance winter_night snowflake",
  3: "st_patricks_day shamrock lucky_shamrock leprechaun_treasure molly_malone whats_the_craic the_tricolour snake_banisher spring cherry_blossom",
  4: "spring cherry_blossom poppy",
  5: "spring cherry_blossom poppy memorial_day arlington taps",
  6: "pride proud old_glory",
  7: "independence old_glory canada_day parade",
  8: "beach tropical sun",
  9: "autumn fall oktoberfest pumpkin_spice wheat_glow",
};
// Library spellings that duplicate another theme.
const LIFX_DUPES = { energising: "energizing", forrest: "forest" };
const lifxLabel = (t) => t.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()).replace(/\bSt\b/, "St.").replace(/\bS\b/, "s").replace(/Witchs/, "Witch's").replace(/Santas/, "Santa's").replace(/Whats/, "What's").replace(/Lo Fi/, "Lo-fi").replace(/ (And|Of|The|For|In) /g, (m) => m.toLowerCase());

class LifxThemesCard extends HTMLElement {
  setConfig(config) {
    if (!Array.isArray(config.entities) || !config.entities.length) throw new Error("entities: the LIFX lights to paint");
    this.config = config;
    this._open = null;
    this._key = `tnwks-lifx-theme:${config.entities.join(",")}`;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._themes = null;
    this._render();
  }
  set hass(hass) {
    this._hass = hass;
    const opts = hass.services?.lifx?.paint_theme?.fields?.theme?.selector?.select?.options;
    const list = (opts || []).map((o) => (typeof o === "string" ? o : o.value)).filter((t) => !(LIFX_DUPES[t] && opts.includes(LIFX_DUPES[t])));
    if (list.join() !== (this._themes || []).join()) {
      this._themes = list;
      this._render();
    }
  }
  getCardSize() {
    return 3;
  }
  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }
  _groups() {
    const known = new Map();
    for (const [name, icon, names] of LIFX_GROUPS) for (const t of names.split(" ").filter(Boolean)) known.set(t, name);
    const out = LIFX_GROUPS.map(([name, icon]) => ({ name, icon, themes: [] }));
    for (const t of this._themes) out.find((g) => g.name === (known.get(t) || "Moods")).themes.push(t);
    return out.filter((g) => g.themes.length);
  }
  _render() {
    const root = this.shadowRoot;
    if (!root || !this.config) return;
    const last = localStorage.getItem(this._key);
    const chip = (t) => `<button class="chip${t === last ? " on" : ""}" data-theme="${esc(t)}">${esc(lifxLabel(t))}</button>`;
    let body;
    if (!this._themes) body = `<div class="none">Waiting for Home Assistant…</div>`;
    else if (!this._themes.length) body = `<div class="none">The LIFX integration offers no themes (is it loaded?).</div>`;
    else {
      const month = new Date().getMonth() + 1;
      const now = (LIFX_MONTHS[month] || "").split(" ").filter((t) => this._themes.includes(t));
      const groups = this._groups();
      body = `${now.length ? `<div class="lbl">In season</div><div class="chips">${now.map(chip).join("")}</div>` : ""}
        <div class="tabs">${groups.map((g) => `<button class="tab${this._open === g.name ? " on" : ""}" data-group="${esc(g.name)}">${g.icon} ${esc(g.name)} <span>${g.themes.length}</span></button>`).join("")}</div>
        ${this._open ? `<div class="chips">${(groups.find((g) => g.name === this._open)?.themes || []).map(chip).join("")}</div>` : ""}`;
    }
    root.innerHTML = `<style>
      ha-card { padding:12px 14px 14px; }
      .top { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
      .top b { flex:1; font-size:16px; font-weight:500; }
      .top button { font:inherit; font-size:13px; padding:6px 12px; border-radius:16px; border:0; cursor:pointer; background:var(--secondary-background-color); color:var(--primary-text-color); }
      .lbl { font-size:12px; color:var(--secondary-text-color); margin:8px 0 6px; text-transform:uppercase; letter-spacing:.04em; }
      .chips, .tabs { display:flex; flex-wrap:wrap; gap:6px; }
      .tabs { margin-top:10px; }
      .chips + .tabs { margin-top:12px; }
      .tabs + .chips { margin-top:10px; }
      button.chip, button.tab { font:inherit; font-size:13px; padding:7px 12px; border-radius:16px; border:1px solid var(--divider-color); background:none; color:var(--primary-text-color); cursor:pointer; }
      button.tab span { color:var(--secondary-text-color); font-size:12px; }
      button.tab.on { background:var(--secondary-background-color); }
      button.chip.on { background:var(--primary-color); border-color:var(--primary-color); color:var(--text-primary-color, #fff); }
      button.chip.busy { opacity:.5; }
      .none { color:var(--secondary-text-color); font-size:14px; padding:6px 0; }
    </style><ha-card><div class="top"><b>${esc(this.config.title || "Light themes")}</b>${this._themes?.length ? `<button data-shuffle>🔀 Surprise me</button>` : ""}</div>${body}</ha-card>`;
    root.onclick = (e) => {
      const el = e.composedPath().find((n) => n.dataset && (n.dataset.theme || n.dataset.group || "shuffle" in n.dataset));
      if (!el) return;
      if (el.dataset.group) {
        this._open = this._open === el.dataset.group ? null : el.dataset.group;
        return this._render();
      }
      let t = el.dataset.theme;
      if (!t) {
        const month = new Date().getMonth() + 1;
        const pool = (LIFX_MONTHS[month] || "").split(" ").filter((x) => this._themes.includes(x));
        const from = pool.length ? pool : this._themes;
        t = from[Math.floor(Math.random() * from.length)];
      }
      this._paint(t, el);
    };
  }
  async _paint(theme, el) {
    el.classList.add("busy");
    try {
      await this._hass.callService("lifx", "paint_theme", { theme, transition: this.config.transition ?? 1, power_on: true }, { entity_id: this.config.entities });
      localStorage.setItem(this._key, theme);
    } catch (err) {
      console.warn("tnwks lifx themes:", err);
    }
    this._render();
  }
}

// --------------------------------------------------------------- rewards

// The kids spend stars on rewards the parents set up (packages/
// kitchen_rewards.yaml). Rewards are the items of todo.kitchen_rewards
// (summary = name, description = JSON {"emoji", "cost", optional
// "members"}); every redemption, hand-made change and undo is an item of
// todo.kitchen_stars_log (description JSON {"member", "delta", "kind",
// "emoji", "by"}, due = when, completed = undone). All changes go through
// the package's scripts, which check who's asking: anyone may redeem, only
// the managers (the users of MANAGERS' person entities) may define rewards,
// add or remove stars, or undo. The parent controls show only for those
// users and never on the wall display, so the kids' screen can't reach them.
const REWARDS = "todo.kitchen_rewards";
const STARS_LOG = "todo.kitchen_stars_log";
const MANAGERS = ["person.adult1", "person.adult2", "person.ori"];
const kids = () => FAMILY.filter((m) => m.kid);

/** A parent on their own device: a manager's HA user, and not the wall display. */
function isManager(hass) {
  const id = hass?.user?.id;
  // The wall display is the remembered kiosk or, before it has remembered one, its user agent.
  if (!id || isWall() || WALL_UA.test(navigator.userAgent)) return false;
  return MANAGERS.some((p) => hass.states[p]?.attributes?.user_id === id);
}
/** A manager's first name for "by ..." in the log. */
function managerName(hass, id) {
  const p = MANAGERS.map((e) => hass.states[e]).find((s) => s && s.attributes.user_id === id);
  return p ? String(p.attributes.friendly_name || "").split(" ")[0] : "";
}

/** A live copy of one to-do list (todo/item/subscribe); changes re-render every card like the routines do. */
function todoStore(entity, parse) {
  return {
    items: null,
    _sub: null,
    active(hass) {
      return !!hass?.states?.[entity];
    },
    sync(hass) {
      if (this._sub || !this.active(hass)) return;
      this._sub = hass.connection
        .subscribeMessage((msg) => {
          this.items = (msg.items || []).map(parse).filter(Boolean);
          window.dispatchEvent(new Event("kitchen-routines"));
        }, { type: "todo/item/subscribe", entity_id: entity })
        .catch(() => {
          this._sub = null;
          return null;
        });
    },
  };
}
const jsonDesc = (i) => {
  try {
    const m = JSON.parse(i.description || "{}");
    return m && typeof m === "object" ? m : {};
  } catch (_) {
    return {};
  }
};
const Rewards = todoStore(REWARDS, (i, index) => {
  const m = jsonDesc(i);
  const cost = Math.round(Number(m.cost) || 0);
  if (cost <= 0) return null;
  return { uid: i.uid, index, title: i.summary, emoji: m.emoji || "🎁", cost, members: Array.isArray(m.members) && m.members.length ? m.members : null };
});
Rewards.forMember = function (key) {
  return (this.items || []).filter((r) => !key || !r.members || r.members.includes(key)).sort((a, b) => a.cost - b.cost || a.index - b.index);
};
const StarsLog = todoStore(STARS_LOG, (i) => {
  const m = jsonDesc(i);
  if (!m.member || !Number(m.delta)) return null;
  const at = new Date(i.due || 0);
  return { uid: i.uid, title: i.summary, at: isNaN(at) ? new Date(0) : at, undone: i.status === "completed", member: m.member,
    delta: Math.round(Number(m.delta)), kind: m.kind || "adjust", emoji: m.emoji || (m.delta > 0 ? "⭐" : "🔻"), by: m.by || null };
});
StarsLog.recent = function (n) {
  return [...(this.items || [])].sort((a, b) => b.at - a.at).slice(0, n);
};
/** Stars a kid spent on rewards since `since` (still counted, not undone). */
StarsLog.spentSince = function (key, since) {
  return (this.items || []).filter((e) => e.member === key && e.kind === "reward" && !e.undone && e.at >= since).reduce((n, e) => n - e.delta, 0);
};

function fmtWhen(d, now = new Date()) {
  if (sameDay(d, now)) return fmtTime(d);
  if (sameDay(d, addDays(now, -1))) return "Yesterday";
  if (now - d < 6 * MS_DAY) return fmtDay(d);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** A short message over the bottom of the screen (errors from the scripts, a reward picked). */
function toast(text, ms = 3200) {
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:9600;pointer-events:none";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>${BASE_CSS}${MOTION_CSS}
    .t { background:var(--txt); color:var(--bg); padding:13px 20px; border-radius:22px; font-size:16px; font-weight:500; box-shadow:0 8px 24px rgba(0,0,0,.4);
      animation:kin .25s ease-out both; max-width:86vw; text-align:center; }</style><div class="t">${esc(text)}</div>`;
  document.body.appendChild(host);
  setTimeout(() => host.remove(), ms);
}
/**
 * Runs one of the rewards scripts. A refusal ("Only a parent can ...") comes
 * back as the response {ok: false, error}, shows as a toast and rejects.
 */
function rewardScript(hass, name, data) {
  return hass
    .callService("script", name, data, undefined, true, true)
    .then((res) => {
      const r = res?.response;
      if (r && r.ok === false) throw new Error(r.error || "That didn't work.");
      return r;
    })
    .catch((err) => {
      toast(err?.message || String(err));
      throw err;
    });
}

const REWARD_EMOJI = ["🎁", "🍦", "🍭", "🍫", "🍩", "🧁", "🍿", "🍕", "🍔", "🌮", "🥤", "🎬", "📺", "🎮", "📱", "🎧", "🧸", "🎨", "📚", "🧩",
  "🎲", "🛝", "🏊", "🚲", "⚽", "🎳", "🎡", "🎟️", "🏞️", "⛺", "🛍️", "💵", "🪙", "🌙", "🛏️", "🎤", "🐶", "🚗", "👑", "⭐"];

/** Pick a reward: who's getting it, what it costs them, then spend the stars. */
function redeemSheet(card, reward, who, tile) {
  const latest = () => card._hass;
  const elig = kids().filter((m) => !reward.members || reward.members.includes(m.key));
  let pick = elig.find((m) => m.key === who)?.key || (elig.length === 1 ? elig[0].key : null);
  let busy = false;
  const sheet = openSheet({
    render() {
      const h = latest();
      const st = pick ? stars(h, pick) : 0;
      const can = pick && st >= reward.cost;
      const people = elig.map((m) => {
        const s = stars(h, m.key);
        return `<button class="${pick === m.key ? "on" : ""}" data-action="who" data-key="${m.key}"><div class="av" style="background:${m.color}">${esc(m.name[0])}</div>${esc(m.name)} · ⭐ ${s}${s < reward.cost ? ` <i style="font-style:normal;opacity:.75">(${reward.cost - s} more)</i>` : ""}</button>`;
      }).join("");
      const note = !pick ? "Tap your name." : can ? `${esc(MEMBER[pick].name)} will have ⭐ ${st - reward.cost} left.` : `${esc(MEMBER[pick].name)} needs ${reward.cost - st} more stars. Keep going!`;
      return `<div class="rw"><span>${reward.emoji}</span><b>${esc(reward.title)}</b><i>⭐ ${reward.cost}</i></div>
        <div class="lb">Who's getting it?</div><div class="chips">${people}</div>
        <div class="hint" style="font-size:15px;margin-top:12px">${note}</div>
        <div class="acts"><button data-action="cancel">Not now</button><button class="go" data-action="go" ${can && !busy ? "" : "disabled"}>Get it! −${reward.cost} ⭐</button></div>`;
    },
    onAction(a, d, s) {
      if (a === "who") pick = d.key;
      else if (a === "go" && !busy) {
        busy = true;
        s.update();
        const name = MEMBER[pick].name;
        rewardScript(latest(), "kitchen_reward_redeem", { member: pick, reward: reward.uid })
          .then(() => {
            s.close();
            const [x, y] = tile?.isConnected ? FX.at(tile) : [innerWidth / 2, innerHeight / 2];
            FX.float(x, y, `−${reward.cost} ⭐`);
            FX.confetti(card.getBoundingClientRect());
            toast(`🎉 ${name} got ${reward.emoji} ${reward.title}!`);
          })
          .catch(() => {
            busy = false;
            s.update();
          });
        return;
      }
      s.update();
    },
  });
  return sheet;
}

/** Parents only: add, change or delete a reward. */
function rewardEditor(hass, reward) {
  const st = reward
    ? { title: reward.title, emoji: reward.emoji, cost: reward.cost, members: reward.members ? [...reward.members] : [] }
    : { title: "", emoji: "🎁", cost: 20, members: [] };
  let armed = false;
  const sheet = openSheet({
    render() {
      const all = `<button class="${st.members.length ? "" : "on"}" data-action="for" data-key="">Everyone</button>`;
      const people = kids().map((m) => `<button class="${st.members.includes(m.key) ? "on" : ""}" data-action="for" data-key="${m.key}"><div class="av" style="background:${m.color}">${esc(m.name[0])}</div>${esc(m.name)}</button>`).join("");
      const emo = REWARD_EMOJI.map((e) => `<button class="${st.emoji === e ? "on" : ""}" data-action="emoji" data-key="${e}">${e}</button>`).join("");
      const step = [-10, -1, 1, 10].map((n) => `<button data-action="cost" data-key="${n}" style="${Math.abs(n) > 1 ? "width:58px;font-size:17px" : ""}">${n > 0 ? "+" : "−"}${Math.abs(n) > 1 ? Math.abs(n) : ""}</button>`);
      return `<div class="tt">${st.emoji} ${reward ? "Edit reward" : "New reward"}</div>
        <input class="tx" name="title" placeholder="What do they get? (e.g. Pick the movie)" value="${esc(st.title)}" autocomplete="off" autocapitalize="sentences" enterkeyhint="done">
        <div class="lb">Costs</div><div class="step">${step[0]}${step[1]}<b>⭐ ${st.cost}</b>${step[2]}${step[3]}</div>
        <div class="lb">For</div><div class="chips">${all}${people}</div>
        <div class="lb">Icon</div><div class="emo">${emo}</div>
        <div class="acts">${reward ? `<button class="del ${armed ? "arm" : ""}" data-action="delete">${armed ? "Tap again to delete" : "Delete"}</button>` : ""}
          <button data-action="cancel">Cancel</button><button class="go" data-action="save" ${st.title.trim() ? "" : "disabled"}>${reward ? "Save" : "Add reward"}</button></div>`;
    },
    onInput(t, s) {
      if (t.name === "title") {
        st.title = t.value;
        s.update();
      }
    },
    onAction(a, d, s) {
      if (a !== "delete") armed = false;
      if (a === "for") st.members = d.key ? [d.key] : [];
      else if (a === "emoji") st.emoji = d.key;
      else if (a === "cost") st.cost = Math.max(1, Math.min(1000, st.cost + Number(d.key)));
      else if (a === "delete") {
        if (!armed) armed = true;
        else {
          rewardScript(hass, "kitchen_reward_save", { reward: reward.uid, delete: true }).catch(() => {});
          return s.close();
        }
      } else if (a === "save") {
        if (!st.title.trim()) return;
        const data = { name: st.title.trim(), emoji: st.emoji, cost: st.cost, members: st.members };
        if (reward) data.reward = reward.uid;
        rewardScript(hass, "kitchen_reward_save", data).catch(() => {});
        return s.close();
      }
      s.update();
    },
  });
  if (!reward) setTimeout(() => sheet.root.querySelector("input.tx")?.focus(), 250);
  return sheet;
}

const TAKE_REASONS = ["Not listening", "Being unkind", "Not sharing", "Talking back"];
const GIVE_REASONS = ["Bonus", "Helped out", "Being kind", "Great listening"];
/** Parents only: take stars away (or give some) by hand, with a reason for the log. */
function starsSheet(card, who) {
  const latest = () => card._hass;
  const st = { member: who || kids()[0].key, delta: -1, reason: "" };
  const sheet = openSheet({
    render() {
      const h = latest();
      const cur = stars(h, st.member);
      const next = Math.max(0, cur + st.delta);
      const people = kids().map((m) => `<button class="${st.member === m.key ? "on" : ""}" data-action="who" data-key="${m.key}"><div class="av" style="background:${m.color}">${esc(m.name[0])}</div>${esc(m.name)} · ⭐ ${stars(h, m.key)}</button>`).join("");
      const take = st.delta < 0;
      const n = Math.abs(st.delta);
      const quick = [-10, -5, -1, 1, 5, 10].map((x) => `<button data-action="amt" data-key="${x}">${x > 0 ? "+" : "−"}${Math.abs(x)}</button>`).join("");
      const reasons = (take ? TAKE_REASONS : GIVE_REASONS).map((r) => `<button class="${st.reason === r ? "on" : ""}" data-action="reason" data-key="${esc(r)}">${esc(r)}</button>`).join("");
      return `<div class="tt">${take ? "🔻 Take away stars" : "⭐ Give stars"}</div>
        <div class="lb">Who</div><div class="chips">${people}</div>
        <div class="lb">How many</div><div class="amt" style="color:${take ? "var(--danger)" : "var(--okt)"}">${take ? "−" : "+"}${n} ⭐</div>
        <div class="chips">${quick}</div>
        <div class="hint" style="font-size:15px">⭐ ${cur} → ⭐ ${next}${take && cur + st.delta < 0 ? " (stars don't go below 0)" : ""}</div>
        <div class="lb">Why</div><div class="chips">${reasons}</div>
        <input class="tx" name="reason" style="margin-top:10px" placeholder="Or type a reason" value="${esc(st.reason)}" autocomplete="off" autocapitalize="sentences" enterkeyhint="done">
        <div class="acts"><button data-action="cancel">Cancel</button><button class="go" data-action="go" ${next !== cur ? "" : "disabled"}>${take ? `Take away ${n}` : `Give ${n}`}</button></div>`;
    },
    onInput(t, s) {
      if (t.name === "reason") {
        st.reason = t.value;
        s.update();
      }
    },
    onAction(a, d, s) {
      if (a === "who") st.member = d.key;
      else if (a === "amt") {
        let v = st.delta + Number(d.key);
        // Crossing zero flips the direction instead of landing on nothing.
        if (v === 0) v = Number(d.key) > 0 ? 1 : -1;
        if (Math.sign(v) !== Math.sign(st.delta)) st.reason = "";
        st.delta = Math.max(-100, Math.min(100, v));
      } else if (a === "reason") st.reason = st.reason === d.key ? "" : d.key;
      else if (a === "go") {
        const { member: m, delta, reason } = st;
        rewardScript(latest(), "kitchen_stars_adjust", { member: m, delta, reason: reason.trim() })
          .then(() => toast(`${delta < 0 ? "−" : "+"}${Math.abs(delta)} ⭐ ${MEMBER[m].name}`))
          .catch(() => {});
        return s.close();
      }
      s.update();
    },
  });
  return sheet;
}

const REWARDS_CSS = `
  .rc { background:var(--card); box-shadow:var(--shadow); border-radius:22px; padding:16px; display:flex; flex-direction:column; gap:14px; }
  .top { display:flex; align-items:center; gap:10px; }
  .top .t { font-size:24px; font-weight:500; }
  .top .q { color:var(--sub); font-size:15px; }
  .tools { margin-left:auto; display:flex; gap:8px; }
  .tools button { height:38px; padding:0 14px; border-radius:19px; background:var(--card2); color:var(--txt); font-size:15px; font-weight:500; display:flex; align-items:center; gap:6px; }
  .tools button.on { background:var(--accent); color:var(--ink); }
  .wals { display:flex; gap:12px; }
  .wal { flex:1; display:flex; align-items:center; gap:12px; background:var(--card2); border-radius:18px; padding:12px 14px; border:3px solid transparent; transition:border-color .2s, background-color .2s; position:relative; }
  .wal .av { width:50px; height:50px; font-size:22px; }
  .wal .nm { font-size:20px; font-weight:500; }
  .wal .sub { font-size:13px; color:var(--sub); }
  .wal .st { margin-left:auto; font-size:26px; font-weight:700; white-space:nowrap; display:flex; flex-direction:column; align-items:flex-end; gap:6px; }
  .wal .adj { height:30px; padding:0 12px; border-radius:15px; background:var(--card); font-size:13px; font-weight:500; color:var(--txt); display:grid; place-items:center; }
  .grid { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
  .tile { background:var(--card2); border-radius:18px; padding:14px 10px 12px; display:flex; flex-direction:column; align-items:center; gap:6px; text-align:center; min-height:150px; position:relative;
    transition:opacity .2s, transform .15s, box-shadow .2s; }
  .tile .e { font-size:46px; line-height:1.1; }
  .tile .n { font-size:16px; font-weight:500; line-height:1.2; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
  .tile .c { margin-top:auto; font-size:14px; font-weight:700; padding:4px 10px; border-radius:12px; background:var(--card); color:var(--acct); }
  .tile.can { box-shadow:inset 0 0 0 3px var(--accent); }
  .tile.can .c { background:var(--accent); color:var(--ink); }
  .tile.cant { opacity:.6; }
  .tile .pb { width:80%; height:6px; border-radius:3px; background:var(--card); overflow:hidden; }
  .tile .pb div { height:100%; border-radius:3px; }
  .tile .more { font-size:12px; color:var(--sub); }
  .tile .for { position:absolute; top:8px; left:8px; width:22px; height:22px; font-size:11px; }
  .tile .pen { position:absolute; top:8px; right:10px; color:var(--sub); font-size:16px; }
  .tile.add { border:2px dashed var(--line); background:transparent; justify-content:center; color:var(--sub); font-size:15px; }
  .tile.add .e { font-size:40px; font-weight:300; line-height:1; }
  .sec { font-size:13px; color:var(--sub); text-transform:uppercase; letter-spacing:.06em; }
  .log { display:flex; flex-direction:column; gap:6px; }
  .le { display:flex; align-items:center; gap:10px; background:var(--card2); border-radius:14px; padding:8px 12px; font-size:15px; min-height:48px; }
  .le .ic { font-size:22px; width:28px; text-align:center; flex:none; }
  .le .av { width:26px; height:26px; font-size:12px; }
  .le .tt { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .le .tt i { font-style:normal; color:var(--sub); font-size:12px; margin-left:6px; }
  .le .d { font-weight:700; white-space:nowrap; }
  .le .d.neg { color:var(--danger); }
  .le .d.pos { color:var(--okt); }
  .le.undone { opacity:.45; }
  .le.undone .tt, .le.undone .d { text-decoration:line-through; }
  .le .un { height:30px; padding:0 12px; border-radius:15px; background:var(--card); font-size:13px; color:var(--sub); display:grid; place-items:center; flex:none; }
  .le .un.arm { background:#c62828; color:#fff; }
  .none { color:var(--sub); font-size:15px; padding:6px 2px; }
  /* Phones: the stars go under the name. */
  @media (max-width: 560px) {
    .wals { gap:8px; }
    .wal { flex-wrap:wrap; gap:8px 10px; padding:10px; }
    .wal .av { width:40px; height:40px; font-size:18px; }
    .wal .nm { font-size:18px; }
    .wal .st { margin-left:0; width:100%; flex-direction:row; justify-content:space-between; align-items:center; font-size:22px; }
    .grid { gap:8px; }
    .tile .e { font-size:38px; }
    .tile .n { font-size:14px; }
    .top .q { display:none; }
  }
`;
class RewardsCard extends KitchenCard {
  css = REWARDS_CSS;
  action(a, d, e, el) {
    const h = this._hass;
    if (a === "who") {
      this._who = this._who === d.key ? null : d.key;
      this._whoAt = Date.now();
    } else if (a === "reward") {
      const r = (Rewards.items || []).find((x) => x.uid === d.uid);
      if (!r) return;
      if (this._edit) return rewardEditor(h, r);
      return redeemSheet(this, r, this._who, el);
    } else if (a === "add") return rewardEditor(h, null);
    else if (a === "edit") this._edit = !this._edit;
    else if (a === "adjust") {
      e.stopPropagation();
      return starsSheet(this, d.key);
    } else if (a === "undo") {
      if (this._armUndo !== d.uid || Date.now() - this._armAt > 5000) {
        this._armUndo = d.uid;
        this._armAt = Date.now();
      }
      else {
        this._armUndo = null;
        rewardScript(h, "kitchen_stars_undo", { entry: d.uid }).catch(() => {});
      }
    }
    this._queue(true);
  }
  render() {
    const h = this._hass;
    if (!Rewards.active(h)) {
      return `<div class="rc"><div class="top"><div class="t">🎁 Rewards</div></div><div class="none">Rewards aren't set up yet. They need the Local To-do lists "Kitchen rewards" and "Kitchen stars log" in Home Assistant.</div></div>`;
    }
    Rewards.sync(h);
    StarsLog.sync(h);
    const mgr = isManager(h);
    if (!mgr) this._edit = false;
    if (this._armUndo && Date.now() - this._armAt > 5000) this._armUndo = null;
    // The kid who's picking is forgotten after 2 minutes, so the next one starts fresh.
    if (this._who && Date.now() - (this._whoAt || 0) > 2 * 60 * 1000) this._who = null;
    const who = this._who ? MEMBER[this._who] : null;
    const wals = kids().map((m) => {
      const s = stars(h, m.key);
      const on = this._who === m.key;
      return `<button class="wal" data-action="who" data-key="${m.key}" style="${on ? `border-color:${m.color};background:color-mix(in srgb, ${m.color} 18%, var(--card2))` : ""}">
        <div class="av" style="background:${m.color}">${esc(m.name[0])}</div><div><div class="nm">${esc(m.name)}</div><div class="sub">${on ? "Picking now" : "Tap to pick"}</div></div>
        <div class="st">⭐ ${s}${mgr ? `<span class="adj" data-action="adjust" data-key="${m.key}">± stars</span>` : ""}</div></button>`;
    }).join("");
    const list = Rewards.forMember(who?.key);
    const st = who ? stars(h, who.key) : 0;
    const tiles = list.map((r) => {
      const can = who && st >= r.cost;
      const pct = who ? Math.min(100, Math.round((100 * st) / r.cost)) : 0;
      const forOne = r.members?.length === 1 && !who ? MEMBER[r.members[0]] : null;
      return `<button class="tile ${who ? (can ? "can" : "cant") : ""}" data-k="r${esc(r.uid)}" data-action="reward" data-uid="${esc(r.uid)}">
        ${forOne ? `<div class="av for" style="background:${forOne.color}">${esc(forOne.name[0])}</div>` : ""}${this._edit ? `<span class="pen">✎</span>` : ""}
        <span class="e">${r.emoji}</span><span class="n">${esc(r.title)}</span>
        ${who && !can ? `<div class="pb"><div style="width:${pct}%;background:${who.color}"></div></div><span class="more">${r.cost - st} more</span>` : ""}
        <span class="c">⭐ ${r.cost}</span></button>`;
    });
    if (mgr) tiles.push(`<button class="tile add" data-k="add" data-action="add"><span class="e">+</span>Add a reward</button>`);
    const grid = tiles.length
      ? `<div class="grid">${tiles.join("")}</div>`
      : `<div class="none">No rewards yet. Mom or Dad can add them from their phone.</div>`;
    const tools = mgr && (Rewards.items || []).length
      ? `<div class="tools"><button class="${this._edit ? "on" : ""}" data-action="edit">${this._edit ? "Done" : "✎ Edit"}</button></div>`
      : "";
    const recent = StarsLog.recent(this.config.log ?? 5);
    const log = recent.map((x) => {
      const m = MEMBER[x.member];
      const by = x.kind === "adjust" && x.by ? managerName(h, x.by) : "";
      return `<div class="le ${x.undone ? "undone" : ""}" data-k="l${esc(x.uid)}"><span class="ic">${x.emoji}</span>
        ${m ? `<div class="av" style="background:${m.color}">${esc(m.name[0])}</div>` : ""}
        <span class="tt">${esc(x.title)}<i>${fmtWhen(x.at)}${by ? ` · ${esc(by)}` : ""}${x.undone ? " · undone" : ""}</i></span>
        <span class="d ${x.delta < 0 ? "neg" : "pos"}">${x.delta < 0 ? "−" : "+"}${Math.abs(x.delta)} ⭐</span>
        ${mgr && !x.undone ? `<button class="un ${this._armUndo === x.uid ? "arm" : ""}" data-action="undo" data-uid="${esc(x.uid)}">${this._armUndo === x.uid ? "Sure?" : "Undo"}</button>` : ""}</div>`;
    }).join("");
    const q = who ? `${esc(who.name)} has ⭐ ${st}` : "Who's picking?";
    return `<div class="rc">
      <div class="top"><div class="t">🎁 Rewards</div><div class="q">${this._edit ? "Tap a reward to change it" : q}</div>${tools}</div>
      <div class="wals">${wals}</div>
      ${grid}
      ${recent.length ? `<div class="sec">Recent</div><div class="log">${log}</div>` : ""}
    </div>`;
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
  "kitchen-rewards-card": [RewardsCard, "Kitchen rewards", "Kids spend their stars on rewards; parents set them up from their phones"],
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
  "tnwks-lifx-themes-card": [LifxThemesCard, "LIFX themes", "Paint LIFX lights with the themes HA's LIFX integration offers"],
};
window.customCards = window.customCards || [];
for (const [tag, [cls, name, description]] of Object.entries(CARDS)) {
  if (!customElements.get(tag)) customElements.define(tag, cls);
  window.customCards.push({ type: tag, name, description });
}
console.info(`%c tnwks kitchen cards ${VERSION} `, "background:#ff9f43;color:#1b1206;border-radius:4px");
