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
ships and finishes an issue in Ghostty under Aerospace and tmux. `pr` commits, pushes, opens or
updates the pull request and answers its review.

Issues live in one of two trackers: the ticket store, a git repo of Markdown files that skuld
defines and works, or Jira Cloud (`cryoport.atlassian.net`) for work projects. Pull requests are
GitHub's, and they are the one GitHub integration; GitHub Issues is not used.

<a id="definitions"></a>

## 2. Definitions

| Term | Definition |
| --- | --- |
| Issue | The unit of planned work, named by its key, `PREFIX-n`: a project prefix and a per-project number, zero-padded to four digits in the store (`SK-0012`). It lives in the ticket store or in Jira |
| Project | A key prefix and the repos its work happens in. Store projects are listed in skuld's `store.yml`, Jira projects in the machine config |
| Ticket store | A git repo, outside every development repo, holding ticket files, milestones and review files, with its own remote (§3, §7.7) |
| Configured repo | A development repo some project lists by its `origin` remote, checked out on this Mac at a path in `checkouts` (§7.0) |
| Milestone | A store-wide group of issues with a status (`open`, `locked`, `closed`) and an `ordinal` (§4.1) |
| Ordinal | An integer that orders milestones, and issues within a milestone; lower comes first (§7.6) |
| Default branch | The branch GitHub has configured for the repo: `main`, or `develop` under git-flow. It carries the code |
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

The boundary is drawn around outside systems, not around processes. The terminal front end runs
`ghostty`, `open`, `aerospace`, `tmux`, `fzf`, `nvim` and `lazygit` as part of its normal work; none
of them touches a tracker or git state, so they need no port. A new call to Jira, a tracker write or
a git remote operation always goes through its adapter.

`gh` is already authenticated, so nothing holds a GitHub token.

<a id="platform"></a>

### 6.3 Platform

| Layer | Choice |
| --- | --- |
| Trackers | The ticket store (Markdown with YAML front matter, in a git repo); Jira Cloud, `cryoport.atlassian.net` |
| Store commits | `issue-watch` under launchd: debounced, gated by the checker, commits and pushes |
| Auth | `~/.netrc` for Jira, read per call; `gh`'s and git's own credentials for GitHub and the store's remote |
| Terminal front end | Ghostty, Aerospace and tmux via `subprocess`; fzf for the picker; nvim for ticket files |
| Browser | Brave, reached through Aerospace and AppleScript by `openUrl()`; never system `open`, and never the terminal's own link-click (§7.6.1). Browser automation and browser tests run in Chrome, so `Brave Browser` always names the human's own Brave |
| Pages | One local Flask server on `127.0.0.1`, run by a LaunchAgent from login and controlled by `plan serve start\|stop\|restart\|status`, rendering an issue's pages from Jinja templates (§7.6.1) |
| Pull requests | `gh`; Jira linkage through Jira's development panel, not a custom field |
| Jira writes | `/rest/api/3/*`, ADF for descriptions and comments |

<a id="operations"></a>

## 7. Operations

```text
   Next up (plan order)       worktree <repo>.worktrees/<KEY>          the issue (store or Jira)
   ────────────────────       ───────────────────────────────          ─────────────────────────
   (1) START ───────────────▶ branch <type>/<KEY>-slug, off base ────▶ in progress, "Started" logged
                              Ghostty+tmux window, Claude beside        (a store ticket opens in nvim)
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
"Started" on the issue (an ADF comment in Jira, a `## Log` line in a ticket) and opens the worktree
in a new Ghostty window, moved to an Aerospace workspace, running
`tmux new-session -A -s <KEY> -c <worktree>` with a Claude window and a shell window.

**Workspaces are numbered, not named for the issue.** Aerospace workspaces keep the hotkeys
`~/.aerospace.toml` binds them to (`ctrl-alt-1`..`9`, `ctrl-alt-a`..`z`, etc.); `plan` never renames
or creates one. `term.freeWorkspace()` reads that file's `# Workspaces: 1 work, 2 editors, ...`
comment line (`namedWorkspaces()`) and picks the first workspace not named there that has no
windows (`aerospaceConfig` in config, default `~/.aerospace.toml`).

**Which issue sits on which workspace is tracked in a slots file**, not inferred from the
workspace's name. `term.writeSlot(key, workspace)` writes a `<workspace>=<KEY>` line to `slotsFile`
(config, default `~/.cache/plan/workspaces`), replacing any line for that workspace, through a temp
file and a rename so a reader never sees a half-written file. It then triggers SketchyBar
(`sketchybar --trigger plan_workspaces_change`, skipped if SketchyBar isn't installed) so
`~/.config/sketchybar/plugins/workspaces.sh` can relabel the bar. `term.openIssueWorkspace()` looks
up the issue's slot and reuses that workspace if it still has windows; otherwise it picks a free
workspace and relabels it. Every command that touches workspaces (`start`, `open`, `done`, and
`plan list` and the picker) first sweeps the slots file (`term.sweepSlots()`), dropping any line
whose workspace has no windows.

If the issue's branch already exists here or on `origin`, Start reuses it on its own base. On an
issue in progress the command is Open, which reopens the worktree, and its tmux session and
Aerospace workspace through the slot lookup, recreating either if missing
([failure-modes.md, *Open*](failure-modes.md)). Start refuses a closed issue. A lock file per issue
keeps two invocations from starting it at once ([failure-modes.md, *Start*](failure-modes.md)).

**The issue beside Claude.** A Jira issue's browse URL opens through `openUrl()` (§7.6.1), with the
workspace Start just resolved passed explicitly, since Start runs in the invoking terminal and not
the new workspace. A store issue opens instead as a tmux pane split beside the `=<KEY>:claude`
window (`term.openTicketPane()`, `tmux split-window -h -d`), running `nvim` on the ticket file in
the store, never on a copy in a worktree. The pane is tagged with a pane option
(`@plan_ticket <KEY>`), so a second Open reuses it rather than splitting again. The URL or file path
is printed either way. Claude's prompt names the tracker: a Jira key, or the ticket file's path.

Last, Start and Open switch to that workspace, focus its Ghostty window and select the session's
`claude` window (`term.focusIssueWorkspace()`: `tmux select-window -t =<KEY>:claude`,
`aerospace workspace <ws>`, then `aerospace focus --window-id` on the workspace's Ghostty window),
so the command finishes with the human at the issue's Claude. This runs after `openUrl()`, and
overrides its return of focus to the calling terminal. It is best-effort, like the SketchyBar
trigger.

<a id="ship"></a>

### 7.2 Ship

A round is written into `<store>/reviews/<KEY>/`: `commit.txt` when the tree has changes,
`pr-body.md`, and a reply to each review thread in `replies/<comment id>.md`. For a Jira issue it
also holds `issue.md`, the issue's description with Done boxes ticked; a store issue's Done boxes
are ticked in the ticket's own `## Done` section.

Ship runs `ship.check()` and refuses by name a missing PR body, a missing commit message while the
tree has changes, or a branch that isn't the issue's. On success it runs `pr <KEY>` visibly in a
tmux window or pane. `pr` commits with `commit.txt`, pushes, and opens the PR or replaces its body.
It then posts each reply to its thread and resolves the thread. For a Jira issue it applies the box
changes in `issue.md` to the description, and refuses any other difference, so an edit made in Jira
since is kept. Each file is deleted once sent, so a second `pr` sends nothing twice; the store's
history keeps what was sent.

`pr` writes no tracker field to link the PR. Jira's GitHub integration reads the key in the branch
name and PR title. `done.py` finds the PR the same way, with `gh pr view <branch>` or
`gh pr list --head <branch>`.

Ship again is the next round. With nothing left to send, `pr` sends nothing, so Ship is safe to run
again (§8).

<a id="finish"></a>

### 7.3 Finish

The finish is the squash merge. The PR's subject includes the key (e.g. `Add X (CIAM-123)`).

Done shows once a PR for the issue's branch is found and merged. It removes the issue's worktree,
brings the main checkout's base up to the merge, deletes the branch, and notes the PR and its merge
commit on the issue: an ADF comment in Jira, a `## Log` line in a ticket.

**Done never changes an issue's status.** `lib/done.py` contains no call to a transition. The
human's Claude Code `PreToolUse` hook also blocks any Bash command recognized as moving a Jira issue
to a terminal status (Done, Closed, Cancelled, Resolved), so a hand-run command can't do it from
inside a Claude session either. Done's last step prints the close note (`closeNote(key)`): for Jira,
`Close CIAM-123 in Jira` with the browse URL; for a ticket, the file to set `status: done` in. Done
never calls `openUrl()`; the printed line is what points the human at the issue.

Done accepts an issue that is in progress or already closed, reasoning from Jira's
`statusCategory` or the ticket's `status`, since Jira's Done-category status names vary by issue
type.

It checks everything before it changes anything, and Done again finishes what a stopped one left
([failure-modes.md, *Done*](failure-modes.md)). Done also frees the issue's workspace slot
(`term.removeSlot(key)`, §7.1) and triggers SketchyBar.

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
`/rest/api/3/search/jql`. The default is `project = CIAM AND assignee = currentUser() AND
statusCategory != Done ORDER BY Rank`. The two lists are merged into one, in one row shape; a ticket
with `after: <JIRA-KEY>` sorts directly after that Jira issue.

<a id="open-url"></a>

### 7.6.1 Opening a page

`plan browse <KEY | pr#N | url> [KEY]`, Start and Open open a page through
`term.openUrl(url, workspace=None, ...)`. It always prints the URL first, then adds the page as a
tab to a Brave window (§6.3) in the same Aerospace workspace as the Ghostty and tmux window that ran
the command. Nothing else opens a page. System `open` sends a page to whichever Brave window was
used last, often on another workspace, so neither the code nor an agent's shell uses it for one
(§10, rule 7).

`plan browse` takes a key, which opens the issue's page: Jira's browse URL, or the local server's
page for a store ticket. It also takes `pr#N`, which opens the pull request, or a URL. `[KEY]` names the
issue whose workspace the page belongs in when neither the target nor the session names one.

Target workspace, in order: (1) an explicit `workspace` argument, which Start always passes (§7.1);
(2) inside tmux, when the session name is a key in the slots file, that slot's workspace; (3)
otherwise, the focused workspace (`aerospace list-workspaces --focused`). (2) comes before (3) for a
command run from a tmux session whose window has lost Aerospace focus.

When the target workspace has a Brave (`com.brave.Browser`) window, `openUrl()` finds it by its
title, which `aerospace list-windows` and Brave's AppleScript both report, and adds the tab to that
window through AppleScript. Focus doesn't move, so a hidden workspace stays hidden and the screen
doesn't switch. Two Brave windows on the workspace with the same title can't be told apart that
way: `openUrl()` then focuses the one Aerospace names (`aerospace focus --window-id`), adds the tab
to Brave's front window, and returns focus to the window focused before. When the workspace has no
Brave window, `openUrl()` makes one through AppleScript and moves it to the target workspace by a
before-and-after diff of `browserBundleId` window ids, the same diffing `appWindowIds()` and
`newAppWindowId()` do for Ghostty (§7.1). Every Aerospace and AppleScript call in `openUrl()` is
best-effort: a missing `aerospace`, a missing Brave or any other failure is swallowed, since the
printed URL stands on its own.

Pages for an issue, such as its report and its review, come from the local server, rendered from
Jinja templates with the issue's data. A page's URL path keys (the issue key, the round, the page)
name it, and the server renders it from the data on each request, so no command publishes a page. A
URL whose target doesn't exist redirects (302) to the nearest page that does: a missing round to the
latest round, and an issue with no rounds to the issue's page. An unknown key gets a 404 page that
says what exists. Every such page's header links the issue, to its Jira browse URL or
to the server's page for a store ticket, and links its pull request; before a PR exists, the header
says so. A page is always opened at its server URL, never as a `file://` path.

Done never calls `openUrl()` (§7.3). `plan view` prints the browse URL without calling it, since a
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
| A tmux session for the issue exists but its slot's workspace has no windows left | Open could reuse a workspace with nothing on it | Workspace reuse is gated on `windowsIn()`, not just the slot; a stale slot is swept and a fresh workspace picked (§7.1) |

<a id="views"></a>

## 9. Views

| View | Shows |
| --- | --- |
| The picker and `plan list` | Next up in plan order, both trackers merged, with each issue's project name (§7.6) |
| `plan view <KEY>` | One issue: fields, open blockers, what it blocks, and its body or description |
| A ticket in nvim | The ticket file itself, opened beside Claude by Start and Open (§7.1) |
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
   the issue's Aerospace workspace through `openUrl()`, never through system `open` (§7.6.1).
