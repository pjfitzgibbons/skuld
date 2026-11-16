import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { eventually, staysTrue } from "../support/eventually.js";
import { useFreshFixture } from "../support/hooks.js";
import { scenario } from "../support/steps.js";
import { notificationShows, sendLink, terminalNames } from "../support/workbench.js";

const KEY = "SK-0102";

// The link `plan review` sends (lib/term.py reviewLink): its query encoded twice, since VS Code
// decodes a link's query once before the handler reads it.
function reviewLink(key, round, token) {
  const query = new URLSearchParams({ key, round: String(round), token }).toString();
  return `vscode://skuld.plan-window/review?${encodeURIComponent(query)}`;
}

// A round's request.md and request.json in the worktree, as review.py request writes them: the
// round's link and the real path the window names in the review line.
function requestRound(worktree, round) {
  const folder = path.join(worktree, ".tmp", KEY, "rounds", String(round));
  const token = crypto.randomBytes(16).toString("hex");
  fs.mkdirSync(folder, { recursive: true });
  fs.writeFileSync(path.join(folder, "request.md"), `Round ${round} of ${KEY}'s review.\n`);
  fs.writeFileSync(path.join(folder, "request.json"), JSON.stringify({ round, token }));
  return { link: reviewLink(KEY, round, token), request: fs.realpathSync(path.join(folder, "request.md")) };
}

function reviewLine({ request }) {
  return `[${KEY} review: read ${request} and do the review it describes.]`;
}

async function reviewTerminals() {
  return (await terminalNames()).filter((name) => name === `Review ${KEY}`);
}

describe("a review link", () => {
  const fixture = useFreshFixture();
  let issue;
  let round;
  let later;

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
      [`send the review link for round 1 of ${KEY}`, () => sendLink((round = requestRound(issue.worktree, 1)).link)],
      ["the window has one Review terminal for the issue", () => eventually(reviewTerminals, [`Review ${KEY}`])],
      [
        "Claude started in the worktree, given the round's line",
        () => eventually(claudeStarts, [[KEY, reviewLine(round), fs.realpathSync(issue.worktree)]]),
      ],
    ],
  });

  scenario("replaces the Review terminal of an earlier round with a fresh Claude", {
    see: [`the "Review ${KEY}" terminal closes and a new one opens, its prompt naming round 2's request.md`],
    steps: [
      [`send the review link for round 1 of ${KEY}`, () => sendLink((round = requestRound(issue.worktree, 1)).link)],
      ["Claude started for round 1", () => eventually(() => fixture.claudeStarts().length, 1)],
      [`send the review link for round 2 of ${KEY}`, () => sendLink((later = requestRound(issue.worktree, 2)).link)],
      [
        "a second Claude started, on round 2's line",
        () => eventually(() => fixture.claudeStarts().map(({ args }) => args), [reviewLine(round), reviewLine(later)]),
      ],
      ["one Review terminal is left", () => staysTrue(async () => (await reviewTerminals()).length === 1)],
    ],
  });

  scenario("refuses a link whose token isn't the round's, leaving the running review alone", {
    see: [
      `an error toast says round 1 of ${KEY} wasn't requested with this link's token, and the "Review ${KEY}" terminal stays open`,
    ],
    steps: [
      [`send the review link for round 1 of ${KEY}`, () => sendLink((round = requestRound(issue.worktree, 1)).link)],
      ["Claude started for round 1", () => eventually(() => fixture.claudeStarts().length, 1)],
      [`send a review link for round 1 of ${KEY} with another token`, () => sendLink(reviewLink(KEY, 1, "f".repeat(32)))],
      ["the error names the token", () => eventually(() => notificationShows([`wasn't requested with this link's token`]), true)],
      [
        "no second Claude starts, and the Review terminal stays",
        () => staysTrue(async () => fixture.claudeStarts().length === 1 && (await reviewTerminals()).length === 1),
      ],
    ],
  });

  scenario("refuses a round with no request in the issue's worktree, starting nothing", {
    see: [`an error toast says ${KEY} has no request.md for round 3, and no terminal opens`],
    steps: [
      [`send a review link for round 3 of ${KEY}, never requested`, () => sendLink(reviewLink(KEY, 3, "f".repeat(32)))],
      ["the error names the round", () => eventually(() => notificationShows([`no request.md for round 3`]), true)],
      ["no Claude starts", () => staysTrue(async () => fixture.claudeStarts().length === 0 && (await reviewTerminals()).length === 0)],
    ],
  });
});
