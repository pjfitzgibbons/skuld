const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const windowPlan = require("./plan");

function settingsOf(values) {
  return (name) => values[name];
}

const plan = {
  key: "SK-9",
  worktree: "/work/repo.worktrees/SK-9",
  ticket: "/work/issues/SK-0009-example.md",
  ticketUrl: null,
  prompt: "Work on SK-9.",
};

test("settings with no plan.key are no issue", () => {
  assert.equal(windowPlan.readPlan(settingsOf({})), null);
});

test("an issue's settings are read, with no ticket for a Jira issue", () => {
  const read = windowPlan.readPlan(settingsOf({ key: "CIAM-1", worktree: "/w", prompt: "p" }));
  assert.deepEqual(read, { key: "CIAM-1", worktree: "/w", ticket: null, ticketUrl: null, prompt: "p" });
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

test("the Claude and shell terminals unset every inherited CLAUDE* session variable, and keep the rest", () => {
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
  assert.deepEqual(windowPlan.browseRequest(query), { url: "https://example.com/a b?q='x'" });
});

// The query term.browseLink() sends for https://example.com/?a=1&key=SK-10&b=2 in SK-9's window,
// after VS Code's decode; test_term.py checks the sender's side.
test("a page's own query, and a key inside it, stay in the url", () => {
  const query = "url=https%3A%2F%2Fexample.com%2F%3Fa%3D1%26key%3DSK-10%26b%3D2&key=SK-9";
  assert.deepEqual(windowPlan.browseRequest(query), { url: "https://example.com/?a=1&key=SK-10&b=2" });
});

test("a browse link with no key opens too", () => {
  assert.deepEqual(windowPlan.browseRequest("url=http%3A%2F%2F127.0.0.1%3A8737%2F"), { url: "http://127.0.0.1:8737/" });
});

test("a browse link for any issue opens in the one plan window", () => {
  assert.deepEqual(windowPlan.browseRequest("url=https%3A%2F%2Fexample.com&key=SK-10"), { url: "https://example.com" });
});

test("a browse link with no url, or one that isn't http(s), is refused", () => {
  assert.match(windowPlan.browseRequest("key=SK-9").refusal, /needs \?url=/);
  assert.match(windowPlan.browseRequest("url=file%3A%2F%2F%2Fetc%2Fpasswd").refusal, /only http\(s\)/);
  assert.match(windowPlan.browseRequest("url=javascript%3Aalert(1)").refusal, /only http\(s\)/);
});

test("the issues file lists one key per line, in its order", () => {
  assert.deepEqual(windowPlan.parseIssueKeys("SK-0045\nCIAM-12\nAB-1\n"), ["SK-0045", "CIAM-12", "AB-1"]);
  assert.deepEqual(windowPlan.parseIssueKeys("SK-0045\r\n  CIAM-12  \n"), ["SK-0045", "CIAM-12"]);
});

test("blank and invalid lines in the issues file are skipped, and a repeated key shows once", () => {
  assert.deepEqual(windowPlan.parseIssueKeys("\n# note\nsk-1\n2SK-1\nSK-\n3=SK-1\nSK-7\nSK-2\nSK-7\n"), ["SK-7", "SK-2"]);
  assert.deepEqual(windowPlan.parseIssueKeys(""), []);
});

test("the pulses file gives each listed key its colour; a half-written or foreign line is skipped", () => {
  const text = "SK-1=orange\r\nSK-2=green\nSK-3=off\nSK-4=blue\nsk-5=green\nSK-6=green=x\nSK-7\nSK-\n";
  assert.deepEqual(windowPlan.parsePulses(text), { "SK-1": "orange", "SK-2": "green" });
  assert.deepEqual(windowPlan.parsePulses(""), {});
});

test("each item is labelled with its key, coloured from the pulses, and marked when its Claude is open", () => {
  const items = windowPlan.issueItems(["SK-1", "SK-2", "SK-3"], { "SK-1": "orange", "SK-2": "green", "SK-9": "green" }, ["SK-3"]);
  assert.deepEqual(items, [
    { key: "SK-1", pulse: "orange", iconFile: "media/waiting.svg", isOpen: false },
    { key: "SK-2", pulse: "green", icon: "pass-filled", themeColor: "charts.green", isOpen: false },
    { key: "SK-3", pulse: "off", icon: "circle-outline", themeColor: null, isOpen: true },
  ]);
});

test("the badge counts only the coloured issues", () => {
  const items = windowPlan.issueItems(["SK-1", "SK-2", "SK-3"], { "SK-1": "orange", "SK-3": "green" }, []);
  assert.equal(windowPlan.pulsedCount(items), 2);
  assert.equal(windowPlan.pulsedCount(windowPlan.issueItems(["SK-1"], {}, [])), 0);
});

test("clearing an issue's colour runs `<command> pulse <KEY> off`", () => {
  assert.deepEqual(windowPlan.clearPulseCommand("plan", "SK-1"), ["plan", ["pulse", "SK-1", "off"]]);
  assert.deepEqual(windowPlan.clearPulseCommand("/opt/bin/plan", "SK-63"), ["/opt/bin/plan", ["pulse", "SK-63", "off"]]);
});

test("an issue's workspace file is <KEY>.code-workspace in the workspace dir, ~ expanded", () => {
  assert.equal(windowPlan.workspaceFilePath("SK-63", "/w/"), "/w/SK-63.code-workspace");
  assert.equal(windowPlan.expandHome("~/.cache/plan/code-workspaces", "/Users/p"), "/Users/p/.cache/plan/code-workspaces");
  assert.equal(windowPlan.expandHome("/abs/~x", "/Users/p"), "/abs/~x");
});

function tempDir(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "plan-window-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test("a missing file reads as empty, so a missing issues file shows no items", (t) => {
  const missing = path.join(tempDir(t), "issues");
  assert.equal(windowPlan.readTextOrEmpty(missing), "");
  assert.deepEqual(windowPlan.parseIssueKeys(windowPlan.readTextOrEmpty(missing)), []);
});

// lib/term.py issueWorkspace's output for a store issue.
const workspaceText = JSON.stringify({
  folders: [{ path: plan.worktree }, { path: "/work/issues" }],
  settings: { "plan.key": "SK-9", "plan.worktree": plan.worktree, "plan.prompt": plan.prompt, "plan.ticket": plan.ticket },
});

test("an issue's record is read from its workspace file's plan.* settings", () => {
  assert.deepEqual(windowPlan.issueRecord(workspaceText, "SK-9"), plan);
});

test("a workspace file that's missing, not JSON, keyless or for another issue is refused, naming plan open", () => {
  assert.throws(() => windowPlan.issueRecord("", "SK-9"), /SK-9's workspace file is missing or isn't JSON; run plan open SK-9/);
  assert.throws(() => windowPlan.issueRecord('{"folders": [', "SK-9"), /isn't JSON/);
  assert.throws(() => windowPlan.issueRecord('{"settings": {}}', "SK-9"), /has no plan\.key/);
  assert.throws(() => windowPlan.issueRecord(workspaceText, "SK-10"), /SK-10's workspace file is for SK-9/);
});

test("an open link names an issue key, or is refused", () => {
  assert.deepEqual(windowPlan.openRequest("key=SK-9"), { key: "SK-9" });
  assert.match(windowPlan.openRequest("").refusal, /needs \?key=/);
  assert.match(windowPlan.openRequest("key=sk-9").refusal, /an issue key, not sk-9/);
});

test("Claude continues the worktree's last conversation when it has one, and starts on the prompt otherwise", () => {
  assert.deepEqual(windowPlan.claudeTerminalOptions(plan, "/bin/bash", {}, true).shellArgs, [
    "-l",
    "-c",
    'claude --continue; exec "/bin/bash" -l',
  ]);
  assert.match(windowPlan.claudeTerminalOptions(plan, "/bin/bash", {}, false).shellArgs[2], /^claude "\$PLAN_PROMPT";/);
});

test("a worktree's conversations are under ~/.claude/projects, every non-alphanumeric character a dash", () => {
  assert.equal(
    windowPlan.claudeProjectDir("/Users/p/skuld.worktrees/SK-9", {}, "/Users/p"),
    "/Users/p/.claude/projects/-Users-p-skuld-worktrees-SK-9",
  );
  assert.equal(windowPlan.claudeProjectDir("/w/a_b", { CLAUDE_CONFIG_DIR: "/c" }, "/Users/p"), "/c/projects/-w-a-b");
  assert.equal(windowPlan.claudeProjectDir(`/${"a".repeat(200)}`, {}, "/Users/p"), null);
});

test("a conversation is a .jsonl file in the project directory", () => {
  assert.equal(windowPlan.hasConversation(["0754d21d.jsonl", "memory"]), true);
  assert.equal(windowPlan.hasConversation(["memory"]), false);
});

test("a worktree whose project directory holds a transcript has a conversation; a missing one hasn't", (t) => {
  const home = tempDir(t);
  const worktree = "/w/repo.worktrees/SK-9";
  assert.equal(windowPlan.worktreeHasConversation(worktree, {}, home), false);
  const dir = windowPlan.claudeProjectDir(worktree, {}, home);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "abc.jsonl"), "{}\n");
  assert.equal(windowPlan.worktreeHasConversation(worktree, {}, home), true);
});

test("an issue's Claude terminal marks it open; its shell alone doesn't", () => {
  const terminals = [{ name: "Claude SK-1" }, { name: "Shell SK-2" }, { name: "bash" }, { name: "Claude sk-3" }];
  assert.deepEqual(windowPlan.keysWithClaude(terminals), ["SK-1"]);
});

const jiraWorkspaceText = JSON.stringify({
  folders: [{ path: "/w/repo.worktrees/CIAM-7" }, { path: "/work/issues" }],
  settings: {
    "plan.key": "CIAM-7",
    "plan.worktree": "/w/repo.worktrees/CIAM-7",
    "plan.prompt": "p",
    "plan.ticketUrl": "https://example.atlassian.net/browse/CIAM-7",
  },
});

test("a Jira issue's record has no ticket file and its page as ticketUrl, which opening it shows", () => {
  const record = windowPlan.issueRecord(jiraWorkspaceText, "CIAM-7");
  assert.equal(record.ticket, null);
  assert.equal(record.ticketUrl, "https://example.atlassian.net/browse/CIAM-7");
  assert.equal(windowPlan.ticketPage(record), "https://example.atlassian.net/browse/CIAM-7");
});

test("a Jira page opens reusing a browser tab already on it", () => {
  const record = windowPlan.issueRecord(jiraWorkspaceText, "CIAM-7");
  assert.deepEqual(windowPlan.ticketPageOptions(record), {
    url: "https://example.atlassian.net/browse/CIAM-7",
    reuseUrlFilter: "https://example.atlassian.net/browse/CIAM-7",
  });
  assert.equal(windowPlan.ticketPageOptions(plan), null);
});

test("a store issue's ticket file wins over a page, and an issue with neither shows none", () => {
  assert.equal(windowPlan.ticketPage({ ...plan, ticketUrl: "https://example.com/SK-9" }), null);
  assert.equal(windowPlan.ticketPage({ ...plan, ticket: null }), null);
});

test("a plan.ticketUrl that isn't an http(s) link is refused", () => {
  const settings = { key: "CIAM-7", worktree: "/w", ticketUrl: "javascript:alert(1)" };
  assert.throws(() => windowPlan.readPlan(settingsOf(settings)), /plan\.ticketUrl is javascript:alert\(1\), not an http\(s\) link/);
});

test("the Claude terminal keeps CLAUDE_CONFIG_DIR, so the conversation check and Claude read one config dir", (t) => {
  const inherited = { CLAUDE_CONFIG_DIR: tempDir(t), CLAUDE_PID: "4242" };
  const env = windowPlan.claudeTerminalOptions(plan, "/bin/bash", inherited).env;
  assert.equal("CLAUDE_CONFIG_DIR" in env, false);
  assert.equal(env.CLAUDE_PID, null);
  const terminalEnv = { ...inherited, ...env };
  assert.equal(
    windowPlan.claudeProjectDir(plan.worktree, terminalEnv, "/Users/p"),
    windowPlan.claudeProjectDir(plan.worktree, inherited, "/Users/p"),
  );
});

test("a key needs two or more leading letters or digits, as lib/jira.py KEY_RE", () => {
  assert.deepEqual(windowPlan.parseIssueKeys("A-1\nSK-9\nCIAM-12\nS2-3"), ["SK-9", "CIAM-12", "S2-3"]);
});

test("a terminal name uses the same key rule", () => {
  const terminals = [{ name: "Claude A-1" }, { name: "Claude SK-9" }];
  assert.deepEqual(windowPlan.keysWithClaude(terminals), ["SK-9"]);
});

test("only a key with a pulse line is cleared on click", () => {
  const pulses = windowPlan.parsePulses("SK-1=orange\n");
  assert.equal(windowPlan.hasPulse(pulses, "SK-1"), true);
  assert.equal(windowPlan.hasPulse(pulses, "SK-2"), false);
  assert.equal(windowPlan.hasPulse(pulses, "constructor"), false);
});

test("the plan window is the one whose workspace file is plan.code-workspace in workspaceDir", () => {
  const dir = "~/.cache/plan/code-workspaces";
  assert.equal(windowPlan.isPlanWindow("/h/.cache/plan/code-workspaces/plan.code-workspace", dir, "/h"), true);
  assert.equal(windowPlan.isPlanWindow("/h/.cache/plan/code-workspaces/../code-workspaces/plan.code-workspace", dir, "/h"), true);
  assert.equal(windowPlan.isPlanWindow("/h/.cache/plan/code-workspaces/SK-9.code-workspace", dir, "/h"), false);
  assert.equal(windowPlan.isPlanWindow("/elsewhere/plan.code-workspace", dir, "/h"), false);
  assert.equal(windowPlan.isPlanWindow(undefined, dir, "/h"), false);
});
