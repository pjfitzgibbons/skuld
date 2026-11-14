// What the plan window needs, worked out with no VS Code calls, so the tests can check it.
// extension.js carries it out.
const fs = require("fs");
const os = require("os");
const path = require("path");

// The one issue-key rule. lib/issuekeys.py's ISSUE_KEY_PATTERN is the canonical copy; this string must
// stay equal to it (plan.test.js asserts the value). Every key check here is built from it.
const ISSUE_KEY_PATTERN = "[A-Z]+-[0-9]+";
const ISSUE_KEY = new RegExp(`^${ISSUE_KEY_PATTERN}$`);

// An issue: its plan.* settings, read from the issue's own workspace file (issueRecord), which
// Start writes and nothing opens. A store issue has a ticket file; a Jira issue has none, and
// ticketUrl, its Jira page, instead.
function readPlan(setting) {
  const key = setting("key");
  if (!key) return null;
  const worktree = setting("worktree");
  if (!worktree) throw new Error(`plan.key is ${key} but plan.worktree is unset`);
  const ticketUrl = setting("ticketUrl") || null;
  if (ticketUrl && !isWebUrl(ticketUrl)) throw new Error(`plan.ticketUrl is ${ticketUrl}, not an http(s) link`);
  return { key, worktree, ticket: setting("ticket") || null, ticketUrl, prompt: setting("prompt") || "" };
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
  ISSUE_KEY_PATTERN,
  hasPulse,
  isPlanWindow,
  browseRequest,
  openRequest,
  readPlan,
  issueRecord,
  unsetClaudeVariables,
  claudeTerminalName,
  claudeTerminalOptions,
  findClaudeTerminal,
  shellTerminalOptions,
  findShellTerminal,
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
