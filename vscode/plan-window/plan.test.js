const { test } = require("node:test");
const assert = require("node:assert/strict");
const windowPlan = require("./plan");

function settingsOf(values) {
  return (name) => values[name];
}

const plan = {
  key: "SK-9",
  worktree: "/work/repo.worktrees/SK-9",
  ticket: "/work/issues/SK-0009-example.md",
  prompt: "Work on SK-9.",
};

test("a window with no plan.key is not an issue window", () => {
  assert.equal(windowPlan.readPlan(settingsOf({})), null);
});

test("an issue window's settings are read, with no ticket for a Jira issue", () => {
  const read = windowPlan.readPlan(settingsOf({ key: "CIAM-1", worktree: "/w", prompt: "p" }));
  assert.deepEqual(read, { key: "CIAM-1", worktree: "/w", ticket: null, prompt: "p" });
});

test("plan.key without plan.worktree is refused, naming both", () => {
  assert.throws(() => windowPlan.readPlan(settingsOf({ key: "SK-9" })), /plan\.key is SK-9 but plan\.worktree is unset/);
});

test("the Claude terminal starts in the worktree and passes the prompt by environment", () => {
  const options = windowPlan.claudeTerminalOptions(plan, "/bin/bash");
  assert.equal(options.name, "Claude SK-9");
  assert.equal(options.cwd, plan.worktree);
  assert.deepEqual(options.env, { PLAN_KEY: "SK-9", PLAN_PROMPT: "Work on SK-9." });
  assert.deepEqual(options.shellArgs, ["-l", "-c", 'claude "$PLAN_PROMPT"; exec "/bin/bash" -l']);
  assert.equal(options.isTransient, true);
});

test("an existing Claude terminal is found by name, so a second is never made", () => {
  const terminals = [{ name: "bash" }, { name: "Claude SK-9" }];
  assert.equal(windowPlan.findClaudeTerminal(plan, terminals), terminals[1]);
  assert.equal(windowPlan.findClaudeTerminal(plan, [{ name: "Claude SK-10" }]), undefined);
});

test("the ticket opens only when no tab has it, and never for a Jira issue", () => {
  assert.equal(windowPlan.ticketNeedsOpening(plan, []), true);
  assert.equal(windowPlan.ticketNeedsOpening(plan, [plan.ticket]), false);
  assert.equal(windowPlan.ticketNeedsOpening({ ...plan, ticket: null }, []), false);
});

test("the shell terminal is a login shell in the worktree, carrying the key", () => {
  const options = windowPlan.shellTerminalOptions(plan, "/bin/bash");
  assert.equal(options.name, "Shell SK-9");
  assert.equal(options.cwd, plan.worktree);
  assert.deepEqual(options.shellArgs, ["-l"]);
  assert.deepEqual(options.env, { PLAN_KEY: "SK-9" });
});

test("an existing shell terminal is found by name, apart from Claude's", () => {
  const terminals = [{ name: "Claude SK-9" }, { name: "Shell SK-9" }];
  assert.equal(windowPlan.findShellTerminal(plan, terminals), terminals[1]);
  assert.equal(windowPlan.findShellTerminal(plan, [{ name: "Claude SK-9" }]), undefined);
});
