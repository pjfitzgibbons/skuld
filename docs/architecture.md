# Architecture

## Contents

- [1. Purpose](#purpose)
- [2. Definitions](#definitions)
- [3. Storage](#storage)
- [4. Plans](#plans)
  - [4.1 Projects and milestones](#members)
  - [4.2 Dependencies and parents](#dependencies)
- [5. Identity](#identity)
- [6. Components](#components)
  - [6.1 Ports and clients](#front-ends)
  - [6.2 Process boundary](#process-boundary)
  - [6.3 Platform](#platform)
- [7. Operations](#operations)
  - [7.0 Which repo](#repo-resolution)
  - [7.1 Start](#start)
  - [7.2 Ship](#ship)
  - [7.3 Finish](#finish)
  - [7.4 Commits](#tool-commits)
  - [7.5 Archiving](#archiving)
  - [7.6 Next up](#ready-file)
  - [7.7 The ticket store](#file-backed-issues)
- [8. Failure modes](#failure-modes)
- [9. Views](#views)
- [10. Rules](#invariants)

**Failure modes by command:** [`failure-modes.md`](failure-modes.md)

<a id="purpose"></a>

## 1. Purpose

Skuld plans and ships work from the terminal. It holds the library, the `plan` and `pr` commands,
the helper scripts and the definitions they share. `plan` lists what's next, and starts, opens,
ships and finishes an issue. Every issue's Claude runs in one VS Code window, the plan window. `pr`
commits, pushes, opens or updates the pull request and answers its review.

Issues live in one of two trackers: the ticket store, a git repo of Markdown files that skuld
defines and works, or Jira Cloud (`cryoport.atlassian.net`) for work projects. Pull requests are
GitHub's, and they are the one GitHub integration; GitHub Issues is not used.

<a id="definitions"></a>

## 2. Definitions

| Term | Definition |
| --- | --- |
| Issue | The unit of planned work, named by its key, `PREFIX-n`: a project prefix and a per-project number, zero-padded to four digits in the store (`SK-0012`). It lives in the ticket store or in Jira. A key's shape is `ISSUE_KEY_PATTERN` (§5) |
| Project | A key prefix and the repos its work happens in. Store projects are listed in skuld's `store.yml`, Jira projects in the machine config |
| Ticket store | A git repo, outside every development repo, holding ticket files, milestones and review files, with its own remote (§3, §7.7) |
| Configured repo | A development repo some project lists by its `origin` remote, checked out on this Mac at a path in `checkouts` (§7.0) |
| Milestone | A store-wide group of issues with a status (`open`, `locked`, `closed`) and an `ordinal` (§4.1) |
| Ordinal | An integer that orders milestones, and issues within a milestone; lower comes first (§7.6) |
| Default branch | The branch GitHub has configured for the repo: `main`, or `develop` under git-flow. It carries the code |
| Issues file | The list of issues that have been Started or Opened and not yet Done, one `<KEY>` per line, that the Issues view and the Claude status engine read (§7.1) |
| Pulses file | Each issue's colour in the Issues view, one `<KEY>=orange` or `<KEY>=green` line per coloured issue, written by `plan pulse` and the Claude status engine (§7.1) |
| Issues view | The view `plan.issues` that the `plan-window` extension adds to the plan window's Explorer: one item per issue in the issues file, tinted from the pulses file. Clicking an item opens the issue (§6.3, §7.1) |
| Plan window | The one VS Code window for all issues, on the plan workspace file, `<workspaceDir>/plan.code-workspace`, which lists as folders the ticket store's folder, always first, and the worktrees of the issues in the issues file. Each issue's Claude runs in an integrated terminal named "Claude <KEY>", and pages open in its integrated browser (§7.1) |
| Issue workspace file | `<workspaceDir>/<KEY>.code-workspace`, the issue's record of its key, worktree, prompt and ticket (a store ticket's file in `plan.ticket`, or a Jira issue's browse URL in `plan.ticketUrl`), which `plan-window` reads to open the issue. It holds settings only: no folders, no window title and no terminal environment. No window opens on it (§7.1) |
| Start | Moving an issue to "in progress" from `plan start`, which makes its branch and worktree and logs "Started" on the issue (§7.1) |
| Round | One pass of review: the commit message, PR body and review replies in `<store>/reviews/<KEY>/` that `pr` sends (§7.2) |
| Finish | The squash merge of the issue's PR, then Done, which notes the merge on the issue and leaves closing it to the human (§7.3) |
| Next up | The open issues in plan order: the store's issues by milestone and ordinal, merged with the Jira project's JQL results (§7.6) |
| Blocker | An open issue the issue depends on: a key in the ticket's `blocked_by`, or a Jira "Blocks" link read inward (§4.2) |

<a id="storage"></a>

## 3. Storage

Each run touches up to three places, and each holds one kind of thing:

- **Skuld** holds code and definitions: `lib/`, `bin/`, the helper scripts, the issue file spec
  (`docs/issue-format.md`), the issue template (`templates/issue.md`), and `store.yml`, which names
  the store's projects and their repo remotes.
- **The ticket store** holds data: `active/` and `archive/` for ticket files, `milestones/`, and
  `reviews/<KEY>/` for review rounds. It sits at the path the machine config's `issueStore` names,
  and has a git remote of its own. A watcher commits and pushes every change once the checker
  passes (§7.7).
- **The configured repo** holds code. Its branches, worktrees and pull requests are what `plan` and
  `pr` change there. It holds no tracker files.

Jira Cloud holds Jira issues with their fields, status, comments and issue links, outside this
tool's control. Its GitHub integration links commits and pull requests to an issue when the branch
name or PR title contains the key, and shows them in the issue's development panel; this tool
writes no field for that.

The machine config, `~/.config/skuld/config.json`, holds what differs per Mac: `issueStore`,
`checkouts` (this Mac's main checkouts) and the Jira projects with their repo remotes.

The Jira API token lives only in `~/.netrc` (machine `cryoport.atlassian.net`), read fresh by
`lib/jira.py` on each call: never cached, never in an env var, never logged. Nothing in skuld or the
ticket store holds a credential.

<a id="plans"></a>

## 4. Plans

<a id="members"></a>

### 4.1 Projects and milestones

A project is a key prefix and a list of repo remotes. One project may have several repos; one repo
belongs to one project. Store projects (`SK`, Skuld, among them) are listed in skuld's `store.yml`;
Jira projects (`CIAM`) in the machine config.

Milestones belong to the ticket store, one file each in `milestones/`, with an `id`, a `name`, a
`status` (`open`, `locked`, `closed`), an `ordinal` and an optional `due` date. An issue names its
milestone in `milestone:`; an open issue is never in a closed milestone. Jira issues use Jira's own
versions and rank.

<a id="dependencies"></a>

### 4.2 Dependencies and parents

A store issue lists the issues that block it in `blocked_by`. Only the blocked side is written; what
an issue blocks is derived by reading every ticket, so the two directions can't disagree. A blocker
is cleared when its status is `done` or `wontfix`, and an archived blocker is closed. A
`blocked_by` key may name a Jira issue, which Jira's adapter is asked about.

A Jira issue's blockers are its "Blocks" links (id `10000`, outward `blocks`, inward
`is blocked by`) whose target's `statusCategory` isn't Done.

A store issue names its parent in `parent`. A Jira issue's parent is Jira's own hierarchy (Epic →
Story/Task/Bug → Sub-task), which this tool reads and doesn't model.

<a id="identity"></a>

## 5. Identity

An issue is its key, unique within its project: `SK-0012` in the store, `CIAM-123` in Jira. A store
issue's number is one above the highest found in `active/` and `archive/` together, and is never
reused. It is zero-padded to four digits, so a listing of the store sorts in number order, and
`plan` pads a key typed without the zeros (`SK-12` is `SK-0012`). Its file is `<KEY>-<slug>.md`, the slug fixed when the file is made, so a retitle doesn't
rename it.

**A key matches `ISSUE_KEY_PATTERN`, `[A-Z]+-[0-9]+`.** Where a whole string is tested the pattern is
anchored, `^[A-Z]+-[0-9]+$`. A project prefix is capital letters only and the number is ASCII digits, so
`CIAM-123` and `SK-0063` are keys, and `ab-1`, `AB-`, `AB_C-1`, `AB2-1` and a number in non-ASCII digits are
not. `lib/issuekeys.py` defines it as `ISSUE_KEY_PATTERN`, and `plan-window` carries the same string in
`plan.js`; a test on each side asserts the exact value. Every key check in `lib`, the extension, the
skill's scripts and the Claude hooks uses it: the issues file, the pulses file, branch names, worktree
folders, `.tmp/<KEY>/` folders and `plan` arguments.

Its branch is `<type>/<KEY>-<slug>`, with the prefix chosen from the issue's type: a Jira
Story or Task, or a store `task` or `question`, is `feat/`; a Bug or `bug` is `fix/`; anything else
`chore/`. Its worktree is `<repo>.worktrees/<KEY>` beside the main checkout. A commit message names
the issue in its subject, e.g. `Add X (CIAM-123)`, which is what Jira's GitHub integration matches.
No commit or merge changes an issue's status (§7.3), so there is no `fixes`/`refs` distinction.

<a id="components"></a>

## 6. Components

<a id="front-ends"></a>

### 6.1 Ports and clients

`plan` and `pr` are one Python package in modules. Every outside system is reached through a port,
a module interface with one class per adapter:

| Port | Does | Adapters |
| --- | --- | --- |
| `IssueStore` | list Next up, read an issue, create, set fields, transition, append a log line, close note | `FileStore`, `Jira` |
| `ReviewStore` | record a round, its findings and outcomes, and read them back | files under `<store>/reviews/<KEY>/` |
| `PullRequests` | open or update a PR, read its state and threads, post replies | `gh` |
| `GitRemote` | fetch, push, rebase onto the base, add and remove worktrees | `git` |

`storeFor(cfg, key)` picks an issue's adapter from its prefix: `FileStore` for a prefix `store.yml`
lists, `Jira` for any other.

The clients:

- **`plan`** (`bin/plan`, `lib/cli.py`) shows Next up in an fzf picker, and runs `new`, `set`,
  `view`, Start, Open, Ship and Done ([GUIDE.md §3](../GUIDE.md#3-commands)).
- **`pr`** commits, pushes, opens or updates the PR, posts review replies, and cleans up after a
  merge.
- **`issue-check`** runs the checker over the store or the files named (§7.7).
- **`issue-watch`** is the watcher: it commits and pushes the store (§7.7).
- **`plan archive`** moves closed issues to `archive/` (§7.5).
- **`jira-get`** sends one read-only GET with `~/.netrc` auth, so an agent can read Jira issues
  without seeing the token.

<a id="process-boundary"></a>

### 6.2 Process boundary

Each outside system is reached only through its port's adapter. Policy code names no tool.

- `lib/jira.py` makes every call to Jira: the JQL search, reading an issue, looking up a transition
  by its target status name and posting it with a read-back guard, reading issue links for blockers,
  and posting ADF comments. It reads the token from `~/.netrc` on each call.
- `FileStore` is the only writer of ticket files that `plan` runs, and it checks every file before
  writing it.
- The `GitRemote` and `PullRequests` adapters run `git` and `gh`: fetching, adding and removing
  worktrees, reading PRs, and pulling and deleting merged branches.

The boundary is drawn around outside systems, not around processes. The front end runs `code`,
and `fzf` as part of its normal work; none of them touches a tracker or git state, so they need
no port. A new call to Jira, a tracker write or a git remote operation always goes
through its adapter.

`gh` is already authenticated, so nothing holds a GitHub token.

<a id="platform"></a>

### 6.3 Platform

| Layer | Choice |
| --- | --- |
| Trackers | The ticket store (Markdown with YAML front matter, in a git repo); Jira Cloud, `cryoport.atlassian.net` |
| Store commits | `issue-watch` under launchd: debounced, gated by the checker, commits and pushes |
| Auth | `~/.netrc` for Jira, read per call; `gh`'s and git's own credentials for GitHub and the store's remote |
| Front end | VS Code, one plan window for all issues, opened by the `code` CLI on the plan workspace file, which opens or raises it wherever it is, all via `subprocess`; fzf for the picker; ticket files in an editor tab |
| Issues view | The `plan-window` extension's view `plan.issues`, contributed under `contributes.views.explorer`, so it shows in the plan window's Explorer: one item per key in the issues file, in file order, labelled with the key. It watches the issues file and the pulses file, and tints an item orange or green from the pulses file. Clicking an item opens the issue (§7.1) |
| Claude status | `lib/agents.py`, run every few seconds by a LaunchAgent as `plan agents tick --watch`, and at once when a waiting marker changes: reads `claude agents --json`, the waiting markers in `~/.cache/plan/waiting` that the Claude Code hook `bin/claude-waiting` keeps, the issues file and git, once a minute each listed issue's PR through `gh`, and writes an issue's colour to the pulses file only when it changes (§7.1) |
| Browser | The plan window's integrated browser, reached by `showUrl()` (§7.6.1) and by opening a Jira issue (§7.1); never system `open`. Browser automation and browser tests run in Chrome, never in the plan window |
| Pages | One local Flask server on `127.0.0.1`, run by a LaunchAgent from login and controlled by `plan serve start\|stop\|restart\|status`, rendering an issue's pages from Jinja templates (§7.6.1) |
| Pull requests | `gh`; Jira linkage through Jira's development panel, not a custom field |
| Jira writes | `/rest/api/3/*`, ADF for descriptions and comments |

<a id="operations"></a>

## 7. Operations

```text
   Next up (plan order)       worktree <repo>.worktrees/<KEY>          the issue (store or Jira)
   ────────────────────       ───────────────────────────────          ─────────────────────────
   (1) START ───────────────▶ branch <type>/<KEY>-slug, off base ────▶ in progress, "Started" logged
                              plan window, Claude in its terminal       (a store ticket opens in a tab)
                              Claude works · writes <store>/reviews/<KEY>/
   (2) SHIP ────────────────▶ pr <KEY>: commit, push, open/update PR ─▶ Done boxes ticked
                              review rounds: replies/                    (Jira: dev panel, native)
   (3) squash merge on GitHub; the PR title and branch carry <KEY>
   (4) DONE ────────────────▶ worktree and branch removed, base up ───▶ merge noted;
                                                                         the human closes <KEY>
```

The default branch carries the code, and everything reaches it by PR. An issue's status lives in its
tracker, and the only status change this tool makes is Start's move to "in progress". **Done never
changes an issue's status** (§7.3).

<a id="repo-resolution"></a>

### 7.0 Which repo

Several repos can serve one project (`CIAM` spans `cryo-gitops-github` and
`Cryoport-Authentication-Service`), so no command looks up "the" repo for a project.
`mainCheckout()` resolves it on every call:

1. **A key decides.** When the command has a key (Open, Start, Ship, Done, `view`), its prefix gives
   the project, and the project's repos that appear in `checkouts` are the candidates. A checkout
   matches a repo by its `origin` remote, normalized (ssh or https, with or without `.git`). The
   current folder may belong to another project: `plan open CIAM-9` works from inside skuld.
2. **Existing work wins.** Each candidate is searched for `<repo>.worktrees/<KEY>` or the issue's
   branch (`existingWorktree()`, `issueBranch()`). The worktree or branch found is the record of
   which repo the issue's work lives in, so there is no extra state to keep in sync. Found in more
   than one repo, the command refuses and names each.
3. **New work.** With nothing found, the current folder's repo is used when it is a candidate. A
   linked worktree resolves to its main checkout through `git rev-parse --path-format=absolute
   --git-common-dir`, so a run inside `<repo>.worktrees/CIAM-9` means `<repo>`. Otherwise one
   candidate is used outright, several open an fzf pick ("Which repo holds this issue's work?"), and
   none is refused, naming the project's remotes and `checkouts`.
4. **No key.** `plan list` and the picker show the current repo's project first when run inside a
   configured repo, and every configured project anywhere else.

The resolved repo gives the project, its tracker, the main checkout, the worktree root
`<repo>.worktrees/`, the base branch, and the store folders the run writes to. A run makes its git
and GitHub changes in the configured repo, and its ticket and review changes in the store; it never
writes a ticket into a development repo.

<a id="start"></a>

### 7.1 Start

Start shows for an issue that hasn't started: a store `status: open`, or a Jira `statusCategory` of
`new`. It lists the issue's open blockers (§4.2) and asks whether to go on; no stops with nothing
changed. It then makes the branch `<type>/<KEY>-<slug>` and its worktree off the latest base, and
moves the issue to "in progress".

- **Jira.** The transition is looked up by target status name at call time, never by a hardcoded
  id: `lib/jira.py` lists the issue's available transitions, picks the one whose `to.name` matches
  the configured target (default `"In Progress"`), and posts it. Issue types in `CIAM` don't share
  one status scheme, so Start's "already started" check reasons from `statusCategory.key`
  (`new`/`indeterminate`/`done`), never from a status name.
- **Store.** `FileStore` sets `status: in-progress` in the front matter, leaving the rest of the
  file untouched.

Either way, Start reads the issue back and refuses if its status isn't the target (§8). It then logs
"Started" on the issue (an ADF comment in Jira, a `## Log` line in a ticket) and brings the issue
into the plan window (§2). Start writes the issue workspace file `<KEY>.code-workspace` to
`workspaceDir` (config, default `~/.cache/plan/code-workspaces`), outside every repo. It holds the
issue's `plan.key`, `plan.worktree` and `plan.prompt`, and either `plan.ticket`, a store ticket's
file, or `plan.ticketUrl`, a Jira issue's browse URL, set only when there is no `plan.ticket`.
`plan-window` reads it when the issue is opened; no window opens on it. Start rewrites it each time. Start then adds the
issue to the issues file and its worktree to the plan workspace file, `plan.code-workspace` in the
same folder, which lists as folders the ticket store's folder and the worktrees of the issues in
the issues file, and is written through a temp file and a rename. The store's folder is always the
first folder: Start and Open add it when it is missing and move it to the front when it is listed
further down, so the folder Done removes is never the one VS Code names the workspace by. Start
then runs `code` on the plan workspace file, which opens the plan window or raises it. If `code`
fails, Start stops and prints the command to run by hand.

**The plan window is the only window that acts.** Every VS Code window loads `plan-window`, but it
acts only in the window whose workspace file is `<plan.workspaceDir>/plan.code-workspace`. That
window sets the context key `plan.isPlanWindow`, and the Issues view's `when` clause shows the view
only where the key is true. In any other window there is no Issues view, no click command and no
file watcher. A `/open` or `/browse` link that reaches such a window shows "this link is for the
plan window." and does nothing else.

**The plan workspace's settings tell `plan-window` where things are.** Start and Open write
`plan.issuesFile`, `plan.pulsesFile`, `plan.workspaceDir` and `plan.command` there, and
`workbench.browser.openLocalhostLinks`. `plan.command` is the absolute path of the running
checkout's `bin/plan`, and it overrides the extension's default `plan`.

**Opening an issue brings up its Claude.** A click on the issue's item in the Issues view, or the
link `vscode://skuld.plan-window/open?key=<KEY>`, opens the issue. `plan-window` reads the issue
workspace file and focuses the plan window's "Claude <KEY>" terminal when there is one. Otherwise
it creates that terminal in the worktree and starts Claude in it: `claude --continue` when the
worktree has an earlier Claude session, and `claude "$PLAN_PROMPT"`, a prompt naming the issue,
when it has none. The steps run in a fixed order. First the issue's ticket opens, every time: a
store ticket's file in an editor tab, or a Jira issue's browse URL in the integrated browser. A tab
already showing the ticket or the page is brought to the front, and a second one is never opened. Then
the shell beside Claude opens, then the Claude terminal is focused or created, and last the
issue's colour is cleared. Claude comes after the ticket and the shell so that focus ends on it.
`plan-window` sets `PLAN_KEY` in the environment of both terminals, since the issue
workspace file holds no terminal environment, so a command run in either knows its issue. Both unset every `CLAUDE*` session variable the
window inherited: a window opened by `code` from inside a Claude session carries that session's
`CLAUDE_CODE_CHILD_SESSION` and `CLAUDE_PID`, and a Claude started with them never registers, so
`claude agents --json` wouldn't list it. `CLAUDE_CONFIG_DIR` is kept. It says where Claude keeps
its config and conversations, not which session it belongs to, and the check for an earlier
session reads it. The window never closes an issue's terminals. When a key
leaves the issues file, the issue leaves the Issues view, and its `Claude <KEY>` and `Shell <KEY>`
terminals stay until the human closes them, since `plan done` is often run in one of them.

**Each issue's item in the Issues view shows its Claude's state.** The status engine, `lib/agents.py`, places
each session from `claude agents --json` by its cwd: a cwd in `<repo>.worktrees/<KEY>` gives the
key, and the key counts only while it is in the issues file. A key is an `ISSUE_KEY_PATTERN` key (§5), the
rule for the issues file, the pulses file's lines and the extension alike. A session it can't
place shows on `-` and pulses nothing; a session in the worktree of a key that isn't listed shows
as `- (KEY)` in `plan agents` and as `-(KEY)` in the status line, so the key isn't lost. An issue pulses orange while one of its Claudes waits on a person (a question, a
permission), green while one is idle with `.tmp/<KEY>/report.html` newer than the branch's last
commit, and is cleared (`off`) otherwise, or when it leaves the issues file. Orange outranks green.
A tick writes the pulses file only when an issue's colour changes, and reads no store or Jira.

**The engine's record of what it sent is kept on disk.** `<pulsesFile>.sent` holds a JSON object
`{KEY: colour}` for each issue last pulsed orange or green; off is not stored. It is written under
the same lock and through the same temp file and rename as the other shared files. At start the
engine lays this record over the pulses file's colours (`agents.loadSent`), so a colour cleared by
a click stays cleared across a restart, and the first tick sends nothing for a listed issue that is
off.

**A Claude's own hooks say when it waits.** `claude agents --json` doesn't call a session at a
permission prompt `waiting`, so the engine also reads Claude Code's hooks. `bin/claude-waiting`,
registered by `bin/install-agents`, writes a marker named for the session in `~/.cache/plan/waiting`
on a Notification that holds it for a person (a permission prompt, a question, a wait for input),
the same one that shows the system notification, and removes it on the session's next prompt, tool
call, Stop or end. A marked session is waiting, except an idle one with a fresh report, which stays
done. The watch loop checks the directory's mtime four times a second and ticks as soon as it
changes, so the issue's item turns orange as the notification shows. The hook prints nothing and always
exits 0; a dead session's marker is ignored, since the engine places only the sessions `claude
agents --json` lists. Clicking an issue's item in the Issues view clears its colour only when the issue has a line in
the pulses file, and the engine pulses it again only when that issue's colour changes. `plan agents` lists the same sessions with each issue's title, from the store for a store
key and from Jira only when the machine config names the key's project.

**A Copilot review on an issue's PR turns its item orange too.** Once a minute, on its own thread, the watch loop runs `gh pr view` in the worktree of each issue in the issues file that has a Claude (`lib/reviews.py`). Each tick uses whatever the last finished check found, so a slow GitHub never holds up a pulse, and a `gh` call is given up after 20 seconds. If the PR is open and Copilot has reviewed its head commit (`headRefOid`), the review is waiting, and the issue pulses orange even over green. The next push moves the head and clears it, so nothing is kept between checks. A worktree with no PR, or a `gh` that fails, counts as no review. The LaunchAgent's PATH includes the directory of the `gh` that `bin/install-agents` found.

**Which issues are in the plan window is tracked in the issues file.**
`term.addIssue(key)` appends the key as a line of `issuesFile` (config, default
`~/.cache/plan/issues`), one `<KEY>` per line and no key twice, through a temp file and a rename so
a reader never sees a half-written file. Start and Open add the issue, and Done removes it
(`term.removeIssue(key)`). A key stays until Done, whether or not the plan window is open. The plan
workspace file lists the worktree of each key in it, and the Issues view and the status engine
read it; `plan list` and the picker do not.

**Each issue's colour is kept in the pulses file.** `pulsesFile` (config, default
`~/.cache/plan/pulses`) holds a `<KEY>=orange` or `<KEY>=green` line per coloured issue, and `off`
removes the key's line. `plan pulse <KEY> <orange|green|off>` and the status engine write it,
through a temp file and a rename. `plan-window` clears an issue's colour by running
`plan pulse <KEY> off` with its `plan.command` setting, the running checkout's `bin/plan`, and only
when the key has a line; if the command fails, the
window shows a warning and the colour stays. Done removes the key's line along with its
issues-file line. A missing file means no issue has a colour. The Issues view watches the issues
file and the pulses file.

**Writes to the shared files are locked.** `lib` changes the issues file, the pulses file and the
plan workspace file by reading, changing and writing it back, and it holds an exclusive lock
around each of these changes, so two writers never interleave. Each write goes through its own
uniquely named temp file. Every writer of these files is `lib`, the extension's colour clear
included, so every write takes the lock.

**VS Code places the plan window; skuld does not.** Start and Open run `code <plan workspace>`,
which opens the plan workspace in a new window, or raises the window that already shows it,
wherever that window is. A window is known by its workspace file's path, so a second plan window is
never opened. `plan` reads no Desktop, switches none, records no window id, moves no window and
needs no helper or permission grant.

- **Window closed by hand.** The next Start or Open runs `code` again, which opens the plan window
  with every issue's folder. Opening an issue that has no terminal there starts one.
- **Window on another Desktop or display.** `code` raises it where it is.
- **A finished issue.** Done removes the issue's key from the issues file and its worktree from
  the plan workspace. Its terminals stay in the plan window until the human closes them.
- **Reopening a finished issue is one manual step.** Done removes the key, the pulse and the folder,
  and leaves the terminals open. To reopen, the human sets the issue back to In Progress (a Jira
  issue in Jira; a store ticket by `status: in-progress` in its front matter), then runs
  `plan start <KEY>`. Start accepts an issue in the in-progress category and refuses only a done
  one. With no worktree it makes one at `<repo>.worktrees/<KEY>`, reusing a local branch for the
  issue, else checking out `origin`'s, else making a new branch from the base. It then adds the key
  and the folder again like any Start, and the Claude terminal resumes with `claude --continue`
  when the worktree's conversation files exist.


If the issue's branch already exists here or on `origin`, Start reuses it on its own base. On an
issue in progress the command is Open, which reopens the worktree and brings the issue into the
plan window, recreating any part that is missing ([failure-modes.md,
*Open*](failure-modes.md)). A "Claude <KEY>" terminal that is already open is focused, never started twice, and the ticket's tab or the Jira page's tab is brought forward without opening a second one.
Start refuses a closed issue. A lock file per issue keeps two invocations from starting it at once
([failure-modes.md, *Start*](failure-modes.md)).

**The issue beside Claude.** Opening the issue puts its ticket beside Claude: a store ticket in an
editor tab of the plan window, on the ticket file in the store, never on a copy in a worktree; a
Jira issue's browse URL in the plan window's integrated browser. Start prints the URL or file
path. Claude's
prompt names the tracker: a Jira key, or the ticket file's path.

Last, Start and Open open the issue's `/open` link, so the command finishes with the human at the
issue's Claude. `term.revealIssue` sends it with `code --open-url <link>`, after `code` has brought
the plan window forward; system `open` is not used. It is best-effort: if the send fails, Start or
Open still prints `<KEY> ready.` and adds one line on stderr, `couldn't open <KEY> in the plan
window: <error>; click it in the Issues view`.

<a id="ship"></a>

### 7.2 Ship

A round is written into `<store>/reviews/<KEY>/`: `commit.txt` when the tree has changes,
`pr-body.md`, and a reply to each review thread in `replies/<comment id>.md`. For a Jira issue it
also holds `issue.md`, the issue's description with Done boxes ticked; a store issue's Done boxes
are ticked in the ticket's own `## Done` section.

Ship runs `ship.check()` and refuses by name a missing PR body, a missing commit message while the
tree has changes, or a branch that isn't the issue's. On success it runs `pr <KEY>` visibly in an
integrated terminal of the plan window. `pr` commits with `commit.txt`, pushes, and opens the PR or
replaces its body. It then posts each reply to its thread and resolves the thread. For a Jira issue
it applies the box changes in `issue.md` to the description, and refuses any other difference, so an
edit made in Jira since is kept. Each file is deleted once sent, so a second `pr` sends nothing
twice; the store's history keeps what was sent.

`pr` writes no tracker field to link the PR. Jira's GitHub integration reads the key in the branch
name and PR title. `done.py` finds the PR the same way, with `gh pr view <branch>` or
`gh pr list --head <branch>`.

Ship again is the next round. With nothing left to send, `pr` sends nothing, so Ship is safe to run
again (§8).

<a id="finish"></a>

### 7.3 Finish

The finish is the squash merge. The PR's subject includes the key (e.g. `Add X (CIAM-123)`).

Done shows once a PR for the issue's branch is found and merged. It removes the issue's worktree and
its issue workspace file, brings the main checkout's base up to the merge, deletes the branch, and notes
the PR and its merge commit on the issue: an ADF comment in Jira, a `## Log` line in a ticket.

**Done never changes an issue's status.** `lib/done.py` contains no call to a transition. The
human's Claude Code `PreToolUse` hook also blocks any Bash command recognized as moving a Jira issue
to a terminal status (Done, Closed, Cancelled, Resolved), so a hand-run command can't do it from
inside a Claude session either. Done's last step prints the close note (`closeNote(key)`): for Jira,
`Close CIAM-123 in Jira` with the browse URL; for a ticket, the file to set `status: done` in. Done
never calls `showUrl()`; the printed line is what points the human at the issue.

Done accepts an issue that is in progress or already closed, reasoning from Jira's
`statusCategory` or the ticket's `status`, since Jira's Done-category status names vary by issue
type.

It checks everything before it changes anything, and Done again finishes what a stopped one left
([failure-modes.md, *Done*](failure-modes.md)). Done also removes the issue from the issues file
(`term.removeIssue(key)`), its worktree from the plan workspace file and its line from the pulses
file. The issue leaves the Issues view, and its terminals stay open until the human closes them
(§7.1). The ticket store's folder stays in the plan workspace.

<a id="tool-commits"></a>

### 7.4 Commits

`plan` and `pr` make no commits of their own in a development repo; every commit there is the
human's or Claude's, and reaches the default branch through a pull request. In the ticket store,
only the watcher commits (§7.7).

<a id="archiving"></a>

### 7.5 Archiving

`plan archive` moves every closed ticket whose `closed` date is older than `store.yml`'s
`archiveAfterDays` from `active/` to `archive/`, with `git mv`. Only a closed ticket moves; every
other change edits the file in place, and the store's git history is the record of it. `FileStore`
reads both folders, so a key resolves whichever one holds it.

<a id="ready-file"></a>

### 7.6 Next up

Next up answers "what's next" in the picker and in `plan list`. For the ticket store it is plan
order:

1. Milestones by `ordinal`, then issues with no milestone.
2. Within a milestone, open issues that block others first, then by the issue's `ordinal`, then
   issues with no ordinal by number.

For Jira it is the config's raw JQL (`jql` in the machine config), run against
`/rest/api/3/search/jql`. The default is `project in (<the jira entry's projects>) AND assignee =
currentUser() AND statusCategory != Done ORDER BY Rank`. With no Jira projects in the machine
config there is no Jira list. The two lists are merged into one, in one row shape; a ticket
with `after: <JIRA-KEY>` sorts directly after that Jira issue.

<a id="open-url"></a>

### 7.6.1 Opening a page

`plan browse <KEY | pr#N | url> [KEY]` opens a page through `showUrl()` in `lib/term.py`.
It always prints the URL first, then opens the page in the integrated browser of the plan window (§2).
A localhost or loopback link clicked in one of that window's terminals opens there too, through the
`workbench.browser.openLocalhostLinks` setting in the plan workspace file, and `plan-window` opens a Jira issue's browse URL when the issue is opened (§7.1). Nothing else opens a page. System
`open` sends a page to the default browser, outside the plan window, so neither the code nor an
agent's shell uses it for one (§10, rule 7).

`plan browse` takes a key, which opens the issue's page: Jira's browse URL, or the local server's
page for a store ticket. It also takes `pr#N`, which opens the pull request, or a URL. `[KEY]` names the
issue the page belongs to when neither the target nor the terminal names one.

The issue, in order: (1) an explicit key: the target's own, or `[KEY]`; (2) the `PLAN_KEY` in the environment of the terminal that ran the command; (3)
otherwise no issue, and the link carries no key.

VS Code sends a `vscode://` link to the focused window, and no terminal can name its window's id.
So `showUrl()` runs `code <plan workspace>`, which focuses the plan window, then
`code --open-url 'vscode://skuld.plan-window/browse?url=<url>&key=<KEY>'`, with the URL and key
percent-encoded and both passed as arguments, never through a shell. VS Code decodes a link's query
once before the handler reads it, so the whole query is encoded once more, and a `&` or `key=` inside
the page's URL stays inside the `url` parameter. plan-window's `/browse` handler
opens the page with `workbench.action.browser.open`. The handler opens only http(s) URLs.

Every step of `showUrl()` is best-effort: a missing `code`, a closed plan window or any other
failure is swallowed, since the printed URL stands on its own.

Pages for an issue, such as its report and its review, come from the local server, rendered from
Jinja templates with the issue's data. A page's URL path keys (the issue key, the round, the page)
name it, and the server renders it from the data on each request, so no command publishes a page. A
URL whose target doesn't exist redirects (302) to the nearest page that does: a missing round to the
latest round, and an issue with no rounds to the issue's page. An unknown key gets a 404 page that
says what exists. Every such page's header links the issue, to its Jira browse URL or
to the server's page for a store ticket, and links its pull request; before a PR exists, the header
says so. A page is always opened at its server URL, never as a `file://` path.

Done never calls `showUrl()` (§7.3). `plan view` prints the browse URL without calling it, since a
view is read-only and run from wherever the human happens to be.

<a id="file-backed-issues"></a>

### 7.7 The ticket store

The store is a folder of Markdown files with YAML front matter, in a git repo of its own:

```text
<store>/
  milestones/<id>.md
  active/<KEY>-<slug>.md
  archive/<KEY>-<slug>.md
  reviews/<KEY>/
```

`FileStore` is the `IssueStore` adapter for it. It answers for every prefix `store.yml` lists, and
reads `active/` and `archive/` together. A ticket's front matter holds `key`, `title`, `status`
(`open`, `in-progress`, `done`, `wontfix`), `type`, `opened`, `closed`, `milestone`, `ordinal`,
`after`, `parent`, `blocked_by` and `points`; its body is the house outline (Task, Guardrails,
Lenses, Done), then `## Log`, one dated line per thing `plan` did. `docs/issue-format.md` is the
spec.

**The checker.** `issue-check` (stdlib Python) checks every file against the spec and exits
non-zero on any error, naming file, line and rule: front matter keys, types and enums; the key
against the file name and `store.yml`; no duplicate keys; `parent` and `blocked_by` naming tickets
that exist, with no `blocked_by` cycle; `closed` set exactly when the ticket is closed; milestones
that exist and aren't closed under an open ticket; the body's outline and log lines. Every writer,
`plan`, a person or an agent, produces files it accepts. `FileStore` runs the same checks before
every write, so `plan` can't produce a file the watcher would refuse.

**Writing tickets.** `plan new <PREFIX> "<title>"` takes the next number, fills skuld's
`templates/issue.md`, writes the ticket, checks it and opens it in `$EDITOR`. `plan set <KEY>
field=value…` changes front-matter fields, all or nothing, checked first. Nobody writes front matter
by hand. A transition edits the one `status` line and reads the file back (§7.1); a log entry
appends a dated line under `## Log`. `FileStore` answers in the same shapes `Jira` does, so Start,
Ship and Done have no store-specific branch.

**The watcher.** `issue-watch` runs under launchd and watches `active/`, `archive/`, `milestones/`
and `reviews/`. After a quiet period (`debounceSeconds` in `store.yml`), so a mass edit lands as one
commit, it runs the checker. On failure it commits nothing, posts a macOS notification naming the
file and rule, and logs the same. On success it commits every change with a generated message
(`SK-0012: status in-progress`, `12 issues: ordinal`) and pushes, retrying a failed push after
`pull --rebase`. The watcher watches the checkout that `issueStore` names.

<a id="failure-modes"></a>

## 8. Failure modes

Each command that writes has its own table in [`failure-modes.md`](failure-modes.md). The events
they share:

| Event | Effect | Handling |
| --- | --- | --- |
| Jira is down, or the network is unreachable | Jira's Next up can't load, and no Jira issue can start or finish | Start and Done refuse before they change anything |
| No token in `~/.netrc` | No call to Jira can be made | Refused, naming `~/.netrc` and the expected `machine cryoport.atlassian.net` entry |
| A transition succeeds at the API layer but a workflow condition silently blocks the status change | The status is unchanged, though a comment made in the same call may still be saved | The write reads the issue back, refuses, and names the attempted and actual status ([failure-modes.md, *Jira silently blocks a transition*](failure-modes.md#jira-silently-blocks-a-transition)) |
| A transition id doesn't correspond to an allowed transition from the current status | The POST fails with a 4xx | Visible to the caller; `lib/jira.py` looks the transition up by name immediately before, so this means the issue moved in between |
| A key's project has no repo in `checkouts` on this Mac | There is nowhere to make the worktree | Refused, naming the project's remotes and `checkouts` (§7.0) |
| A key's work is found in more than one repo | The command can't tell which repo holds it | Refused, naming each repo (§7.0) |
| A `checkouts` path's remote is listed by no project | The checkout serves nothing | Reported as a config error, naming the path and its remote |
| A ticket fails the checker | The watcher would push a bad file | The watcher commits nothing, notifies and logs file and rule, and tries again on the next change; `FileStore` refuses to write such a file at all |
| The store's push fails (offline, or behind) | The remote lags the local store | The watcher retries after `pull --rebase`; a conflict stops it, loudly, until a person resolves it |
| Two Macs take the same number offline | Two tickets share a key | The checker's duplicate-key rule refuses the second; it is renumbered by hand |
| Two invocations start or finish one issue | Both would change the same worktree | A lock file per issue refuses the second |
| The Jira description is edited after `issue.md` is written | The ticks would overwrite the edit | `checklist.py` refuses any difference beyond box state, and keeps the file |
| A round's files are still unsent at Done | Done would finish with review work unsent | Done refuses before it changes anything, and names them |
| The key in a commit message or PR title | Jira's GitHub integration links it in the issue's development panel | None; this is the intended mechanism (§7.2) |
| The plan window was closed by hand | Open could find no window to raise | `code <plan workspace>` opens the plan window again, and opening the issue starts its terminal (§7.1) |

<a id="views"></a>

## 9. Views

| View | Shows |
| --- | --- |
| The picker and `plan list` | Next up in plan order, both trackers merged, with each issue's project name (§7.6) |
| `plan view <KEY>` | One issue: fields, open blockers, what it blocks, and its body or description |
| A ticket in an editor tab | The ticket file itself, opened in the plan window when the issue is opened (§7.1) |
| The Issues view | Every issue in the issues file, in the plan window's Explorer, tinted orange or green from the pulses file (§7.1) |
| `plan agents` | Every Claude session, with its issue key and state (§7.1) |
| `git log` in the store | Every change to every ticket, milestone and review round |
| Jira's issue list, board and issue page | Jira issues, their links, comments and development panel |

<a id="invariants"></a>

## 10. Rules

1. Issues live in a tracker, never in a development repo: the ticket store or Jira Cloud. Nothing
   tracker-related is committed in the repos the trackers plan.
2. The default branch carries the code, and every change reaches it through a pull request. An
   issue's status lives in its tracker: Start's one move sets it in progress, and **nothing else in
   this tool changes an issue's status**; Done is structurally incapable of it (§7.3, §7.4).
3. Every outside system is reached through its port's adapter, and every call to Jira through
   `lib/jira.py` (§6.2).
4. Every ticket file passes the checker. `FileStore` checks before it writes, and the watcher
   commits nothing that fails (§7.7).
5. Tickets are edited in place; only a closed ticket moves, from `active/` to `archive/` (§7.5).
6. Skuld holds code and definitions, the ticket store holds data, and neither holds the other's
   (§3).
7. A page for an issue comes from the local server, links the issue and its pull request, and opens in
   the plan window's integrated browser through `showUrl()`, never through system `open` (§7.6.1).
