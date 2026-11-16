import fs from "node:fs";
import path from "node:path";
import { eventually, staysTrue } from "../support/eventually.js";
import { useFreshFixture } from "../support/hooks.js";
import { scenario } from "../support/steps.js";
import { notificationShows, sendLink, terminalNames } from "../support/workbench.js";

const KEY = "SK-0102";

// The link `plan review` sends (lib/term.py reviewLink): its query encoded twice, since VS Code
// decodes a link's query once before the handler reads it.
function reviewLink(key, request) {
  const query = new URLSearchParams({ key, request }).toString();
  return `vscode://skuld.plan-window/review?${encodeURIComponent(query)}`;
}

// A round's request.md in the worktree, as review.py writes it.
function writeRoundRequest(worktree, round) {
  const request = path.join(worktree, ".tmp", KEY, "rounds", String(round), "request.md");
  fs.mkdirSync(path.dirname(request), { recursive: true });
  fs.writeFileSync(request, `Round ${round} of ${KEY}'s review.\n`);
  return request;
}

function reviewLine(request) {
  return `[${KEY} review: read ${request} and do the review it describes.]`;
}

async function reviewTerminals() {
  return (await terminalNames()).filter((name) => name === `Review ${KEY}`);
}

describe("a review link", () => {
  const fixture = useFreshFixture();
  let issue;

  beforeEach(() => {
    fixture.writeIssues([KEY]);
    issue = fixture.addStoreIssue(KEY);
  });

  // The fake Claude's starts as [key, arguments, working directory], the directory resolved, since
  // the fixture is under /tmp, which macOS links to /private/tmp.
  const claudeStarts = () => fixture.claudeStarts().map(({ key, args, cwd }) => [key, args, fs.realpathSync(cwd)]);

  scenario("starts Claude on the round's line in a Review terminal in the issue's worktree", {
    see: [
      `a "Review ${KEY}" terminal opens as a tab in the editor area, printing "fake Claude started", the review line as its prompt and the worktree as its working directory`,
    ],
    steps: [
      [`send the review link for round 1 of ${KEY}`, () => sendLink(reviewLink(KEY, writeRoundRequest(issue.worktree, 1)))],
      ["the window has one Review terminal for the issue", () => eventually(reviewTerminals, [`Review ${KEY}`])],
      [
        "Claude started in the worktree, given the round's line",
        () => eventually(claudeStarts, [[KEY, reviewLine(writeRoundRequest(issue.worktree, 1)), fs.realpathSync(issue.worktree)]]),
      ],
    ],
  });

  scenario("replaces the Review terminal of an earlier round with a fresh Claude", {
    see: [`the "Review ${KEY}" terminal closes and a new one opens, its prompt naming round 2's request.md`],
    steps: [
      [`send the review link for round 1 of ${KEY}`, () => sendLink(reviewLink(KEY, writeRoundRequest(issue.worktree, 1)))],
      ["Claude started for round 1", () => eventually(() => fixture.claudeStarts().length, 1)],
      [`send the review link for round 2 of ${KEY}`, () => sendLink(reviewLink(KEY, writeRoundRequest(issue.worktree, 2)))],
      [
        "a second Claude started, on round 2's line",
        () => eventually(() => fixture.claudeStarts().map(({ args }) => args), [
          reviewLine(writeRoundRequest(issue.worktree, 1)),
          reviewLine(writeRoundRequest(issue.worktree, 2)),
        ]),
      ],
      ["one Review terminal is left", () => staysTrue(async () => (await reviewTerminals()).length === 1)],
    ],
  });

  scenario("refuses a request outside the issue's worktree, starting nothing", {
    see: ["an error toast says the request isn't in the issue's worktree, and no terminal opens"],
    steps: [
      [`send a review link for ${KEY} naming another folder's request.md`, () => sendLink(reviewLink(KEY, "/elsewhere/request.md"))],
      ["the error names the worktree", () => eventually(() => notificationShows([`isn't in ${KEY}'s worktree`]), true)],
      ["no Claude starts", () => staysTrue(async () => fixture.claudeStarts().length === 0 && (await reviewTerminals()).length === 0)],
    ],
  });
});
