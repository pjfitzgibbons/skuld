import { expect } from "@wdio/globals";
import { eventually, staysTrue } from "../support/eventually.js";
import { useFreshFixture } from "../support/hooks.js";
import { scenario } from "../support/steps.js";
import {
  clickIssue,
  issueItems,
  issueKeys,
  notificationCount,
  notificationShows,
  sendLink,
  showFile,
  showIssuesView,
  tabsOfFile,
  terminalNames,
  windowEnvironment,
} from "../support/workbench.js";

const KEY = "SK-0101";
const TERMINALS = [`Claude ${KEY}`, `Shell ${KEY}`];

async function sortedTerminalNames() {
  return (await terminalNames()).sort();
}

describe("choosing an issue with a local ticket", () => {
  const fixture = useFreshFixture();
  let issue;

  beforeEach(async () => {
    fixture.writeIssues([KEY]);
    issue = fixture.addStoreIssue(KEY);
    await showIssuesView();
    await eventually(issueKeys, [KEY]);
  });

  const claudeArguments = () => fixture.claudeStarts().map(({ key, args }) => [key, args]);
  const ticketTabs = async () => tabsOfFile(issue.ticket);
  const ticketTabStates = async (property) => (await ticketTabs()).map((tab) => tab[property]);

  scenario("opens the ticket file in a text tab that is not a preview", {
    see: [`a tab named ${KEY}-example.md opens in the editor area with the ticket text, in normal (not italic) type`],
    steps: [
      [`click ${KEY}`, () => clickIssue(KEY)],
      ["one text tab shows the ticket file, kept open", () => eventually(() => ticketTabStates("isPreview"), [false])],
    ],
  });

  scenario("opens a shell terminal and a Claude terminal named for the issue", {
    see: [
      `a "Claude ${KEY}" terminal opens as a tab in the editor area, printing "fake Claude started" and "issue key (PLAN_KEY):    ${KEY}"`,
      `a "Shell ${KEY}" terminal opens in the panel at the bottom`,
    ],
    steps: [
      [`click ${KEY}`, () => clickIssue(KEY)],
      [
        "the window has a Claude terminal and a Shell terminal named for the key",
        () => eventually(sortedTerminalNames, TERMINALS),
      ],
    ],
  });

  scenario("starts Claude on the issue's prompt, with no --continue, when the worktree has no conversation", {
    see: [`the Claude terminal prints 'argument (the prompt):   "Work on ${KEY}."' and no --continue line`],
    steps: [
      [`click ${KEY}`, () => clickIssue(KEY)],
      [
        "the Claude that started was given the prompt only",
        () => eventually(claudeArguments, [[KEY, `[Work on ${KEY}.]`]]),
      ],
    ],
  });

  scenario("starts Claude with --continue when the worktree has a conversation", {
    see: ["the Claude terminal prints 'argument:                --continue' and no prompt line"],
    steps: [
      ["leave a conversation for the worktree in Claude's config dir", () => fixture.addConversation(issue.worktree)],
      [`click ${KEY}`, () => clickIssue(KEY)],
      ["the Claude that started was given --continue", () => eventually(claudeArguments, [[KEY, "[--continue]"]])],
    ],
  });

  scenario("starts Claude with CLAUDE_CONFIG_DIR and no other CLAUDE* variable, though the window has some", {
    see: ["the Claude terminal prints 'CLAUDE_CONFIG_DIR:' with the fixture's claude-config and 'other CLAUDE* variables: none'"],
    steps: [
      [
        "check the window itself was launched with CLAUDE_CODE_CHILD_SESSION and CLAUDE_PID set",
        async () => {
          const inherited = await windowEnvironment(["CLAUDE_CODE_CHILD_SESSION", "CLAUDE_PID"]);
          expect(inherited).toEqual({ CLAUDE_CODE_CHILD_SESSION: "1", CLAUDE_PID: "4242" });
        },
      ],
      [`click ${KEY}`, () => clickIssue(KEY)],
      [
        "the Claude that started has the fixture's CLAUDE_CONFIG_DIR and no other CLAUDE* variable",
        () =>
          eventually(
            async () =>
              fixture.claudeStarts().map(({ configDir, otherClaudeVariables }) => [configDir, otherClaudeVariables]),
            [[fixture.paths.claudeConfig, "none"]],
          ),
      ],
    ],
  });

  scenario("opens no second ticket tab and no second terminal when the issue is chosen again, and shows the ticket", {
    see: [
      "after the first click: the ticket tab, the Claude terminal and the Shell terminal",
      "a second file opens in front of the ticket tab",
      "after the second click the ticket tab is in front again, and no tab or terminal was added",
    ],
    steps: [
      [`click ${KEY}`, () => clickIssue(KEY)],
      [
        "the ticket tab and both terminals are open",
        async () => {
          await eventually(async () => (await ticketTabs()).length, 1);
          await eventually(sortedTerminalNames, TERMINALS);
        },
      ],
      [
        "show another file, so the ticket tab is no longer the active one",
        async () => {
          await showFile(fixture.addStoreIssue("SK-0102").ticket);
          await eventually(() => ticketTabStates("isActive"), [false]);
        },
      ],
      [`click ${KEY} again`, () => clickIssue(KEY)],
      [
        "the ticket tab is the active one again, still the only one",
        () => eventually(() => ticketTabStates("isActive"), [true]),
      ],
      [
        "there are still just the two terminals, and Claude started once",
        async () => {
          await staysTrue(async () => (await sortedTerminalNames()).join() === TERMINALS.join());
          expect(fixture.claudeStarts()).toHaveLength(1);
        },
      ],
    ],
  });

  scenario("runs `plan pulse <KEY> off` to clear the colour, with no warning", {
    see: ["nothing visible but the ticket and terminals opening, and no warning toast at the bottom right"],
    steps: [
      [`${KEY} is orange`, () => fixture.writePulses({ [KEY]: "orange" })],
      [`click ${KEY}`, () => clickIssue(KEY)],
      [
        "the fake plan was called once, with pulse, the key and off",
        () => eventually(async () => fixture.planCalls(), [`pulse ${KEY} off`]),
      ],
      ["no warning shows, since plan succeeded", () => staysTrue(async () => (await notificationCount()) === 0)],
    ],
  });

  scenario("does not run plan when the issue has no colour to clear", {
    see: [`${KEY} opens its ticket and terminals, with no warning, and plan is not run`],
    steps: [
      [`click ${KEY}, which has no line in the pulses file`, () => clickIssue(KEY)],
      ["both terminals open", () => eventually(sortedTerminalNames, TERMINALS)],
      ["the fake plan is never called", () => staysTrue(async () => fixture.planCalls().length === 0)],
    ],
  });

  scenario("warns that it could not clear the colour when plan fails", {
    see: [`a warning toast at the bottom right: plan window: could not clear ${KEY}'s colour … fixture plan failure`],
    steps: [
      [`${KEY} is orange`, () => fixture.writePulses({ [KEY]: "orange" })],
      ["make the fake plan fail", () => fixture.makePlanFail()],
      [`click ${KEY}`, () => clickIssue(KEY)],
      [
        "a warning names the issue and the failure",
        () => eventually(() => notificationShows([`could not clear ${KEY}'s colour`, "fixture plan failure"]), true),
      ],
    ],
  });

  scenario("leaves the item's colour when plan fails", {
    see: [`${KEY} is a solid orange warning triangle, the warning toast shows, and ${KEY} is still the solid orange warning triangle`],
    steps: [
      [
        `${KEY} is orange and the fake plan fails`,
        () => {
          fixture.writePulses({ [KEY]: "orange" });
          fixture.makePlanFail();
        },
      ],
      [`click ${KEY}`, () => clickIssue(KEY)],
      ["the warning shows", () => eventually(() => notificationShows(["could not clear"]), true)],
      [
        "the item is still the solid orange warning triangle",
        () => eventually(async () => (await issueItems()).map((item) => item.icon), ["waiting.svg"]),
      ],
    ],
  });

  scenario("removes the item but leaves its terminals open when the key leaves the issues file", {
    see: [
      `${KEY} disappears from the Issues section, as Done removes it`,
      `the "Claude ${KEY}" and "Shell ${KEY}" terminals stay open`,
    ],
    steps: [
      [`click ${KEY}`, () => clickIssue(KEY)],
      ["both terminals are open", () => eventually(sortedTerminalNames, TERMINALS)],
      [`remove ${KEY} from the issues file, as Done does`, () => fixture.writeIssues([])],
      ["the item is gone", () => eventually(issueKeys, [])],
      [
        "both terminals are still open",
        () => staysTrue(async () => (await sortedTerminalNames()).join() === TERMINALS.join()),
      ],
    ],
  });
});

describe("a vscode://skuld.plan-window/open link", () => {
  const fixture = useFreshFixture();
  let issue;

  beforeEach(async () => {
    fixture.writeIssues([KEY]);
    issue = fixture.addStoreIssue(KEY);
    await showIssuesView();
    await eventually(issueKeys, [KEY]);
  });

  scenario("does what choosing the issue does", {
    see: [`with no click: the ${KEY} ticket tab, the Claude terminal and the Shell terminal open, and no warning`],
    steps: [
      [`${KEY} is orange`, () => fixture.writePulses({ [KEY]: "orange" })],
      [
        `send the window vscode://skuld.plan-window/open?key=${KEY}`,
        () => sendLink(`vscode://skuld.plan-window/open?key=${KEY}`),
      ],
      [
        "the ticket opens, both terminals open, Claude starts and plan clears the colour",
        async () => {
          await eventually(async () => (await tabsOfFile(issue.ticket)).length, 1);
          await eventually(sortedTerminalNames, TERMINALS);
          await eventually(async () => fixture.claudeStarts().map(({ key }) => key), [KEY]);
          await eventually(async () => fixture.planCalls(), [`pulse ${KEY} off`]);
        },
      ],
      ["no warning shows", () => staysTrue(async () => (await notificationCount()) === 0)],
    ],
  });
});
