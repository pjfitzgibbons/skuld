import http from "node:http";
import { eventually, staysTrue } from "../support/eventually.js";
import { useFreshFixture } from "../support/hooks.js";
import { scenario } from "../support/steps.js";
import { clickIssue, issueKeys, openTabs, showIssuesView } from "../support/workbench.js";

const KEY = "CIAM-7";
const PAGE_TITLE = `Jira page for ${KEY}`;

// Stands in for Jira: a local page, so the suite needs no network and no login.
function startTicketServer() {
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html");
    response.end(`<title>${PAGE_TITLE}</title><h1>${KEY}</h1>`);
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

async function pageTabCount() {
  return (await openTabs()).filter((tab) => tab.label === PAGE_TITLE).length;
}

describe("choosing a Jira issue", () => {
  const fixture = useFreshFixture();
  let server;

  before(async () => {
    server = await startTicketServer();
  });

  after(() => new Promise((resolve) => server.close(resolve)));

  beforeEach(async () => {
    fixture.writeIssues([KEY]);
    fixture.addJiraIssue(KEY, `http://127.0.0.1:${server.address().port}/browse/${KEY}`);
    await showIssuesView();
    await eventually(issueKeys, [KEY]);
  });

  scenario("opens its page in the integrated browser", {
    see: [`after the click, a browser tab titled "${PAGE_TITLE}" opens in the editor area`],
    steps: [
      [`click ${KEY}`, () => clickIssue(KEY)],
      ["a browser tab shows the page", () => eventually(pageTabCount, 1)],
    ],
  });

  scenario("opens no second browser tab when the issue is chosen again", {
    see: [`the "${PAGE_TITLE}" browser tab opens once, and stays the only one after the second click`],
    steps: [
      [`click ${KEY}`, () => clickIssue(KEY)],
      ["a browser tab shows the page", () => eventually(pageTabCount, 1)],
      [`click ${KEY} again`, () => clickIssue(KEY)],
      ["there is still one browser tab for the page", () => staysTrue(async () => (await pageTabCount()) === 1, 4000)],
    ],
  });
});
