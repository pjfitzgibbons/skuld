// WebdriverIO config for the plan window's end-to-end suite: a stable VS Code, downloaded into
// .wdio-vscode-service, started with this extension loaded and no other, a user data dir and
// extensions dir under the run's fixture directory, and the plan.* settings pointing at it. Two
// sessions run one after the other: the plan window (on the fixture's plan.code-workspace, running
// specs/*.e2e.js) and a window on another workspace (specs/other-window/).
// support/fixture.js says what the fixture holds and how it isolates the run.
import path from "node:path";
import { fileURLToPath } from "node:url";
import { resizeWindow } from "./support/workbench.js";
import { WATCHING, assertWatchable } from "./support/steps.js";
import { ROOT_VARIABLE, createRunFixture, openFixture, pluginSettings, removeRunFixture } from "./support/fixture.js";

const VSCODE_VERSION = "1.145.3";
function refuseWatchingWithoutTerminal() {
  if (!WATCHING) return;
  try {
    assertWatchable();
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }
}

const e2eDir = path.dirname(fileURLToPath(import.meta.url));

// The launcher makes the fixture; the workers load this file again and inherit its environment.
if (!process.env[ROOT_VARIABLE]) {
  refuseWatchingWithoutTerminal();
  createRunFixture();
}
const fixture = openFixture();

const userSettings = {
  ...pluginSettings(fixture.paths),
  // wdio-vscode-service defaults to a maximized window; a person watching wants a size that
  // sits beside a terminal.
  "window.newWindowDimensions": "default",
  "window.restoreFullscreen": false,
  // Three steps smaller than the default, as View: Zoom Out (Cmd/Ctrl -) pressed three times; it
  // fits more of the half-screen window.
  "window.zoomLevel": -3,
  // Without it a "screen reader usage detected" prompt can cover the notifications the specs read.
  "editor.accessibilitySupport": "on",
  // The chat panel opens in the secondary side bar by default; the specs have no use for it.
  "workbench.secondarySideBar.defaultVisibility": "hidden",
  "chat.disableAIFeatures": true,
};

// A session of the test VS Code on one workspace file (wdio-vscode-service's filePath, passed to VS
// Code as --file-uri, which opens a .code-workspace as a workspace), running the given specs.
function sessionOn(workspaceFile, specs) {
  return {
    browserName: "vscode",
    browserVersion: VSCODE_VERSION,
    specs,
    "wdio:vscodeOptions": {
      extensionPath: path.join(e2eDir, ".."),
      storagePath: fixture.paths.storage,
      filePath: workspaceFile,
      userSettings,
      // The service passes --disable-extensions, which isolates the run but makes VS Code show "All
      // installed extensions are temporarily disabled". The extensions dir under storagePath is
      // empty and per run, so the only extension is the one loaded by extensionPath.
      vscodeArgs: { disableExtensions: false },
      version: VSCODE_VERSION,
    },
  };
}

export const config = {
  runner: "local",
  maxInstances: 1,
  capabilities: [
    sessionOn(fixture.paths.planWorkspaceFile, ["./specs/*.e2e.js"]),
    sessionOn(fixture.paths.otherWorkspaceFile, ["./specs/other-window/*.e2e.js"]),
  ],
  services: [["vscode", { cachePath: path.join(e2eDir, ".wdio-vscode-service") }]],
  framework: "mocha",
  // In watch mode the scenarios print their own steps and results to the terminal.
  reporters: WATCHING ? [] : ["spec"],
  logLevel: process.env.E2E_LOG_LEVEL || "warn",
  // A watched test waits for Enter, so its time is the person's.
  mochaOpts: { ui: "bdd", timeout: WATCHING ? 3600000 : 120000 },
  before: resizeWindow,
  onComplete: () => removeRunFixture(fixture.paths),
};
