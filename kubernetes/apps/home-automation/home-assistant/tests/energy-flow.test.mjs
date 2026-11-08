import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// The calculation helpers have no DOM dependencies. Registration is stubbed;
// actual card layout, keyboard interaction and entity details are checked in
// the browser against HA's real data before deployment.
globalThis.HTMLElement = class {};
globalThis.customElements = { get() {}, define() {} };
const source = readFileSync(
  new URL(
    "../app/config/www/tnwks-energy/energy-flow-card.js",
    import.meta.url,
  ),
  "utf8",
);
const { dayStart, powerWatts, energyChange, branches } = await import(
  "data:text/javascript;base64," + Buffer.from(source).toString("base64")
);

test("daily energy follows HA's timezone across UTC midnight and DST changes", () => {
  for (const [now, expected] of [
    ["2026-11-08T00:40:00Z", "2026-11-07T08:00:00.000Z"],
    ["2026-11-01T20:00:00Z", "2026-11-01T07:00:00.000Z"],
    ["2026-11-02T20:00:00Z", "2026-11-02T08:00:00.000Z"],
    ["2026-03-08T20:00:00Z", "2026-03-08T08:00:00.000Z"],
    ["2026-03-09T20:00:00Z", "2026-03-09T07:00:00.000Z"],
  ])
    assert.equal(dayStart("America/Los_Angeles", new Date(now)), expected);
  assert.equal(
    dayStart("Asia/Kathmandu", new Date("2026-11-08T15:00:00Z")),
    "2026-11-07T18:15:00.000Z",
  );
});

test("power meters in W and kW are comparable, while missing readings stay unknown", () => {
  assert.equal(
    powerWatts({ state: "3.25", attributes: { unit_of_measurement: "kW" } }),
    3250,
  );
  assert.equal(
    powerWatts({ state: "3250", attributes: { unit_of_measurement: "W" } }),
    3250,
  );
  for (const state of ["unavailable", "unknown", ""]) {
    assert.equal(
      powerWatts({ state, attributes: { unit_of_measurement: "W" } }),
      null,
    );
  }
  assert.equal(
    powerWatts({ state: "3250", attributes: { unit_of_measurement: "VA" } }),
    null,
  );
});

test("daily consumption uses recorded changes, including corrections, rather than lifetime totals", () => {
  assert.equal(
    energyChange([
      { state: 9283, change: 0 },
      { state: 9285, change: 2 },
      { state: 9284.75, change: -0.25 },
    ]),
    1.75,
  );
  assert.equal(energyChange([]), null);
  assert.equal(energyChange([{ change: null }]), null);
  assert.equal(energyChange([{ change: 0 }]), 0);
});

test("grouping preserves tracked usage and shows the untracked remainder separately", () => {
  const summary = branches(
    10,
    [
      { name: "Load A", value: 4 },
      { name: "Load B", value: 2 },
      { name: "Load C", value: 1 },
      { name: "Unavailable", value: null },
    ],
    1,
  );
  assert.equal(summary.tracked, 7);
  assert.deepEqual(
    summary.visible.map((d) => d.value),
    [4, 3, 3],
  );
  assert.equal(summary.visible.at(-1).name, "Untracked");
  assert.equal(
    summary.visible.reduce((sum, d) => sum + d.value, 0),
    10,
  );
});

test("independently sampled devices exceeding the house total are not silently rescaled", () => {
  const summary = branches(5, [{ name: "Load A", value: 6 }]);
  assert.equal(summary.tracked, 6);
  assert.equal(summary.visible[0].value, 6);
  assert.equal(summary.visible.length, 1);
});
