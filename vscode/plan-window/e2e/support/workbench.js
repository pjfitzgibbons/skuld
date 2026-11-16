// Questions the specs ask the running VS Code, and the few things they do to it, through the
// VS Code API (browser.executeWorkbench) so an answer is a fact about the window, not a guess from
// its pixels. Each function body runs inside VS Code: it can use only its `vscode` argument and
// what is passed in.
import { execFile } from "node:child_process";
import path from "node:path";
import { browser, $ } from "@wdio/globals";
import { eventually as eventuallyEqual } from "./eventually.js";

export function terminalNames() {
  return browser.executeWorkbench((vscode) => vscode.window.terminals.map((terminal) => terminal.name));
}

// Every open tab: its label, its file (for a text tab), and whether it is its group's active tab.
export function openTabs() {
  return browser.executeWorkbench((vscode) =>
    vscode.window.tabGroups.all.flatMap((group) =>
      group.tabs.map((tab) => ({
        label: tab.label,
        file: tab.input && tab.input.uri ? tab.input.uri.fsPath : null,
        isActive: tab.isActive,
        isPreview: tab.isPreview,
      })),
    ),
  );
}

export async function tabsOfFile(file) {
  return (await openTabs()).filter((tab) => tab.file === file);
}

// Shows a file in the first editor group, which makes it the active tab there.
export function showFile(file) {
  return browser.executeWorkbench(async (vscode, path) => {
    const document = await vscode.workspace.openTextDocument(vscode.Uri.file(path));
    await vscode.window.showTextDocument(document, { viewColumn: vscode.ViewColumn.One, preview: false });
  }, file);
}

// What choosing an issue in the Issues view does, by the command the view's items carry.
export function chooseIssue(key) {
  return browser.executeWorkbench(
    (vscode, issueKey) => vscode.commands.executeCommand("plan.openIssue", issueKey),
    key,
  );
}

// The extension host's values of these environment variables, which terminals inherit.
export function windowEnvironment(names) {
  return browser.executeWorkbench(
    (vscode, wanted) => Object.fromEntries(wanted.map((name) => [name, process.env[name]])),
    names,
  );
}

// Closes every terminal, editor and notification, so a spec starts with nothing of the last one's
// open: a warning one spec provokes must not stay on screen in the next.
export async function closeEverything() {
  await browser.executeWorkbench(async (vscode) => {
    vscode.window.terminals.forEach((terminal) => terminal.dispose());
    await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    await vscode.commands.executeCommand("notifications.clearAll");
  });
}

export function notificationCount() {
  return browser.execute(() => document.querySelectorAll(".notifications-toasts .notification-toast").length);
}

// How many notifications the notification center holds, which keeps a notification after its toast
// has hidden, so a check made late still sees what appeared at startup.
export async function notificationsInCenter() {
  await browser.executeWorkbench((vscode) => vscode.commands.executeCommand("notifications.showList"));
  const count = await browser.execute(() => document.querySelectorAll(".notifications-center .monaco-list-row").length);
  await browser.executeWorkbench((vscode) => vscode.commands.executeCommand("notifications.hideList"));
  return count;
}

// Whether one notification showing now has every one of these texts in its message.
export function notificationShows(texts) {
  return browser.execute(
    (wanted) =>
      [...document.querySelectorAll(".notifications-toasts .notification-list-item-message")].some((element) =>
        wanted.every((text) => element.textContent.includes(text)),
      ),
    texts,
  );
}

// The Issues view's items, in order, as the DOM shows them: the key, the icon (the codicon's class,
// pass-filled or circle-outline, or "waiting.svg" for the custom orange triangle, read from the
// icon's background image) and the icon's colour, "" when it has none. The DOM is all the
// view exposes of a tree item's icon.
export function issueItems() {
  return browser.execute(() => {
    const header = [...document.querySelectorAll(".pane-header")].find(
      (element) => element.textContent.trim() === "Issues",
    );
    if (!header) return [];
    return [...header.parentElement.querySelectorAll(".monaco-list-row")].map((row) => {
      const icon = row.querySelector(".custom-view-tree-node-item-icon");
      return {
        key: row.getAttribute("aria-label").trim(),
        icon:
          [...icon.classList].find((name) => name.startsWith("codicon-") && name !== "codicon-colored") ||
          (getComputedStyle(icon).backgroundImage.match(/([^/"]+\.svg)/) || [])[1],
        color: icon.style.color,
      };
    });
  });
}

// Whether the Explorer has an Issues section at all, which only the plan window shows.
export function issuesViewIsShowing() {
  return browser.execute(() =>
    [...document.querySelectorAll(".pane-header")].some((element) => element.textContent.trim() === "Issues"),
  );
}

export async function issueKeys() {
  return (await issueItems()).map((item) => item.key);
}

// The colour a theme colour id (charts.orange) has now, as the DOM writes it (rgb(...)), so a
// spec compares an item's colour with the theme's and not with a number the theme may change.
export function themeColor(id) {
  return browser.execute(
    (variable) => {
      const probe = document.createElement("span");
      probe.style.color = `var(--vscode-${variable})`;
      document.querySelector(".monaco-workbench").append(probe);
      const resolved = probe.style.color && getComputedStyle(probe).color;
      probe.remove();
      return resolved;
    },
    id.replaceAll(".", "-"),
  );
}

// Shows the Issues view, which the Explorer has below its folders.
export function showIssuesView() {
  return browser.executeWorkbench((vscode) => vscode.commands.executeCommand("plan.issues.focus"));
}

// Clicks the issue's item in the Issues view, as a person does.
export async function clickIssue(key) {
  const item = await $(`.monaco-list-row[role="treeitem"][aria-label^="${key}"]`);
  await item.waitForClickable();
  await item.click();
}

// Sends the window a vscode://skuld.plan-window/... link. The link goes to the VS Code that owns
// the run's user data dir, the way VS Code's `--open-url` hands a link to a running instance, so
// it never reaches an installed VS Code that has registered the vscode:// scheme with the OS.
export function sendLink(link) {
  const options = browser.requestedCapabilities["wdio:vscodeOptions"];
  const userDataDir = path.join(options.storagePath, "settings");
  const { NODE_OPTIONS, ...env } = process.env;
  return new Promise((resolve, reject) => {
    execFile(options.binary, ["--user-data-dir", userDataDir, "--open-url", link], { env }, (error) =>
      error ? reject(error) : resolve(),
    );
  });
}

// The window's outer size. WebDriver's window commands (browser.setWindowSize, getWindowSize) are
// not supported by VS Code's chromedriver, so the page asks and resizes its own window.
export function windowSize() {
  return browser.execute(() => ({ width: window.outerWidth, height: window.outerHeight }));
}

// The window's size: the left half of the screen's available area, full height, so a person can
// watch it beside a terminal on a small screen.
export async function halfScreenSize() {
  const available = await browser.execute(() => ({ width: screen.availWidth, height: screen.availHeight }));
  return { width: Math.floor(available.width / 2), height: available.height };
}

// Moves the window to the screen's top left and resizes it, so the screen's bottom edge does not
// clamp it further than the screen's size does.
export async function resizeWindow() {
  const wanted = await halfScreenSize();
  await browser.execute(
    (w, h) => {
      window.moveTo(0, 0);
      window.resizeTo(w, h);
    },
    wanted.width,
    wanted.height,
  );
  await eventuallyEqual(windowSize, wanted);
}

// Whether the secondary side bar, where the chat panel opens, has any width.
export function secondarySideBarIsShowing() {
  return browser.execute(() => document.querySelector(".part.auxiliarybar").getBoundingClientRect().width > 0);
}
