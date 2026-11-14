import { expect } from "@wdio/globals";
import { eventually, staysTrue } from "../support/eventually.js";
import { useFreshFixture } from "../support/hooks.js";
import { scenario } from "../support/steps.js";
import {
  halfScreenSize,
  issueItems,
  issueKeys,
  notificationCount,
  notificationsInCenter,
  secondarySideBarIsShowing,
  showIssuesView,
  themeColor,
  windowSize,
} from "../support/workbench.js";

async function itemOf(key) {
  return (await issueItems()).find((item) => item.key === key);
}

async function iconOf(key) {
  return (await itemOf(key))?.icon;
}

describe("the test window", () => {
  scenario("shows no notification at startup, and no chat panel", {
    see: [
      "no notification toast, and the bell at the bottom right has nothing in it",
      "no chat panel on the right: the window is just the Explorer, the editor area and the status bar",
    ],
    steps: [
      [
        "wait through the first moments of the window",
        () => staysTrue(async () => (await notificationCount()) === 0, 4000),
      ],
      ["open the notification center", async () => expect(await notificationsInCenter()).toBe(0)],
      [
        "check the secondary side bar, where chat opens, is closed",
        async () => expect(await secondarySideBarIsShowing()).toBe(false),
      ],
    ],
  });

  scenario("fills the left half of the screen, not maximized or full screen", {
    see: [
      "the window takes the left half of the screen, full height, leaving the right half free",
    ],
    steps: [["read the window's size", async () => expect(await windowSize()).toEqual(await halfScreenSize())]],
  });
});

describe("the Issues view", () => {
  const fixture = useFreshFixture();

  beforeEach(showIssuesView);

  scenario("lists exactly the keys in the issues file", {
    see: ["in the Explorer's Issues section, three items appear: SK-0101, SK-0102 and CIAM-7, in that order"],
    steps: [
      [
        "write SK-0101, SK-0102 and CIAM-7 into the issues file",
        () => fixture.writeIssues(["SK-0101", "SK-0102", "CIAM-7"]),
      ],
      ["the view lists those three keys, in file order", () => eventually(issueKeys, ["SK-0101", "SK-0102", "CIAM-7"])],
    ],
  });

  scenario("shows a key added to the issues file without a reload", {
    see: ["Issues starts with SK-0101 only, then SK-0102 appears beneath it, with no reload"],
    steps: [
      ["write SK-0101 into the issues file", () => fixture.writeIssues(["SK-0101"])],
      ["the view lists SK-0101", () => eventually(issueKeys, ["SK-0101"])],
      ["add SK-0102 to the issues file", () => fixture.writeIssues(["SK-0101", "SK-0102"])],
      ["the view lists SK-0102 too", () => eventually(issueKeys, ["SK-0101", "SK-0102"])],
    ],
  });

  scenario("drops a key removed from the issues file without a reload", {
    see: ["Issues starts with SK-0101 and SK-0102, then SK-0101 disappears, with no reload"],
    steps: [
      ["write SK-0101 and SK-0102 into the issues file", () => fixture.writeIssues(["SK-0101", "SK-0102"])],
      ["the view lists both", () => eventually(issueKeys, ["SK-0101", "SK-0102"])],
      ["remove SK-0101 from the issues file", () => fixture.writeIssues(["SK-0102"])],
      ["the view lists only SK-0102", () => eventually(issueKeys, ["SK-0102"])],
    ],
  });

  scenario("shows a solid orange warning triangle for an orange pulse, a green tick for a green one, and a plain circle for none", {
    see: ["SK-0101 gets a solid orange warning triangle icon", "SK-0102 gets a green tick icon", "SK-0103 gets a plain grey circle"],
    steps: [
      [
        "write three issues, SK-0101 orange and SK-0102 green",
        () => {
          fixture.writeIssues(["SK-0101", "SK-0102", "SK-0103"]);
          fixture.writePulses({ "SK-0101": "orange", "SK-0102": "green" });
        },
      ],
      [
        "the three items show the warning triangle, the tick and the circle",
        () =>
          eventually(
            () => issueItems().then((items) => items.map(({ key, icon }) => [key, icon])),
            [
              ["SK-0101", "waiting.svg"],
              ["SK-0102", "codicon-pass-filled"],
              ["SK-0103", "codicon-circle-outline"],
            ],
          ),
      ],
      [
        "the triangle is its own orange, so has no theme colour; the tick is the theme's green; the circle has no colour",
        async () => {
          expect((await itemOf("SK-0101")).color).toBe("");
          expect((await itemOf("SK-0102")).color).toBe(await themeColor("charts.green"));
          expect((await itemOf("SK-0103")).color).toBe("");
        },
      ],
    ],
  });

  scenario("changes an item's colour when the pulses file changes", {
    see: ["SK-0101 starts as a solid orange warning triangle, turns into a green tick, then into a plain circle"],
    steps: [
      [
        "write SK-0101 into the issues file, orange in the pulses file",
        () => {
          fixture.writeIssues(["SK-0101"]);
          fixture.writePulses({ "SK-0101": "orange" });
        },
      ],
      ["the item is the solid orange warning triangle", () => eventually(() => iconOf("SK-0101"), "waiting.svg")],
      ["the pulses file now says SK-0101 is green", () => fixture.writePulses({ "SK-0101": "green" })],
      ["the item turns into the green tick", () => eventually(() => iconOf("SK-0101"), "codicon-pass-filled")],
      ["the pulses file no longer lists SK-0101", () => fixture.writePulses({})],
      ["the item turns into the plain circle", () => eventually(() => iconOf("SK-0101"), "codicon-circle-outline")],
    ],
  });
});
