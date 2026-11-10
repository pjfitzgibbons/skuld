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

test("the Claude and shell terminals unset every inherited CLAUDE* variable, and keep the rest", () => {
  const inherited = { CLAUDE_CODE_CHILD_SESSION: "1", CLAUDE_PID: "4242", CLAUDECODE: "1", HOME: "/Users/p" };
  const unset = { CLAUDE_CODE_CHILD_SESSION: null, CLAUDE_PID: null, CLAUDECODE: null };
  assert.deepEqual(windowPlan.claudeTerminalOptions(plan, "/bin/bash", inherited).env, {
    ...unset,
    PLAN_KEY: "SK-9",
    PLAN_PROMPT: "Work on SK-9.",
  });
  assert.deepEqual(windowPlan.shellTerminalOptions(plan, "/bin/bash", inherited).env, { ...unset, PLAN_KEY: "SK-9" });
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

test("a browse link for this window opens its url", () => {
  const query = new URLSearchParams({ url: "https://example.com/a b?q='x'", key: "SK-9" }).toString();
  assert.deepEqual(windowPlan.browseRequest(query, "SK-9"), { url: "https://example.com/a b?q='x'" });
});

// The query term.browseLink() sends for https://example.com/?a=1&key=SK-10&b=2 in SK-9's window,
// after VS Code's decode; test_term.py checks the sender's side.
test("a page's own query, and a key inside it, stay in the url", () => {
  const query = "url=https%3A%2F%2Fexample.com%2F%3Fa%3D1%26key%3DSK-10%26b%3D2&key=SK-9";
  assert.deepEqual(windowPlan.browseRequest(query, "SK-9"), { url: "https://example.com/?a=1&key=SK-10&b=2" });
});

test("a browse link with no key opens in whichever window has it", () => {
  assert.deepEqual(windowPlan.browseRequest("url=http%3A%2F%2F127.0.0.1%3A8737%2F", null), { url: "http://127.0.0.1:8737/" });
});

test("a browse link for another issue's window is refused, naming both", () => {
  const query = "url=https%3A%2F%2Fexample.com&key=SK-10";
  assert.match(windowPlan.browseRequest(query, "SK-9").refusal, /SK-10's window, and this is SK-9/);
  assert.match(windowPlan.browseRequest(query, null).refusal, /SK-10's window, and this is no issue's/);
});

test("a browse link with no url, or one that isn't http(s), is refused", () => {
  assert.match(windowPlan.browseRequest("key=SK-9", "SK-9").refusal, /needs \?url=/);
  assert.match(windowPlan.browseRequest("url=file%3A%2F%2F%2Fetc%2Fpasswd", "SK-9").refusal, /only http\(s\)/);
  assert.match(windowPlan.browseRequest("url=javascript%3Aalert(1)", "SK-9").refusal, /only http\(s\)/);
});
