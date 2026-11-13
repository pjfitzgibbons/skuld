// Waits until what `read` returns equals `expected`, and fails saying what it last returned. The
// window and the fake commands answer a moment after the fixture changes, so a spec waits for the
// state it expects rather than sleeping.
import { isDeepStrictEqual, inspect } from "node:util";
import { browser } from "@wdio/globals";

const TIMEOUT_MS = 15000;
const INTERVAL_MS = 250;

export async function eventually(read, expected) {
  let seen;
  const arrived = async () => {
    seen = await read();
    return isDeepStrictEqual(seen, expected);
  };
  try {
    await browser.waitUntil(arrived, { timeout: TIMEOUT_MS, interval: INTERVAL_MS });
  } catch {
    throw new Error(`expected ${inspect(expected)}, last saw ${inspect(seen)}`);
  }
}

// Stays true for `ms`: for "nothing more happens", which is only believable after a wait.
export async function staysTrue(read, ms = 2000) {
  const end = Date.now() + ms;
  do {
    if (!(await read())) throw new Error("stopped holding");
    await browser.pause(250);
  } while (Date.now() < end);
}
