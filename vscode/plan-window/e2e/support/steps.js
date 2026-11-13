// A test as a person can follow it. `scenario` declares a test by what a person should see and the
// steps that run, each a description and the code that carries it out, so the description printed
// before the test is the list the test runs.
//
// With E2E_WATCH=1 each scenario prints its steps and what to look for, waits for Enter, runs
// with a pause after each step, and prints PASS or FAIL. Without it a scenario is a plain test.
//
// The worker's stdin is not the terminal, so watch mode writes to and reads Enter from /dev/tty.
import fs from "node:fs";
import tty from "node:tty";
import { browser } from "@wdio/globals";

export const WATCHING = process.env.E2E_WATCH === "1";
const PAUSE_MS = Number(process.env.E2E_WATCH_PAUSE_MS || 1000);
const LAST_STEP_PAUSE_FACTOR = 3;

const BOLD = "\x1b[1m";
const GREEN = "\x1b[32m";
const RED = "\x1b[31m";
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";

// Opens the terminal, or returns null when the process has none (CI, a detached run).
function openTerminal() {
  try {
    return fs.openSync("/dev/tty", "r+");
  } catch {
    return null;
  }
}

export function assertWatchable() {
  const fd = openTerminal();
  if (fd === null) {
    throw new Error(
      "E2E_WATCH=1 needs a terminal to show the steps and read Enter from, and /dev/tty cannot be opened here",
    );
  }
  fs.closeSync(fd);
}

let terminalFd = null;

function show(text) {
  terminalFd ??= openTerminal();
  fs.writeSync(terminalFd, `${text}\n`);
}

let terminalInput = null;

// One reader for the run: a second stream on the same terminal would compete with the first for it.
function terminal() {
  if (!terminalInput) {
    terminalInput = new tty.ReadStream(terminalFd);
    terminalInput.unref();
  }
  return terminalInput;
}

function waitForEnter() {
  const input = terminal();
  return new Promise((resolve) => {
    const onData = (chunk) => {
      if (!chunk.toString().includes("\n")) return;
      input.off("data", onData);
      input.pause();
      resolve();
    };
    input.on("data", onData);
    input.resume();
  });
}

function describeScenario(title, steps, see) {
  const numbered = steps.map(([description], index) => `  ${index + 1}. ${description}`);
  const looks = see.map((line) => `  - ${line}`);
  return [`\n${BOLD}=== ${title}${RESET}`, "Steps:", ...numbered, "You should see:", ...looks].join("\n");
}

async function introduce(title, steps, see) {
  show(describeScenario(title, steps, see));
  show(`${BOLD}Press <Enter> to run this test…${RESET}`);
  await waitForEnter();
}

async function runSteps(steps) {
  for (const [index, [description, action]] of steps.entries()) {
    show(`${DIM}  > ${index + 1}. ${description}${RESET}`);
    await action();
    const isLast = index === steps.length - 1;
    await browser.pause(isLast ? PAUSE_MS * LAST_STEP_PAUSE_FACTOR : PAUSE_MS);
  }
}

async function runWatched(title, steps, see) {
  await introduce(title, steps, see);
  try {
    await runSteps(steps);
    show(`${GREEN}${BOLD}PASS${RESET}`);
  } catch (error) {
    show(`${RED}${BOLD}FAIL: ${error.message.split("\n")[0]}${RESET}`);
    throw error;
  }
}

async function runPlain(steps) {
  for (const [, action] of steps) await action();
}

// One test. `steps` is [[description, action], ...] run in order; `see` is what a person watching
// should be able to see happen, in the window or in the terminal.
export function scenario(title, { steps, see }) {
  it(title, () => (WATCHING ? runWatched(title, steps, see) : runPlain(steps)));
}
