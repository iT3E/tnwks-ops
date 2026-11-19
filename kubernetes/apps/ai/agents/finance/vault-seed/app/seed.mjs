// The seed of a vault share (docs/apps/agent-platform.md, "The seed of the
// finance share"). Two steps of the Job vault-seed (job.yaml), after the
// secret scan of the source (the init container scan):
//
//   node seed.mjs report  Reads the report of the scan, gives each finding an
//                         id, prints one line for each finding, and keeps the
//                         list for the next step. It never prints a value.
//   node seed.mjs copy    Copies the source to the share, if the owner
//                         decided each finding, and prints one result line.
//
// The id of a finding is the first 16 hex characters of the SHA-256 of the
// rule, the line, the column and the whole content of the file. So it is
// the same in each run while the file does not change, and it says nothing
// of the file: to guess it, one needs the whole file. The owner's decisions
// come from git as two lists of ids: SKIP (the file stays out of the share)
// and TAKE (the file goes as it is).
//
// The copy: plain files and directories only, with no link and no other
// kind of file; never the config folder of the notes app (.obsidian), the
// trash (.trash) or a .git folder, at any depth; never over a file that is
// there (the share must be empty); files 0600 and folders 0700, as the
// owner uid 1000 that the export squashes each client to. The copy goes to
// a folder .seed-incoming-<time> of the share first, is counted there, and
// then each top entry is renamed into place. A failure before the renames
// removes that folder, so the share is as it was. The source is mounted
// read only and stays as it is.
//
// Each way out of the copy prints one line and nothing else:
// `vault-seed result=ok ...` with counts only, or
// `vault-seed result=failed reason=<kind>`. No name of a note is in it.
// A missing source folder fails with reason=source-missing (the scan only
// warns and finds nothing there), a source with no file with
// reason=source-empty, and a source whose every file stays out with
// reason=nothing-to-copy.

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const SOURCE = process.env.SOURCE ?? "/state/vault";
const SHARE = process.env.SHARE ?? "/share";
const WORK = process.env.WORK ?? "/work";
const REPORT = path.join(WORK, "report.json");
const FINDINGS = path.join(WORK, "findings.json");
const STAGING = ".seed-incoming-";
// The folders that never go to a share, at any depth, and the name of each
// in the result line.
const LEFT_OUT = new Map([
  [".obsidian", "config"],
  [".trash", "trash"],
  [".git", "git"],
]);
const ID = /^[0-9a-f]{16}$/;

function fail(step, reason) {
  console.log(`vault-seed ${step === "copy" ? "result=failed" : "report=failed"} reason=${reason}`);
  process.exit(1);
}

// The lists of the owner. The copy step stops on a wrong list; the report
// step only marks the findings, so the owner still sees each one.
function decisions(strict) {
  const read = (name) => (process.env[name] ?? "").split(/[\s,]+/).filter(Boolean);
  const skip = read("SKIP");
  const take = read("TAKE");
  const both = skip.filter((id) => take.includes(id));
  if (strict) {
    if (![...skip, ...take].every((id) => ID.test(id))) fail("copy", "decision-form");
    if (both.length) fail("copy", `decision-twice count=${both.length}`);
  }
  return {
    skip: new Set(skip.filter((id) => !both.includes(id))),
    take: new Set(take.filter((id) => !both.includes(id))),
  };
}

// The reason a relative path stays out (config, trash, git), or null.
function leftOut(rel) {
  for (const part of rel.split("/")) {
    if (LEFT_OUT.has(part)) return LEFT_OUT.get(part);
  }
  return null;
}

function report() {
  const { skip, take } = decisions(false);
  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(REPORT, "utf8"));
  } catch {
    fail("report", "no-report");
  }
  if (!Array.isArray(raw)) fail("report", "report-form");
  const findings = [];
  for (const f of raw) {
    const file = String(f.File ?? "");
    if (!file.startsWith(`${SOURCE}/`)) fail("report", "report-path");
    // A finding inside an archive names the member after a "!": the file
    // that moves is the archive.
    const rel = file.slice(SOURCE.length + 1).split("!")[0];
    const full = path.join(SOURCE, rel);
    let content;
    try {
      if (!fs.lstatSync(full).isFile()) fail("report", "finding-not-a-file");
      content = fs.readFileSync(full);
    } catch {
      fail("report", "finding-unreadable");
    }
    const id = crypto
      .createHash("sha256")
      .update(`vault-seed-v1\0${f.RuleID}\0${f.StartLine}\0${f.StartColumn}\0`)
      .update(content)
      .digest("hex")
      .slice(0, 16);
    const out = leftOut(rel);
    const decision = out ? `left-out-${out}` : skip.has(id) ? "skip" : take.has(id) ? "take" : "none";
    findings.push({ id, rel, decision });
    // The relative path is in JSON form, so a line break in a name can not
    // make a second line. The value of the finding is not in the report
    // (the scan runs with --redact) and not here.
    console.log(
      `vault-seed finding id=${id} rule=${String(f.RuleID).replace(/[^A-Za-z0-9_.-]/g, "?")} ` +
        `line=${Number(f.StartLine)} decision=${decision} file=${JSON.stringify(rel)}`,
    );
  }
  fs.writeFileSync(FINDINGS, JSON.stringify(findings));
  const open = findings.filter((f) => f.decision === "none").length;
  console.log(`vault-seed report=ok findings=${findings.length} undecided=${open}`);
}

// Counts the plain files under a folder, with no link followed.
function countFiles(dir, counts) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    const st = fs.lstatSync(full);
    if (st.isDirectory()) countFiles(full, counts);
    else if (st.isFile()) {
      counts.files += 1;
      if (e.name.endsWith(".md")) counts.notes += 1;
      counts.bytes += st.size;
    }
  }
}

function copy() {
  const { skip, take } = decisions(true);
  let findings;
  try {
    findings = JSON.parse(fs.readFileSync(FINDINGS, "utf8"));
  } catch {
    fail("copy", "no-findings-list");
  }
  const known = new Set(findings.filter((f) => !f.decision.startsWith("left-out")).map((f) => f.id));
  const stale = [...skip, ...take].filter((id) => !known.has(id));
  if (stale.length) fail("copy", `stale-decision count=${stale.length}`);
  const open = findings.filter((f) => f.decision === "none").length;
  if (open) fail("copy", `findings undecided=${open}`);
  const skipFiles = new Set(findings.filter((f) => f.decision === "skip").map((f) => f.rel));
  const taken = findings.filter((f) => f.decision === "take").length;

  try {
    if (!fs.lstatSync(SOURCE).isDirectory()) fail("copy", "source-missing");
  } catch {
    fail("copy", "source-missing");
  }
  // A folder of a run that stopped half way is removed; anything else in
  // the share stops the run.
  for (const name of fs.readdirSync(SHARE)) {
    if (name.startsWith(STAGING)) fs.rmSync(path.join(SHARE, name), { recursive: true, force: true });
  }
  if (fs.readdirSync(SHARE).length) fail("copy", "share-not-empty");

  const c = {
    sourceFiles: 0, sourceNotes: 0, files: 0, notes: 0, bytes: 0,
    config: 0, trash: 0, git: 0, findings: 0, links: 0, other: 0,
  };
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
  const stage = path.join(SHARE, `${STAGING}${stamp}`);
  let step = "copy";
  try {
    fs.mkdirSync(stage, { mode: 0o700 });
    const walk = (rel) => {
      const entries = fs.readdirSync(path.join(SOURCE, rel), { withFileTypes: true })
        .map((e) => e.name).sort();
      for (const name of entries) {
        const r = rel ? `${rel}/${name}` : name;
        const src = path.join(SOURCE, r);
        const st = fs.lstatSync(src);
        if (st.isSymbolicLink()) {
          c.links += 1;
        } else if (st.isDirectory()) {
          const out = LEFT_OUT.get(name);
          if (out) {
            const n = { files: 0, notes: 0, bytes: 0 };
            countFiles(src, n);
            c[out] += n.files;
            c.sourceFiles += n.files;
            c.sourceNotes += n.notes;
          } else {
            fs.mkdirSync(path.join(stage, r), { mode: 0o700 });
            walk(r);
          }
        } else if (st.isFile()) {
          c.sourceFiles += 1;
          if (name.endsWith(".md")) c.sourceNotes += 1;
          if (skipFiles.has(r)) {
            c.findings += 1;
            continue;
          }
          const dst = path.join(stage, r);
          fs.copyFileSync(src, dst, fs.constants.COPYFILE_EXCL);
          fs.chmodSync(dst, 0o600);
          fs.utimesSync(dst, st.atime, st.mtime);
          c.files += 1;
          c.bytes += st.size;
          if (name.endsWith(".md")) c.notes += 1;
        } else {
          c.other += 1;
        }
      }
    };
    walk("");
    // A source with no file is a fault (a wrong path, or a volume that is
    // not the one of the agent), never a seed of nothing.
    if (c.sourceFiles === 0 || c.files === 0) {
      step = c.sourceFiles === 0 ? "source-empty" : "nothing-to-copy";
      throw new Error();
    }
    step = "verify";
    const check = { files: 0, notes: 0, bytes: 0 };
    countFiles(stage, check);
    if (check.files !== c.files || check.notes !== c.notes || check.bytes !== c.bytes) throw new Error();
    if (c.sourceFiles !== c.files + c.config + c.trash + c.git + c.findings) throw new Error();
    step = "rename";
    for (const name of fs.readdirSync(stage)) {
      const target = path.join(SHARE, name);
      if (fs.existsSync(target)) throw new Error();
      fs.renameSync(path.join(stage, name), target);
    }
    fs.rmdirSync(stage);
  } catch {
    // Before the renames the share is as it was; in them, the entries moved
    // so far stay, and a second run stops at share-not-empty.
    if (step !== "rename") fs.rmSync(stage, { recursive: true, force: true });
    fail("copy", step);
  }
  console.log(
    `vault-seed result=ok source-files=${c.sourceFiles} source-notes=${c.sourceNotes} ` +
      `files=${c.files} notes=${c.notes} bytes=${c.bytes} ` +
      `left-out-config=${c.config} left-out-trash=${c.trash} left-out-git=${c.git} ` +
      `left-out-findings=${c.findings} taken-findings=${taken} links=${c.links} other=${c.other}`,
  );
}

const mode = process.argv[2];
if (mode === "report") report();
else if (mode === "copy") copy();
else fail("copy", "mode");
