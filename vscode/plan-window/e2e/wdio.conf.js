// WebdriverIO config for the plan window's end-to-end suite: a stable VS Code, downloaded into
// .wdio-vscode-service, started with this extension loaded and no other, a user data dir and
// extensions dir under the run's fixture directory, and the plan.* settings pointing at it.
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

export const config = {
  runner: "local",
  specs: ["./specs/**/*.e2e.js"],
  maxInstances: 1,
  capabilities: [
    {
      browserName: "vscode",
      browserVersion: VSCODE_VERSION,
      "wdio:vscodeOptions": {
        extensionPath: path.join(e2eDir, ".."),
        storagePath: fixture.paths.storage,
        userSettings: {
          ...pluginSettings(fixture.paths),
          // wdio-vscode-service defaults to a maximized window; a person watching wants a size that
          // sits beside a terminal.
          "window.newWindowDimensions": "default",
          "window.restoreFullscreen": false,
          // Without it a "screen reader usage detected" prompt can cover the notifications the specs read.
          "editor.accessibilitySupport": "on",
          // The chat panel opens in the secondary side bar by default; the specs have no use for it.
          "workbench.secondarySideBar.defaultVisibility": "hidden",
          "chat.disableAIFeatures": true,
        },
        // The service passes --disable-extensions, which isolates the run but makes VS Code show "All
        // installed extensions are temporarily disabled". The extensions dir under storagePath is
        // empty and per run, so the only extension is the one loaded by extensionPath.
        vscodeArgs: { disableExtensions: false },
        version: VSCODE_VERSION,
      },
    },
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
