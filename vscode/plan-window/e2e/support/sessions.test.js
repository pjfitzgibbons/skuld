import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { sessionsFor, specArguments } from "./sessions.js";

const e2eDir = path.join(path.sep, "work", "e2e");
const planSpec = path.join(e2eDir, "specs", "open-issue.e2e.js");
const otherSpec = path.join(e2eDir, "specs", "other-window", "other-window.e2e.js");

// A table of what each pattern expands to, as wdio's own expansion would return it.
function expanding(table) {
  return (pattern) => table[pattern] ?? [];
}

test("--spec and --spec= values are read from the arguments", () => {
  assert.deepEqual(specArguments(["run", "wdio.conf.js", "--spec", "a.js", "--spec=b.js"]), ["a.js", "b.js"]);
});

test("no --spec calls for both sessions", () => {
  assert.equal(sessionsFor([], expanding({}), e2eDir), "both");
});

test("a pattern is classified by the files it matches, not by its text", () => {
  const expand = expanding({ "**/other-window/*.e2e.js": [otherSpec], "specs/*.e2e.js": [planSpec] });
  assert.equal(sessionsFor(["**/other-window/*.e2e.js"], expand, e2eDir), "other");
  assert.equal(sessionsFor(["specs/*.e2e.js"], expand, e2eDir), "plan");
});

test("a pattern whose files span both folders is refused", () => {
  const expand = expanding({ "specs/**/*.e2e.js": [planSpec, otherSpec] });
  assert.throws(() => sessionsFor(["specs/**/*.e2e.js"], expand, e2eDir), /run one session at a time/);
});

test("patterns that each match one session but together span both are refused", () => {
  const expand = expanding({ a: [planSpec], b: [otherSpec] });
  assert.throws(() => sessionsFor(["a", "b"], expand, e2eDir), /run one session at a time/);
});

test("a pattern that matches no file is refused, even beside one that does", () => {
  const expand = expanding({ a: [planSpec] });
  assert.throws(() => sessionsFor(["a", "specs/nope.e2e.js"], expand, e2eDir), /--spec specs\/nope\.e2e\.js matches no spec file/);
});
