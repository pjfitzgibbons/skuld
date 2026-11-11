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
  - [7.2 Rounds](#rounds)
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
the helper scripts and the definitions they share. `plan` lists what's next, and starts, opens and
finishes an issue, each in its own VS Code window on its own native macOS Desktop. `pr` commits,
pushes, opens or updates the pull request and answers its review.

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
| Slot | Where an issue's window lives: a native macOS Desktop's number, recorded as `<slot>=<KEY>` in the slots file (§7.1) |
| Issue window | The VS Code window Start opens for an issue on its slot, on a multi-root workspace of the issue's worktree and the ticket store. Claude and a shell run in its integrated terminals, and the issue's pages open in its integrated browser (§7.1) |
| Start | Moving an issue to "in progress" from `plan start`, which makes its branch and worktree and logs "Started" on the issue (§7.1) |
| Round | One pass of change and review, numbered from 0, in `<store>/reviews/<KEY>/rounds/<n>/`. Round 0 is the author's change reviewed by `/code-review medium`; each later round answers a PR review. Its report lists the findings and what the human runs next, and `pr` sends it (§7.2) |
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
| `WindowPlacer` | put an issue window on a free slot, find it again, label the slot, and list where each issue's window is | `SpacesPlacer` (native macOS Desktops) |

`storeFor(cfg, key)` picks an issue's adapter from its prefix: `FileStore` for a prefix `store.yml`
lists, `Jira` for any other. `placerFrom(cfg)` returns the `WindowPlacer`, a `SpacesPlacer`.

The clients:

- **`plan`** (`bin/plan`, `lib/cli.py`) shows Next up in an fzf picker, and runs `new`, `set`,
  `view`, Start, Open and Done ([GUIDE.md §3](../GUIDE.md#3-commands)).
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
`osascript`, the `skuld-spaces` helper and `fzf` as part of its normal work; none of them touches a tracker or git state, so
they need no port. A new call to Jira, a tracker write or a git remote operation always goes
through its adapter.

`gh` is already authenticated, so nothing holds a GitHub token.

<a id="platform"></a>

### 6.3 Platform

| Layer | Choice |
| --- | --- |
| Trackers | The ticket store (Markdown with YAML front matter, in a git repo); Jira Cloud, `cryoport.atlassian.net` |
| Store commits | `issue-watch` under launchd: debounced, gated by the checker, commits and pushes |
| Auth | `~/.netrc` for Jira, read per call; `gh`'s and git's own credentials for GitHub and the store's remote |
| Front end | VS Code, one issue window per issue, opened by the `code` CLI on a workspace file and placed by the `WindowPlacer` on a native Desktop, all via `subprocess`; fzf for the picker; ticket files in an editor tab |
| Spaces helper | `skuld-spaces`, built from `macos/spaces.swift` with `swiftc` alone: read-only SkyLight calls, resolved with `dlsym`, that list Desktops and each VS Code window's Desktop as JSON |
| Menu bar | SlotBar or SketchyBar, whichever has its LaunchAgent loaded. SlotBar is a menu-bar extra built from `macos/slotbar/` with `swiftc` alone and signed with a local self-signed certificate. It reads the slots file and Desktops through read-only SkyLight calls, takes pulses as the distributed notification `skuld.pulse` (a Desktop 1 to 16 and `orange`, `green` or `off`, anything else ignored) from `plan pulse` and the Claude status engine, and switches Desktops by posting ^N, which needs the Accessibility grant |
| Claude status | `lib/agents.py`, run every few seconds by a LaunchAgent as `plan agents tick --watch`, and at once when a waiting marker changes: reads `claude agents --json`, the waiting markers in `~/.cache/plan/waiting` that the Claude Code hook `bin/claude-waiting` keeps, the slots file and git, once a minute each slotted issue's PR through `gh`, and pulses each issue's Desktop through `slotbar-pulse` only when its colour changes (§7.1) |
| Browser | The issue window's integrated browser, reached by `showUrl()` (§7.6.1); never system `open`. Browser automation and browser tests run in Chrome, never in an issue window |
| Pages | One local Flask server on `127.0.0.1`, run by a LaunchAgent from login and controlled by `plan serve start\|stop\|restart\|status`, rendering an issue's pages from Jinja templates (§7.6.1) |
| Pull requests | `gh`; Jira linkage through Jira's development panel, not a custom field |
| Jira writes | `/rest/api/3/*`, ADF for descriptions and comments |

<a id="operations"></a>

## 7. Operations

```text
   Next up (plan order)       worktree <repo>.worktrees/<KEY>          the issue (store or Jira)
   ────────────────────       ───────────────────────────────          ─────────────────────────
   (1) START ───────────────▶ branch <type>/<KEY>-slug, off base ────▶ in progress, "Started" logged
                              issue window, Claude in its terminal      (a store ticket opens in a tab)
   (2) ROUND 0 ─────────────▶ Claude implements, runs /code-review medium and fixes what it finds
                              writes round 0: report, review, commit.txt, pr-body.md
                              adds after-merge steps to the ticket ───▶ ## After merge
                              opens <KEY>/report/0 in the issue window, and stops
       the human reads it and runs pr <KEY>: commit, push, open PR, ask Copilot ─▶ Done boxes ticked
   (3) ROUND n ─────────────▶ Copilot reviews; Claude answers each thread and fixes
                              writes round n: replies/, report, review, commit.txt
                              opens <KEY>/report/n, and stops
       the human reads it and runs pr <KEY>: commit, push, reply, resolve threads
       ... until a round's report has no open findings
   (4) squash merge on GitHub, at any round; the PR title and branch carry <KEY>
   (5) the human runs the report's after-merge steps against the updated base
   (6) DONE ────────────────▶ worktree and branch removed, base up ───▶ merge noted; a store ticket
                                                                         set to status: done;
                                                                         the human closes a Jira <KEY>
```

The default branch carries the code, and everything reaches it by PR. An issue's status lives in its
tracker. This tool changes it twice: Start moves an issue to "in progress", and Done sets a store
ticket to `done`. **Done never changes a Jira issue's status** (§7.3).

<a id="repo-resolution"></a>

### 7.0 Which repo

Several repos can serve one project (`CIAM` spans `cryo-gitops-github` and
`Cryoport-Authentication-Service`), so no command looks up "the" repo for a project.
`mainCheckout()` resolves it on every call:

1. **A key decides.** When the command has a key (Open, Start, Done, `view`), its prefix gives
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
"Started" on the issue (an ADF comment in Jira, a `## Log` line in a ticket) and opens the issue
window (§2). Start writes the workspace file `<KEY>.code-workspace`, listing the worktree and the
ticket store, to `workspaceDir` (config, default `~/.cache/plan/code-workspaces`), outside every
repo, and the `WindowPlacer` opens it in a VS Code window on the issue's slot. The file is a
throwaway that Start rewrites each time. VS Code knows a window by its workspace file's path, so
an issue whose file is still at `<repo>.worktrees/<KEY>.code-workspace` keeps using it until Done
deletes it. A window is always found by its window id, never
by its title. Claude runs in one integrated
terminal, started with a prompt naming the issue, and a shell in another. Both terminals carry the
issue's key in their environment, so a command run in either knows its issue. Both unset every
`CLAUDE*` variable the window inherited: a window opened by `code -n` from inside a Claude session
carries that session's `CLAUDE_CODE_CHILD_SESSION` and `CLAUDE_PID`, and a Claude started with them
never registers, so `claude agents --json` wouldn't list it.

**Each issue's Desktop shows its Claude's state.** The status engine, `lib/agents.py`, places each
session from `claude agents --json` by its cwd: a cwd in `<repo>.worktrees/<KEY>` gives the key,
and the slots file gives the key's Desktop. A session it can't place shows on `-` and pulses
nothing. A Desktop pulses orange while one of its Claudes waits on a person (a question, a
permission), green while one is idle with its latest round's report newer than the branch's last
commit, and is cleared (`off`) otherwise, or when its issue leaves the slots file. Orange outranks
green. A tick sends a pulse only when a Desktop's colour changes, and reads no store or Jira; a
failed pulse makes the next tick send every Desktop's colour again, so a restarted SlotBar gets them
all back.

**A Claude's own hooks say when it waits.** `claude agents --json` doesn't call a session at a
permission prompt `waiting`, so the engine also reads Claude Code's hooks. `bin/claude-waiting`,
registered by `bin/install-agents`, writes a marker named for the session in `~/.cache/plan/waiting`
on a Notification that holds it for a person (a permission prompt, a question, a wait for input),
the same one that shows the system notification, and removes it on the session's next prompt, tool
call, Stop or end. A marked session is waiting, except an idle one with a fresh report, which stays
done. The watch loop checks the directory's mtime four times a second and ticks as soon as it
changes, so the Desktop turns orange as the notification shows. The hook prints nothing and always
exits 0; a dead session's marker is ignored, since the engine places only the sessions `claude
agents --json` lists. Switching to or from a Desktop clears its colour in SlotBar, and the engine pulses it
again only when that Desktop's colour changes. `plan agents` lists the same sessions with each issue's title, from the store for a store
key and from Jira only when the machine config names the key's project.

**A Copilot review on an issue's PR turns its Desktop orange too.** Once a minute, on its own thread, the watch loop runs `gh pr view` in the worktree of each issue that has a Desktop and a Claude (`lib/reviews.py`). Each tick uses whatever the last finished check found, so a slow GitHub never holds up a pulse, and a `gh` call is given up after 20 seconds. If the PR is open and Copilot has reviewed its head commit (`headRefOid`), the review is waiting, and the Desktop pulses orange even over green. The next push moves the head and clears it, so nothing is kept between checks. A worktree with no PR, or a `gh` that fails, counts as no review. The LaunchAgent's PATH includes the directory of the `gh` that `bin/install-agents` found.

**Which issue sits on which slot is tracked in a slots file**, not inferred from a workspace's or
Desktop's name. `term.writeSlot(key, slot)` writes a `<slot>=<KEY>` line to `slotsFile` (config,
default `~/.cache/plan/workspaces`), replacing any line for that slot and the key's own old line,
through a temp file and a rename so a reader never sees a half-written file. It then triggers
SketchyBar (`sketchybar --trigger plan_workspaces_change`, skipped if SketchyBar isn't installed) so
`~/.config/sketchybar/plugins/desktops.sh` can relabel the bar. Every command that touches slots
(`start`, `open`, `done`, and `plan list` and the picker) first sweeps the slots file through the
placer, dropping any line whose issue window is gone. `plan list` shows each issue's slot, and both
numbers when its window is somewhere else.

**Slots are Desktop numbers**, counted the way the "Switch to Desktop N" shortcuts (^N) count them:
across displays in the order macOS lists them, desktop Spaces only. No public call creates a Desktop
or moves an open window to another one, so `SpacesPlacer` does neither. It reads the Desktops, the
showing Desktop, each VS Code window's Desktop and the frontmost VS Code window through
`skuld-spaces` (§6.3), and builds the helper from its source when it is missing or older than the
source. A missing SkyLight symbol makes the helper report "unknown", and Start stops there. A second
file, `windowsFile` (default `~/.cache/plan/windows`), records `<KEY>=<window id>`, so Open and the
sweep know which window is the issue's.

To place a window, `SpacesPlacer` sends the free Desktop's ^N shortcut through System Events, as the
shortcut is bound in `com.apple.symbolichotkeys`, waits until that Desktop shows, and runs
`code <workspace file>`. With a Desktop per display, more than one shows at once. A Desktop that
never shows stops Start before `code` runs. A window id that wasn't there before is the issue's
new window. When no new id appears and VS Code has switched away from that Desktop or brought a
different window to the front, the workspace was already open, and the frontmost VS Code window
becomes the issue's. A window already in front on another display changes neither, so it is
adopted when its title, read through System Events, starts with the issue's key. To find a recorded window again,
`code <workspace file>` switches to its Desktop and brings it forward.

- **Free Desktop.** The issue's own slot when its Desktop holds no VS Code window, else the first
  Desktop that exists, isn't in `reservedDesktops` (default `[1]`), holds no VS Code window and is
  no other issue's slot. Start refuses a Desktop that doesn't exist or whose ^N shortcut is off,
  naming the setting, before it runs `code`. With no free Desktop, Start and Open refuse, even for a
  workspace that is already open, since placing starts by switching to a free Desktop.
- **Wrong Desktop.** A window that lands on a Desktop other than the one switched to, an adopted
  window, or an open window found on a Desktop other than its slot gets its slot rewritten to where
  it is, and the command says so. The window is never moved.
- **Full screen.** A window on no numbered Desktop, such as a full-screen one, stops Start and
  Open, which say to take it out of full screen and run `plan open` again.
- **Taken Desktop.** When a window is on another issue's slot, the command leaves that slot alone,
  drops the issue's own slot, and says to drag the window to a free Desktop and run `plan open`
  again.

If the issue's branch already exists here or on `origin`, Start reuses it on its own base. On an
issue in progress the command is Open, which reopens the worktree, its issue window and its slot
through the placer, recreating any that is missing ([failure-modes.md,
*Open*](failure-modes.md)). An issue window that is already open is reused, never opened twice.
Start refuses a closed issue. A lock file per issue keeps two invocations from starting it at once
([failure-modes.md, *Start*](failure-modes.md)).

**The issue beside Claude.** A Jira issue's browse URL opens through `showUrl()` (§7.6.1), with the
issue's key passed explicitly, since Start runs in the invoking terminal and not in the issue
window. A store issue opens instead in an editor tab of the issue window, on the ticket file in the
store's folder, never on a copy in a worktree. The URL or file path is printed either way. Claude's
prompt names the tracker: a Jira key, or the ticket file's path.

Last, Start and Open switch to the issue's slot and focus the issue window with Claude's terminal in
front, so the command finishes with the human at the issue's Claude, by running
`code <workspace file>`. This runs after `showUrl()`. It is best-effort: if it fails, Start or Open
still succeeds and says nothing.

<a id="rounds"></a>

### 7.2 Rounds

An issue's work is a series of rounds, numbered from 0, each in its own folder,
`<store>/reviews/<KEY>/rounds/<n>/`. The highest `n` is the latest round. Every round ends the same
way: Claude writes the round's files, opens its report in the issue window with
`plan browse <KEY>/report`, and stops. The human reads the report and runs `pr <KEY>` in the issue
window's terminal.

Round 0 is the author's change. Claude implements the issue, runs `/code-review medium` on the
diff, and fixes what it accepts. Its report lists those findings, each with its disposition. Each
later round answers a review of the PR: Copilot's threads get a reply in `replies/<comment id>.md`
and a fix where one is needed, and the report lists those findings.

A round holds:

- `report.md`, the round's report as text, and `review.json`, its `findings` and `dispositions`.
  The local server renders both as pages (§7.6.1).
- `commit.txt` when the tree has changes, and `pr-body.md`.
- `replies/<comment id>.md`, one reply per review thread.
- For a Jira issue, `issue.md`: the issue's description with Done boxes ticked. A store issue's Done
  boxes are ticked in the ticket's own `## Done` section.

Every report lists the commands to run. Under "This round" is `pr <KEY>`, which the server writes
from the key. Under "After the merge" are the steps in the ticket's `## After merge` section, shown
as the ticket gives them: commands to run against the updated default branch, and any other
follow-up. Claude adds a step there when the work creates one. A ticket without the section has
nothing to do after the merge.

`pr` refuses by name a branch that isn't the issue's, a missing PR body, and a missing commit
message while the tree has changes. Then it commits with `commit.txt`, pushes, and opens the PR or
replaces its body. It posts each reply to its thread and resolves the thread. For a Jira issue it
applies the box changes in `issue.md` to the description, and refuses any other difference, so an
edit made in Jira since is kept. Each sent file is deleted, so a second `pr` sends nothing twice;
the store's history keeps what was sent.

`pr` writes no tracker field to link the PR. Jira's GitHub integration reads the key in the branch
name and PR title. `done.py` finds the PR the same way, with `gh pr view <branch>` or
`gh pr list --head <branch>`.

A round whose report has no open findings is ready to merge, but the human may merge the PR at any
round. With nothing left to send, `pr` sends nothing, so it is safe to run again (§8).

<a id="finish"></a>

### 7.3 Finish

The finish is the squash merge. The PR's subject includes the key (e.g. `Add X (CIAM-123)`).

Done shows once a PR for the issue's branch is found and merged. It removes the issue's worktree and
its workspace file, brings the main checkout's base up to the merge, deletes the branch, and notes
the PR and its merge commit on the issue: an ADF comment in Jira, a `## Log` line in a ticket.

**Done closes a store ticket.** After the merge line, it sets the ticket's `status: done` and
`closed:` to the day, and regenerates `INDEX.md` with `scripts/index.py`; the watcher commits both
(§7.7). A ticket already `done` with `closed:` set is left as it is.

**Done never changes a Jira issue's status.** A Jira close can carry required fields or workflow
conditions only a person can satisfy, so `lib/done.py` makes no Jira transition. The human's Claude
Code `PreToolUse` hook also blocks any Bash command recognized as moving a Jira issue to a terminal
status (Done, Closed, Cancelled, Resolved), so a hand-run command can't do it from inside a Claude
session either. For a Jira issue, Done's last step prints the close note (`closeNote(key)`),
`Close CIAM-123 in Jira` with the browse URL. Done never calls `showUrl()`; the printed line is what
points the human at the issue.

Done accepts an issue that is in progress or already closed, reasoning from Jira's
`statusCategory` or the ticket's `status`, since Jira's Done-category status names vary by issue
type.

It checks everything before it changes anything, and Done again finishes what a stopped one left
([failure-modes.md, *Done*](failure-modes.md)). Done also frees the issue's slot
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
`/rest/api/3/search/jql`. The default is `project in (<the jira entry's projects>) AND assignee =
currentUser() AND statusCategory != Done ORDER BY Rank`. With no Jira projects in the machine
config there is no Jira list. The two lists are merged into one, in one row shape; a ticket
with `after: <JIRA-KEY>` sorts directly after that Jira issue.

<a id="open-url"></a>

### 7.6.1 Opening a page

`plan browse <KEY | KEY/report | pr#N | url> [KEY]`, Start and Open open a page through `showUrl()` in `lib/term.py`.
It always prints the URL first, then opens the page in the integrated browser of the issue window (§2).
A localhost or loopback link clicked in one of that window's terminals opens there too, through the
`workbench.browser.openLocalhostLinks` setting Start writes into the workspace file. Nothing else opens a page. System
`open` sends a page to the default browser, outside every issue window, so neither the code nor an
agent's shell uses it for one (§10, rule 7).

`plan browse` takes a key, which opens the issue's page: Jira's browse URL, or the local server's
page for a store ticket. It also takes `pr#N`, which opens the pull request, or a URL. `[KEY]` names the
issue whose window the page belongs in when neither the target nor the terminal names one.

The issue window, in order: (1) an explicit key: the target's own, or `[KEY]`, and Start always
passes its issue's (§7.1); (2) the `PLAN_KEY` in the environment of the terminal that ran the
command; (3) otherwise, whichever VS Code window has focus.

VS Code sends a `vscode://` link to the focused window, and no terminal can name its window's id.
So `showUrl()` runs `code <workspace file>`, which focuses the issue's window, then
`code --open-url 'vscode://skuld.plan-window/browse?url=<url>&key=<KEY>'`, with the URL and key
percent-encoded and both passed as arguments, never through a shell. VS Code decodes a link's query
once before the handler reads it, so the whole query is encoded once more, and a `&` or `key=` inside
the page's URL stays inside the `url` parameter. plan-window's `/browse` handler
opens the page with `workbench.action.browser.open`. It refuses a link whose `key` isn't its
window's `plan.key`, so a link that reaches another window, because focus moved between the two
commands, shows a refusal and opens nothing. A link with no key opens in the focused window. The
handler opens only http(s) URLs.

Every step of `showUrl()` is best-effort: a missing `code`, a closed issue window or any other
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
appends a dated line under `## Log`. `FileStore` answers in the same shapes `Jira` does, so Start
and Done have no store-specific branch.

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
| The issue's slot names a Desktop whose issue window is gone | Open could treat an empty Desktop as the issue's | A slot is reused only while the window id in `windowsFile` is still open; a stale slot is swept and a free Desktop picked (§7.1) |

<a id="views"></a>

## 9. Views

| View | Shows |
| --- | --- |
| The picker and `plan list` | Next up in plan order, both trackers merged, with each issue's project name (§7.6) |
| `plan view <KEY>` | One issue: fields, open blockers, what it blocks, and its body or description |
| A ticket in an editor tab | The ticket file itself, opened in the issue window by Start and Open (§7.1) |
| `git log` in the store | Every change to every ticket, milestone and review round |
| Jira's issue list, board and issue page | Jira issues, their links, comments and development panel |

<a id="invariants"></a>

## 10. Rules

1. Issues live in a tracker, never in a development repo: the ticket store or Jira Cloud. Nothing
   tracker-related is committed in the repos the trackers plan.
2. The default branch carries the code, and every change reaches it through a pull request. An
   issue's status lives in its tracker. Start sets it in progress, and Done sets a store ticket to
   `done`; **nothing in this tool moves a Jira issue to a terminal status** (§7.3, §7.4).
3. Every outside system is reached through its port's adapter, and every call to Jira through
   `lib/jira.py` (§6.2).
4. Every ticket file passes the checker. `FileStore` checks before it writes, and the watcher
   commits nothing that fails (§7.7).
5. Tickets are edited in place; only a closed ticket moves, from `active/` to `archive/` (§7.5).
6. Skuld holds code and definitions, the ticket store holds data, and neither holds the other's
   (§3).
7. A page for an issue comes from the local server, links the issue and its pull request, and opens in
   the issue window's integrated browser through `showUrl()`, never through system `open` (§7.6.1).
