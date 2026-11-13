// The plan window: one VS Code window for every issue. Its Issues view in the Explorer shows one
// item per issue in plan's issues file, tinted from its pulses file and refreshed when either
// changes; choosing an item, or a vscode://skuld.plan-window/open?key=<KEY> link, runs openIssue.
// An issue that leaves the issues file leaves the view; its terminals stay until closed by hand,
// since `plan done` or its Claude may be running in them.
//
// The window also handles vscode://skuld.plan-window/browse?url=<url>[&key=<KEY>], which `plan
// browse` sends: it opens the page in the window's integrated browser.
const { execFile } = require("child_process");
const path = require("path");
const vscode = require("vscode");
const windowPlan = require("./plan");

let refreshIssues = () => {};

function activate(context) {
  context.subscriptions.push(vscode.window.registerUriHandler({ handleUri }));
  registerIssuesView(context);
}

async function handleUri(uri) {
  if (uri.path === "/browse") return openBrowseLink(uri.query);
  if (uri.path === "/open") return openOpenLink(uri.query);
  vscode.window.showErrorMessage(`plan window: unknown link ${uri.path}`);
}

async function openBrowseLink(query) {
  const request = windowPlan.browseRequest(query);
  if (request.refusal) {
    vscode.window.showErrorMessage(`plan window: ${request.refusal}`);
    return;
  }
  await openInBrowser(request.url);
}

// The integrated browser's own command, which takes any http(s) URL, or { url, reuseUrlFilter };
// the localhost link opener takes only localhost and loopback hosts.
async function openInBrowser(urlOrOptions) {
  await vscode.commands.executeCommand("workbench.action.browser.open", urlOrOptions);
}

async function openOpenLink(query) {
  const request = windowPlan.openRequest(query);
  if (request.refusal) {
    vscode.window.showErrorMessage(`plan window: ${request.refusal}`);
    return;
  }
  await openIssue(request.key);
}

// What choosing an issue does: bring up its ticket (a store issue's file, or a Jira issue's page),
// its shell and its Claude in this window, then ask the plan CLI to clear its colour, since it's
// been seen. The status engine (lib/agents.py) colours it again only when its Claude's state changes.
async function openIssue(key) {
  try {
    const plan = readIssueRecord(key);
    await showTicket(plan);
    await openTicketPage(plan);
    showShellTerminal(plan);
    showClaudeTerminal(plan);
    clearPulse(key);
  } catch (error) {
    vscode.window.showErrorMessage(`plan window: ${error.message}`);
  }
  refreshIssues();
}

// Runs `plan pulse <KEY> off` without blocking the extension host. The CLI writes the pulses file
// under the lock the status engine takes; this window never writes it. When the command fails the
// colour stays in the file, so the view still shows it, which is the true state.
function clearPulse(key) {
  const [file, args] = windowPlan.clearPulseCommand(planSetting("command"), key);
  execFile(file, args, (error, _stdout, stderr) => {
    if (!error) return;
    const reason = (stderr || "").trim() || error.message;
    vscode.window.showWarningMessage(`plan window: could not clear ${key}'s colour with "${file}": ${reason}`);
  });
}

// The issue's record is its own workspace file, which Start writes and nothing opens.
function readIssueRecord(key) {
  const file = windowPlan.workspaceFilePath(key, planSetting("workspaceDir"));
  return windowPlan.issueRecord(windowPlan.readTextOrEmpty(file), key);
}

// A store issue's ticket file, brought to the front on every click. VS Code reveals the tab that
// already has the document, so a second tab is never made.
async function showTicket(plan) {
  if (plan.ticket === null) return;
  const document = await vscode.workspace.openTextDocument(vscode.Uri.file(plan.ticket));
  await vscode.window.showTextDocument(document, {
    viewColumn: vscode.ViewColumn.One,
    preview: false,
    preserveFocus: true,
  });
}

// A Jira issue's page, in the browser tab already showing it if there is one, as a store issue's
// ticket tab is reused.
async function openTicketPage(plan) {
  const options = windowPlan.ticketPageOptions(plan);
  if (options) await openInBrowser(options);
}

function showClaudeTerminal(plan) {
  const terminal = windowPlan.findClaudeTerminal(plan, vscode.window.terminals) || createClaudeTerminal(plan);
  terminal.show(false);
}

function showShellTerminal(plan) {
  const terminal =
    windowPlan.findShellTerminal(plan, vscode.window.terminals) ||
    vscode.window.createTerminal(windowPlan.shellTerminalOptions(plan, userShell(), process.env));
  terminal.show(true);
}

function createClaudeTerminal(plan) {
  const resume = windowPlan.worktreeHasConversation(plan.worktree, process.env);
  return vscode.window.createTerminal({
    ...windowPlan.claudeTerminalOptions(plan, userShell(), process.env, resume),
    location: { viewColumn: vscode.ViewColumn.Two },
  });
}

function userShell() {
  return vscode.env.shell || "/bin/bash";
}

function planSetting(name) {
  return vscode.workspace.getConfiguration("plan").get(name);
}

function issuesFile() {
  return windowPlan.expandHome(planSetting("issuesFile"));
}

function pulsesFile() {
  return windowPlan.expandHome(planSetting("pulsesFile"));
}

function registerIssuesView(context) {
  const changed = new vscode.EventEmitter();
  const view = vscode.window.createTreeView("plan.issues", {
    treeDataProvider: { onDidChangeTreeData: changed.event, getChildren: issueTreeItems, getTreeItem: (item) => item },
  });
  refreshIssues = () => {
    view.badge = pulsedBadge(readIssues());
    changed.fire();
  };
  refreshIssues();
  context.subscriptions.push(
    changed,
    view,
    vscode.commands.registerCommand("plan.openIssue", openIssue),
    watchFile(issuesFile(), refreshIssues),
    watchFile(pulsesFile(), refreshIssues),
    vscode.window.onDidOpenTerminal(refreshIssues),
    vscode.window.onDidCloseTerminal(refreshIssues),
  );
}

// The files are outside every workspace folder, so each is watched through its folder's Uri, a
// non-recursive watch on the file's name; a rename into place counts as a create.
// https://code.visualstudio.com/api/references/vscode-api#workspace.createFileSystemWatcher
function watchFile(file, onChange) {
  const pattern = new vscode.RelativePattern(vscode.Uri.file(path.dirname(file)), path.basename(file));
  const watcher = vscode.workspace.createFileSystemWatcher(pattern);
  watcher.onDidCreate(onChange);
  watcher.onDidChange(onChange);
  watcher.onDidDelete(onChange);
  return watcher;
}

function readIssueKeys() {
  return windowPlan.parseIssueKeys(windowPlan.readTextOrEmpty(issuesFile()));
}

function readIssues() {
  const pulses = windowPlan.parsePulses(windowPlan.readTextOrEmpty(pulsesFile()));
  return windowPlan.issueItems(readIssueKeys(), pulses, windowPlan.keysWithClaude(vscode.window.terminals));
}

function issueTreeItems() {
  return readIssues().map(issueTreeItem);
}

function issueTreeItem(issue) {
  const item = new vscode.TreeItem(issue.key);
  const tint = issue.themeColor ? new vscode.ThemeColor(issue.themeColor) : undefined;
  item.iconPath = new vscode.ThemeIcon(issue.icon, tint);
  item.description = issue.isOpen ? "open" : undefined;
  item.command = { command: "plan.openIssue", title: "Open issue", arguments: [issue.key] };
  return item;
}

// The view's badge counts the coloured issues, so a pulse shows on the Explorer's icon even while
// the view is collapsed.
function pulsedBadge(issues) {
  const count = windowPlan.pulsedCount(issues);
  return count ? { value: count, tooltip: `${count} issue${count === 1 ? "" : "s"} to look at` } : undefined;
}

function deactivate() {}

module.exports = { activate, deactivate };
