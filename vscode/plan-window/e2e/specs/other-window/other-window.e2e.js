// A window on any workspace but the plan workspace: the extension is loaded there and does nothing.
import { expect } from "@wdio/globals";
import { staysTrue, eventually } from "../../support/eventually.js";
import { useFreshFixture } from "../../support/hooks.js";
import { scenario } from "../../support/steps.js";
import {
  issuesViewIsShowing,
  notificationShows,
  openTabs,
  sendLink,
  terminalNames,
} from "../../support/workbench.js";

const KEY = "SK-0101";

describe("a window that is not the plan window", () => {
  const fixture = useFreshFixture();

  beforeEach(() => {
    fixture.writeIssues([KEY]);
    fixture.addStoreIssue(KEY);
  });

  scenario("has no Issues view", {
    see: ["the Explorer has no Issues section, though the issues file lists " + KEY],
    steps: [["look for the Issues section", async () => expect(await issuesViewIsShowing()).toBe(false)]],
  });

  scenario("ignores an /open link, and says the link is for the plan window", {
    see: [
      "a notification says the link is for the plan window",
      "no terminal and no ticket tab opens, and plan is not run",
    ],
    steps: [
      [`send the window vscode://skuld.plan-window/open?key=${KEY}`, () => sendLink(`vscode://skuld.plan-window/open?key=${KEY}`)],
      [
        "the notification shows",
        () => eventually(() => notificationShows(["this link is for the plan window"]), true),
      ],
      [
        "nothing else changes",
        () =>
          staysTrue(
            async () =>
              (await terminalNames()).length === 0 &&
              (await openTabs()).length === 0 &&
              fixture.claudeStarts().length === 0 &&
              fixture.planCalls().length === 0,
          ),
      ],
    ],
  });
});
