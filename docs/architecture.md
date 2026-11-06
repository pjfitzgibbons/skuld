# Architecture

## Contents

- [1. Purpose](#purpose)
- [2. Definitions](#definitions)
- [3. Storage](#storage)
- [4. Plans](#plans)
  - [4.1 Projects and versions](#members)
  - [4.2 Dependencies and parents](#dependencies)
- [5. Identity](#identity)
- [6. Components](#components)
  - [6.1 Jira and its clients](#front-ends)
  - [6.2 Process boundary](#process-boundary)
  - [6.3 Platform](#platform)
- [7. Operations](#operations)
  - [7.1 Start](#start)
  - [7.2 Ship](#ship)
  - [7.3 Finish](#finish)
  - [7.4 Tool commits](#tool-commits)
  - [7.5 Baselines](#freeze)
  - [7.6 Next up](#ready-file)
  - [7.7 File-backed JP issues](#file-backed-issues)
- [8. Failure modes](#failure-modes)
- [9. Views](#views)
- [10. Invariants](#invariants)

**Requirements:** [`requirements.md`](requirements.md) · **Decisions:** [`adr/`](adr/) ·
**Failure modes by command:** [`failure-modes.md`](failure-modes.md)

<a id="purpose"></a>
## 1. Purpose

Jira Plan plans work in Jira Cloud (`cryoport.atlassian.net`, project `CIAM`), which runs outside
this repo. This repo is the terminal front end around it: a `plan` CLI (`bin/plan`, `lib/`) that
starts, ships and finishes an issue, run from Ghostty under Aerospace and tmux instead of a VS Code
panel. `pr`, the jira-issues skill's script, commits, pushes and opens or updates the pull request.
Jira's own GitHub integration links commits and PRs to the issue through its development panel;
GitHub Issues is not used, and pull requests are the one GitHub integration this tool drives.

<a id="definitions"></a>
## 2. Definitions

| Term | Definition |
|---|---|
| Issue | A Jira issue, `CIAM-123`, the project key plus a per-project number. The unit of planned work |
| Project | The Jira project `CIAM`; other projects may exist in the tenant but this tool is scoped to one by config |
| Default branch | The branch GitHub has configured for the repo: `main`, or `develop` under git-flow. It carries the code |
| Tool commit | None: the tool makes no commits of its own, and every commit reaches the default branch through a pull request (§7.4) |
| Start | Transitioning an issue to its project's "in progress" status from `plan start`, which makes its branch and worktree and posts a "Started" comment (§7.1) |
| Round | One pass of review: the commit message, PR body, review replies and Done ticks in `.tmp/<KEY>/` that `pr` sends (§7.2) |
| Finish | The squash merge of the issue's PR, then Done, which comments the issue and tells the human to close it (§7.3) |
| Next up | The config's JQL, run against `/rest/api/3/search/jql`: by default `project = CIAM AND assignee = currentUser() AND statusCategory != Done ORDER BY Rank` (§7.6) |
| Blocker | A Jira issue link of type "Blocks", read from the inward direction ("is blocked by"), whose target's `statusCategory` isn't Done |

<a id="storage"></a>
## 3. Storage

Jira Cloud is the source of truth, outside this tool's control. It holds every issue with its
fields, status, comments and issue links. The repos it plans hold no tracker files, and this tool
keeps no local database of its own.

The Jira GitHub integration (installed at the tenant level, outside this repo) links commits and
pull requests to an issue automatically when the branch name or PR title contains the issue's key,
and surfaces them in the issue's development panel. This tool relies on that integration for PR
linkage; it does not write a custom field for it (contrast the original design's `Pull requests`
field, dropped — see [§7.2](#ship)).

A checkout's `.tmp/` is ignored by git. `.tmp/<KEY>/` holds a round's files for issue `<KEY>` until
`pr` sends and deletes them, and `plan done` refuses to remove a worktree that still holds any it
hasn't sent.

The API token lives only in `~/.netrc` (machine `cryoport.atlassian.net`), read fresh by
`lib/jira.py` on each call — never cached, never in an env var, never logged. Nothing in this repo
holds a Jira credential.

<a id="plans"></a>
## 4. Plans

<a id="members"></a>
### 4.1 Projects and versions

One Jira Cloud site holds the plan. This tool is scoped to one project, `CIAM`, by config
(`jql` and `project` in `~/.config/jira-plan/config.json`). Other projects in the tenant are out of
scope for this tool, though Jira itself has no such restriction.

<a id="dependencies"></a>
### 4.2 Dependencies and parents

A dependency is Jira's "Blocks" issue link type (id `10000`, outward `blocks`, inward
`is blocked by`; confirmed live via `GET /rest/api/3/issueLinkType`). Start warns when an issue
linked as "is blocked by" has a `statusCategory` other than Done.

A child is a sub-task. Jira's own hierarchy (Epic → Story/Task/Bug → Sub-task) replaces the
parent/sub-task relation the original tool modeled on top of Redmine; this tool reads it, it does
not model it separately.

<a id="identity"></a>
## 5. Identity

An issue is its Jira key, `CIAM-123`, unique per project. Its branch is `<type>/CIAM-123-<slug>`,
with `feat/`, `fix/` or `chore/` chosen from the issue's Jira type via a config map (Story/Task →
`feat`, Bug → `fix`, anything else → `chore`), and its worktree is `<repo>.worktrees/CIAM-123`
beside the main checkout. A commit message names the issue in its subject, e.g.
`Add X (CIAM-123)`, which is what the Jira GitHub integration matches on — there is no `fixes
#<n>`/`refs #<n>` distinction, because Jira Plan never transitions the issue from a commit or a PR
merge (§7.3); only a human transition, or `plan start`'s one transition to "in progress", moves its
status.

<a id="components"></a>
## 6. Components

<a id="front-ends"></a>
### 6.1 Jira and its clients

Jira Cloud serves the web UI and the REST API v3. Everything else is a client of the REST API:

- **The `plan` CLI** (`bin/plan`, `lib/cli.py`) shows Next up in an fzf picker, and runs Start,
  Open, Ship and Done ([GUIDE.md §4](../GUIDE.md#4-the-terminal-frontend)).
- **`pr`** commits, pushes, opens or updates the PR, posts review replies, applies Done ticks to
  the issue's description, and cleans up after a merge. It and its Python scripts live in
  `~/.claude/skills/jira-issues/scripts/`, outside this repo.
- **`jira-get`** (if present; see `bin/`), sends one read-only GET with `~/.netrc` auth, so an
  agent can read issues without seeing the token.

<a id="process-boundary"></a>
### 6.2 Process boundary

`lib/jira.py` and `lib/start.py` are the only modules that open a network connection to Jira, and
`lib/jira.py` is the only one that does. The rest of `lib/` is plain functions, testable without a
network or a repo.

- `lib/jira.py` makes every call to Jira: the JQL search for Next up, reading an issue, looking up
  a transition by its target status name and posting it with a read-back guard, reading issue
  links for blockers, and posting ADF comments. It reads the token from `~/.netrc` on each call, so
  no repo or process environment holds it.
- `lib/start.py` runs git and `gh` for Start, Ship and Done: it fetches, adds and removes
  worktrees, reads PRs, and pulls and deletes merged branches.

This is a **narrower** invariant than the original tool's, deliberately: the original scoped "only
these modules spawn a process or open a connection" to the whole extension, because a VS Code
extension has no legitimate reason to shell out widely. The terminal front end does — `bin/plan`
and `lib/term.py` spawn `ghostty`/`open`, `aerospace`, `tmux`, `fzf`, `nvim` and `lazygit` as part
of its normal operation, none of which touch Jira or git state. So the invariant here is scoped to
**every call to Jira**, not every process: a new Jira call must go through `lib/jira.py`, full
stop; a new *subprocess* may be added to the terminal front end without violating this invariant,
as long as it isn't a Jira call in disguise.

`gh` is already authenticated, so nothing holds a GitHub token. `pr` writes to GitHub through `gh`
and to Jira through the REST API, with the same `~/.netrc` token `lib/jira.py` uses.

<a id="platform"></a>
### 6.3 Platform

| Layer | Choice | Why |
|---|---|---|
| Tracker | Jira Cloud, `cryoport.atlassian.net`, project `CIAM` | decision #1 |
| Auth | `~/.netrc`, read per call | decision #1 |
| Terminal frontend | Ghostty + Aerospace + tmux via `subprocess`, fzf for the picker | decision #8 |
| Browser | Brave, launched explicitly via `open -na "Brave Browser" --args --new-window <url>` (new window) or `open -a "Brave Browser" <url>` (existing window, as a tab) — never a bare `open <url>`, and never the terminal's own link-click | user decision 2026-11-05 |
| Pull requests | Subprocess to `gh`; linkage via Jira's native development panel, not a custom field | decisions #4, #9 |
| Tracker writes | `/rest/api/3/*`, ADF for descriptions and comments | decision #1 |

<a id="operations"></a>
## 7. Operations

```
   Next up (JQL)                 worktree <repo>.worktrees/CIAM-123        Jira CIAM-123
   ──────────────                ──────────────────────────────────        ─────────────
   (1) START ──────────────────▶ branch <type>/CIAM-123-slug, off base ──▶ In Progress,
                                 Ghostty+tmux window, Claude Code beside    "Started" comment
                                 Claude works · writes .tmp/CIAM-123/ files
   (2) SHIP ────────────────────▶ pr CIAM-123: commit, push, open/update PR ──▶ dev panel (native)
                                 review rounds: replies/, issue.md ───────▶ Done boxes ticked
   (3) squash merge on GitHub; PR title/branch carry CIAM-123
   (4) DONE ───────────────────▶ worktree and branch removed, base up ────▶ ADF comment posted;
                                                                             human closes CIAM-123
```

The default branch carries the code, and everything reaches it by PR. An issue's status lives in
Jira, and the only status change this tool ever makes is Start's one transition to "in progress";
**Done never transitions the issue** (§7.3) — that is a hard rule, not a default.

<a id="repo-resolution"></a>
### 7.0 Which repo (`mainCheckout()`)

Several repos can serve one Jira project (e.g. `CIAM` spans `cryo-gitops-github` and
`Cryoport-Authentication-Service`), so Start, Open, Ship and Done don't look up "the" repo for an
issue's project — `lib/start.py`'s `mainCheckout()` resolves it fresh on every call, in this order
(DECIDED, user decision 2026-11-05):

1. **Existing work wins, searched across every candidate repo.** The candidates are the cwd's own
   main checkout (if cwd is inside one — a linked worktree resolves to its main checkout via `git
   rev-parse --path-format=absolute --git-common-dir`, so running from inside
   `<repo>.worktrees/CIAM-9` still means "repo"), plus every repo listed under
   `checkouts.<PROJECT>` in config. `mainCheckout()` runs the same `existingWorktree()` lookup
   Start/Open/Done already used within one repo (an existing worktree at `<repo>.worktrees/CIAM-9`,
   or the issue's branch under `issueBranch()`) against **each** candidate. The worktree (or
   branch) found is the record of which repo the issue lives in — there's no extra state to keep in
   sync. Found in more than one repo is a refusal naming every repo it was found in, not a silent
   pick; that ties into the still-open one-issue-per-repo question below.
2. **New work uses the cwd's own repo.** If nothing existing was found anywhere, and cwd is inside
   a git repo, that repo's main checkout is where the new worktree/branch is made — `checkouts` is
   not consulted for new work when cwd is already a repo, even if a different repo is listed for
   the project.
3. **`checkouts` is the fallback for new work when cwd isn't a repo at all.** `checkouts.<PROJECT>`
   is a repo path, or now a **list** of them (a bare string is still accepted, as a one-item list —
   back-compat). Exactly one listed repo is used outright, with no prompt. More than one opens an
   `fzf` picker ("Which repo holds this issue's work?"). None listed (and cwd not a repo) is a
   refusal: `"Run plan from the repo the work belongs in (or set checkouts.<PROJECT> in
   ~/.config/jira-plan/config.json)"`.

This applies wherever `mainCheckout()`/`resolveDir()` are consulted: `lib/cli.py`'s `resolveDir()`
(Open/the picker) and `lib/start.py`'s `start()`/`lib/done.py`'s `finish()` (Start/Ship/Done) all
thread the real `cwd` through to it instead of looking up a single configured path per project.

**OPEN (not solved here):** whether one Jira issue may legitimately span two repos at once (e.g. a
CIAM change that touches both `cryo-gitops-github` and `Cryoport-Authentication-Service` in the
same round). Rule 1's "more than one repo" case currently always refuses rather than picking one or
supporting both; this is deferred, not answered, pending a real case.

**OPTIONAL-LATER (not solved here):** a Jira component or label like `repo:<name>` on the issue
could preselect the repo at Start instead of relying on cwd, for a user who starts work without
first `cd`-ing into the right repo. Not implemented.

<a id="start"></a>
### 7.1 Start

Start shows for an issue whose `statusCategory` is `new` ("To Do"). It checks blockers — any issue
link of type "Blocks" read inward ("is blocked by") whose target's `statusCategory` isn't `done` —
then makes the branch `<type>/CIAM-123-<slug>` and its worktree off the latest base, and transitions
the issue to its project's "in progress"-category status.

The transition is looked up **by target status name at call time**, never by a hardcoded transition
id: `lib/jira.py` lists the issue's available transitions, picks the one whose `to.name` matches
the configured target (default `"In Progress"`), and posts it. Because Jira issue types in `CIAM`
don't share one status scheme (Task-type issues have a status literally named "In Progress" in
category In Progress; Story, Bug and Epic each have several differently-named In-Progress-category
statuses — confirmed via `GET /rest/api/3/project/CIAM/statuses`), the lookup matches on **status
name** but Start's own "is this issue already started" check reasons from `statusCategory.key`
(`new`/`indeterminate`/`done`), so it still works correctly on a Story or Bug whose in-progress
status isn't literally named "In Progress".

After posting the transition, Start **reads the issue back** and refuses if its `statusCategory`
isn't `indeterminate` (In Progress) — the Jira equivalent of Redmine's silent-204 guard (§8). Start
then posts a "Started" ADF comment and opens the worktree in a new Ghostty window, moved to an
Aerospace workspace, running `tmux new-session -A -s CIAM-123 -c <worktree>` with a Claude window
and a shell window.

**Workspaces are numbered, not named for the issue.** Aerospace workspaces keep the fixed hotkeys
`~/.aerospace.toml` already binds them to (`ctrl-alt-1`..`9`, `ctrl-alt-a`..`z`, etc.); `plan` never
renames or creates one. `term.freeWorkspace()` reads that file's `# Workspaces: 1 work, 2 editors,
...` comment line (`namedWorkspaces()`) and picks the first workspace **not** named there that
currently has no windows — `aerospaceConfig` in config, default `~/.aerospace.toml`.

**Which issue sits on which workspace is tracked in a slots file**, not inferred from the
workspace's name. `term.writeSlot(key, workspace)` writes a `<workspace>=<KEY>` line to
`slotsFile` (config, default `~/.cache/plan/workspaces`), replacing any existing line for that
workspace, via a temp-file-then-rename so a reader never sees a half-written file; it then triggers
SketchyBar (`sketchybar --trigger plan_workspaces_change`, skipped silently if SketchyBar isn't
installed) so `~/.config/sketchybar/plugins/workspaces.sh` can relabel the bar. Open's reuse rule:
`term.openIssueWorkspace()` looks up the issue's existing slot, and reuses that workspace as-is if
it still has windows; otherwise it picks a fresh free workspace and relabels it — this is also the
path Start always takes, since a new issue never has an existing slot. Every command that touches
workspaces (`start`, `open`, `done`, and `plan list`/the fzf picker) first **sweeps** the slots
file (`term.sweepSlots()`), dropping any line whose workspace currently has no windows, so a slot
is never kept past the point the user closed its last window by hand.

If the issue's branch already exists here or on `origin`, Start reuses it on its own base. On an
issue whose `statusCategory` is `indeterminate` (In Progress) the command is Open, which reopens
the worktree (and its tmux session/Aerospace workspace via the slot lookup above, recreating either
independently if the other already exists — [failure-modes.md, *Open* under the terminal frontend
rows](failure-modes.md)), making it the same way if this machine has none. Start refuses an issue
whose `statusCategory` is `done`. A lock file per issue keeps two invocations from starting it at
once ([failure-modes.md, *Start*](failure-modes.md)).

Start then opens the issue's browse URL through `openUrl()` (§7.6.1), passing the workspace it just
resolved explicitly — Start runs in whatever terminal invoked it, not the new issue's own
workspace, so it can't rely on `openUrl()`'s own caller-workspace inference.

Last, Start/Open switches to that workspace, focuses its Ghostty window and selects the session's
`claude` tmux window (`term.focusIssueWorkspace()`: `tmux select-window -t =<KEY>:claude`,
`aerospace workspace <ws>`, then `aerospace focus --window-id` on the workspace's Ghostty window),
so the command finishes with the human at the issue's Claude — user decisions 2026-11-05. It runs after `openUrl()`, overriding that function's return of focus to
the calling terminal (§7.6.1), and is best-effort like the SketchyBar trigger.

<a id="ship"></a>
### 7.2 Ship

A round is written into `.tmp/CIAM-123/`: `commit.txt` when the tree has changes, `pr-body.md`, a
reply to each review thread in `replies/<comment id>.md`, and `issue.md`, the issue's description
with Done boxes ticked.

Ship runs `ship.check()` and refuses by name a missing PR body, a missing commit message while the
tree has changes, or a branch that isn't the issue's. On success it runs `pr CIAM-123` visibly in a
tmux window/pane, in the same invocation — the pre-flight check and the `pr` run used to be two
commands, Ship and Go, with a separate review window (`nvim` + `lazygit` on the round files)
between them; that confirmation step is removed now, before a planned review page replaces it, and
`plan go` no longer exists. `pr` commits with `commit.txt`, pushes, and opens the PR or replaces
its body. It then posts each reply to its thread and resolves the thread, and applies the box
changes in `issue.md` to the issue's description. It refuses any other difference, so an edit made
in Jira since is kept. Each file is deleted once sent, so a second `pr` sends nothing twice.
**Unlike the original tool, `pr` never writes a tracker field to link the PR** — Jira's GitHub
integration reads the branch name and PR title itself and populates the issue's development panel
automatically, because both contain `CIAM-123` (decision #4). `done.py` finds the PR the same way:
`gh pr view <branch>` / `gh pr list --head <branch>`, never from a Jira field.

Ship again runs `pr` again: the next round. With nothing left to send, `pr` sends nothing, so Ship
stays safe to re-run (§8, "every step runs only when it has something to do").

<a id="finish"></a>
### 7.3 Finish

The finish is the squash merge. The PR's subject includes `CIAM-123` (e.g. `Add X (CIAM-123)`),
which is what the Jira GitHub integration matches; there is no `fixes`/`refs` distinction, because
no commit or merge ever transitions the issue (§5).

Done shows once a PR for the issue's branch is found and merged. Once the PR has merged, it removes
the issue's worktree, brings the main checkout's base up to the merge, deletes the branch, and
posts an ADF comment linking the PR and its merge commit.

**Done never transitions the issue — not to Done, not to any status.** This is a hard rule enforced
at two levels: structurally, `lib/done.py` contains no call to `jira.transition()` at all; and
defensively, the human's own Claude Code `PreToolUse` hook independently blocks any Bash command
recognized as moving a Jira issue to a terminal status (Done/Closed/Cancelled/Resolved), so even a
hand-run CLI command can't do it from inside a Claude session. Done's last step instead prints
`Close CIAM-123 in Jira` with the issue's browse URL (`https://cryoport.atlassian.net/browse/
CIAM-123`), plainly — unlike Start (§7.1), Done never calls `openUrl()` (§7.6.1); the printed line
is the only thing that points the human at the issue.

Done accepts an issue whose `statusCategory` is `indeterminate` (In Progress) or `done` — unlike
the original, which accepted "In Progress" or "Closed" by exact name, Done here reasons from
category because, as in §7.1, the exact Done-category status name varies by issue type.

It checks everything before it changes anything, and Done again finishes what a stopped one left
([failure-modes.md, *Done*](failure-modes.md)). Done also frees the issue's workspace slot
(`term.removeSlot(key)`, §7.1) and triggers SketchyBar, but opens no browser window — the printed
`Close CIAM-123 in Jira` note above is the only place Done points the human at the issue.

<a id="tool-commits"></a>
### 7.4 Tool commits

The tool makes no commits of its own. An issue's status lives in Jira: Start makes the one
transition to "in progress" (§7.1); nothing else in this tool ever moves it, and Done is
structurally incapable of transitioning the issue (§7.3). Nothing reaches the default branch except
through a pull request.

<a id="freeze"></a>
### 7.5 Baselines

Not built: the original tool's administrator-only baseline date fields were a Redmine-specific
custom-field feature with no Jira equivalent in scope for this port. Left out, rather than
approximated with a Jira custom field no process here reads.

<a id="ready-file"></a>
### 7.6 Next up

Next up is the config's raw JQL (`jql` in `~/.config/jira-plan/config.json`), run against
`/rest/api/3/search/jql` — the current search endpoint; the older `/rest/api/3/search` returns
`410 Gone`. The default is `project = CIAM AND assignee = currentUser() AND statusCategory != Done
ORDER BY Rank`. It answers "what's next" in the `plan` CLI's fzf picker and in `plan list`.

<a id="open-url"></a>
### 7.6.1 Opening the browse URL

`term.openUrl(url, workspace=None, ...)` always prints `url` first, then best-effort acts on a
Brave window (§6.3, user decision 2026-11-05) in the **same Aerospace workspace as the Ghostty/tmux
window that ran the command** — never a bare `open <url>`, which (as the human reported) opens in
whatever default/terminal-associated browser context is frontmost, not necessarily the right
workspace.

Target workspace, in order: (1) an explicit `workspace` argument, which Start always passes (§7.1),
since it runs in the invoking terminal, not the new issue's workspace; (2) if inside tmux and the
session name is a key present in the slots file, that slot's workspace; (3) otherwise, the focused
workspace (`aerospace list-workspaces --focused`). Order (2) before (3) covers a command run from a
tmux session whose window has since lost Aerospace focus.

Once a target workspace is resolved: if it already has a `browserBundleId` (`com.brave.Browser`)
window, `openUrl()` focuses that window (`aerospace focus --window-id`) and opens the URL as a new
tab in it (`open -a "Brave Browser" <url>`). Verified live 2026-11-05 (JP-4): `plan open CIAM-163`
with Brave already running opened the ticket as the active tab of the focused workspace's Brave
window, not a new window. Otherwise it opens a new window
(`open -na "Brave Browser" --args --new-window <url>`, matching `~/.local/bin/plan-open`'s
mechanism exactly) and moves it to the target workspace by a before/after window-id diff on
`browserBundleId`, the same diffing `appWindowIds()`/`newAppWindowId()` already do for Ghostty
(§7.1). Either way, `openUrl()` then returns focus to whatever window was focused before it ran
(captured via `aerospace list-windows --focused` beforehand, restored via `aerospace focus
--window-id` afterward) — **DECIDED-BY-AGENT**: no design doc settles whether or how focus should
return to the terminal; returning it to exactly the window that was focused when `openUrl()` was
called was chosen so a Start/Open run never leaves the terminal in the background. Start/Open
then moves focus on to the issue's own workspace anyway (§7.1, user decision 2026-11-05); the
restore still governs any other `openUrl()` caller. Every Aerospace/
`open` call in `openUrl()` is wrapped best-effort (§7.1's SketchyBar pattern): a missing `aerospace`
binary, a missing Brave, or any other failure is swallowed, since the URL printed first already
stands on its own.

Start passes its resolved workspace explicitly (§7.1). Done never calls `openUrl()` (§7.3) — it
only prints. `plan view` prints the browse URL directly too, without calling `openUrl()`, since a
view is read-only and run from wherever the human happens to be; opening a browser window for it
would be a second side effect this command doesn't otherwise have.

<a id="file-backed-issues"></a>
### 7.7 File-backed JP issues

**User decision 2026-11-05.** `plan`'s own backlog -- this repo's `issues/` (format:
`issues/README.md`) -- is a second tracker, scoped to one project key, `JP`, and stored as files
instead of Jira. `lib/fileissues.py`'s `FileIssues` class implements the same surface
`lib/jira.py`'s `Jira` class does (`list(jql)`, `issue(key)`, `blockers(issue)`,
`statusTransitionId(key, name)`, `transition(key, name)`, `textComment(text)`,
`addComment(key, doc)`, plus `browseUrl(key)`/`closeNote(key)`, added to both classes for this),
returning dicts shaped the way `Jira`'s already are, so `lib/start.py`, `lib/done.py` and
`lib/ship.py` need no `JP`-specific branch at all. `lib/cli.py`'s `jiraFrom(cfg, key)` is the
dispatch: `projectOf(key) == "JP"` (`fileissues.isFileBacked(key)`) returns a `FileIssues`
instance, anything else a `Jira` one -- every call site already knows its key before it needs a
client, so this is a plain per-key pick, not a wrapping router object. `checkouts.JP` defaults to
this repo's own root, and `issuesDir` to its `issues/` folder (both derived from
`lib/config.py`'s own location), so Start/Open/Ship/Done find the repo without any user
configuration.

Three decisions this implements:

1. **All five commands work on a JP key:** `list`/the picker, `view`, `start`/`open`, `ship`,
   `done`. `issues/README.md`'s status enum (`open | in-progress | done | wontfix`) maps to the
   Jira-shaped `(status name, statusCategory key)` pair every caller already reads:
   `open` -> ("To Do", `new`), `in-progress` -> ("In Progress", `indeterminate`), `done`/`wontfix`
   -> (`"Done"`/`"Won't Fix"`, `done`) -- both terminal values read as Jira's `done` category,
   since nothing needs to tell them apart. `issues/README.md`'s type enum maps to a Jira issuetype
   name the existing branch-prefix map already handles: `bug` -> `Bug` (`fix/`), `task`/`question`
   -> `Task` (`feat/`). Files carry no issue links, so `blockers()` always returns `[]`, and no
   parent/hierarchy, so `issue().fields.parent` is always `None`.
2. **Writes edit the file.** Start's transition to "in progress" (`FileIssues.transition()`) sets
   frontmatter `status: in-progress` by regex-replacing that one line (everything else in the file
   is untouched), then **reads the file back** and refuses if the status it reads isn't the target
   -- the same guard `Jira.transition()` applies after its POST (§7.1,
   [failure-modes.md](failure-modes.md#jira-silently-blocks-a-transition)). Every `addComment`
   call (Start's "Started…" note, Done's merge note) appends a dated `- YYYY-MM-DD: <text>` line
   under a `## Log` heading at the end of the file, creating the heading once if it isn't there
   yet; `FileIssues.issue()` reads those lines back as `fields.comment.comments`, in ADF-shaped
   bodies, so Done's "did I already post this note" check (§7.3) still works unmodified. **Done
   still never changes status** -- same hard rule as Jira (§7.3, [§10](#invariants)) -- so a JP
   issue's `done`/`wontfix` is always set by a human editing the file directly; Done's closing
   note (`jira.closeNote(key)`) says so by name instead of naming a Jira browse URL to close.
3. **Listing is merged.** `plan list` and the picker (`lib/cli.py`'s `issueRows()`) run Jira's
   configured JQL and concatenate it with every JP file whose `status` is `open` or `in-progress`
   (`FileIssues.list()`) -- one combined list, in the same row shape, not two separate ones.

**Opening a JP issue (Start/Open).** A file-backed issue has no Jira browse URL to put in a
browser (§7.6.1 doesn't apply to it). Instead `cmdStart` checks `isFileBacked(key)` and, for a JP
key, calls `term.openTicketWindow(key, dir, jira.browseUrl(key))` (user decision 2026-11-05)
before `focusIssueWorkspace()`: create-if-missing, a `"ticket"` tmux window in the issue's own
`=<KEY>` session (§7.1's session, reused if Start already made it), `cwd` the worktree, running
`nvim <issuePath>` where `issuePath` is `FileIssues.browseUrl(key)` -- the absolute path under
`issuesDir` that `FileIssues` itself reads and writes, never the worktree's own copy of the file
(the worktree may not even have one, since `issues/` lives in this repo, not necessarily the repo
`plan start` makes the JP issue's branch in). A second Open on the same issue reuses the existing
`"ticket"` window rather than opening a duplicate, the same rule `openIssueWorkspace()` already
applies to its `claude`/`shell` windows. The file path still prints either way, exactly as
`openUrl()` always prints its URL first.

The `claude` window's prompt changes too: a JP key's Claude is asked to work on "a local
file-backed issue, not a Jira ticket" and given the issue file's `issuesDir` path, instead of
`Work on Jira <KEY>` (`openIssueWorkspace(..., prompt)`; 2026-11-05, after the Jira prompt sent
JP-3's Claude looking for a Jira ticket).

**OPEN (not solved here):** `projectMarkers` (`lib/config.py`) is documented as a picker/list
marker per project but has no reader anywhere in `lib/` -- it was never wired up for any project,
Jira or file-backed; `JP` rows get no special marker beyond printing `"JP"` as `project`, same as
every other project today.

<a id="failure-modes"></a>
## 8. Failure modes

Each command that writes has its own table in [`failure-modes.md`](failure-modes.md). The events
they share:

| Event | Effect | Handling |
|---|---|---|
| Jira is down, or the network is unreachable | Next up can't load, and nothing can start or finish | Start and Done refuse before they change anything |
| No token in `~/.netrc` | No call to Jira can be made | Refused, naming `~/.netrc` and the expected `machine cryoport.atlassian.net` entry |
| A transition POST succeeds at the API layer but a workflow condition silently blocks the actual status change | The status is unchanged, though a comment made in the same call may still be saved | The write reads the issue back, refuses, and names the attempted and actual status ([failure-modes.md, *Jira silently blocks a transition*](failure-modes.md#jira-silently-blocks-a-transition)) |
| A transition id requested doesn't correspond to an allowed transition from the current status | The POST 4xxs outright | Already visible to the caller; `lib/jira.py` looks the transition up by name immediately beforehand, so this should only happen if the issue moved between the lookup and the POST |
| Two invocations start or finish one issue | Both would change the same worktree | A lock file per issue refuses the second |
| The issue's description is edited after `issue.md` is written | The ticks would overwrite the edit | `checklist.py` refuses any difference beyond box state, and keeps the file |
| A round's files are still unsent at Done | Removing the worktree would delete them | Done refuses before it changes anything, and names them |
| `CIAM-123` in a commit message or PR title | GitHub's Jira integration reads it and populates the issue's development panel | None; this is the intended mechanism (§7.2) |
| A tmux session for the issue exists but its slot's workspace has no windows left (closed by hand) | Open could mistakenly reuse a workspace with nothing on it | Workspace reuse is gated on `windowsIn()`, not just the slot lookup; a stale slot is swept and a fresh workspace picked (§7.1; [failure-modes.md](failure-modes.md)) |

<a id="views"></a>
## 9. Views

Jira's own pages are the views:

| View | Shows |
|---|---|
| Issue list / board | Issues with their fields; the development panel shows linked branches/PRs/commits |
| Next up | The JQL's results, in Jira's issue navigator and in the `plan` CLI's fzf picker (§7.6) |
| An issue's page | Its description, issue links, comments and development panel |

<a id="invariants"></a>
## 10. Invariants

1. Jira Cloud is the tracker. It holds every issue, with its status and history, outside the repos
   it plans, and nothing tracker-related is committed in them.
2. The default branch carries the code, and every change reaches it through a pull request. An
   issue's status lives in Jira: Start's one transition sets it In Progress; **nothing else in this
   tool ever transitions an issue**, and Done is structurally incapable of it (§7.3, §7.4).
3. Retired with the tool's `plan:` commits, so the invariants after it keep their numbers.
4. Carried over unused: not applicable (see §7.5, baselines not built).
5. Retired, so the invariants after it keep their numbers. Invariant 6 bounds remote writes, which
   include Jira's REST API.
6. `lib/jira.py` and `lib/start.py` are the only modules that open a network connection to Jira.
   This invariant is scoped to Jira calls specifically, not to every subprocess — see §6.2 for why
   the terminal front end is a deliberate, documented exception to the broader "process boundary"
   phrasing the original tool used.
7. Retired: points/estimation fields were a Redmine-specific custom-field feature not carried into
   this port.
8. Retired: lanes were a Redmine plugin feature not carried into this port.
9. Retired, so the invariants keep their numbers (see #8).
10. Retired: baselines not built (§7.5).
11. Retired while tracked time is paused, so the invariants keep their numbers.
