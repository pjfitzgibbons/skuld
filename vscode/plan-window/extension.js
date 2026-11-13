// When an issue's window opens, make sure its ticket is in a tab in the first editor group, its
// Claude terminal is running in a second group beside it and a shell waits in the panel, then focus
// Claude. A window without plan.key is left alone.
//
// Every window also handles vscode://skuld.plan-window/browse?url=<url>[&key=<KEY>], which `plan
// browse` sends: it opens the page in the window's integrated browser, unless the key names
// another window. And /open?file=<path>, which the local server sends for a ticket page's Open in
// VS Code: it opens the file in a tab of this window.
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

const LINK_HANDLERS = { "/browse": browse, "/open": openFile };

async function handleUri(uri) {
  const handler = LINK_HANDLERS[uri.path];
  if (!handler) {
    vscode.window.showErrorMessage(`plan window: unknown link ${uri.path}`);
    return;
  }
  await handler(uri.query, vscode.workspace.getConfiguration("plan").get("key") || null);
}

async function browse(query, windowKey) {
  const request = windowPlan.browseRequest(query, windowKey);
  if (request.refusal) {
    vscode.window.showErrorMessage(`plan window: ${request.refusal}`);
    return;
  }
  // The integrated browser's own command, which takes any http(s) URL; the localhost link opener
  // takes only localhost and loopback hosts.
  await vscode.commands.executeCommand("workbench.action.browser.open", request.url);
}

async function openFile(query) {
  const request = windowPlan.openRequest(query);
  if (request.refusal) {
    vscode.window.showErrorMessage(`plan window: ${request.refusal}`);
    return;
  }
  const document = await vscode.workspace.openTextDocument(vscode.Uri.file(request.file));
  await vscode.window.showTextDocument(document, { preview: false });
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
    vscode.window.createTerminal(windowPlan.shellTerminalOptions(plan, userShell(), process.env));
  terminal.show(true);
}

function createClaudeTerminal(plan) {
  return vscode.window.createTerminal({
    ...windowPlan.claudeTerminalOptions(plan, userShell(), process.env),
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
