// The plan window: one VS Code window for every issue, and the only window this extension acts in.
// Every window loads it, but only the window on <plan.workspaceDir>/plan.code-workspace gets the
// Issues view (package.json's when: plan.isPlanWindow), the click command and the file watchers; in
// any other window a link shows one message saying it is for the plan window. Its Issues view in the Explorer shows one
// item per issue in plan's issues file, tinted from its pulses file and refreshed when either
// changes; choosing an item, or a vscode://skuld.plan-window/open?key=<KEY> link, runs openIssue.
// An issue that leaves the issues file leaves the view; `plan done` has the window close its
// terminals and tabs first (the /done and /done-tab links below).
//
// The window also handles vscode://skuld.plan-window/browse?url=<url>[&key=<KEY>], which `plan
// browse` sends: it opens the page in the window's integrated browser; and
// vscode://skuld.plan-window/review?key=<KEY>&round=<n>&token=<token>, which `plan review` sends: it
// starts Claude's review of the issue's PR in a "Review <KEY>" terminal in the issue's worktree, on
// the round's request.md, once request.json there carries the token.
const { execFile } = require("child_process");
const fs = require("fs");
const path = require("path");
const vscode = require("vscode");
const windowPlan = require("./plan");

let refreshIssues = () => {};

// Output > "plan window": when the window activated and which link or issue it started, for finding
// why a click or link did nothing. Set in activate.
let planLog;

// Every VS Code window activates this extension, but only the plan window acts: elsewhere it
// registers no view, command or watcher, and a link gets a message saying where it belongs.
function activate(context) {
  planLog = vscode.window.createOutputChannel("plan window", { log: true });
  context.subscriptions.push(planLog);
  const isPlan = isPlanWindow();
  planLog.info(`activated; plan window: ${isPlan}`);
  vscode.commands.executeCommand("setContext", "plan.isPlanWindow", isPlan);
  context.subscriptions.push(vscode.window.registerUriHandler({ handleUri }));
  if (isPlan) registerIssuesView(context);
}

function isPlanWindow() {
  const workspaceFile = vscode.workspace.workspaceFile;
  return windowPlan.isPlanWindow(workspaceFile && workspaceFile.fsPath, planSetting("workspaceDir"));
}

async function handleUri(uri) {
  planLog.info(windowPlan.linkLogLine(uri.path, uri.query));
  if (!isPlanWindow()) {
    vscode.window.showInformationMessage("plan window: this link is for the plan window.");
    return;
  }
  if (uri.path === "/browse") return openBrowseLink(uri.query);
  if (uri.path === "/open") return openOpenLink(uri.query);
  if (uri.path === "/review") return openReviewLink(uri.query);
  if (uri.path === "/done") return openDoneLink(uri.path, uri.query, closeTerminalsAndPrTab);
  if (uri.path === "/done-tab") return openDoneLink(uri.path, uri.query, closeIssueTab);
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

async function openReviewLink(query) {
  const link = windowPlan.reviewRequest(query);
  if (link.refusal) {
    vscode.window.showErrorMessage(`plan window: ${link.refusal}`);
    return;
  }
  try {
    startReview(readIssueRecord(link.key), link);
  } catch (error) {
    vscode.window.showErrorMessage(`plan window: ${error.message}`);
  }
}

// vscode://skuld.plan-window/done?key=<KEY> (terminals, then the PR's tab) and /done-tab?key=<KEY>
// (the issue's own tab) are what `plan done` sends. Each carries the key only: the PR's URL and the
// ticket's path come from the issue's workspace file. What the window did goes to the result file
// plan done reads, one line per step.
async function openDoneLink(route, query, closeSteps) {
  const request = windowPlan.doneRequest(route, query);
  if (request.refusal) {
    vscode.window.showErrorMessage(`plan window: ${request.refusal}`);
    return;
  }
  let lines;
  try {
    lines = await closeSteps(request.key);
  } catch (error) {
    lines = [windowPlan.doneLine("failed", error.message)];
  }
  lines.forEach((line) => planLog.info(`done ${request.key} ${line}`));
  fs.writeFileSync(windowPlan.doneResultFile(request.key, planSetting("workspaceDir")), JSON.stringify({ route, lines }));
}

async function closeTerminalsAndPrTab(key) {
  const lines = await windowPlan.closeIssueTerminals(key, vscode.window.terminals, hasRunningChild);
  return [...lines, await windowPlan.prTabLine(key, issueWorkspaceText(key), closeTabs)];
}

async function closeIssueTab(key) {
  return [await windowPlan.issueTabLine(key, issueWorkspaceText(key), closeTabs)];
}

async function closeTabs(target, what) {
  const tabs = vscode.window.tabGroups.all.flatMap((group) => group.tabs);
  const toClose = tabs.filter((tab) => windowPlan.tabsShowing([tabTarget(tab)], target).length);
  if (!toClose.length) return windowPlan.doneLine("skipped", `${what} isn't open`);
  await vscode.window.tabGroups.close(toClose);
  return windowPlan.doneLine("ran", `closed ${what}`);
}

// A tab as { file, url }: a text tab's file, or the page of a browser tab. 🔍 Unverified: the
// integrated browser's tab input isn't documented in the extension API, so its page is read from
// the input's url, or its uri when that is an http(s) one.
function tabTarget(tab) {
  const input = tab.input || {};
  const uri = input.uri;
  const file = uri && uri.scheme === "file" ? uri.fsPath : null;
  const url = input.url || (uri && uri.scheme !== "file" ? uri.toString() : null);
  return { file, url: url ? String(url) : null };
}

// Whether a terminal's shell still has a child, which is Claude (or anything else) running in it:
// the terminal's `claude; exec shell` leaves the shell with none once Claude exits.
function hasRunningChild(terminal) {
  return terminal.processId.then(
    (pid) =>
      new Promise((resolve) => {
        if (!pid) return resolve(false);
        execFile("pgrep", ["-P", String(pid)], (error) => resolve(!error));
      }),
  );
}

// Each round is reviewed by a fresh Claude on that round's line, so a Review terminal left from an
// earlier round is closed and a new one started; focusing it would leave the new round unstarted.
// review.py starts, restarts or reopens a round only once the last one finished or its claimed
// reviewer is gone, and the link itself is refused while the round's claimed reviewer is running, so
// a replayed link can't close a reviewer at work. A link whose request isn't found, or whose token
// isn't the round's, is refused too, and a refused link closes nothing.
function startReview(plan, link) {
  const start = windowPlan.reviewStart(plan, link, vscode.window.terminals, userShell(), process.env);
  if (start.refusal) throw new Error(start.refusal);
  if (start.close) start.close.dispose();
  planLog.info(`starting review of ${plan.key} round ${link.round}`);
  const terminal = vscode.window.createTerminal({ ...start.options, location: { viewColumn: vscode.ViewColumn.Two } });
  terminal.show(false);
}

// What choosing an issue does: bring up its ticket (a store issue's file, or a Jira issue's page),
// its shell and its Claude in this window, then ask the plan CLI to clear its colour, since it's
// been seen. The status engine (lib/agents.py) colours it again only when its Claude's state changes.
// The ticket steps are best-effort, each with its own warning (a moved ticket file must not keep the
// terminals from opening); an issue record that can't be read stops the click.
async function openIssue(key) {
  planLog.info(`starting open ${key}`);
  try {
    const plan = readIssueRecord(key);
    await bestEffort(key, () => showTicket(plan));
    await bestEffort(key, () => openTicketPage(plan));
    showShellTerminal(plan);
    showClaudeTerminal(plan);
    if (windowPlan.hasPulse(readPulses(), key)) clearPulse(key);
  } catch (error) {
    vscode.window.showErrorMessage(`plan window: ${error.message}`);
  }
  refreshIssues();
}

async function bestEffort(key, ticketStep) {
  try {
    await ticketStep();
  } catch (error) {
    vscode.window.showWarningMessage(`plan window: could not show the ticket for ${key}: ${error.message}`);
  }
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
  return windowPlan.issueRecord(issueWorkspaceText(key), key);
}

function issueWorkspaceText(key) {
  return windowPlan.readTextOrEmpty(windowPlan.workspaceFilePath(key, planSetting("workspaceDir")));
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
    treeDataProvider: {
      onDidChangeTreeData: changed.event,
      getChildren: () => issueTreeItems(context.extensionUri),
      getTreeItem: (item) => item,
    },
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

function readPulses() {
  return windowPlan.parsePulses(windowPlan.readTextOrEmpty(pulsesFile()));
}

function readIssues() {
  return windowPlan.issueItems(readIssueKeys(), readPulses(), windowPlan.keysWithClaude(vscode.window.terminals));
}

function issueTreeItems(extensionUri) {
  return readIssues().map((issue) => issueTreeItem(issue, extensionUri));
}

function issueIcon(issue, extensionUri) {
  if (issue.iconFile) return vscode.Uri.joinPath(extensionUri, issue.iconFile);
  const tint = issue.themeColor ? new vscode.ThemeColor(issue.themeColor) : undefined;
  return new vscode.ThemeIcon(issue.icon, tint);
}

function issueTreeItem(issue, extensionUri) {
  const item = new vscode.TreeItem(issue.key);
  item.iconPath = issueIcon(issue, extensionUri);
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
