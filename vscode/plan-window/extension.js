// When an issue's window opens, make sure its ticket is in a tab in the first editor group and its
// Claude terminal is running in a second group beside it, then focus Claude. A window without
// plan.key is left alone.
const vscode = require("vscode");
const windowPlan = require("./plan");

async function activate() {
  try {
    await setUpIssueWindow();
  } catch (error) {
    vscode.window.showErrorMessage(`plan window: ${error.message}`);
  }
}

async function setUpIssueWindow() {
  const settings = vscode.workspace.getConfiguration("plan");
  const plan = windowPlan.readPlan((name) => settings.get(name));
  if (!plan) return;
  await openTicketIfClosed(plan);
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

function createClaudeTerminal(plan) {
  return vscode.window.createTerminal({
    ...windowPlan.claudeTerminalOptions(plan, vscode.env.shell || "/bin/bash"),
    location: { viewColumn: vscode.ViewColumn.Two },
  });
}

function openFilePaths() {
  return vscode.window.tabGroups.all
    .flatMap((group) => group.tabs)
    .filter((tab) => tab.input instanceof vscode.TabInputText)
    .map((tab) => tab.input.uri.fsPath);
}

function deactivate() {}

module.exports = { activate, deactivate };
