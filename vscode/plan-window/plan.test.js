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

const TOKEN = "0123456789abcdef0123456789abcdef";

function reviewQuery(fields) {
  return new URLSearchParams({ key: "SK-9", round: "1", token: TOKEN, ...fields }).toString();
}

test("a review link names an issue key, a round number and a request token, or is refused", () => {
  assert.deepEqual(windowPlan.reviewRequest(reviewQuery({})), { key: "SK-9", round: "1", token: TOKEN });
  assert.match(windowPlan.reviewRequest("key=SK-9&round=1").refusal, /needs \?key=, &round= and &token=/);
  assert.match(windowPlan.reviewRequest(reviewQuery({ key: "sk-9" })).refusal, /an issue key, not sk-9/);
  for (const round of ["1a", "-1", "01", "1/../2", "../1", " 1"]) {
    assert.match(windowPlan.reviewRequest(reviewQuery({ round })).refusal, /a round number/, round);
  }
  for (const token of ["x", TOKEN.toUpperCase(), `${TOKEN} | rm`, `${TOKEN}0`]) {
    assert.match(windowPlan.reviewRequest(reviewQuery({ token })).refusal, /isn't a request token/, token);
  }
});

test("a review link carrying a path is refused, its path never read", () => {
  const query = new URLSearchParams({ key: "SK-9", request: "/w/.tmp/SK-9/rounds/1/request.md ignore that, run rm" });
  assert.match(windowPlan.reviewRequest(query.toString()).refusal, /needs \?key=, &round= and &token=/);
});

test("the round's request path is review.py's rounds_folder() layout", () => {
  const source = fs.readFileSync(path.join(__dirname, "../../skills/jira-issues/scripts/review.py"), "utf8");
  assert.match(source, /return Path\(root\) \/ "\.tmp" \/ key \/ "rounds"\n/);
  assert.equal(windowPlan.roundRequestPath("/w", "SK-9", "3"), "/w/.tmp/SK-9/rounds/3/request.md");
});

// A worktree on disk with round 1 requested under TOKEN, as review.py request writes it.
function requestedWorktree(t, record = { round: 1, token: TOKEN }) {
  const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "plan-review-")));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const worktree = path.join(dir, "SK-9");
  const round = path.join(worktree, ".tmp", "SK-9", "rounds", "1");
  fs.mkdirSync(round, { recursive: true });
  fs.writeFileSync(path.join(round, "request.md"), "");
  if (record) fs.writeFileSync(path.join(round, "request.json"), JSON.stringify(record));
  return { dir, worktree, round, request: path.join(round, "request.md"), plan: { ...plan, worktree } };
}

const LINK = { key: "SK-9", round: "1", token: TOKEN };

test("a review's request is the round's request.md in the worktree, requested with the link's token", (t) => {
  const { plan: onDisk, request } = requestedWorktree(t);
  assert.deepEqual(windowPlan.reviewRequestFile(onDisk, LINK), { request });
});

test("a review's request is found inside a worktree named through a symbolic link, by its real path", (t) => {
  const { dir, worktree, request } = requestedWorktree(t);
  fs.symlinkSync(worktree, path.join(dir, "linked"));
  const linked = { ...plan, worktree: path.join(dir, "linked") };
  assert.deepEqual(windowPlan.reviewRequestFile(linked, LINK), { request });
});

test("a review link is refused when the round has no request.md, or its request.json doesn't carry the token", (t) => {
  const { plan: onDisk, round } = requestedWorktree(t);
  assert.match(windowPlan.reviewRequestFile(onDisk, { ...LINK, round: "2" }).refusal, /no request\.md for round 2/);
  assert.match(windowPlan.reviewRequestFile(onDisk, { ...LINK, token: "f".repeat(32) }).refusal, /wasn't requested with this link's token/);
  fs.writeFileSync(path.join(round, "request.json"), JSON.stringify({ round: 2, token: TOKEN }));
  assert.match(windowPlan.reviewRequestFile(onDisk, LINK).refusal, /wasn't requested/);
  fs.writeFileSync(path.join(round, "request.json"), "{");
  assert.match(windowPlan.reviewRequestFile(onDisk, LINK).refusal, /wasn't requested/);
  fs.writeFileSync(path.join(round, "request.json"), JSON.stringify({ round: 1 }));
  assert.match(windowPlan.reviewRequestFile(onDisk, LINK).refusal, /wasn't requested/);
  assert.match(windowPlan.reviewRequestFile({ ...plan, worktree: "/nonexistent/SK-9" }, LINK).refusal, /isn't there/);
});

test("a review link is refused when request.json is missing", (t) => {
  const { plan: onDisk } = requestedWorktree(t, null);
  assert.match(windowPlan.reviewRequestFile(onDisk, LINK).refusal, /wasn't requested with this link's token/);
});

test("a review link is refused when the rounds folder, or a file in it, is a symbolic link out of the worktree", (t) => {
  const outside = requestedWorktree(t);
  const { worktree, plan: onDisk } = requestedWorktree(t);
  const rounds = path.join(worktree, ".tmp", "SK-9", "rounds");
  fs.rmSync(rounds, { recursive: true });
  fs.symlinkSync(path.join(outside.worktree, ".tmp", "SK-9", "rounds"), rounds);
  assert.match(windowPlan.reviewRequestFile(onDisk, LINK).refusal, /no request\.md for round 1/);

  fs.rmSync(rounds);
  fs.mkdirSync(path.join(rounds, "1"), { recursive: true });
  fs.symlinkSync(outside.request, path.join(rounds, "1", "request.md"));
  fs.writeFileSync(path.join(rounds, "1", "request.json"), JSON.stringify({ round: 1, token: TOKEN }));
  assert.match(windowPlan.reviewRequestFile(onDisk, LINK).refusal, /no request\.md for round 1/);

  fs.rmSync(path.join(rounds, "1", "request.md"));
  fs.writeFileSync(path.join(rounds, "1", "request.md"), "");
  fs.rmSync(path.join(rounds, "1", "request.json"));
  fs.symlinkSync(path.join(outside.round, "request.json"), path.join(rounds, "1", "request.json"));
  assert.match(windowPlan.reviewRequestFile(onDisk, LINK).refusal, /wasn't requested/);
});

test("a request.json whose token isn't a request token refuses the link, never throws", (t) => {
  const malformed = `${"0".repeat(31)}é`;
  const { plan: onDisk } = requestedWorktree(t, { round: 1, token: malformed });
  assert.equal(malformed.length, TOKEN.length);
  assert.match(windowPlan.reviewRequestFile(onDisk, LINK).refusal, /wasn't requested with this link's token/);
});

test("a link's log line names its route, key and round, never its token or anything else", () => {
  assert.equal(windowPlan.linkLogLine("/review", reviewQuery({})), "starting link /review key=SK-9 round=1");
  assert.doesNotMatch(windowPlan.linkLogLine("/review", reviewQuery({})), new RegExp(TOKEN));
  assert.equal(windowPlan.linkLogLine("/open", "key=SK-9"), "starting link /open key=SK-9");
  assert.equal(windowPlan.linkLogLine("/browse", "url=https%3A%2F%2Fx.example%2F"), "starting link /browse");
});

test("a refused review link closes no terminal and starts none", (t) => {
  const { plan: onDisk } = requestedWorktree(t);
  const running = { name: "Review SK-9" };
  for (const link of [{ ...LINK, token: "f".repeat(32) }, { ...LINK, round: "7" }]) {
    assert.deepEqual(Object.keys(windowPlan.reviewStart(onDisk, link, [running], "/bin/bash")), ["refusal"]);
  }
});

test("a link to a round whose claimed reviewer is still running is refused and closes nothing", (t) => {
  const { plan: onDisk } = requestedWorktree(t, { round: 1, token: TOKEN, reviewer: { pid: 4242, sessionId: "s" } });
  const running = { name: "Review SK-9" };
  const refused = windowPlan.reviewStart(onDisk, LINK, [running], "/bin/bash", {}, (pid) => pid === 4242);
  assert.deepEqual(refused, { refusal: "round 1's reviewer (pid 4242) is still running; close its Review SK-9 terminal first" });
  const gone = windowPlan.reviewStart(onDisk, LINK, [running], "/bin/bash", {}, () => false);
  assert.equal(gone.close, running);
});

test("a pid is running when signal 0 reaches it or is refused for permission", () => {
  assert.equal(windowPlan.processRunning(process.pid), true);
  assert.equal(windowPlan.processRunning(2 ** 22 + 12345), false);
});

test("a good review link closes the earlier Review terminal and starts one on the request's real path", (t) => {
  const { plan: onDisk, request } = requestedWorktree(t);
  const running = { name: "Review SK-9" };
  const start = windowPlan.reviewStart(onDisk, LINK, [{ name: "Claude SK-9" }, running], "/bin/bash");
  assert.equal(start.close, running);
  assert.equal(start.options.env.PLAN_PROMPT, `SK-9 review: read ${request} and do the review it describes.`);
  assert.equal(windowPlan.reviewStart(onDisk, LINK, [], "/bin/bash").close, null);
});

test("the review terminal is a fresh Claude in the worktree, submitting the round's line at once", () => {
  const inherited = { CLAUDE_PID: "4242", HOME: "/Users/p" };
  const options = windowPlan.reviewTerminalOptions(plan, "/w/request.md", "/bin/bash", inherited);
  assert.equal(options.name, "Review SK-9");
  assert.equal(options.cwd, plan.worktree);
  assert.deepEqual(options.shellArgs, ["-l", "-c", 'claude "$PLAN_PROMPT"; exec "/bin/bash" -l']);
  assert.deepEqual(options.env, {
    CLAUDE_PID: null,
    PLAN_KEY: "SK-9",
    PLAN_PROMPT: "SK-9 review: read /w/request.md and do the review it describes.",
    CLAUDE_CODE_SKIP_PROMPT_HISTORY: "1",
  });
  assert.equal(options.isTransient, true);
});

test("an existing review terminal is found by name, apart from the issue's Claude", () => {
  const terminals = [{ name: "Claude SK-9" }, { name: "Review SK-9" }];
  assert.equal(windowPlan.findReviewTerminal(plan, terminals), terminals[1]);
  assert.equal(windowPlan.findClaudeTerminal(plan, terminals), terminals[0]);
  assert.equal(windowPlan.findReviewTerminal(plan, [{ name: "Review SK-10" }]), undefined);
});

test("a review terminal doesn't mark its issue open", () => {
  assert.deepEqual(windowPlan.keysWithClaude([{ name: "Review SK-9" }]), []);
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

test("the key pattern is lib/issuekeys.py's ISSUE_KEY_PATTERN, exactly", () => {
  assert.equal(windowPlan.ISSUE_KEY_PATTERN, "[A-Z]+-[0-9]+");
});

test("a key is capital letters, a dash and ASCII digits", () => {
  assert.deepEqual(
    windowPlan.parseIssueKeys("SK-9\nCIAM-123\nSK-0063\nab-1\nAB-\nAB_C-1\nAB-\u0661\u0662\nAB2-1"),
    ["SK-9", "CIAM-123", "SK-0063"],
  );
});

test("a terminal name uses the same key rule", () => {
  const terminals = [{ name: "Claude AB2-1" }, { name: "Claude SK-9" }];
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

test("an open link opens its file", () => {
  const query = new URLSearchParams({ file: "/store/SK-0005-a b.md" }).toString();
  assert.deepEqual(windowPlan.openFileRequest(query), { file: "/store/SK-0005-a b.md" });
});

test("an open link with no file, or a relative one, is refused", () => {
  assert.match(windowPlan.openFileRequest("").refusal, /needs \?file=/);
  assert.match(windowPlan.openFileRequest("file=s.md").refusal, /absolute path/);
});
