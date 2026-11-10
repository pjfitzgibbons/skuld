// When an issue's window opens, make sure its ticket is in a tab in the first editor group, its
// Claude terminal is running in a second group beside it and a shell waits in the panel, then focus
// Claude. A window without plan.key is left alone.
//
// Every window also handles vscode://skuld.plan-window/browse?url=<url>[&key=<KEY>], which `plan
// browse` sends: it opens the page in the window's integrated browser, unless the key names
// another window.
const vscode = require("vscode");
const windowPlan = require("./plan");

async function activate(context) {
  context.subscriptions.push(vscode.window.registerUriHandler({ handleUri }));
  try {
    await setUpIssueWindow();
  } catch (error) {
    vscode.window.showErrorMessage(`plan window: ${error.message}`);
  }
}

async function handleUri(uri) {
  if (uri.path !== "/browse") {
    vscode.window.showErrorMessage(`plan window: unknown link ${uri.path}`);
    return;
  }
  const windowKey = vscode.workspace.getConfiguration("plan").get("key") || null;
  const request = windowPlan.browseRequest(uri.query, windowKey);
  if (request.refusal) {
    vscode.window.showErrorMessage(`plan window: ${request.refusal}`);
    return;
  }
  // The integrated browser's own command, which takes any http(s) URL; the localhost link opener
  // takes only localhost and loopback hosts.
  await vscode.commands.executeCommand("workbench.action.browser.open", request.url);
}

async function setUpIssueWindow() {
  const settings = vscode.workspace.getConfiguration("plan");
  const plan = windowPlan.readPlan((name) => settings.get(name));
  if (!plan) return;
  await openTicketIfClosed(plan);
  showShellTerminal(plan);
  showClaudeTerminal(plan);
}

async function openTicketIfClosed(plan) {
  if (!windowPlan.ticketNeedsOpening(plan, openFilePaths())) return;
  const document = await vscode.workspace.openTextDocument(vscode.Uri.file(plan.ticket));
  await vscode.window.showTextDocument(document, {
    viewColumn: vscode.ViewColumn.One,
    preview: false,
    preserveFocus: true,
  });
}

function showClaudeTerminal(plan) {
  const terminal = windowPlan.findClaudeTerminal(plan, vscode.window.terminals) || createClaudeTerminal(plan);
  terminal.show(false);
}

function showShellTerminal(plan) {
  const terminal =
    windowPlan.findShellTerminal(plan, vscode.window.terminals) ||
    vscode.window.createTerminal(windowPlan.shellTerminalOptions(plan, userShell()));
  terminal.show(true);
}

function createClaudeTerminal(plan) {
  return vscode.window.createTerminal({
    ...windowPlan.claudeTerminalOptions(plan, userShell()),
    location: { viewColumn: vscode.ViewColumn.Two },
  });
}

function userShell() {
  return vscode.env.shell || "/bin/bash";
}

function openFilePaths() {
  return vscode.window.tabGroups.all
    .flatMap((group) => group.tabs)
    .filter((tab) => tab.input instanceof vscode.TabInputText)
    .map((tab) => tab.input.uri.fsPath);
}

function deactivate() {}

module.exports = { activate, deactivate };
