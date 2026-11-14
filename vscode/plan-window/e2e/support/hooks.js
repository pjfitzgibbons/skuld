// What every spec shares: a fresh fixture before each test and a bare window after it.
import { openFixture } from "./fixture.js";
import { closeEverything } from "./workbench.js";

export function useFreshFixture() {
  const fixture = openFixture();
  beforeEach(() => fixture.reset());
  afterEach(closeEverything);
  return fixture;
}
