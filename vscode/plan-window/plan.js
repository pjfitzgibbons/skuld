// What an issue's window needs, worked out from its plan.* settings with no VS Code calls, so the
// tests can check it. extension.js carries it out.

// The plan.* settings of an issue's window, or null when the window isn't one plan start made.
function readPlan(setting) {
  const key = setting("key");
  if (!key) return null;
  const worktree = setting("worktree");
  if (!worktree) throw new Error(`plan.key is ${key} but plan.worktree is unset`);
  return { key, worktree, ticket: setting("ticket") || null, prompt: setting("prompt") || "" };
}

function claudeTerminalName(plan) {
  return `Claude ${plan.key}`;
}

// The window's own CLAUDE* variables, each set to null, which VS Code takes as "unset in the
// terminal". A window opened by `code -n` from inside a Claude session inherits that session's
// CLAUDE_CODE_CHILD_SESSION and CLAUDE_PID; a Claude started with them counts itself a child and
// never registers, so `claude agents --json` and `plan agents` wouldn't list it.
function unsetClaudeVariables(inherited) {
  const names = Object.keys(inherited).filter((name) => name.startsWith("CLAUDE"));
  return Object.fromEntries(names.map((name) => [name, null]));
}

// A login shell that runs Claude, then stays as a shell in the worktree once Claude exits, so
// Claude can be started again there. The prompt travels in PLAN_PROMPT, never through shell quoting.
// inherited is the window's environment (process.env), whose CLAUDE* variables are unset.
function claudeTerminalOptions(plan, shell, inherited = {}) {
  return {
    name: claudeTerminalName(plan),
    cwd: plan.worktree,
    shellPath: shell,
    shellArgs: ["-l", "-c", `claude "$PLAN_PROMPT"; exec "${shell}" -l`],
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

function ticketNeedsOpening(plan, openFilePaths) {
  return plan.ticket !== null && !openFilePaths.includes(plan.ticket);
}

// What a /browse?url=<url>[&key=<KEY>] link asks of this window: { url } to open, or { refusal }
// saying why not. A link names the issue window it's for; one that lands in another window (focus
// moved between `code <workspace file>` and `code --open-url`) is refused rather than opened.
// windowKey is this window's plan.key, or null outside an issue window.
function browseRequest(query, windowKey) {
  const params = new URLSearchParams(query);
  const url = params.get("url");
  const key = params.get("key");
  if (!url) return { refusal: "/browse needs ?url=" };
  if (!isWebUrl(url)) return { refusal: `/browse opens only http(s) links, not ${url}` };
  if (key && key !== windowKey) return { refusal: `the link is for ${key}'s window, and this is ${windowKey || "no issue's"}: run plan browse in ${key}'s window` };
  return { url };
}

// What an /open?file=<path> link asks of this window: { file } to open in a tab, or { refusal }
// saying why not. The local server sends it for a ticket page's Open in VS Code, since
// `code <file>` opens a file in whichever window's folders hold it.
function openRequest(query) {
  const file = new URLSearchParams(query).get("file");
  if (!file) return { refusal: "/open needs ?file=" };
  if (!file.startsWith("/")) return { refusal: `/open takes an absolute path, not ${file}` };
  return { file };
}

function isWebUrl(url) {
  try {
    return ["http:", "https:"].includes(new URL(url).protocol);
  } catch {
    return false;
  }
}

module.exports = {
  browseRequest,
  openRequest,
  readPlan,
  unsetClaudeVariables,
  claudeTerminalName,
  claudeTerminalOptions,
  findClaudeTerminal,
  shellTerminalOptions,
  findShellTerminal,
  ticketNeedsOpening,
};
