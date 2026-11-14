// Waits until what `read` returns equals `expected`, and fails saying what it last returned. The
// window and the fake commands answer a moment after the fixture changes, so a spec waits for the
// state it expects rather than sleeping.
import { isDeepStrictEqual, inspect } from "node:util";
import { browser } from "@wdio/globals";
import { showComparison } from "./steps.js";

const TIMEOUT_MS = 15000;
const INTERVAL_MS = 250;

// A value with strings quoted and each element of an array or object on its own line, so the
// key, prompt and variables a Claude was started with read one per line in the watch output.
function shown(value) {
  return inspect(value, { compact: false, breakLength: 0, depth: 4 });
}

export async function eventually(read, expected) {
  let seen;
  const arrived = async () => {
    seen = await read();
    return isDeepStrictEqual(seen, expected);
  };
  try {
    await browser.waitUntil(arrived, { timeout: TIMEOUT_MS, interval: INTERVAL_MS });
  } catch {
    showComparison(shown(expected), shown(seen), false);
    throw new Error(`expected ${shown(expected)}, last saw ${shown(seen)}`);
  }
  showComparison(shown(expected), shown(seen), true);
}

// Stays true for `ms`: for "nothing more happens", which is only believable after a wait.
export async function staysTrue(read, ms = 2000) {
  const end = Date.now() + ms;
  do {
    if (!(await read())) throw new Error("stopped holding");
    await browser.pause(250);
  } while (Date.now() < end);
}
