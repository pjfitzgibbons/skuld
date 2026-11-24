// What the plan window needs, worked out with no VS Code calls, so the tests can check it.
// extension.js carries it out.
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

// The one issue-key rule. lib/issuekeys.py's ISSUE_KEY_PATTERN is the canonical copy; this string must
// stay equal to it (plan.test.js asserts the value). Every key check here is built from it.
const ISSUE_KEY_PATTERN = "[A-Z]+-[0-9]+";
const ISSUE_KEY = new RegExp(`^${ISSUE_KEY_PATTERN}$`);

// An issue: its plan.* settings, read from the issue's own workspace file (issueRecord), which
// Start writes and nothing opens. A store issue has a ticket file; a Jira issue has none, and
// ticketUrl, its Jira page, instead. prUrl is the issue's PR, which plan done writes into the file
// just before it asks the window to close the issue.
function readPlan(setting) {
  const key = setting("key");
  if (!key) return null;
  const worktree = setting("worktree");
  if (!worktree) throw new Error(`plan.key is ${key} but plan.worktree is unset`);
  const ticketUrl = setting("ticketUrl") || null;
  if (ticketUrl && !isWebUrl(ticketUrl)) throw new Error(`plan.ticketUrl is ${ticketUrl}, not an http(s) link`);
  const prUrl = setting("prUrl") || null;
  if (prUrl && !isWebUrl(prUrl)) throw new Error(`plan.prUrl is ${prUrl}, not an http(s) link`);
  return { key, worktree, ticket: setting("ticket") || null, ticketUrl, prUrl, prompt: setting("prompt") || "" };
}

// The issue's plan.* settings from the text of its workspace file (lib/term.py issueWorkspace).
// A file that isn't JSON, or is for another key, is refused rather than half-read.
function issueRecord(workspaceText, key) {
  const settings = parseWorkspace(workspaceText, key).settings || {};
  const plan = readPlan((name) => settings[`plan.${name}`]);
  if (!plan) throw new Error(`${key}'s workspace file has no plan.key; run plan open ${key}`);
  if (plan.key !== key) throw new Error(`${key}'s workspace file is for ${plan.key}; run plan open ${key}`);
  return plan;
}

function parseWorkspace(workspaceText, key) {
  try {
    return JSON.parse(workspaceText) || {};
  } catch {
    throw new Error(`${key}'s workspace file is missing or isn't JSON; run plan open ${key}`);
  }
}

function claudeTerminalName(plan) {
  return `Claude ${plan.key}`;
}

// The window's own CLAUDE* variables, each set to null, which VS Code takes as "unset in the
// terminal". A window opened by `code -n` from inside a Claude session inherits that session's
// CLAUDE_CODE_CHILD_SESSION and CLAUDE_PID; a Claude started with them counts itself a child and
// never registers, so `claude agents --json` and `plan agents` wouldn't list it.
// CLAUDE_CONFIG_DIR is kept: it names where Claude keeps its config and conversations, not a
// session, and worktreeHasConversation reads the same value.
const KEPT_CLAUDE_VARIABLES = ["CLAUDE_CONFIG_DIR"];

function unsetClaudeVariables(inherited) {
  const names = Object.keys(inherited).filter(isSessionVariable);
  return Object.fromEntries(names.map((name) => [name, null]));
}

function isSessionVariable(name) {
  return name.startsWith("CLAUDE") && !KEPT_CLAUDE_VARIABLES.includes(name);
}

// `claude --continue` reopens the worktree's most recent conversation; a worktree with none starts
// Claude on Start's prompt.
function claudeCommand(resume) {
  return resume ? "claude --continue" : 'claude "$PLAN_PROMPT"';
}

// A login shell that runs Claude, then stays as a shell in the worktree once Claude exits, so
// Claude can be started again there. The prompt travels in PLAN_PROMPT, never through shell quoting.
// inherited is the window's environment (process.env), whose CLAUDE* variables are unset.
function claudeTerminalOptions(plan, shell, inherited = {}, resume = false) {
  return {
    name: claudeTerminalName(plan),
    cwd: plan.worktree,
    shellPath: shell,
    shellArgs: ["-l", "-c", `${claudeCommand(resume)}; exec "${shell}" -l`],
    env: { ...unsetClaudeVariables(inherited), PLAN_KEY: plan.key, PLAN_PROMPT: plan.prompt },
    isTransient: true,
  };
}

// The issue's Claude terminal among the window's terminals (anything with a name), or undefined.
function findClaudeTerminal(plan, terminals) {
  return terminals.find((terminal) => terminal.name === claudeTerminalName(plan));
}

function shellTerminalName(plan) {
  return `Shell ${plan.key}`;
}

// A plain login shell in the worktree, beside Claude's terminal; a Claude started in it registers
// too.
function shellTerminalOptions(plan, shell, inherited = {}) {
  return {
    name: shellTerminalName(plan),
    cwd: plan.worktree,
    shellPath: shell,
    shellArgs: ["-l"],
    env: { ...unsetClaudeVariables(inherited), PLAN_KEY: plan.key },
    isTransient: true,
  };
}

function findShellTerminal(plan, terminals) {
  return terminals.find((terminal) => terminal.name === shellTerminalName(plan));
}

function reviewTerminalName(plan) {
  return `Review ${plan.key}`;
}

// The line that starts a round of Claude's review of the issue's PR: the key, and the absolute
// path of the round's request.md, which holds the rest. skills/jira-issues/SKILL.md describes it.
function reviewPrompt(key, requestPath) {
  return `${key} review: read ${requestPath} and do the review it describes.`;
}

// Claude's review of the issue's PR: a fresh Claude, not the issue's own conversation, started as
// the issue's Claude terminal starts one, in the worktree, with the round's line submitted at once.
// The round's prompt stops unless its working directory is the worktree.
// CLAUDE_CODE_SKIP_PROMPT_HISTORY keeps the reviewer's transcript off disk, so the worktree's
// conversations stay the author's: `claude --continue` in the issue's terminal never resumes a
// reviewer, and a worktree reviewed before its first Claude still starts on Start's prompt. The
// session still registers, so `claude agents` lists it for review.py claim.
// https://code.claude.com/docs/en/env-vars#variables
function reviewTerminalOptions(plan, requestPath, shell, inherited = {}) {
  const reviewer = { ...plan, prompt: reviewPrompt(plan.key, requestPath) };
  const options = claudeTerminalOptions(reviewer, shell, inherited);
  return { ...options, name: reviewTerminalName(plan), env: { ...options.env, CLAUDE_CODE_SKIP_PROMPT_HISTORY: "1" } };
}

function findReviewTerminal(plan, terminals) {
  return terminals.find((terminal) => terminal.name === reviewTerminalName(plan));
}

// The page to show for an issue with no ticket file, its Jira page, or null. A ticket file wins.
function ticketPage(plan) {
  return plan.ticket === null ? plan.ticketUrl : null;
}

// The integrated browser's open command's options for the issue's page: with reuseUrlFilter, a
// browser tab already on that page is focused (and navigated to it again) instead of a second one
// opening. The filter is the page's own URL, so the tab's scheme, host and path must match it; a
// query Jira adds to the tab's URL is ignored, since the filter has none.
// https://github.com/microsoft/vscode/blob/30b94dbdc76768b9a9f88288a31c07018c19d6fd/src/vs/workbench/contrib/browserView/electron-browser/features/browserTabManagementFeatures.ts#L269-L334
function ticketPageOptions(plan) {
  const page = ticketPage(plan);
  return page ? { url: page, reuseUrlFilter: page } : null;
}

const ISSUE_TERMINAL = new RegExp(`^(Claude|Shell) (${ISSUE_KEY_PATTERN})$`);

// The issue a terminal was opened for, from its name ("Claude <KEY>" or "Shell <KEY>"), or null.
function terminalIssue(terminal) {
  const match = ISSUE_TERMINAL.exec(terminal.name);
  return match ? { kind: match[1], key: match[2] } : null;
}

// The issues whose Claude terminal is open in this window.
function keysWithClaude(terminals) {
  return terminals
    .map(terminalIssue)
    .filter((issue) => issue?.kind === "Claude")
    .map((issue) => issue.key);
}

// Claude Code keeps a directory's conversations as <session-id>.jsonl files in
// <config dir>/projects/<the directory with every non-alphanumeric character replaced by "-">, the
// config dir being CLAUDE_CONFIG_DIR or ~/.claude. A name past 200 characters is cut and given a
// hash Claude Code doesn't document, so such a worktree has no directory here (null).
// https://code.claude.com/docs/en/sessions#where-transcripts-are-stored
const MAX_PROJECT_DIR_NAME = 200;

function claudeProjectDir(worktree, env = {}, home = os.homedir()) {
  const name = worktree.replace(/[^A-Za-z0-9]/g, "-");
  if (name.length > MAX_PROJECT_DIR_NAME) return null;
  return path.join(env.CLAUDE_CONFIG_DIR || path.join(home, ".claude"), "projects", name);
}

function hasConversation(fileNames) {
  return fileNames.some((name) => name.endsWith(".jsonl"));
}

// Whether Claude has run in the worktree before, so `claude --continue` has something to reopen.
function worktreeHasConversation(worktree, env = {}, home = os.homedir()) {
  const dir = claudeProjectDir(worktree, env, home);
  if (!dir) return false;
  try {
    return hasConversation(fs.readdirSync(dir));
  } catch {
    return false;
  }
}

// What a /browse?url=<url>[&key=<KEY>] link asks of this window: { url } to open, or { refusal }
// saying why not. Every issue shares the one plan window, so the key names no other window and
// isn't checked.
function browseRequest(query) {
  const url = new URLSearchParams(query).get("url");
  if (!url) return { refusal: "/browse needs ?url=" };
  if (!isWebUrl(url)) return { refusal: `/browse opens only http(s) links, not ${url}` };
  return { url };
}

function isWebUrl(url) {
  try {
    return ["http:", "https:"].includes(new URL(url).protocol);
  } catch {
    return false;
  }
}

// What a /open?key=<KEY> link asks of this window: { key } to open, or { refusal } saying why not.
function openRequest(query) {
  const key = new URLSearchParams(query).get("key");
  if (!key) return { refusal: "/open needs ?key=" };
  if (!isIssueKey(key)) return { refusal: `/open needs an issue key, not ${key}` };
  return { key };
}

// What a /done or /done-tab link asks of this window: { key } to close the issue's terminals and PR
// tab (/done), or its own tab (/done-tab), or { refusal }. Like /open, the link carries the key
// only; the PR's URL and the ticket's path come from the issue's workspace file.
function doneRequest(route, query) {
  const key = new URLSearchParams(query).get("key");
  if (!key) return { refusal: `${route} needs ?key=` };
  if (!isIssueKey(key)) return { refusal: `${route} needs an issue key, not ${key}` };
  return { key };
}

// The issue's own terminals among the window's, each found by its exact name, so a terminal named
// for another key, or merely resembling one, is never in the answer. A missing one is undefined.
function issueTerminals(key, terminals) {
  const plan = { key };
  return {
    shell: findShellTerminal(plan, terminals),
    claude: findClaudeTerminal(plan, terminals),
    review: findReviewTerminal(plan, terminals),
  };
}

// The tabs showing target: { file } the tab on that file, or { url } a browser tab on that page,
// which is the URL itself or a page under it (/pull/5/files for /pull/5, never /pull/50). A tab is
// { file, url }, either null.
function tabsShowing(tabs, target) {
  return tabs.filter((tab) => (target.file ? tab.file === target.file : isUnder(tab.url, target.url)));
}

function isUnder(tabUrl, url) {
  if (!tabUrl || !url) return false;
  return tabUrl === url || ["/", "?", "#"].some((separator) => tabUrl.startsWith(url + separator));
}

function doneLine(outcome, text) {
  return `${outcome}: ${text}`;
}

const CLEAN_CLOSE_POLLS = 50;
const CLEAN_CLOSE_POLL_MS = 200;

// Ends what a terminal runs with Ctrl-C twice, then removes it once nothing it started is left
// running (isBusy(terminal) is false). Nothing is killed: a terminal still busy after the polls is
// left open. Resolves to the line saying which.
async function cleanClose(terminal, isBusy, wait = sleep) {
  terminal.sendText("\x03", false);
  terminal.sendText("\x03", false);
  for (let poll = 0; poll < CLEAN_CLOSE_POLLS; poll++) {
    if (!(await isBusy(terminal))) {
      terminal.dispose();
      return doneLine("ran", `closed the ${terminal.name} terminal`);
    }
    await wait(CLEAN_CLOSE_POLL_MS);
  }
  return doneLine("failed", `${terminal.name} is still running after Ctrl-C twice; left open, close it by hand`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// The first three steps of plan done, as the lines it prints: the Shell terminal removed, then
// Claude's and the review's each closed cleanly. A terminal that isn't open is skipped, not an error.
async function closeIssueTerminals(key, terminals, isBusy, wait = sleep) {
  const found = issueTerminals(key, terminals);
  const lines = [];
  if (found.shell) {
    found.shell.dispose();
    lines.push(doneLine("ran", `closed the ${found.shell.name} terminal`));
  } else {
    lines.push(doneLine("skipped", `no ${shellTerminalName({ key })} terminal open`));
  }
  for (const [name, terminal] of [[claudeTerminalName({ key }), found.claude], [reviewTerminalName({ key }), found.review]]) {
    lines.push(terminal ? await cleanClose(terminal, isBusy, wait) : doneLine("skipped", `no ${name} terminal open`));
  }
  return lines;
}

// The issue's record, or null when its workspace file is gone (empty text), as it is after a Done
// that finished; a file that exists but isn't a record still throws.
function maybeIssueRecord(workspaceText, key) {
  return workspaceText === "" ? null : issueRecord(workspaceText, key);
}

// The PR-tab step of plan done, as a line. closeTabs(target, what) closes tabs and returns its line.
// A workspace file that is gone, or has no PR URL, skips the step and names what to close by hand.
async function prTabLine(key, workspaceText, closeTabs) {
  const what = "the PR's browser tab";
  try {
    const record = maybeIssueRecord(workspaceText, key);
    if (!record) return doneLine("skipped", `${key}'s workspace file is gone, so the PR's URL is unknown; close ${what} by hand`);
    if (!record.prUrl) return doneLine("skipped", `no PR URL in the workspace file; close ${what} by hand`);
    return await closeTabs({ url: record.prUrl }, what);
  } catch (error) {
    return doneLine("failed", error.message);
  }
}

// The issue's own tab step: the ticket's file tab for a store issue, the Jira page's browser tab
// for a Jira one.
async function issueTabLine(key, workspaceText, closeTabs) {
  try {
    const record = maybeIssueRecord(workspaceText, key);
    if (!record) return doneLine("skipped", `${key}'s workspace file is gone, so its ticket is unknown; close the issue's tab by hand`);
    if (record.ticket) return await closeTabs({ file: record.ticket }, "the ticket's editor tab");
    return await closeTabs({ url: record.ticketUrl }, "the Jira page's browser tab");
  } catch (error) {
    return doneLine("failed", error.message);
  }
}

// Where the window tells plan done what it did: <KEY>.done-result.json beside the issue's workspace
// file, one { route, lines } object, the route naming the link it answers.
function doneResultFile(key, workspaceDir) {
  return path.join(expandHome(workspaceDir), `${key}.done-result.json`);
}

const ROUND_NUMBER = /^(0|[1-9][0-9]*)$/;
const REQUEST_TOKEN = /^[0-9a-f]{32}$/;

// What a /review?key=<KEY>&round=<n>&token=<token> link asks of this window: { key, round, token }
// to review, or { refusal } saying why not. The link starts a Claude on a prompt it submits at once,
// so it carries no prompt and no path: reviewRequestFile builds the round's request.md from the
// issue's worktree, and starts the review only when request.json there carries the token.
function reviewRequest(query) {
  const params = new URLSearchParams(query);
  const [key, round, token] = ["key", "round", "token"].map((name) => params.get(name));
  if (!key || !round || !token) return { refusal: "/review needs ?key=, &round= and &token=" };
  if (!isIssueKey(key)) return { refusal: `/review needs an issue key, not ${key}` };
  if (!ROUND_NUMBER.test(round)) return { refusal: `/review needs a round number, not ${round}` };
  if (!REQUEST_TOKEN.test(token)) return { refusal: "/review's token isn't a request token" };
  return { key, round, token };
}

// The round's request.md: review.py's rounds_folder(), .tmp/<KEY>/rounds/<n>/ in the worktree.
// The two layouts must stay equal.
function roundRequestPath(worktree, key, round) {
  return path.join(worktree, ".tmp", key, "rounds", round, "request.md");
}

// { request }, the real path of the round's request.md, or { refusal }. The file must exist at the
// path built under the worktree's real path, with no symbolic link on the way, and its request.json
// must name the round and carry the link's token, which only `review.py request` writes and
// `plan review` sends. A round whose claimed reviewer is still running is refused, so a replayed
// link can't close that reviewer's terminal mid-review; isRunning(pid) says whether a pid is alive.
function reviewRequestFile(plan, link, isRunning = processRunning) {
  const worktree = realPath(plan.worktree);
  if (!worktree) return { refusal: `${plan.key}'s worktree ${plan.worktree} isn't there` };
  const request = roundRequestPath(worktree, plan.key, link.round);
  if (realPath(request) !== request) {
    return { refusal: `${plan.key} has no request.md for round ${link.round} at ${request}` };
  }
  const stored = requestRecord(path.join(path.dirname(request), "request.json"));
  if (!isRequestedBy(stored, link)) {
    return { refusal: `round ${link.round} of ${plan.key} wasn't requested with this link's token` };
  }
  const reviewer = runningReviewer(stored, isRunning);
  if (reviewer) {
    return { refusal: `round ${link.round}'s reviewer (pid ${reviewer.pid}) is still running; close its ${reviewTerminalName(plan)} terminal first` };
  }
  return { request };
}

// The reviewer review.py claim recorded in request.json, while its pid is alive, else null.
function runningReviewer(stored, isRunning) {
  const reviewer = stored.reviewer;
  return reviewer && Number.isInteger(reviewer.pid) && isRunning(reviewer.pid) ? reviewer : null;
}

// Whether a process with this pid exists: signal 0 checks without sending anything. EPERM means it
// exists under another user.
function processRunning(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
}

// What a review link does in the window: { refusal }, which closes nothing, or { close, options }:
// the Review terminal left from an earlier round (or null), closed only now that the link's request
// is found, and the new Review terminal's options on that request's real path.
function reviewStart(plan, link, terminals, shell, inherited = {}, isRunning = processRunning) {
  const found = reviewRequestFile(plan, link, isRunning);
  if (found.refusal) return { refusal: found.refusal };
  return {
    close: findReviewTerminal(plan, terminals) || null,
    options: reviewTerminalOptions(plan, found.request, shell, inherited),
  };
}

// Whether request.json, read as stored (or null), is the request the link names.
function isRequestedBy(stored, link) {
  return stored !== null && String(stored.round) === link.round && sameToken(stored.token, link.token);
}

function realPath(file) {
  try {
    return fs.realpathSync(file);
  } catch {
    return null;
  }
}

// request.json's record, or null when it is missing, isn't JSON, or is reached through a symbolic
// link: the record is read only at the path built under the worktree.
function requestRecord(file) {
  try {
    if (realPath(file) !== file) return null;
    const record = JSON.parse(fs.readFileSync(file, "utf8"));
    return record && typeof record === "object" ? record : null;
  } catch {
    return null;
  }
}

// The token compared in constant time, so a link's guesses learn nothing from how long a refusal takes.
// A stored token that isn't a request token is no match: timingSafeEqual needs equal byte lengths.
function sameToken(stored, given) {
  if (typeof stored !== "string" || !REQUEST_TOKEN.test(stored)) return false;
  return crypto.timingSafeEqual(Buffer.from(stored), Buffer.from(given));
}

// The line the output channel logs for a link: its route, and its key and round when it has them.
// Nothing else from the query is logged, so a review link's token never reaches the log.
function linkLogLine(route, query) {
  const params = new URLSearchParams(query);
  const named = ["key", "round"].filter((name) => params.has(name)).map((name) => `${name}=${params.get(name)}`);
  return `starting link ${[route, ...named].join(" ")}`;
}

// The Issues view in the Explorer: one item per issue in the issues file, in its order, coloured
// from the pulses file. Both files are plan's (lib/term.py, lib/pulses.py) and live outside every
// workspace; each is read whole on every change, so a half-written one shows fewer items or
// colours, never an error.

const PULSE_COLORS = ["orange", "green"];

function isIssueKey(text) {
  return ISSUE_KEY.test(text);
}

function fileLines(text) {
  return text.split(/\r?\n/).map((line) => line.trim());
}

// The issues file: one <KEY> per line. A line that isn't a key is skipped, and a key listed twice
// shows once, where it first appears.
function parseIssueKeys(text) {
  return [...new Set(fileLines(text).filter(isIssueKey))];
}

// The pulses file: <KEY>=orange or <KEY>=green lines, as { KEY: colour }. A key with no line is
// off; a line that isn't that shape is skipped.
function parsePulses(text) {
  return Object.fromEntries(fileLines(text).map(parsePulseLine).filter(Boolean));
}

function parsePulseLine(line) {
  const [key, color, ...rest] = line.split("=");
  if (rest.length || !isIssueKey(key) || !PULSE_COLORS.includes(color)) return null;
  return [key, color];
}

// A pulse is told apart by its icon as well as its colour (L7): orange is a Claude waiting on a
// person or a review, green a fresh report. A codicon's colour is a theme colour id; orange is a
// solid warning triangle, which no codicon is, so it is an SVG in the extension (iconFile, relative
// to the extension's folder) that carries its own colour.
const PULSE_LOOKS = {
  orange: { iconFile: "media/waiting.svg" },
  green: { icon: "pass-filled", themeColor: "charts.green" },
  off: { icon: "circle-outline", themeColor: null },
};

// The Issues view's items, labelled with their keys; openKeys are the issues whose Claude terminal
// is open in this window.
function issueItems(keys, pulses, openKeys) {
  return keys.map((key) => {
    const pulse = pulses[key] || "off";
    return { key, pulse, ...PULSE_LOOKS[pulse], isOpen: openKeys.includes(key) };
  });
}

function pulsedCount(issues) {
  return issues.filter((issue) => issue.pulse !== "off").length;
}

function expandHome(file, home = os.homedir()) {
  return file === "~" || file.startsWith("~/") ? path.join(home, file.slice(1)) : file;
}

// The issue's own workspace file: <KEY>.code-workspace in workspaceDir, as lib/term.py's
// workspaceFilePath names it.
function workspaceFilePath(key, workspaceDir) {
  return path.join(expandHome(workspaceDir), `${key}.code-workspace`);
}

// A missing or unreadable file reads as empty: no issues, or no colours.
function readTextOrEmpty(file) {
  try {
    return fs.readFileSync(file, "utf8");
  } catch {
    return "";
  }
}

// The command that clears an issue's colour: the plan CLI's `pulse <KEY> off`, as [file, args] for
// execFile. The CLI writes the pulses file under the lock the status engine and Done also take
// (lib/pulses.py setPulse), which node cannot do, so the extension never writes that file itself.
function clearPulseCommand(command, key) {
  return [command, ["pulse", key, "off"]];
}

// Whether `key` has a colour to clear: only a key with a line in the pulses file does, so a click
// on an uncoloured issue runs no `plan pulse` at all.
function hasPulse(pulses, key) {
  return Object.hasOwn(pulses, key);
}

const PLAN_WORKSPACE_FILE = "plan.code-workspace";

// Whether this window is the plan window (ADR 0004: one window holds every issue): the one whose
// workspace file is <workspaceDir>/plan.code-workspace. workspaceFile is undefined in a window
// with a folder or nothing open.
function isPlanWindow(workspaceFile, workspaceDir, home = os.homedir()) {
  if (!workspaceFile) return false;
  const planFile = path.resolve(expandHome(workspaceDir, home), PLAN_WORKSPACE_FILE);
  return path.resolve(workspaceFile) === planFile;
}

module.exports = {
  processRunning,
  ISSUE_KEY_PATTERN,
  hasPulse,
  isPlanWindow,
  browseRequest,
  openRequest,
  reviewRequest,
  doneRequest,
  issueTerminals,
  tabsShowing,
  doneLine,
  cleanClose,
  closeIssueTerminals,
  prTabLine,
  issueTabLine,
  maybeIssueRecord,
  doneResultFile,
  reviewRequestFile,
  reviewStart,
  linkLogLine,
  roundRequestPath,
  readPlan,
  issueRecord,
  unsetClaudeVariables,
  claudeTerminalName,
  claudeTerminalOptions,
  findClaudeTerminal,
  shellTerminalOptions,
  findShellTerminal,
  reviewPrompt,
  reviewTerminalOptions,
  findReviewTerminal,
  ticketPage,
  ticketPageOptions,
  keysWithClaude,
  claudeProjectDir,
  hasConversation,
  worktreeHasConversation,
  parseIssueKeys,
  parsePulses,
  issueItems,
  pulsedCount,
  expandHome,
  workspaceFilePath,
  readTextOrEmpty,
  clearPulseCommand,
};
