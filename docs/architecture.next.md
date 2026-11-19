# Architecture

## Contents

- [1. Purpose](#purpose)
- [2. Definitions](#definitions)
- [3. Storage](#storage)
  - [3.1 Credentials](#credentials)
- [4. Plans](#plans)
  - [4.1 Projects and milestones](#members)
  - [4.2 Dependencies](#dependencies)
- [5. Identity](#identity)
- [6. Components](#components)
  - [6.1 Ports and clients](#front-ends)
  - [6.2 Process boundary](#process-boundary)
  - [6.3 Platform](#platform)
  - [6.4 Redmine plugins](#redmine-plugins)
  - [6.5 The plan window](#plan-window)
- [7. Operations](#operations)
  - [7.1 Which repo](#repo-resolution)
  - [7.2 Start and Open](#start)
  - [7.3 Ship](#ship)
  - [7.4 Finish](#finish)
  - [7.5 Commits](#tool-commits)
  - [7.6 Review rounds](#review-rounds)
  - [7.7 Next up](#ready-file)
  - [7.8 Opening a page](#open-url)
  - [7.9 The tracker](#tracker)
- [8. Failure modes](#failure-modes)
- [9. Views](#views)
- [10. Rules](#invariants)

<a id="purpose"></a>

## 1. Purpose

Skuld plans and ships work. Its core lists what's next, and starts, opens, ships and finishes an issue, with the checks and refusals that go with each step. The core and its interface run inside Redmine, as skuld's own Redmine plugin. VS Code is skuld's client: every issue's Claude runs in one VS Code window, the plan window, whose `plan-window` extension reaches the core through a bridge. `plan` and `pr` are thin command-line clients of the core. Skuld also holds the Redmine setup: the instance's container, its plugins and its backup.

Skuld's issue tracker is Redmine. On the work computer, issues are Jira's, and that arrangement is outside this document. Pull requests are GitHub's, and they are the one GitHub integration; GitHub Issues is not used.

The design under this architecture is in [design.md](design.md).

<a id="definitions"></a>

## 2. Definitions

| Term | Definition |
| --- | --- |
| Issue | The unit of planned work, named by its key, `PREFIX-n`: its project's prefix and its Redmine number (§5). It lives in Redmine |
| Issue page | Redmine's page for an issue, with what skuld's plugin adds to it: the checklist of Done boxes and the Start, Open and Done links (§6.1, §6.4) |
| Project | A Redmine project that carries a key prefix and the repos its work happens in, one or several. A parent project only groups projects (§4.1) |
| Redmine | Skuld's issue tracker: one Redmine instance, outside every configured repo, with skuld's plugins installed (§6.4, §7.9). The `IssueStore` adapter for it is the `Redmine` adapter |
| Core | Skuld's flow (Next up, Start, Open, Ship and Done) with its checks and refusals, and its interface, running in skuld's Redmine plugin. It reaches every outside system through a port (§6.1) |
| Bridge | The `plan-window` extension's link to the core (§6.2) |
| Client | Anything that triggers the core's flow: the core's interface, VS Code through the bridge, `plan` and `pr` (§6.1) |
| Git and worktree module | The part of `plan-window` that does skuld's work on the Mac: git and worktree operations and the local file writes (§6.2) |
| Bot user | Skuld's own Redmine user, whose role reaches only skuld's projects. Skuld's automation acts as it (§3.1) |
| Journal | Redmine's record of every change to an issue, each entry naming the user who made it (§3) |
| Configured repo | A repo some project lists, checked out on this Mac (§7.1) |
| Main checkout | A configured repo's checkout on this Mac that is not a worktree. The machine config names it (§3) |
| Machine config | What differs per Mac, among it this Mac's main checkouts (§3) |
| Milestone | A Redmine version of a parent project, shared with its tree (§4.1) |
| Ordinal | A whole number, kept in a custom field, that orders issues within a milestone; lower comes first (§7.7) |
| Default branch | The branch GitHub has configured for the repo. It carries the code |
| Plan window | The one VS Code window for all issues, on the plan workspace file. Each issue's Claude runs in an integrated terminal of it, and pages open in its integrated browser (§6.5) |
| Plan workspace file | The workspace file the plan window is open on. It lists as folders the worktrees of the issues in the issues file (§6.5) |
| Issue workspace file | The issue's record of its key, worktree, prompt and issue page, which `plan-window` reads to open the issue (§6.5) |
| Issues file | The list of issues that have been Started or Opened and not yet Done (§6.5) |
| Session status | Each Claude session's state, which Claude Code's hooks report to the core and the core keeps (§6.2) |
| Tint | An issue's colour in the Issues view, which the core gives: orange while one of its sessions waits on a person, green while it has a draft round waiting for `pr` (§6.2) |
| Issues view | The view that `plan-window` adds to the plan window's Explorer: one item per issue in the issues file, in the tint the core gives it. Clicking an item opens the issue (§6.5) |
| Start | Moving an issue that hasn't started to "in progress", making its branch and worktree, journaling "Started", and bringing the issue into the plan window (§7.2) |
| Open | Bringing an issue already in progress into the plan window, recreating whatever part of it is missing (§7.2) |
| Ship | Uploading the issue's round to the core as a draft, and showing its report page, which says to run `pr` (§7.3) |
| Done | The cleanup after the issue's PR is merged: it removes the worktree and branch, brings the default branch up to the merge, and journals the merge on the issue (§7.4) |
| Finish | The squash merge of the issue's PR, whose `fixes <KEY>` closes the issue when Redmine fetches the commit, followed by Done (§7.4) |
| Done box | One item of an issue's checklist of Done conditions, kept in skuld's Redmine plugin. Each tick and untick is a journal entry naming the user (§7.3) |
| Round | One pass of review: its findings, commit message, PR body and review replies. It is kept in the issue's worktree until Ship uploads it to Redmine through `review_findings` as a draft, and `pr` marks it sent (§3, §7.6) |
| Review report | A page of skuld's Redmine plugin that shows an issue's review rounds and their findings (§7.8) |
| Next up | The open issues in plan order (§7.7) |
| Blocker | An open issue the issue depends on, through a Redmine `blocks` relation (§4.2) |

<a id="storage"></a>

## 3. Storage

Each kind of thing has one home:

| Home | Holds |
| --- | --- |
| Skuld | Code and definitions: the core and skuld's Redmine plugin, the `plan-window` extension with its bridge, `plan` and `pr`, and the Redmine setup, which is the instance's container definition, its plugins and its backup |
| Redmine | Data: issues with their journals, relations and checklists, the versions that are milestones, review rounds with their findings and files, each a draft until `pr` sends it, and each Claude session's status |
| A configured repo | Code. Its branches, worktrees and pull requests are what skuld changes there |
| The issue's worktree | The round until Ship uploads it: its commit message, PR body and replies, and the reviewer's files for each round |
| The Mac | The local files that `plan-window` writes (the issues file and the workspace files) and the machine config |

Tracking data lives in Redmine's database, and the journal is the record of every change. Data leaves the database in a nightly backup and a nightly export. The export holds every issue as JSON, journal included, and it is how someone with no Redmine account reads the issues. An agent reads issues through Redmine's REST API.

<a id="credentials"></a>

### 3.1 Credentials

A call from the Mac, from `plan-window`, `plan`, `pr` or Claude Code's hooks, carries the person's own Redmine API key. Skuld's automation carries the bot user's key, and the bot user's role, which reaches only skuld's projects, is what limits it. Both keys live in the macOS Keychain. Anything of skuld's that uses one reads it from there on each call, and never caches it, puts it in an env var or logs it.

The GitHub token that the `PullRequests` adapter uses lives in the settings of skuld's Redmine plugin, in Redmine.

What a person does in the core's interface, the core does as that person's signed-in Redmine user. A call from the Mac acts as the user whose key it carries. Skuld's own automation acts as the bot user. The journal names whoever acted. A `fixes <KEY>` close is Redmine's own, and Redmine journals it as the commit's author.

The link from the Mac to the core is an authenticated network boundary. Nothing in skuld or in a configured repo holds a credential.

<a id="plans"></a>

## 4. Plans

A plan is Redmine's projects, their milestones and the dependencies between their issues.

<a id="members"></a>

### 4.1 Projects and milestones

A project carries a key prefix and the GitHub repos its work happens in. A project may list several repos, and a repo belongs to one project. Each repo is registered as a Redmine repository of its project, with `refs` and `fixes` as its commit keywords (§5). A parent project only groups projects.

Milestones are versions of the parent project, shared with its tree. An issue's milestone is its version. What Redmine lacks for planning is kept in custom fields ([design §3.2](design.md#redmine-plugins)).

<a id="dependencies"></a>

### 4.2 Dependencies

An issue's blockers are the issues that hold a `blocks` relation to it. Redmine refuses a relation that would close a cycle. A blocker is cleared once it is closed.

<a id="identity"></a>

## 5. Identity

An issue is its key, `PREFIX-number`. The number is Redmine's issue number, which is unique across Redmine's projects, so a key's number finds the issue. Each project has a prefix, and skuld's is `SK`. A key whose prefix doesn't match its issue's project is refused.

One key pattern serves every key check, in the core, in `plan-window` and in the Claude hooks.

A key plugin reads keys in Redmine. It reads a key in text and in commits and renders it as a link to its issue.

An issue's branch and worktree are named for its key, and a commit names its issue with a keyword: `refs <KEY>` links the commit to the issue, and `fixes <KEY>` in the merge commit closes it (§7.4). An issue's type, and the branch prefix it gives, belong to its tracker's adapter, and every adapter maps one to the other by the same mechanism. The names are in [design §3.5](design.md#naming).

<a id="components"></a>

## 6. Components

Skuld's components are the core, its clients and its Redmine plugins, joined by the ports and boundaries below.

<a id="front-ends"></a>

### 6.1 Ports and clients

The core runs in skuld's Redmine plugin, on Redmine's plugin API. Every outside system it uses is reached through a port, and the adapters are part of the core:

| Port | Does | Adapter |
| --- | --- | --- |
| `IssueStore` | list Next up, read an issue, create, set fields, transition, append a log line, close note | `Redmine` |
| `ReviewStore` | record a round, its findings, outcomes and files, and read them back | `review_findings` (§7.6) |
| `PullRequests` | open or update a PR, read its state and threads, post replies | GitHub |

The key's prefix picks an issue's `IssueStore` adapter, and skuld's projects go to the `Redmine` adapter. The work computer's routing is outside this document.

Claude Code is a helper agent. It writes code and reviews in the plan window's terminals, which `plan-window` starts, and every command that only reads works with no Claude session running.

| Client | What it does |
| --- | --- |
| The core's interface | Runs inside Redmine. It shows Next up, and on each issue page the checklist of Done boxes and the Start, Open and Done links, which are `vscode://` links into `plan-window`. A person's action there is taken as their signed-in Redmine user (§3.1) |
| VS Code | The plan window and its `plan-window` extension, which reaches the core through the bridge and handles the issue page's links (§6.5) |
| `plan` | Shows Next up and runs Ship, and opens the Start, Open and Done links ([design §3.4](design.md#clis)) |
| `pr` | Commits and pushes the round on the Mac, then calls the core to send the rest of it (§7.3) |

Each client calls the core and holds no flow of its own. Beyond calling the core, `pr` makes the round's commit and push.

<a id="process-boundary"></a>

### 6.2 Process boundary

Each outside system is reached only through its port's adapter. Policy code names no tool: the adapters meet the ports, and the core wires them together.

- The `Redmine`, `review_findings` and GitHub adapters run in skuld's Redmine plugin. The first two work through Redmine's plugin API.
- Every trigger for work on the Mac starts on the Mac: in VS Code, in a terminal running `plan` or `pr`, or at a `vscode://` link on an issue page, which `plan-window` handles. `plan`'s Start, Open and Done open the same links, so the module has one way in, and VS Code must be running for them. The Mac reaches the core across an authenticated network boundary: VS Code through the bridge, and `plan` and `pr` directly (§3.1).
- The core never calls the Mac. Git and worktree operations on the Mac, and the local file writes, belong to `plan-window`'s git and worktree module ([design §3.3.4](design.md#git-module)), except the round's commit and push, which belong to `pr`. A `vscode://` link's handler is a thin wrapper over the module, which does the work on the Mac and calls the core.
- For Start and Done the core is called first, and it can refuse. The module works on the Mac only after the core accepts, so a refusal leaves the Mac untouched.
- Claude Code's hooks report each session's status by calling the core, which keeps it and places each session on the issue its working folder names. The Issues view and `plan agents` read it from the core.
- The core gives each issue its tint, from its sessions' status and from a draft round in `review_findings`.

The boundary is drawn around outside systems. A client runs local programs such as VS Code's `code` as part of its normal work. A call to Redmine or a tracker write always goes through its adapter, and a git operation through the git and worktree module or `pr`.

<a id="platform"></a>

### 6.3 Platform

| Layer | Choice |
| --- | --- |
| Tracker | Redmine 7 in Docker, with skuld's plugins (§6.4) |
| Core | Ruby, in skuld's Redmine plugin, on Redmine's plugin API |
| Bridge | TypeScript, in the `plan-window` extension |
| Client | VS Code, with one plan window for all issues and the `plan-window` extension; `plan` and `pr` in a terminal |
| Backup | A nightly backup of Redmine's database with an integrity check, and a nightly export of every issue as JSON |
| Auth | The person's own Redmine API key in the macOS Keychain, read per call, for calls from the Mac; the bot user's key, kept the same way, for skuld's automation; the signed-in user's Redmine session for actions in the core's interface; the GitHub token in skuld's Redmine plugin's settings; an authenticated link from the Mac to the core |
| Browser | The plan window's integrated browser |
| Pages | Skuld's Redmine plugin, rendering an issue's pages from the issue's data (§7.8) |
| Pull requests | GitHub; Redmine links commits to an issue through the repo's registration and the key plugin |

<a id="redmine-plugins"></a>

### 6.4 Redmine plugins

What Redmine does not do for skuld is a plugin on Redmine's plugin API. Skuld's architecture depends on these plugins:

| Plugin | Holds |
| --- | --- |
| Skuld's own plugin | The core, its interface, the checklist of Done boxes, the pages, the project field that carries the key prefix, and the GitHub token in its settings |
| `review_findings` | `ReviewStore`'s adapter: a ledger of each issue's review rounds and findings (§7.6) |
| The key plugin | Reads keys in text and in commits and renders them as links (§5) |

Their design is in [design §3.2](design.md#redmine-plugins).

<a id="plan-window"></a>

### 6.5 The plan window

Every issue's Claude runs in one VS Code window, the plan window, on the plan workspace file, and a second plan window is never opened. ADR 0004 records the choice of one window ([ADR 0004 §3](adr/0004-one-plan-window.md#decision)).

Every VS Code window loads `plan-window`, and it acts only in the plan window. It holds:

- the bridge to the core (§6.2);
- the handlers for the issue page's Start, Open and Done links (§6.2);
- the Issues view, with one item per issue in the issues file, each in the tint the core gives it;
- the git and worktree module, which makes and removes branches and worktrees and resolves which repo holds an issue's work (§7.1); and
- the local files: the issues file and the workspace files. Writes to them are locked, and a reader never sees a half-written file.

Opening an issue, by a click in the Issues view or by the issue's link, shows the issue page in the integrated browser beside the issue's Claude terminal. The window never closes an issue's terminals. Claude's review of a PR runs in a terminal of its own, in a conversation apart from the author's. The design is in [design §3.3](design.md#extension).

<a id="operations"></a>

## 7. Operations

```text
   Next up (plan order)       the issue's worktree                     the issue (Redmine)
   ────────────────────       ────────────────────                     ───────────────────
   (1) START ───────────────▶ branch and worktree, off the default ──▶ in progress, "Started" journaled
                              branch; plan window, Claude in its        (its page opens beside Claude)
                              terminal; Claude works, the round is
                              produced at the end of each step
   (2) SHIP ────────────────▶ round staged and uploaded, no HEAD ────▶ draft round recorded in review_findings;
                                                                        its report page says to run pr <KEY>
   (3) pr <KEY> ────────────▶ commit and push ───────────────────────▶ PR opened or updated, replies posted,
                                                                        round marked sent;
                                                                        Done boxes ticked, each journaled
   (4) squash merge on GitHub; the merge commit carries fixes <KEY>
   (5) DONE ────────────────▶ worktree and branch removed, default ──▶ merge journaled;
                              branch up to the merge                     Redmine closes the issue on fetch
```

The default branch carries the code, and everything reaches it by PR. An issue's status lives in Redmine, and the only status change skuld makes is Start's move to "in progress". A `fixes <KEY>` keyword in the merge commit closes the issue when Redmine fetches the commit; that close is Redmine's own, and the one place automation moves an issue. Done never changes an issue's status (§7.4).

<a id="repo-resolution"></a>

### 7.1 Which repo

Several repos can serve one project, and the git and worktree module resolves an issue's repo on every call. A key's prefix gives the project, and the project's repos checked out on this Mac are the candidates. A candidate that already holds the issue's worktree or branch is the repo, and work found in more than one repo is refused. With no work found, one candidate is used, several open a pick, and none is refused.

The resolved repo gives the project, the main checkout and the default branch. An operation makes its git and GitHub changes in the configured repo, and its issue and review changes in Redmine; it never writes an issue into a configured repo. The steps are in [design §3.6.1](design.md#repo-resolution).

<a id="start"></a>

### 7.2 Start and Open

Start moves an issue that hasn't started to "in progress", after a person confirms past its open blockers (§4.2), and brings it into the plan window (§6.5). Open does the same for an issue in progress, recreating whatever part of it is missing. Start refuses a closed issue, and a status write that doesn't take is refused (§8).

Both are triggered from the Mac, in VS Code or at the issue's `vscode://` link, which the issue page and `plan` both open (§6.2). For Start the core is called first, and a refusal leaves the Mac untouched. The branch, the worktree and the plan window are the git and worktree module's work, and the status move and its journal entry are the core's. Both end at the issue's Claude, with its issue page beside it. That last step is best-effort, and the issue is ready even when it fails. The design is in [design §3.6.2](design.md#start) and [design §3.6.3](design.md#open), and the failure tables in [failure-modes.md, *Start*](failure-modes.md#start).

<a id="ship"></a>

### 7.3 Ship

The round is ready before Ship runs. Its commit message, PR body and replies to review threads are produced at the end of the author's implementation step and at the end of each review-resolve step, and they stay in the issue's worktree until Ship uploads them (§3).

Ship is triggered on the Mac. It uploads the issue's round to the core, which records it, with its findings and files, through `ReviewStore` as a draft (§7.6). The round's report page, rendered by skuld's Redmine plugin and shown in the plan window, ends with the instruction to run `pr <KEY>`. Ship stages the round's changes, and it moves no HEAD and pushes nothing. A person runs `pr <KEY>` on the Mac. `pr` commits only what Ship staged, with the round's commit message, and pushes, then calls the core, which opens or updates the PR, posts the replies through `PullRequests` and marks the round sent. A second `pr` sends nothing twice.

Done boxes are ticked in Redmine, in the issue's checklist in skuld's plugin. Each tick and untick is a journal entry naming the user.

`pr` writes no tracker field to link the PR. Redmine links commits to the issue through the repo's registration and the key plugin (§5), and Done finds the PR from the issue's branch. The design is in [design §3.6.4](design.md#ship).

<a id="finish"></a>

### 7.4 Finish

The finish is the squash merge of the issue's PR, then Done. The merge commit carries `fixes <KEY>`, which closes the issue when Redmine fetches the commit, and Redmine journals that close as the commit's author.

Done shows once the issue's PR is found and merged, and accepts an issue that is in progress or already closed. It is triggered from the Mac, as Start is, and the core is called first (§7.2). It removes the issue's worktree and branch, brings the main checkout's default branch up to the merge, journals the PR and its merge commit on the issue, and takes the issue out of the plan window. The work on the Mac is the git and worktree module's, and the journal entry is the core's.

Done never changes an issue's status. It checks everything before it changes anything, and Done again finishes what a stopped one left ([failure-modes.md, *Done*](failure-modes.md#done)). The design is in [design §3.6.5](design.md#done).

<a id="tool-commits"></a>

### 7.5 Commits

Every commit in a configured repo carries a message a person or Claude wrote, and reaches the default branch through a pull request. `pr` makes the round's commit with the round's message, and neither `plan` nor `pr` makes a commit of its own. A commit names its issue with `refs <KEY>`, and the squash merge carries `fixes <KEY>` (§5).

<a id="review-rounds"></a>

### 7.6 Review rounds

Review findings have a port of their own, `ReviewStore`, separate from the tracker's. Its adapter is the `review_findings` plugin. When Ship uploads a round, the round, its findings and outcomes, and its files (the commit message, the PR body and the replies to review threads) are recorded in Redmine as a draft, beside the issue they belong to. When `pr` sends it, the round is marked sent.

Claude's review of each round runs as a conversation of its own, apart from the author's, in a terminal of the plan window (§6.5). The design is in [design §3.3.7](design.md#review-terminal).

<a id="ready-file"></a>

### 7.7 Next up

Next up answers "what's next" in `plan` and in the core's interface. It is `IssueStore`'s list of open issues in plan order. The order is in [design §3.6.6](design.md#next-up).

<a id="open-url"></a>

### 7.8 Opening a page

Pages for an issue, such as its review report, come from skuld's Redmine plugin, rendered from the issue's data on each request. A page is always opened at its URL, never as a file.

A page opens in the plan window's integrated browser, never in the default browser, and opening it is best-effort. A link to a page is a `vscode://` link, which `plan-window` opens at the page's Redmine URL. The design is in [design §3.6.7](design.md#pages).

<a id="tracker"></a>

### 7.9 The tracker

Skuld's tracker is one Redmine instance, outside the repos it plans, backed up nightly (§6.3). It is down when its host is down.

The `Redmine` adapter is the `IssueStore` adapter for skuld's projects. It answers in the same shapes every `IssueStore` adapter does, so Start, Ship and Done have no tracker-specific branch. Every write is journaled under the user who made it: the signed-in user for an action in the core's interface, the user whose key a call from the Mac carries, and the bot user for skuld's automation (§3.1).

A person runs, backs up and upgrades the instance from skuld's main checkout.

<a id="failure-modes"></a>

## 8. Failure modes

Each operation that writes has its own table in [`failure-modes.md`](failure-modes.md). The events they share:

| Event | Effect | Handling |
| --- | --- | --- |
| Redmine is down | No Next up, Start, Ship or Done from the core | Branches, worktrees, commits, pushes and PRs still work by hand with git and GitHub; a merged `fixes <KEY>` commit closes its issue when Redmine next fetches the repo |
| Redmine is restored from a backup | Status changes, journal notes and review rounds written since the backup are gone | Branches, worktrees, PRs and merges are untouched |
| A transition succeeds at the API layer but the status doesn't change | The status is unchanged | The write reads the issue back, refuses, and names the attempted and actual status |
| A key's prefix doesn't match its issue's project | The key names the wrong project | Refused, with nothing changed (§5) |
| A key's project has no repo checked out on this Mac | There is nowhere to make the worktree | Refused, naming the project's remotes and this Mac's checkouts (§7.1) |
| A key's work is found in more than one repo | The operation can't tell which repo holds it | Refused, naming each repo (§7.1) |
| A checkout's remote is listed by no project | The checkout serves nothing | Reported as a config error, naming the path and its remote |
| A round is unsent at Done | Done would finish with review work unsent | Done refuses before it changes anything, and names the round |

<a id="views"></a>

## 9. Views

| View | Shows |
| --- | --- |
| Next up, in `plan` | Open issues in plan order, with each issue's project name (§7.7) |
| One issue, in `plan` | The issue's fields, open blockers, what it blocks, and its description |
| The core's interface in Redmine | Next up, and on each issue page the checklist of Done boxes and the Start, Open and Done links (§6.1) |
| The issue page in the plan window | The issue, its journal, relations and checklist, opened beside Claude when the issue is opened (§6.5) |
| The Issues view | Every issue in the issues file, in the plan window's Explorer, tinted orange or green by the core (§6.2) |
| Claude's sessions, in `plan` | Every Claude session, with its issue's key and state, read from the core (§6.2) |
| The journal | Every change to every issue, naming who made it (§3) |
| `review_findings` | Every round and finding (§7.6) |
| The nightly export | Every issue as JSON with its journal, for a reader with no Redmine account (§3) |

<a id="invariants"></a>

## 10. Rules

1. Issues live in Redmine, never in a configured repo. Nothing tracker-related is committed in the repos Redmine plans.
2. The default branch carries the code, and every change reaches it through a pull request. An issue's status lives in Redmine: Start's one move sets it in progress, and nothing else in skuld changes an issue's status. The one automated close is Redmine's own, from `fixes <KEY>` in the merge commit (§7.4, §7.5).
3. Every outside system is reached through its port's adapter, and policy code names no tool (§6.2).
4. The core never calls the Mac. Every trigger for work on the Mac starts on the Mac, and the Mac reaches the core across an authenticated network boundary. Claude Code's hooks report session status by calling the core (§6.2).
5. Every tick and untick of a Done box is a journal entry naming the user (§7.3).
6. Tracking data lives in Redmine's database, and the journal is the record of every change (§3, §7.9).
7. Skuld holds code, definitions and the Redmine setup, Redmine holds data, and neither holds the other's (§3).
8. A page for an issue comes from skuld's Redmine plugin and opens in the plan window's integrated browser, never in the default browser (§7.8).
9. A call from the Mac acts as the person whose Redmine API key it carries, a person's action in the core's interface as their signed-in Redmine user, and skuld's automation as the bot user. Every Redmine API key is read from the Keychain on each call, and is never cached, put in an env var or logged (§3.1).
10. A key's prefix matches its issue's project, and a key that doesn't is refused (§5).
