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

// A login shell that runs Claude, then stays as a shell in the worktree once Claude exits, so
// Claude can be started again there. The prompt travels in PLAN_PROMPT, never through shell quoting.
function claudeTerminalOptions(plan, shell) {
  return {
    name: claudeTerminalName(plan),
    cwd: plan.worktree,
    shellPath: shell,
    shellArgs: ["-l", "-c", `claude "$PLAN_PROMPT"; exec "${shell}" -l`],
    env: { PLAN_KEY: plan.key, PLAN_PROMPT: plan.prompt },
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

// A plain login shell in the worktree, beside Claude's terminal.
function shellTerminalOptions(plan, shell) {
  return {
    name: shellTerminalName(plan),
    cwd: plan.worktree,
    shellPath: shell,
    shellArgs: ["-l"],
    env: { PLAN_KEY: plan.key },
    isTransient: true,
  };
}

function findShellTerminal(plan, terminals) {
  return terminals.find((terminal) => terminal.name === shellTerminalName(plan));
}

function ticketNeedsOpening(plan, openFilePaths) {
  return plan.ticket !== null && !openFilePaths.includes(plan.ticket);
}

module.exports = {
  readPlan,
  claudeTerminalName,
  claudeTerminalOptions,
  findClaudeTerminal,
  shellTerminalOptions,
  findShellTerminal,
  ticketNeedsOpening,
};
