# jira-plan — repo instructions

**Jira Plan** plans work in Jira Cloud (project CIAM, https://cryoport.atlassian.net — see
GUIDE.md), with a VS Code front end (`plan/`, opening one VS Code window per issue on its own macOS
Desktop) that starts, ships and finishes each issue. It does not use GitHub Issues; pull requests
are its one GitHub integration. Nothing is inherited from a parent directory's `CLAUDE.md` or
settings: what this repo needs is here or in `.claude/`.

## Read before

- Read `docs/architecture.md` before moving a responsibility between modules, and §10 before any
  change to how the tool writes to git, GitHub or Jira.
- Read `GUIDE.md` before changing a behaviour it documents as decided.
- Read `docs/requirements.md` before adding a button or script, or changing what one does.
- Read `docs/failure-modes.md` before writing a command that writes to git, Jira, GitHub or a
  file another window reads, and before fixing a review finding in one.
- Read `docs/agents/technical-writer.md` before writing an issue or design doc.
- Read Jira's `Next up` JQL before answering "what's next":
  `jira-get '/rest/api/3/search/jql?jql=...'` (the exact query is in `plan`'s config), or `plan`'s
  Next up list.

## Commands

- **Tests:** `uv run pytest`. It also runs `.claude/hooks/`, and fails under 90% coverage.
- **The terminal frontend:** `python3 -m unittest discover -s lib -t .`.
- **Review findings:** fix only the silent ones, failures that leave no trace and lose work, files,
  commits or the tracker's state. A failure that shows the user something is wrong is already
  mitigated.
- **Reviewing a PR:** how dispositions, re-review and verbatim-fix resolution work is GUIDE.md §5,
  "Review: co-signing and dispositions."
- **Review:** Claude reviews each PR in the issue window's own Claude. `pr` starts a round whenever a push adds commits (GUIDE.md §5, "Claude's review").
- **Browser checks:** a test script in Chrome or Chromium, run with the suite. The
  `chrome-devtools` MCP server (Chrome, `--isolated`) is for investigating an issue, never for
  checking a change, and never drives an issue window's integrated browser.
- **Python:** `python3` on PATH. `/usr/bin/python3` is 3.9 and can't run the skill scripts.
- **An issue's work:** Start, Ship and Done, run from `plan`'s Next up list.
- **An issue's body:** `jira-get /rest/api/3/issue/<KEY>`, or `?expand=changelog` for its history.
- **A PR:** Ship, or `pr <KEY>`, from `.tmp/<KEY>/commit.txt` and `.tmp/<KEY>/pr-body.md`.

## Done differently

- **Issues are Jira's**, `<KEY>` (e.g. `CIAM-123`) at https://cryoport.atlassian.net, never GitHub
  Issues or files in the repo.
- **Branches** are `<type>/<KEY>-<slug>`: `feat/`, `fix/` or `chore/`. Short-lived, off the default
  branch, squash-merged. Nothing reaches the default branch except by PR.
- **Review replies and Done ticks are files** that `pr` sends: a reply to a review thread goes in
  `.tmp/<KEY>/replies/<comment id>.md`, and the issue's description with boxes ticked in
  `.tmp/<KEY>/issue.md` (the jira-issues skill).
- **A PR title ends `(<KEY>)`**, e.g. `Add X (CIAM-123)`. The title becomes the squash commit's
  subject, and Jira's dev panel links the PR to the issue automatically, from the branch name and
  the PR title both containing the key — no Jira field is ever written for this.
- **An AI is never an author** (the Linux kernel's
  [coding-assistants](https://docs.kernel.org/process/coding-assistants.html) rule). No
  `Co-Authored-By`, `Signed-off-by`, `Claude-Session` or "Generated with" line names an AI, in a
  commit, a PR description or a document. Every commit Claude makes ends with
  `Assisted-by: Claude:<model>`; PR descriptions carry no attribution.
- **No code path transitions a Jira issue to a terminal status** (Done, Closed, Cancelled,
  Resolved, ...). Done posts an ADF comment and prints "Close `<KEY>` in Jira" with the browse
  URL; Peter makes that move himself, in the Jira UI. `.claude/hooks/no_terminal_jira_transition.py`
  enforces this.

## Boundaries

- **Claude stages the process; the human verifies and executes it.** Every commit, push, `gh` write and Jira write happens on Start,
  Ship or Done, or a `pr` the human runs. Write `.tmp/<KEY>/commit.txt`, `pr-body.md`, `replies/` and
  `issue.md` for review; reads, inspections, the test suite and cleaning up your own mess
  you run yourself.
- **An agent commits only on its own issue branch,** and only when asked.
- **Never commit secrets.** The tool authenticates to GitHub by shelling out to `gh`, and to Jira
  via `~/.netrc`; do not add a library that needs a token passed another way.
- **Never use real personal data** in fixtures, tests or examples.
- **No speculative files.** No directory or file until something goes in it.
- **Agree a doc change in chat first**, then edit in place on the branch.
- **Rules** ([architecture §10](docs/architecture.md#invariants)); these two break most easily:
  - **2.** The default branch carries the code, and everything reaches it by PR. Status lives in
    the issue's tracker.
  - **3.** Every outside system is reached through its port's adapter; every Jira call goes
    through `lib/jira.py`.

## Gotchas

- **The attribution hook** (`.claude/hooks/no_ai_attribution.py`) reads the command text: the
  `Assisted-by:` trailer must be literal in the `git commit` command, not in a variable. It
  applies to throwaway test repos too.
- **The terminal-transition hook** (`.claude/hooks/no_terminal_jira_transition.py`) blocks any Bash
  command that looks like it moves a Jira issue to a terminal status. It is a false-positive-prone
  substring match by design: if it blocks a genuinely read-only command, don't work around it —
  report it and ask.
- **The page-open hook** (`.claude/hooks/no_bare_open.py`) blocks `open` on a URL or an `.html`
  file. Open a page with `plan browse <KEY | pr#N | url> [KEY]`, or print the URL for Peter.
- **`gh pr create --fill`** copies the commit's `Assisted-by:` trailer into the PR body. Use `gh pc`.
- **zsh globs `[]`:** quote it, `-f 'labels[]=bug'`.
- **~/.netrc holds the Jira token.** Never `cat`, `Read` or print it; `Read(./**/.netrc)` is denied
  in settings.json and curl/`jira-get` read it directly.
- **GitHub's docs and GitHub's behaviour differ.** Verify against the API, and give a claim about
  it a source or mark it 🔍 unverified.
