# Design

## Contents

- [1. Context and scope](#context)
  - [1.1 What is not yet decided](#undecided)
- [2. Goals and non-goals](#goals)
- [3. Design](#design)
  - [3.1 The core and its adapters](#core)
  - [3.2 The Redmine plugins](#redmine-plugins)
  - [3.3 The VS Code extension](#extension)
  - [3.4 The CLIs](#clis)
  - [3.5 Naming](#naming)
  - [3.6 Operations](#operations)
- [4. Alternatives considered](#alternatives)
- [5. Cross-cutting concerns](#lenses)

<a id="context"></a>

## 1. Context and scope

The architecture says what skuld is ([architecture §1](architecture.next.md#purpose)): a core in skuld's Redmine plugin, VS Code as the client through the `plan-window` extension, and `plan` and `pr` as thin CLIs. ADR 0005 records why ([ADR 0005 §3.7](adr/0005-redmine-is-the-tracker.md#runtime)). This document holds the design under that architecture, one section per component, then the naming rules and each operation as the components carry it out.

It covers what skuld builds on the home Mac against Redmine. The work computer's tracker is outside it, as it is outside the architecture.

<a id="undecided"></a>

### 1.1 What is not yet decided

Nothing in this design is undecided. An item that doesn't yet fit the architecture's decisions is marked UNSETTLED, and one that nothing decides yet is marked OPEN. Each sits in the section it belongs to and is listed here.

<a id="goals"></a>

## 2. Goals and non-goals

Goals:

- Every design detail has one home here, so the architecture states what skuld is without it.
- Every trigger starts on the Mac and calls the core, and the core never calls the Mac ([architecture §6.2](architecture.next.md#process-boundary)).
- The design holds when Redmine is hosted. Nothing in it depends on the instance's loopback binding ([ADR 0005 §3.3](adr/0005-redmine-is-the-tracker.md#credentials)).

Non-goals:

- The work computer's tracker, its adapters and its routing.
- The ticket store and its tools, whose rules are ADR 0005's ([§3.11](adr/0005-redmine-is-the-tracker.md#adr-0001)).
- Each command's failure table, which is [failure-modes.md](failure-modes.md)'s.
- The interim-end suite, which is ADR 0005's ([§3.9](adr/0005-redmine-is-the-tracker.md#interim-end)).

<a id="design"></a>

## 3. Design

<a id="core"></a>

### 3.1 The core and its adapters

The core's ports and adapters are in [architecture §6.1](architecture.next.md#front-ends), and the users and keys its callers act with are in [architecture §3.1](architecture.next.md#credentials). The design adds how they are built.

Each port is a module interface with one class per adapter. The `Redmine` and `review_findings` adapters work on Redmine's plugin API. The GitHub adapter works over GitHub's API with the token the plugin reads from the host's secret store. Its operations are to find a PR by its head branch, open it or replace its body, read its state and threads, and post a reply and resolve its thread.

`storeFor(cfg, key)` picks the `IssueStore` adapter from the key's prefix. A key whose prefix doesn't match its issue's project is refused before anything is written.

Git is not one of the core's ports. Fetching, rebasing and adding or removing a worktree belong to the extension's git and worktree module ([§3.3.4](#git-module)), and the round's commit and push belong to `pr` ([§3.4](#clis)).

<a id="redmine-plugins"></a>

### 3.2 The Redmine plugins

The instance is Redmine 7 in Docker, on SQLite in a named volume. On the home Mac it is bound to `127.0.0.1`, on port 3010 unless `REDMINE_PORT` sets another ([ADR 0005 §3.3](adr/0005-redmine-is-the-tracker.md#credentials)). A nightly backup of the database, with an integrity check, and a nightly export of every issue as JSON, journal included and unencrypted, are written to a USB drive. Skuld's main checkout holds the instance's setup, and a person starts, stops, backs up and upgrades it from there.

Redmine's own data model carries most of the plan ([architecture §4.1](architecture.next.md#members)). The design adds the fields skuld relies on:

- The key prefix is a project custom field that skuld adds.
- A milestone's status is `open`, `locked` or `closed`.
- Issue custom fields hold what Redmine lacks: `Points`, `Lane`, the baseline dates and `Ordinal`. `Old ID` holds an imported ticket's store key.
- An issue above 5 points is split before it is scheduled. A reality check lists any that are not, and nothing refuses the save.

The plugins are listed in [architecture §6.4](architecture.next.md#redmine-plugins). In skuld's own plugin, the checklist of Done boxes is a checklist model with a CRUD API. RedmineUP Checklists' design is a reference for it, and nothing of RedmineUP's is installed. The plugin reads the GitHub token for the `PullRequests` adapter from the host's secret store, and serves the pages ([§3.6.7](#pages)).

`review_findings` keeps a round's findings and outcomes, and its files, in one table that any `SELECT` can answer.

The key plugin reads a key in text and in commits, and renders it as a link to its issue. It changes how Redmine behaves, so it is checked again on each Redmine upgrade.

<a id="extension"></a>

### 3.3 The VS Code extension

`plan-window` is TypeScript. Every VS Code window loads it, and it acts only in the plan window. It holds the bridge and the link handlers, the plan window's behaviour, the Issues view, the git and worktree module, the local files and the review terminal. Session status is reported by Claude Code's hooks and kept by the core ([§3.3.6](#session-status)).

<a id="bridge"></a>

#### 3.3.1 The bridge

The bridge is the extension's client of the core. It carries a trigger from VS Code, or from a `vscode://` link into the extension, to the core across the authenticated network boundary, and carries the core's answer back. It holds no flow of its own: a check or a refusal is the core's.

An issue page's Start, Open and Done links are `vscode://` links into the extension ([architecture §6.2](architecture.next.md#process-boundary)). Each handler is a thin wrapper over the git and worktree module, which does the work on the Mac and calls the core through the bridge. `plan`'s Start, Open and Done open the same links with `code --open-url`, so every Start, Open and Done goes through the handlers, and VS Code must be running for them.

For Start and Done the handler calls the core first, and the core can refuse. The module does its work on the Mac only after the core accepts, so a refusal leaves the Mac untouched.

<a id="plan-window"></a>

#### 3.3.2 The plan window

The plan window is the one VS Code window on the plan workspace file, `<workspaceDir>/plan.code-workspace`. `workspaceDir` is configured, and its default is `~/.cache/plan/code-workspaces`. The plan workspace file lists as folders the worktrees of the issues in the issues file. ADR 0004 records the choice of one window ([ADR 0004 §3](adr/0004-one-plan-window.md#decision)).

The plan workspace's first folder is skuld's main checkout. VS Code names the workspace by its first folder, and Done never removes it.

Only the plan window acts. The window on the plan workspace file sets the context key `plan.isPlanWindow`, and the Issues view's `when` clause shows the view only where the key is true. Any other window has no Issues view, no click command and no file watcher. A `/open` or `/browse` link that reaches it shows "this link is for the plan window." and does nothing else.

The plan workspace's settings tell the extension where things are: `plan.issuesFile` and `plan.workspaceDir`, and `workbench.browser.openLocalhostLinks`, which opens a localhost link clicked in one of the window's terminals in its integrated browser.

The issue workspace file, `<workspaceDir>/<KEY>.code-workspace`, is the issue's record of its key, worktree, prompt and page: `plan.key`, `plan.worktree`, `plan.prompt` and `plan.ticketUrl`, the issue page, and `plan.prUrl`, the issue's PR, which Done writes before it closes the issue's windows. It lives outside every repo. Start rewrites it each time, and the extension reads it to open the issue.

VS Code places the plan window. `code <plan workspace>` opens the plan workspace in a window, or raises the window already showing it, wherever that window is. A window is known by its workspace file's path, so a second plan window is never opened. A plan window closed by hand opens again with every issue's folder on the next Start or Open, and opening an issue that has no terminal there starts one. A plan window on another display is raised where it is.

Opening an issue brings up its Claude. A click on the issue's item in the Issues view, or the link `vscode://skuld.plan-window/open?key=<KEY>`, opens the issue, and the steps run in this order:

1. The issue page opens in the integrated browser, in the first editor group, every time. A tab already showing it comes to the front, and a second one is never opened.
2. The shell beside Claude opens, as the terminal `Shell <KEY>`.
3. The terminal `Claude <KEY>`, in the second editor group, is focused when it exists. Otherwise the extension creates it in the worktree and starts Claude in it: `claude --continue` when the worktree has an earlier Claude session, and `claude "$PLAN_PROMPT"`, a prompt naming the issue's key, when it has none.

Claude comes after the page and the shell so that focus ends on it. Both terminals get `PLAN_KEY` in their environment, so a command run in either knows its issue. Both unset every `CLAUDE*` session variable the window inherited. A window opened by `code` from inside a Claude session carries that session's `CLAUDE_CODE_CHILD_SESSION` and `CLAUDE_PID`, and a Claude started with them never registers, so `claude agents --json` wouldn't list it. `CLAUDE_CONFIG_DIR` is kept: it says where Claude keeps its config and conversations, and the check for an earlier session reads it.

The window closes an issue's terminals and tabs only on two links, `vscode://skuld.plan-window/done?key=<KEY>` and `vscode://skuld.plan-window/done-tab?key=<KEY>`, which Done sends with `code --open-url` ([§3.6.5](#done)). Each carries the key only, and the handler acts only in the plan window. `/done` closes the issue's terminals and the PR's tab, and `/done-tab` closes the issue's own tab. The handler finds the terminals by their `<Kind> <KEY>` names and closes `Shell <KEY>`, `Claude <KEY>` and `Review <KEY>`, never one whose name merely resembles them. For each Claude terminal it sends Ctrl-C twice, then waits up to 10 seconds for the terminal's shell to have no child process, and removes the terminal once it has none. It never kills one: a terminal still busy after the wait stays open and the handler says so.

The PR's tab is found by the PR's URL, which Done writes into the issue workspace file as `plan.prUrl` before it sends `/done`, since the link carries no URL. The issue's tab is found by the ticket path or the Jira page in the same file: the ticket's editor tab for a store ticket, the Jira page's integrated-browser tab for a Jira issue. A terminal or tab already gone is skipped without an error.

The handler reports what it did to Done in `<workspaceDir>/<KEY>.done-result.json`, one line per step, each starting `ran:`, `skipped:` or `failed:`. Done removes any old result before it sends a link and waits up to 60 seconds for the new one. A window that stays silent for that long counts as unreachable, as does a failed `code --open-url`.

Pages open in the plan window's integrated browser, through `workbench.action.browser.open`, and never in the default browser ([§3.6.7](#pages)). A page link is the `/browse` `vscode://` link, which the extension opens at the page's Redmine URL wherever Redmine runs, so a page never depends on `workbench.browser.openLocalhostLinks`.

<a id="issues-view"></a>

#### 3.3.3 The Issues view

The Issues view is `plan.issues`, contributed under `contributes.views.explorer`, so it shows in the plan window's Explorer. It has one item per key in the issues file, in file order, labelled with the key. It watches the issues file, reads each issue's tint from the core, and shows the item orange or green, or untinted ([§3.3.6](#session-status)). A click on an item opens the issue ([§3.3.2](#plan-window)).

<a id="git-module"></a>

#### 3.3.4 The git and worktree module

The module does every git operation on the Mac: fetch, push, rebase onto the default branch, make and delete a branch, add and remove a worktree, and read HEAD. It resolves which repo holds an issue's work ([§3.6.1](#repo-resolution)), from `checkouts` in the machine config, `~/.config/skuld/config.json`, which maps each configured repo to its main checkout on this Mac.

A lock file per issue keeps two Starts, or two Dones, of one issue apart. Within a session the second joins the one under way; across sessions it is refused ([failure-modes.md, *Start*](failure-modes.md#start)).

The round's commit and push are `pr`'s ([§3.4](#clis)).

<a id="local-files"></a>

#### 3.3.5 The local files

The extension writes the local files:

| File | Default | Holds |
| --- | --- | --- |
| Issues file, `plan.issuesFile` | `~/.cache/plan/issues` | One `<KEY>` per line, no key twice: the issues Started or Opened and not yet Done |
| Plan workspace file | `<workspaceDir>/plan.code-workspace` | The worktrees of the issues in the issues file, and the settings in [§3.3.2](#plan-window) |
| Issue workspace file | `<workspaceDir>/<KEY>.code-workspace` | One issue's key, worktree, prompt and page |

Start and Open add an issue to the issues file, and Done removes it. A key stays until Done, whether or not the plan window is open. The Issues view reads the issues file; Next up does not.

Each change reads the file, changes it and writes it back under an exclusive lock, so two writers never interleave. Each write goes through its own uniquely named temp file and a rename, so a reader never sees a half-written file. Every writer takes the lock.

<a id="session-status"></a>

#### 3.3.6 Session status

Session status is each Claude session's state, and the core keeps it ([architecture §6.2](architecture.next.md#process-boundary)). Claude Code's hooks, registered by `bin/install-agents`, report it. Each hook makes one call to skuld's Redmine plugin with the person's key, carrying the session's id, its working folder and its state:

| Hook event | State |
| --- | --- |
| A Notification that holds the session for a person (a permission prompt, a question, a wait for input), the same one that shows the system notification | waiting |
| The session's next prompt or tool call | working |
| Stop | idle |
| The session's end | ended |

The hook prints nothing and always exits 0, so a failed call never stops a session.

The core places each session by its working folder. A folder in `<repo>.worktrees/<KEY>` gives the key, matched with `ISSUE_KEY_PATTERN` ([§3.5](#naming)), and the session counts for that issue when the issue exists in Redmine. The local issues file plays no part. A session it can't place shows on `-`.

The core gives each issue its tint:

| Tint | While |
| --- | --- |
| Orange | One of the issue's sessions is waiting |
| Green | The issue has a draft round in `review_findings` waiting for `pr`: the first authorship's round, with its commit message and PR body, or a review-resolve round |
| None | Neither holds |

Orange outranks green. The Issues view shows the tint ([§3.3.3](#issues-view)), and `plan agents` reads the same status ([§3.4](#clis)).

<a id="review-terminal"></a>

#### 3.3.7 The review terminal

Claude's review of an issue's PR runs in its own terminal. `plan review <KEY>` sends the link `vscode://skuld.plan-window/review?key=<KEY>&round=<n>&token=<token>`. The round is a number, and the token is the one `review.py request` stored in that round's `request.json`. The link carries no path and no prompt.

The extension reads the issue workspace file and builds the request's path itself, `<worktree>/.tmp/<KEY>/rounds/<n>/request.md`, under the worktree's real path. It refuses the link unless:

- that file exists at exactly that path, with no symbolic link on the way;
- the round's `request.json` names the round and carries the link's token;
- the reviewer that `review.py claim` recorded in `request.json` has no live pid, so a replayed link can't close a reviewer at work. `review.py` likewise refuses to reopen such a round.

A refused link shows the refusal and leaves every terminal as it is. Otherwise the window closes any `Review <KEY>` terminal and creates a new one in the worktree. It starts Claude there the way the issue's Claude terminal does, with `PLAN_KEY` set and the inherited `CLAUDE*` session variables unset, on a prompt that names the request by its real path and is submitted at once. A reopened round keeps its token, so its link carries the stored one.

Each round gets a fresh conversation, apart from the author's. The extension starts the reviewer with `CLAUDE_CODE_SKIP_PROMPT_HISTORY=1`, which keeps its transcript off disk ([Claude Code docs](https://code.claude.com/docs/en/env-vars#variables)), so the worktree's conversations stay the author's and `claude --continue` in the issue's terminal never resumes a reviewer. The reviewer's session still registers, and `claude agents --json` lists it. Since the next reviewer has none of the earlier one's reasoning, each finding in `review.json` carries it: `why`, why it matters, and `fixShows`, what a correct fix must show. The next round reads both with the author's reply and the fix's diff, and marks the finding Fixed only when the fix shows what `fixShows` asks.

<a id="clis"></a>

### 3.4 The CLIs

`plan` and `pr` are thin clients of the core ([architecture §6.1](architecture.next.md#front-ends)).

`plan` shows Next up in an fzf picker and in `plan list`, and runs `plan view <KEY>`, Start, Open, Ship and Done. `plan view` prints the issue's page URL and opens nothing, since a view is read-only. `plan browse` opens a page ([§3.6.7](#pages)), and `plan review <KEY>` sends the review link ([§3.3.7](#review-terminal)). `plan agents` is a thin read from the core: it lists every Claude session with its issue's key, its state and the issue's title ([§3.3.6](#session-status)). Start, Open and Done in `plan` open the issue's `vscode://` link ([§3.3.1](#bridge)).

`pr <KEY>` sends a round. A person runs it on the Mac, as the round's report page says. It commits only what Ship staged, with the round's commit message. It pushes with git's own credentials, then calls the core, which opens the PR or replaces its body, posts each reply to its thread and resolves the thread, and marks the round sent through `ReviewStore` ([§3.6.4](#ship)).

<a id="naming"></a>

### 3.5 Naming

A key is `PREFIX-<number>`: the project's prefix and the issue's Redmine number, unpadded, as in `SK-123`. Redmine's numbers are unique across its projects, so the number finds the issue.

One pattern serves every key check. `ISSUE_KEY_PATTERN` is `[A-Z]+-[0-9]+`, anchored, `^[A-Z]+-[0-9]+$`, where a whole string is tested. A prefix is capital letters only and the number is ASCII digits, so `SK-123` and `CIAM-123` are keys, and `ab-1`, `AB-`, `AB_C-1`, `AB2-1` and a number in non-ASCII digits are not. The core defines it, the extension carries the same string, and a test on each side of the bridge asserts its exact value. It is the check for the issues file, branch names, worktree folders, `.tmp/<KEY>/` folders, command arguments and the Claude hooks.

| Thing | Name |
| --- | --- |
| Branch | `<type>/<KEY>-<slug>`, where `<type>` is the branch prefix the issue's tracker adapter gives for its type |
| Worktree | `<repo>.worktrees/<KEY>`, beside the main checkout |
| Scratch folder | `.tmp/<KEY>/` in the worktree |
| Commit | A subject naming the key, as in `Add X (SK-123)`, with `refs SK-123`, which links the commit to the issue |
| Pull request | A title naming the key |
| Squash merge | `fixes SK-123`, which closes the issue when Redmine fetches the commit |

Each tracker adapter maps an issue's type to its branch prefix, by the same mechanism for every adapter, and every other part takes the prefix from the adapter. The `Redmine` adapter's map is one to one: the issue's tracker name, lowercased and made safe for a ref, is the prefix, so Bug gives `bug/` and Feature gives `feature/`. No instance setting changes it. The Jira adapter keeps its `typeMap`.

<a id="operations"></a>

### 3.6 Operations

<a id="repo-resolution"></a>

#### 3.6.1 Which repo

The rule is [architecture §7.1](architecture.next.md#repo-resolution)'s. The git and worktree module applies it on every call:

1. A key decides. Its prefix gives the project, and the project's repos that appear in `checkouts` are the candidates. A checkout matches a repo by its `origin` remote, normalized: ssh or https, with or without `.git`.
2. Existing work wins. Each candidate is searched for `<repo>.worktrees/<KEY>` or the issue's branch. The worktree or branch found is the record of which repo holds the issue's work, so there is no extra state to keep in sync. Found in more than one repo, the operation refuses and names each.
3. With no existing work, the current folder's repo is used when it is a candidate. A linked worktree resolves to its main checkout through `git rev-parse --path-format=absolute --git-common-dir`, so a run inside `<repo>.worktrees/SK-9` means `<repo>`. Otherwise one candidate is used outright, several open a pick ("Which repo holds this issue's work?"), and none is refused, naming the project's remotes and `checkouts`.
4. With no key, Next up shows the current repo's project first when run inside a configured repo, and every configured project anywhere else.

A trigger with no current folder, such as an issue page's link or the Issues view, skips step 3's current folder: the repo comes from the issue's project and its repositories, and the main checkout from `checkouts`. When the project has several repos, the extension asks.

The resolved repo gives the project, the main checkout, the worktree root `<repo>.worktrees/` and the default branch. A `checkouts` path whose remote no project lists is reported as a config error, naming the path and its remote.

<a id="start"></a>

#### 3.6.2 Start

Start is [architecture §7.2](architecture.next.md#start)'s. It shows for an issue that hasn't started. It refuses a parent issue with open children, since a parent only groups them.

The extension checks its own link when it activates: when no `~/.vscode/extensions/skuld.plan-window-*` entry resolves into skuld's main checkout, it shows where the link points and the fix. The link is only read, never rewritten.

Start lists the issue's open blockers and asks whether to go on; no stops with nothing changed. It calls the core first, which moves the issue to "in progress", reads it back and refuses if the status isn't the target, and journals "Started". A refusal leaves the Mac untouched. Once the core accepts, the module makes the branch and its worktree off the latest default branch. If the branch already exists here or on `origin`, Start reuses it on its own base. Start refuses a closed issue ([failure-modes.md, *Start*](failure-modes.md#start)).

It then brings the issue into the plan window. It writes the issue workspace file, adds the key to the issues file and the worktree to the plan workspace file, and runs `code` on the plan workspace file. If `code` fails, Start stops and prints the command to run by hand. Last, it sends the issue's `/open` link with `code --open-url <link>`, after `code` has brought the plan window forward, so it finishes with a person at the issue's Claude. That last step is best-effort: if it fails, Start still prints `<KEY> ready.` and adds one line on stderr, `couldn't open <KEY> in the plan window: <error>; click it in the Issues view`. Start prints the issue's page URL.

Reopening a finished issue is one step by hand: a person sets the issue back to in progress in Redmine, then starts it. Start accepts an issue in progress and refuses only a closed one. With no worktree it makes one at `<repo>.worktrees/<KEY>`, reusing the issue's local branch, else `origin`'s, else making the branch from the default branch. It adds the key and the folder like any Start, and the Claude terminal resumes the worktree's earlier conversation when there is one.

<a id="open"></a>

#### 3.6.3 Open

Open is Start for an issue already in progress. It reopens the worktree and brings the issue into the plan window, recreating any part that is missing. A `Claude <KEY>` terminal that is open is focused, never started twice, and the page's tab is brought forward without opening a second one. It ends with the same `/open` link and the same best-effort report as Start.

<a id="ship"></a>

#### 3.6.4 Ship

The round is ready before Ship runs. At the end of the author's implementation step, and at the end of each review-resolve step, the round's files are produced in `.tmp/<KEY>/` of the worktree: a manifest listing the files that belong to the round, `commit.txt` when the manifest lists any, `pr-body.md`, and a reply to each review thread in `replies/<comment id>.md`.

Ship is triggered on the Mac ([architecture §7.3](architecture.next.md#ship)). It reads the round from `.tmp/<KEY>/` and refuses by name a missing PR body, a missing commit message while the round's staged set is non-empty, a file the manifest lists that is missing, or a branch that isn't the issue's. Otherwise it uploads the round to the core, which records it, with its findings and files, through `ReviewStore` as a draft. The plugin renders the round's report page, which the plan window shows, and the page ends with "run `pr <KEY>`". Ship stages exactly the files the manifest lists, and it moves no HEAD and pushes nothing. Ship decides what belongs to the round, and `pr` trusts it.

A person runs `pr <KEY>` on the Mac. It takes the issue's lock, commits only what Ship staged with `commit.txt`, and pushes, then calls the core, which opens the PR or replaces its body, posts each reply to its thread and resolves the thread, and marks the round sent. It holds the lock only for its own run: the commit, the push and the sent mark. A second `pr` in that window is refused by name, and so is Done. Once `pr` releases the lock, running it again is the normal path. Each file is deleted once sent, so a second `pr` sends nothing twice. Ship again is the next round.

<a id="done"></a>

#### 3.6.5 Done

Done is [architecture §7.4](architecture.next.md#finish)'s. It shows once the `PullRequests` adapter finds a PR for the issue's branch and it is merged. It accepts an issue that is in progress or already closed, since Redmine may have closed it from the merge commit before Done runs, and it ignores parent issues. It refuses, before it changes anything, while a round is unsent, and names the round. Done calls the core first, and a refusal leaves the Mac untouched. It checks everything before it changes anything, and Done again finishes what a stopped one left ([failure-modes.md, *Done*](failure-modes.md#done)).

Done refuses when the current working directory is inside the issue's worktree, naming the worktree. The refusal is one of the checks before any change.

Done's steps run in this order, each printing one line when it runs, is skipped or cannot run:

1. Remove the terminal `Shell <KEY>`.
2. Remove the terminals `Claude <KEY>` and `Review <KEY>`, each by a clean close: Ctrl-C twice, then removal once the process has exited. No kill.
3. Remove the PR's browser tab.
4. For a store ticket, set `status: done` and `closed: <today>`, leaving `closed:` as it is when already set.
5. Remove the issue's tab: the ticket's editor tab for a store ticket, the Jira page's browser tab for a Jira issue.
6. For a store ticket, run the store's `scripts/index.py`.

Steps 1 to 3 and 5 are the `done` link handler's work ([§3.3.2](#plan-window)). Steps 1 to 3 run before the worktree is removed, since a terminal in a removed folder keeps running there. Step 4 follows the journal line. The worktree, branch, issues file, pulses file and plan workspace removals follow as before. With VS Code unreachable, Done prints each window step it skipped, naming what to close by hand, and still does steps 4 and 6. A terminal or tab already gone, a ticket already done and an index already current are skipped without an error.

The issue leaves the Issues view once its key leaves the issues file.

Done never changes a Jira issue's status and never opens a page, and a Jira issue gets steps 1 to 3 and 5 only. A store ticket's status and `closed:` are set by Done's step 4. A Jira issue's status is left to the human, and Done prints a close note naming the issue and its browse URL.

<a id="next-up"></a>

#### 3.6.6 Next up

Next up answers "what's next" in the picker, in `plan list` and in the core's interface. It is `IssueStore`'s list of open leaf issues in plan order. A parent issue only groups its children, so Next up never lists one:

1. Milestones ordered by their Redmine version name, then issues with no milestone.
2. Within a milestone, open issues that block others first, then by `Ordinal`, then issues with no ordinal by number.

Each row carries the issue's project name.

<a id="pages"></a>

#### 3.6.7 Pages

Pages are [architecture §7.8](architecture.next.md#open-url)'s, and no command publishes one. The URL path's keys (the issue key, the round, the page) name a page. A URL whose target doesn't exist redirects (302) to the nearest page that does: a missing round to the latest round, and an issue with no rounds to the issue's page. An unknown key gets a 404 page that says what exists. Every page's header links the issue and its pull request; before a PR exists, the header says so. A page is always opened at its URL, never as a `file://` path.

`plan browse <KEY | pr#N | url> [KEY]` opens a page. A key opens the issue's page, `pr#N` the pull request, and a URL itself. `[KEY]` names the issue the page belongs to when the target doesn't. The issue is, in order, the explicit key (the target's own, or `[KEY]`), then the `PLAN_KEY` of the terminal that ran the command, and otherwise none, and the link carries no key.

`plan browse` always prints the URL first. VS Code sends a `vscode://` link to the focused window, and no terminal can name its window's id, so it runs `code <plan workspace>`, which focuses the plan window, then `code --open-url 'vscode://skuld.plan-window/browse?url=<url>&key=<KEY>'`. The URL and key are percent-encoded and passed as arguments, never through a shell. VS Code decodes a link's query once before the handler reads it, so the whole query is encoded once more, and a `&` or `key=` inside the page's URL stays inside the `url` parameter. The `/browse` handler opens the page with `workbench.action.browser.open`, and only an http(s) URL. Every step is best-effort, since the printed URL stands on its own.

Nothing else opens a page. The system's `open` sends a page to the default browser, outside the plan window, so neither the code nor an agent's shell uses it for one. Done opens no page, and `plan view` prints the URL without opening it.

<a id="alternatives"></a>

## 4. Alternatives considered

The architectural alternatives (a Mac daemon, a status engine in the extension, a local page server and one project per repo) are weighed in [ADR 0005 §4](adr/0005-redmine-is-the-tracker.md#alternatives). These are the design's own.

- **Keep a record of which repo holds an issue's work.** The issue's worktree or branch already says, so a record would be extra state to keep in sync ([§3.6.1](#repo-resolution)).
- **Let the reviewer continue the author's conversation.** The worktree's conversations would mix the author's with the reviewer's, and `claude --continue` in the issue's terminal could resume a reviewer. Each round gets a fresh conversation with its transcript off disk, and each finding carries its `why` and `fixShows` in place of the reasoning the next reviewer lacks ([§3.3.7](#review-terminal)).
- **Open a page with the system's `open`.** It sends the page to the default browser, outside the plan window ([§3.6.7](#pages)).
- **Name the plan window by a window id.** No terminal can name its window's id. VS Code knows a window by its workspace file's path, so `code <plan workspace>` finds the plan window wherever it is ([§3.3.2](#plan-window)).

<a id="lenses"></a>

## 5. Cross-cutting concerns

- **L1 Security.** A `vscode://` link is untrusted input, and that includes an issue page's Start, Open and Done links. The review link carries no path and no prompt; the extension builds the path under the worktree's real path, refuses a symbolic link on the way, checks the stored token and refuses while a reviewer is alive ([§3.3.7](#review-terminal)). `/browse` opens only http(s), and any link outside the plan window does nothing ([§3.3.2](#plan-window)). Arguments to `code` are passed as arguments, never through a shell. The link from the Mac to the core is a network boundary with authentication. A call from the Mac carries the person's own Redmine API key, and skuld's automation the bot user's, whose role reaches only skuld's projects. Each key is read from the macOS Keychain on each call and never cached, put in an env var or logged. The GitHub token comes from the host's secret store, never from Redmine's database ([ADR 0005 §3.3](adr/0005-redmine-is-the-tracker.md#credentials)).
- **L2 Privacy and data.** Issues, journals, checklists and review rounds are in Redmine's database. The journal names Redmine users. The nightly export holds every issue as JSON, unencrypted, on a USB drive, and the backup holds Redmine's API keys in plain text, so every backup is a secret kept encrypted by the host, with access limited to its owner. Redmine API keys are rotated on a schedule and right after any suspected leak ([ADR 0005 §3.3](adr/0005-redmine-is-the-tracker.md#credentials)). The local files hold keys and paths only. Session status, with each session's id and working folder, is in Redmine. A reviewer's transcript stays off disk.
- **L3 Reliability and failure.** Local files are written under a lock, through a temp file and a rename. Start reads the issue back and refuses a status that didn't change. Done checks everything before it changes anything, and a second Done finishes what a stopped one left. `pr` deletes each file once sent, so a second run sends nothing twice. Opening the issue and opening a page are best-effort, and the printed line stands on its own. A lock file per issue refuses a second Start or Done.
- **L4 Performance and limits.** A hook makes one call to the core per session event. The Issues view and `plan agents` read session status from the core.
- **L5 Observability.** The Issues view shows an issue orange while its session waits and green while its draft round waits for `pr`, and `plan agents` lists every session. Every refusal names its cause, and the round's report page names the `pr <KEY>` to run. A failed `/open` prints one line on stderr. Done reports "closed by <commit>" or warns that Redmine has not closed the issue yet. The journal names who made each change.
- **L6 Compatibility and migration.** Keys are unpadded Redmine numbers; an imported ticket's store key stays in `Old ID` ([ADR 0005 §3.10](adr/0005-redmine-is-the-tracker.md#migration)). The plan workspace's first folder is skuld's main checkout ([§3.3.2](#plan-window)). Moving Redmine to a hosted service changes its binding and where it runs.
- **L7 Usability and accessibility.** Opening an issue ends with focus on its Claude, the page in the first editor group and Claude in the second, and never opens a second tab or a second Claude. The window closes an issue's terminals only when Done asks ([§3.3.2](#plan-window)). A failed `/open` says to click the issue in the Issues view.
- **L8 Testability.** A test on each side of the bridge asserts `ISSUE_KEY_PATTERN`'s exact value. The core's session status is driven by hook calls, so recorded calls test it without Claude Code. The interim-end suite runs against the instance with a fixture GitHub repo ([ADR 0005 §3.9](adr/0005-redmine-is-the-tracker.md#interim-end)). Pages are tested by scripted browser tests outside the plan window.
- **L9 Operations and cost.** Redmine runs in Docker, and a person runs, backs up and upgrades it from skuld's main checkout. The key plugin is checked again on each Redmine upgrade. Nothing runs on the Mac outside VS Code for skuld's flow: no page server and no engine process. VS Code must be running for Start, Open and Done, from `plan` too. The extension checks its link into skuld's main checkout when it activates ([§3.6.2](#start)).
- **L10 Docs impact.** The architecture links here for its design detail. `failure-modes.md` keeps each command's failure table. [GUIDE.md §7](../GUIDE.md) describes the local page server and `plan serve` (`GUIDE.md:702-720`), and this design has neither.
